"""The push worker: carry new cache entries to the configured remote in the background.

A new entry's first push status is decided once, by :func:`first_push_status`:
pending when a remote is configured, deferred when none is.

The worker (the ``push-lifecycle`` workflow, :func:`_push_worker_loop`) ticks
every ``PUSH_POLL_INTERVAL`` seconds.  Each tick snapshots the pending and
failed rows under ``PUSH_MAX_ATTEMPTS``, marks them in flight, pushes each
entry in its own transfer and records each row's own outcome, so a failed
entry fails alone; a tick that pushed
nothing and left failures waits the next ``PUSH_BACKOFFS`` step instead.
Before the worker starts, :func:`_reset_orphan_pushes` returns recently
finished rows stuck in flight or failed to the retry budget.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import col, select

from ..persistence import PushStatus, RunOutput, Sample, get_session

# Exponential backoff (seconds) per attempt index.  Index 0 = first retry.
PUSH_BACKOFFS = (1, 4, 16, 60)
PUSH_MAX_ATTEMPTS = 5
PUSH_POLL_INTERVAL = 2.0  # seconds between worker ticks


def first_push_status(project_dir: Path | str) -> PushStatus:
    """Decide a new cache entry's first push status: the rule, stated once.

    Pending when ``.dvc/config`` declares a remote (the push worker, or a
    standalone sample registration, pushes it); deferred when none does.
    Execution's record phase applies it to a run's outputs, and the sample
    store to a registered sample.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        ``PushStatus.pending`` or ``PushStatus.deferred``.
    """
    from .transport import has_remote_configured

    return PushStatus.pending if has_remote_configured(project_dir) else PushStatus.deferred


def _reset_orphan_pushes(project_root: Path, *, age_days: int = 7) -> int:
    """Reset push_attempts on stale pending/in_flight/failed rows.

    Called on ``run_pipeline`` startup before spawning the worker.  Re-enters
    rows that finished in the last ``age_days`` into the retry budget.

    Args:
        project_root: wfc project root (for logging only).
        age_days: How recent ``finished_at`` must be to qualify.

    Returns:
        Number of rows reset.
    """
    from ..persistence import PushStatus, Run, RunOutput, Sample

    cutoff = datetime.now(UTC).timestamp() - age_days * 86400
    n = 0
    with get_session() as session:
        # RunOutput: join Run to get finished_at.
        ro_rows = session.exec(
            select(RunOutput)
            .join(Run, col(RunOutput.run_id) == col(Run.id))
            .where(col(RunOutput.push_status).in_([
                PushStatus.pending.value,
                PushStatus.in_flight.value,
                PushStatus.failed.value,
            ]))
            .where(col(Run.finished_at).isnot(None))
        ).all()
        for r in ro_rows:
            run = session.get(Run, r.run_id)
            if run and run.finished_at and run.finished_at.timestamp() > cutoff:
                r.push_attempts = 0
                r.push_status = PushStatus.pending.value
                session.add(r)
                n += 1
        # Sample: gate on registered_at (the Sample analog of Run.finished_at)
        # so the worker only re-enters samples registered within the recovery
        # window. Prevents thundering-herd recovery of years-old samples on
        # every pipeline startup.
        sample_rows = session.exec(
            select(Sample)
            .where(col(Sample.push_status).in_([
                PushStatus.pending.value,
                PushStatus.in_flight.value,
                PushStatus.failed.value,
            ]))
            .where(col(Sample.registered_at).isnot(None))
        ).all()
        for s in sample_rows:
            if s.registered_at and s.registered_at.timestamp() > cutoff:
                s.push_attempts = 0
                s.push_status = PushStatus.pending.value
                session.add(s)
                n += 1
        session.commit()
    if n:
        print(f"[push_worker] reset {n} orphan push row(s) for retry")
    return n


@task(
    purpose="Snapshot the rows awaiting a push: pending or failed, under the "
            "attempt limit, with a content hash; group their ids by hash",
    inputs="the database",
    outputs="(RunOutput ids by hash, Sample ids by hash)",
)
def _snapshot_push_rows() -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Snapshot the rows needing a push, grouped by content hash.

    Only ids and hashes leave the session, so no session stays open across
    the network call.

    Returns:
        Tuple of (RunOutput ids by hash, Sample ids by hash).
    """
    with get_session() as session:
        ro_rows = list(session.exec(
            select(RunOutput)
            .where(col(RunOutput.push_status).in_([
                PushStatus.pending.value, PushStatus.failed.value
            ]))
            .where(RunOutput.push_attempts < PUSH_MAX_ATTEMPTS)
            .where(col(RunOutput.content_hash).isnot(None))
        ).all())
        sample_rows = list(session.exec(
            select(Sample)
            .where(col(Sample.push_status).in_([
                PushStatus.pending.value, PushStatus.failed.value
            ]))
            .where(Sample.push_attempts < PUSH_MAX_ATTEMPTS)
            .where(col(Sample.content_hash).isnot(None))
        ).all())

        # Detach IDs and hashes for the actual push (avoid holding the
        # session open across a network call).
        ro_ids_by_hash: dict[str, list[int]] = {}
        for r in ro_rows:
            ro_ids_by_hash.setdefault(r.content_hash, []).append(r.id)
        sample_ids_by_hash: dict[str, list[int]] = {}
        for s in sample_rows:
            sample_ids_by_hash.setdefault(s.content_hash, []).append(s.id)
    return ro_ids_by_hash, sample_ids_by_hash


@task(
    purpose="Mark the snapshot's rows in flight (best-effort, in a session of its own)",
    inputs="RunOutput and Sample ids by hash",
    outputs="the rows' push_status set to in_flight",
)
def _mark_in_flight(
    ro_ids_by_hash: dict[str, list[int]], sample_ids_by_hash: dict[str, list[int]]
) -> None:
    """Mark every snapshot row in flight.

    Args:
        ro_ids_by_hash: RunOutput ids by content hash.
        sample_ids_by_hash: Sample ids by content hash.
    """
    with get_session() as session:
        for r in session.exec(
            select(RunOutput).where(col(RunOutput.id).in_([
                rid for ids in ro_ids_by_hash.values() for rid in ids
            ]))
        ).all():
            r.push_status = PushStatus.in_flight.value
            session.add(r)
        for s in session.exec(
            select(Sample).where(col(Sample.id).in_([
                sid for ids in sample_ids_by_hash.values() for sid in ids
            ]))
        ).all():
            s.push_status = PushStatus.in_flight.value
            session.add(s)
        session.commit()


def _failed_hash(obj) -> str | None:
    """A failed entry's hash, whether reported as a string or a HashInfo."""
    if isinstance(obj, str):
        return obj
    return getattr(obj, "value", None) or getattr(
        getattr(obj, "hash_info", None), "value", None
    )


@task(
    purpose="Push each snapshot entry in its own transfer and keep each entry's "
            "own error; a failed entry fails alone, and only an error opening "
            "the transport fails every entry",
    inputs="the snapshot's content hashes; the sample hashes; project root",
    outputs="each hash's error, or None when it reached the remote",
)
def _push_entries(
    all_hashes: set[str], sample_hashes: set[str], project_root: Path
) -> dict[str, str | None]:
    """Push every entry through the DVC transport, one transfer per entry.

    A malformed entry is refused with the repair its kind names: a sample
    is re-registered, a run output is re-run.

    Args:
        all_hashes: Content hashes to push.
        sample_hashes: The hashes that belong to sample rows.
        project_root: wfc project root.

    Returns:
        Each hash's error message, or None for a hash the remote accepted.
    """
    from . import transport
    from .cache import output_repair, sample_repair

    repairs = {
        h: sample_repair() if h in sample_hashes else output_repair()
        for h in all_hashes
    }
    try:
        result = transport.push(sorted(all_hashes), project_root, repairs=repairs)
    except Exception as exc:  # noqa: BLE001 -- the transport itself failed
        return {h: str(exc) or type(exc).__name__ for h in all_hashes}
    errors = dict(getattr(result, "errors", None) or {})
    for obj in getattr(result, "failed", None) or ():
        h = _failed_hash(obj)
        if h:
            errors.setdefault(h, "DVC reported the entry failed.")
    return {h: errors.get(h) for h in all_hashes}


@task(
    purpose="Record each row's own outcome: pushed with its time, or failed with "
            "its attempt count raised and its entry's own error kept",
    inputs="ids by hash; each hash's error or None",
    outputs="the number of rows marked pushed",
)
def _record_push_outcomes(
    ro_ids_by_hash: dict[str, list[int]],
    sample_ids_by_hash: dict[str, list[int]],
    errors: dict[str, str | None],
) -> int:
    """Write each snapshot row's push outcome.

    Args:
        ro_ids_by_hash: RunOutput ids by content hash.
        sample_ids_by_hash: Sample ids by content hash.
        errors: Each hash's error, or None when the remote accepted it.

    Returns:
        Number of rows marked pushed.
    """
    now = datetime.now(UTC)
    pushed_count = 0
    with get_session() as session:
        for model, ids_by_hash in ((RunOutput, ro_ids_by_hash), (Sample, sample_ids_by_hash)):
            for h, ids in ids_by_hash.items():
                error = errors.get(h, "push failed")
                for row_id in ids:
                    row = session.get(model, row_id)
                    if row is None:
                        continue
                    if error is None:
                        row.push_status = PushStatus.pushed.value
                        row.pushed_at = now
                        row.push_error = None
                        pushed_count += 1
                    else:
                        row.push_attempts = (row.push_attempts or 0) + 1
                        row.push_error = error
                        row.push_status = PushStatus.failed.value
                    session.add(row)
        session.commit()
    return pushed_count


@workflow(
    purpose="One push-worker tick: snapshot the rows awaiting a push, mark them in "
            "flight, push each entry in its own transfer, record each row's own "
            "outcome",
    inputs="project root",
    outputs="(rows pushed, hashes still pending or failed)",
)
def _push_worker_tick(project_root: Path) -> tuple[int, int]:
    """Single push-worker tick: scan rows, push each entry, update DB.

    Returns:
        Tuple of (pushed_count, remaining_count).  ``remaining_count`` is
        the hashes that failed this tick.
    """
    口 = AutoStep(step_num=1, name="Snapshot rows needing push")
    ro_ids_by_hash, sample_ids_by_hash = _snapshot_push_rows()

    all_hashes = set(ro_ids_by_hash) | set(sample_ids_by_hash)
    if not all_hashes:
        return 0, 0

    口 = AutoStep(step_num=2, name="Mark rows in flight")
    _mark_in_flight(ro_ids_by_hash, sample_ids_by_hash)

    口 = AutoStep(step_num=3, name="Push each entry")
    errors = _push_entries(all_hashes, set(sample_ids_by_hash), project_root)

    口 = AutoStep(step_num=4, name="Record each row's outcome")
    pushed_count = _record_push_outcomes(ro_ids_by_hash, sample_ids_by_hash, errors)

    remaining = sum(1 for e in errors.values() if e is not None)
    return pushed_count, remaining


@workflow(
    purpose="push-lifecycle: tick every poll interval until stopped, backing off "
            "after a tick that pushed nothing and left failures, then drain once on stop",
    inputs="project root; a threading.Event that stops the loop",
    outputs="none; rows move from pending or failed through in flight to pushed or failed",
)
def _push_worker_loop(project_root: Path, stop_event) -> None:
    """Background loop: tick every ``PUSH_POLL_INTERVAL`` until stop_event set.

    Exponential backoff is applied when a tick yields zero successful
    pushes — the next tick waits longer.

    The orphan reset (:func:`_reset_orphan_pushes`) runs once before the
    loop starts, on the caller's thread (Execution's ``start_push_worker``).

    Args:
        project_root: wfc project root.
        stop_event: threading.Event; loop exits when set.
    """
    口 = Step(step_num=1, name="Tick until stopped",
             purpose="Run a tick every poll interval; after a tick that pushed "
                     "nothing and left failures, wait the next backoff instead")
    backoff_idx = 0
    while not stop_event.is_set():
        try:
            pushed, remaining = _push_worker_tick(project_root)
        except Exception as exc:
            print(f"[push_worker] tick raised: {exc}", file=sys.stderr)
            pushed, remaining = 0, 0
        if pushed:
            print(f"[push_worker] pushed {pushed} hashes ({remaining} remaining)")
            backoff_idx = 0  # reset on progress
        # No work and no failures -> short sleep; on repeated empty ticks
        # we don't expand backoff (it's a poll loop, not a retry loop).
        wait = PUSH_POLL_INTERVAL
        if remaining and not pushed:
            # All failures: back off.
            wait = PUSH_BACKOFFS[min(backoff_idx, len(PUSH_BACKOFFS) - 1)]
            backoff_idx += 1
        if stop_event.wait(wait):
            break
    口 = Step(step_num=2, name="Final tick on stop",
             purpose="Drain once more on a clean shutdown; a failure here is swallowed")
    # Final drain pass on clean shutdown.
    try:
        _push_worker_tick(project_root)
    except Exception:
        pass
