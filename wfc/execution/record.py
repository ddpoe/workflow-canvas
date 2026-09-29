"""Record phase: the single tail every exit-with-record funnels into.

``compose_record`` is the pure kernel: given how the step ended, exactly
which rows and sidecar files to write. ``run_record`` carries the plan out
in one fixed order (run-row flip, sentinel + run-id sidecar, push enqueue,
outcome sidecar, console line) — each ending simply switches the relevant
writes on or off:

- ``cached`` never flips the run row (the audit row was inserted completed
  at claim time) but touches the sentinel and writes both sidecars.
- ``completed`` flips the row, touches the sentinel, writes both sidecars,
  and enqueues the outputs for async DVC push (non-fatal on failure).
- the failure endings flip the row to failed and write the outcome sidecar
  only.
- ``slurm`` writes nothing at all (the SLURM carve-out is an
  exit-WITHOUT-record point).

The record's writers live here: ``complete_run`` (the run-row flip and the
RunOutput upsert) and ``_write_outcome`` (the outcome sidecar).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from axiom_annotations import task, Step

from ..persistence import Run, RunOutput

from ..contracts import COLLAPSED_SAMPLE
from ..persistence import get_session, project_root as get_project_root

#: Endings that write a failed run row + outcome sidecar.
FAILURE_ENDINGS = ("materialize-failed", "no-container", "script-missing",
                   "launch-failure", "method-failed", "missing-slot",
                   "duplicate-output", "refused-output")


def compose_record(ending: str, error_message: str | None = None) -> dict:
    """Compose the record writes for a step ending.

    Pure function: given how the step ended (and the error to surface, for
    failure endings), return exactly which rows and sidecars to write and
    what to print. No I/O — the caller carries the plan out.

    Args:
        ending: One of ``"cached"``, ``"completed"``, ``"slurm"``, or a
            failure ending (``"materialize-failed"``, ``"no-container"``,
            ``"script-missing"``, ``"launch-failure"``, ``"method-failed"``,
            ``"missing-slot"``, ``"duplicate-output"``,
            ``"refused-output"``).
        error_message: The error to surface on the console line for failure
            endings (ignored for ``cached`` / ``completed``).

    Returns:
        A plan dict with keys:

        - ``"run_status"``: ``"completed"`` / ``"failed"`` to flip the run
          row, or ``None`` to leave it (cache hit, SLURM carve-out).
        - ``"touch_sentinel"`` / ``"write_run_id_sidecar"``: Snakemake
          sentinel + lineage sidecar writes (success and cache hit only).
        - ``"enqueue_push"``: mark RunOutput rows for the async DVC push
          worker (success only).
        - ``"outcome_status"``: the outcome sidecar's status field, or
          ``None`` to skip the sidecar entirely (SLURM carve-out).
        - ``"console"``: the exact stderr line to print, or ``None``.
        - ``"rc"``: the process exit code to return.
    """
    if ending == "cached":
        return {"run_status": None, "touch_sentinel": True,
                "write_run_id_sidecar": True, "enqueue_push": False,
                "outcome_status": "cached", "console": None, "rc": 0}
    if ending == "completed":
        return {"run_status": "completed", "touch_sentinel": True,
                "write_run_id_sidecar": True, "enqueue_push": True,
                "outcome_status": "completed", "console": None, "rc": 0}
    if ending == "slurm":
        # Exit-WITHOUT-record: no row flip, no sentinel, no outcome sidecar.
        return {"run_status": None, "touch_sentinel": False,
                "write_run_id_sidecar": False, "enqueue_push": False,
                "outcome_status": None,
                "console": f"ERROR: {error_message}", "rc": 1}
    if ending not in FAILURE_ENDINGS:
        raise ValueError(f"unknown step ending: {ending!r}")
    if ending == "launch-failure":
        console = f"ERROR: method execution failed: {error_message}"
    else:
        console = f"ERROR: {error_message}"
    return {"run_status": "failed", "touch_sentinel": False,
            "write_run_id_sidecar": False, "enqueue_push": False,
            "outcome_status": "failed", "console": console, "rc": 1}


@task(purpose="Record phase: carry out the composed writes — run-row flip, "
              "sentinel, sidecars, push enqueue, console line",
      inputs="step ending + error fields + collected outputs/metrics",
      outputs="process exit code; rows/sentinel/sidecars written per the plan")
def run_record(
    ending: str,
    run_id: int,
    node_id: str,
    sample: str,
    variant: str,
    pipeline_id: str,
    outcomes_dir: Path,
    error_message: str | None = None,
    error_traceback: str | None = None,
    output_files: list[str] | None = None,
    metrics: dict | None = None,
) -> int:
    """Write the run records for a step ending and return the exit code.

    Args:
        ending: How the step ended (see :func:`compose_record`).
        run_id: The run to record against.
        node_id: The node that ran.
        sample: Sample identifier.
        variant: Parameter variant name.
        pipeline_id: Pipeline execution ID.
        outcomes_dir: The pipeline's outcome-sidecar directory (already
            created by the orchestrator).
        error_message: Error message for failure endings.
        error_traceback: Error traceback for failure endings.
        output_files: Collected output paths (success only; drives the
            push enqueue).
        metrics: Collected metrics (success only; stored on the run row).

    Returns:
        0 for ``cached`` / ``completed``, 1 for every failure ending.
    """
    from wfc import layout

    口 = Step(step_num=1, name="Compose record writes",
             purpose="Derive exactly which rows and sidecars to write from how "
                     "the step ended (pure composition)")
    plan = compose_record(ending, error_message)

    口 = Step(step_num=2, name="Flip run row",
             purpose="Mark the run completed (with metrics) or failed (with error "
                     "fields, best-effort); cache hits and the SLURM carve-out "
                     "leave the row untouched")
    if plan["run_status"] == "completed":
        # Output rows were already written by the collect phase — the sole
        # RunOutput writer. complete_run is passed no output_files, so it
        # only flips the row and stores metrics.
        complete_run(
            run_id=run_id,
            status="completed",
            metrics=metrics,
        )
    elif plan["run_status"] == "failed":
        try:
            complete_run(run_id=run_id, status="failed",
                         error_message=error_message,
                         error_traceback=error_traceback)
        except Exception:
            pass

    口 = Step(step_num=3, name="Touch sentinel and run-id sidecar",
             purpose="Touch the Snakemake-visible sentinel and write the "
                     "run_id.txt lineage sidecar next to it")
    if plan["touch_sentinel"]:
        # The Snakefile declares one zero-byte sentinel per
        # (pipeline, node, sample, variant); creating it here signals to
        # Snakemake that the rule succeeded. Real outputs remain in the
        # run-staging dir / DVC cache.
        sentinel_sample = COLLAPSED_SAMPLE if sample == COLLAPSED_SAMPLE else sample
        sentinel_path = layout.run_sentinel_path(
            get_project_root(), pipeline_id, node_id, sentinel_sample, variant
        )
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.touch()
        if plan["write_run_id_sidecar"]:
            # Write run_id.txt next to the sentinel so downstream nodes can
            # resolve parent run IDs by path (mirrors the claim phase's
            # sidecar walk over sentinels/{pipeline}/{node}/{sample}/{variant}).
            (sentinel_path.parent / "run_id.txt").write_text(str(run_id))

    # A plain Step that names its delegate: first_push_status runs inside the
    # try, so an AutoStep cannot bind to it without changing the error handling.
    口 = Step(step_num=4, name="Enqueue outputs for async DVC push",
             purpose="Mark RunOutput rows with Storage's first-push state "
                     "(first_push_status: pending when a remote is configured, "
                     "deferred otherwise) so the push worker picks them up; "
                     "non-fatal on error")
    if plan["enqueue_push"]:
        # Outputs live in the run-staging dir (and the deferred
        # archive pass will move them into the DVC cache). Mark rows for
        # the push worker; if no remote is configured, leave them deferred.
        try:
            from ..storage import first_push_status
            target_state = first_push_status(Path(get_project_root()))
            from sqlmodel import select

            from ..persistence import RunOutput
            with get_session() as session:
                for archive_entry_str in output_files or []:
                    # By path: two slots' files may share a base name.
                    row = session.exec(
                        select(RunOutput).where(
                            RunOutput.run_id == run_id,
                            RunOutput.artifact_path == archive_entry_str,
                        )
                    ).first()
                    if row is not None:
                        row.push_status = target_state
                session.commit()
        except Exception as exc:
            print(f"WARNING: push enqueue failed: {exc}", file=sys.stderr)

    口 = Step(step_num=5, name="Write outcome sidecar and report",
             purpose="Write the per-target outcome sidecar for pipeline-summary "
                     "aggregation, print the composed console line, and return "
                     "the exit code")
    if plan["outcome_status"] is not None:
        outcome = {
            "node_id": node_id, "sample": sample, "variant": variant,
            "run_id": run_id, "status": plan["outcome_status"],
            "error": error_message if plan["outcome_status"] == "failed" else None,
        }
        _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
    if plan["console"] is not None:
        print(plan["console"], file=sys.stderr)
    return plan["rc"]


@task(purpose="Mark a run as finished and update the RunOutput rows the collect "
              "phase wrote (cache is authoritative storage)")
def complete_run(
    run_id: int,
    status: str = "completed",
    output_files: list[str] | None = None,
    metrics: dict | None = None,
    error_message: str | None = None,
    error_traceback: str | None = None,
) -> None:
    """Mark a run as finished and record output rows.

    The DVC cache is the authoritative storage for outputs.  Snakemake-visible
    completion is signaled via a zero-byte sentinel touched by
    ``run_step`` (not this function).  Content hashing and caching
    happen in the post-pipeline archive pass (or ``wfc cache archive``);
    RunOutput rows here have ``content_hash=NULL`` and ``artifact_path``
    pointing at the run-archive staging entry.

    Args:
        run_id: The run to complete.
        status: Final status (usually 'completed').
        output_files: Paths to output files (in run-archive staging area).
        metrics: Optional dict of metrics to store.
        error_message: Error message for failed runs.
        error_traceback: Error traceback for failed runs.
    """

    with get_session() as session:
        run = session.get(Run, run_id)
        if run is None:
            print(f"ERROR: run {run_id} not found", file=sys.stderr)
            sys.exit(1)

        口 = Step(step_num=1, name="Mark run finished",
                 purpose="Set status, finished_at, metrics, and (on failure) "
                         "error_message/error_traceback on the Run row")
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        if metrics:
            run.metrics = metrics
        # Persist error information on failed runs
        if error_message is not None:
            run.error_message = error_message
        if error_traceback is not None:
            run.error_traceback = error_traceback

        口 = Step(step_num=2, name="Update the collected output rows",
                 purpose="For each output file, refresh the size and mtime on the "
                         "RunOutput row the collect phase wrote for that path; a "
                         "file with no row is an error, never a new row without a "
                         "slot; content_hash stays NULL (deferred archiving)")
        for fpath in output_files or []:
            src = Path(fpath)
            if not src.exists():
                print(f"ERROR: output file not found: {fpath}", file=sys.stderr)
                sys.exit(1)

            # Matched by path, not base name: two slots' files may share a
            # name. The collect phase is the only writer that knows a file's
            # slot, so a file it did not record is refused here.
            existing_ro = session.exec(
                select(RunOutput).where(
                    RunOutput.run_id == run_id,
                    RunOutput.artifact_path == str(src),
                )
            ).first()
            if existing_ro is None:
                print(
                    f"ERROR: run {run_id} has no output record for {fpath}; "
                    f"outputs are recorded when a step's outputs are collected",
                    file=sys.stderr,
                )
                sys.exit(1)
            if src.is_file():
                stat = src.stat()
                existing_ro.file_size = stat.st_size
                existing_ro.file_mtime = stat.st_mtime

        session.commit()


def outcome_path(outcomes_dir: Path, node_id: str, sample: str, variant: str) -> Path:
    """Where one target's outcome sidecar lives in a pipeline's outcomes dir.

    Args:
        outcomes_dir: The pipeline's outcome-sidecar directory.
        node_id: The target's node.
        sample: The target's sample.
        variant: The target's variant.

    Returns:
        The sidecar path.
    """
    return outcomes_dir / f"{node_id}__{sample}__{variant}.json"


def _write_outcome(outcomes_dir: Path, node_id: str, sample: str, variant: str, outcome: dict) -> None:
    """Write a JSON outcome sidecar file."""
    outcome_path(outcomes_dir, node_id, sample, variant).write_text(
        json.dumps(outcome, indent=2, default=str)
    )


#: The ``phase`` an outcome sidecar carries when the claim refused its target.
CLAIM_REFUSAL_PHASE = "claim"


def write_claim_refusal(pipeline_id: str, node_id: str, sample: str,
                        variant: str, message: str) -> None:
    """Write the failed outcome sidecar for a target the claim refused.

    A refusal lands before any run row exists, so the sidecar is the only
    trace the refused target leaves; the pipeline-end walk turns it into
    the target's failed row after the engine returns. It carries no run id.

    Args:
        pipeline_id: The pipeline the target belongs to.
        node_id: The refused target's node.
        sample: The refused target's sample.
        variant: The refused target's variant.
        message: The refusal, as the claim printed it.
    """
    from wfc import layout

    outcomes_dir = layout.pipeline_outcomes_dir(get_project_root(), pipeline_id)
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    _write_outcome(outcomes_dir, node_id, sample, variant, {
        "node_id": node_id, "sample": sample, "variant": variant,
        "run_id": None, "status": "failed", "phase": CLAIM_REFUSAL_PHASE,
        "error": message,
    })


def read_claim_refusal(outcomes_dir: Path, node_id: str, sample: str,
                       variant: str) -> str | None:
    """Return the message of a target's claim refusal, if the claim refused it.

    Args:
        outcomes_dir: The pipeline's outcome-sidecar directory.
        node_id: The target's node.
        sample: The target's sample.
        variant: The target's variant.

    Returns:
        The refusal message, or ``None`` when the target's sidecar is absent,
        unreadable, or records anything other than a claim refusal.
    """
    path = outcome_path(outcomes_dir, node_id, sample, variant)
    try:
        outcome = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if outcome.get("phase") != CLAIM_REFUSAL_PHASE or outcome.get("status") != "failed":
        return None
    return str(outcome.get("error") or "Refused at claim")
