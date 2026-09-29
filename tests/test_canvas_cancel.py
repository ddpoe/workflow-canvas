"""Backend cancel endpoint tests.

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

from tests.conftest import pin_project_root

import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from wfc.canvas.server import app
from wfc.canvas.state import _active_jobs
from wfc.execution.lifecycle import cancel_pipeline
from wfc.persistence import Run
from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    FIXTURE_ENV_NAME,
    create_sample_csv,
    project_archive_dir,
    write_env_record,
)
from tests.fixtures.routes import (
    build_project_snapshot,
    canvas_client,
    claimed_run,
    restore_project_snapshot,
)
from tests.harness import Scenario, node


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CANCEL_MODULE = "cancel_test_mod"
CANCEL_METHOD = "cancel_test_method"
CANCEL_SAMPLE = "s1"


@pytest.fixture(scope="module")
def cancel_project(tmp_path_factory):
    """The one-method project the cancel tests serve, built once by registration.

    Yields:
        The ``ProjectSnapshot``: the built project, its ``DATABASE_URL``,
        the live database file and the pristine copy.
    """
    from wfc.persistence import reset_engine

    root = tmp_path_factory.mktemp("canvas_cancel_project")
    snapshot = build_project_snapshot(
        Scenario(nodes=[node(CANCEL_METHOD, module=CANCEL_MODULE)],
                 samples=[CANCEL_SAMPLE]),
        root,
    )
    yield snapshot
    reset_engine()


@pytest.fixture
def db_engine(cancel_project, monkeypatch):
    """A pristine copy of the harness-built database, pinned for one test."""
    from wfc.persistence import reset_engine

    restore_project_snapshot(cancel_project, monkeypatch)

    engine = create_engine(cancel_project.database_url)
    yield engine
    engine.dispose()
    reset_engine()


@pytest.fixture
def client(db_engine, cancel_project, monkeypatch):
    """FastAPI test client over the harness-built project ``db_engine`` pinned."""
    return canvas_client(cancel_project.project.root, monkeypatch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cancel_pipeline_flips_running_rows(db_engine, cancel_project, monkeypatch):
    """``cancel_pipeline`` flips every running row to cancelled with the
    canonical message and an idempotent second call is a no-op."""
    pipeline_id = "p-cancel-1"
    run_id = claimed_run(cancel_project.project.root, monkeypatch=monkeypatch,
                         method=CANCEL_METHOD, module=CANCEL_MODULE,
                         sample=CANCEL_SAMPLE, pipeline_id=pipeline_id).run_id

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


def test_cancel_endpoint_idempotent_on_terminal_pipeline(
    client, db_engine, cancel_project, monkeypatch,
):
    """When the recorded Popen is missing/terminal, the endpoint returns
    200 with ``noop: True`` and still flips rows defensively."""
    pipeline_id = "p-idempotent"
    claimed_run(cancel_project.project.root, monkeypatch=monkeypatch,
                method=CANCEL_METHOD, module=CANCEL_MODULE, sample=CANCEL_SAMPLE,
                pipeline_id=pipeline_id)
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


@pytest.mark.integration
@requires_docker
def test_cancel_endpoint_kills_real_snakemake_subprocess_tree(
    git_project, fixture_container_image, monkeypatch,
):
    """Spawn a real Snakemake pipeline via ``POST /api/workflow/run``,
    capture its live rule-executor children mid-run, then cancel and
    assert every captured PID is gone.

    This is the load-bearing assertion: on Windows, terminating the
    Snakemake parent alone leaves rule-executor (``python -m wfc
    run-step``) descendants orphaned. The production code uses
    ``psutil.Process(pid).children(recursive=True)`` plus
    ``terminate``/``kill`` to flush the whole tree; this test would
    fail if anyone removed that recursive walk
    (the simple sibling test has no descendants to leave behind, so it
    cannot detect that change alone).

    The test uses the heartbeat fixture method (~5s of timed stdout
    ticks) so we have a deterministic window to grab live children.
    """
    from wfc.canvas.server import app
    from wfc.canvas.state import _active_jobs
    from wfc.contracts import parse_method_yaml
    from wfc.persistence import reset_engine
    from wfc.init import init_project
    from wfc.registration import register_method, register_module

    project_dir = git_project
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_dir))
    db_path = project_dir / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # init_project BEFORE pin_project_root, not after: pin_project_root writes
    # the bare resolver marker, and init_project's config step is idempotent —
    # an existing wf-canvas.toml is reused verbatim. Called the other way round
    # the project keeps the stub, which carries no [dvc] section, so
    # ensure_dvc_ready refuses and no sample can be registered at all.
    #
    # Explicit out-of-tree archive: the default resolves to
    # ~/.wfc/archives/<project> and init_dvc pre-creates it.
    init_project(project_dir, archive=str(project_archive_dir(project_dir)),
                 assume_yes=True)
    pin_project_root(monkeypatch, project_dir)
    # Execution is container-only. The heartbeat fixture method
    # declares ``env: fixture-env``; write the env manifest pointing at
    # the session-built image so registration validates and run-step dispatches
    # into the real container (whose subprocess tree this test then cancels).
    write_env_record(project_dir, FIXTURE_ENV_NAME, digest=fixture_container_image)
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

    # Register the sample the way a user does: the CSV is staged outside the
    # project (create_sample_csv puts it under <project>-sources/) and
    # registered from there, so the bytes land in the DVC cache and the row
    # records data/samples/sample_a/data.csv as where the run's restore_sample
    # rule will materialize them. Writing the file there by hand instead left
    # input_selector pointing at a sample wfc never registered -- a project no
    # user can produce, and one that keeps passing after registration or
    # restore breaks.
    create_sample_csv(project_dir, "sample_a")

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
