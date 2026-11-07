"""``version: "draft"`` through Contracts, Graph, validation, prepare, the preview and lineage.

A pipeline-document node pinned to ``draft`` (with its draft's ``folder``)
runs its method's draft: enrichment takes the draft's contract from the
contract map's ``versions["draft"]``, validation checks the draft's own
slots and params, preparation pins nothing else for it, and a draft whose
``method.yaml`` changed is read again, never refused for it. Graph
owns the draft-mode closure (a draft and every step downstream of it) and
which draft owns each such step (d-2). The Runs Preview reports every
draft-mode step as new and never looks it up in the cache.

Pure tests feed plain dicts; the project tests add drafts through
Registration's own ``add_draft`` and run pipelines through the scenario
harness.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import canvas_client, register_test_method
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.drivers import run_scenario
from tests.harness.project import method_source_dir
from tests.harness.scenario import SELECTOR_ID

MODULE, METHOD = "mod", "transform"

#: The draft's method.yaml: v1's contract plus a ``scale`` parameter and a
#: second output, so a check that reads the newest version instead of the
#: draft shows.
DRAFT_YAML = """\
inputs:
  data:
    type: .csv
    required: true
outputs:
  output:
    type: .csv
    required: true
  summary:
    type: .json
    required: true
params:
  suffix:
    type: str
    required: false
    default: "_t"
  scale:
    type: int
    required: false
    default: 1
executor: python
env: fixture-env
"""


# --- pure: Contracts and Graph --------------------------------------------------


def _slots(output_ext: str = ".csv", **params) -> dict:
    return {"input_slots": {"data": {"type": ".csv", "required": True}},
            "output_slots": {"output": {"type": output_ext}},
            "params": params or None, "env": "e1", "label": None}


def _contract_map(*, draft_only: bool = False) -> dict:
    draft = {**_slots(".json"), "env": "e-draft", "folder": "/work/transform"}
    if draft_only:
        return {"mod.transform": {**draft, "version": None, "draft_only": True,
                                  "versions": {"draft": draft}}}
    v1 = {**_slots(), "label": 1}
    return {"mod.transform": {**v1, "version": "id-1",
                              "versions": {"id-1": v1, "draft": draft}}}


def _method(node_id: str, **extra) -> dict:
    return {"id": node_id, "type": "method", "module": MODULE, "method": METHOD,
            "params": {}, **extra}


def test_enrichment_runs_the_draft_and_keeps_its_folder():
    from wfc.contracts import enrich_pipeline, resolve_versions

    doc = {"nodes": [_method("d", version="draft", folder="/work/transform")],
           "links": [], "samples": []}
    (enriched,) = enrich_pipeline(doc, _contract_map())["nodes"]
    assert enriched["version"] == "draft"
    assert enriched["folder"] == "/work/transform"
    assert enriched["env"] == "e-draft"
    assert enriched["slot_outputs"] == {"output": "output.json"}

    # A node that names no folder runs the method's draft from its own folder.
    (filled,) = enrich_pipeline({**doc, "nodes": [_method("d", version="draft")]},
                                _contract_map())["nodes"]
    assert filled["folder"] == "/work/transform"
    # Nothing else is pinned for a draft: the enriched form keeps the pin.
    (kept,) = resolve_versions({"nodes": [enriched]}, _contract_map())["nodes"]
    assert kept["version"] == "draft"


@pytest.mark.parametrize("contract_map,node,says", [
    pytest.param(_contract_map(draft_only=True), _method("n"),
                 ("DRAFT method", "mod.transform", "Pick DRAFT", "register"),
                 id="unpinned node on a DRAFT method"),
    pytest.param({"mod.transform": {**_slots(), "version": "id-1",
                                    "versions": {"id-1": _slots()}}},
                 _method("n", version="draft"),
                 ("mod.transform has no draft",),
                 id="draft pin on a method with no draft"),
    pytest.param(_contract_map(), _method("n", version="draft", folder="/elsewhere/transform"),
                 ("/elsewhere/transform", "/work/transform"),
                 id="the node's folder is not the draft's"),
])
def test_enrichment_refuses_a_node_it_cannot_run_as_a_draft(contract_map, node, says):
    from wfc.contracts import enrich_pipeline

    with pytest.raises(ValueError) as exc:
        enrich_pipeline({"nodes": [node], "links": [], "samples": []}, contract_map)
    for text in says:
        assert text in str(exc.value)
    assert "'n'" in str(exc.value)


def test_structure_validation_reads_the_drafts_slots_and_refuses_an_unpinned_draft_method():
    from wfc.graph import validate_structure

    draft_yaml_slots = {**_contract_map()}
    draft_yaml_slots["mod.transform"]["versions"]["draft"]["input_slots"] = {
        "data": {"type": ".csv", "required": True},
        "extra": {"type": ".csv", "required": True}}
    doc = {"nodes": [{"id": "sel", "type": "input_selector", "samples": ["s1"]},
                     _method("d", version="draft", folder="/work/transform"),
                     _method("v", version="id-1")],
           "links": [{"source": "sel", "target": "d", "targetHandle": "data"},
                     {"source": "sel", "target": "v", "targetHandle": "data"}]}
    result = validate_structure(doc, draft_yaml_slots)
    assert not result["valid"]
    # The draft's slots, not v1's: only node d is refused, for its 'extra'.
    unwired = [e for e in result["errors"] if "nothing is connected to" in e]
    assert len(unwired) == 1, result
    assert unwired[0].startswith("Node 'd': mod.transform's draft declares input 'extra'")

    unpinned = {"nodes": [doc["nodes"][0], _method("n")], "links": [doc["links"][0]
                                                                  | {"target": "n"}]}
    refused = validate_structure(unpinned, _contract_map(draft_only=True))
    assert not refused["valid"]
    assert any("DRAFT method" in e and "'n'" in e for e in refused["errors"])


def test_a_draft_nodes_folder_is_a_known_key():
    from wfc.graph import document_key_refusals

    assert document_key_refusals(
        {"nodes": [_method("d", version="draft", folder="/work/transform")]}) == []


def _chain_doc(*method_ids: str, drafts: tuple[str, ...] = (),
               extra_links: tuple[tuple[str, str], ...] = ()) -> dict:
    """selector -> each method in turn, plus extra links; drafts pinned to ``draft``."""
    nodes = [{"id": "sel", "type": "input_selector", "samples": ["s1"]}]
    nodes += [_method(m, version="draft" if m in drafts else "id-1") for m in method_ids]
    chain = ("sel", *method_ids)
    links = [{"source": a, "target": b} for a, b in zip(chain, chain[1:])]
    links += [{"source": a, "target": b} for a, b in extra_links]
    return {"nodes": nodes, "links": links}


def test_graph_names_each_draft_mode_step_and_the_draft_that_owns_it():
    from wfc.graph import draft_owners

    # up -> d1 -> mid -> d2 -> low: d1 owns mid, d2 owns itself and low;
    # up is upstream of every draft and is not in draft mode.
    doc = _chain_doc("up", "d1", "mid", "d2", "low", drafts=("d1", "d2"))
    assert draft_owners(doc) == {"d1": "d1", "mid": "d1", "d2": "d2", "low": "d2"}

    # Two drafts side by side joined below: the join belongs to the first
    # draft in Graph's order (ties broken by document order), d-2.
    join = {"nodes": [{"id": "sel", "type": "input_selector", "samples": ["s1"]},
                      _method("b", version="draft"), _method("a", version="draft"),
                      _method("j", version="id-1"), _method("after", version="id-1")],
            "links": [{"source": "sel", "target": "b"}, {"source": "sel", "target": "a"},
                      {"source": "a", "target": "j"}, {"source": "b", "target": "j"},
                      {"source": "j", "target": "after"}]}
    assert draft_owners(join) == {"b": "b", "a": "a", "j": "b", "after": "b"}

    # No draft: no draft-mode step.
    assert draft_owners(_chain_doc("up", "low")) == {}


def test_lineage_synthesis_keeps_a_draft_steps_folder():
    from types import SimpleNamespace

    from wfc.lineage.synthesis import _mint_nodes

    record = SimpleNamespace(id="draft:1", method=METHOD, module=MODULE, inputs={},
                             nid="", versionIdentity="draft", folder="/work/transform")
    _ids, (minted,) = _mint_nodes([record])
    assert (minted["version"], minted["folder"]) == ("draft", "/work/transform")

    recorded = SimpleNamespace(id="3", method=METHOD, module=MODULE, inputs={},
                               nid="", versionIdentity="id-1")
    _ids, (plain,) = _mint_nodes([recorded])
    assert "folder" not in plain


# --- project: validation and prepare (case 4, backend half) ---------------------


@pytest.fixture
def project(tmp_project):
    """A project with module ``mod`` and its method ``transform`` at v1."""
    register_test_method(tmp_project, module_name=MODULE,
                         method_dir=tmp_project / "methods" / METHOD)
    return tmp_project


@pytest.fixture
def draft_folder(tmp_path_factory) -> Path:
    """The author's copy of ``transform`` outside the project, with DRAFT_YAML."""
    folder = tmp_path_factory.mktemp("author") / METHOD
    shutil.copytree(Path(__file__).parent / "fixtures" / "methods" / METHOD, folder)
    (folder / "method.yaml").write_text(DRAFT_YAML, encoding="utf-8")
    return folder


def _touch(path: Path, text: str) -> None:
    """Rewrite *path* so its size or mtime differs from before."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    after = path.stat()
    if after.st_mtime_ns == before:
        os.utime(path, ns=(after.st_atime_ns, before + 1_000_000_000))


def _draft_doc(folder: Path, **params) -> dict:
    return {"nodes": [{"id": "sel", "type": "input_selector", "samples": []},
                      _method("d", version="draft", folder=str(folder), params=params)],
            "links": [{"source": "sel", "target": "d", "targetHandle": "data"}],
            "samples": []}


@workflow(purpose="A draft whose method.yaml changed is never refused for it: no "
                  "refusal names Update, the run's re-read stores the new "
                  "contract and preparation runs with it, and a change to the "
                  "script alone needs nothing (case 4, backend half)")
def test_a_changed_method_yaml_is_read_again_without_update(project, draft_folder):
    from wfc.execution.prepare import prepare_document
    from wfc.execution.validation import validation_refusals
    from wfc.registration import add_draft, draft_run_refusal, reread_draft

    口 = Step(step_num=1, name="Add the draft; its own params are checked",
             purpose="The draft declares 'scale', which v1 does not: a draft node "
                     "may set it, a v1 node may not")
    add_draft(MODULE, draft_folder)
    assert validation_refusals(_draft_doc(draft_folder, scale=2)) == []
    v1_doc = _draft_doc(draft_folder, scale=2)
    v1_doc["nodes"][1] = _method("d", params={"scale": 2})
    assert any("scale" in r for r in validation_refusals(v1_doc))
    assert any("scale" in r for r in validation_refusals(_draft_doc(draft_folder, scale="x")))

    口 = Step(step_num=2, name="Prepare pins nothing else for the draft",
             purpose="The prepared node runs the draft from its folder with the "
                     "draft's slots and declared defaults")
    prepared = prepare_document(_draft_doc(draft_folder))
    draft_node = prepared["nodes"][1]
    assert draft_node["version"] == "draft"
    assert Path(draft_node["folder"]) == draft_folder.resolve()
    assert set(draft_node["slot_outputs"]) == {"output", "summary"}
    assert draft_node["params"] == {"suffix": "_t", "scale": 1}

    口 = Step(step_num=3, name="Change method.yaml: nothing refuses it or names Update",
             purpose="The run re-reads the draft instead of asking for Update")
    _touch(draft_folder / "method.yaml", DRAFT_YAML.replace("default: 1", "default: 2"))
    assert draft_run_refusal(MODULE, METHOD, str(draft_folder)) is None
    assert not any("Update" in r for r in validation_refusals(_draft_doc(draft_folder)))

    口 = Step(step_num=4, name="The run's re-read, then change only the script",
             purpose="The writing re-read stores the new contract and preparation "
                     "takes its default; a script edit needs nothing")
    assert reread_draft(MODULE, METHOD).check.ok
    assert prepare_document(_draft_doc(draft_folder))["nodes"][1]["params"]["scale"] == 2
    _touch(draft_folder / "transform.py",
           (draft_folder / "transform.py").read_text(encoding="utf-8") + "\n# edited\n")
    assert validation_refusals(_draft_doc(draft_folder)) == []


def test_a_draft_that_cannot_run_is_refused_naming_why(project, draft_folder, tmp_path):
    from wfc.execution.validation import validation_refusals
    from wfc.registration import add_draft

    # No draft yet: the pin names nothing to run.
    (no_draft,) = validation_refusals(_draft_doc(draft_folder))
    assert "mod.transform has no draft" in no_draft

    add_draft(MODULE, draft_folder)
    # The node's folder is not the draft's.
    other = tmp_path / METHOD
    (wrong,) = validation_refusals(_draft_doc(other))
    assert str(other) in wrong and str(draft_folder.resolve()) in wrong

    # The draft's folder is gone.
    shutil.rmtree(draft_folder)
    (gone,) = validation_refusals(_draft_doc(draft_folder))
    assert "gone" in gone and str(draft_folder.resolve()) in gone


def test_an_unpinned_node_on_a_draft_method_is_refused(project, tmp_path_factory):
    from wfc.execution.validation import validation_refusals
    from wfc.registration import add_draft

    fresh = tmp_path_factory.mktemp("author") / "fresh"
    shutil.copytree(Path(__file__).parent / "fixtures" / "methods" / METHOD, fresh)
    (fresh / "transform.py").rename(fresh / "fresh.py")
    add_draft(MODULE, fresh)
    doc = {"nodes": [{"id": "sel", "type": "input_selector", "samples": []},
                     {"id": "n", "type": "method", "module": MODULE, "method": "fresh",
                      "params": {}}],
           "links": [{"source": "sel", "target": "n", "targetHandle": "data"}]}
    (refusal,) = validation_refusals(doc)
    assert "mod.fresh" in refusal and "DRAFT" in refusal and "register" in refusal


# --- project: the Runs Preview (case 24) ---------------------------------------


@workflow(purpose="The Runs Preview over a pipeline whose draft has an identical "
                  "earlier run: the draft and every step below it are new and "
                  "never looked up in the cache; the step above it is classified "
                  "as without the draft (case 24)")
def test_runs_preview_counts_draft_mode_steps_new_and_never_looks_them_up(
        tmp_project, monkeypatch):
    from wfc.execution.cache_status import cache_status
    from wfc.registration import add_draft

    口 = Step(step_num=1, name="Run up -> mid -> low once",
             purpose="Every step has a completed run, so a preview of the same "
                     "document is all cache hits")
    scenario = Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["s1"], pipeline_id="dp-chain", name="dp_chain",
    )
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    document = json.loads(Path(project.pipeline_json).read_text(encoding="utf-8"))

    口 = Step(step_num=2, name="Draft mid from its own unchanged source",
             purpose="The draft's code and params are the ones mid ran: the key "
                     "it would have is the cached run's")
    mid_spec = next(s for s in scenario.method_nodes() if s.id == "mid")
    pointer = add_draft(mid_spec.module, method_source_dir(tmp_project, mid_spec))
    for n in document["nodes"]:
        if n["id"] == "mid":
            n["version"], n["folder"] = "draft", str(pointer.folder)

    口 = Step(step_num=3, name="Preview: up cached, mid and low new, no key for them",
             purpose="Draft mode never looks up the cache: a lookup needs a key, "
                     "and every classified row carries the key it was looked up "
                     "under, so a new row with no key was never classified")
    report = cache_status(document)
    assert report.blocked_reason is None, report.blocked_reason
    rows = {r.node_id: r for r in report.rows}
    assert rows["up"].status == "cached_local"
    assert rows["up"].cache_key and rows["up"].source_run_id is not None
    assert rows["mid"].status == "new_step_changed"
    assert rows["low"].status == "new_upstream_reruns"
    for draft_mode in (rows["mid"], rows["low"]):
        assert draft_mode.cache_key is None
        assert draft_mode.source_run_id is None and draft_mode.source_nid is None
        assert not draft_mode.outputs

    口 = Step(step_num=4, name="The cache-status route marks the draft-mode rows",
             purpose="The canvas reads draft_mode to show the draft reason: true on "
                     "mid and low beside the status the server gives them, false "
                     "on up")
    client = canvas_client(tmp_project, monkeypatch)
    resp = client.post("/api/wfc/cache-status", json=document)
    assert resp.status_code == 200, resp.text
    sent = {r["node_id"]: r for r in resp.json()["rows"]}
    assert (sent["up"]["status"], sent["up"]["draft_mode"]) == ("cached_local", False)
    assert (sent["mid"]["status"], sent["mid"]["draft_mode"]) == ("new_step_changed", True)
    assert (sent["low"]["status"], sent["low"]["draft_mode"]) == ("new_upstream_reruns", True)
