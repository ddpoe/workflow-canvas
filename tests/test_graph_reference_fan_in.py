"""The reference fan-in at load (Graph unit, catalog case ``reference-fan-in``).

Two ``run_reference`` roots wired into one input slot of one method node bind,
through ``wfc.graph.load_pipeline`` on literal values, as a two-element list
under that one slot label: the slot is the method's contract and N references
wired into it are N artifacts under one name, not N invented slots.
"""

from __future__ import annotations

from axiom_annotations import workflow

from wfc.graph import load_pipeline

PATH_A = ".runs/run-a/table/table.parquet"
PATH_B = ".runs/run-b/table/table.parquet"


def _reference(node_id: str, run_id: str) -> dict:
    return {"id": node_id, "type": "run_reference", "run_id": run_id}


@workflow(purpose="Two references wired by source slot into one target slot of "
                  "one method node bind as a two-element list under that slot "
                  "label, and the referenced runs' sample joins the sample list")
def test_two_references_on_one_slot_bind_as_one_two_element_list():
    document = {
        "nodes": [
            _reference("ref_a", "run-a"),
            _reference("ref_b", "run-b"),
            {"id": "merge", "type": "method", "method": "merge", "module": "demo",
             "script": "methods/merge/merge.py", "env": "container:demo",
             "params": {}},
        ],
        "links": [
            {"source": "ref_a", "target": "merge",
             "source_slot": "table", "target_slot": "tables"},
            {"source": "ref_b", "target": "merge",
             "source_slot": "table", "target_slot": "tables"},
        ],
        "samples": [],
    }
    reference_outputs = {
        "ref_a": {"output_paths": {"table": PATH_A}, "sample": "S1"},
        "ref_b": {"output_paths": {"table": PATH_B}, "sample": "S1"},
    }

    pipeline = load_pipeline(document, contract_map={},
                             reference_outputs=reference_outputs)

    (merge,) = pipeline.steps
    assert merge.run_ref_inputs == {"tables": [PATH_A, PATH_B]}
    assert merge.depends_on == [], "references are data sources, not steps"
    assert pipeline.samples == ["S1"]
