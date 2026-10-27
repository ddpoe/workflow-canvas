"""The apply: write the bundle a plan describes, as one zip, all or nothing.

The zip is filled under a temporary name beside ``out`` and renamed into
place, so a failure leaves no bundle. Its bytes depend only on the plan and
the export time: members are written in sorted order with a fixed timestamp
and fixed modes, and every JSON member has sorted keys.

Layout::

    README.md                         what ran, from what, and how to rerun it
    rerun.py                          reruns every step in Docker and compares outputs
    ro-crate-metadata.json            the same facts as a Process Run Crate 0.6
    pipeline.json                    the pipeline document (pipelines/<hash>.json
                                      for each one when the steps ran under several)
    samples/<name>/<file>             each sample the steps read, by its hash
                                      (<name>@<hash12>/ per version when the
                                      steps read several)
    steps/<n>-run-<id>-<module.method>-<sample>/
        command.sh                    the recorded command, host sides as variables
        code/                         the version's files, from its git tree
        outputs.json                  slot, file name, container path, hash
        logs/                         the run's logs, or not-kept.txt
    envs/<env>/                       the image's captures and image-content.json
    images/<env folder>.tar           under --with-image, the image (docker save)
    outputs/<step folder>/<file>      under --with-outputs
    wfc-run.json                      every key input as a value, the invocations

Every byte is read through Storage (the entry read, the output resolver),
git (the version tree) and Environments (the captures); nothing reads the
cache's paths itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import AutoStep, Step, workflow

from .. import layout
from ..environments import capture_files, save_image
from ..identity import (
    KEY_RECIPE,
    SampleIdentity,
    UpstreamRunIdentity,
    build_input_fingerprint,
)
from ..persistence import project_root as get_project_root
from ..storage import (
    KIND_ENV_FINGERPRINT,
    KIND_PIPELINE_DOCUMENT,
    KIND_SAMPLE,
    read_entry,
    resolve_output,
)
from ..version import archive_tree, installed_wfc_version
from .command import command_script
from .describe import render_crate, render_readme
from .plan import ExportPlan, PlanStep, invocation_input_paths

#: The bundle format's name and version, in ``wfc-run.json``.
FORMAT = "wfc-run"
FORMAT_VERSION = 1

#: The timestamp every member carries, so two exports of one plan are equal.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

#: A member this large needs zip64 sizes; streamed members copy in chunks.
_ZIP64 = 2 ** 31
_COPY_CHUNK = 1024 * 1024

#: The rerun script every bundle carries, copied from this package byte for byte.
#: It is read as a file, never imported: it runs without wfc.
RERUN_SCRIPT = Path(__file__).with_name("rerun.py")
_EXECUTABLE = {"rerun.py"}

#: How the key is composed, in words, for a reader of ``wfc-run.json``.
KEY_RECIPE_TEXT = (
    "sha256 of the compact JSON list [key_recipe, code_fingerprint, params, "
    "input_fingerprint, env_fingerprint, method_identity] with sorted keys; "
    "input_fingerprint is over the upstream (input slot, source slot, cache key) "
    "and sample (input slot, content hash) parts"
)


class ExportRefusedError(Exception):
    """The bundle cannot be written; ``reasons`` lists every reason."""

    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("\n".join(self.reasons))


@dataclass(frozen=True)
class BundleResult:
    """A written bundle.

    Attributes:
        path: The zip.
        size: Its size in bytes.
        steps: How many steps it holds.
    """

    path: Path
    size: int
    steps: int


def out_refusal(out: Path | str) -> str | None:
    """Why the bundle cannot be written to *out*, or ``None`` when it can.

    Args:
        out: The zip file to write (``--out``).

    Returns:
        The refusal when *out* names a directory: ``--out`` names the zip
        file, never a folder to put it in.
    """
    if Path(out).is_dir():
        return f"--out names the directory {out}; name the zip file to write instead."
    return None


def safe_sample(sample: str | None) -> str:
    """The sample's part of a step folder name.

    Args:
        sample: The run's sample, ``__all__`` for a collapsed run, or ``None``.

    Returns:
        ``all-samples``, ``no-sample``, or the name with every character
        outside ``[A-Za-z0-9._-]`` replaced by ``_``.
    """
    if sample is None:
        return "no-sample"
    if sample == "__all__":
        return "all-samples"
    return re.sub(r"[^A-Za-z0-9._-]", "_", sample) or "_"


def step_folder(step: PlanStep) -> str:
    """``<n>-run-<id>-<module.method>-<sample>``, a step's folder under ``steps/``."""
    label = re.sub(r"[^A-Za-z0-9._-]", "_", step.label)
    return f"{step.number}-run-{step.run_id}-{label}-{safe_sample(step.sample)}"


def _json_bytes(value) -> bytes:
    """A JSON member's bytes: sorted keys, two-space indent, LF."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _tree_files(path: Path) -> dict[str, Path]:
    """Every file of a file or directory, by POSIX path relative to the directory."""
    if path.is_file():
        return {"": path}
    return {p.relative_to(path).as_posix(): p
            for p in sorted(path.rglob("*")) if p.is_file()}


def _add_tree(members: dict[str, bytes | Path], prefix: str, path: Path) -> None:
    """Add a file at *prefix*, or a directory's files beneath it."""
    for rel, source in _tree_files(path).items():
        members[f"{prefix}/{rel}" if rel else prefix] = source


def _input_paths(step: PlanStep) -> dict[str, list[str]]:
    """The container paths dispatch forwarded per input slot, each a list."""
    return invocation_input_paths(step.invocation)


def _key_block(step: PlanStep) -> dict:
    """Every input of the step's cache key, as values."""
    upstream = [i for i in step.inputs if not i.is_sample]
    samples = [i for i in step.inputs if i.is_sample]
    input_fingerprint = build_input_fingerprint(
        [UpstreamRunIdentity(input_slot=i.slot, source_slot=i.source_slot or "",
                             run_id=int(i.recorded_run_id or 0), cache_key=i.cache_key)
         for i in upstream],
        [SampleIdentity(input_slot=i.slot, content_hash=i.content_hash or "")
         for i in samples],
    )
    return {
        "cache_key": step.cache_key,
        "key_recipe": step.key_recipe,
        "recipe": KEY_RECIPE_TEXT,
        "code_fingerprint": step.code_fingerprint,
        "params": step.params,
        "env_fingerprint": step.key_env_fingerprint,
        "method_identity": step.label,
        "input_fingerprint": input_fingerprint,
        "upstream": [{"input_slot": i.slot, "source_slot": i.source_slot,
                      "run_id": i.recorded_run_id, "step_run_id": i.run_id,
                      "cache_key": i.cache_key} for i in upstream],
        "samples": [{"input_slot": i.slot, "name": i.sample_name,
                     "content_hash": i.content_hash} for i in samples],
    }


def _sample_members(plan: ExportPlan) -> dict[tuple[str | None, str | None], str]:
    """Where each version of each sample sits in the bundle, by (name, hash).

    ``samples/<name>/<file>``; a name the steps read in two or more versions
    gets one folder per version, ``samples/<name>@<hash[:12]>/<file>``, so
    each keeps its own bytes.

    Args:
        plan: The export plan.

    Returns:
        Each sample input's member path, by its name and the hash the run read.
    """
    files: dict[tuple[str | None, str | None], str | None] = {}
    for step in plan.steps:
        for item in step.inputs:
            if item.is_sample:
                files.setdefault((item.sample_name, item.content_hash), item.sample_file)
    names = [name for name, _ in files]
    members: dict[tuple[str | None, str | None], str] = {}
    for (name, content_hash), file in files.items():
        folder = str(name)
        if names.count(name) > 1:
            folder = f"{name}@{(content_hash or '')[:12]}"
        members[(name, content_hash)] = f"samples/{folder}/{file or content_hash}"
    return members


def _step_inputs(step: PlanStep, numbers: dict[int, int],
                 samples: dict[tuple[str | None, str | None], str]) -> list[dict]:
    """The step's inputs, each paired with the container path it was read at."""
    paths = _input_paths(step)
    taken: dict[str, int] = {}
    listed: list[dict] = []
    for item in step.inputs:
        index = taken.get(item.slot, 0)
        taken[item.slot] = index + 1
        slot_paths = paths.get(item.slot, [])
        entry: dict = {"slot": item.slot,
                       "container_path": slot_paths[index] if index < len(slot_paths)
                       else None}
        if item.is_sample:
            entry["from"] = {"sample": item.sample_name,
                             "path": samples[(item.sample_name, item.content_hash)]}
        else:
            entry["from"] = {"step": numbers.get(int(item.run_id or 0)),
                             "run_id": item.run_id, "slot": item.source_slot}
        listed.append(entry)
    return listed


def _env_folders(plan: ExportPlan) -> dict[tuple[str, str | None], str]:
    """Each image's folder under ``envs/``: the env, or ``<env>@<fp[:12]>`` when shared."""
    images = sorted({(s.env_name, s.env_fingerprint) for s in plan.steps
                     if s.env_name is not None}, key=lambda k: (k[0], k[1] or ""))
    names = [env for env, _ in images]
    return {(env, fp): (env if names.count(env) == 1 else f"{env}@{(fp or '')[:12]}")
            for env, fp in images}


def _write_zip(members: dict[str, bytes | Path], out: Path) -> int:
    """Write the members into *out* atomically and deterministically.

    Args:
        members: The bundle's members by name: bytes, or a file to copy.
        out: The zip's path.

    Returns:
        The zip's size in bytes.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = 0o755 if name in _EXECUTABLE or name.endswith("/command.sh") else 0o644
                info.external_attr = (0o100000 | mode) << 16
                source = members[name]
                if isinstance(source, Path):
                    # Streamed: a saved image can be larger than memory.
                    with source.open("rb") as reader, archive.open(
                            info, "w", force_zip64=source.stat().st_size >= _ZIP64) as writer:
                        shutil.copyfileobj(reader, writer, _COPY_CHUNK)
                else:
                    archive.writestr(info, source)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return out.stat().st_size


@workflow(purpose="Write the rerun bundle a plan describes as one zip, all or nothing: "
                  "the pipeline documents and samples through Storage's entry read, "
                  "each step's command, code, outputs record and logs, the images' "
                  "captures and env blobs, the outputs under --with-outputs, and "
                  "wfc-run.json",
          inputs="An ExportPlan with no refusal; the zip's path; the project's "
                 "cache, git trees, run archives and captures",
          outputs="The zip renamed into place, and its path, size and step count")
def write_bundle(plan: ExportPlan, out: Path | str, *,
                 project_dir: Path | str | None = None,
                 progress_fn: Callable[[str], object] | None = None,
                 exported_at: datetime | None = None) -> BundleResult:
    """Write the bundle *plan* describes to *out*.

    Args:
        plan: The export plan.
        out: The zip file to write.
        project_dir: The project root; the ambient one when ``None``.
        progress_fn: Called with each part's name as it is read.
        exported_at: The export time; now when ``None``.

    Returns:
        The written bundle.

    Raises:
        ExportRefusedError: The plan has refusals, or *out* is a directory;
            nothing is written.
    """
    口 = Step(step_num=1, name="Refuse an incomplete plan",
             purpose="A plan with any refusal, or an --out that names a directory, "
                     "writes nothing")
    root = Path(project_dir).resolve() if project_dir is not None else get_project_root()
    out = Path(out)
    if not plan.ok:
        raise ExportRefusedError([r.reason for r in plan.refusals])
    refused_out = out_refusal(out)
    if refused_out is not None:
        raise ExportRefusedError([refused_out])
    report = progress_fn or (lambda _part: None)
    members: dict[str, bytes | Path] = {}
    numbers = {s.run_id: s.number for s in plan.steps}

    with tempfile.TemporaryDirectory(prefix="wfc-bundle-") as scratch:
        口 = Step(step_num=2, name="Read the pipeline documents",
                 purpose="Each distinct document by its hash through Storage's entry "
                         "read: pipeline.json, or pipelines/<hash>.json for several")
        doc_hashes = sorted({s.pipeline_doc_hash for s in plan.steps
                             if s.pipeline_doc_hash})
        documents: dict[str, str] = {}
        for doc_hash in doc_hashes:
            documents[doc_hash] = ("pipeline.json" if len(doc_hashes) == 1
                                   else f"pipelines/{doc_hash}.json")
            report(documents[doc_hash])
            members[documents[doc_hash]] = read_entry(doc_hash, KIND_PIPELINE_DOCUMENT,
                                                      project_dir=root)

        口 = Step(step_num=3, name="Read the samples",
                 purpose="Each sample the steps read, by the hash the run read, "
                         "through Storage's entry read; never a workspace view")
        samples = _sample_members(plan)
        for step in plan.steps:
            for item in step.inputs:
                if not item.is_sample or item.content_hash is None:
                    continue
                prefix = samples[(item.sample_name, item.content_hash)]
                if any(name == prefix or name.startswith(prefix + "/")
                       for name in members):
                    continue
                report(prefix)
                _add_tree(members, prefix, read_entry(
                    item.content_hash, KIND_SAMPLE, name=item.sample_name,
                    project_dir=root))

        口 = Step(step_num=4, name="Write each step's folder",
                 purpose="command.sh, the version's code from its git tree, "
                         "outputs.json and the run's logs")
        folders = {s.run_id: step_folder(s) for s in plan.steps}
        for step in plan.steps:
            口 = Step(step_num=4.1, name="One step's folder",
                     purpose="The step's command, code, outputs record and logs")
            base = f"steps/{folders[step.run_id]}"
            report(base)
            inputs = _step_inputs(step, numbers, samples)
            members[f"{base}/command.sh"] = command_script(
                number=step.number, count=len(plan.steps), run_id=step.run_id,
                label=step.label, version_label=step.version_label, sample=step.sample,
                started_at=step.started_at, env_name=step.env_name, image=step.image,
                folder=folders[step.run_id], invocation=step.invocation or {},
                inputs=[(i["slot"], i["container_path"] or "",
                         i["from"].get("path")
                         or f"step {i['from'].get('step')} output {i['from'].get('slot')}")
                        for i in inputs],
            ).encode("utf-8")
            code_dir = Path(scratch) / f"code-{step.run_id}"
            code_dir.mkdir()
            archive_tree(root, str(step.tree_hash), code_dir)
            _add_tree(members, f"{base}/code", code_dir)
            members[f"{base}/outputs.json"] = _json_bytes([
                {"slot": o.slot, "file_name": o.file_name,
                 "container_path": o.container_path, "content_hash": o.content_hash,
                 "run_id": step.run_id} for o in step.outputs])
            logs = sorted(layout.run_archive_dir(root, step.run_id).glob("*.log"))
            for log in logs:
                members[f"{base}/logs/{log.name}"] = log
            if not logs:
                members[f"{base}/logs/not-kept.txt"] = (
                    b"wfc kept no log for this run.\n")

        口 = Step(step_num=5, name="Write each image's env folder",
                 purpose="The captures folder's files and the env content blob, "
                         "read through Storage's entry read; under --with-image, "
                         "the image saved once as images/<env folder>.tar")
        env_folders = _env_folders(plan)
        saved: dict[tuple[str, str | None], list] = {}
        for image in plan.images:
            saved.setdefault((image.env_name, image.env_fingerprint), []).append(image)
        for (env, fingerprint), folder in env_folders.items():
            report(f"envs/{folder}")
            for rel, data in capture_files(env, fingerprint, root).items():
                members[f"envs/{folder}/{rel}"] = data
            if fingerprint:
                members[f"envs/{folder}/image-content.json"] = read_entry(
                    fingerprint, KIND_ENV_FINGERPRINT, project_dir=root)
            for image in saved.get((env, fingerprint), ()):
                report(f"images/{folder}.tar")
                口 = AutoStep(step_num=5.1, name="Save the image")
                tar = save_image(image.ref, Path(scratch) / f"image-{len(members)}.tar")
                members[f"images/{folder}.tar"] = tar

        口 = Step(step_num=6, name="Copy the outputs",
                 purpose="Under --with-outputs, each output's bytes through Storage's "
                         "output resolver, pulled when pushed")
        if plan.with_outputs:
            for step in plan.steps:
                for output in step.outputs:
                    name = f"outputs/{folders[step.run_id]}/{output.file_name}"
                    report(name)
                    path, _row = resolve_output(step.run_id, output.slot, pull=True,
                                                project_dir=root)
                    _add_tree(members, name, path)

        口 = Step(step_num=7, name="Write wfc-run.json",
                 purpose="Every key input as a value, each step's invocation and "
                         "inputs, and the output hashes")
        moment = exported_at or datetime.now(UTC)
        manifest = {
            "format": FORMAT, "version": FORMAT_VERSION,
            "exported_at": moment.isoformat(),
            "exported_by_wfc": installed_wfc_version(),
            "key_recipe": KEY_RECIPE,
            "requested": list(plan.requested),
            "substitutions": [{"run_id": hit, "original_run_id": original}
                              for hit, original in plan.substitutions],
            "with_outputs": plan.with_outputs,
            "with_image": plan.with_image,
            "pipeline_documents": documents,
            "steps": [{
                "number": s.number, "run_id": s.run_id, "folder": folders[s.run_id],
                "module": s.module, "method": s.method,
                "version_label": s.version_label, "tree_hash": s.tree_hash,
                "sample": s.sample, "git_commit": s.git_commit,
                "started_at": s.started_at, "finished_at": s.finished_at,
                "wfc_version": s.wfc_version,
                "pipeline_doc_hash": s.pipeline_doc_hash,
                "pipeline_document": documents.get(s.pipeline_doc_hash or ""),
                "parent_run_ids": list(s.parent_run_ids),
                "env": {"name": s.env_name, "fingerprint": s.env_fingerprint,
                        "image": s.image, "pullable_ref": s.pullable_ref,
                        "folder": (f"envs/{env_folders[(s.env_name, s.env_fingerprint)]}"
                                   if s.env_name is not None else None)},
                "key": _key_block(s),
                "invocation": s.invocation,
                "inputs": _step_inputs(s, numbers, samples),
                "outputs": [{"slot": o.slot, "file_name": o.file_name,
                             "container_path": o.container_path,
                             "content_hash": o.content_hash}
                            for o in s.outputs],
            } for s in plan.steps],
        }
        members["wfc-run.json"] = _json_bytes(manifest)

        口 = AutoStep(step_num=8, name="Write the README")
        readme = render_readme(manifest, project=root.name)
        members["README.md"] = readme.encode("utf-8")

        口 = AutoStep(step_num=9, name="Write the RO-Crate")
        crate = render_crate(manifest, project=root.name)
        members["ro-crate-metadata.json"] = _json_bytes(crate)

        口 = Step(step_num=10, name="Add rerun.py",
                 purpose="The package's stdlib-only rerun script, its bytes as shipped, "
                         "which replays every step and compares the outputs")
        members["rerun.py"] = RERUN_SCRIPT

        口 = Step(step_num=11, name="Write the zip",
                 purpose="Members in sorted order with fixed times and modes, into a "
                         "temporary file renamed into place",
                 critical="Any failure removes the temporary file: no bundle is left")
        out.parent.mkdir(parents=True, exist_ok=True)
        size = _write_zip(members, out)
    return BundleResult(path=out, size=size, steps=len(plan.steps))
