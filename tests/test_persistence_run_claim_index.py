"""Persistence's arbitration settings: the running-claim index, the busy
timeout and the write transaction.

- The partial unique index over (``method_id``, ``sample``, ``cache_key``)
  refuses a second running claim (a row with a heartbeat) under one key, and
  accepts every other row a key can hold: a cache-hit audit row, a failed
  run, a completed rerun, and a running row with no heartbeat (written before
  this release, or by the hand-run verb).
- The SQLite engine waits up to the busy timeout for another writer.
- ``write_transaction`` holds the database's write lock from its first
  statement, keeps its rows when the block ends and discards them all when it
  raises.

Tier 1 (plain pytest): Persistence's own rules, entered at its facade.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from wfc.persistence import (
    BUSY_TIMEOUT_SECONDS,
    Method,
    Module,
    Run,
    bootstrap_engine,
    get_session,
    write_transaction,
)

KEY = "k" * 64


@pytest.fixture
def engine(tmp_path):
    """A fresh database built by the production bootstrap, with one method."""
    engine = bootstrap_engine(f"sqlite:///{tmp_path / 'wfc.db'}")
    with Session(engine) as session:
        module = Module(name="qc")
        session.add(module)
        session.flush()
        session.add(Method(module_id=module.id, name="filter", env="pixi:a"))
        session.commit()
    yield engine
    engine.dispose()


def _add(engine, **fields) -> None:
    """Insert one run row of method 1 on sample S1 under ``KEY``."""
    row = {"method_id": 1, "sample": "S1", "cache_key": KEY, **fields}
    with Session(engine) as session:
        session.add(Run(**row))
        session.commit()


def test_index_refuses_a_second_running_claim_under_one_key(engine):
    _add(engine, status="running", heartbeat=0)

    with pytest.raises(IntegrityError):
        _add(engine, status="running", heartbeat=0)


def test_index_accepts_every_row_that_is_not_a_second_running_claim(engine):
    _add(engine, status="running", heartbeat=0)
    # A cache-hit audit row of the running claim's key.
    _add(engine, status="running", heartbeat=3, cache_source_run_id=1)
    # A failed run and a completed rerun under the same key.
    _add(engine, status="failed", heartbeat=7)
    _add(engine, status="completed", heartbeat=2)
    _add(engine, status="cancelled")
    # Running rows with no heartbeat: before this release, or register_run.
    _add(engine, status="running")
    _add(engine, status="running")
    # Another sample, and another key, may each hold their own running claim.
    _add(engine, status="running", heartbeat=0, sample="S2")
    _add(engine, status="running", heartbeat=0, cache_key="j" * 64)

    with Session(engine) as session:
        assert len(session.exec(select(Run)).all()) == 9


def test_a_claim_that_leaves_running_frees_its_key(engine):
    _add(engine, status="running", heartbeat=0)
    with Session(engine) as session:
        first = session.get(Run, 1)
        first.status = "failed"
        session.add(first)
        session.commit()

    _add(engine, status="running", heartbeat=0)


def test_sqlite_engine_waits_the_busy_timeout(engine):
    assert BUSY_TIMEOUT_SECONDS == 30
    with engine.connect() as conn:
        timeout_ms = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert timeout_ms == BUSY_TIMEOUT_SECONDS * 1000


def _try_to_write(db_path) -> str | None:
    """Ask a second connection, waiting not at all, for the write lock."""
    other = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        return None
    except sqlite3.OperationalError as exc:
        return str(exc)
    finally:
        other.close()


def test_write_transaction_holds_the_write_lock_from_its_start(tmp_project):
    db_path = tmp_project / ".wfc" / "wfc.db"
    with write_transaction() as session:
        # Only a read so far: a deferred transaction would hold no write lock.
        session.exec(select(Module)).all()
        refused = _try_to_write(db_path)

    assert refused is not None and "locked" in refused
    assert _try_to_write(db_path) is None, "the lock is released on exit"


def test_write_transaction_keeps_its_rows_or_none_of_them(tmp_project):
    with write_transaction() as session:
        session.add(Module(name="kept-a"))
        session.flush()
        session.add(Module(name="kept-b"))

    with pytest.raises(RuntimeError, match="phase failed"):
        with write_transaction() as session:
            session.add(Module(name="discarded-a"))
            session.flush()
            session.add(Module(name="discarded-b"))
            session.flush()
            raise RuntimeError("phase failed")

    with get_session() as fresh:
        names = sorted(fresh.exec(select(Module.name)).all())
    assert "kept-a" in names and "kept-b" in names
    assert not [n for n in names if n.startswith("discarded")]


def test_write_transaction_rows_stay_readable_after_the_block(tmp_project):
    with write_transaction() as session:
        module = Module(name="readable")
        session.add(module)
        session.flush()

    assert module.id is not None
    assert module.name == "readable"
