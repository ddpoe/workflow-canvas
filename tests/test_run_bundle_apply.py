"""The run bundle's apply: the zip a plan describes, and what it lets a reader check.

``write_bundle`` writes the zip all or nothing. These tests check what the
bundle promises a reader: the command rendered from a step's invocation is
the command dispatch ran, each step's cache key is
recomputed from ``wfc-run.json`` alone, and every embedded sample
and output hashes to its recorded hash. Pipeline documents and
samples come through Storage's entry read, so a pushed and pruned entry is
pulled back into a complete bundle, and an entry gone and never pushed
refuses the export by name.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung, whose launch keeps the argv dispatch
handed Docker (``record.dispatch_cmd``). Push and prune are ``wfc cache
push`` and ``wfc cache prune``'s own bodies.
"""

from __future__ import annotations

import json
import os
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import completed_run
from tests.harness import Behavior, Scenario, node, selector, wire
from tests.harness.scenario import DEFAULT_SAMPLE, SELECTOR_ID
from tests.test_run_bundle_plan import _lineage
from wfc import layout
from wfc.identity import (
    SampleIdentity,
    UpstreamRunIdentity,
    build_cache_key,
    build_input_fingerprint,
    hash_path,
)
from wfc.persistence import Run, get_session
from wfc.run_bundle import ExportRefusedError, plan_export, render_argv, write_bundle
from wfc.storage import KIND_PIPELINE_DOCUMENT, KIND_SAMPLE, cache_prune, cache_push

_WHEN = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _hosts(cmd: list[str]) -> dict[str, str]:
    """Each bind's host side, by container path, from a ``docker run`` argv."""
    hosts: dict[str, str] = {}
    for flag, value in zip(cmd, cmd[1:]):
        if flag == "-v":
            spec = value[: -len(":ro")] if value.endswith(":ro") else value
            host, _, container = spec.rpartition(":")
            hosts[container] = host
    return hosts


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as archive:
        return json.loads(archive.read("wfc-run.json"))


@workflow(purpose="The argv rendered from a bundled step's invocation, with each mount's "
                  "host side supplied, is the argv dispatch handed Docker",
          inputs="One run through production's phases on the stub rung, the launch "
                 "argv the stub kept",
          outputs="render_argv over the plan's invocation equals the launched argv")
def test_the_command_rendered_from_the_invocation_is_the_command_dispatch_ran(
    tmp_project, monkeypatch,
):
    """The invocation renders back to the launched argv."""
    driven = completed_run(tmp_project, monkeypatch=monkeypatch)
    (record,) = driven.observation.runs.values()
    launched = record.dispatch_cmd
    assert launched is not None, "dispatch launched nothing"

    plan = plan_export([driven.run_id])
    invocation = plan.step(driven.run_id).invocation

    assert render_argv(invocation, _hosts(launched)) == launched


@workflow(purpose="A bundled step's command.sh names the step, the run, the method "
                  "version and the time in its comments, and maps each container path "
                  "to the bundle file it comes from",
          inputs="One run through production's phases on the stub rung; its bundle "
                 "written and extracted",
          outputs="The script's comments carry the run's identity; each input's "
                  "container path maps to a member of the zip, each mount to its "
                  "host-side variable, the code mount to the step's code folder")
def test_command_sh_names_the_run_and_maps_each_container_path_to_its_bundle_file(
    tmp_project, monkeypatch, tmp_path,
):
    """The command.sh clause: what the script's comments tell a receiver."""
    口 = Step(step_num=1, name="Run and export",
             purpose="One run on the default sample, bundled and extracted")
    driven = completed_run(tmp_project, monkeypatch=monkeypatch)
    plan = plan_export([driven.run_id])
    assert plan.ok, plan.refusals
    step = plan.step(driven.run_id)
    bundle = tmp_path / "bundle.zip"
    write_bundle(plan, bundle, exported_at=_WHEN)
    with zipfile.ZipFile(bundle) as archive:
        members = set(archive.namelist())
        manifest = json.loads(archive.read("wfc-run.json"))
        (entry,) = manifest["steps"]
        script = archive.read(f"steps/{entry['folder']}/command.sh").decode("utf-8")
    comments = "\n".join(line for line in script.splitlines() if line.startswith("#"))

    口 = Step(step_num=2, name="The comments name the run",
             purpose="Step number, run id, module.method, version label and start time")
    assert "step 1 of 1" in comments
    assert f"run {driven.run_id}" in comments
    assert step.label in comments
    assert step.version_label is not None
    assert f"v{step.version_label}" in comments
    assert step.started_at and step.started_at in comments

    口 = Step(step_num=3, name="Each container path maps to its bundle file",
             purpose="Each input's container path is paired with the zip member it "
                     "reads; each mount names its host-side variable")
    sample_inputs = [i for i in entry["inputs"] if "sample" in i["from"]]
    assert sample_inputs, "the run read a sample"
    for item in sample_inputs:
        bundled = item["from"]["path"]
        assert bundled in members or any(m.startswith(bundled + "/") for m in members), (
            f"{bundled} is not in the bundle")
        assert item["container_path"], item
        assert (f"#   {item['slot']}: {item['container_path']} <- {bundled}"
                in comments.splitlines()), comments
    mounts = step.invocation["mounts"]
    assert mounts
    variables = {"project": "$WORK", "cache": "$DVC_CACHE", "code": "$CODE"}
    for mount in mounts:
        assert any(line.startswith(f"#   {mount['container']} ({mount['role']}, ")
                   and line.endswith(f"<- {variables.get(mount['role'], '$CODE')}")
                   for line in comments.splitlines()), (mount, comments)
    assert any(m["role"] == "code" for m in mounts)
    assert f'CODE="${{CODE:-$BUNDLE/steps/{entry["folder"]}/code}}"' in script
    assert any(m.startswith(f"steps/{entry['folder']}/code/") for m in members)


@workflow(purpose="A bundle of a lineage with a cache hit and a fan-in lets a reader "
                  "recompute every step's cache key from wfc-run.json alone, and every "
                  "embedded sample and output hashes to its recorded hash",
          inputs="A shared upstream and its cache hit, three consumers and a join, "
                 "driven through production; an export with outputs",
          outputs="Recomputed keys equal to the recorded ones; hashes of the extracted "
                  "bytes equal to the recorded ones; a consumer of the hit row reads "
                  "the original step; the same plan and time give the same bytes")
def test_a_bundle_carries_every_key_input_and_bytes_matching_their_hashes(
    tmp_project, monkeypatch, tmp_path,
):
    """Recomputed cache keys and embedded hashes, and the cache-hit edge."""
    口 = Step(step_num=1, name="Drive the lineage",
             purpose="A shared upstream, its cache hit, three consumers of the hit "
                     "and a join over two of them")
    scenario = _lineage()

    def drive(target: str) -> int:
        return completed_run(tmp_project, monkeypatch=monkeypatch,
                             scenario=scenario, target=target).run_id

    for spec in scenario.method_nodes():
        # Each step writes its own bytes, so a member filled from another
        # step's output cannot hash to this step's record.
        spec.behavior = Behavior(outputs={"data": f"written by {spec.id}\n"})
    up = drive("up")
    hit = drive("up")
    a, _b, c, join = drive("a"), drive("b"), drive("c"), drive("join")

    口 = Step(step_num=2, name="Export with outputs",
             purpose="Plan and write the bundle of the join and c")
    plan = plan_export([join, c], with_outputs=True)
    assert plan.ok, plan.refusals
    bundle = tmp_path / "bundle.zip"
    result = write_bundle(plan, bundle, exported_at=_WHEN)
    assert result.steps == 5 and bundle.is_file()
    manifest = _manifest(bundle)

    口 = Step(step_num=3, name="Recompute each key from wfc-run.json alone",
             purpose="The input fingerprint and the key from the bundled values")
    with get_session() as session:
        recorded = {s["run_id"]: session.get(Run, s["run_id"]).cache_key
                    for s in manifest["steps"]}
    for step in manifest["steps"]:
        key = step["key"]
        input_fingerprint = build_input_fingerprint(
            [UpstreamRunIdentity(input_slot=u["input_slot"], source_slot=u["source_slot"],
                                 run_id=u["run_id"], cache_key=u["cache_key"])
             for u in key["upstream"]],
            [SampleIdentity(input_slot=s["input_slot"], content_hash=s["content_hash"])
             for s in key["samples"]],
        )
        assert input_fingerprint == key["input_fingerprint"]
        assert build_cache_key(key["code_fingerprint"], key["params"], input_fingerprint,
                               key["env_fingerprint"], key["method_identity"]) \
            == key["cache_key"] == recorded[step["run_id"]], step["folder"]

    a_step = next(s for s in manifest["steps"] if s["run_id"] == a)
    assert [u["run_id"] for u in a_step["key"]["upstream"]] == [hit], (
        "the key is over the row the consumer read")
    assert [i["from"]["run_id"] for i in a_step["inputs"]] == [up], (
        "the bundle wires the consumer to the original step")

    口 = Step(step_num=4, name="Hash every embedded sample and output",
             purpose="Extract the zip; each sample and output hashes to its record")
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(extracted)
        times = {info.filename: info.date_time for info in archive.infolist()}
    assert set(times.values()) == {(1980, 1, 1, 0, 0, 0)}, (
        "every member carries the fixed time, never the clock's")
    recorded_outputs = [o["content_hash"] for s in manifest["steps"] for o in s["outputs"]]
    assert len(recorded_outputs) == 5 and len(set(recorded_outputs)) == 5, (
        "each step's output has bytes of its own")
    checked = 0
    for step in manifest["steps"]:
        for item in step["inputs"]:
            if "sample" in item["from"]:
                sample = next(s for s in step["key"]["samples"]
                              if s["name"] == item["from"]["sample"])
                assert hash_path(extracted / item["from"]["path"]) == sample["content_hash"]
                checked += 1
        for output in step["outputs"]:
            path = extracted / "outputs" / step["folder"] / output["file_name"]
            assert hash_path(path) == output["content_hash"], path
            checked += 1
        assert (extracted / "steps" / step["folder"] / "command.sh").is_file()
        assert any((extracted / "steps" / step["folder"] / "code").rglob("*"))
    assert checked >= 6
    assert hash_path(extracted / "pipeline.json") == manifest["steps"][0]["pipeline_doc_hash"]

    口 = Step(step_num=5, name="Export again",
             purpose="The same plan at the same export time gives the same bytes")
    again = tmp_path / "again.zip"
    write_bundle(plan, again, exported_at=_WHEN)
    assert again.read_bytes() == bundle.read_bytes()


def _on_sample(method: str, content: str, file_name: str | None = None) -> Scenario:
    """One method reading the default sample, registered with *content*.

    *file_name* names the registration source; ``None`` keeps the
    harness's ``<sample>.csv``.
    """
    return Scenario(nodes=[selector(), node(method, inputs=[wire(SELECTOR_ID)])],
                    samples=[DEFAULT_SAMPLE], sample_content=content,
                    sample_file_names={DEFAULT_SAMPLE: file_name} if file_name else {},
                    pipeline_id=f"{method}-pipeline", name=method)


@workflow(purpose="Two versions of one sample name in one bundle each keep their own "
                  "bytes and file name: a run before the sample was registered again "
                  "with new content under another file name and a run after",
          inputs="Two runs on one sample name, the sample registered again with other "
                 "content from a source of another name between them; one export of "
                 "both",
          outputs="Two sample members, each hashing to the hash its run read and named "
                  "as the file its run read")
def test_two_versions_of_one_sample_name_each_keep_their_own_bytes(
    tmp_project, monkeypatch, tmp_path,
):
    """A sample member is keyed by the hash the run read, not by the name alone."""
    口 = Step(step_num=1, name="Run on two versions of one sample",
             purpose="The harness registers the sample again when its content changes; "
                     "the second registration's source carries another file name")
    early = completed_run(tmp_project, monkeypatch=monkeypatch,
                          scenario=_on_sample("early", "id,value\n1,first\n"),
                          target="early").run_id
    # The second registration reads a source named renamed.csv, so the row
    # today records a file name the early run never read.
    late = completed_run(tmp_project, monkeypatch=monkeypatch,
                         scenario=_on_sample("late", "id,value\n1,second\n",
                                             file_name="renamed.csv"),
                         target="late").run_id

    口 = Step(step_num=2, name="Export both and hash each sample member",
             purpose="Each step's sample input names a member holding the bytes it read")
    plan = plan_export([early, late])
    assert plan.ok, plan.refusals
    bundle = tmp_path / "two.zip"
    write_bundle(plan, bundle, exported_at=_WHEN)
    extracted = tmp_path / "two"
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(extracted)
    read: dict[int, tuple[str, str]] = {}
    for step in _manifest(bundle)["steps"]:
        (item,) = [i for i in step["inputs"] if "sample" in i["from"]]
        (sample,) = step["key"]["samples"]
        assert hash_path(extracted / item["from"]["path"]) == sample["content_hash"], step
        read[step["run_id"]] = (item["from"]["path"], sample["content_hash"])
    assert read[early][1] != read[late][1], "the runs read two versions"
    assert read[early][0] != read[late][0], "each version has its own member"
    assert Path(read[early][0]).name == f"{DEFAULT_SAMPLE}.csv", (
        "the early version keeps the file name its run read")
    assert Path(read[late][0]).name == "renamed.csv", (
        "the late version carries the file name its run read")


@workflow(purpose="A pipeline document and a sample that were pushed and then pruned are "
                  "pulled back through Storage's entry read into a complete bundle; a "
                  "document gone and never pushed refuses the export by name and no "
                  "bundle is written",
          inputs="A completed run on a project with a local DVC remote, wfc cache push "
                 "and prune; a second pipeline's run whose document is never pushed",
          outputs="The plan lists the pulls; the bundle's document and sample hash to "
                  "their records; the second export's refusal names the document")
def test_pruned_pushed_entries_are_pulled_into_the_bundle_and_an_unpushed_gone_one_refuses(
    tmp_project, monkeypatch, tmp_path,
):
    """The export-level entry-read witness."""
    口 = Step(step_num=1, name="Run, push and prune",
             purpose="The run's document and sample reach the remote, then leave the "
                     "local cache")
    run = completed_run(tmp_project, monkeypatch=monkeypatch)
    doc_hash = run.run_row["pipeline_doc_hash"]
    report = cache_push(tmp_project)
    assert not report.failures, report.failures
    assert cache_prune(include_local=True, force=True) == 0
    assert not layout.dvc_cache_entry(tmp_project, doc_hash).exists()

    口 = Step(step_num=2, name="Export the pruned run",
             purpose="The plan reports the pulls; the apply pulls them back")
    plan = plan_export([run.run_id])
    assert plan.ok, plan.refusals
    pulled = {(e.kind, e.content_hash) for e in plan.pulls}
    assert (KIND_PIPELINE_DOCUMENT, doc_hash) in pulled
    assert any(kind == KIND_SAMPLE for kind, _ in pulled)
    bundle = tmp_path / "pruned.zip"
    write_bundle(plan, bundle, exported_at=_WHEN)
    extracted = tmp_path / "pruned"
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(extracted)
    assert hash_path(extracted / "pipeline.json") == doc_hash
    manifest = _manifest(bundle)
    (sample,) = manifest["steps"][0]["key"]["samples"]
    (sample_input,) = [i for i in manifest["steps"][0]["inputs"] if "sample" in i["from"]]
    assert hash_path(extracted / sample_input["from"]["path"]) == sample["content_hash"]

    口 = Step(step_num=3, name="A document gone and never pushed",
             purpose="A second pipeline's document awaits its push; its cache object "
                     "is deleted (declared deviation: no wfc verb drops an unpushed "
                     "entry, which the prune keeps)")
    late = completed_run(tmp_project, monkeypatch=monkeypatch, method="late",
                         pipeline_id="late-pipeline", name="late")
    late_hash = late.run_row["pipeline_doc_hash"]
    obj = layout.dvc_cache_entry(tmp_project, late_hash)
    os.chmod(obj, stat.S_IWRITE | stat.S_IREAD)
    obj.unlink()

    refused = plan_export([late.run_id])
    assert not refused.ok
    reasons = "\n".join(r.reason for r in refused.refusals)
    assert "pipeline document" in reasons and late_hash in reasons
    out = tmp_path / "late.zip"
    with pytest.raises(ExportRefusedError):
        write_bundle(refused, out)
    assert not out.exists()
