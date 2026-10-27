"""Every keyed run records the version of the key recipe that built its key.

``Run.key_recipe`` is stamped by the claim phase on the two keyed rows it
writes (a cache miss's fresh row and a cache hit's audit row). A database
whose ``runs`` table predates the column reads its keyed runs as recipe 1
after the additive backfill, and a keyless row stores NULL: the recipe only
means something on a row that has a key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from axiom_annotations import workflow
from sqlmodel import Session, SQLModel, select

from tests.fixtures.routes import completed_run
from tests.fixtures.routes.runs import ROUTE_METHOD
from tests.harness.scenario import DEFAULT_MODULE, DEFAULT_SAMPLE
from wfc.identity import KEY_RECIPE
from wfc.persistence import Run, build_engine, ensure_schema, get_session


def _make_pre_column_db(db_path: Path) -> None:
    """Create a DB whose ``runs`` table predates ``key_recipe``.

    LEGACY-SHAPE FIXTURE (declared deviation from building state through
    production paths): no production path writes a ``runs`` table without
    ``key_recipe`` any more, so the pre-upgrade state is hand-rolled DDL. It
    holds one keyed run and one keyless run.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        f"""
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT, script_path TEXT
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            params TEXT,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            cache_key VARCHAR(64),
            env_fingerprint VARCHAR(32)
        );
        INSERT INTO modules (id, name) VALUES (1, 'mod');
        INSERT INTO methods (id, module_id, name) VALUES (1, 1, 'method_a');
        INSERT INTO runs (id, method_id, sample, status, cache_key)
            VALUES (1, 1, 'S1', 'completed', '{"a" * 64}');
        INSERT INTO runs (id, method_id, sample, status)
            VALUES (2, 1, 'S1', 'completed');
        """
    )
    conn.commit()
    conn.close()


@workflow(purpose="A keyed claim records the key recipe that built its key, on a "
                  "cache miss's fresh row and on a cache hit's audit row")
def test_keyed_claims_record_the_key_recipe(tmp_project, monkeypatch):
    """Both keyed rows the claim phase writes carry ``KEY_RECIPE``."""
    miss = completed_run(tmp_project, monkeypatch=monkeypatch)
    hit = completed_run(tmp_project, monkeypatch=monkeypatch)

    assert hit.run_row["cache_source_run_id"] == miss.run_id
    assert miss.run_row["key_recipe"] == KEY_RECIPE
    assert hit.run_row["key_recipe"] == KEY_RECIPE


@workflow(purpose="Keyed runs in a database created before the key_recipe column "
                  "read recipe 1 after the additive schema backfill")
def test_pre_column_keyed_runs_read_recipe_one(tmp_path):
    """The backfill adds the column; existing keyed rows read 1, unrewritten."""
    db_path = tmp_path / "pre_column.db"
    _make_pre_column_db(db_path)

    engine = build_engine(f"sqlite:///{db_path}")
    ensure_schema(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        keyed = session.exec(select(Run).where(Run.id == 1)).one()
        assert keyed.cache_key is not None
        assert keyed.key_recipe == 1
    engine.dispose()


@workflow(purpose="A keyless run written through a production path stores no key "
                  "recipe")
def test_keyless_run_stores_no_key_recipe(tmp_project, monkeypatch):
    """``register_run`` writes a row with no cache key and a NULL recipe."""
    from wfc.execution.claim import register_run

    completed_run(tmp_project, monkeypatch=monkeypatch)
    run_id = register_run(ROUTE_METHOD, DEFAULT_MODULE, DEFAULT_SAMPLE)

    with get_session() as session:
        row = session.get(Run, run_id)
        assert row.cache_key is None
        assert row.key_recipe is None
