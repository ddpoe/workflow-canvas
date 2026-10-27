"""Preparation fills every declared default a document leaves out.

``wfc.execution.prepare.prepare_document`` -- the one preparation the canvas's
validate route and submission, ``wfc run-pipeline`` and the cache-status
preview share -- fills each parameter a method node, or one of its
``param_sets`` variants, leaves out with the default the version it runs
declares. So a node that leaves a parameter out and one that writes its
default are one params dict and one cache key; a value the document writes,
``null`` included, is kept; and a required parameter with a usable default
runs at it. Preparing a prepared document changes nothing.

Every method here is registered through production registration: the
harness registers ``fit`` (v1), then its ``method.yaml`` params are edited
and it is registered again, minting the version that declares the defaults.
"""

from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_docker_image_inspect, stub_readiness_probes
from tests.fixtures.routes import canvas_client, register_again_with_params
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID

M = DEFAULT_MODULE

#: The params the defaults-declaring version of ``fit`` declares. ``count``
#: is required with a default; ``ratio``'s default is written as an int for
#: a float parameter; ``seed`` declares a null default, which is none.
_DEFAULTED = {
    "count": {"type": "int", "required": True, "default": 3,
              "constraints": {"min": 1, "max": 10}},
    "ratio": {"type": "float", "required": False, "default": 1},
    "label": {"type": "str", "required": False, "default": "base"},
    "seed": {"type": "int", "required": False, "default": None},
}


def _project(tmp_project, monkeypatch, pipeline_id: str):
    """Build a selector -> fit project and return it with fit's node spec."""
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id=pipeline_id, name=pipeline_id.replace("-", "_"),
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    return project, scenario.node_by_id("fit")


def _document(*fits: tuple[str, dict]) -> dict:
    """A canvas-form document: one selector feeding each ``(id, params)`` fit node."""
    nodes = [{"id": "sel", "type": "input_selector", "fan_mode": "out",
              "samples": ["S1"]}]
    links = []
    for node_id, params in fits:
        nodes.append({"id": node_id, "type": "method", "module": M,
                      "method": "fit", "params": params})
        links.append({"source": "sel", "target": node_id, "targetHandle": "data"})
    return {"name": "defaults", "samples": ["S1"], "nodes": nodes, "links": links}


def _fit(document: dict, node_id: str = "fit") -> dict:
    (found,) = [n for n in document["nodes"] if n["id"] == node_id]
    return found


def _preview_key(client, document: dict, node_id: str = "fit") -> str:
    """The cache-status preview's one predicted key for a node."""
    resp = client.post("/api/wfc/cache-status", json=document)
    assert resp.status_code == 200, resp.text
    keys = {row.get("cache_key", row.get("cacheKey"))
            for row in resp.json()["rows"]
            if row.get("node_id", row.get("nodeId")) == node_id}
    assert len(keys) == 1 and None not in keys, resp.text
    return keys.pop()


@workflow(purpose="A node that leaves a parameter out and one that writes its "
                  "declared default prepare to one params dict and preview "
                  "under one cache key; a node that writes null keeps null "
                  "under its own key; a declared null default is never filled")
def test_left_out_and_written_at_its_default_prepare_and_key_as_one(
        tmp_project, monkeypatch):
    from wfc.execution.prepare import prepare_document

    口 = Step(step_num=1, name="Register fit; its next version declares defaults",
             purpose="Production registration; ratio defaults to 1 (a float), "
                     "label to 'base', seed declares a null default")
    project, spec = _project(tmp_project, monkeypatch, "same-as-unset")
    register_again_with_params(project.root, spec, _DEFAULTED)
    stub_readiness_probes(monkeypatch, docker="ok")
    left_out = _document(("fit", {"count": 3}))
    written = _document(("fit", {"count": 3, "ratio": 1.0}))
    nulled = _document(("fit", {"count": 3, "ratio": None}))

    口 = Step(step_num=2, name="Prepare the three documents",
             purpose="Left out and written prepare to one dict carrying every "
                     "usable default; null stays null; seed is never added")
    prepared = [_fit(prepare_document(doc))["params"]
                for doc in (left_out, written, nulled)]
    assert prepared[0] == prepared[1] == {"count": 3, "ratio": 1.0, "label": "base"}
    assert type(prepared[0]["ratio"]) is float
    assert prepared[2] == {"count": 3, "ratio": None, "label": "base"}

    口 = Step(step_num=3, name="The runs preview keys them",
             purpose="Left out and written share one key; null has its own")
    client = canvas_client(project.root, monkeypatch)
    keys = [_preview_key(client, doc) for doc in (left_out, written, nulled)]
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]


@workflow(purpose="A node with two param_sets variants, each leaving out a "
                  "different defaulted parameter, prepares each variant filled "
                  "on its own by the version its node runs -- a pin to an "
                  "older version gets that version's defaults, not the newest's")
def test_each_variant_is_filled_by_its_nodes_version(tmp_project, monkeypatch):
    from wfc.execution.prepare import prepare_document
    from wfc.registration import version_contracts

    口 = Step(step_num=1, name="Two defaults-declaring versions",
             purpose="v2 defaults ratio to 1 and label to 'base'; v3 to 0.7 "
                     "and 'other'")
    project, spec = _project(tmp_project, monkeypatch, "variant-defaults")
    register_again_with_params(project.root, spec, _DEFAULTED)
    register_again_with_params(project.root, spec, {
        **_DEFAULTED,
        "ratio": {"type": "float", "required": False, "default": 0.7},
        "label": {"type": "str", "required": False, "default": "other"}})
    v2 = version_contracts(M, "fit")[1][0].entry.identity

    口 = Step(step_num=2, name="Prepare a v2-pinned node with two variants",
             purpose="Variant a leaves ratio out, b leaves label out")
    document = _document(("fit", {"count": 3}))
    _fit(document)["version"] = v2
    document["param_sets"] = {"fit": {"a": {"count": 4, "label": "x"},
                                      "b": {"count": 5, "ratio": 0.1}}}
    prepared = prepare_document(document)

    口 = Step(step_num=3, name="Each variant carries v2's default for what it left out",
             purpose="a gains ratio 1.0, b gains label 'base'; the node's own "
                     "params gain both")
    assert prepared["param_sets"]["fit"] == {
        "a": {"count": 4, "label": "x", "ratio": 1.0},
        "b": {"count": 5, "ratio": 0.1, "label": "base"},
    }
    assert _fit(prepared)["params"] == {"count": 3, "ratio": 1.0, "label": "base"}


@workflow(purpose="Preparing a sparse document that leaves defaults out and "
                  "writes an int as 3.0 fills the defaults in their declared "
                  "form and keeps the int rule (3.0 prepares as 3); preparing "
                  "the prepared document again changes nothing")
def test_preparing_a_prepared_document_changes_nothing(tmp_project, monkeypatch):
    from wfc.execution.prepare import prepare_document

    口 = Step(step_num=1, name="Register fit; its next version declares defaults",
             purpose="Production registration")
    project, spec = _project(tmp_project, monkeypatch, "prepare-twice")
    register_again_with_params(project.root, spec, _DEFAULTED)
    document = _document(("fit", {"count": 3.0}))
    document["param_sets"] = {"fit": {"a": {"label": "x"}}}

    口 = Step(step_num=2, name="Prepare once",
             purpose="count is the int 3, ratio the float 1.0, label 'base'; "
                     "the variant is filled too")
    once = prepare_document(document)
    params = _fit(once)["params"]
    assert params == {"count": 3, "ratio": 1.0, "label": "base"}
    assert type(params["count"]) is int and type(params["ratio"]) is float
    assert once["param_sets"]["fit"]["a"] == {"label": "x", "count": 3, "ratio": 1.0}

    口 = Step(step_num=3, name="Prepare the result again",
             purpose="The whole document comes back unchanged")
    assert prepare_document(once) == once


@workflow(purpose="A required parameter whose version declares a usable "
                  "default needs no value: the canvas validate route accepts a "
                  "node leaving it out, and canvas submit and run-pipeline "
                  "freeze it at its default; a required parameter whose "
                  "default is null, or a required str whose default is '', "
                  "still needs a value and is refused (Tier 3)")
def test_a_required_parameter_with_a_default_runs_at_it(tmp_project, monkeypatch,
                                                        tmp_path):
    from wfc import layout
    from wfc.canvas.state import _active_jobs
    from wfc.execution import run_pipeline

    from tests.fixtures.fakes.engine import fake_engine_process

    口 = Step(step_num=1, name="Register fit; count is required with default 3",
             purpose="Production registration")
    project, spec = _project(tmp_project, monkeypatch, "required-default")
    register_again_with_params(project.root, spec, _DEFAULTED)
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    client = canvas_client(project.root, monkeypatch)
    document = _document(("fit", {}))
    filled = {"count": 3, "ratio": 1.0, "label": "base"}

    口 = Step(step_num=2, name="Canvas validate accepts it",
             purpose="Valid, no errors")
    resp = client.post("/api/workflow/validate", json=document)
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid"] is True, resp.text

    口 = Step(step_num=3, name="Canvas submit freezes the default",
             purpose="The frozen pipeline.json records count 3")
    runs_dir = layout.pipeline_run_dir(project.root, "probe").parent
    before = set(runs_dir.glob("*/pipeline.json"))
    with fake_engine_process(only_snakemake=True):
        resp = client.post("/api/workflow/run", json=document)
        assert resp.status_code == 200, resp.text
        _active_jobs[resp.json()["job_id"]]["thread"].join(timeout=15)
    _active_jobs.clear()
    (frozen_path,) = set(runs_dir.glob("*/pipeline.json")) - before
    assert _fit(json.loads(frozen_path.read_text(encoding="utf-8")))["params"] == filled

    口 = Step(step_num=4, name="run-pipeline freezes the default",
             purpose="Only the Snakemake spawn is stubbed; the frozen document "
                     "records count 3")
    path = tmp_path / "required-default.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with fake_engine_process(only_snakemake=True):
        run_pipeline(pipeline_path=str(path), project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id="required-default-cli")
    frozen = json.loads(layout.pipeline_doc_path(
        project.root, "required-default-cli").read_text(encoding="utf-8"))
    assert _fit(frozen)["params"] == filled

    口 = Step(step_num=5, name="A null or '' default on a required parameter is no default",
             purpose="The next version: count required with a null default, "
                     "label required with ''; validate refuses both as missing")
    register_again_with_params(project.root, spec, {
        "count": {"type": "int", "required": True, "default": None},
        "label": {"type": "str", "required": True, "default": ""}})
    resp = client.post("/api/workflow/validate", json=document)
    assert resp.status_code == 200, resp.text
    errors = resp.json()["errors"]
    assert f"Node 'fit' ({M}.fit v3): required parameter 'count' (int) is missing" in errors
    assert f"Node 'fit' ({M}.fit v3): required parameter 'label' (str) is missing" in errors
