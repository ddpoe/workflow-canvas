"""
NID (Node ID) System Tests
===========================

Tests for the auto-versioned run identity system that assigns
v1, v2, v3... identifiers per (sample, method) pair, with
support for custom NID labels from canvas node labels.

Tier 2 tests: @workflow(purpose=...), no Step markers.
"""

import sqlite3
from pathlib import Path

import pytest
from axiom_annotations import workflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_db(db_path: Path):
    """Create a wfc.db whose schema is built from the ORM models.

    Building from ``SQLModel.metadata`` (the single source of truth in
    ``wfc/models.py``) rather than hand-rolled ``CREATE TABLE`` strings means
    this fixture follows schema changes automatically -- e.g. the provider's
    ``run_inputs.input_name`` column, which the old hand-rolled DDL omitted.
    The raw ``INSERT`` helpers below populate only the columns each test needs;
    the rest take their model defaults / NULL.

    Args:
        db_path: Path to the SQLite database file.
    """
    import wfc.models  # noqa: F401  -- register tables on SQLModel.metadata
    from sqlmodel import SQLModel, create_engine

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    return sqlite3.connect(str(db_path))


def _insert_module(conn, mod_id: int, name: str):
    """Insert a module row."""
    conn.execute("INSERT INTO modules (id, name) VALUES (?, ?)", (mod_id, name))
    conn.commit()


def _insert_method(conn, method_id: int, name: str, module_id: int):
    """Insert a method row."""
    conn.execute(
        "INSERT INTO methods (id, name, module_id, script_path, env) VALUES (?, ?, ?, ?, ?)",
        (method_id, name, module_id, f"methods/{name}/{name}.py", "container:demo"),
    )
    conn.commit()


def _insert_run(
    conn,
    run_id: int,
    method_id: int,
    sample: str,
    started_at: str,
    *,
    nid: str | None = None,
    status: str = "completed",
):
    """Insert a run row."""
    conn.execute(
        "INSERT INTO runs (id, method_id, sample, status, started_at, nid) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, method_id, sample, status, started_at, nid),
    )
    conn.commit()


def _make_provider(project_root: Path):
    """Create a WfcProvider instance pointing at the given project root."""
    from wfc.canvas.wfc_provider import WfcProvider
    return WfcProvider(str(project_root))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@workflow(purpose="Three runs with same (sample, method) get auto-versioned NIDs v1, v2, v3 in chronological order")
def test_auto_version_chronological(tmp_path):
    """US-1: Re-running the same method on the same sample produces v1, v2, v3."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    conn = _create_test_db(db_path)
    _insert_module(conn, 1, "data_preprocessing")
    _insert_method(conn, 1, "ploidy_filter", 1)

    # Insert 3 runs for same (sample, method) with increasing timestamps
    _insert_run(conn, 1, 1, "Pa16c", "2026-01-01T10:00:00")
    _insert_run(conn, 2, 1, "Pa16c", "2026-01-01T11:00:00")
    _insert_run(conn, 3, 1, "Pa16c", "2026-01-01T12:00:00")
    conn.close()

    provider = _make_provider(tmp_path)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs["1"].nid == "v1"
    assert runs["2"].nid == "v2"
    assert runs["3"].nid == "v3"


@workflow(purpose="Runs across different (sample, method) pairs get independent version sequences")
def test_independent_version_sequences(tmp_path):
    """US-1: Different methods or samples start their own sequence."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    conn = _create_test_db(db_path)
    _insert_module(conn, 1, "data_preprocessing")
    _insert_method(conn, 1, "ploidy_filter", 1)
    _insert_method(conn, 2, "binary_labeling", 1)

    # Two runs for ploidy_filter/Pa16c
    _insert_run(conn, 1, 1, "Pa16c", "2026-01-01T10:00:00")
    _insert_run(conn, 2, 1, "Pa16c", "2026-01-01T11:00:00")
    # One run for binary_labeling/Pa16c (different method, same sample)
    _insert_run(conn, 3, 2, "Pa16c", "2026-01-01T10:30:00")
    # One run for ploidy_filter/Pa22b (same method, different sample)
    _insert_run(conn, 4, 1, "Pa22b", "2026-01-01T10:30:00")
    conn.close()

    provider = _make_provider(tmp_path)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    # ploidy_filter/Pa16c: v1, v2
    assert runs["1"].nid == "v1"
    assert runs["2"].nid == "v2"
    # binary_labeling/Pa16c: v1 (independent sequence)
    assert runs["3"].nid == "v1"
    # ploidy_filter/Pa22b: v1 (independent sequence)
    assert runs["4"].nid == "v1"


@workflow(purpose="Run with custom nid shows that value instead of auto-version")
def test_custom_nid_replaces_auto_version(tmp_path):
    """US-2: Setting a canvas node label causes the run card to display that label."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    conn = _create_test_db(db_path)
    _insert_module(conn, 1, "data_preprocessing")
    _insert_method(conn, 1, "ploidy_filter", 1)

    _insert_run(conn, 1, 1, "Pa16c", "2026-01-01T10:00:00", nid="fast_set")
    _insert_run(conn, 2, 1, "Pa16c", "2026-01-01T11:00:00")
    conn.close()

    provider = _make_provider(tmp_path)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs["1"].nid == "fast_set"
    # Second run has no custom nid, auto-versions as v2 (it's the second
    # run chronologically for this (sample, method) pair)
    assert runs["2"].nid == "v2"


@workflow(purpose="Custom NID then NULL NID correctly auto-versions the NULL run")
def test_custom_then_null_nid(tmp_path):
    """US-2: Clearing the label before a subsequent run restores auto-versioning."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    conn = _create_test_db(db_path)
    _insert_module(conn, 1, "data_preprocessing")
    _insert_method(conn, 1, "ploidy_filter", 1)

    # First run has custom NID, second is NULL
    _insert_run(conn, 1, 1, "Pa16c", "2026-01-01T10:00:00", nid="aggressive")
    _insert_run(conn, 2, 1, "Pa16c", "2026-01-01T11:00:00", nid=None)
    _insert_run(conn, 3, 1, "Pa16c", "2026-01-01T12:00:00", nid=None)
    conn.close()

    provider = _make_provider(tmp_path)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs["1"].nid == "aggressive"
    assert runs["2"].nid == "v2"
    assert runs["3"].nid == "v3"


