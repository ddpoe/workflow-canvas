"""The pipeline record: the durable fact that a pipeline is running.

``run_pipeline`` writes one into the pipeline run dir once ``prepare_launch``
has fixed the pipeline id, adds the engine's identity when the engine starts,
and removes it after its own end steps, on every ending. So a pipeline has a
record from the canvas and from ``wfc run-pipeline`` alike, and only while
its owner is running it.

The record names which entry point started the pipeline, the owning process
(the one running ``run_pipeline``) and the engine process, each by its pid and
its creation time, so a reused pid never reads as the same process; and the
cancel flag a canceller in another process sets and the owner's cancel probe
reads. It answers liveness and the cancel flag only: the run rows stay the
truth of each run's state. Its path is Layout's; every write replaces the
whole file, so a reader never sees half of one.

The step map a status route needs for a recorded pipeline is not stored: it
comes from the frozen ``pipeline.json`` (``frozen_step_map``).
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from axiom_annotations import task

from .. import layout

#: The entry point a pipeline was started from: the ``wfc run-pipeline`` verb
#: (and any headless caller of ``run_pipeline``), or the canvas server.
ENTRY_POINT_CLI = "cli"
ENTRY_POINT_CANVAS = "canvas"

#: How often, and how long apart, a write or read retries a file another
#: process holds open (Windows refuses a replace over an open file).
_RETRIES = 20
_RETRY_SECONDS = 0.025


#: What a pipeline id may be: one path segment of letters, digits, ``_``,
#: ``-`` and ``.``, starting with a letter or digit (a UUID, or a name such
#: as ``run-2``), so it never names a path outside its run dir.
_PIPELINE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class PipelineRecordError(RuntimeError):
    """The pipeline record could not be written, or what is on disk is not one."""


def is_pipeline_id(text: str) -> bool:
    """Whether ``text`` can be a pipeline id, and so may form a record path.

    Args:
        text: An id from a request, a command line or a file name.

    Returns:
        True for one path segment of letters, digits, ``_``, ``-`` and
        ``.`` that starts with a letter or digit (at most 128 characters);
        False for anything else, a separator or ``..`` among them.
    """
    return isinstance(text, str) and _PIPELINE_ID.fullmatch(text) is not None


@dataclass(frozen=True)
class ProcessIdentity:
    """One process, named so that a reused pid is never mistaken for it.

    Attributes:
        pid: The process id.
        created: The process's creation time as psutil reports it (seconds
            since the epoch), or None when it could not be read (the process
            was already gone).
    """

    pid: int
    created: float | None

    def to_json(self) -> dict[str, Any]:
        """The identity as the record stores it."""
        return {"pid": self.pid, "created": self.created}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ProcessIdentity:
        """Read an identity the record stored.

        Args:
            data: ``{"pid": int, "created": float | None}``.

        Returns:
            The identity.
        """
        created = data.get("created")
        return cls(pid=int(data["pid"]),
                   created=float(created) if created is not None else None)


def process_identity(pid: int) -> ProcessIdentity:
    """Name the live process ``pid`` by its pid and creation time.

    Args:
        pid: The process id.

    Returns:
        Its identity; ``created`` is None when psutil cannot see the process.
    """
    import psutil

    try:
        created: float | None = psutil.Process(pid).create_time()
    except (psutil.Error, ValueError, OSError):
        created = None
    return ProcessIdentity(pid=pid, created=created)


@dataclass(frozen=True)
class PipelineRecord:
    """What the pipeline record holds.

    Attributes:
        pipeline_id: The pipeline it records.
        entry_point: ``cli`` or ``canvas``: what started it.
        owner: The process running ``run_pipeline`` for it.
        engine: The engine process, once it has started.
        cancel_requested: Whether a cancel was asked for.
    """

    pipeline_id: str
    entry_point: str
    owner: ProcessIdentity
    engine: ProcessIdentity | None = None
    cancel_requested: bool = False

    def to_json(self) -> dict[str, Any]:
        """The record as it is written to disk."""
        return {
            "pipeline_id": self.pipeline_id,
            "entry_point": self.entry_point,
            "owner": self.owner.to_json(),
            "engine": self.engine.to_json() if self.engine is not None else None,
            "cancel_requested": self.cancel_requested,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PipelineRecord:
        """Read a record from its on-disk form.

        Args:
            data: What ``to_json`` wrote.

        Returns:
            The record.
        """
        engine = data.get("engine")
        return cls(
            pipeline_id=str(data["pipeline_id"]),
            entry_point=str(data["entry_point"]),
            owner=ProcessIdentity.from_json(data["owner"]),
            engine=ProcessIdentity.from_json(engine) if engine else None,
            cancel_requested=bool(data.get("cancel_requested", False)),
        )


def _retrying(action: Callable[[], Any]) -> Any:
    """Run a file action, retrying while another process holds the file.

    Args:
        action: The action; a ``PermissionError`` is retried, briefly.

    Returns:
        What the action returns.
    """
    for _ in range(_RETRIES - 1):
        try:
            return action()
        except PermissionError:
            time.sleep(_RETRY_SECONDS)
    return action()


def _record_path(project_root: Path | str, pipeline_id: str) -> Path | None:
    """The record file of a pipeline, or None when the id is not a pipeline id.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        Layout's record path; None for an id :func:`is_pipeline_id` refuses,
        which never forms a path.
    """
    if not is_pipeline_id(pipeline_id):
        return None
    return layout.pipeline_record_path(Path(project_root), pipeline_id)


def _store(project_root: Path, record: PipelineRecord) -> None:
    """Replace the record file with ``record``, whole.

    Args:
        project_root: The wfc project root.
        record: The record to write.

    Raises:
        PipelineRecordError: The file could not be written, or the id is not
            a pipeline id.
    """
    path = _record_path(project_root, record.pipeline_id)
    if path is None:
        raise PipelineRecordError(
            f"{record.pipeline_id!r} is not a pipeline id: no record written")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(record.to_json(), indent=2), encoding="utf-8")
        _retrying(lambda: os.replace(tmp, path))
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PipelineRecordError(
            f"could not write the pipeline record {path}: {exc}") from exc


@task(purpose="Write a pipeline's record into its run dir: the pipeline id, the "
              "entry point, this process as its owner, no engine yet, no cancel",
      inputs="project root, pipeline id, entry point",
      outputs="the record written; PipelineRecordError when it could not be",
      critical="the whole file is replaced, so a reader never sees half a record")
def write_pipeline_record(
    project_root: Path | str,
    pipeline_id: str,
    entry_point: str,
    owner: ProcessIdentity | None = None,
) -> PipelineRecord:
    """Write the record of a pipeline this process is about to run.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline; its run dir must exist (``prepare_launch``
            made it).
        entry_point: ``ENTRY_POINT_CLI`` or ``ENTRY_POINT_CANVAS``.
        owner: The owning process; this process when omitted.

    Returns:
        The record written.

    Raises:
        PipelineRecordError: The file could not be written.
    """
    record = PipelineRecord(
        pipeline_id=pipeline_id,
        entry_point=entry_point,
        owner=owner if owner is not None else process_identity(os.getpid()),
    )
    _store(Path(project_root), record)
    return record


def read_pipeline_record(project_root: Path | str, pipeline_id: str) -> PipelineRecord | None:
    """Read a pipeline's record.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        The record, or None when the pipeline has none (an id that is not a
        pipeline id has none).

    Raises:
        PipelineRecordError: The file exists but is not a record.
    """
    path = _record_path(project_root, pipeline_id)
    if path is None:
        return None
    try:
        text = _retrying(lambda: path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PipelineRecordError(f"could not read the pipeline record {path}: {exc}") from exc
    try:
        return PipelineRecord.from_json(json.loads(text))
    except (ValueError, KeyError, TypeError) as exc:
        raise PipelineRecordError(f"{path} is not a pipeline record: {exc}") from exc


def list_pipeline_records(project_root: Path | str) -> list[PipelineRecord]:
    """Every pipeline record in the project, read now.

    A file that is not a record is skipped, with a line on stderr naming it.

    Args:
        project_root: The wfc project root.

    Returns:
        The records, in pipeline-id order.
    """
    import sys

    pipelines = layout.pipelines_dir(Path(project_root))
    if not pipelines.is_dir():
        return []
    records: list[PipelineRecord] = []
    for path in sorted(pipelines.glob(f"*/{layout.PIPELINE_RECORD_FILENAME}")):
        try:
            record = read_pipeline_record(project_root, path.parent.name)
        except PipelineRecordError as exc:
            print(f"[pipeline_record] skipped: {exc}", file=sys.stderr)
            continue
        if record is not None:
            records.append(record)
    return records


@task(purpose="Add the engine's identity (pid and creation time) to a pipeline's "
              "record, keeping what else it holds",
      inputs="project root, pipeline id, the engine's pid",
      outputs="the updated record, or None when the pipeline has no record")
def record_engine(project_root: Path | str, pipeline_id: str, pid: int) -> PipelineRecord | None:
    """Record the engine process of a running pipeline.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.
        pid: The engine's process id.

    Returns:
        The updated record, or None when the pipeline has none.

    Raises:
        PipelineRecordError: The record could not be read or written.
    """
    record = read_pipeline_record(project_root, pipeline_id)
    if record is None:
        return None
    updated = replace(record, engine=process_identity(pid))
    _store(Path(project_root), updated)
    return updated


def request_cancel(project_root: Path | str, pipeline_id: str) -> PipelineRecord | None:
    """Set a pipeline's cancel flag, which its owner's cancel probe reads.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        The updated record, or None when the pipeline has none.

    Raises:
        PipelineRecordError: The record could not be read or written.
    """
    record = read_pipeline_record(project_root, pipeline_id)
    if record is None:
        return None
    updated = replace(record, cancel_requested=True)
    _store(Path(project_root), updated)
    return updated


def cancel_requested(project_root: Path | str, pipeline_id: str) -> bool:
    """Whether a pipeline's record asks for a cancel.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        True when the record exists and its flag is set; False otherwise,
        including when the record cannot be read.
    """
    try:
        record = read_pipeline_record(project_root, pipeline_id)
    except PipelineRecordError:
        return False
    return bool(record is not None and record.cancel_requested)


@task(purpose="Remove a pipeline's record: the pipeline is no longer running",
      inputs="project root, pipeline id",
      outputs="True when a record was removed, False when there was none")
def remove_pipeline_record(project_root: Path | str, pipeline_id: str) -> bool:
    """Remove a pipeline's record.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        True when a record was removed, False when there was none (an id
        that is not a pipeline id has none).

    Raises:
        PipelineRecordError: The file could not be removed.
    """
    path = _record_path(project_root, pipeline_id)
    if path is None:
        return False

    def _unlink() -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    try:
        return bool(_retrying(_unlink))
    except OSError as exc:
        raise PipelineRecordError(f"could not remove the pipeline record {path}: {exc}") from exc


def frozen_step_map(project_root: Path | str, pipeline_id: str) -> dict[str, str] | None:
    """Each node of a pipeline's frozen document mapped to its method name.

    The same map the canvas builds when it submits a document: a method
    node's id to its method, a system node's id to an empty string.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        The step map, or None when the pipeline has no readable frozen
        document (or the id is not a pipeline id).
    """
    if not is_pipeline_id(pipeline_id):
        return None
    path = layout.pipeline_doc_path(Path(project_root), pipeline_id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return {str(n["id"]): str(n.get("method") or "")
                for n in document.get("nodes", [])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
