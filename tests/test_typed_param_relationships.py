"""Behavior tests for typed-param-relationships.

Pipeline Variables (server-side substitution + editable sidecar) and the
options declarations a parameter's ``options_from`` reads. These are
subsystem-level behavior tests, not snapshots of current output.
"""
from __future__ import annotations

import pytest

from wfc.graph import resolve_variables, UnknownVariableError
from wfc.contracts import resolve_options, parse_method_yaml


# ── Variable substitution ──────────────────────────────────────────


def test_resolve_variables_substitutes_var_refs_in_node_params():
    """Post-substitution dict has literals; no $var keys remain."""
    pipeline = {
        "nodes": [
            {"id": "n1", "method": "m", "params": {"label_col": {"$var": "lab"}}},
        ],
        "variables": {"lab": {"type": "str", "value": "label"}},
    }
    out = resolve_variables(pipeline)
    assert out["nodes"][0]["params"]["label_col"] == "label"
    assert "variables" not in out


def test_resolve_variables_substitutes_in_param_sets():
    """Variant dicts inside param_sets are also substituted (one-pass)."""
    pipeline = {
        "nodes": [{"id": "n1", "method": "m", "params": {}}],
        "param_sets": {
            "n1": {"v1": {"label_col": {"$var": "lab"}}},
        },
        "variables": {"lab": {"type": "str", "value": "label"}},
    }
    out = resolve_variables(pipeline)
    assert out["param_sets"]["n1"]["v1"]["label_col"] == "label"


def test_resolve_variables_unknown_name_raises():
    """Unknown variable → UnknownVariableError carrying the name."""
    pipeline = {
        "nodes": [{"id": "n1", "method": "m", "params": {"x": {"$var": "missing"}}}],
        "variables": {},
    }
    with pytest.raises(UnknownVariableError) as exc_info:
        resolve_variables(pipeline)
    assert exc_info.value.name == "missing"


def test_resolve_variables_dict_whole_value_splice():
    """Dict-typed variables splice as whole values, not partial merges."""
    pipeline = {
        "nodes": [{"id": "n1", "method": "m", "params": {"chmap": {"$var": "cm"}}}],
        "variables": {"cm": {"type": "dict", "value": {"p27": "R1_p27", "CycD1": "R1_CycD1"}}},
    }
    out = resolve_variables(pipeline)
    assert out["nodes"][0]["params"]["chmap"] == {"p27": "R1_p27", "CycD1": "R1_CycD1"}


def test_resolve_variables_cache_equiv_to_literal():
    """Literal pipeline and var-refs resolving to same value produce
    identical post-substitution dicts (so cache keys hash identically)."""
    literal = {
        "nodes": [{"id": "n1", "method": "m", "params": {"x": "label"}}],
    }
    via_var = {
        "nodes": [{"id": "n1", "method": "m", "params": {"x": {"$var": "lab"}}}],
        "variables": {"lab": {"value": "label"}},
    }
    assert resolve_variables(literal)["nodes"][0]["params"] == \
           resolve_variables(via_var)["nodes"][0]["params"]


def test_resolve_variables_rejects_nested_var_refs():
    """A variable's value cannot itself be a $var ref."""
    pipeline = {
        "nodes": [{"id": "n1", "method": "m", "params": {"x": {"$var": "a"}}}],
        "variables": {"a": {"value": {"$var": "b"}}, "b": {"value": "literal"}},
    }
    with pytest.raises(ValueError):
        resolve_variables(pipeline)


# ── parse_method_yaml producer side reuses the shared vocab unchanged ─


def test_parse_method_yaml_passes_through_options_unchanged(tmp_path):
    """Producer side: declaring outputs.<slot>.options is a no-modification
    pass-through, and a parameter's options_from is kept as written;
    resolve_options consumes the stored block directly."""
    method_dir = tmp_path / "my_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        # Every method.yaml must name a built container env.
        "env: image-io\n"
        "inputs:\n"
        "  data:\n"
        "    type: .csv\n"
        "outputs:\n"
        "  measurements:\n"
        "    type: .csv\n"
        "    options:\n"
        "      fixed: [a, b, c]\n"
        "      from_params:\n"
        "        - params: [chmap]\n"
        "          pattern: 'R1_{}'\n"
        "params:\n"
        "  group_by:\n"
        "    type: str\n"
        "    options_from: data\n"
        "  chmap:\n"
        "    type: list\n",
        encoding="utf-8",
    )
    spec = parse_method_yaml(method_dir)
    options = spec["outputs"]["measurements"]["options"]
    assert options == {"fixed": ["a", "b", "c"],
                       "from_params": [{"params": ["chmap"], "pattern": "R1_{}"}]}
    assert spec["params"]["group_by"]["options_from"] == "data"
    assert resolve_options(options, [{"chmap": ["p27", "CycD1"]}])["all"] == [
        "a", "b", "c", "R1_p27", "R1_CycD1"]
