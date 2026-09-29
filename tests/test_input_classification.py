"""Tier 1 tests: the pure per-slot input-source classifier.

``classify_input_sources`` is plain computation (pipeline document in,
classification out) — these tests call it with literal dicts and check the
returned classification, no fixtures or I/O.

The selector slot is a decision table over three axes: what else feeds the
node (nothing, a method parent, a reference), whether a selector edge feeds
it, and whether a pipeline document exists at all. Each row flips one
sub-condition on its own.
"""
from __future__ import annotations

import pytest

from wfc.execution.materialize import classify_input_sources
from wfc.graph import load_pipeline


def _pipeline(nodes, links):
    return {"nodes": nodes, "links": links, "param_sets": {}}


def _document(*, selector: bool, method_parent: bool, reference: bool,
              fan_mode: str = "out", slot: str | None = "raw") -> dict:
    """A document whose node ``n1`` is fed by the named kinds of input."""
    nodes = [{"id": "n1", "method": "m", "module": "mod", "env": "e"}]
    links: list[dict] = []
    if selector:
        nodes.append({"id": "sel1", "type": "input_selector",
                      "fan_mode": fan_mode, "samples": ["s1"]})
        link = {"source": "sel1", "target": "n1"}
        if slot is not None:
            link["target_slot"] = slot
        links.append(link)
    if method_parent:
        nodes.append({"id": "up", "method": "u", "module": "mod", "env": "e"})
        links.append({"source": "up", "target": "n1", "target_slot": "mask"})
        nodes.append({"id": "sel0", "type": "input_selector",
                      "samples": ["s1"]})
        links.append({"source": "sel0", "target": "up"})
    if reference:
        nodes.append({"id": "ref1", "type": "run_reference", "run_id": "7"})
        links.append({"source": "ref1", "target": "n1", "target_slot": "model"})
    return _pipeline(nodes, links)


#: (document kinds, parent entries, ref inputs) -> expected selector slot.
DECISION_TABLE = [
    pytest.param(dict(selector=True, method_parent=False, reference=False),
                 [], None, "raw", id="selector-only"),
    pytest.param(dict(selector=True, method_parent=True, reference=False),
                 ["mask:5"], None, "raw", id="selector-beside-method-parent"),
    pytest.param(dict(selector=True, method_parent=False, reference=True),
                 [], ["model=/r/m.pkl"], "raw", id="selector-beside-reference"),
    pytest.param(dict(selector=True, method_parent=True, reference=True),
                 ["mask:5"], ["model=/r/m.pkl"], "raw",
                 id="selector-beside-method-and-reference"),
    pytest.param(dict(selector=False, method_parent=True, reference=False),
                 ["mask:5"], None, None, id="method-parent-only"),
    pytest.param(dict(selector=False, method_parent=False, reference=True),
                 [], ["model=/r/m.pkl"], None, id="reference-only"),
    pytest.param(dict(selector=False, method_parent=False, reference=False),
                 [], None, None, id="document-without-selector-or-parents"),
    pytest.param(dict(selector=True, method_parent=False, reference=False,
                      fan_mode="in"),
                 [], None, "raw", id="fan-in-selector-feeds-its-slot"),
    pytest.param(dict(selector=True, method_parent=False, reference=False,
                      slot=None),
                 [], None, "data", id="unslotted-selector-link-feeds-data"),
]


@pytest.mark.parametrize("kinds, parents, refs, expected", DECISION_TABLE)
def test_selector_slot_follows_the_selector_edge_alone(kinds, parents, refs,
                                                       expected):
    """With a document, the selector edge decides the slot; parents and
    references neither grant it nor suppress it."""
    cls = classify_input_sources(_document(**kinds), "n1", parents, refs)
    assert cls["selector_slot"] == expected


@pytest.mark.parametrize("parents, expected", [
    pytest.param([], "data", id="no-document-no-parents"),
    pytest.param(["data:8"], None, id="no-document-with-parents"),
])
def test_missing_document_keeps_the_parentless_sample_fallback(parents,
                                                               expected):
    """With no document there is no edge to read: a parentless drive reads
    its sample into ``data`` and a drive with parents reads none."""
    assert classify_input_sources(None, "n1", parents, None)["selector_slot"] \
        == expected


def test_parent_entries_keep_their_slots_and_refs_parse_per_label():
    """Every parent entry keeps the input slot it names; ref-input entries
    classify per slot label and a malformed entry without ``=`` is skipped."""
    cls = classify_input_sources(
        _document(selector=True, method_parent=True, reference=True), "n1",
        parent_run_ids=["sources:5", "data:8"],
        ref_inputs=["model=/tmp/ref.csv", "malformed-entry"],
    )
    assert [(e.input_slot, e.source_slot, e.run_id)
            for e in cls["parents"]] == [("sources", None, 5), ("data", None, 8)]
    assert cls["references"] == [("model", "/tmp/ref.csv")]


def test_the_load_stamps_the_classifier_answer_on_each_step():
    """The step the emitter reads carries the classifier's own answer, so
    the phase that fills the slot, the phase that keys on it and the
    emitted rule's restore dependency cannot disagree."""
    document = _document(selector=True, method_parent=True, reference=True)
    pipeline = load_pipeline(
        document, contract_map={},
        reference_outputs={"ref1": {"output_paths": {"out": "/r/m.pkl"},
                                    "sample": "s1"}},
    )
    for step in pipeline.steps:
        assert step.selector_slot == classify_input_sources(
            document, step.node_id, [], None)["selector_slot"], step.node_id
