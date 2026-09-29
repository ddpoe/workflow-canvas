"""
Schema-backfill tests for ``wfc.persistence.ensure_schema``.

``ensure_schema`` is the model-driven, additive schema backfill. It brings an older
``.wfc/wfc.db`` up to the current model schema BEFORE ``create_all`` runs:

  - existing tables gain their newly-introduced (nullable / constant-default)
    columns via ``ALTER TABLE … ADD COLUMN``, and
  - wholly-missing tables are left for the subsequent ``create_all``.

These tests deliberately hand-roll an OLD-shape SQLite schema (the one
allowed legacy-fixture exception per ``.pev/test-policy.json`` →
``db-schema-fixtures``) to prove the backfill upgrades it so an ORM
``select(Run)`` succeeds.

The two ``ensure_schema`` upgrade tests are Tier 1 (plain pytest). The
statement-builder and refusal cases are Tier 2: they witness the
Persistence catalog cases ``backfill-statement`` and ``backfill-refusals``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from axiom_annotations import workflow
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlmodel import SQLModel, Session, select

from wfc.persistence.backfill import _add_column_ddl
from wfc.persistence import (
    build_engine, ensure_schema, get_engine,
    reset_engine,
)
from wfc.persistence import Method, Module, Run


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

    engine = build_engine(f"sqlite:///{db_path}")
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
    reject, not a silent working backend.
    """
    db_path = tmp_path / "legacy_methods.db"
    _make_legacy_db(db_path)  # methods has a row, no env column

    engine = build_engine(f"sqlite:///{db_path}")
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


@workflow(purpose="The backfill's statement builder turns one missing column "
                  "into the ALTER statement SQLite accepts on a populated "
                  "table, or into no statement when SQLite could not add it",
          inputs="Literal columns of every shape the models use, the SQLite "
                 "dialect",
          outputs="One ALTER statement per addable column; None otherwise")
def test_backfill_statement_for_each_column_shape():
    """Persistence catalog ``backfill-statement``: the pure kernel, no database."""
    dialect = sqlite_dialect.dialect()

    def ddl(column):
        return _add_column_ddl("t", column, dialect)

    # A nullable column needs no default.
    assert ddl(Column("note", String)) == "ALTER TABLE t ADD COLUMN note VARCHAR"
    # A constant Python default becomes a SQL DEFAULT literal: text quoted
    # with its embedded quote doubled, a number bare, a boolean as 1 or 0.
    assert ddl(Column("label", String, nullable=False, default="it's")) == (
        "ALTER TABLE t ADD COLUMN label VARCHAR NOT NULL DEFAULT 'it''s'"
    )
    assert ddl(Column("count", Integer, nullable=False, default=3)) == (
        "ALTER TABLE t ADD COLUMN count INTEGER NOT NULL DEFAULT 3"
    )
    assert ddl(Column("flag", Boolean, nullable=False, default=True)) == (
        "ALTER TABLE t ADD COLUMN flag BOOLEAN NOT NULL DEFAULT 1"
    )
    assert ddl(Column("off", Boolean, nullable=False, default=False)) == (
        "ALTER TABLE t ADD COLUMN off BOOLEAN NOT NULL DEFAULT 0"
    )
    # A callable default is Python-side only, and a NOT NULL column with no
    # default at all has nothing to fill existing rows with: no statement.
    assert ddl(Column("stamp", DateTime, nullable=False, default=datetime.now)) is None
    assert ddl(Column("name", String, nullable=False)) is None


def _make_modules_missing_two_columns(db_path: Path) -> None:
    """Create a populated ``modules`` table missing ``description`` and ``name``.

    LEGACY-SHAPE FIXTURE (frozen on purpose): hand-written DDL for an older
    table, the one case the test policy permits raw ``CREATE TABLE``.
    ``description`` is nullable in the model; ``name`` is NOT NULL with neither
    a constant default nor a ``server_default``, so SQLite cannot add it to a
    table that already has rows.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE modules (id INTEGER PRIMARY KEY);
        INSERT INTO modules (id) VALUES (1);
        """
    )
    conn.commit()
    conn.close()


@workflow(purpose="Bringing an older database up to the models adds what it "
                  "can, skips a column SQLite cannot add, and never raises, "
                  "so building the engine is not blocked by the skipped column",
          inputs="An older populated table missing one nullable column and one "
                 "NOT NULL column with no usable default, bound by the override",
          outputs="The nullable column added, the other absent, the engine built")
def test_backfill_skips_what_it_cannot_add_and_never_raises(tmp_path, monkeypatch):
    """Persistence catalog ``backfill-refusals``."""
    db_path = tmp_path / "older.db"
    _make_modules_missing_two_columns(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    try:
        engine = get_engine()  # the bootstrap: backfill, then create_all
        assert engine is not None
    finally:
        reset_engine()

    conn = sqlite3.connect(str(db_path))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(modules)")}
    rows = conn.execute("SELECT id FROM modules").fetchall()
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "description" in columns, "the addable nullable column was not added"
    assert "name" not in columns, "a NOT NULL column with no default cannot land"
    assert rows == [(1,)], "the existing row survives the pass"
    assert "runs" in tables, "the pass continued: create_all still built the rest"
