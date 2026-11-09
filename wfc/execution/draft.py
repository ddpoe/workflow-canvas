"""The draft step: run a draft-mode step, keep its results in its draft's folder, record nothing.

A pipeline node pinned to ``draft`` and every step downstream of it run as
draft steps (Graph's :func:`~wfc.graph.draft_owners` names them and the draft
each belongs to). A draft step always runs: it has no claim, no run row, no
cache lookup or write, no archive and no push. Its results go to its owning
draft's folder (Layout's ``draft_result_dir``) and what it did is kept in
that draft's ``draft-run.json``, the draft-run store this module owns.

This module is also the one speller and parser of the draft string,
``draft:<module>.<method>/<node>@<sample>``, which names a draft step
wherever a run id goes. Every part is required: the string is copied from
wfc's own output, never typed short. It means "the latest draft run of this
draft".

``draft-run.json``::

    {"module": "<draft's module>", "method": "<draft's method>",
     "pipeline_id": "<pipeline>", "pipeline_name": "<name>" | null,
     "started_at": "<ISO 8601 UTC>", "folder": "<method folder>",
     "snapshot": {"<path>": [<size>, <mtime_ns>]},
     "steps": {"<node_id>": {"<sample>": {
         "node_id", "sample", "variant", "module", "method",
         "version": "draft" | "<content identity>", "params", "inputs",
         "status": "running" | "completed" | "failed", "error_message",
         "started_at", "finished_at", "outputs": {"<slot>": "<relpath>"},
         "metrics", "size",
         "process": {"pid": <int>, "created": <epoch seconds> | null}}}}}

A draft run from another pipeline replaces the stored one: the record and
every result folder go before the new run's first step is written.

A step's ``process`` names the process that ran it (``wfc try`` itself, or a
pipeline's run-step process) by Execution's :class:`ProcessIdentity`, written
with the entry's first write, so a ``running`` entry whose process is gone can
be told from a live one. ``created`` is null when it could not be read; an
entry written before the field has none. Both read as "not recorded"
(:func:`draft_step_process`). It is draft-only state: no cache key,
fingerprint, invocation record or ``command.sh`` reads it.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..persistence import project_root as get_project_root
from ..storage import retry_while_held, write_file
from .composer import PipelineDocumentError
from .pipeline_record import ProcessIdentity, process_identity

#: Where every draft string starts.
DRAFT_REF_PREFIX = "draft:"
#: The full form a refusal shows.
DRAFT_REF_FORM = "draft:<module>.<method>/<node>@<sample>"
#: The pin a pipeline node runs its method's draft under.
_DRAFT_PIN = "draft"
#: Seconds a writer waits for the store's lock before taking a stale one.
_LOCK_STALE_SECONDS = 30.0


class DraftRefError(ValueError):
    """A draft string is refused: a missing part, or no such draft step now."""


class DraftRef(NamedTuple):
    """A parsed draft string.

    Attributes:
        module: The draft's module.
        method: The draft's method.
        node_id: The draft's own step or a step it owns.
        sample: The sample.
    """

    module: str
    method: str
    node_id: str
    sample: str

    @property
    def text(self) -> str:
        """The string, spelled in full."""
        return spell_draft_ref(self.module, self.method, self.node_id, self.sample)


def spell_draft_ref(module: str, method: str, node_id: str, sample: str) -> str:
    """Spell a draft string: the inverse of :func:`parse_draft_ref`.

    Args:
        module: The owning draft's module.
        method: The owning draft's method.
        node_id: The step's node id.
        sample: The sample.

    Returns:
        ``draft:<module>.<method>/<node>@<sample>``.
    """
    return f"{DRAFT_REF_PREFIX}{module}.{method}/{node_id}@{sample}"


def is_draft_ref(text: Any) -> bool:
    """Whether *text* is a draft string (it starts with ``draft:``)."""
    return isinstance(text, str) and text.strip().startswith(DRAFT_REF_PREFIX)


def parse_draft_ref(text: str) -> DraftRef:
    """Parse a draft string; every part is required.

    Args:
        text: ``draft:<module>.<method>/<node>@<sample>``.

    Returns:
        The parts.

    Raises:
        DraftRefError: A part is missing (the message shows the full form and
            points to ``wfc runs``).
    """
    raw = str(text).strip()
    body = raw[len(DRAFT_REF_PREFIX):] if raw.startswith(DRAFT_REF_PREFIX) else ""
    qualified, _, rest = body.partition("/")
    module, _, method = qualified.rpartition(".")
    node_id, at, sample = rest.rpartition("@")
    if not (raw.startswith(DRAFT_REF_PREFIX) and module and method and at
            and node_id and sample):
        raise DraftRefError(
            f"'{raw}' is not a full draft string. A draft step is named "
            f"{DRAFT_REF_FORM}, every part written; `wfc runs` lists each "
            f"draft step's full string.")
    return DraftRef(module, method, node_id, sample)


# --- the draft-run store ------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _store_lock(path: Path):
    """Hold the draft run's lock file (one writer at a time, across processes).

    On Windows, creating the lock while its last holder is still removing it
    is refused with ``PermissionError``; that is a held lock too, waited out
    like one that exists.
    """
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            if time.monotonic() - started > _LOCK_STALE_SECONDS:
                retry_while_held(lambda: lock.unlink(missing_ok=True))
                started = time.monotonic()
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        retry_while_held(lambda: lock.unlink(missing_ok=True))


def _read_record(path: Path, *, strict: bool = False) -> dict | None:
    """Read a draft-run record; a read another handle refuses is tried again first.

    Args:
        path: The record.
        strict: For a writer holding the store lock: a record that is there
            but cannot be read raises instead of reading as no draft run,
            since a writer that took it for none would start a new draft
            run and delete the other steps' results. A record that is not
            valid JSON still reads as none.

    Raises:
        OSError: ``strict`` and the record could not be read.
    """
    if not path.is_file():
        return None
    try:
        text = retry_while_held(lambda: path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError:
        if strict:
            raise
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _write_record(path: Path, record: dict) -> None:
    """Replace a draft-run record whole, while other processes may be reading it."""
    write_file(path, json.dumps(record, indent=2, default=str).encode("utf-8"),
               temporary=path.with_name(path.name + f".{os.getpid()}.tmp"))


def _clear_results(module: str, method: str, root: Path) -> None:
    """Remove the draft run's record and result folders; the pointer stays."""
    folder = layout.draft_dir(root, module, method)
    if not folder.is_dir():
        return
    keep = {layout.DRAFT_POINTER_FILENAME, layout.DRAFT_SESSION_DIR_NAME,
            layout.DRAFT_RUN_FILENAME + ".lock"}
    for entry in folder.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            retry_while_held(functools.partial(entry.unlink, missing_ok=True))


def read_draft_run(module: str, method: str, root: Path | None = None) -> dict | None:
    """Read a draft's latest draft run, or ``None`` when it has none.

    Args:
        module: The draft's module.
        method: The draft's method.
        root: The project root; the resolved one when omitted.

    Returns:
        The ``draft-run.json`` record.
    """
    root = Path(root) if root is not None else get_project_root()
    return _read_record(layout.draft_run_path(root, module, method))


def clear_draft_run(module: str, method: str, root: Path | None = None) -> bool:
    """Clear a draft's draft run: its record and result folders (the pointer stays).

    Args:
        module: The draft's module.
        method: The draft's method.
        root: The project root; the resolved one when omitted.

    Returns:
        Whether there was a draft run to clear.
    """
    root = Path(root) if root is not None else get_project_root()
    path = layout.draft_run_path(root, module, method)
    with _store_lock(path):
        had = path.is_file()
        _clear_results(module, method, root)
    return had


def qualified_draft(module: str, method: str) -> str:
    """A draft's name as ``pipeline_runs.drafts`` and the cleared reasons spell it."""
    return f"{module}.{method}"


def _all_draft_runs(root: Path, *, strict: bool = False) -> list[tuple[str, str, dict]]:
    """Every draft's latest draft run, as ``(module, method, record)``.

    Args:
        root: The project root.
        strict: Raise when the draft store cannot be read; otherwise an
            unreadable store reads as no drafts.

    Raises:
        OSError: ``strict`` and the draft folder cannot be read.
        ValueError: ``strict`` and a draft pointer is not one wfc wrote.
    """
    from ..registration import list_drafts

    try:
        pointers = list_drafts(root)
    except (OSError, ValueError):
        if strict:
            raise
        return []
    runs = []
    for pointer in pointers:
        record = read_draft_run(pointer.module, pointer.method, root)
        if record is not None:
            runs.append((pointer.module, pointer.method, record))
    return runs


def _record_steps(record: dict) -> list[dict]:
    return [step for by_sample in (record.get("steps") or {}).values()
            if isinstance(by_sample, dict)
            for step in by_sample.values() if isinstance(step, dict)]


def draft_steps_by_pipeline(root: Path | str | None = None) -> dict[str, list[tuple[str, dict]]]:
    """Every stored draft step, by the pipeline whose draft run it is in.

    Args:
        root: The project root; the resolved one when omitted.

    Returns:
        ``{pipeline id: [(<module>.<method>, step entry), ...]}``.
    """
    root = Path(root) if root is not None else get_project_root()
    found: dict[str, list[tuple[str, dict]]] = {}
    for module, method, record in _all_draft_runs(root):
        pid = str(record.get("pipeline_id"))
        for step in _record_steps(record):
            found.setdefault(pid, []).append((qualified_draft(module, method), step))
    return found


def pipeline_draft_steps(pipeline_id: str,
                         root: Path | str | None = None) -> list[tuple[str, dict]]:
    """The draft steps a pipeline still has, in its drafts' ``draft-run.json``.

    Args:
        pipeline_id: The pipeline.
        root: The project root; the resolved one when omitted.

    Returns:
        ``[(<module>.<method>, step entry), ...]``; empty once they are cleared.
    """
    return draft_steps_by_pipeline(root).get(pipeline_id, [])


#: The key of a draft step's entry that names the process running the step.
DRAFT_STEP_PROCESS_KEY = "process"


def draft_step_process(step: Mapping[str, Any]) -> ProcessIdentity | None:
    """The process a draft step's entry records as running it, when it can be probed.

    Args:
        step: A draft step's entry from ``draft-run.json``.

    Returns:
        The recorded pid and creation time; ``None`` when the entry records
        none (it was written before the field), when its creation time is
        unknown, or when what is stored is not an identity. ``None`` means
        "not recorded": the step's liveness cannot be told from it.
    """
    raw = step.get(DRAFT_STEP_PROCESS_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        identity = ProcessIdentity.from_json(dict(raw))
    except (KeyError, TypeError, ValueError):
        return None
    return identity if identity.created is not None else None


@dataclass(frozen=True)
class DraftStepListing:
    """One stored draft step as a run listing shows it.

    Attributes:
        run: Its draft string (``spell_draft_ref``), what a run id is for a
            stored run.
        pipeline_id: The pipeline whose draft run it is in.
        pipeline_name: That pipeline's name, or ``None``.
        node_id: The step's node.
        method: The step's own ``module.method``.
        sample: The step's sample.
        status: The step's status.
        process: The process that ran (or runs) the step, as
            :func:`draft_step_process` reads it; ``None`` when not recorded.
            Execution's ``probe_process`` takes it as it is.
    """

    run: str
    pipeline_id: str | None
    pipeline_name: str | None
    node_id: str
    method: str
    sample: str
    status: str | None
    process: ProcessIdentity | None = None


@task(purpose="List every stored draft step from each draft's latest draft run, "
               "filtered by pipeline and by the step's own module.method; an "
               "unreadable draft store raises rather than listing nothing",
      inputs="pipeline name or id, module.method, project root",
      outputs="DraftStepListing per step, by draft, node and sample")
def list_draft_steps(pipeline: str | None = None, method: str | None = None,
                     root: Path | str | None = None) -> list[DraftStepListing]:
    """Every stored draft step, each draft's latest draft run, for a run listing.

    The draft-run side of what :func:`wfc.persistence.list_runs` answers for
    stored runs.

    Args:
        pipeline: Only the steps of the pipeline with this name or id.
        method: Only the steps whose own ``module.method`` is this.
        root: The project root; the resolved one when omitted.

    Returns:
        The listings, by draft, then node, then sample.

    Raises:
        OSError: The draft folder cannot be read.
        ValueError: A draft pointer is not one wfc wrote.
    """
    root = Path(root) if root is not None else get_project_root()
    listings: list[DraftStepListing] = []
    for module, owner_method, record in _all_draft_runs(root, strict=True):
        if pipeline and pipeline not in (record.get("pipeline_id"),
                                         record.get("pipeline_name")):
            continue
        for node_id, by_sample in sorted((record.get("steps") or {}).items()):
            for sample, step in sorted(by_sample.items()):
                qualified = f"{step.get('module')}.{step.get('method')}"
                if method and method != qualified:
                    continue
                listings.append(DraftStepListing(
                    run=spell_draft_ref(module, owner_method, node_id, sample),
                    pipeline_id=record.get("pipeline_id"),
                    pipeline_name=record.get("pipeline_name"),
                    node_id=node_id, method=qualified, sample=sample,
                    status=step.get("status"), process=draft_step_process(step)))
    return listings


@task(purpose="End a pipeline's running draft steps as its running rows were "
              "ended (failed, or cancelled with a reason), in their drafts' "
              "draft-run.json",
      inputs="pipeline id, the status, the message and cancel reason",
      outputs="the number of draft steps ended")
def end_running_draft_steps(pipeline_id: str, *, status: str, message: str,
                            cancel_reason: str | None = None,
                            root: Path | str | None = None) -> int:
    """End every running draft step of a pipeline (its failure, cancel or close-out).

    Args:
        pipeline_id: The pipeline.
        status: ``failed`` or ``cancelled``.
        message: The error message a step without one gets.
        cancel_reason: The reason a cancelled step records.
        root: The project root; the resolved one when omitted.

    Returns:
        How many steps were ended.
    """
    root = Path(root) if root is not None else get_project_root()
    ended = 0
    for module, method, _record in _all_draft_runs(root):
        path = layout.draft_run_path(root, module, method)
        with _store_lock(path):
            record = _read_record(path, strict=True)
            if record is None or record.get("pipeline_id") != pipeline_id:
                continue
            changed = False
            for step in _record_steps(record):
                if step.get("status") != "running":
                    continue
                step.update(status=status, finished_at=_now(),
                            error_message=step.get("error_message") or message,
                            cancel_reason=cancel_reason)
                changed = True
                ended += 1
            if changed:
                _write_record(path, record)
    return ended


class DraftInProgressError(DraftRefError):
    """A draft's draft run is still in progress in another pipeline (d-12)."""


def _pipeline_running(pipeline_id: str, record: dict) -> bool:
    """Whether the pipeline a draft run belongs to is still running.

    Its ``pipeline_runs`` row says so while it runs; a draft run with no row
    (none was written) is running while one of its steps is.
    """
    from ..persistence import PipelineRun, get_session

    with get_session() as session:
        row = session.get(PipelineRun, pipeline_id)
        status = None if row is None else row.status
    if status is not None:
        return status == "running"
    return any(s.get("status") == "running" for s in _record_steps(record))


def draft_run_in_progress(module: str, method: str, root: Path | str | None = None, *,
                          pipeline_id: str | None = None) -> str | None:
    """Why a draft cannot start a draft run now: its latest one is still in progress.

    Replacing a draft run's folder while its steps write into it would leave
    neither run readable (d-12).

    Args:
        module: The draft's module.
        method: The draft's method.
        root: The project root; the resolved one when omitted.
        pipeline_id: The pipeline about to run it; its own draft run is
            never in its way.

    Returns:
        The refusal, naming the running pipeline, or ``None``.
    """
    root = Path(root) if root is not None else get_project_root()
    record = read_draft_run(module, method, root)
    if record is None:
        return None
    running_id = str(record.get("pipeline_id"))
    if running_id == pipeline_id or not _pipeline_running(running_id, record):
        return None
    return _in_progress_text(module, method, record)


def _in_progress_text(module: str, method: str, record: dict) -> str:
    pipeline = record.get("pipeline_name") or record.get("pipeline_id")
    return (f"{qualified_draft(module, method)}'s draft run is still in progress "
            f"in pipeline {pipeline}: wait for it to end, or cancel it, then run "
            f"again.")


@task(purpose="Write one draft step's entry into its owning draft's "
              "draft-run.json, replacing the stored draft run first when it "
              "is another pipeline's: refused while that one is in progress "
              "(d-12); on replacement the cleared reason is written and the "
              "status helper called for the pipelines it cleared",
      inputs="the owning draft, the pipeline, the step's entry",
      outputs="draft-run.json with the step's entry; on replacement the old "
              "record and result folders are gone, and so are the later "
              "drafts' results computed from it",
      critical="One writer at a time across processes (a lock file); the "
               "record is replaced atomically")
def record_draft_step(module: str, method: str, *, pipeline_id: str,
                      pipeline_name: str | None, step: dict,
                      root: Path | None = None) -> bool:
    """Record a draft step, starting a new draft run when the pipeline differs.

    Args:
        module: The owning draft's module.
        method: The owning draft's method.
        pipeline_id: The pipeline the step runs in.
        pipeline_name: The pipeline's name, if any.
        step: The step's entry (``node_id`` and ``sample`` required).
        root: The project root; the resolved one when omitted.

    Returns:
        Whether a stored draft run was replaced.

    Raises:
        DraftInProgressError: The stored draft run is another pipeline's and
            that pipeline is still running; nothing is written.
    """
    from ..registration import read_draft

    root = Path(root) if root is not None else get_project_root()
    path = layout.draft_run_path(root, module, method)
    replaced: dict | None = None
    with _store_lock(path):
        record = _read_record(path, strict=True)
        if record is None or record.get("pipeline_id") != pipeline_id:
            if record is not None:
                old_id = str(record.get("pipeline_id"))
                if _pipeline_running(old_id, record):
                    raise DraftInProgressError(_in_progress_text(module, method, record))
                replaced = record
            _clear_results(module, method, root)
            pointer = read_draft(module, method, root)
            record = {
                "module": module, "method": method, "pipeline_id": pipeline_id,
                "pipeline_name": pipeline_name, "started_at": _now(),
                "folder": str(pointer.folder) if pointer else None,
                "snapshot": dict(pointer.snapshot) if pointer else {},
                "steps": {},
            }
        steps = record.setdefault("steps", {})
        steps.setdefault(str(step["node_id"]), {})[str(step["sample"])] = step
        _write_record(path, record)
    if replaced is not None:
        from .draft_clearing import settle_replacement

        settle_replacement(module, method, replaced, record, root)
    return replaced is not None


# --- resolving a draft string -------------------------------------------------


class DraftStepOutput(NamedTuple):
    """Where one output of a draft step is, or why it cannot be read.

    Attributes:
        path: The output on the host, or ``None``.
        refusal: Why not, or ``None``.
    """

    path: Path | None
    refusal: str | None


def draft_run_time(record: dict) -> str:
    """The ``HH:MM:SS`` a draft run started, in local time.

    Args:
        record: The draft run's ``draft-run.json`` record.

    Returns:
        ``HH:MM:SS``; the stored stamp as it is when it does not parse, or
        ``an unknown time`` when there is none.
    """
    stamp = str(record.get("started_at") or "")
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return stamp or "an unknown time"


def draft_run_label(record: dict) -> str:
    """How every message names a draft run: ``the draft run of HH:MM:SS in <pipeline>``.

    Args:
        record: The draft run's ``draft-run.json`` record.

    Returns:
        The label, naming the pipeline by its name, else its id.
    """
    pipeline = record.get("pipeline_name") or record.get("pipeline_id")
    return f"the draft run of {draft_run_time(record)} in {pipeline}"


def _owner_elsewhere(ref: DraftRef, record: dict, root: Path) -> str | None:
    """The full string of the draft that owns the node in the same draft run's pipeline."""
    from ..registration import list_drafts

    for pointer in list_drafts(root):
        if (pointer.module, pointer.method) == (ref.module, ref.method):
            continue
        other = read_draft_run(pointer.module, pointer.method, root)
        if (other and other.get("pipeline_id") == record.get("pipeline_id")
                and ref.node_id in (other.get("steps") or {})):
            return spell_draft_ref(pointer.module, pointer.method, ref.node_id,
                                   ref.sample)
    return None


@task(purpose="Resolve a draft string to the latest draft run's step: refuse a "
              "draft that is gone or has no draft run, a node or sample that "
              "run did not use (listing the ones it did), and a step named "
              "through the wrong draft (with the right string)",
      inputs="a draft string, the project root",
      outputs="the parsed string, the draft run record and the step's entry")
def resolve_draft_step(text: str, root: Path | None = None) -> tuple[DraftRef, dict, dict]:
    """Find the draft step a draft string names, in its draft's latest draft run.

    Args:
        text: The draft string.
        root: The project root; the resolved one when omitted.

    Returns:
        ``(ref, record, step)``.

    Raises:
        DraftRefError: Saying why the string names no draft step now.
    """
    from ..registration import read_draft

    root = Path(root) if root is not None else get_project_root()
    ref = parse_draft_ref(text)
    qualified = f"{ref.module}.{ref.method}"
    if read_draft(ref.module, ref.method, root) is None:
        raise DraftRefError(
            f"{qualified} has no draft: it was registered or removed. A "
            f"registered draft's runs are recorded; `wfc runs --method "
            f"{qualified}` lists them.")
    record = read_draft_run(ref.module, ref.method, root)
    if record is None:
        raise DraftRefError(
            f"{qualified} has no draft run: its outputs were deleted, or it "
            f"has not run. Run it again; `wfc runs` lists the draft steps "
            f"that exist.")
    steps = record.get("steps") or {}
    if ref.node_id not in steps:
        right = _owner_elsewhere(ref, record, root)
        if right is not None:
            raise DraftRefError(
                f"node '{ref.node_id}' belongs to another draft in that "
                f"pipeline: name it {right}.")
        used = ", ".join(sorted(steps)) or "(none)"
        raise DraftRefError(
            f"node '{ref.node_id}' is not in {qualified}'s latest draft run "
            f"({draft_run_label(record)}), which ran nodes: {used}.")
    by_sample = steps[ref.node_id]
    if ref.sample not in by_sample:
        used = ", ".join(sorted(by_sample)) or "(none)"
        raise DraftRefError(
            f"sample '{ref.sample}' is not in {qualified}'s latest draft run "
            f"of node '{ref.node_id}' ({draft_run_label(record)}), which used "
            f"samples: {used}.")
    return ref, record, by_sample[ref.sample]


def draft_step_output(text: str, slot: str | None,
                      root: Path | None = None) -> DraftStepOutput:
    """The host path of one output of a draft step, read from ``draft-run.json``.

    Materialize's input source for a parent entry whose run part is a draft
    string: draft steps have no output rows.

    Args:
        text: The draft string.
        slot: The output slot; ``None`` takes the step's one output.
        root: The project root; the resolved one when omitted.

    Returns:
        The output's path, or the refusal.
    """
    root = Path(root) if root is not None else get_project_root()
    try:
        ref, _record, step = resolve_draft_step(text, root)
    except DraftRefError as exc:
        return DraftStepOutput(None, str(exc))
    if step.get("status") != "completed":
        return DraftStepOutput(None, f"that draft step is {step.get('status')}, "
                                     f"not completed.")
    outputs = step.get("outputs") or {}
    if slot is None:
        if len(outputs) != 1:
            return DraftStepOutput(None, f"name its output: it has "
                                         f"{', '.join(outputs) or '(none)'}.")
        slot = next(iter(outputs))
    if slot not in outputs:
        return DraftStepOutput(None, f"it has no output '{slot}' (its outputs: "
                                     f"{', '.join(outputs) or '(none)'}).")
    path = layout.draft_dir(root, ref.module, ref.method) / outputs[slot]
    if not path.exists():
        return DraftStepOutput(None, f"its output '{slot}' is gone from {path}.")
    return DraftStepOutput(path, None)


# --- the draft step -------------------------------------------------------------


class _Code(NamedTuple):
    """What a draft step runs: script, copy, contract, image and interpreter."""

    script: Path
    code_dir: Path
    contract: dict
    version: str
    label: str
    image_ref: str
    interpreter: str
    env_bin: str | None
    entry: Any
    #: The draft folder to copy into ``code_dir`` once the step starts.
    source: Path | None = None


def _take_code_copy(code: _Code) -> None:
    """Copy a draft's folder into the step's code copy (after its record is written)."""
    if code.source is None:
        return
    if code.code_dir.exists():
        shutil.rmtree(code.code_dir)
    shutil.copytree(code.source, code.code_dir)


def _draft_code(node: dict, node_id: str, sample: str, owner: tuple[str, str],
                root: Path) -> _Code:
    """The code a draft-mode step runs and the image it runs on.

    The draft's own node runs its folder on its env's current image (read
    here; the copy inside the draft folder is taken by
    :func:`_take_code_copy` once the step's record is written, so a refusal
    here leaves the previous draft run as it was); a step downstream of it
    runs its pinned registered version on that version's image.

    Raises:
        ValueError: Why the step cannot run (named for the user).
    """
    from ..contracts import parse_method_yaml
    from ..environments import (
        EnvNotRunnableError,
        ensure_runnable,
        ensure_version_runnable,
        version_bin_dir,
        version_interpreter,
    )
    from ..registration import (
        draft_run_refusal,
        read_draft,
        resolve_method_code,
        snapshot_selected_script,
    )
    from .node_env import resolve_node_env

    module, method = str(node.get("module")), str(node.get("method"))
    qualified = f"{module}.{method}"
    if node.get("version") == _DRAFT_PIN:
        why = draft_run_refusal(module, method, node.get("folder"), root)
        if why is not None:
            raise ValueError(why)
        pointer = read_draft(module, method, root)
        assert pointer is not None  # draft_run_refusal refuses no draft
        code_dir = layout.draft_code_dir(root, owner[0], owner[1], node_id, sample)
        source = pointer.folder.resolve()
        contract = parse_method_yaml(source)
        if contract is None:
            raise ValueError(f"{qualified}'s draft folder {pointer.folder} has "
                             f"no method.yaml.")
        selected = snapshot_selected_script(source, method)
        if selected is None:
            raise ValueError(f"{qualified}'s draft folder {pointer.folder} holds "
                             f"no script the method selects.")
        node_env = resolve_node_env(str(contract.get("env") or ""), root)
        if node_env.record is None:
            raise ValueError(
                f"{qualified}'s draft names env '{contract.get('env')}', which "
                f"is not registered with a built image. {node_env.error} "
                f"Register it with `wfc register-env`.".replace("  ", " "))
        try:
            image = ensure_runnable(node_env.name, node_env.record, root)
            interpreter = version_interpreter(node_env.name,
                                              node_env.record.env_fingerprint, root)
            env_bin = version_bin_dir(node_env.name,
                                      node_env.record.env_fingerprint, root)
        except EnvNotRunnableError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - the capture is unreadable
            raise ValueError(f"env '{node_env.name}': {exc}") from exc
        return _Code(code_dir / Path(selected).resolve().relative_to(source), code_dir,
                     contract, _DRAFT_PIN, "DRAFT", image, interpreter, env_bin, None,
                     source)
    code = resolve_method_code(module, method, root,
                               version=node.get("version") or None)
    contract = parse_method_yaml(code.code_dir) or {}
    node_env = resolve_node_env(code.entry.env_name, root)
    if node_env.record is None:
        raise ValueError(f"{qualified} v{code.entry.label}'s env "
                         f"'{code.entry.env_name}' has no built image.")
    try:
        image = ensure_version_runnable(
            node_env.name, code.entry.env_fingerprint, root,
            image=code.entry.image_ref or node_env.record.container)
        interpreter = version_interpreter(node_env.name, code.entry.env_fingerprint, root)
        env_bin = version_bin_dir(node_env.name, code.entry.env_fingerprint, root)
    except EnvNotRunnableError as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the capture is unreadable
        raise ValueError(f"env '{node_env.name}': {exc}") from exc
    return _Code(code.script, code.code_dir, contract, code.entry.identity,
                 f"v{code.entry.label}", image, interpreter, env_bin, code.entry)


def _draft_params(node: dict, document: dict, node_id: str, variant: str,
                  code: _Code) -> dict:
    """The step's params, checked against its contract and filled with its defaults."""
    from ..contracts import normalize_and_fill_params, param_refusals

    variants = (document.get("param_sets") or {}).get(node_id) or {}
    params = dict(variants.get(variant, node.get("params") or {}) or {})
    schema = code.contract.get("params") or {}
    refusals = param_refusals(node_id, f"{node.get('module')}.{node.get('method')}",
                              code.label, params, schema)
    if refusals:
        raise ValueError("\n".join(refusals))
    return normalize_and_fill_params(params, schema)


def _parent_entries(document: dict, node_id: str, owners: dict[str, str],
                    sample: str, variant: str, pipeline_id: str) -> list[str]:
    """One parent entry per link into the step: a recorded run's id, or a draft string.

    A recorded upstream contributes the run its run-id sidecar names (the
    claim's own check); an upstream draft-mode step contributes the draft
    string of its owning draft. Selectors and references contribute nothing
    here (the sample and ``--ref-input`` feed those slots).

    Raises:
        ValueError: A recorded upstream's sidecar is refused.
    """
    from ..graph import document_node, inbound_wiring
    from .claim import check_sidecar_run
    from .parents import spell_parent_entry

    root = get_project_root()
    entries: list[str] = []
    for wire in inbound_wiring(document, node_id):
        if wire.source_kind in ("run_reference", "input_selector"):
            continue
        source = str(wire.raw_source_id)
        if source in owners:
            owner = document_node(document, owners[source]) or {}
            run_part = spell_draft_ref(str(owner.get("module")),
                                       str(owner.get("method")), source, sample)
        else:
            sidecar = layout.run_id_sidecar_path(root, pipeline_id, wire.source_id,
                                                 sample, variant)
            named = check_sidecar_run(sidecar, node_id=node_id, slot=wire.target_slot,
                                      source_node_id=source, sample=sample,
                                      pipeline_id=pipeline_id)
            if named.refusal is not None:
                raise ValueError(named.refusal)
            run_part = str(named.run_id)
        entries.append(spell_parent_entry(wire.target_slot, wire.source_slot, run_part))
    return entries


def _tree_size(folder: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return total


@workflow(purpose="Run one draft-mode step: its contract through Contracts, its "
                  "inputs (recorded runs or draft steps), its container on the "
                  "env's current image (a draft) or its version's (a step below "
                  "one), collect's checks, and its entry in draft-run.json; no "
                  "claim, run row, cache, archive or push",
          inputs="node, sample, variant, the pipeline document and id, optional "
                 "parent entries (wfc try), ref inputs, a collapsed bundle",
          outputs="0 on success, 1 on failure; the step's results in its "
                  "draft's folder and its entry in draft-run.json; the sentinel "
                  "inside a pipeline",
          critical="Writes no database row and keys nothing: a draft step "
                   "always runs and leaves no run reference")
def run_draft_step(node_id: str, sample: str, variant: str = "default", *,
                   pipeline_id: str | None = None,
                   pipeline_json: str | None = None,
                   document: dict | None = None,
                   parent_run_ids: list[str] | None = None,
                   ref_inputs: list[str] | None = None,
                   collapsed_samples: list[str] | None = None,
                   pipeline_name: str | None = None,
                   on_start: Callable[[], None] | None = None) -> int:
    """Run a draft-mode step end to end.

    Args:
        node_id: The step's node id.
        sample: The sample.
        variant: The parameter variant.
        pipeline_id: The pipeline's id (``WFC_PIPELINE_ID`` when omitted).
        pipeline_json: The pipeline document's path (``WFC_PIPELINE_JSON``
            when omitted and no *document* is given).
        document: The pipeline document itself (``wfc try``'s one-node
            document), instead of a path.
        parent_run_ids: Parent entries as typed (``input:output:run``, the
            run part a run id or a draft string); ``None`` walks the
            document's links.
        ref_inputs: ``label=path`` reference inputs.
        collapsed_samples: A collapsed fan-in root's bundled samples.
        pipeline_name: The pipeline's name for the draft run record.
        on_start: Called once the step starts (its record written), never
            for a step refused before it (``wfc try`` says then which
            draft run it replaces).

    Returns:
        0 on success, 1 on failure (the error printed). A refusal
        before the step's record leaves the previous draft run as it was.
    """
    from ..graph import document_node, draft_owners
    from .collect import check_step_outputs
    from .container_vars import step_variables
    from .dispatch import launch_container
    from .materialize import run_materialize

    root = get_project_root()

    def _refuse(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 1

    口 = Step(step_num=1, name="Read the step and the draft that owns it",
             purpose="The document's node and Graph's ownership rule; a step "
                     "that is not draft-mode is refused")
    pipeline_id = pipeline_id or os.environ.get("WFC_PIPELINE_ID")
    if document is None:
        path = pipeline_json or os.environ.get("WFC_PIPELINE_JSON")
        if not path or not Path(path).is_file():
            return _refuse(f"draft step '{node_id}' needs its pipeline document.")
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not pipeline_id:
        return _refuse(f"draft step '{node_id}' needs a pipeline id.")
    node = document_node(document, node_id)
    owners = draft_owners(document)
    raw_id = str(node.get("id")) if node else node_id
    if node is None or raw_id not in owners:
        return _refuse(f"node '{node_id}' is not a draft-mode step of this "
                       f"pipeline: it has no draft upstream of it.")
    owner_node = document_node(document, owners[raw_id]) or {}
    owner = (str(owner_node.get("module")), str(owner_node.get("method")))
    pipeline_name = pipeline_name or document.get("name")
    entry: dict = {
        "node_id": raw_id, "sample": sample, "variant": variant,
        "module": node.get("module"), "method": node.get("method"),
        "version": node.get("version"), "params": {}, "inputs": [],
        "status": "running", "error_message": None, "started_at": _now(),
        "finished_at": None, "outputs": {}, "metrics": {}, "size": 0,
        # The process running the step (this one), written with the entry's
        # first write; a creation time psutil cannot read is stored as null.
        DRAFT_STEP_PROCESS_KEY: process_identity(os.getpid()).to_json(),
    }

    def _end(status: str, error: str | None = None) -> int:
        entry.update(status=status, error_message=error, finished_at=_now())
        record_draft_step(*owner, pipeline_id=pipeline_id,
                          pipeline_name=pipeline_name, step=entry, root=root)
        if error is not None:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        sentinel = layout.run_sentinel_path(root, pipeline_id, node_id, sample, variant)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
        return 0

    def _refuse_before_start(message: str) -> int:
        # Before the step's record: the previous draft run stays as it was. A
        # draft run this pipeline already started (an earlier step of it)
        # keeps the refusal as this step's failed entry.
        stored = read_draft_run(*owner, root)
        if stored is not None and stored.get("pipeline_id") == pipeline_id:
            return _end("failed", message)
        return _refuse(message)

    口 = Step(step_num=2, name="Read the contract, the params and the inputs",
             purpose="The contract from the folder (or the version's copy) "
                     "through Contracts' parser, its param checks and default "
                     "fill, and one parent entry per link; nothing is written yet",
             critical="Every refusal here comes before the step's record, so "
                      "the previous draft run and its outputs stay")
    try:
        code = _draft_code(node, raw_id, sample, owner, root)
        params = _draft_params(node, document, node_id, variant, code)
        entries = (list(parent_run_ids) if parent_run_ids is not None else
                   _parent_entries(document, node_id, owners, sample, variant,
                                   pipeline_id))
    except Exception as exc:  # noqa: BLE001 - every refusal ends the step
        return _refuse_before_start(f"draft step '{raw_id}' ({sample}): {exc}")
    entry.update(params=params, inputs=entries,
                 version=code.version if code.version != _DRAFT_PIN else _DRAFT_PIN)

    口 = AutoStep(step_num=3, name="Materialize")
    mat = run_materialize(node_id=node_id, sample=sample, pipeline_data=document,
                          parent_run_ids=entries, ref_inputs=ref_inputs,
                          collapsed_samples=collapsed_samples)
    if not mat["ok"]:
        return _refuse_before_start(mat["error_message"])

    口 = Step(step_num=4, name="Start the step: its record, then its code copy",
             purpose="The step's entry in draft-run.json (replacing another "
                     "pipeline's draft run, refused while that one is in "
                     "progress), the caller told the step started, then the "
                     "draft folder's copy the container runs",
             critical="Materialize writes nothing into the draft folder, so it "
                      "runs before the record (decisions::d-11)")
    try:
        record_draft_step(*owner, pipeline_id=pipeline_id,
                          pipeline_name=pipeline_name, step=entry, root=root)
    except DraftInProgressError as exc:
        return _refuse(str(exc))
    if on_start is not None:
        on_start()
    result_dir = layout.draft_result_dir(root, owner[0], owner[1], raw_id, sample)
    try:
        _take_code_copy(code)
    except OSError as exc:
        return _end("failed", f"draft step '{raw_id}' ({sample}): its code copy "
                              f"could not be taken: {exc}")

    口 = Step(step_num=5, name="Dispatch on the step's image",
             purpose="A fresh result folder, the run context and the WFC_* "
                     "values (the draft string is the step's id), then the "
                     "shared container launch with no run-row ties")
    from ..contracts import declared_names

    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir(parents=True)
    ref_text = spell_draft_ref(owner[0], owner[1], raw_id, sample)
    slot_outputs = node.get("slot_outputs") or {}
    (result_dir / "_run_context.json").write_text(json.dumps({
        "run_id": ref_text, "run_dir": str(result_dir), "sample": sample,
        "slot_paths": mat["slot_paths"], "params": params,
        "method_name": node.get("method"), "module_name": node.get("module"),
        "slot_outputs": slot_outputs, "slot_types": node.get("slot_types") or {},
    }, indent=2, default=str), encoding="utf-8")
    step_env = step_variables(run_id=ref_text, run_dir=str(result_dir.resolve()),
                              sample=sample, params=params, node_id=node_id,
                              pipeline_id=pipeline_id, variant=variant,
                              slot_paths=mat["slot_paths"])
    declared = declared_names(code.contract.get("inputs"), code.contract.get("outputs"))
    from .container_vars import container_variables
    host_env = {**os.environ, **container_variables(step=step_env, declared=declared)}
    launched = launch_container(
        image_ref=code.image_ref, interpreter=code.interpreter, env_bin=code.env_bin,
        script=code.script, project_root=root, run_dir=result_dir,
        step_env=step_env, declared=declared, host_env=host_env,
        code_dir=str(code.code_dir), gpus=bool(code.contract.get("gpus")),
        notes=mat["notes"], copy_entry=code.entry)
    if not launched["ok"]:
        return _end("failed", launched["error_message"])

    口 = AutoStep(step_num=6, name="Check the step's outputs")
    checked = check_step_outputs(str(node.get("method")), result_dir, node,
                                 slot_outputs, root)
    if not checked["ok"]:
        return _end("failed", checked["error_message"])

    口 = Step(step_num=7, name="Keep the step in draft-run.json",
             purpose="Each output's path inside the draft folder, the metrics "
                     "and the folder's size; the sentinel inside a pipeline")
    draft_folder = layout.draft_dir(root, *owner)
    entry["outputs"] = {slot: Path(path).resolve().relative_to(draft_folder.resolve()).as_posix()
                        for slot, path in checked["located"]}
    entry["metrics"] = checked["metrics"]
    entry["size"] = _tree_size(result_dir)
    return _end("completed")


# --- before a pipeline with drafts launches ----------------------------------------


#: A draft that cannot run as it is: it cannot be read for a run, its
#: Registration check fails, it selects no script, or its env cannot run.
DRAFT_INVALID = "draft_invalid"
#: A draft whose draft run is still running in another pipeline (d-12).
DRAFT_IN_USE = "draft_in_use"
#: A document that does not match its draft's contract read now: a param or
#: variant, a link slot, or a required input nothing is connected to.
DRAFT_MISMATCH = "draft_mismatch"


@dataclass(frozen=True)
class DraftNodeRefusal:
    """One reason a pipeline's draft node cannot run.

    Attributes:
        node_id: The pipeline node the reason is about.
        draft: The draft as ``<module>.<method>``.
        kind: :data:`DRAFT_INVALID`, :data:`DRAFT_IN_USE` or
            :data:`DRAFT_MISMATCH`.
        message: The line shown, starting with the node.
        reasons: Each failed Registration check line, when the check is
            the reason; empty otherwise.
        pipeline_id: The pipeline whose draft run is still running
            (:data:`DRAFT_IN_USE`).
        pipeline_name: That pipeline's name, else ``None``.
    """

    node_id: str
    draft: str
    kind: str
    message: str
    reasons: tuple[str, ...] = ()
    pipeline_id: str | None = None
    pipeline_name: str | None = None


class DraftLaunchRefusedError(PipelineDocumentError):
    """A pipeline's draft cannot run: every reason, before anything launches.

    Its message is every refusal's line, one per line. A refusal of the
    document (``PipelineDocumentError``), so every run door's one catch of a
    refused document reports it; ``refusals`` keeps each node's reason for
    the doors that mark nodes.

    Attributes:
        refusals: Each :class:`DraftNodeRefusal`, in the order found.
    """

    def __init__(self, refusals: Iterable[DraftNodeRefusal]) -> None:
        self.refusals = tuple(refusals)
        super().__init__("\n".join(r.message for r in self.refusals))


def _draft_refusals(node_id: str, module: str, method: str, reread: Any, root: Path,
                    pipeline_id: str | None = None) -> list[DraftNodeRefusal]:
    """Why one re-read draft cannot run now, its env image made runnable when it can.

    Args:
        node_id: The first pipeline node that runs the draft.
        module: The draft's module.
        method: The draft's method.
        reread: Registration's ``DraftReread`` of the draft (writing form).
        root: The project root.
        pipeline_id: The pipeline about to launch, when known.

    Returns:
        Each reason: a ✗ of the Registration check (naming the check), a
        draft run still in progress, no selected script, an env that is
        not registered or cannot be made runnable.
    """
    from ..environments import EnvNotRunnableError, ensure_runnable
    from ..registration import snapshot_selected_script
    from .node_env import resolve_node_env

    qualified = qualified_draft(module, method)
    head = f"node '{node_id}'"

    def invalid(text: str, reasons: tuple[str, ...] = ()) -> DraftNodeRefusal:
        return DraftNodeRefusal(node_id, qualified, DRAFT_INVALID, f"{head}: {text}",
                                reasons or (text,))

    found: list[DraftNodeRefusal] = []
    if not reread.check.ok:
        found.append(invalid(f"{qualified}'s draft fails the Registration check: "
                             f"{'; '.join(reread.check.failures)}",
                             tuple(reread.check.failures)))
    busy = draft_run_in_progress(module, method, root, pipeline_id=pipeline_id)
    if busy is not None:
        record = read_draft_run(module, method, root) or {}
        found.append(DraftNodeRefusal(
            node_id, qualified, DRAFT_IN_USE, f"{head}: {busy}",
            pipeline_id=str(record.get("pipeline_id") or "") or None,
            pipeline_name=record.get("pipeline_name")))
    if not reread.check.ok:
        return found  # the check names the script and env it could not accept
    pointer = reread.pointer
    if snapshot_selected_script(pointer.folder, method) is None:
        found.append(invalid(f"{qualified}'s draft folder {pointer.folder} holds no "
                             f"script the method selects."))
    env_name = str((reread.contract or {}).get("env") or "")
    node_env = resolve_node_env(env_name, root)
    if node_env.record is None:
        found.append(invalid(
            f"{qualified}'s draft names env '{env_name}', which is not registered "
            f"with a built image. {node_env.error} Register it with "
            f"`wfc register-env`.".replace("  ", " ")))
        return found
    try:
        ensure_runnable(node_env.name, node_env.record, root)
    except EnvNotRunnableError as exc:
        found.append(invalid(str(exc)))
    return found


@task(purpose="The draft preflight every run door calls before anything is "
              "written: read each draft node's draft again (Registration's "
              "writing re-read, which stores the contract on the pointer), "
              "refuse any ✗ of its Registration check naming the check, check "
              "each draft node's params, param_sets variants, links and "
              "unwired required inputs against the contract read now, refuse a draft whose draft run is still "
              "in progress (d-12), and make each draft's env image runnable",
      inputs="the pipeline document (substituted), project root, the pipeline "
             "about to launch",
      outputs="nothing, or DraftLaunchRefusedError naming every reason",
      critical="runs before the launch and before the draft step's first "
               "record, so a refusal writes no row and leaves the previous "
               "draft run; nothing in the document is dropped or adjusted")
def preflight_drafts(document: dict, root: Path | str | None = None, *,
                     pipeline_id: str | None = None,
                     fed_outside: Mapping[str, Iterable[str]] | None = None) -> None:
    """Read every draft of a pipeline again and make it runnable, or refuse the launch.

    ``wfc try``, ``wfc run-pipeline`` and the canvas's run route all call
    this. A changed ``method.yaml`` needs no Update: the re-read stores it,
    and the document must match it. A param the contract no longer declares,
    a value of the wrong type, a link to a slot it no longer declares, or a
    required input it now declares that nothing is connected to is refused
    naming the node and the field; nothing is dropped.

    Args:
        document: The pipeline document, its variables substituted (sparse
            or enriched form).
        root: The project root; the resolved one when omitted.
        pipeline_id: The pipeline about to launch, when it is known: its own
            draft run is never in its way (a relaunch of the same id).
        fed_outside: Per node id, the inputs fed from outside the document
            (``wfc try``'s parent entries); only the try passes any.

    Raises:
        DraftLaunchRefusedError: Every reason any draft cannot run.
    """
    from ..graph import is_draft_node, link_slot_errors, unwired_input_errors
    from ..registration import DraftNotFoundError, DraftRefusedError, draft_run_refusal, reread_draft
    from .validation import draft_param_refusals

    root = Path(root) if root is not None else get_project_root()
    nodes = [n for n in document.get("nodes") or []
             if isinstance(n, dict) and is_draft_node(n)]
    refusals: list[DraftNodeRefusal] = []
    contracts: dict[tuple[str, str], dict] = {}
    slots: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        key = (str(node.get("module")), str(node.get("method")))
        if key in seen:
            continue
        seen.add(key)
        node_id, qualified = str(node.get("id")), qualified_draft(*key)
        口 = Step(step_num=1.1, name="Read the draft again",
                 purpose="Registration's no-draft, wrong-folder and folder-gone "
                         "refusals, then its writing re-read: the contract read "
                         "now stored on the pointer, and the Registration check")
        why = draft_run_refusal(*key, node.get("folder"), root)
        if why is None:
            try:
                reread = reread_draft(*key, root, write=True)
            except (DraftNotFoundError, DraftRefusedError, ValueError) as exc:
                why = str(exc)
        if why is not None:
            refusals.append(DraftNodeRefusal(node_id, qualified, DRAFT_INVALID,
                                             f"node '{node_id}': {why}", (why,)))
            continue
        口 = Step(step_num=1.2, name="The check, the run in progress and the env",
                 purpose="Any ✗ refuses naming the check; a draft run still in "
                         "progress elsewhere, no selected script, or an env "
                         "that cannot be made runnable refuses too")
        found = _draft_refusals(node_id, *key, reread, root, pipeline_id)
        refusals.extend(found)
        if reread.contract is not None:
            contracts[key] = reread.contract
            slots[f"{key[0]}.{key[1]}"] = {"versions": {_DRAFT_PIN: {
                "input_slots": reread.contract.get("inputs") or {},
                "output_slots": reread.contract.get("outputs") or {},
                "folder": str(reread.pointer.folder)}}}

    口 = Step(step_num=2, name="Params, variants and links against the contract read now",
             purpose="Each draft node's params and each of its param_sets "
                     "variants through the validator's draft check, Graph's "
                     "link-slot rule over the draft nodes' link ends, and "
                     "Graph's unwired-required-input rule over their inputs",
             critical="Nothing is dropped or adjusted; each refusal names the "
                      "node and the field")
    param_sets = document.get("param_sets")
    param_sets = param_sets if isinstance(param_sets, dict) else {}

    def mismatches(node: dict, lines: list[str]) -> list[DraftNodeRefusal]:
        draft = qualified_draft(str(node.get("module")), str(node.get("method")))
        return [DraftNodeRefusal(str(node.get("id")), draft, DRAFT_MISMATCH, line)
                for line in lines]

    for node in nodes:
        contract = contracts.get((str(node.get("module")), str(node.get("method"))))
        if contract is not None:
            refusals.extend(mismatches(node, draft_param_refusals(
                node, param_sets.get(str(node.get("id"))), contract)))
    links = list(document.get("links") or [])
    # Each rule runs per node, so every line is the node's it names.
    for node in nodes:
        refusals.extend(mismatches(node, link_slot_errors([node], links, slots)))
    for node in nodes:
        refusals.extend(mismatches(node, unwired_input_errors(
            [node], links, slots, fed_outside=fed_outside)))
    if refusals:
        raise DraftLaunchRefusedError(refusals)
