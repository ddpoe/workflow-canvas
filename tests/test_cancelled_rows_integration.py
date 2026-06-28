"""
Tier 3 integration tests for the cancelled-run-rows feature.

These exercise the full wired path through ``wfc.cli.run_pipeline`` --
including the fail_pipeline -> _write_cancelled_rows ordering on failure
and the always-on walk on --keep-going partial-prune success exits --
plus the SSE log-stream endpoint's terminal event for cancelled rows.

Snakemake itself is mocked (subprocess.run + load_pipeline/generate_snakefile)
because running the real engine is too heavy for a unit test; we simulate
Snakemake's side effects by pre-populating Run rows that reflect the state
Snakemake would have left behind when the mock subprocess returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from axiom_annotations import workflow, Step

from wfc.canvas.server import app, _active_jobs
from wfc.database import get_session, reset_engine
from wfc.models import Method, Module, Run


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_wfc_project(tmp_path, monkeypatch):
    """Minimal wfc project root: .wfc/ marker + empty DB + env vars pointing here."""
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    (wfc_dir / "wf-canvas.toml").write_text('[project]\nname = "int"\n')

    db_path = wfc_dir / "wfc.db"
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    # Touch engine so metadata is created.
    from wfc.database import get_engine
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    yield tmp_path
    reset_engine()


def _seed_methods(names):
    """Create one module + the listed methods. Returns {name: method_id}."""
    with get_session() as s:
        mod = Module(name="mod", description="integration test module")
        s.add(mod)
        s.commit()
        s.refresh(mod)
        ids = {}
        for name in names:
            m = Method(
                name=name, module_id=mod.id,
                script_path=f"methods/{name}/{name}.py",
                env="container:demo",
            )
            s.add(m)
            s.commit()
            s.refresh(m)
            ids[name] = m.id
    return ids


def _add_run(method_id, sample, pid, status="completed", params=None):
    with get_session() as s:
        r = Run(
            method_id=method_id,
            sample=sample,
            pipeline_id=pid,
            status=status,
            params=params,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def _write_pipeline_json(project_root: Path, pid: str, nodes, links, samples,
                         param_sets=None):
    pipeline_dir = project_root / ".runs" / "pipelines" / pid
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    doc = {"nodes": nodes, "links": links, "samples": samples}
    if param_sets:
        doc["param_sets"] = param_sets
    (pipeline_dir / "pipeline.json").write_text(json.dumps(doc, indent=2))


# =============================================================================
# Tier 3: end-to-end run_pipeline paths
# =============================================================================


@workflow(
    purpose="--keep-going partial prune: one sample fails mid-DAG, surviving "
            "sample completes, cancelled rows appear only for the failed "
            "sample's descendants (US-4 Tier 3).",
)
def test_keep_going_partial_prune_writes_cancelled_rows_for_failed_sample_only(
    tmp_wfc_project, monkeypatch,
):
    """Full run_pipeline path exercised with a mocked Snakemake subprocess.

    Scenario: A->B->C, two samples S1/S2. S1 fails at B (so C never runs for
    S1); S2 succeeds all the way through. After run_pipeline returns, we
    expect one cancelled row (C for S1) linked to S1's B-failure.
    """
    from wfc.cli import run_pipeline

    pid = "pipe-keep-going-1"
    method_ids = _seed_methods(["method_a", "method_b", "method_c"])

    # Simulate Snakemake --keep-going behaviour:
    # S1: A completed, B failed, C missing
    # S2: A, B, C all completed
    _add_run(method_ids["method_a"], "S1", pid, status="completed")
    b_s1_failed = _add_run(method_ids["method_b"], "S1", pid, status="failed")
    _add_run(method_ids["method_a"], "S2", pid, status="completed")
    _add_run(method_ids["method_b"], "S2", pid, status="completed")
    _add_run(method_ids["method_c"], "S2", pid, status="completed")

    _write_pipeline_json(
        tmp_wfc_project, pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            {"id": "b", "method": "method_b", "module": "mod", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            {"id": "c", "method": "method_c", "module": "mod", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        ],
        links=[
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
        ],
        samples=["S1", "S2"],
    )

    # Path the frozen pipeline.json to the canonical location run_pipeline reads.
    pipeline_json_path = (
        tmp_wfc_project / ".runs" / "pipelines" / pid / "pipeline.json"
    )
    assert pipeline_json_path.exists()

    Step(step_num=1, name="Invoke run_pipeline with mocked Snakemake",
         purpose="Keep-going returncode 0 -- success path invokes walk")

    # We rely on the real `load_pipeline` to parse the frozen pipeline.json
    # (both run_pipeline and _write_cancelled_rows call it). Only Snakemake's
    # subprocess is mocked, plus generate_snakefile (stubbed to a placeholder
    # Snakefile body since we're not exercising Snakemake itself).
    #
    # Phase D Pass 2: run_pipeline now uses subprocess.Popen + .wait() (so the
    # canvas cancel endpoint can SIGTERM the live process).  The mock has to
    # cover Popen, not subprocess.run.
    fake_proc = MagicMock()
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    with patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake"), \
         patch("subprocess.Popen", return_value=fake_proc) as mock_sp:
        run_pipeline(
            pipeline_path=str(pipeline_json_path),
            project_root=str(tmp_wfc_project),
            wfc_root=str(tmp_wfc_project),
            pipeline_id=pid,
        )

    Step(step_num=2, name="Assert cancelled row for S1's C only",
         purpose="Partial-prune: only failed sample's descendants get cancelled rows")

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()

    assert len(cancelled) == 1, (
        f"Expected exactly 1 cancelled row (S1's C); got {len(cancelled)}: "
        f"{[(r.method_id, r.sample) for r in cancelled]}"
    )
    only = cancelled[0]
    assert only.sample == "S1"
    assert only.method_id == method_ids["method_c"]
    assert only.cancelled_due_to_run_id == b_s1_failed

    # And S2 should have no cancelled rows whatsoever.
    with get_session() as s:
        s2_cancelled = s.exec(
            select(Run).where(
                Run.pipeline_id == pid,
                Run.status == "cancelled",
                Run.sample == "S2",
            )
        ).all()
    assert s2_cancelled == []


@workflow(
    purpose="Hard-abort (no --keep-going): a failure fail-fasts Snakemake, "
            "all descendants across all samples become cancelled rows "
            "(US-4 Tier 3).",
)
def test_hard_abort_writes_cancelled_rows_for_all_sample_descendants(
    tmp_wfc_project, monkeypatch,
):
    """Scenario: A->B, two samples. S1's A fails; Snakemake exits non-zero
    before running anything for S2. fail_pipeline flips S2's in-flight
    rows to 'failed'; the walk then writes cancelled rows for every
    descendant of every failed ancestor.
    """
    from wfc.cli import run_pipeline

    pid = "pipe-hard-abort-1"
    method_ids = _seed_methods(["method_a", "method_b"])

    # S1: A failed.
    a_s1_failed = _add_run(method_ids["method_a"], "S1", pid, status="failed")
    # S2: A was still 'running' when Snakemake aborted -- fail_pipeline will
    # flip it to 'failed'.
    a_s2_running = _add_run(method_ids["method_a"], "S2", pid, status="running")
    # Neither sample's B ran.

    _write_pipeline_json(
        tmp_wfc_project, pid,
        nodes=[
            {"id": "a", "method": "method_a", "module": "mod", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            {"id": "b", "method": "method_b", "module": "mod", "env": "container:demo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        ],
        links=[{"source": "a", "target": "b"}],
        samples=["S1", "S2"],
    )
    pipeline_json_path = (
        tmp_wfc_project / ".runs" / "pipelines" / pid / "pipeline.json"
    )

    Step(step_num=1, name="run_pipeline with failing Snakemake",
         purpose="Hard-abort returncode != 0 drives fail_pipeline + walk")

    # Phase D Pass 2: see note on Popen mock in the keep-going test above.
    fake_proc = MagicMock()
    fake_proc.wait.return_value = 1
    fake_proc.returncode = 1
    with patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake"), \
         patch("subprocess.Popen", return_value=fake_proc):
        with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
            run_pipeline(
                pipeline_path=str(pipeline_json_path),
                project_root=str(tmp_wfc_project),
                wfc_root=str(tmp_wfc_project),
                pipeline_id=pid,
            )

    Step(step_num=2, name="Assert both samples' B are cancelled",
         purpose="fail_pipeline flipped S2's A to failed; walk fills B for S1 & S2")

    with get_session() as s:
        # fail_pipeline should have flipped S2's running A to failed.
        a_s2_row = s.exec(
            select(Run).where(Run.id == a_s2_running)
        ).first()
        assert a_s2_row.status == "failed"

        cancelled = s.exec(
            select(Run).where(
                Run.pipeline_id == pid, Run.status == "cancelled"
            ).order_by(Run.sample)
        ).all()

    # Both S1's B and S2's B should be cancelled.
    cancelled_by_sample = {(r.sample, r.method_id): r for r in cancelled}
    assert (("S1", method_ids["method_b"]) in cancelled_by_sample), (
        f"Missing S1/method_b cancelled row; got {list(cancelled_by_sample)}"
    )
    assert (("S2", method_ids["method_b"]) in cancelled_by_sample), (
        f"Missing S2/method_b cancelled row; got {list(cancelled_by_sample)}"
    )

    # S1's cancelled B points at S1's failed A; S2's cancelled B points at S2's now-failed A.
    assert (cancelled_by_sample[("S1", method_ids["method_b"])]
            .cancelled_due_to_run_id == a_s1_failed)
    assert (cancelled_by_sample[("S2", method_ids["method_b"])]
            .cancelled_due_to_run_id == a_s2_running)


# =============================================================================
# Tier 3: SSE log-stream endpoint on a cancelled run
# =============================================================================


@workflow(
    purpose="SSE log-stream endpoint treats cancelled runs as terminal and "
            "emits a terminal event with status='cancelled' (US-2 Tier 3).",
)
def test_stream_logs_cancelled_run_emits_terminal_event_and_closes(tmp_wfc_project):
    """GET /api/wfc/run/{id}/stream-logs on a cancelled run returns a terminal
    SSE event with status='cancelled', then closes.

    Cancelled rows have no stdout/stderr log files on disk (they never ran).
    The endpoint must still classify them as terminal (not try to live-tail)
    and must pass the status through unchanged (unlike 'completed'->'success').
    """
    method_ids = _seed_methods(["method_a", "method_b"])
    # Seed a failed A plus a cancelled B that references it.
    pid = "pipe-sse-cancelled-1"
    a_failed = _add_run(method_ids["method_a"], "S1", pid, status="failed")
    with get_session() as s:
        cancelled_row = Run(
            method_id=method_ids["method_b"],
            sample="S1",
            pipeline_id=pid,
            status="cancelled",
            started_at=None,
            cancelled_due_to_run_id=a_failed,
        )
        s.add(cancelled_row)
        s.commit()
        s.refresh(cancelled_row)
        cancelled_id = cancelled_row.id

    Step(step_num=1, name="Hit /api/wfc/run/{id}/stream-logs",
         purpose="Cancelled run is terminal; endpoint must not block live-polling")

    _active_jobs.clear()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(f"/api/wfc/run/{cancelled_id}/stream-logs")

    Step(step_num=2, name="Assert terminal cancelled event + clean close",
         purpose="Exactly one terminal event with status='cancelled'")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # Parse SSE body.
    events = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines = [
            line[len("data: "):]
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if data_lines:
            events.append(json.loads("".join(data_lines)))

    terminal = [e for e in events if e.get("type") == "terminal"]
    assert len(terminal) == 1, f"Expected one terminal event; got {events}"
    assert terminal[0]["status"] == "cancelled", (
        f"Expected status='cancelled'; got {terminal[0]}"
    )
