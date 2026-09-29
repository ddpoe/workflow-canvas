"""The additive schema backfill.

There is no migration system. ``create_all`` builds a missing table whole but
never adds a column to a table that already exists. :func:`ensure_schema`
closes that gap for SQLite: for each existing table it adds every column the
model declares and the table lacks, one ``ALTER TABLE … ADD COLUMN`` statement
per column, built by :func:`_add_column_ddl`.
"""

from sqlmodel import SQLModel

from . import schema  # noqa: F401  (registers every table on SQLModel.metadata)


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
                            f"[wfc.persistence] ensure_schema: could not add "
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
            f"[wfc.persistence] ensure_schema backfill warning: {exc}",
            file=_sys.stderr,
        )
