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
whole file, so a reader never sees half of one, and every read-modify-write
(the engine, the cancel flag) and the removal hold the record's lock file
beside it, across processes, so a flag set as the record goes never writes
the record back. ``hold_lock_file`` is that lock: a file created
exclusively, naming its holder, taken over only when the holder is gone.

The step map a status route needs for a recorded pipeline is not stored: it
comes from the frozen ``pipeline.json`` (``frozen_step_map``).
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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


#: Seconds two readings of one process's creation time may differ by and
#: still name the same process (as the recovery's probe allows).
_LOCK_HOLDER_TOLERANCE_SECONDS = 1.0

#: Seconds a lock file naming no holder (its holder stopped between creating
#: it and writing its name into it) is waited on before it is taken.
_UNNAMED_LOCK_STALE_SECONDS = 30.0

#: How long a waiter sleeps between tries for a held lock file.
_LOCK_POLL_SECONDS = 0.02

_own_identity: ProcessIdentity | None = None


def _this_process() -> ProcessIdentity:
    """This process's identity, read once."""
    global _own_identity
    if _own_identity is None or _own_identity.pid != os.getpid():
        _own_identity = process_identity(os.getpid())
    return _own_identity


def _lock_holder_gone(lock: Path) -> bool:
    """Whether the process a lock file names is gone, so the lock may be taken.

    Args:
        lock: The lock file.

    Returns:
        True when the holder it names is no longer that live process (no
        process has its pid, or the pid now names another process), or when
        it names no holder and is older than ``_UNNAMED_LOCK_STALE_SECONDS``.
        False while the holder lives, and when the file is already gone.
    """
    try:
        holder: ProcessIdentity | None = ProcessIdentity.from_json(
            json.loads(lock.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError):
        holder = None
    if holder is None or holder.created is None:
        try:
            return time.time() - lock.stat().st_mtime > _UNNAMED_LOCK_STALE_SECONDS
        except OSError:
            return False
    now = process_identity(holder.pid)
    return (now.created is None
            or abs(now.created - holder.created) > _LOCK_HOLDER_TOLERANCE_SECONDS)


def _unlink_lock(lock: Path) -> None:
    """Remove a lock file, retrying while another process holds it open.

    On Windows a file another process has open (a waiter reading who holds
    the lock) cannot be removed; the removal is retried for up to
    ``_RETRIES * _RETRY_SECONDS`` seconds, as the record's own writes are, so
    a release never leaves the lock naming a holder that has moved on. A
    file already gone is left.

    Raises:
        PermissionError: The file was still held open after every retry.
    """
    _retrying(lambda: lock.unlink(missing_ok=True))


def _create_exclusive(path: Path) -> bool:
    """Create an empty file only if none exists; whether this call created it."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except (FileExistsError, PermissionError):
        return False
    return True


def _guard_is_stale(path: Path) -> bool:
    """Whether a takeover file is older than ``_UNNAMED_LOCK_STALE_SECONDS``.

    Args:
        path: The takeover file.

    Returns:
        True when it is that old; False when it is younger or already gone.
    """
    try:
        return time.time() - path.stat().st_mtime > _UNNAMED_LOCK_STALE_SECONDS
    except OSError:
        return False


def _remove_stale_guard(guard: Path) -> None:
    """Remove a takeover file whose waiter stopped inside it, claiming it first.

    Two waiters can judge the same takeover file stale. Each renames it to a
    name of its own before removing it, so only one claims it, and the
    claimed file is judged again: when another waiter removed the stale file
    and a third made a fresh one between the first judgement and the rename,
    the rename took that fresh file, which is put back (unless yet another
    takeover file has appeared meanwhile) instead of being removed.

    Args:
        guard: The takeover file a waiter could not create.
    """
    if not _guard_is_stale(guard):
        return
    claimed = guard.with_name(f"{guard.name}.{os.getpid()}.{time.monotonic_ns()}")
    try:
        os.rename(guard, claimed)
    except OSError:
        return  # another waiter claimed it first, or it is gone
    try:
        if not _guard_is_stale(claimed):
            try:
                os.link(claimed, guard)
            except OSError:
                pass  # a newer takeover file holds the name
        claimed.unlink(missing_ok=True)
    except OSError:
        pass


def _take_over_stale_lock(lock: Path) -> bool:
    """Remove a lock file whose holder is gone, one waiter at a time.

    Deciding that the holder is gone and removing the file are two steps, so
    two waiters could both decide, and the second could remove the lock the
    first has just taken. A takeover file beside the lock (created
    exclusively) lets one waiter at a time decide and remove; the decision is
    made again inside it, so a lock taken by another waiter in between is
    left. A takeover file older than ``_UNNAMED_LOCK_STALE_SECONDS`` (its
    waiter stopped inside it) is removed so the next waiter can try
    (``_remove_stale_guard``).

    Args:
        lock: The lock file a waiter found held.

    Returns:
        True when this waiter held the takeover file and decided (whether or
        not it removed the lock); False when another waiter holds the
        takeover file, so the caller should wait before trying again.
    """
    guard = lock.with_name(lock.name + ".takeover")
    if not _create_exclusive(guard):
        _remove_stale_guard(guard)
        return False
    try:
        if _lock_holder_gone(lock):
            try:
                _unlink_lock(lock)
            except PermissionError:
                pass  # still open elsewhere; the next try decides again
    finally:
        try:
            _unlink_lock(guard)
        except PermissionError:
            pass  # left to age out as a stale takeover file
    return True


@task(
    purpose="Hold a lock file across processes: create it exclusively naming "
            "this process, wait while another holder lives, remove it on leaving",
    inputs="lock: the lock file path (its folder must exist)",
    outputs="the lock held for the block",
    critical="A lock is taken over only when the holder it names is gone (no "
             "live process with that pid and creation time); one waiter at a "
             "time decides and removes it, so two waiters never both take it",
)
@contextmanager
def hold_lock_file(lock: Path) -> Iterator[None]:
    """Hold a lock file: one holder at a time, across processes and threads.

    The lock is the file's existence, created exclusively (the draft run
    store's pattern), so it needs no dependency and works on Windows and
    POSIX alike. The file names its holder by pid and creation time; a waiter
    takes over a lock whose holder is gone (a process that stopped while
    holding it), never one whose holder lives, and only one waiter at a time
    does so (``_take_over_stale_lock``). The lock is not re-entrant: a holder
    that asks again waits on itself.

    Args:
        lock: The lock file; its folder must exist.

    Yields:
        Nothing; the lock is held until the block ends.

    Raises:
        FileNotFoundError: The lock file's folder does not exist (or was
            removed while this caller waited).
    """
    payload = json.dumps(_this_process().to_json()).encode("utf-8")
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_holder_gone(lock) and _take_over_stale_lock(lock):
                continue
            time.sleep(_LOCK_POLL_SECONDS)
            continue
        except PermissionError:
            # Windows refuses to create a file another process is deleting.
            time.sleep(_LOCK_POLL_SECONDS)
            continue
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        _unlink_lock(lock)


@contextmanager
def _record_lock(project_root: Path | str, pipeline_id: str) -> Iterator[None]:
    """Hold the record's lock file around a read-modify-write or a removal.

    A pipeline with no run dir (or an id that is not a pipeline id) has no
    record to change, so nothing is locked and no folder is made.
    """
    if not is_pipeline_id(pipeline_id):
        yield
        return
    root = Path(project_root)
    if not layout.pipeline_run_dir(root, pipeline_id).is_dir():
        yield
        return
    with hold_lock_file(layout.pipeline_record_lock_path(root, pipeline_id)):
        yield


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
    with _record_lock(project_root, pipeline_id):
        record = read_pipeline_record(project_root, pipeline_id)
        if record is None:
            return None
        updated = replace(record, engine=process_identity(pid))
        _store(Path(project_root), updated)
        return updated


def request_cancel(project_root: Path | str, pipeline_id: str) -> PipelineRecord | None:
    """Set a pipeline's cancel flag, which its owner's cancel probe reads.

    The read and the write hold the record's lock file, as its removal does,
    so a flag set as the record goes never writes the record back.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        The updated record, or None when the pipeline has none.

    Raises:
        PipelineRecordError: The record could not be read or written.
    """
    with _record_lock(project_root, pipeline_id):
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
        with _record_lock(project_root, pipeline_id):
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
