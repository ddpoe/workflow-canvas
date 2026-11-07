"""Dead or alive, the close-out and the cancel of a recorded pipeline.

A pipeline is **alive** while its record's owning process (the one running
``run_pipeline``) is still that same live process, while its recorded engine
is, or while one of its running rows' heartbeats moves. Otherwise it is
**dead**, once one stall window (the claim's ``STALL_WINDOW_SECONDS``) has
passed on this process's monotonic clock since it was first looked at. A
process whose pid is live but whose creation time differs is another process
that reused the pid: it counts as gone, and it is never killed. A pipeline
with no record is judged by its heartbeats alone; a running row with no
heartbeat counts as still. ``judge_liveness`` is that rule over plain values;
``probe_process`` and ``HeartbeatSampler`` read its inputs; ``assess_pipeline``
puts them together for one pipeline.

``close_out_pipeline`` ends a dead pipeline: its running rows fail with
"Interrupted: the server stopped", the pipeline-end walk gives every target
still without a row its reason (``upstream_failed`` below a failed run,
otherwise ``interrupted``), Storage releases what the pipeline held, the
record goes, and the pipeline's status and end are stored once the record is
gone. When the record says a cancel was asked for, the user's reason wins:
the running rows are cancelled for the user and the walk says ``user``.

``cancel_recorded_pipeline`` is the one way a pipeline is cancelled through
its record: it sets the record's cancel flag, kills the engine's process tree
when its identity matches, and cancels the running rows; when the owner is
still alive its own end steps then walk with ``user``, and when it is gone
this operation closes the pipeline out with ``user`` itself. Never two walks.

Both take plain values (a project root, a pipeline id) and are idempotent.
They are serialised per pipeline across processes by the close-out lock file
beside the pipeline record (``close_out_lock``), so ``wfc cancel`` in a
terminal and the canvas server's recovery never both walk one pipeline: one
walk, one release, one record removal. The cancel sets its flag before it
waits for the lock, so a close-out that holds the lock reads the flag and the
cancel's reason wins whichever takes the lock first.
"""
from __future__ import annotations

import enum
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import select

from .. import layout
from ..persistence import (
    CANCEL_REASON_INTERRUPTED,
    Run,
    get_session,
)
from ..storage import end_pipeline_samples, release_finished_runs
from . import claim as _claim
from .lifecycle import _write_cancelled_rows, cancel_pipeline, fail_pipeline
from .pipeline_outcome import store_pipeline_outcome
from .pipeline_record import (
    PipelineRecord,
    ProcessIdentity,
    hold_lock_file,
    is_pipeline_id,
    process_identity,
    read_pipeline_record,
    remove_pipeline_record,
    request_cancel,
)

#: The error message of a run a close-out failed: it was running when the
#: server (or the CLI process) running its pipeline stopped.
INTERRUPTED_RUN_MESSAGE = "Interrupted: the server stopped"

#: Seconds two readings of one process's creation time may differ by and
#: still name the same process (a platform may derive the time from a boot
#: time it re-reads); a pid reused this close to its predecessor's start is
#: not a case a pipeline meets.
CREATED_TOLERANCE_SECONDS = 1.0

#: Seconds the kill waits for a terminated process tree before killing what
#: is left of it.
_TERMINATE_GRACE_SECONDS = 2.0


class UnknownPipelineError(LookupError):
    """The pipeline has no record and no running row: there is nothing to cancel."""


class ProcessState(enum.Enum):
    """What became of a recorded process."""

    #: The recorded process is still running.
    ALIVE = "alive"
    #: No process has the recorded pid (or its creation time cannot be read).
    GONE = "gone"
    #: Another process has the recorded pid: its creation time differs.
    REUSED = "reused"
    #: No process was recorded (no record, or no engine yet).
    UNKNOWN = "unknown"


class Verdict(enum.Enum):
    """Whether a pipeline is still being run."""

    #: Its owner or engine is alive, or a running row's heartbeat moved.
    ALIVE = "alive"
    #: Nothing is running it, and one stall window has passed.
    DEAD = "dead"
    #: Nothing shows it alive yet, but the window has not passed.
    UNDECIDED = "undecided"


def probe_process(identity: ProcessIdentity | None) -> ProcessState:
    """Find out what became of a recorded process.

    Args:
        identity: The process as the record names it, or None when nothing
            was recorded.

    Returns:
        ``UNKNOWN`` for no identity; ``GONE`` when the recorded creation
        time is unknown or no live process has the pid; ``REUSED`` when the
        live process with the pid was created at another time; ``ALIVE``
        otherwise.
    """
    if identity is None:
        return ProcessState.UNKNOWN
    if identity.created is None:
        return ProcessState.GONE
    now = process_identity(identity.pid)
    if now.created is None:
        return ProcessState.GONE
    if abs(now.created - identity.created) > CREATED_TOLERANCE_SECONDS:
        return ProcessState.REUSED
    return ProcessState.ALIVE


@task(purpose="The dead-or-alive rule: a pipeline is alive while its owner or "
              "its engine is that same live process, or while a running row's "
              "heartbeat moves; otherwise it is dead once the stall window has "
              "passed, and undecided before",
      inputs="the owner's and the engine's process state, whether a heartbeat "
             "moved, whether the pipeline has running rows, whether the window "
             "has passed",
      outputs="Verdict: alive, dead or undecided",
      critical="a reused pid and an unknown process count as gone; a heartbeat "
               "counts only on a running row")
def judge_liveness(owner: ProcessState, engine: ProcessState, *,
                   heartbeat_moved: bool, has_running_rows: bool,
                   window_passed: bool) -> Verdict:
    """Decide whether a pipeline is still being run.

    Args:
        owner: What became of the record's owning process (``UNKNOWN`` for
            a pipeline with no record).
        engine: What became of the record's engine process.
        heartbeat_moved: Whether a running row's heartbeat moved since the
            last look.
        has_running_rows: Whether the pipeline has a running row.
        window_passed: Whether one stall window has passed with nothing
            moving.

    Returns:
        The verdict.
    """
    if ProcessState.ALIVE in (owner, engine):
        return Verdict.ALIVE
    if has_running_rows and heartbeat_moved:
        return Verdict.ALIVE
    return Verdict.DEAD if window_passed else Verdict.UNDECIDED


@dataclass(frozen=True)
class HeartbeatReading:
    """One look at a pipeline's running rows.

    Attributes:
        has_running_rows: Whether the pipeline has a running row.
        moved: Whether a running row's heartbeat differs from the last look
            (a row first seen running with a heartbeat counts as moving).
        window_passed: Whether one stall window has passed since the
            heartbeats last moved (or since the first look).
    """

    has_running_rows: bool
    moved: bool
    window_passed: bool


@dataclass
class _Watch:
    beats: dict[int, int | None]
    still_since: float


@dataclass
class HeartbeatSampler:
    """Watches pipelines' running-row heartbeats for movement.

    Only movement counts: each change restarts the pipeline's still timer,
    which runs on this process's monotonic clock, so no clock is compared
    between writers. A running row with no heartbeat never moves.

    Attributes:
        window: The stall window in seconds; the claim's
            ``STALL_WINDOW_SECONDS`` (read at each look) when None.
        clock: The monotonic clock.
    """

    window: float | None = None
    clock: Callable[[], float] = time.monotonic
    _watches: dict[str, _Watch] = field(default_factory=dict)

    def observe(self, pipeline_id: str) -> HeartbeatReading:
        """Look at a pipeline's running rows now.

        Args:
            pipeline_id: The pipeline.

        Returns:
            What this look saw.
        """
        with get_session() as session:
            rows = session.exec(
                select(Run.id, Run.heartbeat)
                .where(Run.pipeline_id == pipeline_id)
                .where(Run.status == "running")
            ).all()
        beats = {int(run_id): beat for run_id, beat in rows}
        now = self.clock()
        window = self.window if self.window is not None else _claim.STALL_WINDOW_SECONDS
        watch = self._watches.get(pipeline_id)
        if watch is None:
            self._watches[pipeline_id] = _Watch(beats=beats, still_since=now)
            return HeartbeatReading(bool(beats), False, window <= 0)
        moved = any(
            beat is not None and (run_id not in watch.beats or watch.beats[run_id] != beat)
            for run_id, beat in beats.items())
        watch.beats = beats
        if moved:
            watch.still_since = now
        return HeartbeatReading(bool(beats), moved, now - watch.still_since >= window)

    def forget(self, pipeline_id: str) -> None:
        """Stop watching a pipeline (it ended, or was closed out).

        Args:
            pipeline_id: The pipeline.
        """
        self._watches.pop(pipeline_id, None)


def assess_pipeline(project_root: Path | str, pipeline_id: str,
                    sampler: HeartbeatSampler) -> Verdict:
    """Judge one pipeline dead or alive now.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.
        sampler: The sampler watching it since the first look.

    Returns:
        The verdict of :func:`judge_liveness` over the record's owner and
        engine and the sampler's reading.
    """
    record = read_pipeline_record(project_root, pipeline_id)
    reading = sampler.observe(pipeline_id)
    return judge_liveness(
        probe_process(record.owner if record else None),
        probe_process(record.engine if record else None),
        heartbeat_moved=reading.moved,
        has_running_rows=reading.has_running_rows,
        window_passed=reading.window_passed,
    )


@task(purpose="Kill a recorded process and its descendants, only when the live "
              "process with its pid is the one recorded: terminate the tree, "
              "then kill what is left after a grace period",
      inputs="the recorded process identity",
      outputs="True when the tree was killed, False when the process was gone, "
              "the pid now names another process, or the kill failed",
      critical="a pid whose creation time differs is never touched; a kill "
               "error is printed and contained, never raised")
def kill_process_tree(identity: ProcessIdentity | None) -> bool:
    """Kill a recorded process's tree when the process is still the one recorded.

    A process that vanishes during the kill is skipped. Any other error the
    kill meets (access denied, say) is printed, the rest of the tree is
    still tried, and the answer is False: the tree may not be dead.

    Args:
        identity: The process as recorded, or None.

    Returns:
        True when the tree was terminated or killed without an error.
    """
    import psutil

    if probe_process(identity) is not ProcessState.ALIVE:
        return False
    assert identity is not None
    errors: list[str] = []

    def _signal(proc, how: str) -> None:
        try:
            getattr(proc, how)()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as exc:
            errors.append(f"{how} {proc.pid}: {exc!r}")

    try:
        parent = psutil.Process(identity.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return False
    except psutil.Error as exc:
        print(f"[kill] could not read process {identity.pid}'s tree: {exc!r}",
              file=sys.stderr)
        return False
    procs = [*children, parent]
    for proc in procs:
        _signal(proc, "terminate")
    try:
        _, alive = psutil.wait_procs(procs, timeout=_TERMINATE_GRACE_SECONDS)
        for proc in alive:
            _signal(proc, "kill")
        psutil.wait_procs(alive, timeout=_TERMINATE_GRACE_SECONDS)
    except psutil.Error as exc:
        errors.append(f"wait: {exc!r}")
    if errors:
        print(f"[kill] killing process {identity.pid}'s tree met errors: "
              f"{'; '.join(errors)}", file=sys.stderr)
        return False
    return True


_locks: dict[str, threading.RLock] = {}
_depths: dict[str, int] = {}
_locks_guard = threading.Lock()


@task(
    purpose="Serialise the close-out and the cancel of one pipeline, across "
            "threads (a re-entrant lock) and processes (the close-out lock file)",
    inputs="project_root, pipeline_id",
    outputs="the lock held for the block",
    critical="The lock file is taken over only when the process holding it is "
             "gone; a run dir this call made is removed again when empty, and a "
             "waiter whose run dir was removed that way makes it again",
)
@contextmanager
def close_out_lock(project_root: Path | str, pipeline_id: str) -> Iterator[None]:
    """Hold the lock that serialises the close-out and the cancel of one pipeline.

    Across processes it is the close-out lock file beside the pipeline record
    (Layout's ``pipeline_close_out_lock_path``), so ``wfc cancel`` in a
    terminal and the canvas server's recovery never walk one pipeline at the
    same time. Within this process a re-entrant lock stands in front of it:
    threads queue on it, and the cancel closes the pipeline out while it
    holds the lock. A missing run dir is made for the lock file and removed
    again when it is left empty; a waiter in another process whose run dir
    was removed that way while it waited makes it again. An id that is not a
    pipeline id never forms a path and is locked within this process only.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Yields:
        Nothing; the lock is held until the block ends.
    """
    root = Path(project_root)
    key = f"{root.resolve()}::{pipeline_id}"
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        depth = _depths.get(key, 0)
        _depths[key] = depth + 1
        try:
            if depth or not is_pipeline_id(pipeline_id):
                yield
            else:
                run_dir = layout.pipeline_run_dir(root, pipeline_id)
                held = ExitStack()
                while True:
                    made = not run_dir.is_dir()
                    run_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        held.enter_context(hold_lock_file(
                            layout.pipeline_close_out_lock_path(root, pipeline_id)))
                    except FileNotFoundError:
                        # The holder this caller waited on (another process)
                        # removed the run dir it had made; make it again.
                        continue
                    break
                try:
                    with held:
                        yield
                finally:
                    if made:
                        try:
                            run_dir.rmdir()
                        except OSError:
                            pass
        finally:
            if depth:
                _depths[key] = depth
            else:
                _depths.pop(key, None)


def _has_running_row(pipeline_id: str) -> bool:
    """Whether the pipeline has a running row."""
    with get_session() as session:
        return session.exec(
            select(Run.id).where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")).first() is not None


def _frozen_document_hash(project_root: Path | str, pipeline_id: str) -> str | None:
    """The canonical-JSON hash of the pipeline's frozen document, as the launch kept it."""
    from ..identity import canonical_json_hash

    path = layout.pipeline_doc_path(Path(project_root), pipeline_id)
    try:
        return canonical_json_hash(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class CloseOut:
    """What a close-out did.

    Attributes:
        pipeline_id: The pipeline.
        runs_failed: The running rows it failed as interrupted.
        rows_written: The rows the pipeline-end walk wrote.
        user_cancelled: Whether the walk gave the ``user`` reason.
        samples_removed: The sample views Storage removed.
        record_removed: Whether a record was removed.
        runs_cancelled: The running rows it cancelled for the user (a close-out
            with the user's reason cancels them instead of failing them).
        closed: False when nothing was left to close: the pipeline had no
            record and no running row (another closer ended it first), so
            nothing was written, released or removed.
    """

    pipeline_id: str
    runs_failed: list[int]
    rows_written: int
    user_cancelled: bool
    samples_removed: list[str]
    record_removed: bool
    runs_cancelled: int = 0
    closed: bool = True


@workflow(purpose="Close out a pipeline nothing is running any more: end its "
                  "running rows (failed as interrupted, or cancelled for the "
                  "user when a cancel was asked for), write a row for every "
                  "target still without one, ask Storage to release what the "
                  "pipeline held, remove its record, and store its status and end",
          inputs="project root, pipeline id; the pipeline's record and rows",
          outputs="CloseOut: the rows ended and written, the samples removed",
          critical="only for a pipeline judged dead (or whose owner is gone after "
                   "a cancel); idempotent and serialised per pipeline across "
                   "processes by the close-out lock file, so one walk, one "
                   "release and one record removal. A pipeline with no record "
                   "and no running row is left as it is. The record goes last, "
                   "so a close-out that fails part-way is tried again")
def close_out_pipeline(project_root: Path | str, pipeline_id: str, *,
                       user_cancelled: bool = False) -> CloseOut:
    """End a pipeline whose owner and engine are gone.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.
        user_cancelled: Whether the caller is cancelling it for the user; the
            record's cancel flag counts the same.

    Returns:
        What the close-out did; a second call (or one after another process
        closed the pipeline out) finds nothing to close and does nothing.
    """
    with close_out_lock(project_root, pipeline_id):
        口 = Step(step_num=1, name="Read the record's cancel flag",
                 purpose="A cancel the user asked for (the flag, or the cancel "
                         "operation calling) makes the reason user; otherwise "
                         "it is interrupted, also for a pipeline with no record "
                         "(one from before records). Read under the lock, after "
                         "a cancel in another process set the flag, so the "
                         "cancel's reason wins. A pipeline with no record and no "
                         "running row was closed already: nothing is done")
        record: PipelineRecord | None = read_pipeline_record(project_root, pipeline_id)
        if record is None and not _has_running_row(pipeline_id):
            return CloseOut(pipeline_id=pipeline_id, runs_failed=[], rows_written=0,
                            user_cancelled=user_cancelled, samples_removed=[],
                            record_removed=False, closed=False)
        user = user_cancelled or bool(record is not None and record.cancel_requested)

        口 = Step(step_num=2, name="End the running rows",
                 purpose="With the user's reason, cancel_pipeline cancels each "
                         "running row for the user, as the cancel does; "
                         "otherwise fail_pipeline fails each as interrupted, "
                         "with no traceback")
        failed: list[int] = []
        cancelled = 0
        if user:
            cancelled = cancel_pipeline(pipeline_id, str(project_root))
        else:
            failed = fail_pipeline(pipeline_id, message=INTERRUPTED_RUN_MESSAGE,
                                   traceback_text=None, project_root=project_root)

        口 = Step(step_num=3, name="Hash the frozen document",
                 purpose="The walk stamps its rows with the launch's document "
                         "hash: the canonical hash of the frozen file, which the "
                         "launch kept in the cache")
        doc_hash = _frozen_document_hash(project_root, pipeline_id)

        口 = AutoStep(step_num=4, name="Write a row for every target without one")
        written = _write_cancelled_rows(
            pipeline_id, str(project_root), doc_hash, user_cancelled=user,
            no_cause_reason=CANCEL_REASON_INTERRUPTED)

        removed: list[str] = []
        try:
            口 = AutoStep(step_num=5, name="Release the pipeline's samples and records")
            removed = end_pipeline_samples(project_root, pipeline_id)
        except Exception as exc:  # noqa: BLE001 - best-effort, as every ending
            print(f"[close-out] releasing pipeline {pipeline_id}'s samples raised: "
                  f"{exc}", file=sys.stderr)

        口 = Step(step_num=6, name="Release the records bound to ended runs",
                 purpose="The rows step 2 ended held their claims' in-use "
                         "records; Storage releases every record bound to a run "
                         "that is no longer running")
        try:
            release_finished_runs()
        except Exception as exc:  # noqa: BLE001 - prune releases them too
            print(f"[close-out] releasing ended runs' records raised: {exc}",
                  file=sys.stderr)

        口 = AutoStep(step_num=7, name="Remove the record")
        gone = remove_pipeline_record(project_root, pipeline_id)

        口 = AutoStep(step_num=8, name="Store the pipeline's status and end")
        store_pipeline_outcome(project_root, pipeline_id)
        return CloseOut(pipeline_id=pipeline_id, runs_failed=failed,
                        rows_written=written, user_cancelled=user,
                        samples_removed=removed, record_removed=gone,
                        runs_cancelled=cancelled)


@dataclass(frozen=True)
class CancelOutcome:
    """What a cancel did.

    Attributes:
        pipeline_id: The pipeline.
        had_record: Whether the pipeline had a record.
        engine_killed: Whether the engine's process tree was killed.
        runs_cancelled: The running rows cancelled for the user.
        owner_alive: Whether the owner is alive: its own end steps then walk.
        close_out: The close-out this operation ran (owner gone), or None.
        ended_before: Whether the pipeline ended between the cancel's flag and
            its turn at the lock (another process closed it out, with the
            user's reason, or its owner finished): nothing was left to cancel.
    """

    pipeline_id: str
    had_record: bool
    engine_killed: bool
    runs_cancelled: int
    owner_alive: bool
    close_out: CloseOut | None
    ended_before: bool = False


@workflow(purpose="Cancel a pipeline through its record: set the cancel flag "
                  "before taking the close-out lock, then kill the engine's "
                  "process tree when its identity matches and cancel the "
                  "running rows for the user; then, only when the owner is "
                  "gone, walk with user, release and remove the record",
          inputs="project root, pipeline id",
          outputs="CancelOutcome",
          critical="never two walks: a live owner's end steps walk with user "
                   "(its cancel probe reads the flag); a pid whose creation time "
                   "differs is never killed; serialised per pipeline with the "
                   "close-out across processes, and a close-out that takes the "
                   "lock first reads the flag, so the cancel's reason wins")
def cancel_recorded_pipeline(project_root: Path | str, pipeline_id: str) -> CancelOutcome:
    """Cancel a pipeline, whichever process runs it.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        What the cancel did.

    Raises:
        UnknownPipelineError: The pipeline has no record and no running row.
    """
    口 = Step(step_num=1, name="Set the record's cancel flag",
             purpose="Before the close-out lock: the owner's cancel probe reads "
                     "the flag, so a live owner in another process learns of the "
                     "cancel, and a close-out holding the lock in another "
                     "process reads it and walks with the user's reason")
    announced = request_cancel(project_root, pipeline_id)
    with close_out_lock(project_root, pipeline_id):
        口 = Step(step_num=2, name="Read the pipeline again under the lock",
                 purpose="A pipeline that ended while the cancel waited (no "
                         "record and no running row any more) is left as it "
                         "ended; one with neither from the start is refused; a "
                         "record written since the flag gets the flag")
        record = read_pipeline_record(project_root, pipeline_id)
        if record is None and not _has_running_row(pipeline_id):
            if announced is None:
                raise UnknownPipelineError(
                    f"pipeline {pipeline_id} has no record and no running step: "
                    f"nothing to cancel")
            return CancelOutcome(pipeline_id=pipeline_id, had_record=True,
                                 engine_killed=False, runs_cancelled=0,
                                 owner_alive=False, close_out=None, ended_before=True)
        if record is not None and not record.cancel_requested:
            record = request_cancel(project_root, pipeline_id) or record

        killed = False
        if record is not None:
            # A kill that fails is printed and the cancel carries on: the
            # rows still end cancelled for the user.
            try:
                口 = AutoStep(step_num=3, name="Kill the engine's tree on identity match")
                killed = kill_process_tree(record.engine)
            except Exception as exc:  # noqa: BLE001 - the cancel must reach its rows
                print(f"[cancel] killing pipeline {pipeline_id}'s engine raised: "
                      f"{exc!r}", file=sys.stderr)

        口 = Step(step_num=4, name="Cancel the running rows for the user",
                 purpose="cancel_pipeline flips each running row to cancelled "
                         "with the user reason and removes the samples the "
                         "pipeline restored")
        flipped = cancel_pipeline(pipeline_id, str(project_root))

        owner_alive = (record is not None
                       and probe_process(record.owner) is ProcessState.ALIVE)
        closed: CloseOut | None = None
        if not owner_alive:
            口 = AutoStep(step_num=5, name="Owner gone: close out with the user reason")
            closed = close_out_pipeline(project_root, pipeline_id, user_cancelled=True)
        return CancelOutcome(pipeline_id=pipeline_id, had_record=record is not None,
                             engine_killed=killed, runs_cancelled=flipped,
                             owner_alive=owner_alive, close_out=closed)
