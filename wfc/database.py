"""
Database engine and session management.

Uses DATABASE_URL env var (defaults to SQLite at <project_root>/.wfc/wfc.db).
Auto-creates all tables on first access.

Project-root resolution is explicit (not cwd-derived): see ``project_root()``.
This matters for any wfc subprocess whose cwd is not the project — notably
Snakemake-spawned shell rules under Windows UNC paths, where cmd.exe silently
rewrites cwd to C:\\Windows\\ and the old cwd-based resolver tried to mkdir
``C:\\Windows\\.wfc``.
"""

import os
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

# Import all models so SQLModel.metadata knows about them
from .models import (  # noqa: F401
    Module, Method, MethodVersion, TrackedFunction, ParamDef,
    Run, RunInput, RunOutput, Sample, ModuleContract, MethodContract,
)

_engine = None
_project_root_cache: Path | None = None


def migrate_legacy_state_dir(project_dir: Path) -> bool:
    """Migrate a legacy ``.pm/`` state directory to ``.wfc/`` (one-time).

    The placeholder ``pm`` name was renamed to ``wfc`` (ADR-021). Existing
    projects keep their state in ``.pm/`` with the SQLite database named
    ``pm.db``. This function performs a safe, one-time, idempotent migration:

    1. If ``project_dir/.wfc/`` already exists, do nothing (already migrated,
       or a fresh project) and return ``False``.
    2. If ``project_dir/.pm/`` does not exist, there is nothing to migrate;
       return ``False``.
    3. Otherwise: back up ``.pm/`` to a timestamped sibling
       ``.pm.bak-<UTC-timestamp>/`` **before any destructive move**, then move
       ``.pm/`` → ``.wfc/`` and rename ``.wfc/pm.db`` → ``.wfc/wfc.db``.
       Return ``True``.

    The backup-before-move ordering is the load-bearing safety guarantee: the
    original state is fully copied aside before the directory is touched, so a
    failure mid-move never leaves the project without its data.

    Args:
        project_dir: The project root directory to (possibly) migrate.

    Returns:
        ``True`` if a migration was performed, ``False`` if it was a no-op.
    """
    import shutil
    from datetime import datetime, timezone

    project_dir = Path(project_dir)
    wfc_dir = project_dir / ".wfc"
    legacy_dir = project_dir / ".pm"

    # Idempotent: a project already on .wfc/ is left untouched.
    if wfc_dir.exists():
        return False
    if not legacy_dir.is_dir():
        return False

    # 1. Back up .pm/ BEFORE touching it.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = project_dir / f".pm.bak-{timestamp}"
    # Defensive against a (vanishingly unlikely) same-second collision.
    suffix = 0
    while backup_dir.exists():
        suffix += 1
        backup_dir = project_dir / f".pm.bak-{timestamp}-{suffix}"
    shutil.copytree(legacy_dir, backup_dir)

    # 2. Move .pm/ -> .wfc/ (only after the backup exists).
    legacy_dir.rename(wfc_dir)

    # 3. Rename the db file pm.db -> wfc.db inside the new .wfc/.
    legacy_db = wfc_dir / "pm.db"
    new_db = wfc_dir / "wfc.db"
    if legacy_db.exists() and not new_db.exists():
        legacy_db.rename(new_db)

    return True


def project_root() -> Path:
    """Resolve the wfc project root directory.

    Precedence:
      1. ``WFC_PROJECT_ROOT`` environment variable, if set (must point at a
         directory containing ``.wfc/wf-canvas.toml``).
      2. Walk upward from ``Path.cwd()`` looking for a ``.wfc/wf-canvas.toml``
         marker.
      3. Raise ``RuntimeError`` — do not silently create ``.wfc/`` in a wrong
         directory.

    Result is cached per-process. Call ``reset_engine()`` to clear the cache
    in tests.
    """
    global _project_root_cache
    if _project_root_cache is not None:
        return _project_root_cache

    env = os.environ.get("WFC_PROJECT_ROOT")
    if env:
        root = Path(env).resolve()
        # One-time legacy .pm/ -> .wfc/ migration (ADR-021).
        migrate_legacy_state_dir(root)
        marker = root / ".wfc" / "wf-canvas.toml"
        if not marker.exists():
            raise RuntimeError(
                f"WFC_PROJECT_ROOT={root} does not contain .wfc/wf-canvas.toml — "
                f"not a workflow-canvas project"
            )
        _project_root_cache = root
        return root

    start = Path.cwd().resolve()
    for candidate in (start, *start.parents):
        # Migrate a legacy .pm/ project encountered during the walk so the
        # .wfc/wf-canvas.toml marker check below sees the migrated layout.
        if (candidate / ".pm" / "wf-canvas.toml").exists():
            migrate_legacy_state_dir(candidate)
        if (candidate / ".wfc" / "wf-canvas.toml").exists():
            _project_root_cache = candidate
            return candidate

    raise RuntimeError(
        f"Could not resolve workflow-canvas project root from cwd={start}. "
        f"Set WFC_PROJECT_ROOT or run wfc from a directory inside a project "
        f"(one containing .wfc/wf-canvas.toml)."
    )


def _default_db_url() -> str:
    """SQLite at ``<project_root>/.wfc/wfc.db``."""
    db_dir = project_root() / ".wfc"
    db_dir.mkdir(exist_ok=True)
    return f"sqlite:///{db_dir / 'wfc.db'}"


def make_sqlite_url(db_path) -> str:
    """Return a SQLite SQLAlchemy URL for a filesystem database path.

    Args:
        db_path: Path (str or ``pathlib.Path``) to the ``.db`` file.

    Returns:
        A ``sqlite:///<abs-path>`` URL string suitable for ``build_engine``.
    """
    return f"sqlite:///{Path(db_path)}"


def build_engine(url: str):
    """Construct a SQLAlchemy engine for ``url`` with the project's SQLite settings.

    This is the single chokepoint for engine construction: the SQLite
    ``check_same_thread=False`` handling (needed for multi-thread use under the
    canvas server and Snakemake-spawned subprocesses) lives here instead of being
    inlined at every call site. Both :func:`get_engine` (global engine) and the
    canvas history provider (per-load ``db_path``-bound engine) build through it.

    Unlike :func:`get_engine`, this performs **no** schema work — it neither runs
    ``ensure_schema`` nor ``create_all``. Callers decide whether to migrate.

    Args:
        url: SQLAlchemy database URL (e.g. ``sqlite:///…/wfc.db``).

    Returns:
        A fresh, un-migrated SQLAlchemy ``Engine``.
    """
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args)


def _add_column_ddl(table_name: str, column, dialect) -> str | None:
    """Build the ``ALTER TABLE … ADD COLUMN`` statement for one missing column.

    The column type and NULL/NOT-NULL clause are compiled exactly as
    ``create_all`` would for the given dialect (so the ADDed column matches the
    model). Foreign-key and primary-key clauses are dropped by ``CreateColumn``
    for ADD COLUMN, which is what SQLite's ``ALTER … ADD COLUMN`` accepts.

    For a NOT-NULL column SQLite refuses ``ADD COLUMN`` on a populated table
    unless a constant ``DEFAULT`` is supplied. When the model carries a scalar
    Python-side default (e.g. ``env="inherit"``, ``push_status="deferred"``) and
    no SQL ``server_default``, that default is rendered as a literal ``DEFAULT``
    so existing rows backfill cleanly. A NOT-NULL column with no constant default
    (and no server_default) cannot be added additively and returns ``None``.

    Args:
        table_name: Name of the table to alter.
        column: The SQLAlchemy ``Column`` to add.
        dialect: The SQLAlchemy dialect to compile against (SQLite).

    Returns:
        The full ``ALTER TABLE …`` SQL string, or ``None`` if the column is not
        additively safe.
    """
    from sqlalchemy.schema import CreateColumn

    col_spec = CreateColumn(column).compile(dialect=dialect).string

    if not column.nullable and column.server_default is None:
        default = column.default
        literal = getattr(default, "arg", None) if default is not None else None
        # Only scalar constants are usable as a SQL DEFAULT; callables (e.g.
        # datetime.now factories) are Python-side only.
        if literal is None or callable(literal):
            # No constant default for a NOT-NULL column -- not additively safe
            # under SQLite on a populated table.
            return None
        if isinstance(literal, bool):
            default_sql = "1" if literal else "0"
        elif isinstance(literal, (int, float)):
            default_sql = str(literal)
        else:
            escaped = str(literal).replace("'", "''")
            default_sql = f"'{escaped}'"
        col_spec = f"{col_spec} DEFAULT {default_sql}"

    return f"ALTER TABLE {table_name} ADD COLUMN {col_spec}"


def ensure_schema(engine) -> None:
    """Additively backfill an existing SQLite DB to match the current models.

    The project has no migration system: ``create_all`` builds wholly-missing
    tables but never adds a newly-introduced column to a table that already
    exists. This function closes that gap model-driven: for every table in
    ``SQLModel.metadata`` that ALREADY EXISTS in the database, it diffs
    ``PRAGMA table_info`` against the model's declared columns and issues
    ``ALTER TABLE <t> ADD COLUMN <c>`` for each missing one (column type and
    nullability/default derived from the model definition).

    Run this BEFORE ``create_all`` so that:

    * existing tables gain their missing columns here, and
    * wholly-missing tables (e.g. ``run_annotations`` on an old DB) are built by
      the subsequent ``create_all``.

    Contract / guard rails:

    * **SQLite-only.** No-op on any other dialect (production is SQLite today;
      the additive-``ADD COLUMN`` mechanics below are SQLite-specific).
    * **Additive only.** Renames, drops, type changes are out of scope. A NOT-NULL
      column is added only when it carries a constant Python default (emitted as a
      SQL ``DEFAULT`` literal so existing rows backfill); a NOT-NULL column with no
      constant default cannot be added to a populated table under SQLite and is
      skipped (best-effort) — this matches the schema, where every drifted column
      is either nullable or constant-defaulted.
    * **Per-column resilient.** One column's ALTER failing does not abort the rest.
    * **Best-effort.** Never raises; probe/ALTER errors are logged to stderr and
      swallowed so engine init / provider load is never blocked.

    Args:
        engine: SQLAlchemy engine bound to the database to upgrade.
    """
    from sqlalchemy import text
    try:
        if engine.dialect.name != "sqlite":
            return
        dialect = engine.dialect
        with engine.connect() as conn:
            for table in SQLModel.metadata.sorted_tables:
                rows = conn.execute(
                    text(f"PRAGMA table_info({table.name})")
                ).fetchall()
                existing = {row[1] for row in rows}
                if not existing:
                    # Table doesn't exist yet -- create_all will build it whole.
                    continue
                for column in table.columns:
                    if column.name in existing:
                        continue
                    ddl = _add_column_ddl(table.name, column, dialect)
                    if ddl is None:
                        continue
                    try:
                        conn.execute(text(ddl))
                    except Exception as col_exc:
                        # One un-addable column must not abort the rest of the
                        # backfill. Log and continue.
                        import sys as _sys
                        print(
                            f"[wfc.database] ensure_schema: could not add "
                            f"{table.name}.{column.name}: {col_exc}",
                            file=_sys.stderr,
                        )
            conn.commit()
    except Exception as exc:
        # Backfill is best-effort -- never block engine init / provider load on
        # a probe error. A genuinely missing column surfaces later as a clear
        # runtime error, which is more informative than a boot hang. Log to
        # stderr so the warning is visible in captured output.
        import sys as _sys
        print(
            f"[wfc.database] ensure_schema backfill warning: {exc}",
            file=_sys.stderr,
        )


def get_engine():
    """Get or create the global SQLAlchemy engine."""
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL") or _default_db_url()
        _engine = build_engine(url)
        # Additive backfill BEFORE create_all: on legacy DBs an existing table
        # (e.g. ``runs``) may be missing newly-introduced columns, and
        # create_all won't add them. ensure_schema closes that gap; create_all
        # then builds any wholly-missing tables.
        ensure_schema(_engine)
        SQLModel.metadata.create_all(_engine)
    return _engine


@contextmanager
def get_session():
    """Yield a transactional DB session."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


def reset_engine():
    """Reset engine and project-root cache (for tests)."""
    global _engine, _project_root_cache
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _project_root_cache = None


def runs_dir() -> Path:
    """Return (and create) the ``.runs/`` artifact store under the project root."""
    d = project_root() / ".runs"
    d.mkdir(exist_ok=True)
    return d
