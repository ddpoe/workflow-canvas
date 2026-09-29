"""The second-selector rule (Graph unit, catalog case ``legality-second-selector``).

A method node fed by two per-sample ``input_selector`` nodes is refused at
both moments -- structural validation returns an error, the load raises --
naming the method node and both selectors with one message. A selector
beside a reference, or beside a method upstream on another slot, passes at
both moments. Literal documents through ``wfc.graph.validate_structure``
(sparse) and ``wfc.graph.load_pipeline`` (canonical).
"""

from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc.graph import load_pipeline, validate_structure

CONTRACTS = {
    "demo.join": {
        "input_slots": {"data": {"required": True}, "other": {"required": True}},
        "output_slots": {"out": {"type": "csv"}},
    },
    "demo.prep": {
        "input_slots": {"data": {"required": True}},
        "output_slots": {"out": {"type": "csv"}},
    },
}


def _selector(node_id: str) -> dict:
    return {"id": node_id, "type": "input_selector", "samples": ["s1"],
            "fan_mode": "out"}


def _method(node_id: str, method: str) -> dict:
    return {"id": node_id, "type": "method", "method": method, "module": "demo",
            "script": f"methods/{method}/{method}.py", "env": "container:demo",
            "params": {}}


def _document(nodes: list[dict], links: list[tuple[str, str, str]]) -> dict:
    """A document whose links carry both spellings of the target slot."""
    return {
        "nodes": nodes,
        "links": [
            {"source": s, "target": t, "targetHandle": slot, "target_slot": slot,
             "sourceHandle": "out", "source_slot": "out"}
            for s, t, slot in links
        ],
        "samples": [],
    }


def _both_moments(document: dict, reference_outputs: dict | None = None):
    """The validation result and the load outcome (the definition or the error)."""
    result = validate_structure(document, CONTRACTS)
    try:
        loaded = load_pipeline(document, contract_map=CONTRACTS,
                               reference_outputs=reference_outputs or {})
    except ValueError as exc:
        loaded = exc
    return result, loaded


@workflow(purpose="A method node fed by two per-sample selectors on different "
                  "slots is refused by structural validation and by the load "
                  "with one message naming the node and both selectors; a "
                  "selector beside a reference, or beside a method upstream "
                  "on another slot, passes at both moments")
def test_second_per_sample_selector_is_refused_at_both_moments():
    two_selectors = _document(
        [_selector("sel_a"), _selector("sel_b"), _method("join_1", "join")],
        [("sel_a", "join_1", "data"), ("sel_b", "join_1", "other")],
    )
    result, refused = _both_moments(two_selectors)
    assert result["valid"] is False
    (error,) = [e for e in result["errors"] if "per-sample input selectors" in e]
    assert "'join_1'" in error and "'sel_a'" in error and "'sel_b'" in error
    assert isinstance(refused, ValueError)
    assert str(refused) == error

    beside_a_reference = _document(
        [_selector("sel_a"),
         {"id": "ref_1", "type": "run_reference", "run_id": "run-1"},
         _method("join_1", "join")],
        [("sel_a", "join_1", "data"), ("ref_1", "join_1", "other")],
    )
    result, loaded = _both_moments(
        beside_a_reference,
        {"ref_1": {"output_paths": {"out": ".runs/run-1/out/out.csv"},
                   "sample": "s1"}},
    )
    assert result["valid"] is True, result["errors"]
    assert not isinstance(loaded, ValueError)

    beside_a_method = _document(
        [_selector("sel_a"), _method("prep_1", "prep"), _method("join_1", "join")],
        [("sel_a", "prep_1", "data"), ("sel_a", "join_1", "data"),
         ("prep_1", "join_1", "other")],
    )
    result, loaded = _both_moments(beside_a_method)
    assert result["valid"] is True, result["errors"]
    assert not isinstance(loaded, ValueError)
    assert loaded.steps[-1].depends_on == ["prep_1"]
