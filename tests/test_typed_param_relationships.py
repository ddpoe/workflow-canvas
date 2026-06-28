"""Behavior tests for typed-param-relationships (ADR-015).

Track 1 (column_of_input) and Track 2 (Pipeline Variables, server-side
substitution + editable sidecar). These are subsystem-level behavior
tests, not regression locks.
"""
from __future__ import annotations

import pytest

from wfc.canvas.wfc_provider import resolve_variables, UnknownVariableError
from wfc.contracts import resolve_columns, parse_method_yaml


# ── US-3: Variable substitution ──────────────────────────────────────────


def test_resolve_variables_substitutes_var_refs_in_node_params():
    """US-3: post-substitution dict has literals; no $var keys remain."""
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
    """US-3: unknown variable → UnknownVariableError carrying the name."""
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
    """US-3: literal pipeline and var-refs resolving to same value produce
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
    """Edge case 9: a variable's value cannot itself be a $var ref."""
    pipeline = {
        "nodes": [{"id": "n1", "method": "m", "params": {"x": {"$var": "a"}}}],
        "variables": {"a": {"value": {"$var": "b"}}, "b": {"value": "literal"}},
    }
    with pytest.raises(ValueError):
        resolve_variables(pipeline)


# ── US-1: column_of_input resolution against contracts ──────────────────


def test_resolve_columns_strict_plus_from_params_union():
    """Track 1 reuses ADR-005's resolve_columns. Verify the union semantics."""
    spec = {
        "strict": ["a", "b"],
        "from_params": [
            {"params": ["chmap"], "pattern": "R1_{}"},
        ],
    }
    out = resolve_columns(spec, {"chmap": ["p27", "CycD1"]})
    assert out == {"a", "b", "R1_p27", "R1_CycD1"}


# ── US-5: parse_method_yaml producer side reuses ADR-005 vocab unchanged ─


def test_parse_method_yaml_passes_through_columns_unchanged(tmp_path):
    """Producer side: declaring outputs.<slot>.columns.strict is a
    no-modification pass-through. resolve_columns consumes it directly."""
    method_dir = tmp_path / "my_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        # ADR-019 Cycle H: every method.yaml must name a built container env.
        "env: image-io\n"
        "inputs:\n"
        "  data:\n"
        "    type: .csv\n"
        "outputs:\n"
        "  measurements:\n"
        "    type: .csv\n"
        "    columns:\n"
        "      strict: [a, b, c]\n"
        "params: {}\n",
        encoding="utf-8",
    )
    spec = parse_method_yaml(method_dir)
    cols = spec["outputs"]["measurements"]["columns"]
    assert cols == {"strict": ["a", "b", "c"]}
    assert resolve_columns(cols, {}) == {"a", "b", "c"}
