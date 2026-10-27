"""Contracts' parameter check and fill rule.

The check names node, parameter and version for each refusal. A parameter a
node leaves out runs at its version's declared (non-null) default: the fill
adds it, a required parameter with a usable default needs no value, and the
schema check lists each declared default that breaks the value rule.
"""

from __future__ import annotations

from axiom_annotations import workflow

from wfc.contracts import (
    declared_default_refusals,
    fill_declared_defaults,
    fill_document_defaults,
    normalize_param_value,
    param_refusals,
)

_SCHEMA = {
    "alpha": {"type": "int", "required": True, "constraints": {"min": 0, "max": 10}},
    "mode": {"type": "str", "default": "mean",
             "constraints": {"enum": ["mean", "median"]}},
    "scale": {"type": "float"},
}


def test_each_kind_of_refusal_names_node_parameter_and_version():
    """Undeclared, wrong type and constraint refusals; a missing required one."""
    refusals = param_refusals("n1", "mod.meth", "v2",
                              {"beta": 1, "mode": "mode", "scale": True}, _SCHEMA)
    assert len(refusals) == 4
    assert all(r.startswith("Node 'n1' (mod.meth v2): ") for r in refusals)
    text = "\n".join(refusals)
    assert "parameter 'beta' is not declared by v2" in text
    assert "required parameter 'alpha' (int) is missing" in text
    assert "parameter 'mode' must be one of \"mean\", \"median\", got \"mode\"" in text
    assert "parameter 'scale' expects float, got true (bool)" in text


@workflow(purpose="An omitted optional parameter is accepted and runs at its "
                  "declared default, while a written int value is still given "
                  "its declared type's form")
def test_an_omitted_optional_parameter_is_accepted_and_filled_with_its_default():
    params = {"alpha": 3.0}
    assert param_refusals("n1", "mod.meth", "v1", params, _SCHEMA) == []
    normalized = {"alpha": normalize_param_value("int", params["alpha"])}
    filled = fill_declared_defaults(normalized, _SCHEMA)
    assert filled == {"alpha": 3, "mode": "mean"}
    assert type(filled["alpha"]) is int
    assert params == {"alpha": 3.0}, "the rule returns a new dict"


def test_written_values_are_kept_whatever_they_are():
    """``{}``, ``[]``, ``""`` and ``null`` written in the document are never replaced."""
    schema = {
        "opts": {"type": "dict", "default": {"k": 1}},
        "items": {"type": "list", "default": [1, 2]},
        "label": {"type": "str", "default": "x"},
        "level": {"type": "int", "default": 4},
    }
    written = {"opts": {}, "items": [], "label": "", "level": None}
    assert fill_declared_defaults(written, schema) == written


def test_a_declared_null_default_is_not_filled():
    """``default: null`` declares no default: the key stays out."""
    schema = {"seed": {"type": "int", "default": None}, "k": {"type": "int"}}
    assert fill_declared_defaults({}, schema) == {}
    assert fill_declared_defaults(None, schema) == {}


def test_an_optional_empty_string_default_is_filled():
    """An optional ``str`` parameter's ``""`` default is a value and is filled."""
    schema = {"suffix": {"type": "str", "default": ""}}
    assert fill_declared_defaults({}, schema) == {"suffix": ""}


def test_filling_a_filled_dict_changes_nothing():
    """The rule is idempotent, and a filled list default is not the contract's object."""
    schema = {"items": {"type": "list", "default": [1, 2]},
              "mode": {"type": "str", "default": "mean"}}
    once = fill_declared_defaults({}, schema)
    assert fill_declared_defaults(once, schema) == once
    once["items"].append(3)
    assert schema["items"]["default"] == [1, 2]


def test_a_required_parameter_with_a_usable_default_needs_no_value():
    """Left out, it is accepted and filled; written, it is checked as before."""
    schema = {"k": {"type": "int", "required": True, "default": 5}}
    assert param_refusals("n1", "mod.meth", "v1", {}, schema) == []
    assert fill_declared_defaults({}, schema) == {"k": 5}
    assert param_refusals("n1", "mod.meth", "v1", {"k": None}, schema) == [
        "Node 'n1' (mod.meth v1): parameter 'k' is required and cannot be "
        "empty (got null)"]


def test_a_required_parameter_with_a_null_or_empty_default_still_needs_a_value():
    """A null default is no default, and a required parameter cannot be ``""``."""
    schema = {"a": {"type": "int", "required": True, "default": None},
              "b": {"type": "str", "required": True, "default": ""}}
    assert param_refusals("n1", "mod.meth", "v1", {}, schema) == [
        "Node 'n1' (mod.meth v1): required parameter 'a' (int) is missing",
        "Node 'n1' (mod.meth v1): required parameter 'b' (str) is missing",
    ]
    assert fill_declared_defaults({}, schema) == {}


def test_the_schema_check_lists_each_bad_default_by_parameter():
    """Type, enum and min/max breaches are listed; a required ``""`` default is not."""
    schema = {
        "count": {"type": "int", "default": "three"},
        "mode": {"type": "str", "default": "slow",
                 "constraints": {"enum": ["fast"]}},
        "ratio": {"type": "float", "default": -1.0, "constraints": {"min": 0}},
        "name": {"type": "str", "required": True, "default": ""},
        "seed": {"type": "int", "default": None},
        "ok": {"type": "int", "default": 2},
        "bare": "int",
    }
    assert declared_default_refusals(schema) == [
        "parameter 'count' declares a default that expects int, got \"three\" (str)",
        "parameter 'mode' declares a default that must be one of \"fast\", got \"slow\"",
        "parameter 'ratio' declares a default that must be >= 0, got -1.0",
    ]
    assert declared_default_refusals(None) == []


def _contract_map() -> dict:
    """A method with two versions: v1 declares ``k``, v2 adds ``m``."""
    v1 = {"version": "v1", "params": {"k": {"type": "int", "default": 1}}}
    v2 = {"version": "v2", "params": {"k": {"type": "int", "default": 2},
                                      "m": {"type": "str", "default": "z"}}}
    return {"mod.meth": {**v2, "versions": {"v1": v1, "v2": v2}},
            "mod.bare": {"version": "v1", "params": {},
                         "versions": {"v1": {"params": {}}}}}


@workflow(purpose="A document's method nodes, with or without a params key, "
                  "and each param_sets variant are filled by the version each "
                  "node runs, each variant on its own; system nodes and nodes "
                  "whose version declares no params pass through")
def test_each_node_and_variant_is_filled_by_the_version_it_runs():
    document = {
        "nodes": [
            {"id": "sel", "type": "input_selector", "params": {}},
            {"id": "new", "module": "mod", "method": "meth", "version": "v2"},
            {"id": "old", "module": "mod", "method": "meth", "version": "v1",
             "params": {"k": 7}},
            {"id": "bare", "module": "mod", "method": "bare", "version": "v1",
             "params": {"x": 1}},
        ],
        "param_sets": {"new": {"a": {"k": 5}, "b": {"m": "y"}},
                       "bare": {"a": {"x": 2}}},
    }
    filled = fill_document_defaults(document, _contract_map())
    by_id = {n["id"]: n for n in filled["nodes"]}
    assert by_id["sel"] == {"id": "sel", "type": "input_selector", "params": {}}
    assert by_id["new"]["params"] == {"k": 2, "m": "z"}
    assert by_id["old"]["params"] == {"k": 7}
    assert by_id["bare"] == document["nodes"][3]
    assert filled["param_sets"] == {"new": {"a": {"k": 5, "m": "z"},
                                            "b": {"m": "y", "k": 2}},
                                    "bare": {"a": {"x": 2}}}
    assert "params" not in document["nodes"][1], "the input is not changed"
    assert fill_document_defaults(filled, _contract_map()) == filled
