"""Contracts unit: the enrichment pass is a plain function over plain dicts.

Enrichment is the one place slot filenames are derived for a pipeline
document. ``wfc.contracts`` exposes ``enrich_pipeline(sparse_doc,
contract_map)``: the contract map is an argument Registration builds, and
the pass takes and returns plain dicts, so any composer can enrich a
document without a web server or a database session.

The witness hands the pass a hand-built map and a hand-built document and
asserts the literal canonical document: a directory slot and a compound
extension beside it, two modules sharing a method name, a node with no
module (emitted unenriched), a system node with its own fields, and links
with and without slot handles. Expectations are literal, never computed
with the derivation under test.

Witness: ``docs/system/contracts.json``, section ``catalog.enrichment``;
requirement "Agreement oracle: one slot-filename derivation".
"""
from __future__ import annotations

import ast
from pathlib import Path

from axiom_annotations import workflow

from wfc.contracts import enrich_pipeline

DIGEST = "a" * 64


@workflow(purpose="A sparse pipeline document enriched against a hand-built "
                  "contract map, with no session and no server, is the "
                  "literal canonical document: slot filenames from canonical "
                  "types, script path and env from the module-qualified "
                  "contract, the module-less node emitted unenriched, system "
                  "nodes and links carried through",
          inputs="A plain-dict document with a selector, two same-named "
                 "methods under two modules, and a module-less node, and a "
                 "contract map declaring a directory slot, a .tar.gz slot and "
                 "a csv slot",
          outputs="The literal canonical document")
def test_enrich_pipeline_is_a_plain_function_over_the_contract_map():
    contract_map = {
        "imaging.segment": {
            "output_slots": {"masks": {"type": "directory"},
                             "bundle": {"type": ".tar.gz"}},
            "script_path": "methods/segment/segment.py",
            "env": "image-io",
        },
        "stats.segment": {
            "output_slots": {"table": {"type": "csv"}},
            "script_path": "methods/segment/run.py",
            "env": "container:legacy-env",
        },
    }
    document = {
        "name": "diverse",
        "nodes": [
            {"id": "sel", "type": "input_selector", "method": "", "module": None,
             "params": {}, "samples": ["s1"], "source": None, "fan_mode": None},
            {"id": "a", "type": "method", "method": "segment", "module": "imaging",
             "params": {"k": 1}, "label": "A"},
            {"id": "b", "type": "method", "method": "segment", "module": "stats",
             "params": {}},
            {"id": "c", "type": "method", "method": "segment", "module": None,
             "params": {}},
        ],
        "links": [
            {"source": "sel", "target": "a", "sourceHandle": None, "targetHandle": "data"},
            {"source": "a", "target": "b", "sourceHandle": "bundle", "targetHandle": "data"},
        ],
        "samples": ["s1"],
    }

    assert enrich_pipeline(document, contract_map) == {
        "name": "diverse",
        "nodes": [
            {"id": "sel", "type": "input_selector", "method": "", "module": "",
             "params": {}, "samples": ["s1"], "source": "registered",
             "fan_mode": "out"},
            {"id": "a", "method": "segment", "module": "imaging",
             "script": "methods/segment/segment.py", "params": {"k": 1},
             "slot_outputs": {"masks": "masks", "bundle": "bundle.tar.gz"},
             "slot_types": {"masks": "directory", "bundle": ".tar.gz"},
             "env": "image-io", "label": "A"},
            {"id": "b", "method": "segment", "module": "stats",
             "script": "methods/segment/run.py", "params": {},
             "slot_outputs": {"table": "table.csv"},
             "slot_types": {"table": ".csv"},
             "env": "container:legacy-env"},
            {"id": "c", "method": "segment", "module": "",
             "script": "methods/segment/segment.py", "params": {},
             "slot_outputs": {}, "slot_types": {}, "env": None},
        ],
        "links": [
            {"source": "sel", "target": "a", "target_slot": "data"},
            {"source": "a", "target": "b", "source_slot": "bundle",
             "target_slot": "data"},
        ],
        "samples": ["s1"],
    }


def test_contracts_package_imports_nothing_from_wfc():
    """The unit's boundary, held by its own source: stdlib and the YAML
    parser only, no import from ``wfc`` (not even ``wfc.layout``)."""
    package_dir = Path(__file__).resolve().parents[1] / "wfc" / "contracts"
    offenders = []
    for source in sorted(package_dir.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports inside the package are its own modules;
                # a two-dot import would reach back into ``wfc``.
                names = [node.module or ""] if node.level == 0 else (
                    ["wfc"] if node.level >= 2 else []
                )
            else:
                continue
            offenders.extend(f"{source.name}: {n}" for n in names
                             if n == "wfc" or n.startswith("wfc."))
    assert offenders == []


def test_a_reference_node_passes_through_with_its_own_fields():
    """A run_reference node keeps its own fields and gains no method enrichment."""
    contract_map = {
        "imaging.segment": {
            "output_slots": {"masks": {"type": "directory"}},
            "script_path": "methods/segment/segment.py",
            "env": "image-io",
        },
    }
    document = {
        "name": "referenced",
        "nodes": [
            {"id": "ref", "type": "run_reference", "method": "", "module": None,
             "params": {}, "run_id": 412, "label": "Earlier run"},
            {"id": "a", "type": "method", "method": "segment", "module": "imaging",
             "params": {}},
        ],
        "links": [
            {"source": "ref", "target": "a", "sourceHandle": "masks",
             "targetHandle": "data"},
        ],
        "samples": [],
    }

    (reference,) = [n for n in enrich_pipeline(document, contract_map)["nodes"]
                    if n["id"] == "ref"]

    assert reference == {"id": "ref", "type": "run_reference", "method": "",
                         "module": "", "params": {}, "run_id": 412,
                         "label": "Earlier run"}
