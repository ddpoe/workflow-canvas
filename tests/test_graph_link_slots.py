"""The link-slot rule and the ``param_sets`` shape (Graph unit).

A link that names a slot its node's version does not declare -- an output
its source method does not declare, an input its target does not -- is
refused by structural validation and by the load with one message naming
the node and the slot (catalog case 4's link half, at the Graph level). A
draft node is checked against its draft's contract. A link naming no slot,
and the source end of a selector or a reference link, are not checked.

A top-level ``param_sets`` that is not a mapping is refused by name wherever
Graph first reads it, never an ``AttributeError``.
"""

from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc.graph import load_pipeline, resolve_variables, validate_structure

CONTRACTS = {
    "demo.prep": {
        "input_slots": {"data": {"required": True}},
        "output_slots": {"out": {"type": "csv"}},
        "versions": {
            "v1": {
                "input_slots": {"data": {"required": True}},
                "output_slots": {"out": {"type": "csv"}},
            },
            # The draft renamed its output and its input.
            "draft": {
                "input_slots": {"table": {"required": True}},
                "output_slots": {"cleaned": {"type": "csv"}},
            },
        },
    },
    "demo.plot": {
        "input_slots": {"data": {"required": True}},
        "output_slots": {"figure": {"type": "png"}},
    },
}


def _selector(node_id: str = "sel") -> dict:
    return {"id": node_id, "type": "input_selector", "samples": ["s1"],
            "fan_mode": "out"}


def _method(node_id: str, method: str, version: str | None = None) -> dict:
    node = {"id": node_id, "type": "method", "method": method, "module": "demo",
            "env": "container:demo", "params": {}}
    if version:
        node["version"] = version
    return node


def _link(source: str, target: str, source_slot: str | None,
          target_slot: str | None) -> dict:
    """A link carrying both spellings of each slot it names."""
    link = {"source": source, "target": target}
    if source_slot:
        link["sourceHandle"] = link["source_slot"] = source_slot
    if target_slot:
        link["targetHandle"] = link["target_slot"] = target_slot
    return link


def _document(nodes: list[dict], links: list[dict], **extra) -> dict:
    return {"nodes": nodes, "links": links, "samples": [], **extra}


def _both_moments(document: dict):
    """The validation result and the load outcome (the definition or the error)."""
    result = validate_structure(document, CONTRACTS)
    try:
        loaded = load_pipeline(document, contract_map=CONTRACTS, reference_outputs={})
    except ValueError as exc:
        loaded = exc
    return result, loaded


def _the_slot_error(result: dict) -> str:
    (error,) = [e for e in result["errors"] if "does not declare" in e]
    return error


@workflow(purpose="A link naming an output its source method does not declare, "
                  "or an input its target does not, is refused by structural "
                  "validation and by the load with one message naming the "
                  "node and the slot; a draft node is checked against its "
                  "draft's contract; a slotless link and a selector's source "
                  "end are not checked")
def test_a_link_to_an_undeclared_slot_is_refused_naming_node_and_slot():
    """Catalog case 4 (its link half, at the Graph level): one message names node and slot."""
    undeclared_output = _document(
        [_selector(), _method("prep_1", "prep"), _method("plot_1", "plot")],
        [_link("sel", "prep_1", "samples", "data"),
         _link("prep_1", "plot_1", "result", "data")],
    )
    result, refused = _both_moments(undeclared_output)
    assert result["valid"] is False
    error = _the_slot_error(result)
    assert "'prep_1'" in error and "'result'" in error and "output" in error
    assert isinstance(refused, ValueError)
    assert str(refused) == error

    undeclared_input = _document(
        [_selector(), _method("prep_1", "prep"), _method("plot_1", "plot")],
        [_link("sel", "prep_1", None, "data"),
         _link("prep_1", "plot_1", "out", "table")],
    )
    result, refused = _both_moments(undeclared_input)
    error = _the_slot_error(result)
    assert "'plot_1'" in error and "'table'" in error and "input" in error
    assert str(refused) == error

    # The draft node's links are read against the draft's own slots: the
    # newest version's `out` is refused on it, its draft's `cleaned` runs.
    draft_old_slots = _document(
        [_selector(), _method("prep_1", "prep", "draft"), _method("plot_1", "plot")],
        [_link("sel", "prep_1", None, "table"),
         _link("prep_1", "plot_1", "out", "data")],
    )
    result, refused = _both_moments(draft_old_slots)
    error = _the_slot_error(result)
    assert "'prep_1'" in error and "'out'" in error
    assert str(refused) == error

    draft_new_slots = _document(
        [_selector(), _method("prep_1", "prep", "draft"), _method("plot_1", "plot")],
        [_link("sel", "prep_1", None, "table"),
         _link("prep_1", "plot_1", "cleaned", "data")],
    )
    result, loaded = _both_moments(draft_new_slots)
    assert result["valid"] is True, result["errors"]
    assert not isinstance(loaded, ValueError)

    slotless = _document(
        [_selector(), _method("prep_1", "prep"), _method("plot_1", "plot")],
        [_link("sel", "prep_1", None, None), _link("prep_1", "plot_1", None, None)],
    )
    result, loaded = _both_moments(slotless)
    assert not [e for e in result["errors"] if "does not declare" in e]
    assert not isinstance(loaded, ValueError)


@pytest.mark.parametrize("param_sets", [["prep_1"], "fast", 3])
@workflow(purpose="A top-level param_sets that is not a mapping is refused by "
                  "name by the load, by substitution and by structural "
                  "validation, never an AttributeError")
def test_a_param_sets_that_is_not_a_mapping_is_refused_by_name(param_sets):
    document = _document(
        [_selector(), _method("prep_1", "prep")],
        [_link("sel", "prep_1", None, "data")],
        param_sets=param_sets,
    )
    with pytest.raises(ValueError, match="'param_sets' must be a mapping") as loaded:
        load_pipeline(document, contract_map=CONTRACTS, reference_outputs={})
    with pytest.raises(ValueError, match="'param_sets' must be a mapping") as resolved:
        resolve_variables(document)
    assert str(resolved.value) == str(loaded.value)
    result = validate_structure(document, CONTRACTS)
    assert str(loaded.value) in result["errors"]
