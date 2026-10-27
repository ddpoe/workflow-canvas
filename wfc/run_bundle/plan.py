"""The export plan: what a bundle of the selected runs would hold, and why it cannot.

The plan writes no bundle. It selects the runs (:mod:`.selection`), runs
Storage's archive pass over the selected runs' unarchived outputs, so that
every output has wfc's own hash, and then collects every reason the bundle
could not be complete, all of them, before anything is decided:

- a requested run that does not exist, or a run that did not complete;
- a run with no invocation record or no pipeline document hash (every run
  made before wfc recorded them; they are refused by name, never backfilled);
- a version whose tree git no longer holds;
- an output still without a content hash after the archive pass;
- a malformed output record or cache entry, in Storage's own words;
- a pipeline document, sample or env content blob Storage's entry read
  cannot serve (gone from the local cache and never pushed), and an image
  with no captures folder;
- with ``--with-outputs``, an output gone from the local cache and never
  pushed.

It also lists each step whose image has no pullable reference: an image only
this machine holds. That is a warning the surfaces ask about, not a refusal;
under ``--with-image`` the bundle carries the image, so there is none, and
an image this machine does not hold is a refusal instead.

The surfaces print or serialise the :class:`ExportPlan`; the apply step
writes the bundle from it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path, PurePosixPath

from axiom_annotations import AutoStep, Step, workflow
from sqlmodel import select

from .. import layout
from ..environments import (
    CaptureError,
    CaptureMismatchError,
    daemon_ref,
    image_info,
    image_state,
    local_image_ref,
    pullable_ref,
)
from ..persistence import (
    Method,
    MethodVersion,
    Module,
    Run,
    RunInput,
    Sample,
    get_session,
    read_run_records,
)
from ..persistence import project_root as get_project_root
from ..storage import (
    KIND_ENV_FINGERPRINT,
    KIND_PIPELINE_DOCUMENT,
    KIND_SAMPLE,
    OUTPUT_MISSING,
    OUTPUT_REMOTE,
    ResolveOutputError,
    archive_outputs,
    check_entry_shape,
    entry_state,
    output_export_name,
    output_location,
    output_records,
    output_repair,
    recorded_output_slots,
    unarchived_outputs,
)
from ..version import git_toplevel, object_exists
from .selection import select_runs

#: The lead above a refused plan's reasons on the canvas: the route's 409
#: and the Export runs dialog. The CLI keeps its own two-line refusal.
REFUSED_TEXT = "The bundle cannot be complete. Nothing was written."

#: The one run status a bundle can hold: a run that ran to its end.
COMPLETED = "completed"



@dataclass(frozen=True)
class Refusal:
    """One reason the bundle cannot be complete.

    Attributes:
        run_id: The run the reason is about, or ``None`` for none in
            particular.
        reason: The sentence a surface prints, naming the run and the repair.
    """

    run_id: int | None
    reason: str


@dataclass(frozen=True)
class PlanOutput:
    """One recorded output of a step.

    Attributes:
        slot: The contract slot the output fills.
        file_name: The output's file name, as ``wfc export`` names it.
        content_hash: wfc's hash of its bytes, or ``None`` when it has none.
        container_path: Where the method wrote it, inside the container, or
            ``None`` when the record names no path.
    """

    slot: str
    file_name: str
    content_hash: str | None
    container_path: str | None = None


@dataclass(frozen=True)
class PlanInput:
    """One input a step read, from its run's input rows.

    An upstream input names the run the row records (``recorded_run_id``,
    which may be a cache-hit row) and the step it comes from in the bundle
    (``run_id``, the original). A sample input names the sample and the hash
    the run read.

    Attributes:
        slot: The input slot the value was read into.
        recorded_run_id: The upstream run the row records, or ``None`` for a
            sample.
        run_id: The upstream step's run in the bundle (the original of a
            cache-hit row), or ``None`` for a sample.
        source_slot: The upstream output that fed the slot: the recorded one,
            or the upstream's one recorded output when the entry named none.
        cache_key: The recorded upstream row's cache key, or ``None``.
        sample_name: The sample, for a sample input.
        content_hash: The sample's hash the run read, for a sample input.
        sample_file: The file (or directory) name the run read the sample
            under: the last part of the path it read the input at, or,
            when that path does not name the sample, the name registered
            today.
    """

    slot: str
    recorded_run_id: int | None = None
    run_id: int | None = None
    source_slot: str | None = None
    cache_key: str | None = None
    sample_name: str | None = None
    content_hash: str | None = None
    sample_file: str | None = None

    @property
    def is_sample(self) -> bool:
        """Whether the input is a registered sample."""
        return self.recorded_run_id is None


@dataclass(frozen=True)
class EntryRead:
    """A cache entry the bundle reads through Storage's entry read.

    Attributes:
        kind: ``sample``, ``pipeline-document`` or ``env-fingerprint``.
        content_hash: The entry's hash.
        name: The sample's name, for a sample.
        state: ``local``, or ``remote`` when the read will pull it.
    """

    kind: str
    content_hash: str
    name: str | None
    state: str


@dataclass(frozen=True)
class PlanStep:
    """One run the bundle holds, as the plan read it.

    Attributes:
        number: The step's 1-based place in dependency order.
        run_id: The executed run.
        parent_run_ids: The steps its inputs come from, in slot order, each
            named by its original run.
        module: The method's module name.
        method: The method name.
        sample: The sample the run ran on, or ``None``.
        status: The run row's status.
        version_label: The method version's label, or ``None``.
        tree_hash: The version's git tree, or ``None``.
        env_name: The env the version ran in, or ``None``.
        env_fingerprint: The env's fingerprint, or ``None``.
        image: The image's recorded ``docker://`` reference, or ``None``.
        pullable_ref: The registry reference the image can be pulled by, or
            ``None`` when it cannot be pulled.
        cache_key: The run's cache key, or ``None``.
        pipeline_doc_hash: The pinned pipeline document's hash, or ``None``.
        invocation: The recorded invocation, or ``None``.
        outputs: The run's recorded outputs, in row order.
        inputs: The run's recorded inputs, in row order.
        params: The run's parameters.
        code_fingerprint: The version's code fingerprint, or ``None``.
        key_env_fingerprint: The env fingerprint the run's key is over: the
            run row's, else the version's.
        key_recipe: The key recipe the run's key was composed under.
        git_commit: The project commit the run ran at, or ``None``.
        started_at: When the run started, ISO 8601, or ``None``.
        finished_at: When the run finished, ISO 8601, or ``None``.
        wfc_version: The wfc version the run ran under, or ``None``.
    """

    number: int
    run_id: int
    parent_run_ids: tuple[int, ...]
    module: str
    method: str
    sample: str | None
    status: str
    version_label: int | None
    tree_hash: str | None
    env_name: str | None
    env_fingerprint: str | None
    image: str | None
    pullable_ref: str | None
    cache_key: str | None
    pipeline_doc_hash: str | None
    invocation: dict | None
    outputs: tuple[PlanOutput, ...] = ()
    inputs: tuple[PlanInput, ...] = ()
    params: dict = field(default_factory=dict)
    code_fingerprint: str | None = None
    key_env_fingerprint: str | None = None
    key_recipe: int | None = None
    git_commit: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    wfc_version: str | None = None

    @property
    def label(self) -> str:
        """``module.method``, as a step folder and a message name it."""
        return f"{self.module}.{self.method}"


@dataclass(frozen=True)
class LocalOnlyImage:
    """A step whose image no registry holds: only this machine has it.

    Attributes:
        run_id: The step's run.
        env_name: The env.
        image: The image's recorded reference.
        state: Where the image is now (``wfc.environments`` image state:
            ``local``, ``missing`` or ``unknown``).
    """

    run_id: int
    env_name: str
    image: str
    state: str


@dataclass(frozen=True)
class PlanImage:
    """An image the bundle carries under ``--with-image``.

    Attributes:
        env_name: The env.
        env_fingerprint: The image's env fingerprint.
        ref: The reference the local daemon holds it under, which the apply
            saves.
    """

    env_name: str
    env_fingerprint: str | None
    ref: str


@dataclass(frozen=True)
class ExportPlan:
    """What an export of the requested runs would write, or why it cannot.

    Attributes:
        requested: The run ids asked for, in the order given.
        steps: Every run the bundle holds, in dependency order.
        substitutions: ``(cache-hit run, original run)`` per replaced row.
        refusals: Every reason the bundle cannot be complete; empty when it
            can.
        local_only: The steps whose image has no pullable reference.
        archived: How many outputs the plan's archive pass hashed.
        entries: Every cache entry the bundle reads by hash (pipeline
            documents, samples, env blobs), each once, with whether the read
            will pull it.
        with_outputs: Whether the bundle carries the outputs' bytes.
        with_image: Whether the bundle carries each step's image.
        images: Under ``with_image``, each distinct image (env and
            fingerprint) once, with the local reference the apply saves.
    """

    requested: tuple[int, ...]
    steps: tuple[PlanStep, ...]
    substitutions: tuple[tuple[int, int], ...]
    refusals: tuple[Refusal, ...]
    local_only: tuple[LocalOnlyImage, ...]
    archived: int = 0
    entries: tuple[EntryRead, ...] = ()
    with_outputs: bool = False
    with_image: bool = False
    images: tuple[PlanImage, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the bundle can be written: no refusal."""
        return not self.refusals

    @property
    def pulls(self) -> tuple[EntryRead, ...]:
        """The entries the apply will pull back from the remote."""
        return tuple(e for e in self.entries if e.state == OUTPUT_REMOTE)

    @property
    def token(self) -> str:
        """The plan's identity as one opaque string.

        Two plans with the same token describe the same bundle: the same runs,
        options, step facts, outputs and their hashes, substitutions,
        refusals, local-only warnings, entries (and whether each is pulled)
        and images. The archive count is no part of it: a dry run's archive
        pass hashes the outputs that a later plan's pass finds already hashed.
        A surface that shows a plan sends its token back with the apply, and
        the apply goes ahead only when a fresh plan has the same token.

        Returns:
            A sha256 hex digest of the plan's content as canonical JSON.
        """
        content = {f.name: getattr(self, f.name) for f in fields(self)
                   if f.name != "archived"}
        canonical = json.dumps(_plain(content), sort_keys=True, default=str,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def warning_lines(self) -> tuple[str, ...]:
        """The never-pushed warning, one sentence per image, for every surface.

        Each names the image, its env and the steps (and runs) that use it,
        and says the bundle points at an image only this machine holds. Run
        bundle owns the wording; the CLI prints it and the canvas shows it.

        Returns:
            One sentence per local-only image, in step order; empty when the
            plan has no local-only image (always, under ``with_image``).
        """
        numbers = {s.run_id: s.number for s in self.steps}
        users: dict[str, list[LocalOnlyImage]] = {}
        for warning in self.local_only:
            users.setdefault(warning.image, []).append(warning)
        lines = []
        for image, warnings in users.items():
            named = ", ".join(f"step {numbers[w.run_id]} (run {w.run_id})"
                              for w in warnings)
            lines.append(
                f"The image {image} (env {warnings[0].env_name}), used by {named}, "
                f"was never pushed to a registry. The bundle points at an image only "
                f"this machine holds; once it is deleted, "
                f"{'that step' if len(warnings) == 1 else 'those steps'} cannot be "
                f"rerun or compared.")
        return tuple(lines)

    def step(self, run_id: int) -> PlanStep:
        """The step of *run_id*.

        Args:
            run_id: A step's run id.

        Returns:
            The step.

        Raises:
            KeyError: The run is not a step of the bundle.
        """
        for step in self.steps:
            if step.run_id == run_id:
                return step
        raise KeyError(run_id)

    def to_dict(self) -> dict:
        """The plan as plain JSON values, for a surface to serialise.

        Returns:
            ``requested``, ``steps`` (number, run id, method, sample, parents,
            image and whether it is pullable), ``substitutions``,
            ``refusals``, ``warnings`` (the local-only images),
            ``warning_lines`` (:attr:`warning_lines`, the text a surface
            shows), ``refused_text`` (the lead a surface shows above the
            refusals), ``pulls``, the options, ``images``, ``ok`` and the
            plan's ``token``.
        """
        return {
            "requested": list(self.requested),
            "steps": [{"number": s.number, "run_id": s.run_id,
                       "method": s.label, "sample": s.sample,
                       "parent_run_ids": list(s.parent_run_ids),
                       "image": s.image, "pullable_ref": s.pullable_ref}
                      for s in self.steps],
            "substitutions": [{"run_id": hit, "original_run_id": original}
                              for hit, original in self.substitutions],
            "refusals": [{"run_id": r.run_id, "reason": r.reason}
                         for r in self.refusals],
            "warnings": [{"run_id": w.run_id, "env": w.env_name, "image": w.image,
                          "state": w.state} for w in self.local_only],
            "warning_lines": list(self.warning_lines),
            "refused_text": REFUSED_TEXT,
            "pulls": [{"kind": e.kind, "content_hash": e.content_hash, "name": e.name}
                      for e in self.pulls],
            "with_outputs": self.with_outputs,
            "with_image": self.with_image,
            "images": [{"env": i.env_name, "fingerprint": i.env_fingerprint,
                        "ref": i.ref} for i in self.images],
            "ok": self.ok,
            "token": self.token,
        }


def _plain(value):
    """*value* with every dataclass and tuple turned into dicts and lists, for JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _iso(moment) -> str | None:
    """An ISO 8601 rendering of a row's time, or ``None``."""
    return moment.isoformat() if moment is not None else None


def invocation_input_paths(invocation: dict | None) -> dict[str, list[str]]:
    """The container paths dispatch forwarded per input slot, each a list.

    Read from the invocation record's ``WFC_INPUT_PATHS``; the n-th input
    row of a slot was read at the slot's n-th path.

    Args:
        invocation: The run's invocation record, or ``None``.

    Returns:
        ``{slot: [container path, ...]}``; empty when none is recorded.
    """
    raw = ((invocation or {}).get("env") or {}).get("WFC_INPUT_PATHS")
    try:
        decoded = json.loads(raw) if raw else {}
    except ValueError:
        decoded = {}
    if not isinstance(decoded, dict):
        return {}
    return {slot: (value if isinstance(value, list) else [value])
            for slot, value in decoded.items()}


def _sample_file_read(sample: Sample | None, row: RunInput,
                      read_path: str | None) -> str | None:
    """The file (or directory) name the run read the sample under.

    The path the run read the input at, ``.../samples/<name>/<file>``,
    names the file whenever the part before it is the sample's name; that
    holds for an older version and for the same bytes registered again
    from a source of another name. Otherwise the name registered today
    stands in.

    Args:
        sample: The sample's row today, or ``None``.
        row: The run's input row for the sample.
        read_path: The container path the run read the input at, or ``None``.

    Returns:
        The file name, or ``None`` when neither source names one.
    """
    if read_path:
        parts = PurePosixPath(read_path.replace("\\", "/")).parts
        if len(parts) >= 2 and parts[-2] == row.sample_name:
            return parts[-1]
    return sample.registered_path if sample is not None else None


def _read_inputs(session, run_id: int, originals: dict[int, int],
                 invocation: dict | None = None) -> tuple[PlanInput, ...]:
    """Read a run's input rows, in row order.

    Args:
        session: Open DB session.
        run_id: The run.
        originals: ``{cache-hit run: original run}`` for the selection.
        invocation: The run's invocation record, for the path each sample
            was read at.

    Returns:
        One :class:`PlanInput` per row.
    """
    rows = session.exec(select(RunInput).where(RunInput.run_id == run_id)
                        .order_by(RunInput.id)).all()  # type: ignore[arg-type]
    paths = invocation_input_paths(invocation)
    taken: dict[str, int] = {}
    inputs: list[PlanInput] = []
    for row in rows:
        slot = row.input_name or ""
        index = taken.get(slot, 0)
        taken[slot] = index + 1
        if row.source_run_id is None:
            sample = (session.exec(select(Sample).where(Sample.name == row.sample_name))
                      .first() if row.sample_name else None)
            slot_paths = paths.get(slot, [])
            read_path = slot_paths[index] if index < len(slot_paths) else None
            inputs.append(PlanInput(
                slot=slot, sample_name=row.sample_name, content_hash=row.content_hash,
                sample_file=_sample_file_read(
                    sample, row, read_path if isinstance(read_path, str) else None)))
            continue
        recorded = int(row.source_run_id)
        source_slot = row.source_slot
        if source_slot is None:
            try:
                slots = recorded_output_slots(recorded, session=session)
            except ResolveOutputError:
                slots = []
            source_slot = slots[0] if len(slots) == 1 else None
        upstream = session.get(Run, recorded)
        inputs.append(PlanInput(
            slot=slot, recorded_run_id=recorded,
            run_id=originals.get(recorded, recorded), source_slot=source_slot,
            cache_key=upstream.cache_key if upstream is not None else None))
    return tuple(inputs)


def _read_step(session, number: int, run: Run, parents: tuple[int, ...],
               originals: dict[int, int]) -> PlanStep:
    """Read one step's facts from its run row, method, version and input rows.

    Args:
        session: Open DB session.
        number: The step's place in dependency order.
        run: The run row.
        parents: The step's parent steps.
        originals: ``{cache-hit run: original run}`` for the selection.

    Returns:
        The step, with no outputs yet.
    """
    method = session.get(Method, run.method_id) if run.method_id else None
    module = session.get(Module, method.module_id) if method is not None else None
    version = session.get(MethodVersion, run.version_id) if run.version_id else None
    return PlanStep(
        number=number, run_id=int(run.id or 0), parent_run_ids=parents,
        module=module.name if module is not None else "unknown module",
        method=method.name if method is not None else "unknown method",
        sample=run.sample, status=run.status,
        version_label=version.label if version is not None else None,
        tree_hash=version.tree_hash if version is not None else None,
        env_name=(version.env_name if version is not None and version.env_name
                  else run.env_name),
        env_fingerprint=(version.env_fingerprint
                         if version is not None and version.env_fingerprint
                         else run.env_fingerprint),
        image=version.image_ref if version is not None else None,
        pullable_ref=None, cache_key=run.cache_key,
        pipeline_doc_hash=run.pipeline_doc_hash, invocation=run.invocation,
        inputs=_read_inputs(session, int(run.id or 0), originals, run.invocation),
        params=dict(run.params or {}),
        code_fingerprint=version.code_fingerprint if version is not None else None,
        key_env_fingerprint=(run.env_fingerprint or
                             (version.env_fingerprint if version is not None else None)),
        key_recipe=run.key_recipe, git_commit=run.git_commit,
        started_at=_iso(run.started_at), finished_at=_iso(run.finished_at),
        wfc_version=run.wfc_version,
    )


def _run_refusals(step: PlanStep, project_dir: Path, top: Path | None) -> list[str]:
    """The reasons one step's run cannot be bundled, from its own row.

    Args:
        step: The step.
        project_dir: The project root.
        top: The repository's top level, or ``None`` outside git.

    Returns:
        The refusal sentences, in a fixed order.
    """
    name = f"Run {step.run_id} ({step.label})"
    reasons: list[str] = []
    if step.status != COMPLETED:
        reasons.append(f"{name} is {step.status}, not completed: only a run that "
                       f"ran to its end can be rerun and compared.")
    if step.invocation is None:
        reasons.append(f"{name} has no invocation record: it was made before wfc "
                       f"recorded what each run invoked, so its command cannot be "
                       f"replayed. Re-run it to export it.")
    if step.pipeline_doc_hash is None:
        reasons.append(f"{name} has no pipeline document hash: it was made before "
                       f"wfc pinned the document each run ran under. Re-run it "
                       f"to export it.")
    if step.tree_hash is None:
        reasons.append(f"{name} records no version tree, so its code cannot be "
                       f"bundled. Re-run it to export it.")
    elif top is None or not object_exists(project_dir, step.tree_hash, top=top):
        reasons.append(f"{name}: git no longer holds the tree {step.tree_hash} of "
                       f"{step.label} version {step.version_label}, so its code "
                       f"cannot be bundled. Fetch the commit that holds it, or "
                       f"re-run the run on a version git holds.")
    return reasons


def _no_hash_reason(status: str | None) -> str:
    """Why an output still has no hash, from the archive pass's status for it.

    Args:
        status: The status the plan's archive pass reported, or ``None``
            when the pass did not consider the output.

    Returns:
        The clause naming the cause, in the archive pass's own words.
    """
    if status is None:
        return "the archive pass did not hash it"
    kind, _, message = status.partition(":")
    if kind == "missing":
        return "its file is gone from the run archive"
    if message:
        return message
    return f"the archive pass reported {status}"


def _container_path(row, project_dir: Path) -> str | None:
    """Where a run wrote an output, inside the container: Layout's translation.

    Args:
        row: The output record.
        project_dir: The project root.

    Returns:
        The container path, or ``None`` when the record names no path.
    """
    if not row.artifact_path:
        return None
    artifact = Path(row.artifact_path)
    if not artifact.is_absolute():
        artifact = project_dir / artifact
    mounts = layout.mount_table(project_dir, layout.dvc_cache_dir(project_dir))
    return layout.translate_host_path(str(artifact), mounts)


def _output_refusals(step: PlanStep, project_dir: Path,
                     archive_status: dict[tuple[int, str], str],
                     with_outputs: bool = False,
                     ) -> tuple[tuple[PlanOutput, ...], list[str]]:
    """Read a step's output records through Storage and refuse the unusable ones.

    Args:
        step: The step.
        project_dir: The project root.
        archive_status: The archive pass's status per ``(run id, output
            name)``.
        with_outputs: Whether the bundle carries the outputs' bytes, so an
            output no read can serve is refused.

    Returns:
        The step's outputs and the refusal sentences for them.
    """
    try:
        rows = output_records(step.run_id, project_dir=project_dir)
    except ResolveOutputError as exc:
        return (), [str(exc)]
    outputs: list[PlanOutput] = []
    reasons: list[str] = []
    for row in rows:
        output = PlanOutput(slot=str(row.slot), file_name=output_export_name(row),
                            content_hash=row.content_hash,
                            container_path=_container_path(row, project_dir))
        outputs.append(output)
        if row.content_hash is None:
            why = _no_hash_reason(archive_status.get((step.run_id, row.output_name or "")))
            reasons.append(f"Run {step.run_id} ({step.label}) output "
                           f"'{output.slot}' ({output.file_name}) has no content "
                           f"hash: {why}.")
            continue
        try:
            check_entry_shape(project_dir, row.content_hash, output_repair(step.run_id))
        except ResolveOutputError as exc:
            reasons.append(str(exc))
            continue
        if with_outputs and output_location(row, project_dir) == OUTPUT_MISSING:
            reasons.append(f"Run {step.run_id} ({step.label}) output "
                           f"'{output.slot}' ({output.file_name}) is not in the "
                           f"local cache and was never pushed, so --with-outputs "
                           f"cannot carry it. {output_repair(step.run_id)}")
    return tuple(outputs), reasons


def _entry_refusals(steps: list[PlanStep], project_dir: Path
                    ) -> tuple[tuple[EntryRead, ...], list[Refusal]]:
    """Probe every entry the bundle reads by hash, each once, without pulling.

    The pipeline documents, the samples the steps read and the env content
    blobs, in step order. An entry Storage's entry read would refuse (gone
    and never pushed, or malformed) is a refusal in Storage's own words; an
    image with no captures folder is a refusal too.

    Args:
        steps: The plan's steps.
        project_dir: The project root.

    Returns:
        The entries the read can serve, and the refusals.
    """
    entries: list[EntryRead] = []
    refusals: list[Refusal] = []
    seen: set[tuple[str, str]] = set()

    def probe(run_id: int, kind: str, content_hash: str | None, name: str | None) -> None:
        if content_hash is None or (kind, content_hash) in seen:
            return
        seen.add((kind, content_hash))
        try:
            state = entry_state(content_hash, kind, name=name, project_dir=project_dir)
        except ResolveOutputError as exc:
            refusals.append(Refusal(run_id, str(exc)))
            return
        entries.append(EntryRead(kind=kind, content_hash=content_hash, name=name,
                                 state=state))

    captures: set[tuple[str, str | None]] = set()
    for step in steps:
        probe(step.run_id, KIND_PIPELINE_DOCUMENT, step.pipeline_doc_hash, None)
        for item in step.inputs:
            if item.is_sample:
                probe(step.run_id, KIND_SAMPLE, item.content_hash, item.sample_name)
        if step.env_name is None or (step.env_name, step.env_fingerprint) in captures:
            continue
        captures.add((step.env_name, step.env_fingerprint))
        probe(step.run_id, KIND_ENV_FINGERPRINT, step.env_fingerprint, None)
        try:
            image_info(step.env_name, step.env_fingerprint, project_dir)
        except CaptureError as exc:
            refusals.append(Refusal(step.run_id, (
                f"Run {step.run_id} ({step.label}): the bundle cannot carry its "
                f"image's captures: {exc}.")))
    return tuple(entries), refusals


def _with(step: PlanStep, **changes) -> PlanStep:
    """A copy of *step* with *changes*."""
    return replace(step, **changes)


def _images_to_save(steps: list[PlanStep], refusals: list[Refusal]) -> list[PlanImage]:
    """Each distinct image of *steps* once, by a reference the local daemon holds.

    Args:
        steps: The plan's steps, their pullable references filled.
        refusals: Extended with one refusal per image the daemon does not hold,
            or per image Docker could not be asked about.

    Returns:
        The images to save, in step order.
    """
    groups: dict[tuple[str, str | None], list[PlanStep]] = {}
    for step in steps:
        if step.env_name is not None and step.image is not None:
            groups.setdefault((step.env_name, step.env_fingerprint), []).append(step)
    images: list[PlanImage] = []
    for (env, fingerprint), group in groups.items():
        first = group[0]
        candidates = [*((s.invocation or {}).get("image_ref") for s in group),
                      daemon_ref(str(first.image)), first.pullable_ref]
        try:
            ref = local_image_ref(candidates)
        except RuntimeError as exc:
            refusals.append(Refusal(first.run_id, (
                f"--with-image: Docker could not be asked for the image of env "
                f"{env!r} ({exc}). Start Docker, or export without --with-image.")))
            continue
        if ref is None:
            pull = (f"pull it with `docker pull {first.pullable_ref}`"
                    if first.pullable_ref else
                    f"rebuild it with `wfc register-env {env}`")
            refusals.append(Refusal(first.run_id, (
                f"--with-image: the image of env {env!r} ({first.image}) is not on "
                f"this machine, so it cannot be saved into the bundle. "
                f"{pull[0].upper()}{pull[1:]}, or export without --with-image.")))
            continue
        images.append(PlanImage(env_name=env, env_fingerprint=fingerprint, ref=ref))
    return images


@workflow(purpose="Plan an export of runs as a rerun bundle, writing no bundle: select "
                  "the runs and every ancestor (originals only), archive their "
                  "unarchived outputs, collect every refusal and find the images "
                  "only this machine holds",
          inputs="The requested run ids; the project's database, cache, git and "
                 "image captures; an optional per-output archive progress callback",
          outputs="The ExportPlan: steps in dependency order, substitutions, "
                  "refusals and local-only image warnings")
def plan_export(
    run_ids: Iterable[int | str],
    *,
    project_dir: Path | str | None = None,
    progress_fn: Callable[[int, str, str], object] | None = None,
    with_outputs: bool = False,
    with_image: bool = False,
) -> ExportPlan:
    """Plan the export of *run_ids* and every run above them.

    Args:
        run_ids: The runs asked for.
        project_dir: The project root; the ambient one when ``None``.
        progress_fn: Passed to Storage's archive pass: called per output with
            ``(run_id, output_name, status)``.
        with_outputs: Whether the bundle carries the outputs' bytes
            (``--with-outputs``): an output no read can serve is then refused.
        with_image: Whether the bundle carries each step's image
            (``--with-image``): each distinct image must then be on this
            machine, and a local-only image is no warning, since the
            bundle carries it.

    Returns:
        The :class:`ExportPlan`. Its ``refusals`` hold every reason, so a
        surface lists them all and writes nothing.
    """
    root = Path(project_dir).resolve() if project_dir is not None else get_project_root()
    requested = tuple(dict.fromkeys(int(r) for r in run_ids))

    口 = AutoStep(step_num=1, name="Read the run graph")
    records = read_run_records()

    口 = AutoStep(step_num=2, name="Select the runs")
    selection = select_runs(records, requested)

    口 = Step(step_num=3, name="Read each step's facts",
             purpose="The run row, its method and module, and its version: the "
                     "image, env, tree, key, document hash and invocation",
             outputs="One PlanStep per selected run, in dependency order")
    originals = dict(selection.substitutions)
    with get_session() as session:
        steps = [
            _read_step(session, number, run, selection.parents.get(run_id, ()),
                       originals)
            for number, run_id in enumerate(selection.steps, start=1)
            if (run := session.get(Run, run_id)) is not None
        ]

    口 = Step(step_num=4, name="Archive the selected runs' outputs",
             purpose="Storage's archive pass over each completed step with "
                     "unarchived outputs, so every output has wfc's own hash; "
                     "nothing is hashed at export",
             critical="The pass is the only write the plan makes, and it completes "
                      "hashes the runs already owe; an output it refuses as "
                      "changed keeps no hash and is refused below")
    archive_status: dict[tuple[int, str], str] = {}
    archived = 0
    for step in steps:
        with get_session() as session:
            pending = unarchived_outputs(session, run_id=step.run_id)
        if not pending:
            continue
        口 = AutoStep(step_num=4.1, name="Archive one step's outputs")
        results = archive_outputs(root, run_id=step.run_id, progress_fn=progress_fn)
        for entry in results:
            archive_status[(int(entry["run_id"]), entry["output_name"] or "")] = \
                entry["status"]
            archived += entry["status"] == "archived"

    口 = Step(step_num=5, name="Collect every refusal",
             purpose="Each reason any step cannot be bundled, all listed before "
                     "anything is decided: not found, not completed, no "
                     "invocation, no document hash, a tree git no longer holds, "
                     "an output with no hash (or, with outputs, none a read can "
                     "serve), Storage's malformed record and entry refusals in "
                     "its own words, and an entry (document, sample, env blob) "
                     "gone and never pushed",
             outputs="The refusals, in step order; each step's outputs; the "
                     "entries the bundle reads, with those it will pull")
    refusals = [Refusal(run_id, f"Run {run_id} not found.")
                for run_id in selection.missing]
    top = git_toplevel(root)
    for index, step in enumerate(steps):
        reasons = _run_refusals(step, root, top)
        outputs, output_reasons = _output_refusals(step, root, archive_status,
                                                   with_outputs)
        steps[index] = _with(step, outputs=outputs)
        refusals.extend(Refusal(step.run_id, reason)
                        for reason in reasons + output_reasons)
    entries, entry_refusals = _entry_refusals(steps, root)
    refusals.extend(entry_refusals)

    口 = Step(step_num=6, name="Find the local-only images",
             purpose="Each step's image by its registry reference; an image with "
                     "none is held only by this machine, asked about once per "
                     "env and fingerprint",
             outputs="Each step's pullable reference; the local-only warnings; a "
                     "push record Environments cannot read is a refusal")
    local_only: list[LocalOnlyImage] = []
    states: dict[tuple[str, str | None], str] = {}
    for index, step in enumerate(steps):
        if step.env_name is None or step.image is None:
            refusals.append(Refusal(step.run_id, (
                f"Run {step.run_id} ({step.label}) records no image, so it "
                f"cannot be rerun. Re-run it to export it.")))
            continue
        try:
            ref = pullable_ref(step.env_name, step.env_fingerprint, step.image, root)
        except (ValueError, CaptureMismatchError) as exc:
            refusals.append(Refusal(step.run_id, str(exc)))
            continue
        steps[index] = _with(step, pullable_ref=ref)
        if ref is not None or with_image:
            continue
        key = (step.env_name, step.env_fingerprint)
        if key not in states:
            try:
                states[key] = image_state(step.env_name, step.env_fingerprint, root,
                                          container=step.image).state
            except CaptureMismatchError as exc:
                refusals.append(Refusal(step.run_id, str(exc)))
                continue
        local_only.append(LocalOnlyImage(run_id=step.run_id, env_name=step.env_name,
                                         image=step.image, state=states[key]))

    images: list[PlanImage] = []
    if with_image:
        口 = Step(step_num=7, name="Find each image to save",
                 purpose="Under --with-image, each distinct image once, by the first "
                         "reference the local daemon holds it under: the one the run "
                         "ran, the recorded one, then the registry one",
                 outputs="The images the apply saves; an image this machine does not "
                         "hold is a refusal naming it")
        images = _images_to_save(steps, refusals)

    口 = Step(step_num=8, name="Assemble the plan",
             purpose="One value the surfaces print or serialise",
             outputs="The ExportPlan")
    return ExportPlan(requested=requested, steps=tuple(steps),
                      substitutions=selection.substitutions,
                      refusals=tuple(refusals), local_only=tuple(local_only),
                      archived=archived, entries=entries, with_outputs=with_outputs,
                      with_image=with_image, images=tuple(images))
