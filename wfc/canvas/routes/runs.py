"""Run routes: submit a pipeline, cancel it, and report its status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import PipelineInput
from ..run_state import aggregate_pipeline_status
from ..state import _active_jobs, _server_project_root
from ..submission import RunNotReady, SubmissionRefused, submit_pipeline

router = APIRouter()


# Per-node state row returned by /api/workflow/status. Captured as a typed
# Pydantic model (rather than a bare ``Dict[str, Any]``) so the OpenAPI
# schema names every field the frontend may consume — the bridge function
# ``runStatusToNodeState`` consumes the generated TS type, so any
# rename/removal here surfaces as a TS compile error.
class NodeRunState(BaseModel):
    status: str
    error: str | None = None
    error_run_id: str | None = None
    error_sample: str | None = None
    tally: dict[str, int] | None = None
    run_ids: list[str] | None = None
    upstream_node_id: str | None = None
    upstream_run_id: str | None = None
    cancelled_due_to_run_id: str | None = None
    # Cache-hit surface. Set when
    # the newest Run row for this node that has ``cache_source_run_id``
    # populated (the cache-hit audit row). Frontend uses these to emit
    # CACHE_HIT into the per-node state machine and to render the
    # cache-hit banner in InspectorPanel without spawning a streaming
    # actor for a run that has nothing to stream.
    cache_hit: bool | None = None
    original_run_id: str | None = None
    cache_key: str | None = None
    # Per-node async push state. Always present (never absent on
    # the wire) so the frontend can read them without missing-field branches.
    # ``push_state`` is the aggregated state for this node's outputs:
    #   ``deferred`` -- no remote configured
    #   ``pending`` / ``in_flight`` -- at least one output still draining
    #   ``failed`` -- at least one output exhausted retries
    #   ``pushed`` -- all outputs successfully pushed
    # Counts are summed across all RunOutput rows for the node within the
    # current pipeline_id.
    push_state: str = "deferred"
    push_pending_count: int = 0
    push_failed_count: int = 0


class WorkflowStatusResponse(BaseModel):
    job_id: str
    overall_status: str
    node_states: dict[str, NodeRunState]
    thread_alive: bool
    log: str
    error: Any | None = None


@router.post("/api/workflow/run")
def run_workflow(pipeline: PipelineInput):
    """Submit a pipeline for execution via Snakemake.

    Enriches the PipelineJSON with script paths and slot_outputs from the DB,
    writes it to a temp file, and spawns a background thread running
    ``run_pipeline()``. Returns the pipeline_id as the job_id immediately.

    Rejects empty pipelines with HTTP 400.  Before spawning the run thread it
    pre-flights Docker and git: a not-ready
    environment is rejected with HTTP 409 carrying a kind-tagged
    ``{kind, message, hint}`` payload (the same shape the frontend renders for
    pre-run errors), so no orphan run is started.  Multiple pipelines may run
    concurrently — each gets its own pipeline_id-scoped workspace.
    """
    try:
        submitted = submit_pipeline(pipeline, _server_project_root())
    except RunNotReady as exc:
        raise HTTPException(status_code=409, detail=exc.payload) from exc
    except SubmissionRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "submitted",
        "job_id": submitted.pipeline_id,
        "message": f"Pipeline '{pipeline.name or 'unnamed'}' submitted ({len(pipeline.nodes)} nodes)",
        "step_map": submitted.step_map,
    }


@router.post("/api/workflow/cancel/{job_id}")
def cancel_workflow(job_id: str):
    """Cancel an in-flight pipeline.

    Terminates the live Snakemake subprocess (and its descendants) and
    flips any ``running`` rows for this pipeline to ``cancelled`` with
    ``error_message="Cancelled by user"``.  Idempotent: cancelling a
    pipeline whose process has already exited returns 200 with a no-op
    indication.  Returns 404 for unknown job_ids.
    """
    if job_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    job_info = _active_jobs[job_id]
    proc = job_info.get("proc")

    # Mark cancellation requested BEFORE killing the subprocess so the
    # background thread's exception handler suppresses its orphan-failure
    # flip (which would otherwise race cancel_pipeline and overwrite
    # ``cancelled`` rows with ``failed``).
    job_info["cancel_requested"] = True

    # Helper to call cancel_pipeline (lazy-imported, like submission.py's run_pipeline_fn).
    def _cancel_rows():
        try:
            from ...execution.lifecycle import cancel_pipeline as _cp
            _cp(job_id)
        except Exception:
            pass  # Best-effort.

    # Already terminal or never-launched -> idempotent no-op.
    if proc is None or proc.poll() is not None:
        _cancel_rows()
        return {"status": "cancelled", "job_id": job_id, "noop": True}

    # Live subprocess -> terminate the process tree.
    try:
        import psutil as _psutil
        try:
            parent = _psutil.Process(proc.pid)
            children = parent.children(recursive=True)
        except _psutil.NoSuchProcess:
            children = []
            parent = None

        # Polite terminate first, then SIGKILL stragglers.
        for p in children:
            try:
                p.terminate()
            except _psutil.NoSuchProcess:
                pass
        if parent is not None:
            try:
                parent.terminate()
            except _psutil.NoSuchProcess:
                pass

        gone, alive = _psutil.wait_procs(
            ([parent] if parent is not None else []) + list(children),
            timeout=2.0,
        )
        for p in alive:
            try:
                p.kill()
            except _psutil.NoSuchProcess:
                pass
    except Exception:
        # Belt-and-braces: even if psutil failed, fall back to
        # subprocess-level kill so the row-flip below is meaningful.
        try:
            proc.kill()
        except Exception:
            pass

    _cancel_rows()
    return {"status": "cancelled", "job_id": job_id, "noop": False}


@router.get("/api/workflow/status/{job_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(job_id: str):
    """Return per-node and overall status for a running or completed pipeline.

    Queries the ``runs`` table by ``pipeline_id`` and derives overall status.
    Also returns whether the background thread is still alive and any captured
    log output.

    Args:
        job_id: The pipeline_id returned by the run endpoint.

    Returns:
        JSON with ``job_id``, ``overall_status``, ``node_states``, ``thread_alive``,
        ``log``, and ``error`` fields.
    """
    if job_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    job_info = _active_jobs[job_id]
    thread = job_info.get("thread")
    thread_alive = thread.is_alive() if thread else False
    job_error = job_info.get("error")

    status = aggregate_pipeline_status(
        job_id,
        job_info.get("step_map", {}),
        thread_alive,
        job_error,
        job_info.get("log_dir"),
        bool(job_info.get("cancel_requested")),
    )

    return {
        "job_id": job_id,
        "overall_status": status.overall_status,
        "node_states": status.node_states,
        "thread_alive": thread_alive,
        "log": status.log,
        "error": job_error,
    }
