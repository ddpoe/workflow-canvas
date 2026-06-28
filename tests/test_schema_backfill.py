"""
Schema-backfill tests for ``wfc.database.ensure_schema``.

``ensure_schema`` is the model-driven, additive replacement for the old
one-off ``_migrate_add_cancelled_due_to_run_id``. It brings an older
``.wfc/wfc.db`` up to the current model schema BEFORE ``create_all`` runs:

  - existing tables gain their newly-introduced (nullable / constant-default)
    columns via ``ALTER TABLE … ADD COLUMN``, and
  - wholly-missing tables are left for the subsequent ``create_all``.

These tests deliberately hand-roll an OLD-shape SQLite schema (the one
allowed legacy-fixture exception per ``.pev/test-policy.json`` →
``db-schema-fixtures``) to prove the backfill upgrades it so an ORM
``select(Run)`` succeeds.

Tier 1: plain pytest — internal database primitive, no subsystem boundary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlmodel import SQLModel, Session, select

from wfc.database import build_engine, make_sqlite_url, ensure_schema
from wfc.models import Method, Module, Run


def _make_legacy_db(db_path: Path) -> None:
    """Create a DB whose ``runs`` table predates several nullable columns and
    whose ``run_annotations`` table is entirely absent.

    LEGACY-SHAPE FIXTURE (frozen on purpose): hand-rolled DDL recreates an
    *old* schema to exercise the back-compat ``ensure_schema`` backfill. This
    is the one case the test-policy permits raw ``CREATE TABLE`` — do not
    "fix" it to ``create_all``; the whole point is the missing columns/table.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT, script_path TEXT
        );
        -- runs is missing the newer nullable columns (cancelled_due_to_run_id,
        -- cache_source_run_id, version_id, metrics, nid, error_*, …) and
        -- run_annotations does not exist at all.
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            params TEXT,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            started_at TEXT,
            finished_at TEXT
        );
        INSERT INTO modules (id, name) VALUES (1, 'mod');
        INSERT INTO methods (id, module_id, name) VALUES (1, 1, 'method_a');
        INSERT INTO runs (id, method_id, sample, status) VALUES (1, 1, 'S1', 'completed');
        """
    )
    conn.commit()
    conn.close()


def test_ensure_schema_adds_missing_column_and_table(tmp_path):
    """A drifted DB (runs missing a newer nullable column + run_annotations
    absent) is upgraded by ensure_schema + create_all so an ORM read works."""
    db_path = tmp_path / "legacy.db"
    _make_legacy_db(db_path)

    # Sanity: the legacy DB really is missing the column and the table.
    conn = sqlite3.connect(str(db_path))
    before_runcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    before_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "cancelled_due_to_run_id" not in before_runcols
    assert "run_annotations" not in before_tables

    engine = build_engine(make_sqlite_url(db_path))
    # ensure_schema backfills existing tables; create_all builds the missing ones.
    ensure_schema(engine)
    SQLModel.metadata.create_all(engine)

    conn = sqlite3.connect(str(db_path))
    after_runcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    after_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    # The missing nullable column was added to the existing table…
    assert "cancelled_due_to_run_id" in after_runcols
    assert "cache_source_run_id" in after_runcols
    assert "version_id" in after_runcols
    assert "metrics" in after_runcols
    assert "nid" in after_runcols
    # …and the wholly-missing table now exists (built by create_all).
    assert "run_annotations" in after_tables

    # A subsequent ORM read over the upgraded schema succeeds.
    with Session(engine) as session:
        runs = session.exec(select(Run)).all()
    assert len(runs) == 1
    assert runs[0].sample == "S1"
    assert runs[0].status == "completed"
    assert runs[0].cancelled_due_to_run_id is None
    engine.dispose()


def test_ensure_schema_backfills_notnull_column_with_default_on_populated_table(tmp_path):
    """A legacy ``methods`` table with rows but no ``env`` column gains it with
    the model's DB-only ``server_default`` ('') applied to existing rows.

    SQLite refuses ADD COLUMN for a NOT-NULL column on a populated table unless
    a constant DEFAULT is supplied. ``Method.env`` carries no Python-side
    default (a method must declare its env), but a DB-level ``server_default=''``
    lets ensure_schema add the column without stranding pre-existing rows.
    Backfilled legacy rows get ``''`` — the sentinel the run-time env guards
    reject, not a silent working backend (the old behaviour backfilled
    ``'inherit'``, which this cycle removed).
    """
    db_path = tmp_path / "legacy_methods.db"
    _make_legacy_db(db_path)  # methods has a row, no env column

    engine = build_engine(make_sqlite_url(db_path))
    ensure_schema(engine)
    SQLModel.metadata.create_all(engine)

    conn = sqlite3.connect(str(db_path))
    meth_cols = {r[1] for r in conn.execute("PRAGMA table_info(methods)")}
    env_value = conn.execute("SELECT env FROM methods WHERE id=1").fetchone()
    conn.close()
    assert "env" in meth_cols
    assert env_value == ("",)

    with Session(engine) as session:
        method = session.exec(select(Method)).first()
        module = session.exec(select(Module)).first()
    assert method is not None and method.env == ""
    assert module is not None and module.name == "mod"
    engine.dispose()
