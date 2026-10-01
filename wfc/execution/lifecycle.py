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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axiom_annotations import Step, task, workflow
from sqlmodel import col, select

from .. import layout
from ..contracts import COLLAPSED_SAMPLE
from ..orchestration import EngineOutcome
from ..persistence import Method, Module, Run, get_session


def sweep_legacy_workspace(project_root: Path) -> bool:
    """Delete the legacy ``.runs/workspace/`` tree if it is present.

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


@task(purpose="Launch bookkeeping: sweep the legacy workspace, fix the pipeline "
              "id, create the log directories, and freeze the substituted and "
              "editable documents into the pipeline dir",
      inputs="project root, the caller's document path, the pre-substitution "
             "and substituted documents, an optional caller-provided pipeline id",
      outputs="PipelineLaunch: pipeline id, log dir, frozen document path")
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
        The launch's pipeline id, log dir and frozen document path.
    """
    import logging as _logging

    project_root = Path(project_root)

    # The cache is authoritative and nothing writes to ``.runs/workspace/``.
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
    # .runs/pipelines/<pid>/pipeline.json and expect literals, so the
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
    return PipelineLaunch(pipeline_id=pipeline_id, log_dir=log_dir, frozen_doc=frozen_doc)


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
            name="wfc-push-worker",
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


@task(purpose="Engine-failure sequence: flip in-flight rows to failed unless the "
              "caller cancelled, write the cancelled rows, drain the push worker, "
              "and raise the engine's failure message",
      inputs="pipeline id, project root, the engine's outcome, the push-worker "
             "handle, the cancellation probe",
      outputs="raises RuntimeError -- never returns")
def finish_failed(
    pipeline_id: str,
    project_root: str,
    outcome: EngineOutcome,
    worker: PushWorker,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """Close a failed engine run and raise the outcome's message.

    Args:
        pipeline_id: The pipeline that failed.
        project_root: The wfc project root.
        outcome: The engine's outcome (a non-zero exit). Its ``message`` is
            raised as it is; this sequence builds no text.
        worker: The push-worker handle to drain.
        is_cancelled: Optional probe; when it answers True the cancel
            handler owns row state and ``fail_pipeline`` is skipped.

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
        _write_cancelled_rows(pipeline_id, project_root)
    except Exception as _walk_exc:
        print(
            f"[run_pipeline] _write_cancelled_rows raised: {_walk_exc}",
            file=sys.stderr,
        )
    cancelled = bool(is_cancelled and is_cancelled())
    drain_push_worker(worker, cancel=cancelled)
    raise RuntimeError(outcome.message)


@task(purpose="Success sequence: the pipeline-end cancelled-row walk, the deferred "
              "archive pass, and the push-worker drain",
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
    """
    # -- Pipeline-end cancelled-row walk (success path) --
    # Always on: on a fully-successful pipeline this is an O(nodes)
    # no-op. When Snakemake's --keep-going skipped some targets, it fills
    # in cancelled rows for the un-run triples before the archive pass.
    try:
        _write_cancelled_rows(pipeline_id, project_root)
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
    """Mark any in-flight runs for this pipeline as 'cancelled'.

    Sibling of :func:`fail_pipeline`.  User-initiated cancel: distinct from
    upstream-failure cancel (which writes :func:`_write_cancelled_rows`
    rows tagged with ``upstream_node_id``).  Idempotent — repeated calls
    on a pipeline whose runs are already terminal are a no-op.

    Args:
        pipeline_id: Pipeline whose in-flight rows should be flipped.
        project_root: Reserved (matches ``fail_pipeline`` style); not used
            today because we only touch the runs table.

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
            run.finished_at = datetime.now(UTC)
            if run.error_message is None:
                run.error_message = "Cancelled by user"
        session.commit()
        n = len(in_flight)
        if n > 0:
            print(f"Marked {n} in-flight run(s) as cancelled for pipeline {pipeline_id}")
        return n


def fail_pipeline(pipeline_id: str) -> None:
    """Mark any in-flight runs for this pipeline as 'failed'.

    Already-completed runs are left untouched (their data is safe in .runs/{id}/).
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")
        )
        in_flight = session.exec(stmt).all()
        for run in in_flight:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            # Only set error fields if not already populated
            # (the try/except in the generated rule may have already called
            # complete_run with error details before fail_pipeline runs)
            if run.error_message is None:
                run.error_message = f"Pipeline {pipeline_id} failed (orphaned run)"
            if run.error_traceback is None:
                run.error_traceback = "No traceback available (run was still in-flight when pipeline failed)"
        session.commit()

        n = len(in_flight)
        if n > 0:
            print(f"Marked {n} in-flight run(s) as failed for pipeline {pipeline_id}")
        else:
            print(f"No in-flight runs to mark for pipeline {pipeline_id}")
        print("Workspace preserved for debugging")


@workflow(purpose="Pipeline-end cancelled-row walk: expand the frozen document's "
                  "targets, compare them against the run rows, record each "
                  "claim-refused target as a failed row, link each missing "
                  "target to its nearest failed ancestor by BFS, and write the "
                  "cancelled rows",
          inputs="pipeline id, project root (locates the frozen pipeline.json)",
          outputs="number of cancelled rows written")
def _write_cancelled_rows(pipeline_id: str, project_root: str) -> int:
    """Persist first-class 'cancelled' Run rows for targets that did not run.

    Called at pipeline-end (both success and failure paths) from
    ``run_pipeline``. A target the claim refused has no row but left a
    claim-refusal outcome sidecar; it first gets a ``failed`` row carrying
    the refusal's message, so it is a failed ancestor like any other. For
    every expected ``(raw document node id, sample, variant)`` target
    produced by ``expand_step_combos``, if the DB does not already contain
    a Run row with that ``node_id``, sample and params, walk upstream
    through the frozen DAG (``StepDef.depends_on``) until we hit a run with
    ``status='failed'``. Write a Run row with ``status='cancelled'`` and
    ``cancelled_due_to_run_id`` pointing at that failed ancestor.

    A target is keyed by the node's raw document id (``Run.node_id``), the
    id the claim stamps on every run row. ``StepDef.node_id`` is resolved
    to it through the Graph unit's ``document_node``: for a legacy
    numeric-id document the step's id is its method name while the raw id
    is the number. Every row the walk writes carries the raw id.

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
            locate ``.runs/pipelines/<pid>/pipeline.json``).

    Returns:
        Number of cancelled rows written (useful for tests / logging).
    """
    from ..graph import document_node, expand_step_combos, resolve_variant_model
    from .composer import load_pipeline_from_path

    口 = Step(step_num=1, name="Expand the frozen document's targets",
             purpose="Load the frozen pipeline document, derive the one variant "
                     "model and enumerate every (node, sample, variant) target "
                     "the Snakefile scheduled")
    pipeline_json = layout.pipeline_doc_path(Path(project_root), pipeline_id)
    if not pipeline_json.exists():
        # Nothing to reconcile -- no frozen pipeline doc means the run
        # never reached the snake-gen stage (or was cleaned up).
        return 0

    try:
        pipeline = load_pipeline_from_path(pipeline_json)
        raw_document = json.loads(pipeline_json.read_text())
    except Exception as exc:
        print(
            f"[cancelled-walk] Failed to load pipeline.json for {pipeline_id}: {exc}",
            file=sys.stderr,
        )
        return 0

    steps = pipeline.steps
    if not steps:
        return 0

    # StepDef.node_id -> the node's raw document id, the id every run row
    # carries in ``Run.node_id``. The two differ for a legacy numeric-id
    # document ("1" vs the method name); sentinel and sidecar paths, the
    # variant tables and the combos stay keyed by StepDef.node_id.
    raw_id_by_step: dict[str, str] = {}
    for s in steps:
        doc_node = document_node(raw_document, s.node_id)
        raw_id_by_step[s.node_id] = (str(doc_node["id"]) if doc_node is not None
                                     else s.node_id)

    # The one variant derivation (the Graph unit's): tables by node id with
    # a method-name fallback, the axis, the padding -- the same model the
    # Snakefile emitter scheduled from, so the walk enumerates exhaustively.
    resolved_params = resolve_variant_model(pipeline).tables

    # Per-step combos: a collapsed step's sample axis is COLLAPSED_SAMPLE,
    # a per-sample step's is the sample list. A pipeline mixing the two
    # (a fan-in branch beside a per-sample branch) has no single
    # whole-pipeline answer, so the walk enumerates step by step.
    combos_by_step: dict[str, list[dict[str, str]]] = {}
    for step, combo in expand_step_combos(
        steps=steps,
        samples=pipeline.samples,
        resolved_params=resolved_params,
        explicit_combos=pipeline.explicit_combos,
    ):
        combos_by_step.setdefault(step.node_id, []).append(combo)

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

        # Helper: normalised params fingerprint for equality matching.
        def _fp(params: dict | None) -> str:
            return json.dumps(params or {}, sort_keys=True, default=str)

        # A target's key: its node's raw document id, the sample and the
        # params fingerprint. The node id tells apart two nodes sharing a
        # method; the fingerprint keeps one node's variants apart.
        def _key(raw_node_id: str, sample: str,
                 params: dict | None) -> tuple[str, str, str]:
            return (raw_node_id, sample, _fp(params))

        def _target_key(step, sample: str, params) -> tuple[str, str, str]:
            return _key(raw_id_by_step[step.node_id], sample, params)

        # Targets that already have a Run row (any status). The walk uses
        # this set for its presence check (idempotency + cache-hit
        # safety), so cancelled rows count too.
        actual_keys: set[tuple[str, str, str]] = {
            _key(r.node_id, r.sample or "", r.params)
            for r in existing_runs if r.node_id
        }

        # Failed-run lookup keyed by target -> lowest run.id (the tiebreak).
        failed_by_key: dict[tuple[str, str, str], int] = {}
        for r in existing_runs:
            if r.status != "failed" or not r.node_id:
                continue
            key = _key(r.node_id, r.sample or "", r.params)
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
                         "Run row carrying the refusal's message -- no version, "
                         "cache key or lineage rows, the row class of the "
                         "cancelled rows -- and index it as a failed ancestor "
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
        for step in steps:
            for combo in combos_by_step.get(step.node_id, []):
                sample = combo["sample"]
                variant = combo["variant"]
                params = resolved_params.get(step.node_id, {}).get(variant, step.params)
                key = _target_key(step, sample, params)
                if key in actual_keys:
                    continue
                message = read_claim_refusal(outcomes_dir, step.node_id, sample, variant)
                if message is None:
                    continue
                method_row = _method_row(step)
                if method_row is None:
                    continue
                failed_row = Run(
                    method_id=method_row.id,
                    params=params,
                    sample=sample,
                    status="failed",
                    pipeline_id=pipeline_id,
                    started_at=None,
                    finished_at=datetime.now(UTC),
                    error_message=message,
                    node_id=raw_id_by_step[step.node_id],
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

        口 = Step(step_num=4, name="Link each missing target to its failed ancestor",
                 purpose="For every scheduled target with no Run row, BFS upstream "
                         "through depends_on to the nearest failed ancestor "
                         "(equidistant hits tie-break on the lowest run id); a "
                         "target with no failed ancestor is skipped with a log line")
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
                        _target_key(anc_step, anc_sample, anc_params))
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

        for step in steps:
            for combo in combos_by_step.get(step.node_id, []):
                variant = combo["variant"]
                # The combo already carries the step's own sample axis:
                # COLLAPSED_SAMPLE for a collapsed step, a sample name for
                # the rest (expand_step_combos decides per step).
                effective_sample = combo["sample"]
                variants_for_step = resolved_params.get(step.node_id, {})
                params = variants_for_step.get(variant, step.params)
                key = _target_key(step, effective_sample, params)
                if key in actual_keys:
                    continue

                # Missing target -- walk upstream to find the cause.
                cause_run_id = _find_failed_ancestor(
                    step.node_id, effective_sample, variant
                )
                if cause_run_id is None:
                    # No failed ancestor -- this can happen on a
                    # legitimately-skipped branch (shouldn't in practice).
                    # Skip rather than write a row with a NULL cause, so
                    # history holds no rows the user can't act on from the
                    # banner. Log for debugging.
                    print(
                        f"[cancelled-walk] no failed ancestor for "
                        f"({step.node_id}, {effective_sample}, {variant}) "
                        f"in pipeline {pipeline_id}; skipping",
                        file=sys.stderr,
                    )
                    continue

                # Look up method_id -- skip with a warning if the method
                # has been unregistered since the run was scheduled.
                method_row = _method_row(step)
                if method_row is None:
                    continue

                new_row = Run(
                    method_id=method_row.id,
                    params=params,
                    sample=effective_sample,
                    status="cancelled",
                    pipeline_id=pipeline_id,
                    started_at=None,
                    finished_at=None,
                    cancelled_due_to_run_id=cause_run_id,
                    node_id=raw_id_by_step[step.node_id],
                )
                new_rows.append(new_row)
                # Mark the target as now present so repeated steps don't
                # double-write (paranoia against combo duplication).
                actual_keys.add(key)

        口 = Step(step_num=5, name="Write the cancelled rows",
                 purpose="Insert one cancelled Run row per missing target with "
                         "cancelled_due_to_run_id at the failed ancestor; "
                         "idempotent on re-invocation")
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
        status: ``running`` while any row is, else ``failed`` when any
            failed, else ``cancelled`` when any was cancelled, else
            ``completed``.
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
    if counts["running"]:
        status = "running"
    elif counts["failed"]:
        status = "failed"
    elif counts["cancelled"]:
        status = "cancelled"
    else:
        status = "completed"
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
