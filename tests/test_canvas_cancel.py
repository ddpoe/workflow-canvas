"""ADR-015 Phase D Pass 2: backend cancel endpoint tests (US-6).

Covers:
- ``cancel_pipeline()`` flips ``running`` rows to ``cancelled`` with the
  canonical ``"Cancelled by user"`` error message.
- ``POST /api/workflow/cancel/{job_id}`` returns 404 for unknown jobs.
- The endpoint is idempotent on terminal pipelines.
- The endpoint terminates the live Snakemake subprocess (and its
  descendants) — the no-orphan-processes constraint on Windows.

  Two tests cover the live-kill behaviour at different fidelities:
  1. ``test_cancel_endpoint_kills_live_subprocess_tree`` (fast, unit-level):
     stubbed Popen sleep loop with no descendants — proves parent is
     terminated.
  2. ``test_cancel_endpoint_kills_real_snakemake_subprocess_tree``
     (slower, integration-level): spawns real Snakemake via the
     ``/api/workflow/run`` endpoint, captures the live rule-executor
     children, then cancels and asserts every descendant PID is gone.
     This is the test that actually verifies the load-bearing
     no-orphans constraint — if the production code ever regresses to
     parent-only termination (skipping ``children(recursive=True)``),
     test #2 fails while #1 still passes.
"""
from __future__ import annotations

import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from wfc.canvas.server import app, _active_jobs
from wfc.cli import cancel_pipeline
from wfc.models import Run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """In-memory SQLite engine with the Run table."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture
def client(db_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


def _seed_running_run(engine, pipeline_id: str) -> int:
    """Insert a stub `running` Run row tied to ``pipeline_id``."""
    with Session(engine) as session:
        # Run requires method_id; create a fake one inline for these tests.
        from wfc.models import Module, Method
        mod = session.exec(select(Module)).first()
        if mod is None:
            mod = Module(name="cancel_test_mod", description="")
            session.add(mod)
            session.flush()
        meth = session.exec(select(Method)).first()
        if meth is None:
            meth = Method(name="cancel_test_method", module_id=mod.id, script_path="x.py")
            session.add(meth)
            session.flush()
        run = Run(
            method_id=meth.id,
            sample="s1",
            pipeline_id=pipeline_id,
            status="running",
        )
        session.add(run)
        session.commit()
        return run.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cancel_pipeline_flips_running_rows(db_engine):
    """``cancel_pipeline`` flips every running row to cancelled with the
    canonical message and an idempotent second call is a no-op (US-6)."""
    pipeline_id = "p-cancel-1"
    run_id = _seed_running_run(db_engine, pipeline_id)

    n = cancel_pipeline(pipeline_id)
    assert n == 1

    with Session(db_engine) as session:
        row = session.exec(select(Run).where(Run.id == run_id)).one()
    assert row.status == "cancelled"
    assert row.error_message == "Cancelled by user"
    assert row.finished_at is not None

    # Idempotent: running call again should be a no-op (0 rows flipped).
    n2 = cancel_pipeline(pipeline_id)
    assert n2 == 0


def test_cancel_endpoint_404_for_unknown_job(client):
    """Cancelling a job_id we never registered returns 404."""
    rand = str(uuid.uuid4())
    res = client.post(f"/api/workflow/cancel/{rand}")
    assert res.status_code == 404


def test_cancel_endpoint_idempotent_on_terminal_pipeline(client, db_engine):
    """When the recorded Popen is missing/terminal, the endpoint returns
    200 with ``noop: True`` and still flips rows defensively."""
    pipeline_id = "p-idempotent"
    _seed_running_run(db_engine, pipeline_id)
    # Simulate a job entry whose process already exited.
    _active_jobs[pipeline_id] = {
        "thread": None,
        "pipeline_id": pipeline_id,
        "step_map": {},
        "log_dir": "",
        "error": None,
        "proc": None,
    }
    res = client.post(f"/api/workflow/cancel/{pipeline_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "cancelled"
    assert body["noop"] is True

    # Second call still 200 (no-op).
    res2 = client.post(f"/api/workflow/cancel/{pipeline_id}")
    assert res2.status_code == 200
    assert res2.json()["noop"] is True


def test_cancel_endpoint_kills_live_subprocess_tree(client):
    """Fast unit-level check that the cancel endpoint terminates the
    parent ``proc`` registered in ``_active_jobs``.

    This test stubs a single ``python.exe`` sleep loop (no descendants)
    so it stays under a second; the recursive-tree teardown is
    exercised by the integration sibling
    ``test_cancel_endpoint_kills_real_snakemake_subprocess_tree``."""
    pipeline_id = "p-livekill"
    # A simple sleep loop in Python — long enough to outlast the test.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time;\nwhile True:\n    time.sleep(1)\n"],
    )
    try:
        _active_jobs[pipeline_id] = {
            "thread": None,
            "pipeline_id": pipeline_id,
            "step_map": {},
            "log_dir": "",
            "error": None,
            "proc": proc,
        }
        # Sanity: process is alive before cancel.
        assert proc.poll() is None

        res = client.post(f"/api/workflow/cancel/{pipeline_id}")
        assert res.status_code == 200

        # Subprocess should have exited within a couple of seconds.
        deadline = time.time() + 5.0
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "cancel endpoint did not terminate live subprocess"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


# ---------------------------------------------------------------------------
# Integration test: real Snakemake subprocess tree teardown
# ---------------------------------------------------------------------------


def test_cancel_endpoint_kills_real_snakemake_subprocess_tree(
    git_project, monkeypatch,
):
    """Spawn a real Snakemake pipeline via ``POST /api/workflow/run``,
    capture its live rule-executor children mid-run, then cancel and
    assert every captured PID is gone.

    This is the load-bearing US-6 assertion: on Windows, terminating the
    Snakemake parent alone leaves rule-executor (``python -m wfc
    run-step``) descendants orphaned. The production code uses
    ``psutil.Process(pid).children(recursive=True)`` plus
    ``terminate``/``kill`` to flush the whole tree; this test would
    regress to a false pass if anyone removed that recursive walk
    (the simple sibling test has no descendants to leave behind, so it
    cannot detect that regression alone).

    The test uses the heartbeat fixture method (~5s of timed stdout
    ticks) so we have a deterministic window to grab live children.
    """
    from wfc.canvas.server import app, _active_jobs
    from wfc.contracts import parse_method_yaml
    from wfc.database import reset_engine
    from wfc.init import init_project
    from wfc.register import register_method, register_module

    project_dir = git_project
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_dir))
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(project_dir))
    db_path = project_dir / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    init_project(project_dir)
    reset_engine()

    register_module(name="test_pipeline", contracts=[])

    # Copy the heartbeat fixture method into the project and register it.
    heartbeat_src = (
        Path(__file__).resolve().parent / "fixtures" / "methods" / "heartbeat"
    )
    heartbeat_dest = project_dir / "methods" / "heartbeat"
    if heartbeat_dest.exists():
        import shutil as _sh
        _sh.rmtree(heartbeat_dest)
    import shutil as _sh
    _sh.copytree(heartbeat_src, heartbeat_dest)

    register_method(
        method_dir=heartbeat_dest,
        module_name="test_pipeline",
        method_name="heartbeat",
    )

    # Stage a sample CSV so input_selector has data to feed in.
    sample_dir = project_dir / "data" / "samples" / "sample_a"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "data.csv").write_text("id,value\n1,10\n2,20\n3,30\n")

    _active_jobs.clear()
    client = TestClient(app, raise_server_exceptions=False)

    # POST a real pipeline. Same shape as scripts/dev_routes._streaming_demo
    # so the canvas-side enrichment + Snakefile generation behave
    # identically to the dogfood path.
    payload = {
        "name": "cancel-tree-int",
        "nodes": [
            {
                "id": "node_selector",
                "type": "input_selector",
                "method": "",
                "module": "",
                "params": {},
                "samples": ["sample_a"],
                "source": "registered",
                "fan_mode": "out",
            },
            {
                "id": "node_heartbeat",
                "type": "method",
                "method": "heartbeat",
                "module": "test_pipeline",
                "params": {
                    "duration_s": 5.0,
                    "stdout_lines": 15,
                    "stderr_every": 4,
                },
            },
        ],
        "links": [
            {
                "source": "node_selector",
                "target": "node_heartbeat",
                "sourceHandle": "output",
                "targetHandle": "data",
            },
        ],
        "samples": ["sample_a"],
    }

    res = client.post("/api/workflow/run", json=payload)
    assert res.status_code == 200, f"run failed: {res.status_code} {res.text}"
    job_id = res.json()["job_id"]

    # Poll until Snakemake has launched at least one rule-executor child.
    # Heartbeat sleeps duration_s/stdout_lines = ~0.33s between ticks, and
    # rule-executor (`python -m wfc run-step`) lives for the whole heartbeat
    # — so once it's spawned we have a comfortable window before the run
    # finishes.  Generous deadline to absorb Snakemake startup on cold
    # caches / slow CI.
    captured_pids: list[int] = []
    deadline = time.time() + 60.0
    snakemake_proc = None
    while time.time() < deadline:
        job_info = _active_jobs.get(job_id) or {}
        sm_proc = job_info.get("proc")
        if sm_proc is not None and sm_proc.poll() is None:
            try:
                parent = psutil.Process(sm_proc.pid)
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            if children:
                snakemake_proc = sm_proc
                captured_pids = [c.pid for c in children]
                break
        time.sleep(0.2)

    # Guard against a false pass: if we never saw any descendants, the
    # test isn't actually verifying tree teardown — fail loudly.
    assert captured_pids, (
        "Never observed any Snakemake child processes within 60s. "
        "Either Snakemake failed to start or the heartbeat method finished "
        "before we could grab the tree — test cannot validate recursive "
        "termination. Last job_info: "
        f"{_active_jobs.get(job_id)}"
    )
    assert snakemake_proc is not None
    parent_pid = snakemake_proc.pid

    # Now cancel via the endpoint.
    cancel_res = client.post(f"/api/workflow/cancel/{job_id}")
    assert cancel_res.status_code == 200, cancel_res.text
    body = cancel_res.json()
    assert body["status"] == "cancelled"
    # noop=False because the live subprocess was still running.
    assert body["noop"] is False

    # After cancel returns, every captured descendant PID should be dead.
    # psutil.pid_exists is the cleanest portable check; on Windows a
    # zombie/exited process disappears from the table once the parent
    # reaps it (which the cancel handler does via wait_procs).
    deadline = time.time() + 5.0
    still_alive: list[int] = []
    while time.time() < deadline:
        still_alive = [pid for pid in captured_pids if psutil.pid_exists(pid)]
        if not still_alive and not psutil.pid_exists(parent_pid):
            break
        time.sleep(0.1)

    assert not still_alive, (
        f"Cancel endpoint left descendant PIDs alive after returning: "
        f"{still_alive} (captured {len(captured_pids)} children of "
        f"snakemake pid={parent_pid}). The recursive child-termination "
        "path may have regressed."
    )
    assert not psutil.pid_exists(parent_pid), (
        f"Snakemake parent pid {parent_pid} still alive after cancel."
    )

    # Best-effort: ensure background thread also winds down so the
    # test client teardown doesn't race.
    job_info = _active_jobs.get(job_id) or {}
    thread = job_info.get("thread")
    if thread is not None:
        thread.join(timeout=5.0)

    reset_engine()
