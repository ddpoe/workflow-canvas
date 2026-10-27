"""Graph answers the wiring question: ``inbound_wiring`` over a literal document.

The per-node inbound wiring (target slot, source id, source kind,
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
            "and the same wiring in a document that writes its ids as numbers"
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

    # A document that writes its ids as numbers: each node's identity is its
    # id read as a string, so the consumer is named '2' and its source comes
    # back as '1', the id the executor keys the source's sidecars by.
    numeric = {
        "nodes": [
            {"id": 1, "method": "m_a", "module": "mod"},
            {"id": 2, "method": "m_c", "module": "mod"},
        ],
        "links": [{"source": 1, "target": 2, "target_slot": "data", "source_slot": "out"}],
    }
    (wire,) = inbound_wiring(numeric, "2")
    assert (wire.source_id, wire.raw_source_id, wire.target_slot, wire.source_slot) == (
        "1", "1", "data", "out",
    )


def test_document_node_finds_a_node_by_its_id():
    # A node answers to its own id, as a string even when the document wrote
    # it as a number. The returned entry is the document's own.
    numeric = {
        "nodes": [
            {"id": 1, "type": "input_selector"},
            {"id": 2, "method": "m_c", "module": "mod"},
        ],
    }
    assert document_node(numeric, "2") is numeric["nodes"][1]
    assert document_node(numeric, "1") is numeric["nodes"][0]

    # A node whose id equals another node's method name: the id names the
    # node that carries it.
    shadowed = {
        "nodes": [
            {"id": "a", "method": "b", "module": "mod"},
            {"id": "b", "method": "m_b", "module": "mod"},
        ],
    }
    assert document_node(shadowed, "b") is shadowed["nodes"][1]

    # Nothing answers: an unknown id, and a document with no nodes.
    assert document_node(numeric, "m_missing") is None
    assert document_node({}, "2") is None
