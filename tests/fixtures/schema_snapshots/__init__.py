"""Released database schemas, frozen on purpose.

LEGACY-SHAPE FIXTURE: ``wfc-<release>.sql`` is the DDL of that release, dumped
from its models. It is the one case the testing SOP permits raw
``CREATE TABLE`` (Database-Schema Test Fixtures, schema-drift witness): a
database built from it stands for a user's database created by that release,
which this release must open with every schema change applied.
:data:`SNAPSHOT_RELEASE` is the latest release, refreshed at each release.
:data:`BACKFILL_RELEASE` is the older release whose databases the backfill
tests upgrade; it stays while wfc still backfills that release's shape.
Never edit a snapshot to match the current models.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: The latest release, whose snapshot the schema-drift witness opens.
SNAPSHOT_RELEASE = "0.7.0"

#: The release whose databases the backfill tests upgrade: it predates the
#: methods uniqueness rule and the runs' cancel reason.
BACKFILL_RELEASE = "0.6.1"


def snapshot_ddl(release: str) -> Path:
    """Return the checked-in DDL file of *release*."""
    return Path(__file__).parent / f"wfc-{release}.sql"


#: The checked-in DDL of the latest release.
SNAPSHOT_DDL = snapshot_ddl(SNAPSHOT_RELEASE)

#: Rows a released database holds, written against the 0.6.1 columns, which
#: every later release keeps: modules, methods, method_versions,
#: method_contracts, runs and a lineage row (run_inputs). Run 2
#: (``align.filter``) is fed by run 1 (``qc.filter``); both ran version rows
#: with no environment fingerprint.
#:
#: The other runs are the histories a key can hold that the running-claim
#: index must leave alone: run 3 failed and run 4 retried its key; run 5
#: re-ran run 1's key after its outputs went missing; run 6 is a cache-hit
#: audit row of run 1; runs 7 and 8 are running rows two killed claims left
#: under one key.
SEED_ROWS: dict[str, tuple[tuple[str, ...], list[tuple]]] = {
    "modules": (("id", "name", "description"), [
        (1, "qc", "Quality control"),
        (2, "align", None),
    ]),
    "methods": (("id", "module_id", "name", "script_path", "env"), [
        (1, 1, "filter", "filter.py", "pixi:analysis"),
        (2, 2, "filter", "filter.py", "conda:aligner"),
    ]),
    "method_versions": (("id", "method_id", "code_fingerprint", "git_commit",
                         "recorded_at"), [
        (1, 1, "a" * 64, "1" * 40, "2026-09-01 10:00:00.000000"),
        (2, 1, "b" * 64, "2" * 40, "2026-09-02 10:00:00.000000"),
        (3, 2, "c" * 64, None, "2026-09-03 10:00:00.000000"),
    ]),
    "method_contracts": (("id", "method_id", "input_slots", "output_slots",
                          "params_schema", "executor"), [
        (1, 1, '{"table": {"type": "tsv"}}', '{"out": {"type": "tsv"}}',
         '{"min_q": {"type": "int"}}', "python"),
    ]),
    "runs": (("id", "method_id", "params", "sample", "status", "pipeline_id",
              "version_id", "cache_key", "cache_source_run_id",
              "env_fingerprint", "node_id"), [
        (1, 1, '{"min_q": 20}', "S1", "completed", "p-1", 1, "k" * 64, None,
         "e" * 32, "n1"),
        (2, 2, "{}", None, "completed", "p-1", 3, "m" * 64, None, "f" * 32,
         "n2"),
        (3, 1, '{"min_q": 30}', "S2", "failed", "p-2", 1, "q" * 64, None,
         "e" * 32, "n1"),
        (4, 1, '{"min_q": 30}', "S2", "completed", "p-3", 1, "q" * 64, None,
         "e" * 32, "n1"),
        (5, 1, '{"min_q": 20}', "S1", "completed", "p-4", 1, "k" * 64, None,
         "e" * 32, "n1"),
        (6, 1, '{"min_q": 20}', "S1", "completed", "p-5", 1, "k" * 64, 1,
         "e" * 32, "n1"),
        (7, 1, '{"min_q": 40}', "S3", "running", "p-6", 1, "r" * 64, None,
         "e" * 32, "n1"),
        (8, 1, '{"min_q": 40}', "S3", "running", "p-7", 1, "r" * 64, None,
         "e" * 32, "n1"),
    ]),
    "run_inputs": (("id", "run_id", "source_run_id", "input_name",
                    "source_slot"), [
        (1, 2, 1, "table", "out"),
    ]),
}


def seed_sql() -> str:
    """Render :data:`SEED_ROWS` as INSERT statements for a released schema.

    Returns:
        One ``INSERT`` statement per seeded row, newline-separated, for
        :func:`build_release_database`'s ``seed_sql``.
    """
    statements = []
    for table, (columns, rows) in SEED_ROWS.items():
        for row in rows:
            values = ", ".join(
                "NULL" if v is None
                else str(v) if isinstance(v, int)
                else "'" + str(v).replace("'", "''") + "'"
                for v in row
            )
            statements.append(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});"
            )
    return "\n".join(statements)


def build_release_database(db_path: Path, seed_sql: str = "",
                           release: str = SNAPSHOT_RELEASE) -> None:
    """Create a SQLite database from a release snapshot, then run ``seed_sql``.

    Args:
        db_path: Where to create the database file.
        seed_sql: Statements (usually ``INSERT``s against the old schema) run
            after the DDL, in the same script.
        release: The release whose snapshot to build from; the latest by
            default.
    """
    ddl = snapshot_ddl(release).read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(ddl + "\n" + seed_sql)
        conn.commit()
    finally:
        conn.close()
