"""Run detail: what one run did, read for ``wfc show-run`` and any other door.

One answer to "what happened in this run": a recorded run's status, metrics,
outputs (slot, path and where its bytes are), error and traceback from its
``Run`` row, the paths of its two logs (Layout's ``run_log_path``) and the
last lines of its stderr. A draft string resolves through the draft-run
store (:func:`~wfc.execution.draft.resolve_draft_step`) to the same shape,
its logs in the draft step's results folder. :func:`run_log` reads one of
the two logs whole. Nothing is written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, task

from .. import layout
from ..persistence import project_root as get_project_root

#: How many of stderr's last lines a detail carries.
STDERR_TAIL_LINES = 40


class RunDetailError(ValueError):
    """The run id or draft string names no run; the message says why."""


@dataclass(frozen=True)
class RunDetailOutput:
    """One output of a run.

    Attributes:
        slot: The contract output slot it fills.
        path: The path recorded for it (a draft step's file in its draft's
            folder), or ``None``.
        state: Where its bytes are: ``local``, ``remote`` or ``missing``
            (Storage's ``output_location``); a draft step's file is
            ``local`` or ``missing``.
    """

    slot: str
    path: str | None
    state: str


@dataclass(frozen=True)
class RunDetail:
    """One run, as ``show-run`` prints it.

    Attributes:
        run: The run id, or the draft string as given back by the store.
        draft: Whether it is a draft step.
        status: The status as recorded (a run row's, or the draft step's).
        method: ``<module>.<method>`` (with `` v<N>`` for a recorded run that
            records a version label).
        sample: The sample, or ``None``.
        pipeline_id: The pipeline it ran in, or ``None``.
        node_id: The pipeline node, or ``None``.
        metrics: Its metrics.
        outputs: Its outputs, in record order.
        outputs_error: Why its outputs could not be listed (a malformed
            record), else ``None``.
        error: Its error message, or ``None``.
        traceback: Its error traceback, or ``None`` (a draft step keeps none).
        stdout_log: The path of its stdout log.
        stderr_log: The path of its stderr log.
        stderr_tail: The last lines of stderr (empty when there is no log).
        cache_source_run_id: For a cache hit, the run whose outputs it reused.
    """

    run: str
    draft: bool
    status: str
    method: str | None
    sample: str | None
    pipeline_id: str | None
    node_id: str | None
    metrics: dict[str, Any]
    outputs: tuple[RunDetailOutput, ...]
    outputs_error: str | None
    error: str | None
    traceback: str | None
    stdout_log: Path
    stderr_log: Path
    stderr_tail: tuple[str, ...] = field(default=())
    cache_source_run_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """The detail as plain JSON values, paths as strings."""
        return {
            "run": self.run, "draft": self.draft, "status": self.status,
            "method": self.method, "sample": self.sample,
            "pipeline_id": self.pipeline_id, "node_id": self.node_id,
            "metrics": self.metrics,
            "outputs": [{"slot": o.slot, "path": o.path, "state": o.state}
                        for o in self.outputs],
            "outputs_error": self.outputs_error,
            "error": self.error, "traceback": self.traceback,
            "stdout_log": str(self.stdout_log), "stderr_log": str(self.stderr_log),
            "stderr_tail": list(self.stderr_tail),
            "cache_source_run_id": self.cache_source_run_id,
        }


def log_tail(path: Path, lines: int = STDERR_TAIL_LINES) -> tuple[str, ...]:
    """The last *lines* lines of a log; none when the file is absent.

    Args:
        path: The log file.
        lines: How many lines to keep.

    Returns:
        The lines, without their line endings.
    """
    if not Path(path).is_file():
        return ()
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return tuple(text.splitlines()[-lines:])


@task(purpose="Read one run's detail: a recorded run's row, outputs and logs, "
              "or a draft step's entry, outputs and logs through the draft-run "
              "store, in one shape",
      inputs="a run id or a draft string, the project root",
      outputs="RunDetail: status, metrics, outputs (slot, path, where its bytes "
              "are), error, traceback, the two log paths and stderr's last lines",
      critical="reads only; an id or string naming no run is a RunDetailError")
def run_detail(run: str | int, root: Path | None = None) -> RunDetail:
    """One run's detail, by run id or draft string.

    Args:
        run: A recorded run's id, or a draft string
            (``draft:<module>.<method>/<node>@<sample>``).
        root: The project root; the resolved one when omitted.

    Returns:
        The run's :class:`RunDetail`.

    Raises:
        RunDetailError: The id is not a number or names no run, or the draft
            string names no draft step now (the store's words).
    """
    from .draft import DRAFT_REF_PREFIX

    root = Path(root) if root is not None else get_project_root()
    text = str(run).strip()

    口 = Step(step_num=1, name="Read a draft step",
             purpose="A draft string resolves through the draft-run store; its "
                     "logs and files are in its results folder")
    if text.startswith(DRAFT_REF_PREFIX):
        return _draft_detail(text, root)

    口 = Step(step_num=2, name="Read a recorded run",
             purpose="The run row, its method, its outputs and where their "
                     "bytes are, and its logs in the run archive")
    try:
        run_id = int(text)
    except ValueError:
        raise RunDetailError(
            f"{text!r} is not a run id or a draft string; `wfc runs` lists "
            f"both.") from None
    return _recorded_detail(run_id, root)


@task(purpose="Read one of a run's two logs whole, recorded run or draft step, "
              "from where its detail says the log is",
      inputs="a run id or a draft string, which log (stdout or stderr), the "
             "project root",
      outputs="the log's text",
      critical="reads only; a run with no such log file is a RunDetailError "
               "naming the path, never an empty log")
def run_log(run: str | int, which: str, root: Path | None = None) -> str:
    """One run's whole stdout or stderr log, by run id or draft string.

    Args:
        run: A recorded run's id, or a draft string.
        which: ``stdout`` or ``stderr``.
        root: The project root; the resolved one when omitted.

    Returns:
        The log's text (undecodable bytes replaced).

    Raises:
        RunDetailError: The run names no run (as :func:`run_detail`), or it
            has no such log file; the message names the path.
        ValueError: ``which`` is neither ``stdout`` nor ``stderr``.
    """
    if which not in ("stdout", "stderr"):
        raise ValueError(f"which must be 'stdout' or 'stderr', not {which!r}")
    口 = AutoStep(step_num=1, name="Find the run")
    detail = run_detail(run, root)
    口 = Step(step_num=2, name="Read the log",
             purpose="The whole file at the detail's log path; an absent file is "
                     "refused with its path")
    path = detail.stdout_log if which == "stdout" else detail.stderr_log
    if not path.is_file():
        raise RunDetailError(f"{detail.run} has no {which} log at {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _recorded_detail(run_id: int, root: Path) -> RunDetail:
    """A recorded run's detail from its row, its outputs and its archive's logs."""
    from ..persistence import Method, MethodVersion, Module, Run, get_session
    from ..storage import MalformedRecordError, output_location, output_records

    with get_session() as session:
        row = session.get(Run, run_id)
        if row is None:
            raise RunDetailError(f"Run {run_id} not found; `wfc runs` lists the runs.")
        method = session.get(Method, row.method_id) if row.method_id else None
        module = session.get(Module, method.module_id) if method else None
        version = session.get(MethodVersion, row.version_id) if row.version_id else None
        outputs: list[RunDetailOutput] = []
        outputs_error = None
        try:
            for record in output_records(run_id, project_dir=root, session=session):
                outputs.append(RunDetailOutput(
                    slot=str(record.slot), path=record.artifact_path,
                    state=output_location(record, root)))
        except MalformedRecordError as exc:
            outputs, outputs_error = [], str(exc)
        label = None
        if method is not None:
            label = f"{module.name if module else '?'}.{method.name}"
            if version is not None and version.label is not None:
                label += f" v{version.label}"
        detail = dict(status=row.status, sample=row.sample, pipeline_id=row.pipeline_id,
                      node_id=row.node_id, metrics=dict(row.metrics or {}),
                      error=row.error_message, traceback=row.error_traceback,
                      cache_source_run_id=row.cache_source_run_id)
    stderr_log = layout.run_log_path(root, run_id, "stderr")
    return RunDetail(run=str(run_id), draft=False, method=label,
                     outputs=tuple(outputs), outputs_error=outputs_error,
                     stdout_log=layout.run_log_path(root, run_id, "stdout"),
                     stderr_log=stderr_log, stderr_tail=log_tail(stderr_log),
                     **detail)


def _draft_detail(text: str, root: Path) -> RunDetail:
    """A draft step's detail from the draft-run store and its results folder."""
    from .draft import DraftRefError, resolve_draft_step

    try:
        ref, _record, step = resolve_draft_step(text, root)
    except DraftRefError as exc:
        raise RunDetailError(str(exc)) from exc
    folder = layout.draft_result_dir(root, ref.module, ref.method,
                                     str(step.get("node_id")), str(step.get("sample")))
    draft_folder = layout.draft_dir(root, ref.module, ref.method)
    outputs = []
    for slot, relpath in (step.get("outputs") or {}).items():
        path = draft_folder / str(relpath)
        outputs.append(RunDetailOutput(slot=str(slot), path=str(path),
                                       state="local" if path.exists() else "missing"))
    stderr_log = folder / layout.RUN_LOG_FILENAMES["stderr"]
    return RunDetail(
        run=ref.text, draft=True, status=str(step.get("status") or "-"),
        method=f"{step.get('module')}.{step.get('method')}",
        sample=ref.sample, pipeline_id=_record.get("pipeline_id"), node_id=ref.node_id,
        metrics=dict(step.get("metrics") or {}), outputs=tuple(outputs),
        outputs_error=None, error=step.get("error_message"), traceback=None,
        stdout_log=folder / layout.RUN_LOG_FILENAMES["stdout"],
        stderr_log=stderr_log, stderr_tail=log_tail(stderr_log))
