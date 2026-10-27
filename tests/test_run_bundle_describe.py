"""The run bundle's descriptions: ``README.md`` for people, the RO-Crate for tools.

Both are written from the same facts as ``wfc-run.json``. These tests check
that every step folder, output entry and README row names a run id that
``wfc-run.json`` holds, and that ``ro-crate-metadata.json``
meets every MUST of the Process Run Crate profile, version 0.6, and of the
RO-Crate 1.3 specification it extends. The README's
"If a step says DIFFERENT" section names the bundle's real paths.

The MUSTs checked, from the profile's own text
(https://w3id.org/ro/wfrun/process/0.6, read 2026-10-20) and RO-Crate 1.3
(https://w3id.org/ro/crate/1.3: "Root Data Entity" and "Data Entities"):

RO-Crate 1.3
  R1. The metadata document holds a descriptor with ``@id``
      ``ro-crate-metadata.json`` and ``@type`` ``CreativeWork``, whose
      ``about`` references the Root Data Entity's ``@id``.
  R2. The Root Data Entity's ``@type`` is ``Dataset`` or contains it.
  R3. The Root Data Entity has every one of ``name``, ``description``,
      ``datePublished`` and ``license``; ``datePublished`` is a single ISO
      8601 string.
  R4. A File data entity's ``@type`` is ``File`` or contains it; its
      relative ``@id`` names a file present at that path in the crate.
  R5. A directory data entity's ``@type`` is ``Dataset`` or contains it;
      its relative ``@id`` resolves to a directory present in the crate.
  R6. Every file and directory data entity is linked from the Root Data
      Entity through ``hasPart``.
  (Each entity has an ``@id`` and an ``@type``, ids are unique, and the
  context names RO-Crate 1.3 and, for ``environment``, ``containerImage``
  and ``md5``, the workflow-run terms.)

Process Run Crate 0.6
  P1. The Root Data Entity's ``conformsTo`` references a ``CreativeWork``
      whose ``@id`` is the profile's versioned permalink.
  P2. Each application has an ``@id`` and an ``@type`` (the profile's
      SHOULD: ``SoftwareApplication``, ``SoftwareSourceCode`` or
      ``ComputationalWorkflow``; this crate uses ``SoftwareApplication``).
  P3. Each execution is an action with an ``@id`` and an ``@type``
      (``CreateAction`` here) whose ``instrument`` references its
      application.

Beyond the MUSTs, each step's action is ``#run-<id>``
and names its image (``#image-<env>``), its environment, and its inputs and
outputs by hash. A file's hash is the workflow-run ``md5`` term; a
directory's wfc hash (the MD5 of DVC's ``.dir`` manifest) is its
``identifier``.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung, whose env image was never pushed.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.routes import completed_run
from tests.test_run_bundle_plan import _lineage
from wfc.identity import DIR_HASH_SUFFIX, hash_path
from wfc.run_bundle import plan_export, write_bundle
from wfc.run_bundle.describe import render_crate, render_readme

_WHEN = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

#: The profile and specification the crate claims, as their permalinks.
PROCESS_RUN_CRATE = "https://w3id.org/ro/wfrun/process/0.6"
RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.3/context"
WORKFLOW_RUN_CONTEXT = "https://w3id.org/ro/terms/workflow-run/context"
APPLICATION_TYPES = {"SoftwareApplication", "SoftwareSourceCode", "ComputationalWorkflow"}


def _drive_lineage(tmp_project, monkeypatch) -> dict[str, int]:
    """A shared upstream, its cache hit, three consumers of the hit and a join."""
    scenario = _lineage()

    def drive(target: str) -> int:
        return completed_run(tmp_project, monkeypatch=monkeypatch,
                             scenario=scenario, target=target).run_id

    runs = {"up": drive("up"), "hit": drive("up")}
    for target in ("a", "b", "c", "join"):
        runs[target] = drive(target)
    return runs


def _export(runs: dict[str, int], out: Path, *, with_outputs: bool) -> Path:
    """Plan and write the bundle of the join and c; extract it beside the zip."""
    plan = plan_export([runs["join"], runs["c"]], with_outputs=with_outputs)
    assert plan.ok, plan.refusals
    write_bundle(plan, out, exported_at=_WHEN)
    extracted = out.with_suffix("")
    with zipfile.ZipFile(out) as archive:
        archive.extractall(extracted)
    return extracted


def _types(entity: dict) -> set[str]:
    kind = entity["@type"]
    return {kind} if isinstance(kind, str) else set(kind)


def _ids(value) -> list[str]:
    """The ``@id`` of each reference in a property's value (one or a list)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [item["@id"] for item in items]


def _table_rows(readme: str) -> list[list[str]]:
    """Each body row of the README's step table, as its cells."""
    rows = []
    for line in readme.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        if cells and cells[1].isdigit():
            rows.append(cells)
    return rows


@workflow(purpose="Every step folder, outputs.json entry and README row of a bundle "
                  "names a run id that wfc-run.json holds; the README names each "
                  "cache-hit substitution, the parameters, each local-only image and "
                  "how to rerun",
          inputs="A shared upstream and its cache hit, three consumers of the hit and "
                 "a join, driven through production; an export of the join and c",
          outputs="Folders, output entries and README rows each naming one of the "
                  "bundle's runs, one row per step; the README's substitution line")
def test_every_folder_output_entry_and_readme_row_names_a_bundled_run(
    tmp_project, monkeypatch, tmp_path,
):
    """Traceability, and what the README holds."""
    口 = Step(step_num=1, name="Drive and export the lineage",
             purpose="Plan and write the bundle of the join and c")
    runs = _drive_lineage(tmp_project, monkeypatch)
    bundle = _export(runs, tmp_path / "bundle.zip", with_outputs=False)
    manifest = json.loads((bundle / "wfc-run.json").read_text(encoding="utf-8"))
    run_ids = {s["run_id"] for s in manifest["steps"]}
    assert run_ids == {runs[t] for t in ("up", "a", "b", "c", "join")}

    口 = Step(step_num=2, name="Read the step folders and output entries",
             purpose="Each folder and each outputs.json entry names its step's run")
    folders = sorted(p.name for p in (bundle / "steps").iterdir())
    assert len(folders) == len(manifest["steps"])
    for folder in folders:
        match = re.match(r"^\d+-run-(\d+)-", folder)
        assert match, folder
        folder_run = int(match.group(1))
        assert folder_run in run_ids, folder
        entries = json.loads((bundle / "steps" / folder / "outputs.json")
                             .read_text(encoding="utf-8"))
        assert entries, folder
        assert {e["run_id"] for e in entries} == {folder_run}, folder

    口 = Step(step_num=3, name="Read the README's rows",
             purpose="One row per step, each naming a bundled run, in step order")
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    rows = _table_rows(readme)
    assert [int(r[1]) for r in rows] == [s["run_id"] for s in manifest["steps"]]
    for row, step in zip(rows, manifest["steps"]):
        assert step["folder"] in row[0], "the step cell links its folder"
        assert step["module"] + "." + step["method"] in row[2]
        for output in step["outputs"]:
            assert output["file_name"] in row[6] and output["content_hash"] in row[6]
        assert "local only" in row[4], "the stub env's image was never pushed"
    join_row = rows[-1]
    assert f"run {runs['a']}" in join_row[5] and f"run {runs['b']}" in join_row[5], (
        "the join's inputs name the steps they came from")

    口 = Step(step_num=4, name="Read the README's other lines",
             purpose="The substitution, the provenance line, the parameters and the "
                     "rerun instruction")
    assert (f"Run {runs['hit']} reused run {runs['up']} (same cache key), so run "
            f"{runs['up']} is the step in this bundle.") in readme
    assert f"project `{Path(tmp_project).name}`" in readme
    for commit in {s["git_commit"] for s in manifest["steps"] if s["git_commit"]}:
        assert commit[:12] in readme
    assert '{"k": 3}' in readme and '{"k": 4}' in readme
    assert "`python rerun.py` (Python 3.11 or newer, and Docker)" in readme


#: The README section a receiver reads when the rerun reports a difference.
DIFFERENT_HEADING = "## If a step says DIFFERENT"
#: Step 2 of that section in a bundle that holds no outputs, verbatim.
HASHES_ONLY = ("This bundle holds only the hashes of the original outputs. Ask the sender "
               "for a bundle exported with --with-outputs.")


def _section(readme: str, heading: str) -> str:
    """The README's ``## `` section under *heading*, up to the next one."""
    lines = readme.splitlines()
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    return "\n".join(lines[start:end])


@workflow(purpose="The README's 'If a step says DIFFERENT' section, after 'Rerun "
                  "and check', says what same and DIFFERENT mean and the common causes, and "
                  "names the bundle's real paths: the originals in outputs/<step folder>/ "
                  "when the bundle holds them, else the hashes-only sentence and no "
                  "outputs/, both runs' logs, and where the rerun folder, its work/ and "
                  "logs/ are",
          inputs="One completed run, exported once with hashes only and once with the "
                 "outputs embedded",
          outputs="Each README's section; the bundle paths it names exist in that bundle")
def test_the_readme_says_what_different_means_with_the_bundles_real_paths(
    tmp_project, monkeypatch, tmp_path,
):
    """What DIFFERENT means, for a receiver who may not have wfc."""
    口 = Step(step_num=1, name="Export one run twice",
             purpose="A bundle with hashes only, and one with the outputs embedded")
    run = completed_run(tmp_project, monkeypatch=monkeypatch)
    bundles = {}
    for with_outputs in (False, True):
        plan = plan_export([run.run_id], with_outputs=with_outputs)
        assert plan.ok, plan.refusals
        out = tmp_path / ("full.zip" if with_outputs else "plain.zip")
        write_bundle(plan, out, exported_at=_WHEN)
        with zipfile.ZipFile(out) as archive:
            archive.extractall(out.with_suffix(""))
        bundles[with_outputs] = out.with_suffix("")

    for with_outputs, bundle in bundles.items():
        口 = Step(step_num=1.1, name="Read the section",
                 purpose="Its place, its meaning and causes, the logs of both runs, "
                         "and where the rerun folder and its work/ are")
        readme = (bundle / "README.md").read_text(encoding="utf-8")
        manifest = json.loads((bundle / "wfc-run.json").read_text(encoding="utf-8"))
        headings = [line for line in readme.splitlines() if line.startswith("## ")]
        assert headings.index(DIFFERENT_HEADING) == headings.index("## Rerun and check") + 1
        assert "env fingerprint" in _section(readme, "## Rerun and check")
        section = _section(readme, DIFFERENT_HEADING)
        for phrase in ("`same` means the rerun wrote exactly the file that was recorded",
                       "DIFFERENT means the bytes differ", "creation date",
                       "floating-point arithmetic", "a parallel job finished",
                       "randomness without a fixed seed",
                       "Start at the first difference `rerun.py` names",
                       "`steps/<step folder>/logs/`", "`logs/<step folder>/`",
                       "`rerun-<UTC time>/` next to `rerun.py`", "`--work DIR`",
                       "`rerun-<UTC time>/work/`", "`DIR/work/`"):
            assert phrase in section, phrase
        assert "the image" not in section, "image means the container image in a bundle"
        for step in manifest["steps"]:
            assert (bundle / "steps" / step["folder"] / "logs").is_dir()

        口 = Step(step_num=1.2, name="Read where the originals are",
                 purpose="outputs/<step folder>/ when the bundle holds the outputs; "
                         "the hashes-only sentence otherwise")
        if with_outputs:
            assert HASHES_ONLY not in section
            for phrase in ("`outputs/<step folder>/`", "open both figures",
                           "`rerun.py` names both files for the first difference"):
                assert phrase in section, phrase
            for step in manifest["steps"]:
                for output in step["outputs"]:
                    assert (bundle / "outputs" / step["folder"] / output["file_name"]).exists()
        else:
            assert HASHES_ONLY in section
            assert "outputs/" not in section
            assert not (bundle / "outputs").exists()


#: The README section that names the images only the exporting machine holds.
LOCAL_IMAGES_HEADING = "## Images only the exporting machine holds"


@workflow(purpose="A plain bundle calls the stub env's never-pushed image local only; the "
                  "same wfc-run.json with with_image set names the image's bundle member "
                  "in the README and the crate and calls it neither local only nor lost "
                  "once the machine deletes it",
          inputs="One completed run on the stub env's never-pushed image, exported plain",
          outputs="The README row, local-images section and crate image description of "
                  "each form")
def test_a_never_pushed_image_the_bundle_holds_is_not_called_local_only(
    tmp_project, monkeypatch, tmp_path,
):
    """Under --with-image the README and crate say the image is in the bundle.

    A plain bundle of the stub env's never-pushed image marks it local only
    and says the step can no longer be rerun once the machine deletes it.
    The same wfc-run.json with ``with_image`` set (the value a --with-image
    apply writes; rendering it skips ``docker save``) names the image's
    bundle member instead, and says neither.
    """
    run = completed_run(tmp_project, monkeypatch=monkeypatch)
    plan = plan_export([run.run_id])
    assert plan.ok, plan.refusals
    out = tmp_path / "plain.zip"
    write_bundle(plan, out, exported_at=_WHEN)
    with zipfile.ZipFile(out) as archive:
        plain = json.loads(archive.read("wfc-run.json"))
    (step,) = plain["steps"]
    assert step["env"]["name"] and not step["env"]["pullable_ref"]
    project = Path(tmp_project).name
    member = "images/" + step["env"]["folder"].split("/", 1)[1] + ".tar"

    readme = render_readme(plain, project=project)
    contents = _section(readme, "## What is in this bundle")
    assert "`samples/<name>@<hash12>/`" in contents, (
        "the contents name the per-version sample folders")
    (row,) = _table_rows(readme)
    assert "(local only: never pushed)" in row[4]
    assert LOCAL_IMAGES_HEADING in readme
    assert "can no longer be rerun" in readme
    image = next(e for e in render_crate(plain, project=project)["@graph"]
                 if "ContainerImage" in _types(e))
    assert "never pushed to a registry" in image["description"]
    assert "saved in this bundle" not in image["description"]

    held = plain | {"with_image": True}
    readme = render_readme(held, project=project)
    (row,) = _table_rows(readme)
    assert "local only" not in readme
    assert LOCAL_IMAGES_HEADING not in readme
    assert "can no longer be rerun" not in readme
    assert f"saved in this bundle as `{member}`" in row[4]
    image = next(e for e in render_crate(held, project=project)["@graph"]
                 if "ContainerImage" in _types(e))
    assert f"saved in this bundle as {member}" in image["description"]


@workflow(purpose="ro-crate-metadata.json meets every Process Run Crate 0.6 and RO-Crate "
                  "1.3 MUST listed in this module, and each step's action is #run-<id> "
                  "naming its image, its environment, and its inputs and outputs by "
                  "the hashes wfc-run.json records",
          inputs="The lineage of the traceability test, exported once with hashes "
                 "only and once with the outputs embedded",
          outputs="Both crates conform; every relative id is a file or directory in "
                  "the bundle whose wfc hash is the one the crate states")
def test_the_crate_meets_the_process_run_crate_musts(tmp_project, monkeypatch, tmp_path):
    """Crate conformance, checked against the MUST list above."""
    口 = Step(step_num=1, name="Drive and export the lineage twice",
             purpose="A bundle with hashes only, and one with the outputs embedded")
    runs = _drive_lineage(tmp_project, monkeypatch)
    bundles = [_export(runs, tmp_path / "plain.zip", with_outputs=False),
               _export(runs, tmp_path / "full.zip", with_outputs=True)]

    口 = Step(step_num=2, name="Check each bundle's crate",
             purpose="The same checks over both bundles")
    for bundle in bundles:
        口 = Step(step_num=2.1, name="Check the RO-Crate 1.3 MUSTs",
                 purpose="R1-R6 over the crate's graph and the extracted files")
        crate = json.loads((bundle / "ro-crate-metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((bundle / "wfc-run.json").read_text(encoding="utf-8"))
        assert RO_CRATE_CONTEXT in crate["@context"]
        assert WORKFLOW_RUN_CONTEXT in crate["@context"]
        graph = crate["@graph"]
        assert all("@id" in e and "@type" in e for e in graph)
        entities = {e["@id"]: e for e in graph}
        assert len(entities) == len(graph), "ids are unique"

        descriptor = entities["ro-crate-metadata.json"]  # R1
        assert "CreativeWork" in _types(descriptor)
        root = entities[descriptor["about"]["@id"]]
        assert "Dataset" in _types(root)  # R2
        for prop in ("name", "description", "datePublished", "license"):  # R3
            assert root.get(prop), prop
        assert isinstance(root["datePublished"], str)
        datetime.fromisoformat(root["datePublished"])

        parts = set(_ids(root.get("hasPart")))
        data_entities = [e for e in graph
                         if not e["@id"].startswith(("#", "http", "./"))
                         and e["@id"] != "ro-crate-metadata.json"]
        assert data_entities
        for entity in data_entities:
            path = bundle / entity["@id"]
            if "File" in _types(entity):  # R4
                assert path.is_file(), entity["@id"]
            else:  # R5
                assert "Dataset" in _types(entity) and entity["@id"].endswith("/")
                assert path.is_dir(), entity["@id"]
            assert entity["@id"] in parts, f"R6: {entity['@id']} not in hasPart"
            stated = entity.get("md5") or entity.get("identifier")
            if stated:
                assert hash_path(path) == stated, entity["@id"]

        口 = Step(step_num=2.2, name="Check the Process Run Crate 0.6 MUSTs",
                 purpose="P1-P3: the profile claim, the applications, the actions")
        assert PROCESS_RUN_CRATE in _ids(root["conformsTo"])  # P1
        assert "CreativeWork" in _types(entities[PROCESS_RUN_CRATE])
        actions = [e for e in graph if "CreateAction" in _types(e)]
        assert {a["@id"] for a in actions} == {f"#run-{s['run_id']}"
                                               for s in manifest["steps"]}
        assert set(_ids(root.get("mentions"))) >= {a["@id"] for a in actions}
        for action in actions:  # P3
            (instrument,) = _ids(action["instrument"])
            assert _types(entities[instrument]) & APPLICATION_TYPES  # P2

        口 = Step(step_num=2.3, name="Check each step's action against wfc-run.json",
                 purpose="Image, environment, and inputs and outputs by hash")
        for step in manifest["steps"]:
            action = entities[f"#run-{step['run_id']}"]
            image = entities[action["containerImage"]["@id"]]
            assert "ContainerImage" in _types(image)
            assert image["@id"] == "#image-" + step["env"]["folder"].split("/", 1)[1]
            environment = {entities[i]["name"]: entities[i]["value"]
                           for i in _ids(action.get("environment"))}
            assert environment == step["invocation"]["env"]

            def hashes(ids: list[str], entities: dict = entities) -> list[str]:
                return sorted(entities[i].get("md5") or entities[i]["identifier"]
                              for i in ids)

            assert hashes(_ids(action.get("result"))) == sorted(
                o["content_hash"] for o in step["outputs"])
            expected_inputs = []
            for item in step["inputs"]:
                if "sample" in item["from"]:
                    expected_inputs += [s["content_hash"] for s in step["key"]["samples"]
                                        if s["input_slot"] == item["slot"]]
                else:
                    source = next(s for s in manifest["steps"]
                                  if s["run_id"] == item["from"]["run_id"])
                    expected_inputs += [o["content_hash"] for o in source["outputs"]
                                        if o["slot"] == item["from"]["slot"]]
            assert set(hashes(_ids(action.get("object")))) == set(expected_inputs)
            assert len(_ids(action.get("object"))) >= len(step["inputs"]) > 0
            for entity_id in _ids(action.get("result")):
                stated = entities[entity_id].get("md5") or entities[entity_id]["identifier"]
                assert ("md5" in entities[entity_id]) != stated.endswith(DIR_HASH_SUFFIX)
