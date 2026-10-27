"""Run routes: submit a pipeline, cancel it, and report its status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...execution import (
    UnknownPipelineError,
    cancel_recorded_pipeline,
    is_pipeline_id,
    kill_process_tree,
    process_identity,
)
from ..models import PipelineInput
from ..recovery import reach_pipeline
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
    # Why a cancelled node was cancelled: ``user``, ``upstream_failed``,
    # ``not_run``, ``interrupted`` or ``engine_failed``, from its newest
    # cancelled row. The Inspector reads it,
    # not the cause link, to tell a stop from a target never started.
    cancel_reason: str | None = None
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
    """Cancel a running pipeline, in this server's job table or reached by its record.

    Goes through Execution's one cancel operation: it sets the record's
    cancel flag, kills the engine's process tree when its identity matches,
    and flips the ``running`` rows to ``cancelled`` for the user; when the
    pipeline's owner is gone it also gives every unstarted target a ``user``
    row, releases what the pipeline held and removes the record. For a job
    in this server's table the in-memory flag is set first, so its run
    thread's failure branch leaves the rows to the cancel and its own end
    steps walk with ``user``; a job whose process is not recorded (refused
    before its record was written) has that process's tree killed here.

    Idempotent: a pipeline whose engine has already exited answers 200 with
    ``noop: true``. An id with neither a job nor a record nor a running row,
    or one that is not a pipeline id, answers 404.
    """
    project_root = _server_project_root()
    job_info = _active_jobs.get(job_id)
    if job_info is None and not is_pipeline_id(job_id):
        # Not a pipeline id: it never forms a record path.
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if job_info is not None:
        # Before the cancel, so the run thread's exception handler suppresses
        # its orphan-failure flip (which would otherwise race the cancel and
        # overwrite ``cancelled`` rows with ``failed``).
        job_info["cancel_requested"] = True
    else:
        # Kept before the cancel, which may remove the record: the status
        # route then still answers for the pipeline, from its rows.
        reach_pipeline(project_root, job_id)

    try:
        outcome = cancel_recorded_pipeline(project_root, job_id)
    except UnknownPipelineError as exc:
        if job_info is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
        outcome = None

    killed = bool(outcome is not None and outcome.engine_killed)
    proc = job_info.get("proc") if job_info is not None else None
    if not killed and proc is not None and proc.poll() is None:
        killed = kill_process_tree(process_identity(proc.pid))
    return {"status": "cancelled", "job_id": job_id, "noop": not killed}


@router.get("/api/workflow/status/{job_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(job_id: str):
    """Return per-node and overall status for a running or completed pipeline.

    Queries the ``runs`` table by ``pipeline_id`` and derives overall status.
    A job in this server's table reports its run thread's liveness, its
    step map, its recorded error and its log. Any other pipeline is reached
    through its record (one another process runs, a CLI pipeline among
    them, or one this server ran before a restart): its step map comes from
    its frozen document, its log from its run directory, and the record's
    presence stands for the thread's liveness. Once reached, it is answered
    from its rows after its record is gone too.

    Args:
        job_id: The pipeline_id returned by the run endpoint.

    Returns:
        JSON with ``job_id``, ``overall_status``, ``node_states``, ``thread_alive``,
        ``log``, and ``error`` fields. 404 for an id with neither a job nor
        a record that this server has not reached, and for one that is not
        a pipeline id.
    """
    job_info = _active_jobs.get(job_id)
    if job_info is None:
        if not is_pipeline_id(job_id):
            # Not a pipeline id: it never forms a record path.
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        reached = reach_pipeline(_server_project_root(), job_id)
        if reached is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        status = aggregate_pipeline_status(
            job_id, reached.step_map, reached.live, None, reached.log_dir,
            reached.cancel_requested,
        )
        return {
            "job_id": job_id,
            "overall_status": status.overall_status,
            "node_states": status.node_states,
            "thread_alive": reached.live,
            "log": status.log,
            "error": None,
        }

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
