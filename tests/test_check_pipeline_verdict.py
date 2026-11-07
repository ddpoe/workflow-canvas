"""Catalog case 6: ``wfc check-pipeline`` gives canvas Validate's verdict.

For every refused document the catalogs already exercise (the loader refusal
kinds of case 7 that are documents, the malformed document of the validation
doors, a required input nothing is connected to, and case 4's link to a
slot its node does not declare), ``check-pipeline
--json`` and the canvas's validate route give the same verdict: not valid,
the same errors in the same order. A document ``check-pipeline`` passes is one
``wfc run-pipeline`` accepts and launches. The file-level kinds (a missing
file, bad JSON, a non-object) have no canvas counterpart: the canvas posts a
document, never a file.

Declared fixture deviations: none. The project is built by the harness
through production registration (a second version of ``fit`` with typed
params, a second version of ``pair`` whose ``method.yaml`` adds a required
input); the engine process is the only thing stubbed.
"""
from __future__ import annotations

import json

import pytest
import yaml
from axiom_annotations import Step, workflow

from tests.fixtures.pipeline_verbs import (
    TYPED_PARAMS,
    docker_up_project,
    error_lines,
    fit_document,
    launching,
    malformed_document,
    refusal_kinds,
    undeclared_slot_document,
    write_document,
)
from tests.fixtures.routes import canvas_client, register_again_with_params
from tests.harness import node, wire
from tests.harness.project import method_source_dir
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID

M = DEFAULT_MODULE


def _unwired_document() -> dict:
    """``pair`` fed only ``data``; its newest version also requires ``ref``."""
    return {
        "name": "unwired", "samples": ["S1"],
        "nodes": [{"id": "sel", "type": "input_selector", "fan_mode": "out",
                   "samples": ["S1"]},
                  {"id": "pair", "type": "method", "module": M, "method": "pair",
                   "params": {}}],
        "links": [{"source": "sel", "target": "pair", "targetHandle": "data"}],
    }


# The validator words two of case 7's document refusals differently from the
# loader that refusal_kinds() quotes: it names an unregistered method as
# "Unknown method", and it reports the dangling link as a second edge into
# fit's ``data`` slot rather than as an unknown source node.
_VALIDATOR_WORDS = {
    "unregistered method": ["Unknown method", f"{M}.", "'missing'"],
    "Graph refusal": ["Input slot 'data' on node 'fit' has 2 incoming edges"],
}


def _refused_documents() -> dict[str, tuple[dict, list[str]]]:
    """Every refused document the catalogs exercise, by name, with words its errors carry.

    Case 7's document kinds keep the words refusal_kinds() gives them, except
    where the validator words the same refusal its own way (``_VALIDATOR_WORDS``).
    """
    documents = {kind: (content, _VALIDATOR_WORDS.get(kind, words))
                 for kind, (content, words) in refusal_kinds().items()
                 if isinstance(content, dict)}
    documents["malformed params and keys"] = (malformed_document(),
                                              ["unknown top-level key 'bogus_top'"])
    documents["unwired required input"] = (_unwired_document(),
                                           ["Node 'pair'", "'ref'"])
    documents["link to an undeclared slot"] = (undeclared_slot_document(),
                                               ["'fit'", "'table'", "does not declare"])
    return documents


def _register_project(tmp_project, monkeypatch):
    """fit v2 declares typed params; pair v2 adds a required input ``ref``."""
    project = docker_up_project(tmp_project, monkeypatch,
                       node("fit", inputs=[wire(SELECTOR_ID)]),
                       node("pair", inputs=[wire(SELECTOR_ID)]))
    register_again_with_params(project.root, project.scenario.node_by_id("fit"),
                               TYPED_PARAMS)
    from wfc.registration import register_method

    spec = project.scenario.node_by_id("pair")
    method_dir = method_source_dir(project.root, spec)
    contract = yaml.safe_load((method_dir / "method.yaml").read_text(encoding="utf-8"))
    contract["inputs"] = {**(contract.get("inputs") or {}),
                          "ref": {"type": ".csv", "required": True}}
    (method_dir / "method.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    register_method(method_dir=method_dir, module_name=spec.module, assume_yes=True)
    return project


@pytest.mark.parametrize("kind", sorted(_refused_documents()))
@workflow(purpose="Case 6: for a refused document the catalogs exercise, "
                  "check-pipeline --json and canvas Validate give one verdict: "
                  "not valid, the same errors in the same order, each also an "
                  "ERROR: line on check-pipeline's stderr")
def test_check_pipeline_gives_canvas_validates_verdict(kind, cli, tmp_project,
                                                       monkeypatch, tmp_path):
    口 = Step(step_num=1, name="Register fit and pair",
             purpose="Production registration; fit v2 declares typed params, "
                     "pair v2 requires a second input")
    project = _register_project(tmp_project, monkeypatch)
    document, words = _refused_documents()[kind]

    口 = Step(step_num=2, name="Canvas Validate",
             purpose="The validate route's verdict on the document")
    resp = canvas_client(project.root, monkeypatch).post(
        "/api/workflow/validate", json=document)
    assert resp.status_code == 200, resp.text
    canvas = resp.json()
    assert canvas["valid"] is False, canvas
    assert any(all(w in e for w in words) for e in canvas["errors"]), canvas

    口 = Step(step_num=3, name="check-pipeline",
             purpose="Exit 1; the same verdict and errors, each an ERROR: line")
    result = cli("check-pipeline", "--pipeline", write_document(tmp_path, "doc", document),
                 "--json")
    assert result.returncode == 1, result.stderr
    checked = json.loads(result.stdout)
    assert checked["valid"] is False
    assert checked["errors"] == canvas["errors"]
    assert error_lines(result.stderr) == [f"ERROR: {e}" for e in canvas["errors"]]


@workflow(purpose="Case 6: a document check-pipeline passes is one run-pipeline "
                  "accepts: check-pipeline exits 0 and canvas Validate agrees, "
                  "then run-pipeline launches the engine once and exits 0")
def test_a_document_check_pipeline_passes_runs(cli, tmp_project, monkeypatch, tmp_path):
    口 = Step(step_num=1, name="Register fit and pair",
             purpose="The same project the refused documents are checked in")
    project = _register_project(tmp_project, monkeypatch)
    document = fit_document()
    document["nodes"][1]["params"] = {"count": 2}
    path = write_document(tmp_path, "good", document)

    口 = Step(step_num=2, name="check-pipeline and canvas Validate pass it",
             purpose="Exit 0, valid, no errors; the validate route agrees")
    result = cli("check-pipeline", "--pipeline", path, "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True
    resp = canvas_client(project.root, monkeypatch).post(
        "/api/workflow/validate", json=document)
    assert resp.json()["valid"] is True, resp.text

    口 = Step(step_num=3, name="run-pipeline accepts it",
             purpose="Exit 0 and the engine launched once")
    result, launches = launching(cli, "run-pipeline", "--pipeline", path)
    assert result.returncode == 0, result.stderr
    assert len(launches) == 1
