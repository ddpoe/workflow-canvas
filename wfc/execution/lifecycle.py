"""Pipeline lifecycle: everything around the engine steps of ``run_pipeline``.

Launch bookkeeping (the legacy-workspace sweep, the pipeline id, the log
directories, freezing the substituted and editable documents), the
background push worker as a handle, the engine-failure sequence, the
success sequence (the cancelled-row walk, the archive pass, the drain),
user cancellation, and the outcome summary counted from the run rows
(``pipeline_summary``).

Every entry point takes plain values -- a project root, a pipeline id, a
document, callbacks -- and touches no canvas state, so the lifecycle is
callable headless: from ``run_pipeline``, from the ``wfc`` verbs, from the
canvas server, or from an in-process caller.
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import col, select

from .. import layout
from ..contracts import COLLAPSED_SAMPLE
from ..orchestration import EngineOutcome
from ..persistence import (
    CANCEL_REASON_ENGINE_FAILED,
    CANCEL_REASON_NOT_RUN,
    CANCEL_REASON_UPSTREAM_FAILED,
    CANCEL_REASON_USER,
    ENGINE_SIDE_CANCEL_REASONS,
    Method,
    Module,
    Run,
    get_session,
    write_transaction,
)
from ..persistence import project_root as project_root_of_run
from ..storage import end_pipeline_samples


def sweep_legacy_workspace(project_root: Path) -> bool:
    """Delete the legacy ``.wfc/runs/workspace/`` tree if it is present.

    Nothing writes to the workspace tree (outputs live in the run-archive
    dirs and the DVC cache), so a tree left on disk is swept. The one sweep both
    ``run_pipeline``'s launch and the ``cleanup-workspace`` verb use; each
    caller keeps its own messaging.

    Args:
        project_root: The wfc project root.

    Returns:
        True when a workspace tree existed and was removed, False when there
        was nothing to sweep.

    Raises:
        OSError: If the tree exists and cannot be removed.
    """
    workspace = layout.workspace_dir(Path(project_root))
    if not workspace.exists():
        return False
    shutil.rmtree(workspace)
    return True


@dataclass
class PipelineLaunch:
    """What launch bookkeeping fixed for one pipeline execution."""

    pipeline_id: str
    log_dir: Path
    frozen_doc: Path
    #: The canonical-JSON hash of the frozen document, kept in the cache.
    pipeline_doc_hash: str | None = None


@task(purpose="Launch bookkeeping: sweep the legacy workspace, fix the pipeline "
              "id, create the log directories, freeze the substituted and "
              "editable documents into the pipeline dir, and keep the frozen "
              "document, as it is on disk, in the cache by its canonical-JSON "
              "hash before the engine starts",
      inputs="project root, the caller's document path, the pre-substitution "
             "and substituted documents, an optional caller-provided pipeline id",
      outputs="PipelineLaunch: pipeline id, log dir, frozen document path, the "
              "frozen document's hash",
      critical="the hash is of the frozen file the claims read (the canvas's "
               "own when it froze it), never of a re-prepared dict")
def prepare_launch(
    project_root: Path,
    pipeline_path: Path,
    document: dict,
    substituted: dict,
    pipeline_id: str | None = None,
) -> PipelineLaunch:
    """Fix the pipeline id and freeze the documents before the engine runs.

    Args:
        project_root: The wfc project root.
        pipeline_path: The caller's document path (when it already is the
            frozen file, nothing is rewritten).
        document: The pre-substitution document (frozen as
            ``pipeline.editable.json``).
        substituted: The literal document (frozen as ``pipeline.json``).
        pipeline_id: Caller-provided id; a fresh UUID when omitted.

    Returns:
        The launch's pipeline id, log dir, frozen document path and the
        frozen document's canonical-JSON hash.

    Raises:
        ValueError: The frozen document holds NaN or an infinity.
    """
    import logging as _logging

    project_root = Path(project_root)

    # The cache is authoritative and nothing writes to ``.wfc/runs/workspace/``.
    # Sweep a legacy tree once at pipeline start (idempotent; logs a line
    # when a tree was removed or could not be).
    _ws_logger = _logging.getLogger(__name__)
    _legacy_workspace = layout.workspace_dir(project_root)
    try:
        if sweep_legacy_workspace(project_root):
            _ws_logger.info("removed legacy workspace at %s", _legacy_workspace)
    except Exception as _ws_exc:
        _ws_logger.warning(
            "could not remove legacy workspace %s: %s",
            _legacy_workspace, _ws_exc,
        )

    # Generate pipeline ID and create log directories before Snakemake
    pipeline_id = pipeline_id if pipeline_id is not None else str(uuid.uuid4())
    log_dir = layout.pipeline_run_dir(project_root, pipeline_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    layout.pipeline_run_logs_dir(project_root, pipeline_id).mkdir(exist_ok=True)

    # Freeze the executed pipeline doc into the pipeline dir. The
    # pipeline-end cancelled-rows walk, the canvas history readers and
    # `wfc run-step` (through WFC_PIPELINE_JSON) resolve
    # .wfc/runs/pipelines/<pid>/pipeline.json and expect literals, so the
    # SUBSTITUTED form is what freezes there; the pre-substitution form
    # goes beside it as pipeline.editable.json, as the canvas submission
    # path writes it. The canvas server pre-writes both and passes the
    # frozen file as pipeline_path, so only write when the caller's doc is
    # not already the frozen file.
    frozen_doc = layout.pipeline_doc_path(project_root, pipeline_id)
    try:
        already_frozen = frozen_doc.exists() and frozen_doc.samefile(Path(pipeline_path))
    except OSError:
        already_frozen = False
    if not already_frozen:
        frozen_doc.write_text(json.dumps(substituted, indent=2), encoding="utf-8")
        layout.pipeline_editable_doc_path(project_root, pipeline_id).write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )
    # Keep the document every claim of this launch reads: the frozen file as
    # it is on disk (in the canvas path the canvas wrote it, and the dict
    # re-prepared here need not equal it), before any row can name it.
    from .claim import store_pipeline_document

    frozen = json.loads(frozen_doc.read_text(encoding="utf-8"))
    pipeline_doc_hash = store_pipeline_document(frozen, project_root)
    return PipelineLaunch(pipeline_id=pipeline_id, log_dir=log_dir,
                          frozen_doc=frozen_doc, pipeline_doc_hash=pipeline_doc_hash)


#: The name of the push worker's thread.
PUSH_WORKER_THREAD_NAME = "wfc-push-worker"


@dataclass
class PushWorker:
    """Handle for the background DVC push worker.

    ``thread`` is ``None`` when no remote is configured (rows stay
    ``deferred`` and there is nothing to drain).
    """

    thread: threading.Thread | None
    stop: threading.Event


@task(purpose="Start the background push worker when a remote is configured: "
              "recover orphaned pushes, then run the worker loop on a daemon thread",
      inputs="project root",
      outputs="PushWorker handle (thread is None without a remote)")
def start_push_worker(project_root: Path) -> PushWorker:
    """Start the push worker and return the handle ``drain_push_worker`` consumes.

    Args:
        project_root: The wfc project root.

    Returns:
        The worker handle.
    """
    from ..storage.push_worker import _push_worker_loop, _reset_orphan_pushes

    project_root = Path(project_root)
    # Orphan recovery + push worker.  Only spin up when a
    # remote is configured -- otherwise rows stay in `deferred` and the
    # worker has nothing to do.
    stop = threading.Event()
    thread: threading.Thread | None = None
    try:
        from ..storage import has_remote_configured as _has_remote
        push_enabled = _has_remote(project_root)
    except Exception:
        push_enabled = False
    if push_enabled:
        try:
            _reset_orphan_pushes(project_root)
        except Exception as _orph_exc:
            print(f"[run_pipeline] orphan recovery skipped: {_orph_exc}", file=sys.stderr)
        thread = threading.Thread(
            target=_push_worker_loop,
            args=(project_root, stop),
            daemon=True,
            name=PUSH_WORKER_THREAD_NAME,
        )
        thread.start()
    return PushWorker(thread=thread, stop=stop)


def drain_push_worker(worker: PushWorker, *, cancel: bool = False) -> None:
    """Signal worker stop; wait for it unless cancelling.

    Args:
        worker: The handle ``start_push_worker`` returned.
        cancel: When True the join is skipped (a cancelling caller does not
            wait on the push tail).
    """
    if worker.thread is None:
        return
    worker.stop.set()
    if not cancel:
        # NOTE: 600s join timeout bounds finalize-drain latency. A
        # 50GB push tail at ~100MB/s takes ~8 minutes; a shorter
        # timeout would leave rows in `in_flight` when the pipeline
        # returns. 600s covers realistic dataset sizes
        # at typical bandwidth; longer tails are abandoned and re-
        # entered by _reset_orphan_pushes on the next pipeline run.
        worker.thread.join(timeout=600)


#: How long a pipeline's end waits for a stopping push worker's last tick, in
#: seconds. The worker ticks once more after it is told to stop; this bounds
#: that tick, not the push tail.
PUSH_WORKER_STOP_SECONDS = 5


def stop_push_worker(worker: PushWorker, *, cancel: bool = False) -> None:
    """Make sure a pipeline's push worker is not running when the pipeline ends.

    The endings drain the worker; an engine that raises, or an archive pass
    that raises, reaches no drain, and a worker left running ticks on the
    process engine forever. A worker an ending did not stop is drained here
    (``cancel`` as ``drain_push_worker`` takes it); then, drained by an
    ending or here, the thread gets up to :data:`PUSH_WORKER_STOP_SECONDS`
    to finish its last tick, a cancel's included, whose drain does not wait.
    A last tick still pushing after that wait is left to finish on its own:
    the thread is told to stop, so it ends when that tick does and never
    ticks again.

    Args:
        worker: The handle ``start_push_worker`` returned.
        cancel: When True a worker no ending stopped is not waited on for
            its push tail (an interrupt or a cancel).
    """
    if worker.thread is None or not worker.thread.is_alive():
        return
    if not worker.stop.is_set():
        drain_push_worker(worker, cancel=cancel)
    worker.thread.join(timeout=PUSH_WORKER_STOP_SECONDS)


@task(purpose="Engine-failure sequence: flip in-flight rows to failed unless the "
              "caller cancelled, write the cancelled rows -- a target with no "
              "failed ancestor reads engine_failed, carrying the engine's "
              "message, when the user did not cancel and no step failed, and "
              "not_run otherwise -- remove the samples the pipeline restored, "
              "drain the push worker, and raise the engine's failure message",
      inputs="pipeline id, project root, the engine's outcome, the push-worker "
             "handle, the cancellation probe",
      outputs="raises RuntimeError -- never returns")
def finish_failed(
    pipeline_id: str,
    project_root: str,
    outcome: EngineOutcome,
    worker: PushWorker,
    is_cancelled: Callable[[], bool] | None = None,
    pipeline_doc_hash: str | None = None,
) -> None:
    """Close a failed engine run and raise the outcome's message.

    When the user did not cancel and, once the in-flight rows are flipped
    to failed, no step failed (no failed row, and no claim refusal the walk
    records as one), the engine itself failed: the walk gives every target
    the ``engine_failed`` reason and the outcome's message. Otherwise a
    target with no failed ancestor reads ``not_run``.

    Args:
        pipeline_id: The pipeline that failed.
        project_root: The wfc project root.
        outcome: The engine's outcome (a non-zero exit). Its ``message`` is
            raised as it is; this sequence builds no text.
        worker: The push-worker handle to drain.
        is_cancelled: Optional probe; when it answers True the cancel
            handler owns row state and ``fail_pipeline`` is skipped.
        pipeline_doc_hash: The launch's document hash, stamped on the rows
            the pipeline-end walk writes.

    Raises:
        RuntimeError: Always -- the pipeline failure, ``outcome.message``.
    """
    # If the caller signalled cancellation (e.g. canvas cancel
    # endpoint), the cancel handler owns row state -- skip
    # fail_pipeline so it doesn't overwrite ``cancelled`` rows.
    was_cancelled = bool(is_cancelled and is_cancelled())
    # Flip in-flight rows to 'failed' BEFORE the walk so BFS can
    # find them as failed ancestors. Best-effort -- the walk is
    # still useful even if fail_pipeline hit an issue.
    if not was_cancelled:
        try:
            fail_pipeline(pipeline_id)
        except Exception as _fp_exc:
            print(
                f"[run_pipeline] fail_pipeline raised: {_fp_exc}",
                file=sys.stderr,
            )
    try:
        engine_failed = not was_cancelled and not _pipeline_has_failure(
            pipeline_id, project_root)
        _write_cancelled_rows(
            pipeline_id, project_root, pipeline_doc_hash,
            user_cancelled=was_cancelled,
            no_cause_reason=(CANCEL_REASON_ENGINE_FAILED if engine_failed
                             else CANCEL_REASON_NOT_RUN),
            no_cause_message=outcome.message if engine_failed else None)
    except Exception as _walk_exc:
        print(
            f"[run_pipeline] _write_cancelled_rows raised: {_walk_exc}",
            file=sys.stderr,
        )
    # -- Remove the samples the pipeline restored (failure and cancel) --
    # Best-effort, like the steps above: a failure is printed and the
    # pipeline's own failure is still the one raised.
    try:
        口 = AutoStep(step_num=1, name="Remove the samples the pipeline restored")
        end_pipeline_samples(project_root, pipeline_id)
    except Exception as _end_exc:
        print(
            f"[run_pipeline] removing the pipeline's restored samples raised: "
            f"{_end_exc}",
            file=sys.stderr,
        )
    cancelled = bool(is_cancelled and is_cancelled())
    drain_push_worker(worker, cancel=cancelled)
    raise RuntimeError(outcome.message)


@task(purpose="Success sequence: the pipeline-end cancelled-row walk, the deferred "
              "archive pass, the removal of the samples the pipeline restored, "
              "and the push-worker drain",
      inputs="pipeline id, project root, the push-worker handle, the cancellation "
             "probe, the archive switch and progress callback",
      outputs="quiescent state: cancelled rows written, outputs archived, worker drained")
def finish_succeeded(
    pipeline_id: str,
    project_root: str,
    worker: PushWorker,
    is_cancelled: Callable[[], bool] | None = None,
    *,
    archive: bool = True,
    archive_progress_fn: Callable[[Any, str, str], None] | None = None,
    pipeline_doc_hash: str | None = None,
) -> None:
    """Close a successful engine run.

    Args:
        pipeline_id: The pipeline that completed.
        project_root: The wfc project root.
        worker: The push-worker handle to drain.
        is_cancelled: Optional probe; a cancelling caller skips the drain's join.
        archive: When True run the deferred archive pass.
        archive_progress_fn: Optional per-file progress callback forwarded to
            ``archive_outputs``.
        pipeline_doc_hash: The launch's document hash, stamped on the rows
            the pipeline-end walk writes.
    """
    # -- Pipeline-end cancelled-row walk (success path) --
    # Always on: on a fully-successful pipeline this is an O(nodes)
    # no-op. When Snakemake's --keep-going skipped some targets, it fills
    # in cancelled rows for the un-run triples before the archive pass.
    try:
        _write_cancelled_rows(pipeline_id, project_root, pipeline_doc_hash,
                              user_cancelled=bool(is_cancelled and is_cancelled()))
    except Exception as _walk_exc:
        print(
            f"[run_pipeline] _write_cancelled_rows raised on success path: "
            f"{_walk_exc}",
            file=sys.stderr,
        )

    # -- Deferred archive pass --
    if archive:
        from ..storage import archive_outputs as _archive_outputs

        def _progress(run_id, name: str, status: str) -> None:
            if status != "hashing":
                print(f"  {name}: {status}")
            if archive_progress_fn is not None:
                archive_progress_fn(run_id, name, status)

        print("Archiving pipeline outputs...")
        _results = _archive_outputs(project_root, progress_fn=_progress)
        archived = sum(1 for r in _results if r["status"] == "archived")
        if archived:
            print(f"Archived {archived} output(s).")
        else:
            print("No outputs to archive.")

    # -- Remove the samples the pipeline restored, after the archive pass --
    try:
        口 = AutoStep(step_num=1, name="Remove the samples the pipeline restored")
        end_pipeline_samples(project_root, pipeline_id)
    except Exception as _end_exc:
        print(
            f"[run_pipeline] removing the pipeline's restored samples raised: "
            f"{_end_exc}",
            file=sys.stderr,
        )

    # Drain push worker before returning so the caller sees a
    # quiescent state (all rows reached pushed/failed terminals).
    drain_push_worker(worker, cancel=bool(is_cancelled and is_cancelled()))


def finalize_pipeline(pipeline_id: str) -> None:
    """Log successful pipeline completion.

    Run outputs already live in the run-archive dirs / DVC cache and the
    per-target sentinels are in place, so there is nothing to publish or
    clean up here — this only logs.
    """
    print(f"Pipeline {pipeline_id} completed successfully")


def cancel_pipeline(pipeline_id: str, project_root: str | None = None) -> int:
    """Mark any in-flight runs for this pipeline as 'cancelled', and remove its restored samples.

    Sibling of :func:`fail_pipeline`.  User-initiated cancel: each flipped
    row records the ``user`` reason; the targets that never started get
    their rows, with the same reason, from the pipeline-end walk the run's
    failure ending makes (:func:`_write_cancelled_rows`).  Idempotent —
    repeated calls on a pipeline whose runs are already terminal are a no-op.

    Args:
        pipeline_id: Pipeline whose in-flight rows should be flipped.
        project_root: The wfc project root, whose restored samples the
            pipeline held are removed (the active project when None).

    Returns:
        Number of rows flipped.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")
        )
        in_flight = session.exec(stmt).all()
        for run in in_flight:
            run.status = "cancelled"
            run.cancel_reason = CANCEL_REASON_USER
            run.finished_at = datetime.now(UTC)
            if run.error_message is None:
                run.error_message = "Cancelled by user"
        session.commit()
        n = len(in_flight)
        if n > 0:
            print(f"Marked {n} in-flight run(s) as cancelled for pipeline {pipeline_id}")
    # The samples the pipeline restored go with it, as on success and
    # failure; best-effort, a failure is printed.
    try:
        end_pipeline_samples(project_root or str(project_root_of_run()), pipeline_id)
    except Exception as _end_exc:
        print(f"[cancel_pipeline] removing the pipeline's restored samples "
              f"raised: {_end_exc}", file=sys.stderr)
    return n


#: The traceback a run failed by :func:`fail_pipeline` gets when it has none.
_IN_FLIGHT_TRACEBACK = ("No traceback available (run was still in-flight when "
                        "pipeline failed)")


@task(purpose="Fail a pipeline's in-flight runs: every running row becomes "
              "failed, keeping an error message or traceback it already has, "
              "else the caller's message and traceback text",
      inputs="pipeline id; the message and the traceback text a row without "
             "one gets",
      outputs="the ids of the rows failed",
      critical="a compare-and-set on status running, so a row that ended "
               "meanwhile is left as it ended")
def fail_pipeline(pipeline_id: str, *, message: str | None = None,
                  traceback_text: str | None = _IN_FLIGHT_TRACEBACK) -> list[int]:
    """Mark any in-flight runs for this pipeline as 'failed'.

    Already-completed runs are left untouched (their data is safe in
    .wfc/runs/{id}/). A run's existing error message and traceback are kept:
    the generated rule's handler may have recorded them before this runs.

    Args:
        pipeline_id: The pipeline.
        message: The error message a failed run without one gets. Defaults
            to "Pipeline <id> failed (orphaned run)"; a close-out passes
            "Interrupted: the server stopped".
        traceback_text: The traceback text a failed run without one gets, or
            None to leave it without one. Defaults to the in-flight note; a
            close-out passes None, since an interrupted step raised nothing.

    Returns:
        The ids of the rows this call failed.
    """
    from sqlalchemy import func, update

    text = message if message is not None else f"Pipeline {pipeline_id} failed (orphaned run)"
    values: dict[str, Any] = {
        "status": "failed",
        "finished_at": datetime.now(UTC),
        "error_message": func.coalesce(col(Run.error_message), text),
    }
    if traceback_text is not None:
        values["error_traceback"] = func.coalesce(col(Run.error_traceback), traceback_text)
    with write_transaction() as session:
        ids = [int(i) for i in session.exec(
            select(Run.id).where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")).all()]
        if ids:
            session.execute(
                update(Run)
                .where(col(Run.id).in_(ids))
                .where(col(Run.status) == "running")
                .values(**values)
            )
    if ids:
        print(f"Marked {len(ids)} in-flight run(s) as failed for pipeline {pipeline_id}")
    else:
        print(f"No in-flight runs to mark for pipeline {pipeline_id}")
    print("Workspace preserved for debugging")
    return ids


def _target_key(node_id: str, sample: str,
                params: dict | None) -> tuple[str, str, str]:
    """A target's key: its node's raw document id, its sample and its params.

    The node id tells apart two nodes sharing a method; the params
    fingerprint (canonical JSON) keeps one node's variants apart. A run row
    is keyed the same way from its ``node_id``, ``sample`` and ``params``.
    """
    return (node_id, sample, json.dumps(params or {}, sort_keys=True, default=str))


@dataclass(frozen=True)
class ExpectedTarget:
    """One (node, sample, variant) target a pipeline's frozen document expects.

    Attributes:
        step: The target's ``StepDef``.
        node_id: The node's raw document id, the id its run row records.
        sample: The sample axis value (``COLLAPSED_SAMPLE`` for a collapsed step).
        variants: Every variant name the target is scheduled under, in axis
            order. A node with no variant of a name runs its base params
            under it, so several names can share one target.
        params: The params the target runs with.
    """

    step: Any
    node_id: str
    sample: str
    variants: tuple[str, ...]
    params: dict | None

    @property
    def key(self) -> tuple[str, str, str]:
        """The target's key, as a run row for it is keyed."""
        return _target_key(self.node_id, self.sample, self.params)


@dataclass
class ExpectedTargets:
    """A pipeline's expected targets and the model they were expanded from.

    Attributes:
        steps: The loaded pipeline's steps, in document order.
        resolved_params: The variant tables by step node id.
        raw_id_by_step: Each ``StepDef.node_id`` mapped to the node's raw
            document id.
        targets: Every expected target, once per key, step by step in
            document order.
    """

    steps: list
    resolved_params: dict[str, dict[str, dict]]
    raw_id_by_step: dict[str, str]
    targets: list[ExpectedTarget]

    @property
    def node_order(self) -> list[str]:
        """The pipeline's method nodes by raw document id, in document order."""
        return [self.raw_id_by_step[s.node_id] for s in self.steps]


def expected_targets(pipeline_id: str, project_root: str) -> ExpectedTargets | None:
    """Enumerate every target a pipeline's frozen document expects.

    The one enumeration of a pipeline's expected targets: the pipeline-end
    walk reconciles against it, and the pipeline's expected run count is its
    length, so the two can never differ. Each target is a (node, sample,
    variant) triple from Graph's ``expand_step_combos`` over the one variant
    model (``resolve_variant_model``), the model the Snakefile was scheduled
    from; a collapsed step's sample axis is ``COLLAPSED_SAMPLE``, a per-sample
    step's the sample list, decided step by step. A target is keyed by its
    node's raw document id (``Run.node_id``): ``StepDef.node_id`` is resolved
    to it through Graph's ``document_node`` (for a legacy numeric-id document
    the step's id is its method name while the raw id is the number).

    Args:
        pipeline_id: The pipeline.
        project_root: The wfc project root, which holds the frozen document.

    Returns:
        The expected targets, or None when the pipeline has no frozen
        document or it cannot be loaded.
    """
    from ..graph import document_node, expand_step_combos, resolve_variant_model
    from .composer import load_pipeline_from_path

    pipeline_json = layout.pipeline_doc_path(Path(project_root), pipeline_id)
    if not pipeline_json.exists():
        return None
    try:
        pipeline = load_pipeline_from_path(pipeline_json)
        raw_document = json.loads(pipeline_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"[expected-targets] Failed to load pipeline.json for {pipeline_id}: {exc}",
            file=sys.stderr,
        )
        return None

    steps = list(pipeline.steps)
    raw_id_by_step: dict[str, str] = {}
    for s in steps:
        doc_node = document_node(raw_document, s.node_id)
        raw_id_by_step[s.node_id] = (str(doc_node["id"]) if doc_node is not None
                                     else s.node_id)
    resolved_params = resolve_variant_model(pipeline).tables

    # One target per key, in first-seen order; each later variant name that
    # lands on the same key is added to that target's names.
    by_key: dict[tuple[str, str, str], ExpectedTarget] = {}
    for step, combo in expand_step_combos(
        steps=steps,
        samples=pipeline.samples,
        resolved_params=resolved_params,
        explicit_combos=pipeline.explicit_combos,
    ):
        variant = combo["variant"]
        target = ExpectedTarget(
            step=step,
            node_id=raw_id_by_step[step.node_id],
            sample=combo["sample"],
            variants=(variant,),
            params=resolved_params.get(step.node_id, {}).get(variant, step.params),
        )
        prior = by_key.get(target.key)
        if prior is None:
            by_key[target.key] = target
        else:
            by_key[target.key] = replace(prior, variants=(*prior.variants, variant))
    return ExpectedTargets(steps=steps, resolved_params=resolved_params,
                           raw_id_by_step=raw_id_by_step,
                           targets=list(by_key.values()))


#: The pipeline statuses ``pipeline_status`` gives.
PIPELINE_RUNNING = "running"
PIPELINE_SUCCEEDED = "succeeded"
PIPELINE_FAILED = "failed"
PIPELINE_CANCELLED = "cancelled"
PIPELINE_MIXED = "mixed"


def pipeline_status(runs: Iterable[tuple[str, str | None]],
                    pending: int = 0) -> str:
    """A pipeline's status, from its runs and its pending targets.

    The one rule a pipeline's status comes from:

    - ``running`` -- any run is running (or in any status that has not
      ended), or any expected target is still pending;
    - ``succeeded`` -- every run completed, ran or cached (nothing failed
      or was cancelled);
    - ``failed`` -- nothing completed, and one or more runs failed or were
      cancelled for an engine-side reason (``interrupted``,
      ``engine_failed``: wfc could not run them);
    - ``cancelled`` -- every run was cancelled, none for an engine-side
      reason;
    - ``mixed`` -- anything else: some completed, and some failed or were
      cancelled.

    Args:
        runs: Each of the pipeline's run rows as ``(status, cancel_reason)``,
            the status as stored (``running``, ``completed``, ``failed``,
            ``cancelled``) and the reason ``None`` on a row that is not
            cancelled or recorded none.
        pending: How many expected targets have no row yet.

    Returns:
        One of ``running``, ``succeeded``, ``failed``, ``cancelled``, ``mixed``.
    """
    completed = failed = cancelled = engine_side = 0
    unended = False
    for status, reason in runs:
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        elif status == "cancelled":
            cancelled += 1
            if reason in ENGINE_SIDE_CANCEL_REASONS:
                engine_side += 1
        else:
            unended = True
    if unended or pending > 0:
        return PIPELINE_RUNNING
    if failed == 0 and cancelled == 0:
        return PIPELINE_SUCCEEDED
    if completed == 0:
        if failed > 0 or engine_side > 0:
            return PIPELINE_FAILED
        return PIPELINE_CANCELLED
    return PIPELINE_MIXED


def _pipeline_has_failure(pipeline_id: str, project_root: str) -> bool:
    """Whether a step of the pipeline failed: a failed row, or a claim refusal.

    A claim refusal leaves only its outcome sidecar until the pipeline-end
    walk records it as a failed row, so it counts as a failure here too.

    Args:
        pipeline_id: The pipeline to look at.
        project_root: The wfc project root (locates the frozen document and
            the outcome sidecars).

    Returns:
        True when the pipeline has a failed row or an expected target the
        claim refused.
    """
    with get_session() as session:
        failed = session.exec(
            select(Run.id).where(Run.pipeline_id == pipeline_id,
                                 Run.status == "failed")
        ).first()
    if failed is not None:
        return True
    expected = expected_targets(pipeline_id, project_root)
    if expected is None:
        return False
    from .record import read_claim_refusal

    outcomes_dir = layout.pipeline_outcomes_dir(Path(project_root), pipeline_id)
    return any(
        read_claim_refusal(outcomes_dir, target.step.node_id, target.sample,
                           variant) is not None
        for target in expected.targets for variant in target.variants)


@workflow(purpose="Pipeline-end cancelled-row walk: expand the frozen document's "
                  "targets, compare them against the run rows, record each "
                  "claim-refused target as a failed row, find each missing "
                  "target's nearest failed ancestor by BFS, and write one "
                  "cancelled row per missing target with its reason",
          inputs="pipeline id, project root (locates the frozen pipeline.json), "
                 "the launch's document hash, whether the user cancelled, the "
                 "reason and message for a target with no failed ancestor",
          outputs="number of cancelled rows written")
def _write_cancelled_rows(pipeline_id: str, project_root: str,
                          pipeline_doc_hash: str | None = None, *,
                          user_cancelled: bool = False,
                          no_cause_reason: str = CANCEL_REASON_NOT_RUN,
                          no_cause_message: str | None = None) -> int:
    """Persist first-class 'cancelled' Run rows for targets that did not run.

    Called at pipeline-end (both success and failure paths) from
    ``run_pipeline``. A target the claim refused has no row but left a
    claim-refusal outcome sidecar; it first gets a ``failed`` row carrying
    the refusal's message, so it is a failed ancestor like any other. Every
    other expected target (``expected_targets``) with no Run row of that
    ``node_id``, sample and params gets one ``cancelled`` row, finished now.
    The walk looks upstream through the frozen DAG (``StepDef.depends_on``)
    for the nearest run with ``status='failed'``: when there is one the row
    links it in ``cancelled_due_to_run_id``. The row's ``cancel_reason`` is
    ``user`` after a user cancel, else ``upstream_failed`` when a failed
    ancestor was found and the caller's ``no_cause_reason`` when none was:
    ``not_run`` by default, ``engine_failed`` when the engine failed with no
    step failing, ``interrupted`` when a restart cut the pipeline off. Such a
    row carries ``no_cause_message`` as its error message. The one target left
    without a row is one whose method was unregistered after the pipeline
    was scheduled: there is no method row to write.

    Idempotent: targets that already have any Run row (any status) are
    skipped. Re-invocation writes no duplicates.

    Always on: on a fully-successful pipeline this finds zero
    missing targets and writes zero rows -- the cost of the walk is
    O(steps * samples * variants) DB reads.

    Tiebreak when multiple failed ancestors are equidistant: the
    lowest ``run.id``.

    Args:
        pipeline_id: The pipeline execution ID to reconcile.
        project_root: Absolute path to the wfc project root (used to
            locate ``.wfc/runs/pipelines/<pid>/pipeline.json``).
        pipeline_doc_hash: The launch's document hash (``PipelineLaunch``),
            stamped on every failed and cancelled row written here. The
            per-run values (commit, env name, wfc version) stay NULL: no
            claim ran for these rows.
        user_cancelled: Whether the user cancelled the pipeline; every row
            written then carries the ``user`` reason.
        no_cause_reason: The reason a missing target with no failed
            ancestor gets when the user did not cancel.
        no_cause_message: The error message such a row carries, or ``None``.

    Returns:
        Number of cancelled rows written (useful for tests / logging).
    """
    口 = Step(step_num=1, name="Expand the frozen document's targets",
             purpose="Enumerate every (node, sample, variant) target the "
                     "frozen document expects, through the one enumeration "
                     "the expected run count also reads")
    expected = expected_targets(pipeline_id, project_root)
    if expected is None or not expected.steps:
        # Nothing to reconcile -- no frozen pipeline doc means the run
        # never reached the snake-gen stage (or was cleaned up).
        return 0

    steps = expected.steps
    raw_id_by_step = expected.raw_id_by_step
    resolved_params = expected.resolved_params

    # DAG adjacency keyed by StepDef.node_id (upstream parents for BFS).
    step_by_nid: dict[str, Any] = {s.node_id: s for s in steps}

    口 = Step(step_num=2, name="Compare against the run rows",
             purpose="Load the pipeline's Run rows; index the present targets "
                     "and the failed rows by (raw node id, sample, params "
                     "fingerprint), lowest run id first")
    with get_session() as session:
        existing_runs = session.exec(
            select(Run).where(Run.pipeline_id == pipeline_id)
        ).all()

        def _step_key(step, sample: str, params) -> tuple[str, str, str]:
            return _target_key(raw_id_by_step[step.node_id], sample, params)

        # Targets that already have a Run row (any status). The walk uses
        # this set for its presence check (idempotency + cache-hit
        # safety), so cancelled rows count too.
        actual_keys: set[tuple[str, str, str]] = {
            _target_key(r.node_id, r.sample or "", r.params)
            for r in existing_runs if r.node_id
        }

        # Failed-run lookup keyed by target -> lowest run.id (the tiebreak).
        failed_by_key: dict[tuple[str, str, str], int] = {}
        for r in existing_runs:
            if r.status != "failed" or not r.node_id:
                continue
            key = _target_key(r.node_id, r.sample or "", r.params)
            prior = failed_by_key.get(key)
            if prior is None or (r.id is not None and r.id < prior):
                failed_by_key[key] = r.id

        def _method_row(step) -> Method | None:
            """The registered method a step runs, warning once when it is gone."""
            method_key = (step.method_name, step.module_name)
            mod = session.exec(
                select(Module).where(Module.name == step.module_name)
            ).first()
            method_row = None if mod is None else session.exec(
                select(Method).where(
                    Method.name == step.method_name,
                    Method.module_id == mod.id,
                )
            ).first()
            if method_row is None and method_key not in warned_missing_methods:
                warned_missing_methods.add(method_key)
                print(
                    f"[cancelled-walk] method "
                    f"'{step.module_name}.{step.method_name}' not "
                    f"registered; skipping its rows",
                    file=sys.stderr,
                )
            return method_row

        warned_missing_methods: set[tuple[str, str]] = set()

        口 = Step(step_num=3, name="Record each claim refusal as a failed row",
                 purpose="For every scheduled target with no Run row whose "
                         "claim left a refusal outcome sidecar, write a failed "
                         "Run row carrying the refusal's message and the "
                         "launch's document hash -- no version, cache key, "
                         "lineage rows, commit, env name or wfc version, the "
                         "row class of the cancelled rows -- and index it as a "
                         "failed ancestor "
                         "so the next step cancels the target's descendants "
                         "against it",
                 critical="The claim refuses before any row exists; this row "
                          "is written after the engine returns, so the "
                          "refusal still precedes every row for its target. "
                          "Idempotent: a target that already has a row is "
                          "skipped")
        from .record import read_claim_refusal

        outcomes_dir = layout.pipeline_outcomes_dir(Path(project_root), pipeline_id)
        refused_rows = 0
        for target in expected.targets:
            step = target.step
            key = target.key
            if key in actual_keys:
                continue
            message = next(
                (m for m in (read_claim_refusal(outcomes_dir, step.node_id,
                                                target.sample, variant)
                             for variant in target.variants) if m is not None),
                None)
            if message is None:
                continue
            method_row = _method_row(step)
            if method_row is None:
                continue
            failed_row = Run(
                method_id=method_row.id,
                params=target.params,
                sample=target.sample,
                status="failed",
                pipeline_id=pipeline_id,
                started_at=None,
                finished_at=datetime.now(UTC),
                error_message=message,
                node_id=target.node_id,
                pipeline_doc_hash=pipeline_doc_hash,
            )
            session.add(failed_row)
            session.commit()
            session.refresh(failed_row)
            refused_rows += 1
            actual_keys.add(key)
            failed_by_key.setdefault(key, failed_row.id)  # type: ignore[arg-type]
        if refused_rows:
            print(
                f"[cancelled-walk] wrote {refused_rows} failed row(s) for "
                f"claim refusals in pipeline {pipeline_id}"
            )

        口 = Step(step_num=4, name="Find each missing target's cause and reason",
                 purpose="For every expected target with no Run row, BFS upstream "
                         "through depends_on to the nearest failed ancestor "
                         "(equidistant hits tie-break on the lowest run id), and "
                         "give the row its reason: user after a user cancel, "
                         "else upstream_failed with a failed ancestor, and "
                         "without one the caller's reason with its message -- "
                         "not_run by default, engine_failed when the engine "
                         "failed with no step failing, interrupted when a "
                         "restart cut the pipeline off",
                 critical="Every expected target ends with a row; only a target "
                          "whose method is no longer registered is skipped. "
                          "The caller chooses the no-cause reason; the walk "
                          "never infers an engine-side reason itself")
        # BFS upstream in the DAG from a given node to find the nearest
        # failed ancestor for a (sample, variant) combo. Same-depth hits
        # are collected together and tie-broken by lowest run id.
        def _find_failed_ancestor(
            start_nid: str, sample: str, variant: str
        ) -> int | None:
            seen: set[str] = set()
            # Frontier carries a list of node ids at the current depth;
            # BFS proceeds level-by-level so all equidistant hits land
            # in the same sweep.
            frontier: list[str] = list(step_by_nid[start_nid].depends_on) \
                if start_nid in step_by_nid else []
            while frontier:
                level_hits: list[int] = []
                next_frontier: list[str] = []
                for nid in frontier:
                    if nid in seen:
                        continue
                    seen.add(nid)
                    anc_step = step_by_nid.get(nid)
                    if anc_step is None:
                        continue
                    # Compute the sample this ancestor would have run on:
                    # collapsed ancestors always run at COLLAPSED_SAMPLE.
                    anc_sample = COLLAPSED_SAMPLE if anc_step.sample_collapsed else sample
                    variants_for_anc = resolved_params.get(anc_step.node_id, {})
                    anc_params = variants_for_anc.get(variant, anc_step.params)
                    rid = failed_by_key.get(
                        _step_key(anc_step, anc_sample, anc_params))
                    if rid is not None:
                        level_hits.append(rid)
                    # Always traverse through to upstream parents (a
                    # successful intermediate doesn't block finding the
                    # originating failure further upstream).
                    next_frontier.extend(anc_step.depends_on)
                if level_hits:
                    return min(level_hits)
                frontier = next_frontier
            return None

        new_rows: list[Run] = []
        finished_at = datetime.now(UTC)

        for target in expected.targets:
            step = target.step
            key = target.key
            if key in actual_keys:
                continue

            # Missing target -- walk upstream to find the cause, under each
            # of its variant names in turn until one finds a failed ancestor
            # (an ancestor that sweeps differs by name). The target's sample
            # is its own axis: COLLAPSED_SAMPLE for a collapsed step, a
            # sample name for the rest.
            cause_run_id = next(
                (rid for rid in (_find_failed_ancestor(step.node_id,
                                                       target.sample, variant)
                                 for variant in target.variants)
                 if rid is not None),
                None)
            message = None
            if user_cancelled:
                reason = CANCEL_REASON_USER
            elif cause_run_id is not None:
                reason = CANCEL_REASON_UPSTREAM_FAILED
            else:
                reason = no_cause_reason
                message = no_cause_message

            # Look up method_id -- skip with a warning if the method
            # has been unregistered since the run was scheduled.
            method_row = _method_row(step)
            if method_row is None:
                continue

            new_rows.append(Run(
                method_id=method_row.id,
                params=target.params,
                sample=target.sample,
                status="cancelled",
                pipeline_id=pipeline_id,
                started_at=None,
                finished_at=finished_at,
                cancelled_due_to_run_id=cause_run_id,
                cancel_reason=reason,
                error_message=message,
                node_id=target.node_id,
                pipeline_doc_hash=pipeline_doc_hash,
            ))
            actual_keys.add(key)

        口 = Step(step_num=5, name="Write the cancelled rows",
                 purpose="Insert one cancelled Run row per missing target, with "
                         "its reason, its finish time, cancelled_due_to_run_id "
                         "at the failed ancestor when it has one, and the "
                         "launch's document hash; idempotent on re-invocation")
        if new_rows:
            for r in new_rows:
                session.add(r)
            session.commit()
            print(
                f"[cancelled-walk] wrote {len(new_rows)} cancelled row(s) "
                f"for pipeline {pipeline_id}"
            )

    return len(new_rows)


@dataclass
class PipelineSummary:
    """A pipeline's outcome, counted from its run rows.

    Attributes:
        pipeline_id: The pipeline.
        completed: Rows that ran and completed (no reused result).
        cached: Completed rows that reused a cached run.
        failed: Failed rows, including claim refusals the walk recorded.
        cancelled: Cancelled rows.
        running: Rows still in flight.
        status: The pipeline's status by ``pipeline_status`` over the rows:
            ``running``, ``succeeded``, ``failed``, ``cancelled`` or
            ``mixed``.
        failures: ``{"run_id", "sample", "error"}`` per failed row, in run
            id order.
    """

    pipeline_id: str
    completed: int
    cached: int
    failed: int
    cancelled: int
    running: int
    status: str
    failures: list[dict]

    @property
    def total(self) -> int:
        """Every row the pipeline holds."""
        return (self.completed + self.cached + self.failed + self.cancelled
                + self.running)


def summarize_pipeline(pipeline_id: str) -> PipelineSummary:
    """Count a pipeline's outcome from its run rows.

    The rows are the one record every surface reads -- the canvas status
    route and the history view count the same table -- so a summary read
    after the pipeline-end walk counts claim refusals and cancellations the
    way they do.

    Args:
        pipeline_id: Pipeline execution ID.

    Returns:
        The summary.
    """
    with get_session() as session:
        rows = session.exec(
            select(Run).where(Run.pipeline_id == pipeline_id).order_by(col(Run.id))
        ).all()
    counts = {"completed": 0, "cached": 0, "failed": 0, "cancelled": 0, "running": 0}
    failures: list[dict] = []
    for r in rows:
        if r.status == "completed":
            counts["cached" if r.cache_source_run_id is not None else "completed"] += 1
        elif r.status in counts:
            counts[r.status] += 1
        if r.status == "failed":
            failures.append({"run_id": r.id, "sample": r.sample,
                             "error": r.error_message})
    status = pipeline_status((r.status, r.cancel_reason) for r in rows)
    return PipelineSummary(pipeline_id=pipeline_id, status=status,
                           failures=failures, **counts)


def pipeline_summary(pipeline_id: str) -> int:
    """Print a pipeline's summary, counted from its run rows.

    Args:
        pipeline_id: Pipeline execution ID.

    Returns:
        0 on success.
    """
    summary = summarize_pipeline(pipeline_id)

    print("=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"Pipeline ID: {pipeline_id}")
    print(f"Status: {summary.status}")
    print(f"Total runs: {summary.total}  |  Passed: {summary.completed}  |  "
          f"Failed: {summary.failed}  |  Cached: {summary.cached}  |  "
          f"Cancelled: {summary.cancelled}")
    print()

    if summary.failures:
        print("FAILED RUNS:")
        for f in summary.failures:
            print(f"  - run {f['run_id']} sample={f['sample']}")
            if f.get("error"):
                print(f"    Error: {f['error']}")
        print()

    print("=" * 60)
    return 0
