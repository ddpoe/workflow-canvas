"""Contracts unit: the enrichment pass is a plain function over plain dicts.

Enrichment is the one place slot filenames are derived for a pipeline
document. ``wfc.contracts`` exposes ``enrich_pipeline(sparse_doc,
contract_map)``: the contract map is an argument Registration builds, and
the pass takes and returns plain dicts, so any composer can enrich a
document without a web server or a database session.

The witness hands the pass a hand-built map and a hand-built document and
asserts the literal canonical document: a directory slot and a compound
extension beside it, two modules sharing a method name, a node pinned to
an older version (that version's slots and env), a system node with its
own fields, and links with and without slot handles. A node whose
``module.method`` the map does not hold is refused by name. Expectations are literal, never computed
with the derivation under test.

Witness: ``docs/system/contracts.json``, section ``catalog.enrichment``;
requirement "Agreement oracle: one slot-filename derivation".
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.contracts import (
    MethodNotFoundError,
    enrich_pipeline,
    normalize_document_params,
)

DIGEST = "a" * 64


@workflow(purpose="A sparse pipeline document enriched against a hand-built "
                  "contract map, with no session and no server, is the "
                  "literal canonical document: slot filenames and slot types "
                  "from canonical types, the env from the module-qualified "
                  "contract and no script, each node's version (its pin, "
                  "else the newest) with that version's slots, system "
                  "nodes and links carried through. The "
                  "written env is for display: execution reads the method's "
                  "registered env, never the document's",
          inputs="A plain-dict document with a selector, two same-named "
                 "methods under two modules, one pinned to an older version, "
                 "and a contract map declaring a directory slot, a .tar.gz "
                 "slot and a csv slot",
          outputs="The literal canonical document")
def test_enrich_pipeline_is_a_plain_function_over_the_contract_map():
    contract_map = {
        "imaging.segment": {
            "output_slots": {"masks": {"type": "directory"},
                             "bundle": {"type": ".tar.gz"}},
            "env": "image-io",
            "version": "img2",
            "versions": {"img2": {"output_slots": {"masks": {"type": "directory"},
                                                  "bundle": {"type": ".tar.gz"}},
                                 "env": "image-io", "label": 2}},
        },
        "stats.segment": {
            "output_slots": {"table": {"type": "csv"}},
            "env": "container:legacy-env",
            "version": "st2",
            "versions": {
                "st1": {"output_slots": {"old": {"type": "tsv"}},
                        "env": "old-env", "label": 1},
                "st2": {"output_slots": {"table": {"type": "csv"}},
                        "env": "container:legacy-env", "label": 2},
            },
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
            {"id": "c", "type": "method", "method": "segment", "module": "stats",
             "params": {}, "version": "st1"},
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
             "params": {"k": 1},
             "slot_outputs": {"masks": "masks", "bundle": "bundle.tar.gz"},
             "slot_types": {"masks": "directory", "bundle": ".tar.gz"},
             "env": "image-io", "version": "img2", "label": "A"},
            {"id": "b", "method": "segment", "module": "stats",
             "params": {},
             "slot_outputs": {"table": "table.csv"},
             "slot_types": {"table": ".csv"},
             "env": "container:legacy-env", "version": "st2"},
            {"id": "c", "method": "segment", "module": "stats",
             "params": {},
             "slot_outputs": {"old": "old.tsv"}, "slot_types": {"old": ".tsv"},
             "env": "old-env", "version": "st1"},
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


@pytest.mark.parametrize("node", [
    {"id": "m", "type": "method", "method": "segment", "module": "nowhere",
     "params": {}},
    {"id": "m", "type": "method", "method": "segment", "module": "nowhere",
     "params": {}, "env": "image-io"},
    {"id": "m", "type": "method", "method": "segment", "module": None,
     "params": {}, "env": "image-io"},
], ids=["no-env", "own-env", "no-module"])
def test_a_node_whose_method_the_map_does_not_hold_is_refused_by_name(node):
    """No unenriched node is emitted, even when it carries its own env."""
    contract_map = {"imaging.segment": {"output_slots": {}, "env": "image-io",
                                        "version": "img2", "versions": {}}}

    with pytest.raises(MethodNotFoundError) as refused:
        enrich_pipeline({"nodes": [node], "links": [], "samples": []}, contract_map)

    assert "Node 'm'" in str(refused.value)
    assert f"'{node['module'] or ''}.segment'" in str(refused.value)


def test_param_sets_variants_are_normalized_by_their_nodes_version():
    """A variant's int ``1.0`` becomes ``1`` and its float ``1`` becomes ``1.0``,
    by the declared params of the version its node runs; a value the rule
    refuses is kept as written; the input is unchanged; a second pass gives
    an equal document."""
    import copy
    import json

    params = {"count": {"type": "int", "constraints": {"max": 10}},
              "ratio": {"type": "float"}}
    contract_map = {"stats.fit": {"params": params, "version": "f1",
                                  "versions": {"f1": {"params": params,
                                                      "label": 1}}}}
    document = {
        "nodes": [{"id": "fit", "type": "method", "module": "stats",
                   "method": "fit", "version": "f1",
                   "params": {"count": 2, "ratio": 0.5}}],
        "links": [], "samples": ["S1"],
        "param_sets": {"fit": {
            "small": {"count": 1.0, "ratio": 1},
            "large": {"count": 11.0, "ratio": 2.5},
        }},
    }
    before = copy.deepcopy(document)

    normalized = normalize_document_params(document, contract_map)

    small = normalized["param_sets"]["fit"]["small"]
    large = normalized["param_sets"]["fit"]["large"]
    assert small == {"count": 1, "ratio": 1.0}
    assert type(small["count"]) is int and type(small["ratio"]) is float
    assert type(large["count"]) is float  # 11.0 breaks max: kept as written
    assert large == {"count": 11.0, "ratio": 2.5}
    assert json.dumps(document) == json.dumps(before)
    again = normalize_document_params(normalized, contract_map)
    assert json.dumps(again) == json.dumps(normalized)
