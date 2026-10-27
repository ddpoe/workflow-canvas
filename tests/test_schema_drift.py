"""The schema-drift witness: the previous release's database opens as a fresh one.

A database built from the latest release's schema (the frozen DDL snapshot in
``tests/fixtures/schema_snapshots``, plus a few rows) is opened through
``bootstrap_engine``. Every table's columns, unique constraints and indexes
(a partial index with its predicate) must then equal those of a fresh
``create_all`` database, and the rows must survive. The runs rows hold the
histories a key may hold twice (a retried failure, an outputs-missing rerun,
stale running rows), which the running-claim index must accept. A model change that does not reach an existing database fails here,
naming the table and the difference.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from axiom_annotations import workflow
from sqlmodel import SQLModel

from tests.fixtures.schema_snapshots import (
    SEED_ROWS,
    SNAPSHOT_RELEASE,
    build_release_database,
    seed_sql,
)
from wfc.persistence import bootstrap_engine, build_engine


def _shape(db_path: Path) -> dict[str, dict]:
    """Read every table's columns and uniqueness/index rules from a database.

    Columns are keyed by name (ADD COLUMN appends, so order is not compared)
    with their declared type, NOT NULL flag, default and primary-key position.
    Unique constraints and indexes are normalised to (columns, unique,
    predicate) so a constraint's autoindex and an explicit unique index on the
    same columns compare alike; an explicit index also keeps its name. A
    partial index's predicate is its ``WHERE`` clause with whitespace
    collapsed; ``None`` for an index over every row.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        shape = {}
        for table in tables:
            columns = {
                r[1]: (r[2].upper(), r[3], r[4], r[5])
                for r in conn.execute(f"PRAGMA table_info({table})")
            }
            rules = set()
            for _seq, name, unique, origin, partial in conn.execute(
                f"PRAGMA index_list({table})"
            ):
                if origin == "pk":
                    continue
                cols = tuple(
                    r[2] for r in sorted(conn.execute(f"PRAGMA index_info({name})"))
                )
                predicate = None
                if partial:
                    sql = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='index' "
                        "AND name=?", (name,)
                    ).fetchone()[0]
                    predicate = " ".join(sql.split(" WHERE ", 1)[1].split())
                rules.add((name if origin == "c" else None, cols, bool(unique),
                           predicate))
            shape[table] = {"columns": columns, "rules": rules}
        return shape
    finally:
        conn.close()


def _differences(upgraded: dict, fresh: dict) -> list[str]:
    """Name each table and each way the upgraded database differs from fresh."""
    out = []
    for table in sorted(set(fresh) - set(upgraded)):
        out.append(f"{table}: missing from the upgraded database")
    for table in sorted(set(upgraded) - set(fresh)):
        out.append(f"{table}: not in a fresh database")
    for table in sorted(set(fresh) & set(upgraded)):
        got, want = upgraded[table], fresh[table]
        for col in sorted(set(want["columns"]) - set(got["columns"])):
            out.append(f"{table}: column {col} missing")
        for col in sorted(set(got["columns"]) - set(want["columns"])):
            out.append(f"{table}: column {col} not in the model")
        for col in sorted(set(want["columns"]) & set(got["columns"])):
            if got["columns"][col] != want["columns"][col]:
                out.append(
                    f"{table}: column {col} is {got['columns'][col]}, "
                    f"fresh is {want['columns'][col]} (type, notnull, default, pk)"
                )
        for rule in sorted(want["rules"] - got["rules"], key=repr):
            out.append(f"{table}: missing unique constraint/index {rule}")
        for rule in sorted(got["rules"] - want["rules"], key=repr):
            out.append(f"{table}: unique constraint/index {rule} not in the model")
    return out


@workflow(purpose="A database created by the previous release opens through "
                  "bootstrap_engine with the same tables, columns, unique "
                  "constraints and indexes as a fresh database, and its rows "
                  "survive the upgrade",
          inputs="The frozen release snapshot's DDL with a few rows in modules, "
                 "methods, method_versions, method_contracts, runs and "
                 "run_inputs, the runs holding a failed-then-retried key, an "
                 "outputs-missing rerun, a cache-hit audit row and two stale "
                 "running rows under one key; a fresh create_all database",
          outputs="No schema difference between the two databases, partial "
                  "index predicates included; the runs table carries the "
                  "running-claim index; every seeded row read back unchanged")
def test_previous_release_database_opens_with_the_current_schema(tmp_path):
    """Schema-drift witness."""
    old_db = tmp_path / f"wfc-{SNAPSHOT_RELEASE}.db"
    build_release_database(old_db, seed_sql())

    engine = bootstrap_engine(f"sqlite:///{old_db}")
    engine.dispose()

    fresh_db = tmp_path / "fresh.db"
    fresh = build_engine(f"sqlite:///{fresh_db}")
    SQLModel.metadata.create_all(fresh)
    fresh.dispose()

    upgraded = _shape(old_db)
    differences = _differences(upgraded, _shape(fresh_db))
    assert not differences, (
        f"A {SNAPSHOT_RELEASE} database opened by this release differs from a "
        "fresh one:\n" + "\n".join(differences)
    )
    running_claim = [
        rule for rule in upgraded["runs"]["rules"]
        if rule[1] == ("method_id", "sample", "cache_key") and rule[2]
    ]
    assert running_claim and running_claim[0][3], (
        "the upgraded runs table lacks the partial unique running-claim index"
    )

    conn = sqlite3.connect(str(old_db))
    try:
        for table, (columns, rows) in SEED_ROWS.items():
            got = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
            ).fetchall()
            assert got == rows, f"{table}: rows did not survive the upgrade"
    finally:
        conn.close()
