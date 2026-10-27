"""The canvas server's recovery of pipelines it does not own.

A pipeline whose owner stopped (the canvas server before a restart, or a
``wfc run-pipeline`` process cut off by Ctrl-C or a closed terminal) leaves
its pipeline record and its running rows behind, and nothing ends it. The
lifespan starts :class:`PipelineRecovery` on a daemon thread, and startup
does not wait for it. The thread takes a first look at every candidate at
once, so each one's stall window starts with the server; it then waits one
window and looks again, and again each window while the server runs:

- a candidate is a pipeline with a record whose owner is not this process,
  or one with running rows and no record (a pipeline from before records,
  judged by its heartbeats alone); never one in this server's job table;
- Execution judges each (``assess_pipeline``): a dead one is closed out
  (``close_out_pipeline``); an alive or undecided one is looked at again
  next window, until it ends by itself or is judged dead.

The window is the claim's stall window, read at each wait, or the one the
caller passes.

``reach_pipeline`` is how the status and cancel routes reach a pipeline that
is not in the job table: through its record, keeping what the status route
needs once the record is gone.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import AutoStep, Step, workflow
from sqlmodel import select

from .. import layout
from ..execution import (
    HeartbeatSampler,
    PipelineRecordError,
    ProcessState,
    Verdict,
    assess_pipeline,
    close_out_pipeline,
    frozen_step_map,
    list_pipeline_records,
    probe_process,
    read_pipeline_record,
)
from ..execution import claim as _claim
from ..persistence import Run, get_session
from . import state


def recovery_candidates(project_root: Path | str, owned: Collection[str]) -> list[str]:
    """The pipelines the recovery judges: every one this process does not own.

    Args:
        project_root: The served project's root.
        owned: The ids of the pipelines this server runs (its job table).

    Returns:
        The ids, sorted: each record whose owner is not this process, and
        each pipeline with running rows and no record, less ``owned``.
    """
    me = os.getpid()
    recorded: set[str] = set()
    candidates: set[str] = set()
    for record in list_pipeline_records(project_root):
        recorded.add(record.pipeline_id)
        mine = (record.owner.pid == me
                and probe_process(record.owner) is ProcessState.ALIVE)
        if not mine:
            candidates.add(record.pipeline_id)
    with get_session() as session:
        running = session.exec(
            select(Run.pipeline_id).where(Run.status == "running").distinct()
        ).all()
    candidates.update(pid for pid in running if pid and pid not in recorded)
    return sorted(candidates - set(owned))


class PipelineRecovery:
    """Watches the pipelines this server does not own, and closes out the dead.

    Attributes:
        project_root: The served project's root.
        closed: The ids this recovery closed out, in order.
    """

    def __init__(self, project_root: Path | str, *,
                 owned: Callable[[], Collection[str]],
                 window: float | None = None) -> None:
        """Set the recovery up; :meth:`start` runs it.

        Args:
            project_root: The served project's root.
            owned: Returns the ids of the pipelines this server runs, read
                at every look.
            window: The stall window in seconds; the claim's
                ``STALL_WINDOW_SECONDS`` (read at each wait) when None.
        """
        self.project_root = Path(project_root)
        self.closed: list[str] = []
        self._owned = owned
        self._window = window
        self._sampler = HeartbeatSampler(window=window)
        self._watched: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def window(self) -> float:
        """The stall window, in seconds, as it stands now."""
        return self._window if self._window is not None else _claim.STALL_WINDOW_SECONDS

    def start(self) -> threading.Thread:
        """Run the recovery on a daemon thread; returns at once.

        Returns:
            The thread.
        """
        self._thread = threading.Thread(target=self._run, name="wfc-pipeline-recovery",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to end, and wait for a look in flight to finish.

        Args:
            timeout: The most seconds to wait for the thread; past it the
                daemon thread is left to end at its next wait.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        """The thread: a first look now, then one look per window until stopped."""
        self._guarded(self.baseline)
        while not self._stop.wait(self.window()):
            self._guarded(self.look)

    def _guarded(self, action: Callable[[], object]) -> None:
        """Run one look, reporting what it raised; the thread carries on."""
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - the next window tries again
            print(f"[recovery] a look at the project's pipelines raised: {exc}",
                  file=sys.stderr)

    def baseline(self) -> None:
        """Start every candidate's stall window: read its heartbeats now."""
        for pipeline_id in recovery_candidates(self.project_root, self._owned()):
            self._sampler.observe(pipeline_id)
            self._watched.add(pipeline_id)

    @workflow(purpose="One look of the canvas server's recovery: judge every "
                      "pipeline this server does not own, close out the dead, "
                      "and keep watching the alive and the undecided",
              inputs="the project's pipeline records and running rows; the "
                     "server's job table",
              outputs="the ids closed out by this look",
              critical="never a pipeline this process owns or has in its job "
                       "table; the judgement and the close-out are Execution's")
    def look(self) -> list[str]:
        """Judge each candidate now, and close out the dead ones.

        Returns:
            The ids closed out by this look.
        """
        口 = Step(step_num=1, name="Collect the candidates",
                 purpose="Each record whose owner is not this process, and each "
                         "pipeline with running rows and no record, less the "
                         "server's job table")
        candidates = recovery_candidates(self.project_root, self._owned())

        口 = Step(step_num=2, name="Stop watching the pipelines that ended",
                 purpose="A pipeline that is no longer a candidate ended by "
                         "itself (its record removed, its rows ended) or became "
                         "this server's; its heartbeat watch goes")
        for gone in self._watched - set(candidates):
            self._sampler.forget(gone)
        self._watched = set(candidates)

        口 = Step(step_num=3, name="Judge each candidate, closing out the dead",
                 purpose="An alive or undecided pipeline is left as it is and "
                         "judged again at the next look")
        closed: list[str] = []
        for pipeline_id in candidates:
            口 = Step(step_num=3.1, name="Judge the pipeline",
                     purpose="Execution's dead-or-alive rule over the record's "
                             "owner and engine and the heartbeats watched since "
                             "the first look; a pipeline that cannot be judged "
                             "(an unreadable record) is reported and judged "
                             "again next look, and the others are still judged")
            try:
                verdict = assess_pipeline(self.project_root, pipeline_id, self._sampler)
            except Exception as exc:  # noqa: BLE001 - one bad record never stops the pass
                print(f"[recovery] judging pipeline {pipeline_id} raised: {exc}",
                      file=sys.stderr)
                continue
            if verdict is not Verdict.DEAD:
                continue
            try:
                口 = AutoStep(step_num=3.2, name="Close out a dead pipeline")
                close_out_pipeline(self.project_root, pipeline_id)
            except Exception as exc:  # noqa: BLE001 - tried again next window
                print(f"[recovery] closing out pipeline {pipeline_id} raised: {exc}",
                      file=sys.stderr)
                continue
            print(f"[recovery] closed out pipeline {pipeline_id}: nothing was "
                  f"running it any more")
            self._sampler.forget(pipeline_id)
            self._watched.discard(pipeline_id)
            closed.append(pipeline_id)
        self.closed.extend(closed)
        return closed


@dataclass(frozen=True)
class ReachedPipeline:
    """A pipeline this server does not run in memory, as its routes see it.

    Attributes:
        pipeline_id: The pipeline.
        step_map: Each node of its frozen document mapped to its method.
        log_dir: Its run directory, where its log is.
        live: Whether its record is still there: its owner has not finished
            its end steps, or the recovery has not closed it out yet. It
            stands for the run thread's liveness in the status route, so a
            node without rows reads pending while it is live, as History
            counts it pending while the record exists.
        cancel_requested: Whether the record asks for a cancel.
    """

    pipeline_id: str
    step_map: dict[str, str]
    log_dir: str
    live: bool
    cancel_requested: bool


def reach_pipeline(project_root: Path | str, pipeline_id: str) -> ReachedPipeline | None:
    """Reach a pipeline through its record, for the status and cancel routes.

    The first time a pipeline is reached its step map (from the frozen
    document) and its log directory are kept in the server's table of
    reached pipelines, so the routes keep answering from its rows once its
    record is gone, as they do for an ended in-memory job.

    Args:
        project_root: The served project's root.
        pipeline_id: The pipeline (the job id).

    Returns:
        The pipeline as reached, or None when it has no record and was
        never reached by this server.
    """
    try:
        record = read_pipeline_record(project_root, pipeline_id)
    except PipelineRecordError as exc:
        print(f"[recovery] {exc}", file=sys.stderr)
        record = None
    entry = state._reached_pipelines.get(pipeline_id)
    if record is None and entry is None:
        return None
    if entry is None:
        entry = {
            "step_map": frozen_step_map(project_root, pipeline_id) or {},
            "log_dir": str(layout.pipeline_run_dir(Path(project_root), pipeline_id)),
        }
        state._reached_pipelines[pipeline_id] = entry
    return ReachedPipeline(
        pipeline_id=pipeline_id,
        step_map=dict(entry["step_map"]),
        log_dir=str(entry["log_dir"]),
        live=record is not None,
        cancel_requested=bool(record is not None and record.cancel_requested),
    )


def start_recovery(project_root: Path | str, *,
                   owned: Callable[[], Collection[str]],
                   window: float | None = None) -> PipelineRecovery:
    """Start the recovery on its daemon thread; returns at once.

    Args:
        project_root: The served project's root.
        owned: Returns the ids of the pipelines this server runs.
        window: The stall window in seconds; the claim's when None.

    Returns:
        The running recovery; :meth:`PipelineRecovery.stop` ends it.
    """
    recovery = PipelineRecovery(project_root, owned=owned, window=window)
    recovery.start()
    return recovery
