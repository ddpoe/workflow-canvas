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


def _migrate_add_cancelled_due_to_run_id(engine) -> None:
    """Idempotent ALTER TABLE to add ``runs.cancelled_due_to_run_id`` on
    databases that predate the cancelled-runs feature.

    Mirrors the ADR-004 ``error_message`` / ``error_traceback`` migration
    pattern: probe ``PRAGMA table_info(runs)`` and only issue the ALTER
    when the column is missing. Safe to call on fresh DBs -- SQLModel's
    ``create_all`` has already created the column, so the probe sees it
    and the ALTER is skipped.

    No-op on non-SQLite backends (production uses SQLite today).
    """
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            dialect = engine.dialect.name
            if dialect != "sqlite":
                return
            rows = conn.execute(text("PRAGMA table_info(runs)")).fetchall()
            cols = {row[1] for row in rows}
            if not cols:
                # ``runs`` table doesn't exist yet -- create_all will build
                # it with the column present; nothing to migrate.
                return
            if "cancelled_due_to_run_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE runs ADD COLUMN cancelled_due_to_run_id "
                    "INTEGER REFERENCES runs(id)"
                ))
                conn.commit()
    except Exception as exc:
        # Migration must be best-effort -- never block engine init on a
        # probe error. A missing column surfaces as a runtime error on
        # cancelled-row write, which is more informative than a boot hang.
        # Do log to stderr so the warning is visible in captured output
        # (tests, server logs) rather than silently swallowed.
        import sys as _sys
        print(
            f"[wfc.database] cancelled_due_to_run_id migration warning: {exc}",
            file=_sys.stderr,
        )


def get_engine():
    """Get or create the global SQLAlchemy engine."""
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL") or _default_db_url()
        # SQLite needs check_same_thread=False for multi-thread use
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(url, connect_args=connect_args)
        # Idempotent migration BEFORE create_all: on legacy DBs the ``runs``
        # table already exists without the new column, and create_all won't
        # add it. Running the probe first ensures the column is present
        # whether the DB is fresh or pre-existing.
        _migrate_add_cancelled_due_to_run_id(_engine)
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
