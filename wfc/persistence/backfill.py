"""The schema backfill: missing columns added, drifted tables rebuilt.

There is no migration system: old records are never rewritten or read a
second way. A database an earlier release created must still end up with the
schema a fresh one has, and ``create_all`` builds a missing table whole but
never touches a table that exists. :func:`ensure_schema` closes that gap for
SQLite in two passes, before ``create_all``:

1. The column pass adds every column the model declares and the table lacks,
   one ``ALTER TABLE … ADD COLUMN`` statement per column, built by
   :func:`_add_column_ddl`. It is best-effort: a column SQLite cannot add is
   logged and skipped.
2. The rebuild pass compares each table's unique constraints and indexes with
   the model (by columns, uniqueness and, for a partial index, its
   predicate) and rebuilds every table that differs, by SQLite's
   create-copy-drop-rename procedure, in one transaction. A copy the new
   uniqueness would refuse raises :class:`SchemaRebuildError` and leaves the
   tables as they were; a partial unique index refuses only duplicates among
   the rows its predicate selects.
"""

import re
import sqlite3
import sys

from axiom_annotations import AutoStep, Step, task, workflow
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel

from . import schema  # noqa: F401  (registers every table on SQLModel.metadata)

#: The name prefix of the new table a rebuild copies into before the swap.
REBUILD_PREFIX = "_wfc_rebuild_"

#: The dialect a model index's predicate is compiled with for comparison.
_SQLITE = sqlite_dialect.dialect()

#: Where a ``CREATE INDEX`` statement's predicate begins.
_WHERE = re.compile(r"\sWHERE\s", re.IGNORECASE)

#: The values an added column gives the rows already in its table, run once,
#: right after the column pass adds it: ``(table, column) -> UPDATE``. A
#: cancelled run's reason is read from what the row already records: a
#: linked cause is an upstream failure, the cancel route's message is the
#: user, and anything else never started.
_ADDED_COLUMN_FILLS: dict[tuple[str, str], str] = {
    ("runs", "cancel_reason"): (
        "UPDATE runs SET cancel_reason = CASE"
        " WHEN cancelled_due_to_run_id IS NOT NULL"
        f" THEN '{schema.CANCEL_REASON_UPSTREAM_FAILED}'"
        " WHEN error_message = 'Cancelled by user'"
        f" THEN '{schema.CANCEL_REASON_USER}'"
        f" ELSE '{schema.CANCEL_REASON_NOT_RUN}' END"
        " WHERE status = 'cancelled'"
    ),
}


class SchemaRebuildError(RuntimeError):
    """A table rebuild was refused; the transaction rolled back.

    Raised when an existing table's rows cannot be copied into the table the
    model declares: most often two rows sharing a key the model now declares
    unique. The message names the table, the constraint and the key. The
    tables are left exactly as they were before the rebuild began.
    """


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


@task(
    purpose="Add to each existing table every column its model declares and "
            "the table lacks, skipping a column SQLite cannot add",
    inputs="engine: a SQLite engine",
    outputs="The existing tables with every addable missing column added",
    critical="Best-effort: never raises; a probe or ALTER error is logged to "
             "stderr and the pass goes on",
)
def _add_missing_columns(engine) -> None:
    """Add each missing column to the tables that exist (the column pass).

    For every table in ``SQLModel.metadata`` that already exists, diffs
    ``PRAGMA table_info`` against the model's columns and issues one
    ``ALTER TABLE <t> ADD COLUMN <c>`` per missing column, then the column's
    fill from ``_ADDED_COLUMN_FILLS`` when it has one, so the rows already
    there get their value once, when the column arrives. A NOT-NULL column
    is added only when it carries a constant default (see
    :func:`_add_column_ddl`); one with none cannot be added to a populated
    table under SQLite and is skipped. One column's ALTER failing does not
    abort the rest, and no error escapes: the pass never blocks engine init.

    Args:
        engine: SQLAlchemy engine bound to a SQLite database.
    """
    from sqlalchemy import text
    try:
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
                        fill = _ADDED_COLUMN_FILLS.get((table.name, column.name))
                        if fill is not None:
                            conn.execute(text(fill))
                    except Exception as col_exc:
                        # One un-addable column must not abort the rest of the
                        # pass. Log and continue.
                        print(
                            f"[wfc.persistence] ensure_schema: could not add "
                            f"{table.name}.{column.name}: {col_exc}",
                            file=sys.stderr,
                        )
            conn.commit()
    except Exception as exc:
        # The column pass is best-effort -- never block engine init on a probe
        # error. A genuinely missing column surfaces later as a clear runtime
        # error. Log to stderr so the warning is visible in captured output.
        print(
            f"[wfc.persistence] ensure_schema backfill warning: {exc}",
            file=sys.stderr,
        )


def _predicate(create_sql: str | None) -> str | None:
    """A partial index's predicate, from its ``CREATE INDEX`` statement.

    The text after the statement's ``WHERE``, with whitespace collapsed, so
    the model's compiled statement and the one SQLite stored compare alike.

    Args:
        create_sql: A ``CREATE [UNIQUE] INDEX`` statement, or ``None`` (a
            constraint's autoindex has no statement).

    Returns:
        The normalised predicate, or ``None`` for an index over every row.
    """
    if not create_sql:
        return None
    parts = _WHERE.split(create_sql, maxsplit=1)
    if len(parts) < 2:
        return None
    return " ".join(parts[1].split())


def _index_predicate(index) -> str | None:
    """The predicate of one of the model's indexes, as SQLite would store it."""
    return _predicate(str(CreateIndex(index).compile(dialect=_SQLITE)))


def _model_rules(table) -> set[tuple[tuple[str, ...], bool, str | None]]:
    """The model's unique constraints and indexes on a table.

    Each is normalised to ``(columns, unique, predicate)``, so a unique
    constraint and a unique index over the same columns compare alike, and a
    partial index differs from a full one over the same columns. The primary
    key is not included.

    Args:
        table: A SQLAlchemy ``Table`` from ``SQLModel.metadata``.

    Returns:
        The set of ``(column names, is unique, predicate or None)`` triples.
    """
    rules: set[tuple[tuple[str, ...], bool, str | None]] = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            rules.add((tuple(c.name for c in constraint.columns), True, None))
    for index in table.indexes:
        rules.add((tuple(c.name for c in index.columns), bool(index.unique),
                   _index_predicate(index)))
    return rules


def _unique_rules(table) -> list[tuple[str, tuple[str, ...], str | None]]:
    """The model's uniqueness rules on a table, each with its name.

    Args:
        table: A SQLAlchemy ``Table`` from ``SQLModel.metadata``.

    Returns:
        ``(name, column names, predicate)`` for each unique constraint and
        unique index; an unnamed constraint is named ``UNIQUE(<columns>)``.
        The predicate is a partial index's ``WHERE`` clause, else ``None``.
    """
    rules: list[tuple[str, tuple[str, ...], str | None]] = []
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            cols = tuple(c.name for c in constraint.columns)
            rules.append(
                (constraint.name if isinstance(constraint.name, str) else f"UNIQUE({', '.join(cols)})", cols, None)
            )
    for index in table.indexes:
        if index.unique:
            cols = tuple(c.name for c in index.columns)
            rules.append((index.name or f"UNIQUE({', '.join(cols)})", cols,
                          _index_predicate(index)))
    return rules


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """The column names a table has in the database; empty when it is absent."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _database_rules(conn: sqlite3.Connection, table_name: str) -> set:
    """A table's unique constraints and indexes as the database holds them.

    Read from ``PRAGMA index_list`` and ``index_info``, normalised as
    :func:`_model_rules` normalises the model's: a constraint's autoindex and
    an explicit index both become ``(columns, unique, predicate)``; a partial
    index's predicate is read from the statement ``sqlite_master`` stored.
    The primary key's index is not included.

    Args:
        conn: A sqlite3 connection.
        table_name: The table to read.

    Returns:
        The set of ``(column names, is unique, predicate or None)`` triples.
    """
    rules = set()
    for _seq, name, unique, origin, partial in conn.execute(
        f"PRAGMA index_list({table_name})"
    ).fetchall():
        if origin == "pk":
            continue
        info = sorted(conn.execute(f"PRAGMA index_info({name})").fetchall())
        predicate = None
        if partial:
            stored = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            predicate = _predicate(stored[0] if stored else None)
        rules.add((tuple(row[2] for row in info), bool(unique), predicate))
    return rules


def _drifted_tables(conn: sqlite3.Connection):
    """The existing tables whose unique constraints or indexes differ from the model.

    A table that differs but still lacks a model column (one the column pass
    could not add) cannot be copied into the model's table and is returned
    apart, with the columns it lacks.

    Args:
        conn: A sqlite3 connection.

    Returns:
        ``(drifted, uncopyable)``: the ``Table`` objects to rebuild, and
        ``(table name, missing column names)`` for each table left as it is.
    """
    drifted, uncopyable = [], []
    for table in SQLModel.metadata.sorted_tables:
        existing = _table_columns(conn, table.name)
        if not existing:
            continue
        if _database_rules(conn, table.name) == _model_rules(table):
            continue
        missing = [c.name for c in table.columns if c.name not in existing]
        if missing:
            uncopyable.append((table.name, missing))
            continue
        drifted.append(table)
    return drifted, uncopyable


def _first_duplicate(conn: sqlite3.Connection, table_name: str,
                     columns: tuple[str, ...],
                     predicate: str | None = None) -> tuple | None:
    """The first key two or more rows share over ``columns``, or ``None``.

    Rows with a NULL in any of the columns are skipped: SQLite's uniqueness
    treats NULLs as distinct. With a ``predicate`` (a partial index's), only
    the rows it selects are compared, as the index compares them.
    """
    cols = ", ".join(columns)
    where = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    if predicate:
        where += f" AND ({predicate})"
    row = conn.execute(
        f"SELECT {cols} FROM {table_name} WHERE {where} "
        f"GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    return None if row is None else tuple(row)


def _rebuild_table(conn: sqlite3.Connection, table, dialect) -> None:
    """Rebuild one table as the model declares it, keeping every row.

    Runs inside the caller's transaction with foreign-key enforcement off:
    creates the model's table under a temporary name, copies every row, drops
    the old table, renames the new one into place and creates the model's
    indexes on it.

    Args:
        conn: A sqlite3 connection inside an open transaction.
        table: The model's ``Table``.
        dialect: The SQLAlchemy dialect to compile the DDL with.

    Raises:
        SchemaRebuildError: The rows break one of the model's uniqueness rules,
            or copying them fails.
    """
    name = table.name
    for rule_name, columns, predicate in _unique_rules(table):
        duplicate = _first_duplicate(conn, name, columns, predicate)
        if duplicate is not None:
            scope = f" among rows where {predicate}" if predicate else ""
            raise SchemaRebuildError(
                f"Cannot bring table {name} up to the current schema: its rows "
                f"break {rule_name} on ({', '.join(columns)}): the key "
                f"{duplicate!r} appears in more than one row{scope}. The "
                f"tables are unchanged."
            )

    temp = REBUILD_PREFIX + name
    head = f"CREATE TABLE {dialect.identifier_preparer.format_table(table)} ("
    ddl = str(CreateTable(table).compile(dialect=dialect))
    if head not in ddl:
        raise SchemaRebuildError(
            f"Cannot bring table {name} up to the current schema: its model "
            f"DDL does not start with {head!r}."
        )
    column_list = ", ".join(c.name for c in table.columns)
    try:
        conn.execute(ddl.replace(head, f"CREATE TABLE {temp} (", 1))
        conn.execute(
            f"INSERT INTO {temp} ({column_list}) SELECT {column_list} FROM {name}"
        )
        conn.execute(f"DROP TABLE {name}")
        conn.execute(f"ALTER TABLE {temp} RENAME TO {name}")
        for index in table.indexes:
            conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
    except sqlite3.DatabaseError as exc:
        raise SchemaRebuildError(
            f"Cannot bring table {name} up to the current schema: copying its "
            f"rows failed: {exc}. The tables are unchanged."
        ) from exc


def _foreign_key_violations(conn: sqlite3.Connection) -> set[tuple]:
    """Every ``(table, rowid, parent)`` that ``PRAGMA foreign_key_check`` reports."""
    return {tuple(row[:3]) for row in conn.execute("PRAGMA foreign_key_check")}


@task(
    purpose="Rebuild every existing table whose unique constraints or indexes "
            "differ from the model, keeping its rows, in one transaction",
    inputs="engine: a SQLite engine whose tables carry every column the "
           "column pass could add",
    outputs="Each drifted table rebuilt as the model declares it, rows intact",
    critical="All or nothing: a row the new uniqueness refuses, a failed copy "
             "or a broken reference rolls the whole transaction back and "
             "raises SchemaRebuildError naming the table",
)
def _rebuild_drifted_tables(engine) -> list[str]:
    """Rebuild each table whose unique constraints or indexes differ (the rebuild pass).

    The comparison runs first without a write lock; nothing is written when no
    table differs. Otherwise foreign-key enforcement is turned off (it cannot
    change inside a transaction), an immediate transaction begins, the
    comparison is repeated under the lock, and each drifted table is rebuilt
    by :func:`_rebuild_table`. A foreign-key check then compares the
    violations with those the database held before; a new one aborts. The
    transaction commits only when every table is rebuilt. A table that still
    lacks a model column is left as it is and logged.

    Args:
        engine: SQLAlchemy engine bound to a SQLite database.

    Returns:
        The names of the tables rebuilt.

    Raises:
        SchemaRebuildError: A rebuild was refused; the tables are unchanged.
        sqlalchemy.exc.OperationalError: The database cannot be opened (raised
            by ``engine.connect``, as ``create_all`` would raise it).
    """
    # engine.connect() (not raw_connection()) so a database that cannot be
    # opened raises SQLAlchemy's wrapped OperationalError, the error every
    # caller of the engine already handles.
    with engine.connect() as sa_conn:
        conn = sa_conn.connection.driver_connection
        drifted, uncopyable = _drifted_tables(conn)
        for table_name, missing in uncopyable:
            print(
                f"[wfc.persistence] ensure_schema: cannot rebuild {table_name}: "
                f"it lacks column(s) {', '.join(missing)} the column pass "
                f"could not add",
                file=sys.stderr,
            )
        if not drifted:
            return []

        saved_isolation = conn.isolation_level
        conn.isolation_level = None  # explicit BEGIN / COMMIT below
        enforced = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                drifted, _ = _drifted_tables(conn)
                before = _foreign_key_violations(conn)
                for table in drifted:
                    _rebuild_table(conn, table, engine.dialect)
                broken = sorted(_foreign_key_violations(conn) - before, key=repr)
                if broken:
                    child, rowid, parent = broken[0]
                    raise SchemaRebuildError(
                        f"Cannot bring the schema up to date: after the rebuild "
                        f"row {rowid} of {child} references a missing "
                        f"{parent} row ({len(broken)} broken reference(s)). The "
                        f"tables are unchanged."
                    )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            if enforced:
                conn.execute("PRAGMA foreign_keys=ON")
            conn.isolation_level = saved_isolation
        return [table.name for table in drifted]


@workflow(
    purpose="Bring an existing SQLite database's tables up to the models: add "
            "missing columns, then rebuild tables whose unique constraints or "
            "indexes differ",
    inputs="engine: SQLAlchemy engine bound to the database to upgrade",
    outputs="Existing tables with the model's columns, unique constraints and "
            "indexes; wholly-missing tables are left for create_all",
)
def ensure_schema(engine) -> None:
    """Bring an existing SQLite database's tables up to the current models.

    ``create_all`` builds wholly-missing tables but never changes a table that
    exists. Run this BEFORE ``create_all`` so that existing tables are brought
    up to their models here, and wholly-missing tables (e.g.
    ``run_annotations`` on an old database) are built by ``create_all`` after.

    Two passes:

    * **Column pass (best-effort).** Each missing nullable or constant-default
      column is added with ``ALTER TABLE … ADD COLUMN``. A column SQLite
      cannot add (NOT NULL with no constant default) is skipped, and no error
      escapes this pass.
    * **Rebuild pass (all or nothing).** Each existing table whose unique
      constraints or indexes (by column set, uniqueness and a partial index's
      predicate) differ from the model is rebuilt by SQLite's create-copy-drop-rename procedure, with
      foreign-key enforcement off for the swap and a foreign-key check after
      it, in one transaction. Rows are copied by the model's columns, so a
      column the model no longer declares does not survive a rebuild. A row
      the new uniqueness refuses, a failed copy or a broken reference rolls
      the transaction back and raises :class:`SchemaRebuildError` naming the
      table, the constraint and the key: a silently drifted schema is the
      defect this pass exists to close. A table that still lacks a model
      column after the column pass is left as it is and logged.

    Renames and type changes are out of scope. SQLite-only: a no-op on any
    other dialect.

    Args:
        engine: SQLAlchemy engine bound to the database to upgrade.

    Raises:
        SchemaRebuildError: The rebuild pass was refused; the tables it would
            have rebuilt are unchanged.
    """
    口 = Step(step_num=1, name="Check the dialect",
             purpose="Return at once on any database but SQLite")
    if engine.dialect.name != "sqlite":
        return

    口 = AutoStep(step_num=2, name="Add the missing columns")
    _add_missing_columns(engine)

    口 = AutoStep(step_num=3, name="Rebuild the drifted tables")
    _rebuild_drifted_tables(engine)
