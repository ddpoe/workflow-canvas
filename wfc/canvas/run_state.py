"""The run state of a submitted pipeline: the ``status-aggregation`` workflow.

``aggregate_pipeline_status`` reads the pipeline's run and output rows,
tallies each node, projects the node states, settles the unstarted nodes,
derives the overall status, and reads the log. Steps 2 to 5 are functions
over plain values: run and output records in; tallies, node states and an
overall status out. The status route is the HTTP wrapper around it and passes
the job's step map, its thread's liveness and its recorded error as values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from axiom_annotations import AutoStep, task, workflow
from sqlmodel import select

from ..persistence import get_session
from ..persistence import Run, RunOutput

# The newest failure's message is capped so the Inspector panel stays compact.
_ERROR_MESSAGE_CAP = 600


@dataclass(frozen=True)
class RunRecord:
    """One of the pipeline's run rows, as the tally reads it.

    Attributes:
        run_id: The row's id.
        node_id: The pipeline node the row ran for, as the claim recorded it;
            ``None`` for a row that recorded no node.
        status: The row's status as stored.
        sample: The row's sample.
        error_message: The row's error message.
        cache_source_run_id: The run whose outputs this row reused, if any.
        cache_key: The row's cache key.
        cancelled_due_to_run_id: For a row the pipeline-end walk cancelled,
            the failed run that caused it.
        cause_node_id: The node the failed run behind a cancellation ran
            for, when that run is one of the pipeline's rows.
    """

    run_id: Optional[int]
    node_id: Optional[str]
    status: Optional[str]
    sample: Optional[str]
    error_message: Optional[str]
    cache_source_run_id: Optional[int]
    cache_key: Optional[str]
    cancelled_due_to_run_id: Optional[int] = None
    cause_node_id: Optional[str] = None


@dataclass(frozen=True)
class OutputRecord:
    """One output row of the pipeline's runs.

    Attributes:
        node_id: The node of the run that owns the output.
        push_status: The output's push status as stored.
    """

    node_id: str
    push_status: Optional[str]


@dataclass(frozen=True)
class CancelCause:
    """The failed run behind a node's cancellation.

    Attributes:
        run_id: The failed run's id.
        node_id: The node that run ran for, or ``None`` when it is not one
            of the pipeline's rows.
    """

    run_id: str
    node_id: Optional[str]


@dataclass(frozen=True)
class NodeTallies:
    """Per-node tallies of a pipeline's rows, each keyed by the node id the
    rows recorded.

    Attributes:
        counts: Row count per status; ``running``, ``completed`` and
            ``failed`` are always present. Nodes appear in the order of
            their newest row.
        status: Each node's collapsed display state.
        run_ids: Each node's run ids, newest first.
        errors: The newest failed or cancelled row that carries a message:
            ``message`` (capped), ``run_id``, ``sample``, ``status``.
        cache_hits: The newest row that reused a cached run:
            ``original_run_id``, ``cache_key``.
        push_counts: Output count per push status, for nodes whose runs
            have outputs.
        cancel_causes: The failed run behind the newest cancelled row that
            names one.
    """

    counts: Dict[str, Dict[str, int]]
    status: Dict[str, str]
    run_ids: Dict[str, List[str]]
    errors: Dict[str, Dict[str, Any]]
    cache_hits: Dict[str, Dict[str, Any]]
    push_counts: Dict[str, Dict[str, int]]
    cancel_causes: Dict[str, CancelCause] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineStatus:
    """The aggregated status of a pipeline.

    Attributes:
        overall_status: The pipeline's overall status.
        node_states: Each canvas method node's state.
        log: The captured stdout, then stderr under a separator.
    """

    overall_status: str
    node_states: Dict[str, Dict[str, Any]]
    log: str


def _aggregate_tally(tally: Dict[str, int]) -> str:
    """Collapse a per-sample tally into a single display state."""
    if tally.get("running", 0) > 0:
        return "running"
    completed = tally.get("completed", 0)
    failed = tally.get("failed", 0)
    cancelled = tally.get("cancelled", 0)
    if completed > 0 and failed > 0:
        return "mixed"
    if failed > 0:
        return "failed"
    if completed > 0:
        return "completed"
    # No running, no failures, no completions — if any rows exist
    # they're cancelled. Without this branch the node collapses to
    # "unknown" and overall_status falls through to the "running"
    # fallback, leaving a fully-cancelled pipeline reporting
    # in-flight forever.
    if cancelled > 0:
        return "cancelled"
    return "unknown"


def _aggregate_push(bucket: Dict[str, int]) -> str:
    """Collapse a push-status bucket into a single display state.

    Any failed row dominates; otherwise any in-flight is
    ``in_flight``; otherwise any pending is ``pending``; otherwise
    any pushed (and nothing else) is ``pushed``; otherwise ``deferred``.
    Mirrors the same dominance ordering as the run-status aggregate.
    """
    if bucket.get("failed", 0) > 0:
        return "failed"
    if bucket.get("in_flight", 0) > 0:
        return "in_flight"
    if bucket.get("pending", 0) > 0:
        return "pending"
    if bucket.get("pushed", 0) > 0 and (
        bucket.get("pending", 0) + bucket.get("in_flight", 0) + bucket.get("failed", 0)
    ) == 0:
        return "pushed"
    return "deferred"


@task(
    purpose="Read the pipeline's run rows, newest first, and the output rows of "
            "those runs into plain records; a cancelled row carries the node of "
            "the failed run behind it",
    inputs="The pipeline id",
    outputs="Run records (newest first) and output records, each carrying its "
            "node id",
)
def read_pipeline_rows(pipeline_id: str) -> Tuple[List[RunRecord], List[OutputRecord]]:
    """Read a pipeline's run and output rows.

    Args:
        pipeline_id: The pipeline whose rows are read.

    Returns:
        The run records, newest first, and the output records. An output
        record is read only for a run that recorded its node.
    """
    run_records: List[RunRecord] = []
    output_records: List[OutputRecord] = []
    with get_session() as session:
        runs = session.exec(
            select(Run)
            .where(Run.pipeline_id == pipeline_id)
            .order_by(Run.started_at.desc())
        ).all()
        # run id -> node id over the pipeline's rows: names the node of a
        # cancellation's cause and joins the outputs below. A cause run that
        # is not among these rows leaves the cause's node unset.
        run_id_to_node: Dict[int, str] = {
            r.id: r.node_id for r in runs
            if r.id is not None and r.node_id is not None
        }
        for r in runs:
            cause = r.cancelled_due_to_run_id
            run_records.append(RunRecord(
                run_id=r.id,
                node_id=r.node_id,
                status=r.status,
                sample=r.sample,
                error_message=r.error_message,
                cache_source_run_id=r.cache_source_run_id,
                cache_key=r.cache_key,
                cancelled_due_to_run_id=cause,
                cause_node_id=run_id_to_node.get(cause) if cause is not None else None,
            ))
        if run_id_to_node:
            outputs = session.exec(
                select(RunOutput).where(
                    RunOutput.run_id.in_(list(run_id_to_node.keys()))  # type: ignore[union-attr]
                )
            ).all()
            for ro in outputs:
                output_records.append(OutputRecord(
                    node_id=run_id_to_node[ro.run_id], push_status=ro.push_status,
                ))
    return run_records, output_records


@task(
    purpose="Tally each node's rows: status counts and the collapsed display "
            "state, run ids newest first, the newest failure, the newest cache "
            "hit, the failed run behind the newest cancellation, and the "
            "push-status counts of its outputs; a row that recorded no node is "
            "not tallied",
    inputs="Run records (newest first) and output records",
    outputs="NodeTallies keyed by node id",
)
def tally_nodes(
    runs: Sequence[RunRecord], outputs: Sequence[OutputRecord],
) -> NodeTallies:
    """Tally a pipeline's rows per node.

    Each row counts toward the node id it recorded, so two nodes of one
    method keep their own tallies. A row with no node id is ignored.

    Args:
        runs: The run records, newest first.
        outputs: The output records.

    Returns:
        The per-node tallies.
    """
    # Tally per-sample statuses per node so the canvas can show a
    # "mixed" aggregate when fan-out samples diverge (one failed, rest
    # succeeded, etc.). A flat last-write-wins dict would hide this.
    counts: Dict[str, Dict[str, int]] = {}
    # Also collect run_ids per node so the canvas can point the Output tab
    # at a specific Run row for streaming. Ordered newest-first by started_at
    # so run_ids[0] is the most recent attempt.
    run_ids: Dict[str, List[str]] = {}
    # Most-recent failed/cancelled run per node so the canvas can show a
    # one-liner on the node without the user having to open the log stream.
    errors: Dict[str, Dict[str, Any]] = {}
    # Cache-hit detection. A node is rendered as a cache hit from its newest
    # Run row that carries ``cache_source_run_id`` (the audit row written by
    # the engine when cache reuse skipped real execution).
    cache_hits: Dict[str, Dict[str, Any]] = {}
    # Per-node push aggregates, counted across all RunOutput rows belonging
    # to runs in this pipeline.
    push_counts: Dict[str, Dict[str, int]] = {}
    # The failed run behind the newest cancelled row per node, so the
    # canvas can tell a node cancelled by an upstream failure from one the
    # user stopped.
    cancel_causes: Dict[str, CancelCause] = {}

    for ro in outputs:
        bucket = push_counts.setdefault(
            ro.node_id,
            {"pending": 0, "in_flight": 0, "pushed": 0, "failed": 0, "deferred": 0},
        )
        status = ro.push_status or "deferred"
        if status not in bucket:
            bucket[status] = 0
        bucket[status] += 1

    for run in runs:
        key = run.node_id
        if key is None:
            continue
        status = run.status or "unknown"
        tally = counts.setdefault(key, {"running": 0, "completed": 0, "failed": 0})
        if status not in tally:
            tally[status] = 0
        tally[status] += 1
        run_ids.setdefault(key, []).append(str(run.run_id))
        # Most-recent first: capture cache-hit info from the first
        # row we see for each node that carries ``cache_source_run_id``,
        # the audit-row signal that this run reused another run's outputs.
        if key not in cache_hits and run.cache_source_run_id is not None:
            cache_hits[key] = {
                "original_run_id": str(run.cache_source_run_id),
                "cache_key": run.cache_key or "",
            }
        # Record the newest cancellation cause and the newest failure per
        # node.  Iteration is started_at-DESC so the first hit is the most
        # recent.
        if (key not in cancel_causes and status == "cancelled"
                and run.cancelled_due_to_run_id is not None):
            cancel_causes[key] = CancelCause(
                run_id=str(run.cancelled_due_to_run_id),
                node_id=run.cause_node_id,
            )
        if key not in errors and status in ("failed", "cancelled"):
            raw_msg = (run.error_message or "").strip()
            if raw_msg:
                msg = raw_msg
                if len(msg) > _ERROR_MESSAGE_CAP:
                    msg = msg[:_ERROR_MESSAGE_CAP].rstrip() + "…"
                errors[key] = {
                    "message": msg,
                    "run_id": str(run.run_id),
                    "sample": run.sample,
                    "status": status,
                }

    return NodeTallies(
        counts=counts,
        status={node: _aggregate_tally(t) for node, t in counts.items()},
        run_ids=run_ids,
        errors=errors,
        cache_hits=cache_hits,
        push_counts=push_counts,
        cancel_causes=cancel_causes,
    )


@task(
    purpose="Project each canvas method node that has rows onto its state, "
            "looked up by its own node id: status, tally and run ids; the "
            "newest error when failed or mixed; the failed run and its node "
            "behind a cancellation; the cache hit; the push state and counts",
    inputs="The step map (node id -> method name) and the node tallies",
    outputs="Node states for the method nodes that have rows, in step-map order",
)
def project_node_states(
    step_map: Dict[str, str], tallies: NodeTallies,
) -> Dict[str, Dict[str, Any]]:
    """Project the node tallies onto the canvas nodes.

    Each node of the step map is looked up in the tallies by its own id --
    the id its runs recorded -- so two nodes of one method, and a node
    whose run was a cache hit, each get their own state. The step map's
    method value only marks a method node; system nodes (no method) are
    skipped.

    Args:
        step_map: Each canvas node id mapped to its method name, or to an
            empty value for a system node.
        tallies: The per-node tallies.

    Returns:
        The node states of the method nodes that have rows.
    """
    node_states: Dict[str, Dict[str, Any]] = {}
    for node_id, method_name in step_map.items():
        if not method_name:
            continue  # skip system nodes
        status = tallies.status.get(node_id)
        if status:
            tally = tallies.counts[node_id]
            entry: Dict[str, Any] = {
                "status": status,
                "tally": dict(tally),
                # run_ids newest-first so consumers can treat run_ids[0] as
                # "most recent attempt".
                "run_ids": list(tallies.run_ids.get(node_id, [])),
            }
            # Per-node error surface.  Attached for
            # ``failed`` and ``mixed`` states so the Inspector can show the
            # newest failure without the user having to open the log stream.
            err = tallies.errors.get(node_id)
            if err and status in ("failed", "mixed"):
                entry["error"] = err["message"]
                entry["error_run_id"] = err["run_id"]
                if err.get("sample"):
                    entry["error_sample"] = err["sample"]
            # Why a cancelled node was cancelled: the failed run the
            # pipeline-end walk traced it to, and the node that run ran for.
            # Without it the canvas reads the node as stopped by the user.
            cause = tallies.cancel_causes.get(node_id)
            if cause is not None and status == "cancelled":
                entry["cancelled_due_to_run_id"] = cause.run_id
                entry["upstream_run_id"] = cause.run_id
                if cause.node_id is not None:
                    entry["upstream_node_id"] = cause.node_id
            # Cache-hit surface.  Attached from the node's newest Run row
            # that carries ``cache_source_run_id``, regardless of the
            # aggregated ``status`` above — typically ``completed`` for a
            # clean cache hit.  Frontend reads these via
            # ``runStatusToNodeState`` and emits CACHE_HIT into the
            # per-node state machine.
            ch = tallies.cache_hits.get(node_id)
            if ch is not None:
                entry["cache_hit"] = True
                entry["original_run_id"] = ch["original_run_id"]
                if ch.get("cache_key"):
                    entry["cache_key"] = ch["cache_key"]
            # Per-node push aggregates. Always present.
            push_bucket = tallies.push_counts.get(node_id, {})
            entry["push_state"] = _aggregate_push(push_bucket)
            entry["push_pending_count"] = (
                push_bucket.get("pending", 0) + push_bucket.get("in_flight", 0)
            )
            entry["push_failed_count"] = push_bucket.get("failed", 0)
            node_states[node_id] = entry
    return node_states


@task(
    purpose="Give every method node without rows a pending state, then, once "
            "the thread has ended with no error, settle the pending nodes as "
            "completed (their runs were cache hits)",
    inputs="The step map, the projected node states, the thread's liveness, "
           "the job's recorded error",
    outputs="Node states for every method node; push fields always present",
)
def settle_unstarted_nodes(
    step_map: Dict[str, str],
    node_states: Dict[str, Dict[str, Any]],
    thread_alive: bool,
    error: Any,
) -> Dict[str, Dict[str, Any]]:
    """Settle the method nodes that have no rows.

    Args:
        step_map: Each canvas node id mapped to its method name.
        node_states: The projected node states; not modified.
        thread_alive: Whether the pipeline's run thread is still alive.
        error: The job's recorded error, or ``None``.

    Returns:
        The node states with every method node present.
    """
    settled = dict(node_states)
    # Method nodes not yet in the DB haven't started — mark them pending
    # so the overall status doesn't flip to "completed" prematurely.
    for node_id, method_name in step_map.items():
        if method_name and node_id not in settled:
            # Push fields must always be present, even before any
            # Run row exists for this node. Defaults to deferred + zeros.
            settled[node_id] = {
                "status": "pending",
                "push_state": "deferred",
                "push_pending_count": 0,
                "push_failed_count": 0,
            }

    # If thread is dead and we still have pending nodes with no error,
    # they were likely cache hits — mark them completed.
    if not thread_alive and not error:
        for node_id, ns in settled.items():
            if ns["status"] == "pending":
                # Preserve push-state fields when promoting to
                # completed -- they were defaulted on the pending entry.
                settled[node_id] = {
                    "status": "completed",
                    "push_state": ns.get("push_state", "deferred"),
                    "push_pending_count": ns.get("push_pending_count", 0),
                    "push_failed_count": ns.get("push_failed_count", 0),
                }
    return settled


@task(
    purpose="Derive the pipeline's overall status from its node states: running "
            "dominates, then failed, cancelled, completed_with_failures and "
            "completed; an ended thread with an error and a node still pending "
            "is failed, or cancelled when the user asked for a cancel and no "
            "node failed",
    inputs="The settled node states, the thread's liveness, the job's recorded "
           "error, whether the user asked for a cancel",
    outputs="The overall status",
)
def derive_overall_status(
    node_states: Dict[str, Dict[str, Any]], thread_alive: bool, error: Any,
    cancel_requested: bool = False,
) -> str:
    """Derive the overall status of a pipeline.

    Args:
        node_states: The settled node states.
        thread_alive: Whether the pipeline's run thread is still alive.
        error: The job's recorded error, or ``None``.
        cancel_requested: Whether the user asked for the pipeline to be
            cancelled.

    Returns:
        ``pending``, ``running``, ``failed``, ``cancelled``,
        ``completed_with_failures`` or ``completed``.
    """
    # "mixed" is a terminal state for a node — its per-sample runs finished
    # but with partial failure. At the pipeline level that maps to
    # `completed_with_failures` when nothing is still running; during
    # execution it stays `running`.
    statuses = [ns["status"] for ns in node_states.values()] if node_states else []
    if not statuses:
        overall = "pending"
    elif any(s == "running" for s in statuses):
        overall = "running"
    elif any(s == "failed" for s in statuses):
        # Any fully-failed node fails the pipeline.
        # Failure dominates cancellation — if anything genuinely errored
        # before the cancel landed, surface that.
        overall = "failed"
    elif any(s == "cancelled" for s in statuses):
        # Nothing running, nothing failed, at least one cancelled node.
        # The polling actor reads this as a terminal status and stops
        # the loop; without this branch the chain falls through to the
        # "running" fallback below.
        overall = "cancelled"
    elif any(s == "mixed" for s in statuses):
        # Nothing running, nothing fully failed, but some node saw partial
        # failures across its fan-out samples. Keep-going enabled this.
        overall = "completed_with_failures"
    elif all(s == "completed" for s in statuses):
        overall = "completed"
    else:
        overall = "running"

    # A dead thread with an error is terminal. Any node still pending will
    # never start, so the pipeline failed -- whether nothing ran at all or
    # some nodes finished before the failure. Without this a pipeline whose
    # step was refused before writing a row reports running forever. A user
    # cancel also ends the thread with an error and leaves the unstarted
    # nodes pending; that pipeline was cancelled, unless a node had already
    # failed, since a real failure dominates a cancellation.
    if not thread_alive and error and (
        overall == "pending" or any(s == "pending" for s in statuses)
    ):
        overall = "failed"
        if cancel_requested and not any(s == "failed" for s in statuses):
            overall = "cancelled"
    return overall


@task(
    purpose="Read the pipeline's captured stdout, then its stderr under a "
            "separator, from the log directory",
    inputs="The pipeline's log directory, or None",
    outputs="The log text; empty when there is no directory or no file",
)
def read_pipeline_log(log_dir: Optional[str]) -> str:
    """Read a pipeline's captured log files.

    Args:
        log_dir: The pipeline's log directory, or ``None``.

    Returns:
        The stdout log, followed by the stderr log under a separator.
    """
    log_content = ""
    if log_dir:
        log_path = Path(log_dir)
        stdout_log = log_path / "stdout.log"
        stderr_log = log_path / "stderr.log"
        if stdout_log.exists():
            log_content += stdout_log.read_text(encoding="utf-8", errors="replace")
        if stderr_log.exists():
            stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
            if stderr_text:
                log_content += "\n--- STDERR ---\n" + stderr_text
    return log_content


@workflow(
    purpose="Aggregate a submitted pipeline's status: read its run and output "
            "rows, tally each node, project the node states, settle the "
            "unstarted nodes, derive the overall status, and read the log",
    inputs="The pipeline id, its step map, its thread's liveness, its recorded "
           "error, its log directory, whether the user asked for a cancel",
    outputs="PipelineStatus: overall status, node states, log",
)
def aggregate_pipeline_status(
    pipeline_id: str,
    step_map: Dict[str, str],
    thread_alive: bool,
    error: Any,
    log_dir: Optional[str],
    cancel_requested: bool = False,
) -> PipelineStatus:
    """Aggregate the status of a submitted pipeline.

    Args:
        pipeline_id: The pipeline id (the job id).
        step_map: Each canvas node id mapped to its method name.
        thread_alive: Whether the pipeline's run thread is still alive.
        error: The job's recorded error, or ``None``.
        log_dir: The pipeline's log directory, or ``None``.
        cancel_requested: Whether the user asked for the pipeline to be
            cancelled.

    Returns:
        The pipeline's aggregated status.
    """
    口 = AutoStep(step_num=1, name="Read the pipeline's run and output rows")
    runs, outputs = read_pipeline_rows(pipeline_id)

    口 = AutoStep(step_num=2, name="Tally each node")
    tallies = tally_nodes(runs, outputs)

    口 = AutoStep(step_num=3, name="Project the node states")
    node_states = project_node_states(step_map, tallies)

    口 = AutoStep(step_num=4, name="Settle the unstarted nodes")
    node_states = settle_unstarted_nodes(step_map, node_states, thread_alive, error)

    口 = AutoStep(step_num=5, name="Derive the overall status")
    overall = derive_overall_status(node_states, thread_alive, error,
                                    cancel_requested)

    口 = AutoStep(step_num=6, name="Read the log")
    log = read_pipeline_log(log_dir)

    return PipelineStatus(
        overall_status=overall,
        node_states=node_states,
        log=log,
    )
