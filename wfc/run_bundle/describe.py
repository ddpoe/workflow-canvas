"""The bundle's descriptions: ``README.md`` for people, ``ro-crate-metadata.json`` for tools.

Both are rendered from the ``wfc-run.json`` value the apply writes, so the
three say the same thing about the same runs.

The README is the entry point: the project, commit and wfc version the runs
came from, one line per cache-hit substitution, one table row per step (its
folder, run, method and version, sample, env and image, inputs and where
they came from, outputs and their hashes), the parameters, the images only
the exporting machine holds, how to rerun, and what to do when the rerun
says an output is DIFFERENT.

The crate is a Process Run Crate, version 0.6
(https://w3id.org/ro/wfrun/process/0.6), which extends RO-Crate 1.3. It is
hand-written JSON-LD. Each step is a ``CreateAction`` (``#run-<id>``) whose
``instrument`` is the method version (a ``SoftwareApplication``), whose
``containerImage`` is ``#image-<env>`` and whose ``environment`` is the
recorded ``WFC_*`` values; its ``object`` and ``result`` are the inputs and
outputs. A file in the bundle is a ``File`` (a directory a ``Dataset``) by its
bundle-relative path; an output the bundle holds no bytes of is a local
``#output-<run>-<slot>`` entity, since RO-Crate requires a relative id to
name a file that is present. A file's hash is the workflow-run ``md5`` term;
a directory's wfc hash (the MD5 of DVC's ``.dir`` manifest) is its
``identifier``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from axiom_annotations import task

from ..identity import DIR_HASH_SUFFIX

#: The profile the crate conforms to, by its versioned permalink.
PROCESS_RUN_CRATE = "https://w3id.org/ro/wfrun/process/0.6"
#: The RO-Crate specification the profile extends.
RO_CRATE = "https://w3id.org/ro/crate/1.3"
#: The crate's JSON-LD context: RO-Crate's, and the workflow-run terms.
CRATE_CONTEXT = ["https://w3id.org/ro/crate/1.3/context",
                 "https://w3id.org/ro/terms/workflow-run/context"]
_DOCKER_IMAGE = "https://w3id.org/ro/terms/workflow-run#DockerImage"
_COMPLETED = "http://schema.org/CompletedActionStatus"

#: The crate's license statement: wfc cannot know the data's license.
LICENSE_TEXT = ("No license was stated when this bundle was exported. Ask the "
                "project's owner before reusing its files.")

#: The README's how-to-rerun line (rerun.py's floor is Python 3.11).
RERUN_LINE = "To rerun and check: `python rerun.py` (Python 3.11 or newer, and Docker)."

#: The README's statement of rerun.py's image check, made once.
IMAGE_CHECK_LINE = ("Before it runs a step, `rerun.py` checks the step's image against "
                    "the recorded env fingerprint, and does not use an image whose "
                    "contents differ.")

#: The rerun folder's subfolder holding the rerun's files, as rerun.py names it.
#: It is rerun.py's own folder, not wfc's project mount.
RERUN_FILES_FOLDER = "work"

#: What the README and rerun.py's report say when the bundle holds no outputs.
HASHES_ONLY_TEXT = ("This bundle holds only the hashes of the original outputs. Ask the "
                    "sender for a bundle exported with --with-outputs.")


def _is_dir(content_hash: str | None) -> bool:
    """Whether a wfc hash is a directory's."""
    return bool(content_hash) and str(content_hash).endswith(DIR_HASH_SUFFIX)


def _sample_text(sample: str | None) -> str:
    """A run's sample as the README says it."""
    if sample is None:
        return "no sample"
    if sample == "__all__":
        return "all samples"
    return sample


def _and(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _distinct(values: Iterable[str | None]) -> list[str]:
    """The non-empty values, each once, in first-seen order."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _cell(text: str) -> str:
    """Text for a Markdown table cell: pipes escaped, no line breaks."""
    return text.replace("|", "\\|").replace("\n", " ")


def _method(step: Mapping) -> str:
    return f"{step['module']}.{step['method']}"


def _version(step: Mapping) -> str:
    label = step.get("version_label")
    return f"v{label}" if label is not None else "version not recorded"


def _output_name(output: Mapping) -> str:
    """An output's file name, a directory's with a trailing ``/``."""
    name = str(output["file_name"])
    return name + "/" if _is_dir(output.get("content_hash")) else name


def _step_title(step: Mapping) -> str:
    return f"{_method(step)} on {_sample_text(step.get('sample'))}"


def _readme_title(manifest: Mapping, by_run: Mapping[int, Mapping]) -> str:
    """The README's heading: the requested runs."""
    originals = {s["run_id"]: s["original_run_id"] for s in manifest["substitutions"]}
    requested = list(manifest["requested"])
    if len(requested) == 1:
        step = by_run.get(originals.get(requested[0], requested[0]))
        if step is not None:
            return f"# Run {requested[0]} — {_step_title(step)}"
        return f"# Run {requested[0]}"
    return "# Runs " + _and([str(r) for r in requested])


def _provenance_line(manifest: Mapping, project: str) -> str:
    """Where and when the runs came from; commits shortened to 12 characters."""
    steps = manifest["steps"]
    commits = _distinct((s.get("git_commit") or "")[:12] for s in steps)
    versions = _distinct(s.get("wfc_version") for s in steps)
    commit_text = (f"at commit {commits[0]}" if len(commits) == 1
                   else f"at commits {_and(commits)}" if commits
                   else "at a commit wfc did not record")
    version_text = (f"wfc {_and(versions)}" if versions
                    else "wfc version not recorded")
    return (f"Exported {str(manifest['exported_at'])[:10]} from project `{project}` "
            f"{commit_text} ({version_text}), by wfc {manifest['exported_by_wfc']}.")


def _saved_image(env: Mapping) -> str:
    """The bundle member an image is saved as under ``--with-image``.

    Args:
        env: A step's ``env`` value from ``wfc-run.json``.

    Returns:
        ``images/<env folder>.tar``, the name the apply writes and
        ``rerun.py`` loads.
    """
    folder = str(env.get("folder") or "").split("/", 1)[-1]
    return f"images/{folder}.tar"


def _image_text(step: Mapping, with_image: bool = False) -> str:
    """The env and the image a step ran in.

    Args:
        step: A step of ``wfc-run.json``.
        with_image: Whether the bundle holds each image (``--with-image``).

    Returns:
        The table cell's text. A never-pushed image is marked local only,
        unless the bundle holds it; a held image names its bundle member.
    """
    env = step["env"]
    if env.get("name") is None:
        return "no env recorded"
    if env.get("pullable_ref"):
        text = f"{env['name']} → `{env['pullable_ref']}`"
        if with_image:
            text += f" (also saved in this bundle as `{_saved_image(env)}`)"
        return text
    if with_image:
        return (f"{env['name']} → `{env.get('image')}` (never pushed; saved in this "
                f"bundle as `{_saved_image(env)}`)")
    return f"{env['name']} → `{env.get('image')}` (local only: never pushed)"


def _inputs_text(step: Mapping) -> str:
    """Each input and where it came from."""
    parts = []
    for item in step["inputs"]:
        source = item["from"]
        if "sample" in source:
            parts.append(f"`{source['path']}`")
        else:
            parts.append(f"step {source.get('step')} (run {source.get('run_id')}) "
                         f"`{source.get('slot')}`")
    return "; ".join(parts) or "none"


def _outputs_text(step: Mapping) -> str:
    """Each output and its recorded hash."""
    return "; ".join(f"`{_output_name(o)}` `{o['content_hash']}`"
                     for o in step["outputs"]) or "none"


def _different_lines(with_outputs: bool) -> list[str]:
    """The README's "If a step says DIFFERENT" section, for a receiver without wfc.

    Args:
        with_outputs: Whether the bundle holds the outputs' bytes; without
            them, step 2 says only that the bundle holds their hashes.

    Returns:
        The section's lines, ending with a blank line.
    """
    if with_outputs:
        compare = ("2. Compare the rerun's file with the original in "
                   "`outputs/<step folder>/`: open both figures, or compare tables by "
                   "value with a tolerance. `rerun.py` names both files for the first "
                   "difference.")
    else:
        compare = "2. " + HASHES_ONLY_TEXT
    where = ("The rerun folder is `rerun-<UTC time>/` next to `rerun.py`, or `DIR/` when "
             "it ran with `--work DIR`. It holds the rerun's files in "
             f"`{RERUN_FILES_FOLDER}/` (`rerun-<UTC time>/{RERUN_FILES_FOLDER}/` or "
             f"`DIR/{RERUN_FILES_FOLDER}/`) and each step's logs in "
             "`logs/<step folder>/`.")
    return [
        "## If a step says DIFFERENT",
        "",
        "`same` means the rerun wrote exactly the file that was recorded. DIFFERENT "
        "means the bytes differ. On its own that does not mean the result changed. "
        "Common causes that leave the science unchanged:",
        "",
        "- the file records when it was made (PDFs and many image formats store a "
        "creation date);",
        "- floating-point arithmetic: a different CPU or number of threads can change "
        "the last digits of a number;",
        "- rows written in whatever order a parallel job finished;",
        "- randomness without a fixed seed.",
        "",
        where,
        "",
        "### What to do",
        "",
        "1. Start at the first difference `rerun.py` names. Later differences often "
        "follow from it.",
        compare,
        "3. Small numeric differences that stay small further down the pipeline are "
        "expected between machines. A difference that changes a conclusion needs a full "
        "investigation from that step. Examples are a gene crossing a significance "
        "threshold, or a sample landing in a different cluster. Compare the input "
        "hashes, and read the step's logs: the original run's are in "
        "`steps/<step folder>/logs/`, the rerun's in the rerun folder's `logs/<step folder>/`.",
        "",
    ]


def _contents_lines(manifest: Mapping) -> list[str]:
    """The README's list of what the bundle holds."""
    documents = sorted(manifest["pipeline_documents"].values())
    lines = [
        "## What is in this bundle",
        "",
        "- `rerun.py`: reruns every step in Docker from its recorded invocation and "
        "reports whether each output is the same as the recorded one (standard library "
        "only; `python rerun.py --help`).",
        "- `wfc-run.json`: every input of each step's cache key, as values, "
        "each step's recorded invocation, and each output's hash.",
        "- `ro-crate-metadata.json`: the same facts as a Process Run Crate "
        "(RO-Crate), for tools.",
        f"- {_and([f'`{d}`' for d in documents]) or 'No pipeline document'}: the "
        f"pinned pipeline document{'s' if len(documents) > 1 else ''} the runs ran under.",
        "- `samples/`: every sample the steps read, in `samples/<name>/`; a sample "
        "the steps read in several versions has one folder per version, "
        "`samples/<name>@<hash12>/`, named by the first 12 characters of its hash.",
        "- `steps/<n>-run-<id>-<module.method>-<sample>/`: each step's `command.sh` "
        "(the exact `docker run`, its comments mapping each container path to the "
        "bundle file it read), `code/` (the method version's files), `outputs.json` "
        "(each output's recorded hash) and `logs/`.",
        "- `envs/<env>/`: each image's captures and `image-content.json`, the "
        "content its fingerprint is over.",
    ]
    if manifest.get("with_image"):
        lines.append("- `images/<env folder>.tar`: each image, saved with `docker save`; "
                     "`rerun.py` loads it when this machine does not have the image.")
    if manifest.get("with_outputs"):
        lines.append("- `outputs/<step folder>/`: every output's bytes.")
    return lines


@task(purpose="Render the bundle's README: the project, commit and wfc version the "
              "runs came from, each cache-hit substitution, one row per step, the "
              "parameters, the local-only images, how to rerun and what a "
              "DIFFERENT output means",
      inputs="The wfc-run.json value the apply writes; the project's name",
      outputs="The README's Markdown text")
def render_readme(manifest: Mapping, *, project: str) -> str:
    """Render ``README.md`` from the bundle's ``wfc-run.json`` value.

    Args:
        manifest: The ``wfc-run.json`` value.
        project: The project's name.

    Returns:
        The README's text, LF line endings.
    """
    steps = manifest["steps"]
    with_image = bool(manifest.get("with_image"))
    by_run = {s["run_id"]: s for s in steps}
    lines = [_readme_title(manifest, by_run), "", _provenance_line(manifest, project)]
    if manifest["substitutions"]:
        lines.append("")
    for sub in manifest["substitutions"]:
        lines.append(f"Run {sub['run_id']} reused run {sub['original_run_id']} (same "
                     f"cache key), so run {sub['original_run_id']} is the step in this "
                     f"bundle.")
    lines += ["", "| Step | Run | Method (version) | Sample | Env → image | Inputs | "
                  "Outputs |",
              "|---|---|---|---|---|---|---|"]
    for step in steps:
        cells = [f"[{step['number']}](steps/{step['folder']}/)", str(step["run_id"]),
                 f"{_method(step)} ({_version(step)})",
                 _sample_text(step.get("sample")), _image_text(step, with_image),
                 _inputs_text(step), _outputs_text(step)]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")

    lines += ["", "## Parameters", ""]
    for step in steps:
        params = step["key"].get("params")
        text = (f"`{json.dumps(params, sort_keys=True)}`" if params else "none")
        lines.append(f"- Step {step['number']} (run {step['run_id']}): {text}")

    # Under --with-image the bundle holds every image, so none is the
    # exporting machine's alone.
    local: dict[str, list[Mapping]] = {}
    for step in steps:
        if (not with_image and step["env"].get("name") is not None
                and not step["env"].get("pullable_ref")):
            local.setdefault(str(step["env"].get("image")), []).append(step)
    if local:
        lines += ["", "## Images only the exporting machine holds", ""]
        for image, users in local.items():
            named = _and([f"step {s['number']} (run {s['run_id']})" for s in users])
            lines.append(f"- `{image}` (env {users[0]['env']['name']}), used by {named}, "
                         f"was never pushed to a registry. The bundle names it; once "
                         f"the exporting machine deletes it, "
                         f"{'this step' if len(users) == 1 else 'these steps'} can no "
                         f"longer be rerun or compared.")

    lines += ["", "## Rerun and check", "", RERUN_LINE, "", IMAGE_CHECK_LINE, "",
              "Each step's `command.sh` is the `docker run` that ran, with the host "
              "side of each mount as a shell variable.", ""]
    lines += _different_lines(bool(manifest.get("with_outputs")))
    lines += _contents_lines(manifest)
    return "\n".join(lines) + "\n"


def _image_entity(entity_id: str, env: Mapping, with_image: bool = False) -> dict:
    """A step's image as a workflow-run ``ContainerImage``.

    Args:
        entity_id: The entity's ``@id``, ``#image-<env folder>``.
        env: The step's ``env`` value from ``wfc-run.json``.
        with_image: Whether the bundle holds the image (``--with-image``);
            the description then names its bundle member.

    Returns:
        The entity.
    """
    ref = str(env.get("pullable_ref") or env.get("image") or "")
    bare = ref.split("://", 1)[-1]
    name, _, digest = bare.partition("@")
    tag = None
    if ":" in name.rsplit("/", 1)[-1]:
        name, tag = name.rsplit(":", 1)
    registry = None
    first, sep, rest = name.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        registry, name = first, rest
    entity: dict = {"@id": entity_id, "@type": "ContainerImage",
                    "additionalType": {"@id": _DOCKER_IMAGE}, "name": name,
                    "alternateName": ref,
                    "description": (f"The image of env {env.get('name')}, wfc env "
                                    f"fingerprint {env.get('fingerprint')}"
                                    + ("" if env.get("pullable_ref")
                                       else "; never pushed to a registry")
                                    + (f"; saved in this bundle as {_saved_image(env)}"
                                       if with_image else ""))}
    if registry:
        entity["registry"] = registry
    if tag:
        entity["tag"] = tag
    if digest.startswith("sha256:"):
        entity["sha256"] = digest.split(":", 1)[1]
    return entity


def _hashed(entity: dict, content_hash: str | None) -> dict:
    """Add wfc's hash: ``md5`` for a file, ``identifier`` for a directory."""
    if content_hash:
        if _is_dir(content_hash):
            entity["identifier"] = content_hash
            entity["description"] = ("wfc content hash: the MD5 of DVC's .dir "
                                     "manifest of the directory")
        else:
            entity["md5"] = content_hash
    return entity


@task(purpose="Render the bundle's ro-crate-metadata.json, a Process Run Crate 0.6: "
              "each step a CreateAction naming its method version, image, "
              "environment, and inputs and outputs by hash",
      inputs="The wfc-run.json value the apply writes; the project's name",
      outputs="The crate's JSON-LD value")
def render_crate(manifest: Mapping, *, project: str) -> dict:
    """Render ``ro-crate-metadata.json`` from the bundle's ``wfc-run.json`` value.

    Args:
        manifest: The ``wfc-run.json`` value.
        project: The project's name.

    Returns:
        The crate's JSON-LD value: ``@context`` and a flat ``@graph``.
    """
    steps = manifest["steps"]
    embedded = bool(manifest.get("with_outputs"))
    entities: dict[str, dict] = {}
    parts: list[str] = []

    def add(entity: dict, *, part: bool = False) -> dict:
        if entity["@id"] not in entities:
            entities[entity["@id"]] = entity
            if part:
                parts.append(entity["@id"])
        return {"@id": entity["@id"]}

    add({"@id": "README.md", "@type": "File", "name": "README",
         "encodingFormat": "text/markdown",
         "description": "What ran, from what, and how to rerun it"}, part=True)
    add({"@id": "rerun.py", "@type": "File", "name": "rerun script",
         "encodingFormat": "text/x-python",
         "description": "Reruns every step in Docker from its recorded invocation and "
                        "compares each output's hash with the recorded one (Python 3.11 "
                        "or newer, standard library only)"}, part=True)
    add({"@id": "wfc-run.json", "@type": "File", "name": "wfc run record",
         "encodingFormat": "application/json",
         "description": "Every cache-key input per step, as values, and each "
                        "step's recorded invocation"}, part=True)
    for doc_hash, member in sorted(manifest["pipeline_documents"].items()):
        add(_hashed({"@id": member, "@type": "File", "name": "pipeline document",
                     "encodingFormat": "application/json",
                     "description": "The pinned pipeline document the runs ran under"},
                    doc_hash), part=True)

    outputs: dict[tuple[int, str], dict] = {}
    for step in steps:
        for output in step["outputs"]:
            directory = _is_dir(output.get("content_hash"))
            if embedded:
                entity_id = f"outputs/{step['folder']}/{output['file_name']}"
                entity_id += "/" if directory else ""
            else:
                entity_id = f"#output-{step['run_id']}-{output['slot']}"
            entity = {"@id": entity_id, "@type": "Dataset" if directory else "File",
                      "name": f"{_output_name(output)} (output {output['slot']} of "
                              f"run {step['run_id']})",
                      "alternateName": output["file_name"]}
            if not embedded and output.get("container_path"):
                entity["localPath"] = output["container_path"]
            outputs[(step["run_id"], output["slot"])] = add(
                _hashed(entity, output.get("content_hash")), part=embedded)

    actions = []
    for step in steps:
        method = _method(step)
        app = add({"@id": f"#method-{method}-{_version(step).replace(' ', '-')}",
                   "@type": "SoftwareApplication", "name": method,
                   "softwareVersion": str(step.get("version_label")),
                   "description": f"wfc method {method}, {_version(step)}, git tree "
                                  f"{step.get('tree_hash')}"})
        objects: list[dict] = []
        for item in step["inputs"]:
            source = item["from"]
            ref: dict | None
            if "sample" in source:
                content_hash = next((s["content_hash"] for s in step["key"]["samples"]
                                     if s["input_slot"] == item["slot"]
                                     and s["name"] == source["sample"]), None)
                directory = _is_dir(content_hash)
                ref = add(_hashed({"@id": source["path"] + ("/" if directory else ""),
                                   "@type": "Dataset" if directory else "File",
                                   "name": f"sample {source['sample']}"},
                                  content_hash), part=True)
            else:
                ref = outputs.get((source.get("run_id"), source.get("slot")))
            if ref is not None and ref not in objects:
                objects.append(ref)
        environment = [
            add({"@id": f"#run-{step['run_id']}-env-{name}", "@type": "PropertyValue",
                 "name": name, "value": value})
            for name, value in ((step.get("invocation") or {}).get("env") or {}).items()]
        action: dict = {
            "@id": f"#run-{step['run_id']}", "@type": "CreateAction",
            "name": f"Step {step['number']}: run {step['run_id']}, {method} "
                    f"{_version(step)} on {_sample_text(step.get('sample'))}",
            "description": f"The command that ran is steps/{step['folder']}/command.sh",
            "instrument": app, "object": objects,
            "result": [outputs[(step["run_id"], o["slot"])] for o in step["outputs"]],
            "environment": environment, "actionStatus": {"@id": _COMPLETED},
        }
        if step["env"].get("folder"):
            image_id = "#image-" + step["env"]["folder"].split("/", 1)[1]
            action["containerImage"] = add(_image_entity(
                image_id, step["env"], bool(manifest.get("with_image"))))
        if step.get("started_at"):
            action["startTime"] = step["started_at"]
        if step.get("finished_at"):
            action["endTime"] = step["finished_at"]
        actions.append(add(action))

    requested = _and([str(r) for r in manifest["requested"]])
    root = {
        "@id": "./", "@type": "Dataset",
        "conformsTo": {"@id": PROCESS_RUN_CRATE},
        "name": f"wfc rerun bundle: run{'s' if len(manifest['requested']) > 1 else ''} "
                f"{requested} of project {project}",
        "description": (f"Runs {requested} and every run above them, {len(steps)} "
                        f"steps, exported from the wfc project {project}. README.md "
                        f"says what ran and how to rerun it."),
        "datePublished": manifest["exported_at"],
        "license": LICENSE_TEXT,
        "hasPart": [{"@id": p} for p in parts],
        "mentions": actions,
    }
    graph = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "conformsTo": {"@id": RO_CRATE}, "about": {"@id": "./"}},
        root,
        {"@id": PROCESS_RUN_CRATE, "@type": "CreativeWork",
         "name": "Process Run Crate", "version": "0.6"},
        *entities.values(),
    ]
    return {"@context": list(CRATE_CONTEXT), "@graph": graph}
