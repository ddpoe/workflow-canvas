"""One preparation for every door a pipeline document comes through.

The canvas's submission, ``wfc run-pipeline`` and the cache-status preview
all prepare a document through ``wfc.execution.prepare.prepare_document``:
substitute its variables, then enrich a sparse (canvas-form) document
against the registered contracts. A sparse document handed to
``run-pipeline`` is therefore loaded exactly as the canvas would load it --
named output slots and all -- and the frozen document the canvas writes
passes through a second preparation unchanged.
"""

from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.harness import Scenario, node, selector, wire
from tests.harness.project import build_project
from tests.harness.scenario import SELECTOR_ID

_ENRICHED_ONLY = ("script", "slot_outputs", "slot_types", "env")


def _sparse_form(enriched: dict) -> dict:
    """The canvas's sparse form of an enriched document, with one variable."""
    nodes = []
    for n in enriched["nodes"]:
        sparse = {k: v for k, v in n.items() if k not in _ENRICHED_ONLY}
        if sparse.get("type") in (None, "method") and sparse.get("method") == "producer":
            sparse["params"] = {**sparse.get("params", {}), "rows": {"$var": "rows"}}
        nodes.append(sparse)
    links = []
    for link in enriched["links"]:
        entry = {"source": link["source"], "target": link["target"]}
        if link.get("source_slot"):
            entry["sourceHandle"] = link["source_slot"]
        if link.get("target_slot"):
            entry["targetHandle"] = link["target_slot"]
        links.append(entry)
    return {**{k: v for k, v in enriched.items() if k not in ("nodes", "links")},
            "nodes": nodes, "links": links,
            "variables": {"rows": {"type": "number", "value": 3}}}


@workflow(purpose="run-pipeline prepares a sparse document the way the canvas "
                  "does: its variables are substituted and each method node is "
                  "enriched with the named output slots its contract declares, "
                  "so the composer loads it; an enriched document passes "
                  "through unchanged")
def test_sparse_document_is_prepared_as_the_canvas_does(tmp_project, monkeypatch):
    from wfc.execution.composer import load_pipeline_from_document
    from wfc.execution.prepare import prepare_document

    口 = Step(step_num=1, name="Build a project with a named-output method",
             purpose="The producer declares a 'table' output the consumer reads "
                     "by name; the harness writes the enriched document")
    scenario = Scenario(
        nodes=[
            selector(),
            node("producer", inputs=[wire(SELECTOR_ID)],
                 outputs={"table": ".csv"}),
            node("consumer", inputs=[wire("producer", source_slot="table")]),
        ],
        pipeline_id="prepare-doc", name="prepare_doc",
    )
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    enriched = json.loads(project.pipeline_json.read_text(encoding="utf-8"))

    口 = Step(step_num=2, name="Prepare the canvas's sparse form",
             purpose="No script, slot map or env on any node; links spelled "
                     "with the canvas's handles; one param is a variable")
    prepared = prepare_document(_sparse_form(enriched))

    口 = Step(step_num=3, name="Same nodes and links as the canvas enrichment",
             purpose="The named output slot and its filename come from the "
                     "registered contract; the variable is a literal")
    by_id = {n["id"]: n for n in prepared["nodes"]}
    for n in enriched["nodes"]:
        for key in ("slot_outputs", "slot_types", "env"):
            if key in n:
                assert by_id[n["id"]][key] == n[key], (n["id"], key)
    assert by_id["producer"]["slot_outputs"] == {"table": "table.csv"}
    assert by_id["producer"]["script"]
    assert by_id["producer"]["params"]["rows"] == 3
    assert prepared["links"] == enriched["links"]
    loaded = load_pipeline_from_document(prepared)
    assert loaded.pipeline is not None

    口 = Step(step_num=4, name="An enriched document passes through",
             purpose="The frozen document run-pipeline reads back is prepared "
                     "again without change")
    assert prepare_document(enriched)["nodes"] == enriched["nodes"]
