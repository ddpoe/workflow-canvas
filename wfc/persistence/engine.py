"""The process engine, its sessions, and the one construction path.

The process engine is built on first use by :func:`bootstrap_engine`, which
resolves the database URL, builds the engine and ensures the schema. The URL
is the ``DATABASE_URL`` override when it is set, else Layout's database for
the resolved project root. :func:`get_session` yields a session on the process
engine, and :func:`reset_engine` disposes of the engine and forgets the cached
project root. :func:`use_project` binds the process to another project's
database and root for the length of a block.
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import Session, SQLModel, create_engine

from .. import layout
from . import accessor
from .backfill import ensure_schema

#: The environment variable that overrides the URL rule. It is how a parent
#: process hands its database to a child (Snakemake, ``run-step``, the tests).
OVERRIDE_ENV_VAR = "DATABASE_URL"

#: How long a SQLite connection waits for another writer's lock, in seconds.
BUSY_TIMEOUT_SECONDS = 30

_engine = None

#: Serialises the schema pass across this process's threads: ``create_all``
#: checks for a table and then creates it, so two threads bringing one fresh
#: database up at once collide on "already exists". Not cross-process.
_schema_lock = threading.Lock()

#: Serialises the process engine's first build, so two threads asking for it
#: at once build one engine, not two. A separate lock from ``_schema_lock``:
#: the build runs the schema pass under it.
_engine_lock = threading.Lock()


def _default_db_url() -> str:
    """SQLite at ``<project_root>/.wfc/wfc.db``.

    The state dir is not created here: a resolved root carries the marker
    inside it, so the directory already exists.
    """
    return layout.database_url(accessor.project_root())


def database_url() -> str:
    """The URL the process engine is, or would be, built on.

    The URL rule in one place: the ``DATABASE_URL`` override when it is set,
    else Layout's database for the resolved project root. A caller that has
    to name the database without an engine (the composer, when the engine
    could not be built) reads it from here.

    Returns:
        A SQLAlchemy database URL.
    """
    return os.environ.get(OVERRIDE_ENV_VAR) or _default_db_url()


@task(
    purpose="Construct a SQLAlchemy engine for a URL with the project's SQLite "
            "settings, doing no schema work",
    inputs="url: a SQLAlchemy database URL",
    outputs="a fresh, un-migrated SQLAlchemy Engine",
)
def build_engine(url: str):
    """Construct a SQLAlchemy engine for ``url`` with the project's SQLite settings.

    This is the single chokepoint for engine construction: the SQLite
    ``check_same_thread=False`` handling (needed for multi-thread use under the
    canvas server and Snakemake-spawned subprocesses) lives here instead of being
    inlined at every call site, and so does the busy timeout: a connection
    that finds the database locked by another writer waits up to
    :data:`BUSY_TIMEOUT_SECONDS` before it gives up. The journal mode is
    SQLite's default (no WAL: projects live on network drives, where WAL's
    shared memory is unsafe).

    This performs **no** schema work — it neither runs ``ensure_schema`` nor
    ``create_all``. :func:`bootstrap_engine` does both after building.

    Args:
        url: SQLAlchemy database URL (e.g. ``sqlite:///…/wfc.db``).

    Returns:
        A fresh, un-migrated SQLAlchemy ``Engine``.
    """
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = BUSY_TIMEOUT_SECONDS
    return create_engine(url, connect_args=connect_args)


@workflow(
    purpose="Build a ready engine: resolve the database URL, construct the "
            "engine, then ensure the schema",
    inputs="url: an explicit SQLAlchemy URL, or None to apply the URL rule",
    outputs="a SQLAlchemy Engine whose database carries every table, column, "
            "unique constraint and index the models declare",
)
def bootstrap_engine(url: str | None = None):
    """Build an engine for a database and bring its schema up to date.

    The process engine is built through this on first use. A caller that needs
    another project's database (``wfc init`` creating a new project) passes
    that database's URL, and owns the engine it gets back.

    The schema pass runs one thread at a time within the process, so threads
    bootstrapping one fresh database together all get a ready engine. Two
    processes doing so are not serialised.

    Args:
        url: The database URL. ``None`` applies the URL rule: the
            ``DATABASE_URL`` override, else Layout's database for the resolved
            project root.

    Returns:
        A SQLAlchemy ``Engine`` whose database carries every table, column,
        unique constraint and index the models declare.

    Raises:
        SchemaRebuildError: An existing table's rows break a uniqueness rule
            the model now declares; the tables are left unchanged.
    """
    口 = Step(step_num=1, name="Resolve the URL",
             purpose="Take the explicit URL, else the DATABASE_URL override, "
                     "else Layout's database for the resolved project root",
             outputs="url")
    if url is None:
        url = database_url()

    口 = AutoStep(step_num=2, name="Build the engine")
    engine = build_engine(url)

    口 = Step(step_num=3, name="Ensure the schema",
             purpose="Add each missing column to the existing tables, rebuild "
                     "each existing table whose unique constraints or indexes "
                     "(a partial index with its predicate) differ from the "
                     "model, then create each wholly-missing table",
             critical="The backfill runs before create_all, which never "
                      "changes a table that exists. The column pass is "
                      "best-effort; the rebuild is all or nothing and raises "
                      "SchemaRebuildError naming the table, constraint and key "
                      "when rows break the new uniqueness (for a partial "
                      "index, rows under its predicate only). One thread of "
                      "the process runs the pass at a time")
    with _schema_lock:
        ensure_schema(engine)
        SQLModel.metadata.create_all(engine)
    return engine


def get_engine():
    """Get or create the process engine.

    Returns:
        The process-wide SQLAlchemy ``Engine``, built by
        :func:`bootstrap_engine` on first use.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = bootstrap_engine()
    return _engine


@contextmanager
def get_session():
    """Yield a transactional DB session."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


@contextmanager
def write_transaction():
    """Yield a session whose block is one write transaction, locked from its start.

    A phase whose rows must land together or not at all writes them in one
    of these blocks. On SQLite the transaction begins ``IMMEDIATE``: it takes
    the database's write lock at its first statement, waiting up to the busy
    timeout for another writer, so a read inside the block is never upgraded
    to a write later (under SQLite's deferred transactions that upgrade fails
    at once with "database is locked", ignoring the timeout). On another
    dialect the transaction begins as the driver begins it.

    The block commits when it ends and rolls back when it raises, so every
    row it wrote lands or none does. Do not call ``session.commit()`` inside
    the block: the statements after it would run outside the locked
    transaction. ``flush()`` is safe (it assigns ids). Rows stay readable
    after the block (the session does not expire them on commit).

    Yields:
        A ``Session`` on the process engine, inside the open transaction.

    Raises:
        sqlalchemy.exc.OperationalError: The write lock was not granted
            within the busy timeout.
    """
    engine = get_engine()
    with Session(engine, expire_on_commit=False) as session:
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        session.commit()


def reset_engine():
    """Reset engine and project-root cache (for tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    accessor.clear_cache()


@contextmanager
def use_project(root: Path):
    """Bind this process to another project for the length of a block.

    Inside the block the process engine is that project's database and
    :func:`~wfc.persistence.project_root` answers ``root``. The two are bound
    together, so no read pairs one project's rows with another project's
    files. On exit the block's engine is disposed of, and the engine and the
    root the process held before are restored. The environment is not
    touched.

    Args:
        root: The project root to bind.

    Yields:
        The engine bound for the block.
    """
    global _engine
    root = Path(root)
    bound = bootstrap_engine(layout.database_url(root))
    saved_engine = _engine
    saved_root = accessor.swap_cache(root)
    _engine = bound
    try:
        yield bound
    finally:
        if _engine is not None and _engine not in (saved_engine, bound):
            _engine.dispose()
        bound.dispose()
        _engine = saved_engine
        accessor.swap_cache(saved_root)
