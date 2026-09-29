"""Graph answers the wiring question: ``inbound_wiring`` over a literal document.

The per-node inbound wiring (target slot, canonical source id, source kind,
source slot, referenced run id) that the claim and materialize phases
consume — answered once, pure, from the document alone.
"""

from axiom_annotations import workflow

from wfc.graph import InboundLink, document_node, inbound_wiring


@workflow(
    purpose="From a literal document with no run and no database, the accessor "
            "returns the per-slot wiring claim and materialize consume: a "
            "two-parent fan-in with source slots, a reference-only root (run id "
            "reported, an empty run id reported not decided), a selector root, "
            "and the same wiring in a legacy numeric-id document"
)
def test_inbound_wiring_answers_the_document_topology():
    # Two-parent fan-in into one slot: both links into the consumer's 'sources'
    # slot come back in document order with their source slots and method kind.
    fan_in = {
        "nodes": [
            {"id": "a", "method": "m_a", "module": "mod"},
            {"id": "b", "method": "m_b", "module": "mod"},
            {"id": "c", "method": "m_c", "module": "mod"},
        ],
        "links": [
            {"source": "a", "target": "c", "target_slot": "sources", "source_slot": "out_a"},
            {"source": "b", "target": "c", "target_slot": "sources", "source_slot": "out_b"},
        ],
    }
    wires = inbound_wiring(fan_in, "c")
    assert [(w.source_id, w.target_slot, w.source_slot, w.source_kind) for w in wires] == [
        ("a", "sources", "out_a", "method"),
        ("b", "sources", "out_b", "method"),
    ]
    assert all(isinstance(w, InboundLink) and w.run_id == "" for w in wires)
    assert wires[0].source_node is fan_in["nodes"][0]
    assert inbound_wiring(fan_in, "a") == []

    # Reference-only root: a run_reference source reports its kind and the run
    # id it names; a reference naming no run is reported with an empty run id,
    # not dropped -- the executor decides what to do.
    referenced = {
        "nodes": [
            {"id": "ref", "type": "run_reference", "run_id": " 7 "},
            {"id": "bare", "type": "run_reference", "run_id": ""},
            {"id": "c", "method": "m_c", "module": "mod"},
        ],
        "links": [
            {"source": "ref", "target": "c", "target_slot": "data"},
            {"source": "bare", "target": "c", "target_slot": "extra"},
        ],
    }
    wires = inbound_wiring(referenced, "c")
    assert [(w.source_kind, w.target_slot, w.run_id) for w in wires] == [
        ("run_reference", "data", "7"),
        ("run_reference", "extra", ""),
    ]

    # Selector root: an input_selector source reports its kind; a link naming
    # no target slot defaults to 'data'.
    selected = {
        "nodes": [
            {"id": "sel", "type": "input_selector"},
            {"id": "c", "method": "m_c", "module": "mod"},
        ],
        "links": [{"source": "sel", "target": "c"}],
    }
    (wire,) = inbound_wiring(selected, "c")
    assert (wire.source_kind, wire.target_slot, wire.source_id) == ("input_selector", "data", "sel")

    # Legacy numeric-id document: a numeric source id canonicalises to its
    # method name (the executor keys sidecars by it) while the raw id is kept;
    # the consumer is found by method name, by canonical id or by raw id; a
    # system source with no method keeps its raw id.
    legacy = {
        "nodes": [
            {"id": 1, "method": "m_a", "module": "mod"},
            {"id": 2, "method": "m_c", "module": "mod"},
        ],
        "links": [{"source": 1, "target": 2, "target_slot": "data", "source_slot": "out"}],
    }
    by_method = inbound_wiring(legacy, "m_c")
    assert [(w.source_id, w.raw_source_id, w.target_slot, w.source_slot) for w in by_method] == [
        ("m_a", "1", "data", "out"),
    ]
    assert inbound_wiring(legacy, "2") == by_method
    assert inbound_wiring(legacy, "m_a") == []

    # A legacy system source with no method key has no name to canonicalise
    # to: the link comes back with the source's raw id, and the walk does not
    # raise.
    legacy_selector = {
        "nodes": [
            {"id": 1, "type": "input_selector"},
            {"id": 2, "method": "m_c", "module": "mod"},
        ],
        "links": [{"source": 1, "target": 2}],
    }
    (wire,) = inbound_wiring(legacy_selector, "m_c")
    assert (wire.source_id, wire.raw_source_id, wire.source_kind, wire.target_slot) == (
        "1", "1", "input_selector", "data",
    )


def test_document_node_finds_a_node_by_its_id_else_its_legacy_method_name():
    # A node answers to its own id, as a string even when the document wrote
    # it as a number; a legacy numeric-id document's method node also answers
    # to its method name. The returned entry carries the raw id.
    legacy = {
        "nodes": [
            {"id": 1, "type": "input_selector"},
            {"id": 2, "method": "m_c", "module": "mod"},
        ],
    }
    assert document_node(legacy, "2") is legacy["nodes"][1]
    assert document_node(legacy, "m_c") is legacy["nodes"][1]
    assert document_node(legacy, "1") is legacy["nodes"][0]

    # An id match wins over a method-name match: a node whose id equals
    # another node's method name is the one returned.
    shadowed = {
        "nodes": [
            {"id": "a", "method": "b", "module": "mod"},
            {"id": "b", "method": "m_b", "module": "mod"},
        ],
    }
    assert document_node(shadowed, "b") is shadowed["nodes"][1]

    # Nothing answers: an unknown name, and a document with no nodes.
    assert document_node(legacy, "m_missing") is None
    assert document_node({}, "2") is None
