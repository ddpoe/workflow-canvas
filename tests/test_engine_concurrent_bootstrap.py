"""Two threads bootstrapping one fresh database both get a ready engine.

``bootstrap_engine`` brings a database up to the models before it hands the
engine back, and ``create_all`` checks for a table and then creates it. Two
threads of one process doing that at once on a fresh file race: one creates a
table or index the other has just seen missing, and its CREATE fails with
"already exists". The bootstrap is serialised within the process, so every
thread gets an engine on a database that carries every table.

Real threads and a real SQLite file; nothing is stubbed.
"""

from __future__ import annotations

import threading

from sqlalchemy import inspect
from sqlmodel import SQLModel

from wfc.persistence.engine import bootstrap_engine

THREADS = 8
ROUNDS = 10


def _bootstrap(url: str, start: threading.Barrier, errors: list[str]) -> None:
    """Wait for every thread, then bootstrap ``url``; record what it raised."""
    start.wait()
    try:
        bootstrap_engine(url).dispose()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}".splitlines()[0])


def test_threads_bootstrapping_one_fresh_database_all_get_a_ready_engine(tmp_path):
    for round_no in range(ROUNDS):
        url = f"sqlite:///{tmp_path / f'round-{round_no}.db'}"
        start = threading.Barrier(THREADS)
        errors: list[str] = []
        threads = [threading.Thread(target=_bootstrap, args=(url, start, errors))
                   for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == [], f"round {round_no}: {errors}"
        engine = bootstrap_engine(url)
        try:
            assert set(inspect(engine).get_table_names()) >= set(SQLModel.metadata.tables)
        finally:
            engine.dispose()
