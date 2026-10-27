"""Canvas API route tests: a background run's failure and the status route's per-node extras.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``submit-background-failure``
and ``status-node-extras``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from axiom_annotations import workflow
from sqlmodel import Session

from tests.fixtures.fakes import seed_active_job, stub_pipeline_submission
from wfc.canvas.state import _active_jobs
from wfc.persistence import Method, MethodContract, Module, Run, RunOutput
from wfc.version import DirtyRepositoryError

MODULE = "demo"


def _seed_methods(engine, names: list[str]) -> dict[str, int]:
    """Register one module and the named methods through the models.

    Args:
        engine: The test database's engine.
        names: Method names to register under ``MODULE``.

    Returns:
        Each method name mapped to its row id.
    """
    with Session(engine) as session:
        mod = Module(name=MODULE)
        session.add(mod)
        session.flush()
        methods = [
            Method(name=name, module_id=mod.id, script_path=f"methods/{name}/{name}.py",
                   env="container:demo")
            for name in names
        ]
        session.add_all(methods)
        session.flush()
        session.add_all([
            MethodContract(method_id=m.id, input_slots={}, output_slots={}, params_schema={})
            for m in methods
        ])
        session.commit()
        return {m.name: m.id for m in methods}


def _register_clean(root) -> None:
    """Register ``demo/clean`` through production registration.

    A submitted node's method must have a version to run, so the method is
    registered against an env record naming a registry image. The test
    stubs ``run_pipeline``, so the env pre-flight (which would ask Docker
    whether the image is present) is not reached.

    Args:
        root: The served project's root.
    """
    from tests.fixtures.conftest import (
        register_test_method,
        sample_source_dir,
        write_env_record,
    )

    write_env_record(root, MODULE, digest="d" * 64, image="ghcr.io/wfc-test/demo")
    method_dir = sample_source_dir(root) / "_methods" / "clean"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        f"env: {MODULE}\n"
        "inputs:\n  data:\n    type: .csv\n    required: false\n"
        "outputs: {}\n"
        "params: {}\n",
        encoding="utf-8",
    )
    (method_dir / "clean.py").write_text("def main():\n    pass\n", encoding="utf-8")
    register_test_method(root, module_name=MODULE, method_dir=method_dir,
                         method_name="clean")


@pytest.mark.parametrize("cancel_first", [False, True], ids=["no-cancel", "after-cancel"])
@workflow(
    purpose="A background run that raises closes the pipeline's rows only when no "
            "cancel was requested, and the status route reports the classified "
            "error either way; with its node never started, the pipeline reads "
            "failed, or cancelled when the user cancelled it",
)
def test_a_failed_background_run_closes_rows_unless_cancelled(
    canvas_client, tmp_project, ready_preflight, cancel_first
):
    _register_clean(tmp_project)
    started = threading.Event()
    release = threading.Event()
    failed_pipelines: list[str] = []

    def fake_run_pipeline(**kwargs):
        started.set()
        release.wait(timeout=10)
        raise DirtyRepositoryError("Working tree has uncommitted changes to tracked files")

    payload = {
        "name": "failing",
        "nodes": [{"id": "clean_1", "type": "method", "method": "clean", "module": MODULE}],
        "links": [],
        "samples": [],
    }
    with stub_pipeline_submission(run_pipeline=fake_run_pipeline,
                                  fail_pipeline=failed_pipelines.append):
        submitted = canvas_client.post("/api/workflow/run", json=payload)
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job_id"]
        thread = _active_jobs[job_id]["thread"]
        assert started.wait(timeout=10), "the background run never started"
        if cancel_first:
            cancelled = canvas_client.post(f"/api/workflow/cancel/{job_id}")
            assert cancelled.status_code == 200, cancelled.text
        release.set()
        thread.join(timeout=10)
        assert not thread.is_alive()

    status = canvas_client.get(f"/api/workflow/status/{job_id}")
    assert status.status_code == 200, status.text
    assert failed_pipelines == ([] if cancel_first else [job_id])
    assert status.json()["node_states"]["clean_1"]["status"] == "pending"
    assert status.json()["overall_status"] == ("cancelled" if cancel_first else "failed")
    error = status.json()["error"]
    assert error["kind"] == "dirty_repo"
    assert error["message"] == "Working tree has uncommitted changes to tracked files"
    assert error["hint"]


def test_a_terminal_cancel_of_a_canvas_run_keeps_its_rows_from_the_failure_flip(
    canvas_client, tmp_project, ready_preflight
):
    """A `wfc cancel` from a terminal sets only the pipeline record's flag; the
    background run that then raises leaves the rows to the cancel, though the
    record is gone by the time the failure is handled."""
    from wfc.execution.pipeline_record import (
        ENTRY_POINT_CANVAS,
        remove_pipeline_record,
        request_cancel,
        write_pipeline_record,
    )

    _register_clean(tmp_project)
    started = threading.Event()
    release = threading.Event()
    failed_pipelines: list[str] = []

    def fake_run_pipeline(**kwargs):
        root, pid = kwargs["project_root"], kwargs["pipeline_id"]
        write_pipeline_record(root, pid, ENTRY_POINT_CANVAS)
        started.set()
        release.wait(timeout=10)
        # The engine loop's cancel probe sees the record's flag; the run's
        # end then removes the record before the exception reaches the thread.
        assert kwargs["is_cancelled"]() is True
        remove_pipeline_record(root, pid)
        raise RuntimeError("Snakemake exited with code 1")

    payload = {
        "name": "terminal-cancel",
        "nodes": [{"id": "clean_1", "type": "method", "method": "clean", "module": MODULE}],
        "links": [],
        "samples": [],
    }
    with stub_pipeline_submission(run_pipeline=fake_run_pipeline,
                                  fail_pipeline=failed_pipelines.append):
        submitted = canvas_client.post("/api/workflow/run", json=payload)
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job_id"]
        thread = _active_jobs[job_id]["thread"]
        assert started.wait(timeout=10), "the background run never started"
        assert request_cancel(tmp_project, job_id).cancel_requested
        assert not _active_jobs[job_id]["cancel_requested"], (
            "the premise: only the record's flag is set")
        release.set()
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert failed_pipelines == []


@pytest.mark.parametrize(
    "thread_alive, rowless_status",
    [(True, "pending"), (False, "completed")],
    ids=["thread-alive", "thread-ended"],
)
@workflow(
    purpose="The status route marks the node whose newest row reused a cached run, "
            "gives every node a push state and counts from its outputs' push "
            "statuses, and settles a rowless node as completed once the thread "
            "has ended without an error",
)
def test_status_reports_cache_hits_push_state_and_rowless_nodes(
    canvas_client, canvas_db, thread_alive, rowless_status
):
    ids = _seed_methods(
        canvas_db, ["cached", "fail_mix", "in_flight_mix", "all_pushed", "rowless"]
    )
    job_id = "extras-job"
    now = datetime.now(timezone.utc)
    output_statuses = {
        "fail_mix": ["failed", "pushed"],
        "in_flight_mix": ["pending", "in_flight"],
        "all_pushed": ["pushed", "pushed"],
    }
    with Session(canvas_db) as session:
        original = Run(method_id=ids["cached"], pipeline_id="earlier-job", node_id="n_cached",
                       status="completed", sample="s1", started_at=now - timedelta(days=1))
        session.add(original)
        session.flush()
        session.add(Run(method_id=ids["cached"], pipeline_id=job_id, node_id="n_cached",
                        status="completed",
                        sample="s1", started_at=now, cache_source_run_id=original.id,
                        cache_key="ck-123"))
        for method_name, statuses in output_statuses.items():
            run = Run(method_id=ids[method_name], pipeline_id=job_id, node_id=f"n_{method_name}",
                      status="completed", sample="s1", started_at=now)
            session.add(run)
            session.flush()
            session.add_all([
                RunOutput(run_id=run.id, output_name=f"out_{i}", artifact_type="method_file",
                          push_status=push_status)
                for i, push_status in enumerate(statuses)
            ])
        session.commit()
        original_id = original.id

    seed_active_job(job_id, alive=thread_alive,
                    step_map={f"n_{name}": name for name in ids})

    resp = canvas_client.get(f"/api/workflow/status/{job_id}")
    assert resp.status_code == 200, resp.text
    states = resp.json()["node_states"]

    # (status, push_state, push_pending_count, push_failed_count) per node.
    assert {
        node_id: (ns["status"], ns["push_state"], ns["push_pending_count"],
                  ns["push_failed_count"])
        for node_id, ns in states.items()
    } == {
        "n_cached": ("completed", "deferred", 0, 0),
        "n_fail_mix": ("completed", "failed", 0, 1),
        "n_in_flight_mix": ("completed", "in_flight", 2, 0),
        "n_all_pushed": ("completed", "pushed", 0, 0),
        "n_rowless": (rowless_status, "deferred", 0, 0),
    }
    assert {node_id for node_id, ns in states.items() if ns.get("cache_hit")} == {"n_cached"}
    assert states["n_cached"]["original_run_id"] == str(original_id)
    assert states["n_cached"]["cache_key"] == "ck-123"
