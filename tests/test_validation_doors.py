"""One validation verdict at every door a pipeline document comes through.

The canvas's validate route, its submission, ``wfc run-pipeline`` and the
runs preview all ask ``wfc.execution.validation`` whether a document may run,
so one malformed document is refused with the same lines at each of them; the
canvas paths answer HTTP 400, never a 500. A node pinned to an older version
is checked against that version's contract, not the newest one's.
"""

from __future__ import annotations

import json

import yaml
from axiom_annotations import Step, workflow

from wfc.graph.keys import NODE_KEYS, TOP_LEVEL_KEYS

from tests.fixtures.fakes import stub_readiness_probes
from tests.fixtures.routes import canvas_client, register_again_with_params
from tests.fixtures.routes.clients import run_cli
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.project import method_source_dir
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID

M = DEFAULT_MODULE

#: The params the second version of ``fit`` declares.
_TYPED_PARAMS = {
    "count": {"type": "int", "required": True,
              "constraints": {"min": 1, "max": 10}},
    "mode": {"type": "str", "required": False,
             "constraints": {"enum": ["fast", "slow"]}},
    "ratio": {"type": "float", "required": False},
}


def _malformed_document() -> dict:
    """A canvas-form document with one of each refusal the validator gives."""
    def method(node_id: str, params: dict, **extra) -> dict:
        return {"id": node_id, "type": "method", "module": M, "method": "fit",
                "params": params, **extra}
    return {
        "name": "doors", "bogus_top": 1, "samples": ["S1"],
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["S1"]},
            method("fit", {"extra": 1, "mode": "medium", "ratio": True},
                   colour="red"),
            method("fit2", {"count": True}),
            method("fit3", {"count": 11}),
        ],
        "links": [{"source": "sel", "target": t, "targetHandle": "data"}
                  for t in ("fit", "fit2", "fit3")],
    }


_EXPECTED = [
    "Pipeline: unknown top-level key 'bogus_top' "
    f"(a pipeline takes {', '.join(sorted(TOP_LEVEL_KEYS))})",
    f"Node 'fit': unknown key 'colour' (a node takes {', '.join(sorted(NODE_KEYS))})",
    f"Node 'fit' ({M}.fit v2): parameter 'extra' is not declared by v2; remove it",
    f"Node 'fit' ({M}.fit v2): required parameter 'count' (int) is missing",
    f"Node 'fit' ({M}.fit v2): parameter 'mode' must be one of \"fast\", \"slow\", "
    f"got \"medium\"",
    f"Node 'fit' ({M}.fit v2): parameter 'ratio' expects float, got true (bool)",
    f"Node 'fit2' ({M}.fit v2): parameter 'count' expects int, got true (bool)",
    f"Node 'fit3' ({M}.fit v2): parameter 'count' must be <= 10, got 11",
]


@workflow(purpose="One malformed document (unknown top-level and node keys, an "
                  "undeclared parameter, a missing required one, bool for int "
                  "and for float, an enum and a max breach) is refused with the "
                  "same lines at canvas validate, canvas submit, CLI "
                  "run-pipeline and the runs preview; the canvas paths answer "
                  "400, never a 500, and nothing is launched")
def test_one_malformed_document_gets_the_same_verdict_at_every_door(
        tmp_project, monkeypatch, tmp_path):
    口 = Step(step_num=1, name="Register fit twice; v2 declares typed params",
             purpose="Both versions through production registration; the newest "
                     "(v2) declares count (int, required, 1..10), mode (enum) "
                     "and ratio (float)")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id="doors", name="doors",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), _TYPED_PARAMS)
    stub_readiness_probes(monkeypatch, docker="ok")
    document = _malformed_document()
    client = canvas_client(project.root, monkeypatch)

    口 = Step(step_num=2, name="Canvas validate",
             purpose="Not valid; every refusal is among the errors")
    resp = client.post("/api/workflow/validate", json=document)
    assert resp.status_code == 200, resp.text
    verdict = resp.json()
    assert verdict["valid"] is False
    assert [e for e in verdict["errors"] if e in _EXPECTED] == _EXPECTED

    口 = Step(step_num=3, name="Canvas submit",
             purpose="HTTP 400 whose detail is the refusals, one per line")
    resp = client.post("/api/workflow/run", json=document)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].split("\n") == _EXPECTED

    口 = Step(step_num=4, name="The runs preview",
             purpose="HTTP 400 with the same detail")
    resp = client.post("/api/wfc/cache-status", json=document)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].split("\n") == _EXPECTED

    口 = Step(step_num=5, name="CLI run-pipeline",
             purpose="Exit 1; stderr names the refusal, then each line")
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = run_cli("run-pipeline", "--pipeline", str(path))
    assert result.returncode == 1, result.stderr
    lines = result.stderr.strip().splitlines()
    assert lines[0] == "ERROR: the pipeline document is refused:"
    assert [line.strip() for line in lines[1:]] == _EXPECTED


@workflow(purpose="A param_sets variant runs in place of its node's params, so "
                  "it is checked against the node's version like the params: "
                  "a variant with a fractional int and an undeclared parameter "
                  "is refused at the runs preview, each refusal naming node, "
                  "variant, parameter and version, while the node's own valid "
                  "params pass; the variant fixed, the preview answers 200; a "
                  "variant that is not a mapping is refused naming it, and a "
                  "node's param_sets entry that is not a mapping is refused "
                  "naming the node")
def test_a_param_sets_variant_is_checked_against_its_nodes_version(
        tmp_project, monkeypatch):
    from wfc.execution.validation import validation_refusals

    口 = Step(step_num=1, name="Register fit twice; v2 declares typed params",
             purpose="Production registration; v2 declares count (int, "
                     "required, 1..10), mode and ratio")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id="variants", name="variants",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), _TYPED_PARAMS)
    stub_readiness_probes(monkeypatch, docker="ok")
    client = canvas_client(project.root, monkeypatch)

    def document(variant: dict) -> dict:
        doc = _spelled_document(2, 0.5)
        doc["param_sets"] = {"fit": {"base": {"count": 2, "ratio": 0.5},
                                     "wide": variant}}
        return doc

    口 = Step(step_num=2, name="The preview refuses the bad variant by name",
             purpose="HTTP 400; the refusals name node, variant, parameter "
                     "and version; the node's own params raise none")
    resp = client.post("/api/wfc/cache-status",
                       json=document({"count": 1.5, "extra": 1}))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].split("\n") == [
        f"Node 'fit' variant 'wide' ({M}.fit v2): parameter 'extra' is not "
        f"declared by v2; remove it",
        f"Node 'fit' variant 'wide' ({M}.fit v2): parameter 'count' expects "
        f"int, got 1.5 (float)",
    ]

    口 = Step(step_num=3, name="The variant fixed, the preview answers",
             purpose="HTTP 200; fit's two variants key apart")
    resp = client.post("/api/wfc/cache-status",
                       json=document({"count": 3, "ratio": 0.5}))
    assert resp.status_code == 200, resp.text
    fit_keys = {row.get("cache_key", row.get("cacheKey"))
                for row in resp.json()["rows"]
                if row.get("node_id", row.get("nodeId")) == "fit"}
    assert len(fit_keys) == 2 and None not in fit_keys, resp.text

    口 = Step(step_num=4, name="A variant that is not a mapping is refused",
             purpose="The validator names node and variant")
    assert validation_refusals(document(3)) == [
        "Node 'fit' variant 'wide': must be a mapping of parameters, got int"]

    口 = Step(step_num=5, name="A node's param_sets entry that is not a mapping is refused",
             purpose="The entry must map variant names to params; a list of "
                     "names is refused naming the node, and nothing else is")
    listed = _spelled_document(2, 0.5)
    listed["param_sets"] = {"fit": ["base", "wide"]}
    assert validation_refusals(listed) == [
        "Node 'fit': its param_sets entry must map variant names to params, "
        "got list"]


@workflow(purpose="A node pinned to an older version is validated against that "
                  "version's contract: a param v1 declares and v2 dropped passes "
                  "on a v1 pin and is refused, naming v2, on a v2 pin or no pin")
def test_a_pinned_node_is_checked_against_its_own_versions_contract(
        tmp_project, monkeypatch):
    from wfc.execution.validation import validation_refusals
    from wfc.registration import version_contracts

    口 = Step(step_num=1, name="v1 declares legacy; v2 drops it",
             purpose="Both registered through production")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"legacy": 1})],
        samples=["S1"], pipeline_id="pinned-contract", name="pinned_contract",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), {})
    (v1, _), (v2, _) = version_contracts(M, "fit")

    def document(pin):
        fit = {"id": "fit", "type": "method", "module": M, "method": "fit",
               "params": {"legacy": 1}}
        if pin is not None:
            fit["version"] = pin
        return {"nodes": [{"id": "sel", "type": "input_selector",
                           "samples": ["S1"]}, fit],
                "links": [{"source": "sel", "target": "fit"}], "samples": ["S1"]}

    口 = Step(step_num=2, name="Each pin gets its own version's verdict",
             purpose="v1 pin: accepted. v2 pin and no pin: 'not declared by v2'")
    refused = [f"Node 'fit' ({M}.fit v2): parameter 'legacy' is not declared "
               f"by v2; remove it"]
    assert validation_refusals(document(v1.entry.identity)) == []
    assert validation_refusals(document(v2.entry.identity)) == refused
    assert validation_refusals(document(None)) == refused


@workflow(purpose="Registering a method again after an edit to method.yaml "
                  "that only adds a parameter mints the next version, whose "
                  "contract declares it: a node on v2 that sets the parameter "
                  "passes the validator, and a node pinned to v1 that sets it "
                  "is refused naming v1")
def test_a_params_only_method_yaml_edit_mints_the_next_version(
        tmp_project, monkeypatch):
    from wfc.execution.validation import validation_refusals
    from wfc.registration import register_method, version_contracts

    口 = Step(step_num=1, name="Register fit (v1)",
             purpose="Production registration; v1 declares count")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id="params-edit", name="params_edit",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    spec = scenario.node_by_id("fit")
    method_dir = method_source_dir(project.root, spec)
    script = method_dir / f"{spec.method_name}.py"
    script_before = script.read_bytes()

    口 = Step(step_num=2, name="Add a parameter in method.yaml only; register again",
             purpose="The script and every other file stay as they were")
    contract = yaml.safe_load((method_dir / "method.yaml").read_text(encoding="utf-8"))
    contract["params"] = {**(contract.get("params") or {}),
                          "threshold": {"type": "float", "required": False}}
    (method_dir / "method.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    register_method(method_dir=method_dir, module_name=spec.module, assume_yes=True)
    assert script.read_bytes() == script_before

    口 = Step(step_num=3, name="v2 is minted and its contract carries the parameter",
             purpose="Two versions; only v2's params schema declares threshold")
    versions = version_contracts(M, "fit")
    assert len(versions) == 2
    (v1, v1_contract), (v2, v2_contract) = versions
    assert v1.entry.identity != v2.entry.identity
    assert "threshold" not in (v1_contract.params_schema or {})
    assert "threshold" in v2_contract.params_schema

    def document(pin):
        fit = {"id": "fit", "type": "method", "module": M, "method": "fit",
               "params": {"count": 2, "threshold": 0.5}, "version": pin}
        return {"nodes": [{"id": "sel", "type": "input_selector",
                           "samples": ["S1"]}, fit],
                "links": [{"source": "sel", "target": "fit"}], "samples": ["S1"]}

    口 = Step(step_num=4, name="Validate a node setting threshold on each version",
             purpose="v2: accepted. v1: refused, naming v1")
    assert validation_refusals(document(v2.entry.identity)) == []
    assert validation_refusals(document(v1.entry.identity)) == [
        f"Node 'fit' ({M}.fit v1): parameter 'threshold' is not declared by v1; "
        f"remove it"]


def _spelled_document(count, ratio) -> dict:
    """A canvas-form document whose ``fit`` node writes count and ratio so."""
    return {
        "name": "spelled", "samples": ["S1"],
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["S1"]},
            {"id": "fit", "type": "method", "module": M, "method": "fit",
             "params": {"count": count, "ratio": ratio}},
        ],
        "links": [{"source": "sel", "target": "fit", "targetHandle": "data"}],
    }


@workflow(purpose="Two documents that differ only in how they spell a value "
                  "(an int param written 3.0 or 3, a float param written 1 or "
                  "1.0) prepare to one params dict, preview under one cache "
                  "key, and canvas submit freezes the int as 3 and the float "
                  "as 1.0")
def test_one_value_spelled_two_ways_prepares_and_keys_as_one(
        tmp_project, monkeypatch):
    from wfc import layout
    from wfc.canvas.state import _active_jobs
    from wfc.execution.prepare import prepare_document

    from tests.fixtures.fakes.engine import fake_engine_process

    口 = Step(step_num=1, name="Register fit twice; v2 declares count and ratio",
             purpose="Production registration; v2 declares count (int, 1..10) "
                     "and ratio (float)")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id="spelled", name="spelled",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), _TYPED_PARAMS)
    stub_readiness_probes(monkeypatch, docker="ok")
    floaty, inty = _spelled_document(3.0, 1), _spelled_document(3, 1.0)

    口 = Step(step_num=2, name="Prepare both documents",
             purpose="One params dict: count an int, ratio a float")
    prepared = [next(n for n in prepare_document(doc)["nodes"] if n["id"] == "fit")
                for doc in (floaty, inty)]
    assert prepared[0]["params"] == prepared[1]["params"] == {"count": 3, "ratio": 1.0}
    for fit in prepared:
        assert type(fit["params"]["count"]) is int
        assert type(fit["params"]["ratio"]) is float

    口 = Step(step_num=3, name="The runs preview keys both alike",
             purpose="The preview's predicted key for fit is the same for both")
    client = canvas_client(project.root, monkeypatch)
    keys = []
    for doc in (floaty, inty):
        resp = client.post("/api/wfc/cache-status", json=doc)
        assert resp.status_code == 200, resp.text
        fit_keys = {row.get("cache_key", row.get("cacheKey"))
                    for row in resp.json()["rows"]
                    if row.get("node_id", row.get("nodeId")) == "fit"}
        assert len(fit_keys) == 1 and None not in fit_keys, resp.text
        keys.append(fit_keys.pop())
    assert keys[0] == keys[1]

    口 = Step(step_num=4, name="Canvas submit freezes the declared forms",
             purpose="The frozen pipeline.json canvas submit writes records "
                     "count as 3 and ratio as 1.0")
    runs_dir = layout.pipeline_run_dir(project.root, "probe").parent
    before = set(runs_dir.glob("*/pipeline.json"))
    with fake_engine_process(only_snakemake=True):
        resp = client.post("/api/workflow/run", json=floaty)
        assert resp.status_code == 200, resp.text
        _active_jobs[resp.json()["job_id"]]["thread"].join(timeout=15)
    _active_jobs.clear()
    (frozen_path,) = set(runs_dir.glob("*/pipeline.json")) - before
    frozen = frozen_path.read_text(encoding="utf-8")
    fit = next(n for n in json.loads(frozen)["nodes"] if n["id"] == "fit")
    assert fit["params"] == {"count": 3, "ratio": 1.0}
    assert '"count": 3,' in frozen and '"ratio": 1.0' in frozen
