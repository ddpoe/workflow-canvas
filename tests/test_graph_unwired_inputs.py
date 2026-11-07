"""The unwired-required-input rule (Graph unit).

A required input nothing is connected to is refused by structural validation
and by the load with one message naming the node, its method and the input.
An input is required unless it says ``required: false``; an input with no
``required`` key is required. A link naming no slot feeds ``data``; links
from selectors, methods and run references all count. A draft node is read
against its draft's contract.
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
    "demo.loose": {
        # 'other' says nothing about required: it is required.
        "input_slots": {"data": {"required": True}, "other": {"type": ".csv"}},
        "output_slots": {"out": {"type": "csv"}},
    },
    "demo.optional": {
        "input_slots": {"data": {"required": True}, "other": {"required": False}},
        "output_slots": {"out": {"type": "csv"}},
    },
    "demo.prep": {
        "input_slots": {"data": {}},
        "output_slots": {"out": {"type": "csv"}},
        "versions": {
            "v1": {"input_slots": {"data": {}}, "output_slots": {"out": {"type": "csv"}}},
            # The draft renamed its input and added a second one.
            "draft": {"input_slots": {"table": {}, "extra": {"required": True}},
                      "output_slots": {"out": {"type": "csv"}}},
        },
    },
}


def _selector(node_id: str = "sel") -> dict:
    return {"id": node_id, "type": "input_selector", "samples": ["s1"], "fan_mode": "out"}


def _method(node_id: str, method: str, version: str | None = None) -> dict:
    node = {"id": node_id, "type": "method", "method": method, "module": "demo",
            "env": "container:demo", "params": {}}
    if version:
        node["version"] = version
    return node


def _link(source: str, target: str, slot: str | None = None) -> dict:
    link = {"source": source, "target": target}
    if slot:
        link["targetHandle"] = link["target_slot"] = slot
    return link


def _both_moments(document: dict):
    """The validation's unwired-input errors and the load's refusal (or None)."""
    result = validate_structure(document, CONTRACTS)
    unwired = [e for e in result["errors"] if "nothing is connected to" in e]
    try:
        load_pipeline(document, contract_map=CONTRACTS, reference_outputs={})
    except ValueError as exc:
        return unwired, str(exc)
    return unwired, None


def _doc(nodes: list[dict], links: list[dict]) -> dict:
    return {"nodes": nodes, "links": links, "samples": []}


@workflow(purpose="A required input nothing is connected to is refused by "
                  "structural validation and by the load with the same line, "
                  "naming the node, its method, the input, the declared inputs "
                  "and the fix; an input with no required key is required; a "
                  "draft node is read against its draft's inputs")
def test_an_unwired_required_input_is_refused_at_both_moments_with_the_same_line():
    unwired, refused = _both_moments(_doc(
        [_selector(), _method("j", "join")], [_link("sel", "j", "data")]))
    assert unwired == [
        "Node 'j': demo.join declares input 'other', which nothing is connected "
        "to (its inputs: 'data', 'other'). Connect a link to 'other', or "
        "declare it `required: false` in the method's method.yaml."]
    assert refused == unwired[0]

    # No `required` key: required.
    unwired, refused = _both_moments(_doc(
        [_selector(), _method("l", "loose")], [_link("sel", "l", "data")]))
    assert len(unwired) == 1 and "declares input 'other'" in unwired[0]
    assert refused == unwired[0]

    # A draft node: its draft's inputs, labelled as the draft.
    unwired, refused = _both_moments(_doc(
        [_selector(), _method("p", "prep", version="draft")],
        [_link("sel", "p", "table")]))
    assert len(unwired) == 1
    assert unwired[0].startswith("Node 'p': demo.prep's draft declares input 'extra'")
    assert refused == unwired[0]


@workflow(purpose="A correctly fed input is never refused: required: false may "
                  "be left unconnected, a link naming no slot feeds data, and "
                  "links from a method and from a run reference count")
def test_optional_slotless_and_reference_links_are_not_refused():
    # required: false left unconnected.
    unwired, refused = _both_moments(_doc(
        [_selector(), _method("o", "optional")], [_link("sel", "o", "data")]))
    assert (unwired, refused) == ([], None)

    # A slotless link feeds `data`.
    unwired, refused = _both_moments(_doc(
        [_selector(), _method("p", "prep", version="v1")], [_link("sel", "p")]))
    assert (unwired, refused) == ([], None)

    # A method link and a run-reference link each wire their input, at both
    # moments: at the load the reference is not a method node, and its link
    # still counts.
    nodes = [_selector(), {"id": "ref", "type": "run_reference", "run_id": "r1"},
             _method("p", "prep", version="v1"), _method("j", "join")]
    fed = _doc(nodes, [_link("sel", "p"), _link("p", "j", "data"),
                       _link("ref", "j", "other")])
    result = validate_structure(fed, CONTRACTS)
    assert not [e for e in result["errors"] if "nothing is connected to" in e], result
    reference_outputs = {"ref": {"output_paths": {"out": "/runs/r1/out.csv"},
                                 "sample": "s1", "method": "prep"}}
    load_pipeline(fed, contract_map=CONTRACTS, reference_outputs=reference_outputs)

    unfed = _doc(nodes, [_link("sel", "p"), _link("p", "j", "data")])
    with pytest.raises(ValueError,
                       match="declares input 'other', which nothing is connected to"):
        load_pipeline(unfed, contract_map=CONTRACTS, reference_outputs=reference_outputs)


def test_inputs_fed_outside_the_document_count_as_connected():
    result = validate_structure(
        _doc([_selector(), _method("j", "join")], [_link("sel", "j", "data")]),
        CONTRACTS, fed_outside={"j": ["other"]})
    assert result["valid"], result
