"""Server state: the canvas process's module-level state written directly.

The canvas server keeps two things in ``wfc.canvas.state``: the loaded
project's provider and the table of active jobs. Production writes the first
through the load endpoint and the second through a submission's background
thread; a route test that wants either bound without driving those writes
takes one of the shortcuts here.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from ._entry import fake, shortcut


@shortcut(
    boundary="wfc.canvas.state._wfc_provider -- the loaded-project binding "
             "the load endpoint writes",
    preserves="whatever the bound object answers: a real WfcProvider over "
              "the served project answers as the endpoint's would; a "
              "sentinel or None binds the unloaded and loaded-with-nothing "
              "states",
    not_proven="the load endpoint, or that a served project is what a user "
               "loaded",
    backed_by="pm_mvp::tests.test_canvas_project_root::"
              "test_canvas_reads_and_writes_one_database",
)
def bind_provider(monkeypatch, provider) -> object:
    """Bind a provider (or ``None``) as the server's loaded project.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        provider: A ``WfcProvider`` (loaded or not), ``None``, or any
            object exposing the surface the route under test reads.

    Returns:
        ``provider``.
    """
    from wfc.canvas import state as canvas_state

    monkeypatch.setattr(canvas_state, "_wfc_provider", provider)
    return provider


@fake(
    boundary="scripts.dev_routes._ensure_reference_run -- the demo's "
             "reference-topology seeding",
    preserves="the demo pipeline document served, with the reference's run "
              "id pinned",
    not_proven="that a reference run is seeded and bound",
    backed_by="pm_mvp::tests.test_dev_routes_demo::"
              "test_reference_seeding_submits_once_and_binds_the_completed_run",
)
def stub_reference_seeding(monkeypatch, run_id: str = "1") -> None:
    """Pin the reference run id the demo routes would have seeded.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        run_id: The id every reference resolves to.
    """
    from scripts import dev_routes

    monkeypatch.setattr(dev_routes, "_ensure_reference_run",
                        lambda method, sample=None: run_id)


@shortcut(
    boundary="wfc.canvas.state._active_jobs -- the job record a submission's "
             "background thread would have written",
    preserves="the record's keys as the status route reads them; the "
              "thread's is_alive answer",
    not_proven="that a submission produced the record, or that the thread "
               "ran anything",
    backed_by="pm_mvp::tests.test_canvas_api_runs::"
              "test_a_failed_background_run_closes_rows_unless_cancelled",
)
def seed_active_job(job_id: str, *, alive: bool = False,
                    pipeline_id: str | None = None, log_dir=None,
                    step_map: dict | None = None, error=None,
                    **extra) -> dict:
    """Write one active-job record with a stand-in thread.

    Args:
        job_id: The job's id (the status route's path parameter).
        alive: What the stand-in thread's ``is_alive()`` answers.
        pipeline_id: The record's pipeline id; defaults to ``job_id``.
        log_dir: The record's log directory.
        step_map: Node id -> method name, when the status route needs it.
        error: The structured error a failed start stores, or ``None``.
        **extra: Any other key the record carries.

    Returns:
        The record written.
    """
    from wfc.canvas.state import _active_jobs

    record = {
        "thread": MagicMock(is_alive=MagicMock(return_value=alive)),
        "pipeline_id": job_id if pipeline_id is None else pipeline_id,
        "log_dir": log_dir,
        "error": error,
    }
    if step_map is not None:
        record["step_map"] = step_map
    record.update(extra)
    _active_jobs[job_id] = record
    return record
