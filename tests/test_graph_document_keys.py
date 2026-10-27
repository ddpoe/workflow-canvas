"""Graph refuses the keys a pipeline document carries that wfc does not read."""

from __future__ import annotations

from wfc.graph import document_key_refusals


def test_unknown_node_and_top_level_keys_are_refused_by_name():
    """`script`, a misspelt key and an unknown top-level key are each named.

    A misspelt key's refusal lists the keys allowed where it sits.
    """
    document = {
        "nodes": [
            {"id": "sel", "type": "input_selector", "samples": ["s1"]},
            {"id": "a", "module": "m", "method": "f", "params": {},
             "script": "f.py", "parmas": {}},
        ],
        "links": [{"source": "sel", "target": "a"}],
        "samples": [],
        "pipline_id": "x",
    }
    refusals = document_key_refusals(document)
    assert refusals[0].startswith("Pipeline: unknown top-level key 'pipline_id'")
    assert "param_sets" in refusals[0] and "variables" in refusals[0]
    assert refusals[1].startswith("Node 'a': unknown key 'script'")
    assert refusals[2].startswith("Node 'a': unknown key 'parmas'")
    assert "params" in refusals[2] and "fan_mode" in refusals[2]
    assert len(refusals) == 3


def test_the_enriched_frozen_form_and_a_pin_are_accepted():
    """What enrichment writes and the version pin are known node keys."""
    document = {
        "name": "p", "variables": {}, "keep_going": False,
        "nodes": [{"id": "a", "type": "method", "module": "m", "method": "f",
                   "params": {"k": 1}, "version": "abc", "env": "e",
                   "slot_outputs": {"data": "data.csv"},
                   "slot_types": {"data": ".csv"}, "position": {"x": 0}}],
        "links": [], "samples": ["s1"], "param_sets": None,
        "explicit_combos": None,
    }
    assert document_key_refusals(document) == []
