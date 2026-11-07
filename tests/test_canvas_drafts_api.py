"""The Canvas API over drafts: one colour per module, the draft routes, and draft refusals.

Every project is built by production ``wfc init`` and registration
(``tmp_project`` + ``register_test_method``); drafts are added through the
draft route itself.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes.readiness import stub_readiness_probes
from tests.fixtures.routes import canvas_client, register_test_method
from wfc import layout

MODULE, METHOD = "mod", "transform"

YAML = """\
inputs:
  data:
    type: .csv
    required: true
outputs:
  output:
    type: .csv
    required: true
params:
  suffix:
    type: str
    required: false
    default: "_t"
executor: python
env: fixture-env
"""


@pytest.fixture
def project(tmp_project):
    """A project with module ``mod`` and its method ``transform`` at v1."""
    register_test_method(tmp_project, module_name=MODULE,
                         method_dir=tmp_project / "methods" / METHOD)
    return tmp_project


def _folder(tmp_path_factory, name: str = METHOD, yaml: str = YAML,
            script: bool = True) -> Path:
    folder = tmp_path_factory.mktemp("author") / name
    shutil.copytree(Path(__file__).parent / "fixtures" / "methods" / "transform", folder)
    (folder / "transform.py").rename(folder / f"{name}.py")
    if not script:
        (folder / f"{name}.py").unlink()
    (folder / "method.yaml").write_text(yaml, encoding="utf-8")
    return folder


def _touch(path: Path, text: str) -> None:
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    after = path.stat()
    if after.st_mtime_ns == before:
        os.utime(path, ns=(after.st_atime_ns, before + 1_000_000_000))


def _doc(folder: Path, method: str = METHOD) -> dict:
    return {"name": "try-it",
            "nodes": [{"id": "sel", "type": "input_selector", "samples": []},
                      {"id": "d", "type": "method", "module": MODULE, "method": method,
                       "version": "draft", "folder": str(folder), "params": {}}],
            "links": [{"source": "sel", "target": "d", "targetHandle": "data"}],
            "samples": []}


@workflow(purpose="One module, one colour: the Builder palette and the Registry "
                  "list give each module the same stored colour (case 21, server half)")
def test_every_route_gives_a_module_the_same_colour(project, monkeypatch):
    client = canvas_client(project, monkeypatch)
    palette = client.get("/api/modules")
    registry = client.get("/api/registry/modules")
    assert palette.status_code == registry.status_code == 200
    by_registry = {m["name"]: m["color"] for m in registry.json()["modules"]}
    assert by_registry[MODULE] and by_registry[MODULE].startswith("#")
    assert {name: mod["color"] for name, mod in palette.json().items()} == by_registry


@workflow(purpose="Adding a folder as a draft through the route: the dry run states "
                  "its kind and writes nothing, the add lists it, and the palette's "
                  "Drafts group shows it with its own contract")
def test_draft_routes_add_list_and_palette(project, monkeypatch, tmp_path_factory):
    client = canvas_client(project, monkeypatch)
    folder = _folder(tmp_path_factory)

    口 = Step(step_num=1, name="Dry run", purpose="The kind and sentence; nothing written")
    dry = client.post("/api/registry/drafts?dryRun=true",
                      json={"module": MODULE, "folder": str(folder)})
    assert dry.status_code == 200, dry.text
    assert (dry.json()["kind"], dry.json()["registersAs"], dry.json()["draft"]) == (
        "version", 2, None)
    assert not layout.draft_dir(project, MODULE, METHOD).exists()

    口 = Step(step_num=2, name="Add and list", purpose="The draft is listed, valid")
    added = client.post("/api/registry/drafts", json={"module": MODULE, "folder": str(folder)})
    assert added.status_code == 200, added.text
    (entry,) = client.get("/api/registry/drafts").json()["drafts"]
    assert (entry["module"], entry["method"], entry["kind"]) == (MODULE, METHOD, "version")
    assert entry["valid"] and entry["check"]["ok"] and entry["draftRun"] is None

    口 = Step(step_num=3, name="The palette's Drafts group",
             purpose="The draft's contract from its own method.yaml")
    draft = client.get("/api/modules").json()[MODULE]["drafts"][METHOD]
    assert draft["kind"] == "version" and draft["params_schema"]["suffix"]["default"] == "_t"

    口 = Step(step_num=4, name="Delete outputs, then Remove",
             purpose="Each confirmed with its dry run's token: Delete outputs keeps "
                     "the draft; Remove deletes it, never the folder")
    base = f"/api/registry/drafts/{MODULE}/{METHOD}"
    for route in (f"{base}/draft-run", base):
        token = client.delete(route, params={"dryRun": "true"}).json()["plan"]["token"]
        assert client.delete(route, params={"planned": token}).status_code == 200
        if route != base:
            assert client.get(route).status_code == 404
    assert client.get("/api/registry/drafts").json()["drafts"] == []
    assert (folder / "method.yaml").is_file()


@workflow(purpose="A draft with an unregistered env and no script: the check route "
                  "lists each reason ✗ and writes nothing; Validate and Run refuse "
                  "naming it (case 5, API half)")
def test_an_invalid_draft_is_checked_and_refused(project, monkeypatch, tmp_path_factory):
    client = canvas_client(project, monkeypatch)
    folder = _folder(tmp_path_factory, "broken", YAML.replace("fixture-env", "no-such-env"),
                     script=False)
    assert client.post("/api/registry/drafts",
                       json={"module": MODULE, "folder": str(folder)}).status_code == 200
    pointer = layout.draft_pointer_path(project, MODULE, "broken").read_bytes()

    口 = Step(step_num=1, name="The Registration check", purpose="Each reason ✗")
    check = client.get(f"/api/registry/drafts/{MODULE}/broken/check").json()
    failures = [line["text"] for line in check["lines"] if not line["ok"]]
    assert check["ok"] is False
    assert any("no-such-env" in text for text in failures), failures
    assert any("No method script found" in text for text in failures), failures
    assert layout.draft_pointer_path(project, MODULE, "broken").read_bytes() == pointer

    口 = Step(step_num=2, name="Validate refuses", purpose="draft_invalid with the reasons")
    verdict = client.post("/api/workflow/validate", json=_doc(folder, "broken")).json()
    (refusal,) = verdict["draftRefusals"]
    assert verdict["valid"] is False and refusal["kind"] == "draft_invalid"
    assert refusal["reasons"] == failures and refusal["message"] in verdict["errors"]

    口 = Step(step_num=3, name="Run refuses", purpose="A 409 draft_refused naming it")
    stub_readiness_probes(monkeypatch, docker="ok")
    run = client.post("/api/workflow/run", json=_doc(folder, "broken"))
    assert run.status_code == 409, run.text
    assert run.json()["detail"]["kind"] == "draft_refused"
    assert run.json()["detail"]["refusals"][0]["kind"] == "draft_invalid"


@workflow(purpose="Case 1, the canvas door: a draft whose method.yaml changed, with "
                  "no Update, is reported as changed (not refused) by the snapshot "
                  "check, shown with the new contract in the palette, validated, "
                  "and run: the run route reads it again and stores it on the "
                  "pointer before it answers; a document that no longer matches "
                  "the contract read now is refused naming the node and the slot")
def test_a_changed_method_yaml_runs_without_update(tmp_project, monkeypatch):
    import json

    import yaml

    from tests.fixtures.conftest import mocked_snakemake
    from tests.fixtures.fakes import fake_engine_process
    from tests.fixtures.fakes.docker import (
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from tests.harness import Scenario, node, selector, wire
    from tests.harness.drivers import build_project
    from tests.harness.project import STUB_DIGEST
    from tests.harness.scenario import SELECTOR_ID
    from wfc.canvas.state import _active_jobs
    from wfc.registration import read_draft

    scenario = Scenario(nodes=[selector(), node("mid", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1"], pipeline_id="changed", name="changed",
                        drafts=("mid",))
    mid = scenario.node_by_id("mid")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")
    client = canvas_client(project.root, monkeypatch)
    document = json.loads(project.pipeline_json.read_text(encoding="utf-8"))
    folder = read_draft(mid.module, mid.method_name, project.root).folder
    path = folder / "method.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))

    口 = Step(step_num=1, name="Change method.yaml; press no Update",
             purpose="A new optional param: the document still matches")
    contract["params"] = {**(contract.get("params") or {}),
                          "extra": {"type": "str", "required": False, "default": "x"}}
    _touch(path, yaml.safe_dump(contract, sort_keys=False))

    口 = Step(step_num=2, name="Snapshot check, palette and Validate",
             purpose="Changed is information; the palette has the new param; "
                     "Validate passes")
    snapshot = client.post("/api/registry/drafts/snapshot-check", json={
        "drafts": [{"module": mid.module, "method": mid.method_name}]}).json()
    assert (snapshot["drafts"][0]["state"], snapshot["drafts"][0]["contractChanged"]) == (
        "current", True)
    palette = client.get("/api/modules").json()[mid.module]["drafts"][mid.method_name]
    assert "extra" in palette["params_schema"]
    verdict = client.post("/api/workflow/validate", json=document).json()
    assert verdict["valid"] and verdict["draftRefusals"] == [], verdict

    口 = Step(step_num=3, name="Run, with no Update",
             purpose="Accepted; the pointer holds the contract read now")
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True):
        resp = client.post("/api/workflow/run", json=document)
        assert resp.status_code == 200, resp.text
        _active_jobs[resp.json()["job_id"]]["thread"].join(timeout=30)
    _active_jobs.clear()
    pointer = read_draft(mid.module, mid.method_name, project.root)
    assert "extra" in pointer.contract["params"]
    assert pointer.snapshot["method.yaml"][1] == path.stat().st_mtime_ns

    口 = Step(step_num=4, name="A document the contract read now no longer matches",
             purpose="The input the link feeds is renamed: a 409 draft_refused "
                     "whose refusal names node mid and the input nothing feeds")
    contract["inputs"] = {"renamed": contract["inputs"].pop("data")}
    _touch(path, yaml.safe_dump(contract, sort_keys=False))
    resp = client.post("/api/workflow/run", json=document)
    assert resp.status_code == 409, resp.text
    refusals = resp.json()["detail"]["refusals"]
    assert {(r["nodeId"], r["kind"]) for r in refusals} == {("mid", "draft_mismatch")}
    assert any(r["message"].startswith("Node 'mid'") and "'renamed'" in r["message"]
               for r in refusals), refusals


@workflow(purpose="Case 5, the canvas half: Remove draft and Delete outputs answer a "
                  "dry run with Execution's plan (the text wfc delete-draft prints) "
                  "and its token, deleting nothing; a delete without the token is "
                  "a 400, one whose plan changed since its dry run a 409, both "
                  "deleting nothing; the plan's own token deletes")
def test_a_draft_delete_confirms_against_its_plan(tmp_project, monkeypatch):
    from tests.fixtures.fakes.docker import (
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from tests.harness.drivers import run_scenario
    from tests.harness.project import STUB_DIGEST
    from wfc.execution.draft import read_draft_run
    from wfc.execution.draft_clearing import plan_draft_delete
    from wfc.registration import read_draft

    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    scenario = _draft_chain()
    mid = scenario.node_by_id("mid")
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    client = canvas_client(tmp_project, monkeypatch)
    base = f"/api/registry/drafts/{mid.module}/{mid.method_name}"

    口 = Step(step_num=1, name="The dry run of Remove draft",
             purpose="The plan's message, Deletes and Keeps lines, hint and "
                     "token, as Execution plans them; nothing deleted")
    dry = client.delete(base, params={"dryRun": "true"})
    assert dry.status_code == 200, dry.text
    plan = plan_draft_delete(mid.module, mid.method_name, root=tmp_project)
    assert dry.json()["message"] == dry.json()["plan"]["message"] == plan.message
    assert dry.json()["plan"]["token"] == plan.token
    assert dry.json()["plan"]["draftRun"] == plan.draft_run is not None
    assert dry.json()["plan"]["deletes"] == list(plan.deletes) != []
    assert dry.json()["plan"]["keeps"] == list(plan.keeps) != []
    assert dry.json()["plan"]["hint"] == plan.hint != ""
    removing = dry.json()["plan"]["token"]

    口 = Step(step_num=2, name="No token is a 400",
             purpose="Nothing deleted")
    assert client.delete(base).status_code == 400
    assert client.delete(f"{base}/draft-run").status_code == 400
    assert read_draft_run(mid.module, mid.method_name, tmp_project) is not None

    口 = Step(step_num=3, name="Delete outputs with its own token",
             purpose="The draft run goes; the draft stays")
    token = client.delete(f"{base}/draft-run",
                          params={"dryRun": "true"}).json()["plan"]["token"]
    deleted = client.delete(f"{base}/draft-run", params={"planned": token})
    assert deleted.status_code == 200, deleted.text
    # Execution's words after the delete, as wfc delete-draft prints them.
    assert deleted.json()["message"] == (f"Deleted the draft run of {mid.module}."
                                         f"{mid.method_name}; the draft is kept.")
    assert read_draft_run(mid.module, mid.method_name, tmp_project) is None

    口 = Step(step_num=4, name="Remove with the stale token is a 409",
             purpose="Its plan named a draft run that is gone; the draft stays")
    stale = client.delete(base, params={"planned": removing})
    assert stale.status_code == 409, stale.text
    assert "Nothing was deleted" in stale.json()["detail"]
    assert layout.draft_dir(tmp_project, mid.module, mid.method_name).is_dir()

    口 = Step(step_num=5, name="Plan again and remove",
             purpose="The fresh plan's token deletes the draft folder")
    source = read_draft(mid.module, mid.method_name, tmp_project).folder
    token = client.delete(base, params={"dryRun": "true"}).json()["plan"]["token"]
    removed = client.delete(base, params={"planned": token})
    assert removed.status_code == 200, removed.text
    assert removed.json()["message"] == (f"Removed the draft of {mid.module}."
                                         f"{mid.method_name}. Your method folder "
                                         f"{source} is untouched.")
    assert not layout.draft_dir(tmp_project, mid.module, mid.method_name).exists()


def _draft_chain():
    from tests.harness import Scenario, node, selector, wire
    from tests.harness.scenario import SELECTOR_ID

    return Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["s1"], pipeline_id="draft-chain", name="draft_chain", drafts=("mid",))


@workflow(purpose="A pipeline that ran a draft, through the API: the draft step's "
                  "run panel, the live status of its draft-mode nodes, the bundle "
                  "export refusing the draft step by name while the recorded "
                  "upstream run plans (case 9, API half), and Delete outputs "
                  "leaving History's cleared line")
def test_a_draft_run_reads_through_history_status_and_export(tmp_project, monkeypatch):
    from tests.fixtures.fakes.docker import (
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from tests.fixtures.fakes.server_state import bind_provider
    from tests.harness.drivers import run_scenario
    from tests.harness.project import STUB_DIGEST
    from wfc.canvas.run_state import aggregate_pipeline_status
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.execution.draft import spell_draft_ref

    scenario = _draft_chain()
    mid = scenario.node_by_id("mid")
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    口 = Step(step_num=1, name="Run up -> mid (draft) -> low",
             purpose="up is recorded; mid and low are draft steps")
    result = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    up_run = result.runs[("up", "s1", "default")].run_id
    client = canvas_client(tmp_project, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    ref = spell_draft_ref(mid.module, mid.method_name, "mid", "s1")

    口 = Step(step_num=2, name="The draft step's run panel",
             purpose="DRAFT, succeeded, its output and its recorded parent")
    panel = client.get("/api/wfc/draft-step", params={"ref": ref})
    assert panel.status_code == 200, panel.text
    body = panel.json()
    assert (body["versionText"], body["step"]["status"]) == ("DRAFT", "success")
    assert [o["slot"] for o in body["outputs"]] == ["table"]
    assert body["outputs"][0]["sizeBytes"]
    assert [(i["source"], i["runId"]) for i in body["inputs"]] == [("run", str(up_run))]

    口 = Step(step_num=3, name="The live status of the draft-mode nodes",
             purpose="Read from draft-run.json: completed, not pending, while the "
                     "thread still reads alive")
    status = aggregate_pipeline_status(
        "draft-chain", {"up": "up", "mid": "mid", "low": "low"}, thread_alive=True,
        error=None, log_dir=None, root=tmp_project)
    assert {n: s["status"] for n, s in status.node_states.items()} == {
        "up": "completed", "mid": "completed", "low": "completed"}
    assert status.overall_status == "completed"

    口 = Step(step_num=4, name="The bundle export refuses the draft step only",
             purpose="A draft string is refused naming the draft step; up's "
                     "recorded run holds no draft step in its lineage (draft mode "
                     "spreads only downstream) and plans with no refusal")
    dry = client.post("/api/wfc/export-runs?dryRun=true", json={"run_ids": [ref]})
    assert dry.status_code == 200, dry.text
    plan = dry.json()["plan"]
    assert plan["ok"] is False
    assert any("'mid'" in str(r) for r in plan["refusals"]), plan["refusals"]
    dry = client.post("/api/wfc/export-runs?dryRun=true", json={"run_ids": [str(up_run)]})
    assert dry.status_code == 200, dry.text
    plan = dry.json()["plan"]
    assert (plan["ok"], plan["refusals"]) == (True, [])

    口 = Step(step_num=5, name="Delete outputs: History's cleared line",
             purpose="The pipeline keeps its row; its draft steps are gone and "
                     "the line says how many and why")
    route = f"/api/registry/drafts/{mid.module}/{mid.method_name}/draft-run"
    token = client.delete(route, params={"dryRun": "true"}).json()["plan"]["token"]
    gone = client.delete(route, params={"planned": token})
    assert gone.status_code == 200, gone.text
    summary = next(s for s in client.get("/api/wfc/pipelines").json()
                   if s["pipelineId"] == "draft-chain")
    assert summary["draftMode"] and summary["draftSteps"] == []
    cleared = summary["cleared"]
    assert cleared["count"] == 2 and "outputs deleted" in cleared["text"]
    assert [(d["draft"], d["reason"]) for d in cleared["drafts"]] == [
        (f"{mid.module}.{mid.method_name}", "outputs_deleted")]


def test_a_two_draft_cleared_reason_is_read_per_draft():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from wfc.canvas.draft_views import pipeline_cleared

    row = SimpleNamespace(
        pipeline_id="p", drafts=["a.one", "b.two"], draft_step_count=5,
        cleared_at=datetime(2026, 11, 4, 14, 31, tzinfo=UTC),
        cleared_reason="a.one: registered as v4; b.two: outputs deleted")
    line = pipeline_cleared(row, remaining=1)
    assert line["count"] == 4 and line["text"].startswith("4 draft steps cleared: ")
    assert [(d["draft"], d["reason"], d["registeredAs"]) for d in line["drafts"]] == [
        ("a.one", "registered", 4), ("b.two", "outputs_deleted", None)]
    one = SimpleNamespace(**{**vars(row), "drafts": ["a.one"],
                             "cleared_reason": "replaced by the draft run of 14:31"})
    assert [d["reason"] for d in pipeline_cleared(one)["drafts"]] == ["replaced"]


def test_a_malformed_draft_string_is_a_400_and_a_gone_step_a_404(project, monkeypatch):
    from tests.fixtures.fakes.server_state import bind_provider
    from wfc.canvas.wfc_provider import WfcProvider

    client = canvas_client(project, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(project)))
    short = client.get("/api/wfc/draft-step", params={"ref": "draft:mod.transform"})
    assert short.status_code == 400 and "draft:<module>.<method>" in short.json()["detail"]
    gone = client.get("/api/wfc/draft-step", params={"ref": "draft:mod.transform/d@s1"})
    assert gone.status_code == 404 and "has no draft" in gone.json()["detail"]
    assert client.get("/api/wfc/draft-steps").json() == {"steps": []}
