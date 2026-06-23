"""
Tests for cancelled-run row generation (pipeline-end walk).

Covers the end-to-end contract:

  1. Schema migration: ``cancelled_due_to_run_id`` column exists and is
     nullable on new DBs; migration is idempotent and works on a legacy DB
     missing the column.
  2. `_write_cancelled_rows` walk:
       - Linear chain with one failed middle step.
       - Branching DAG (one failed root; multiple descendants).
       - Cartesian sample × variant fan-out with one variant's upstream
         failing.
       - Fan-in collapsed chain writes cancelled rows with sample="__all__".
       - Idempotency across re-invocations.
       - Skips triples that already have any Run row.
  3. WfcProvider passthrough: cancelled rows surface via `WfcRun.to_dict`
     with `cancelledDueToRunId` set; legacy DB without the column still
     loads.

All tests use the `tmp_project` fixture from tests/conftest.py and insert
rows directly via `get_session` -- no Snakemake subprocess needed to
exercise the walk.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import select

from dflow.core.decorators import workflow, Step

from wfc.database import get_engine, get_session, reset_engine
from wfc.models import Method, Module, Run


# =============================================================================
# Helpers (mirror tests/test_lineage.py's direct-insert style)
# =============================================================================


def _make_module(session, name: str) -> int:
    mod = Module(name=name, description=f"{name} test module")
    session.add(mod)
    session.commit()
    session.refresh(mod)
    return mod.id  # type: ignore[return-value]


def _make_method(session, module_id: int, name: str) -> int:
    m = Method(name=name, module_id=module_id, script_path=f"methods/{name}/{name}.py")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m.id  # type: ignore[return-value]


def _make_run(
    session,
    method_id: int,
    sample: str,
    pipeline_id: str,
    status: str = "completed",
    params: dict | None = None,
) -> int:
    run = Run(
        method_id=method_id,
        sample=sample,
        pipeline_id=pipeline_id,
        status=status,
        params=params,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run.id  # type: ignore[return-value]


def _write_pipeline_json(project_root: Path, pipeline_id: str, nodes, links, samples, param_sets=None) -> Path:
    """Drop a frozen pipeline.json into .runs/pipelines/<pid>/ for the walk."""
    pipeline_dir = project_root / ".runs" / "pipelines" / pipeline_id
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {
        "nodes": nodes,
        "links": links,
        "samples": samples,
    }
    if param_sets:
        doc["param_sets"] = param_sets
    path = pipeline_dir / "pipeline.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


# =============================================================================
# Task 1: schema migration
# =============================================================================


def test_run_has_cancelled_due_to_run_id_column(tmp_project):
    """On fresh DB, `runs` table has the new nullable self-FK column."""
    engine = get_engine()
    with engine.connect() as conn:
        from sqlalchemy import text
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(runs)")).fetchall()
        }
    assert "cancelled_due_to_run_id" in cols


def test_run_started_at_is_nullable(tmp_project):
    """A cancelled row can be inserted with started_at=None."""
    with get_session() as s:
        mod_id = _make_module(s, "m")
        mid = _make_method(s, mod_id, "method_a")
        run = Run(
            method_id=mid,
            sample="S1",
            pipeline_id="pid",
            status="cancelled",
            started_at=None,
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        assert run.started_at is None


def test_legacy_db_migration_adds_column(tmp_path, monkeypatch):
    """A pre-migration DB (missing cancelled_due_to_run_id) gains the column
    on first engine init."""
    db_path = tmp_path / "legacy.db"
    # Create a `runs` table that matches the old schema (no cancelled column).
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Also set a PM project marker so project_root resolution works
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    (wfc_dir / "wf-canvas.toml").write_text('[project]\nname="legacy"\n')
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    reset_engine()
    try:
        get_engine()  # triggers migration
        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        conn.close()
        assert "cancelled_due_to_run_id" in cols
    finally:
        reset_engine()


# =============================================================================
# Task 2: _write_cancelled_rows walk
# =============================================================================


@workflow(
    purpose="Linear chain A->B->C where B failed: one cancelled row for C linked to B",
)
def test_walk_linear_chain_one_failure(tmp_project):
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-lin-1"

    # Seed modules + methods for 3 linear steps
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        b_id = _make_method(s, mod_id, "method_b")
        _ = _make_method(s, mod_id, "method_c")

        # A completed, B failed, C missing entirely
        _make_run(s, a_id, "S1", pid, status="completed")
        b_run_id = _make_run(s, b_id, "S1", pid, status="failed")

    # Frozen pipeline.json with A -> B -> C
    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
            {"id": "c", "method": "method_c", "module": "mod"},
        ],
        links=[
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
        ],
        samples=["S1"],
    )

    口 = Step(step_num=1, name="Invoke walk", purpose="Fill in missing cancelled row for C")
    _write_cancelled_rows(pid, str(tmp_project))

    口 = Step(step_num=2, name="Assert one cancelled row", purpose="Row for method_c linked to B")
    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1
    c_row = cancelled[0]
    assert c_row.sample == "S1"
    assert c_row.cancelled_due_to_run_id == b_run_id
    assert c_row.started_at is None


@workflow(
    purpose="Branching A->{B,C,D} where A failed: three cancelled rows pointing to A",
)
def test_walk_branching_root_failure(tmp_project):
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-branch-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        _ = _make_method(s, mod_id, "method_b")
        _ = _make_method(s, mod_id, "method_c")
        _ = _make_method(s, mod_id, "method_d")

        a_run_id = _make_run(s, a_id, "S1", pid, status="failed")

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
            {"id": "c", "method": "method_c", "module": "mod"},
            {"id": "d", "method": "method_d", "module": "mod"},
        ],
        links=[
            {"source": "a", "target": "b"},
            {"source": "a", "target": "c"},
            {"source": "a", "target": "d"},
        ],
        samples=["S1"],
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 3
    for c in cancelled:
        assert c.cancelled_due_to_run_id == a_run_id
        assert c.sample == "S1"


@workflow(
    purpose="Cartesian samples × descendants after upstream failure: cancelled per (sample, descendant)",
)
def test_walk_cartesian_two_samples_one_failure(tmp_project):
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-cart-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        _ = _make_method(s, mod_id, "method_b")

        # Both samples' method_a failed; method_b never ran
        a_s1 = _make_run(s, a_id, "S1", pid, status="failed")
        a_s2 = _make_run(s, a_id, "S2", pid, status="failed")

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
        ],
        links=[{"source": "a", "target": "b"}],
        samples=["S1", "S2"],
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    samples = sorted(c.sample for c in cancelled)
    assert samples == ["S1", "S2"]
    for c in cancelled:
        if c.sample == "S1":
            assert c.cancelled_due_to_run_id == a_s1
        else:
            assert c.cancelled_due_to_run_id == a_s2


@workflow(
    purpose="Variant sweep: one variant's upstream fails -- only that variant's descendants cancel",
)
def test_walk_variant_sweep_isolated_failure(tmp_project):
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-var-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        _ = _make_method(s, mod_id, "method_b")

        # strict variant completed; loose variant failed for S1
        _make_run(s, a_id, "S1", pid, status="completed", params={"t": 0.1})  # strict
        a_loose = _make_run(s, a_id, "S1", pid, status="failed", params={"t": 0.9})  # loose
        # method_b completed for strict, missing for loose
        b_id = s.exec(select(Method).where(Method.name == "method_b")).first().id
        _make_run(s, b_id, "S1", pid, status="completed", params={"t": 0.1})

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
        ],
        links=[{"source": "a", "target": "b"}],
        samples=["S1"],
        param_sets={
            "a": {"strict": {"t": 0.1}, "loose": {"t": 0.9}},
            "b": {"strict": {"t": 0.1}, "loose": {"t": 0.9}},
        },
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    # Only the loose variant of method_b should be cancelled
    assert len(cancelled) == 1
    assert cancelled[0].cancelled_due_to_run_id == a_loose
    # sanity: params correspond to loose
    assert cancelled[0].params == {"t": 0.9}


@workflow(
    purpose="Fan-in collapsed chain: cancelled row carries sample='__all__'",
)
def test_walk_fan_in_collapsed_failure(tmp_project):
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-fanin-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        m_id = _make_method(s, mod_id, "csv_merge")
        _ = _make_method(s, mod_id, "downstream")

        # Collapsed step failed; downstream missing
        merge_run = _make_run(s, m_id, "__all__", pid, status="failed")

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "sel", "type": "input_selector", "fan_mode": "in", "samples": ["S1", "S2"]},
            {"id": "merge", "method": "csv_merge", "module": "mod"},
            {"id": "down", "method": "downstream", "module": "mod"},
        ],
        links=[
            {"source": "sel", "target": "merge"},
            {"source": "merge", "target": "down"},
        ],
        samples=[],
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1
    assert cancelled[0].sample == "__all__"
    assert cancelled[0].cancelled_due_to_run_id == merge_run


def test_walk_is_idempotent(tmp_project):
    """Calling the walk twice does not duplicate cancelled rows."""
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-idem-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        _ = _make_method(s, mod_id, "method_b")
        _make_run(s, a_id, "S1", pid, status="failed")

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
        ],
        links=[{"source": "a", "target": "b"}],
        samples=["S1"],
    )

    _write_cancelled_rows(pid, str(tmp_project))
    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert len(cancelled) == 1


def test_walk_prefers_nid_keying_when_labels_distinguish_branches(tmp_project):
    """Two canvas nodes share method+sample+params but have distinct labels
    (``nid``). Only the labelled branch whose upstream failed should receive
    cancelled rows -- the other labelled branch (successful) must be left
    alone.
    """
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-nid-1"
    # Pipeline layout:
    #   a_left  (label=branchL) -> downstream  (label=downstreamL)
    #   a_right (label=branchR) -> downstream  (label=downstreamR)
    # Both "a" instances invoke method_a with identical params; both
    # downstream instances invoke method_b with identical params.
    # a_left FAILS; a_right SUCCEEDS.  The method-only triple-key is
    # ambiguous here: ("method_a", "S1", _fp({})) would map to BOTH
    # a_left and a_right.  Nid-keying breaks the ambiguity so only
    # downstreamL gets a cancelled row, not downstreamR.
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        b_id = _make_method(s, mod_id, "method_b")

        a_left = Run(
            method_id=a_id, sample="S1", pipeline_id=pid,
            status="failed", params={}, nid="branchL",
        )
        s.add(a_left); s.commit(); s.refresh(a_left)
        a_left_id = a_left.id

        a_right = Run(
            method_id=a_id, sample="S1", pipeline_id=pid,
            status="completed", params={}, nid="branchR",
        )
        s.add(a_right); s.commit(); s.refresh(a_right)

        # downstreamR ran successfully; downstreamL is missing.
        down_r = Run(
            method_id=b_id, sample="S1", pipeline_id=pid,
            status="completed", params={}, nid="downstreamR",
        )
        s.add(down_r); s.commit()

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "al", "method": "method_a", "module": "mod", "label": "branchL"},
            {"id": "ar", "method": "method_a", "module": "mod", "label": "branchR"},
            {"id": "dl", "method": "method_b", "module": "mod", "label": "downstreamL"},
            {"id": "dr", "method": "method_b", "module": "mod", "label": "downstreamR"},
        ],
        links=[
            {"source": "al", "target": "dl"},
            {"source": "ar", "target": "dr"},
        ],
        samples=["S1"],
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    # Expect exactly one cancelled row, for the left branch.
    assert len(cancelled) == 1, (
        f"nid-keying collision: {[(c.nid, c.sample) for c in cancelled]}"
    )
    c = cancelled[0]
    assert c.nid == "downstreamL"
    assert c.cancelled_due_to_run_id == a_left_id
    # Confirm the right branch was NOT cancelled (its downstream already exists)
    all_right_runs = [c for c in cancelled if c.nid == "downstreamR"]
    assert all_right_runs == []


def test_walk_success_path_writes_nothing(tmp_project):
    """On a fully-successful pipeline the walk finds zero missing triples."""
    from wfc.cli import _write_cancelled_rows

    pid = "pipe-success-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        b_id = _make_method(s, mod_id, "method_b")
        _make_run(s, a_id, "S1", pid, status="completed")
        _make_run(s, b_id, "S1", pid, status="completed")

    _write_pipeline_json(
        tmp_project,
        pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod"},
            {"id": "b", "method": "method_b", "module": "mod"},
        ],
        links=[{"source": "a", "target": "b"}],
        samples=["S1"],
    )

    _write_cancelled_rows(pid, str(tmp_project))

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
    assert cancelled == []


# =============================================================================
# Task 4: WfcProvider passthrough
# =============================================================================


def test_wfc_provider_surfaces_cancelled_due_to_run_id(tmp_project):
    """Cancelled Run rows appear via WfcProvider with cancelledDueToRunId populated."""
    from wfc.canvas.wfc_provider import WfcProvider

    pid = "pipe-prov-1"
    with get_session() as s:
        mod_id = _make_module(s, "mod")
        a_id = _make_method(s, mod_id, "method_a")
        b_id = _make_method(s, mod_id, "method_b")
        a_failed = _make_run(s, a_id, "S1", pid, status="failed")
        # Manually insert a cancelled row linked to a_failed
        cancelled = Run(
            method_id=b_id,
            sample="S1",
            pipeline_id=pid,
            status="cancelled",
            started_at=None,
            cancelled_due_to_run_id=a_failed,
        )
        s.add(cancelled)
        s.commit()
        s.refresh(cancelled)
        cancelled_id = str(cancelled.id)

    prov = WfcProvider(str(tmp_project))
    prov.load()
    run_dict = prov.get_run(cancelled_id)
    assert run_dict is not None
    assert run_dict["status"] == "cancelled"
    # Serialised as string to match parentRunId convention (all canvas IDs
    # are strings on the frontend).
    assert run_dict["cancelledDueToRunId"] == str(a_failed)
    assert isinstance(run_dict["cancelledDueToRunId"], str)


def test_wfc_provider_legacy_db_without_column_still_loads(tmp_path, monkeypatch):
    """A DB written before the migration (no cancelled_due_to_run_id column)
    loads via WfcProvider without exception."""
    from wfc.canvas.wfc_provider import WfcProvider

    # Build a minimal legacy DB by hand
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()
    (wfc_dir / "wf-canvas.toml").write_text('[project]\nname="legacy"\n')
    db_path = wfc_dir / "wfc.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE modules (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
        CREATE TABLE methods (
            id INTEGER PRIMARY KEY, module_id INTEGER, name TEXT,
            script_path TEXT, env TEXT DEFAULT 'inherit'
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            method_id INTEGER NOT NULL,
            params TEXT,
            sample TEXT,
            status TEXT,
            pipeline_id TEXT,
            nf_process_name TEXT,
            started_at TEXT,
            finished_at TEXT,
            metrics TEXT,
            nid TEXT
        );
        CREATE TABLE run_inputs (
            id INTEGER PRIMARY KEY, run_id INTEGER, source_run_id INTEGER,
            input_name TEXT, artifact_path TEXT
        );
        CREATE TABLE run_outputs (
            id INTEGER PRIMARY KEY, run_id INTEGER, output_name TEXT,
            artifact_path TEXT, artifact_type TEXT
        );
        INSERT INTO modules (id, name) VALUES (1, 'mod');
        INSERT INTO methods (id, module_id, name) VALUES (1, 1, 'method_a');
        INSERT INTO runs (id, method_id, sample, status) VALUES (1, 1, 'S1', 'completed');
        """
    )
    conn.commit()
    conn.close()

    prov = WfcProvider(str(tmp_path))
    prov.load()  # must not raise
    run = prov.get_run("1")
    assert run is not None
    assert run["status"] == "success"  # completed → success remap
    assert run.get("cancelledDueToRunId") is None
