"""
Workflow Test: Node-ID-Based Pipeline Identity

Validates that pipelines with duplicate method names (same method used
by multiple nodes) correctly produce distinct node_ids, topo-sort
independently, and generate separate Snakemake rules.

Scenario: Two parallel branches — each uses ``csv_filter`` → ``feature_qc``,
but the four nodes have unique string IDs: ``filter_rep2``, ``filter_rep3``,
``qc_rep2``, ``qc_rep3``.

This exercises the ``has_duplicate_methods=True`` code path in
``load_pipeline()``; the other fixture pipeline JSONs have unique method
names per node.
"""

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.graph import topo_sort_steps, expand_step_combos
from wfc.execution import load_pipeline_from_path
from wfc.orchestration import generate_snakefile


# =============================================================================
# Fixtures
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def dup_pipeline_path(tmp_path):
    """Copy the duplicate-methods pipeline JSON into a temp directory."""
    src = PROJECT_ROOT / "tests" / "fixtures" / "pipelines" / "pipeline_duplicate_methods.json"
    dst = tmp_path / "pipeline_duplicate_methods.json"
    dst.write_text(src.read_text())
    return dst


# =============================================================================
# Test 1: Engine handles duplicate methods end-to-end
# =============================================================================

@workflow(
    purpose="Verify load_pipeline, topo_sort, and expand_step_combos all use "
            "node_id (not method_name) when methods repeat across nodes")
def test_engine_duplicate_methods(dup_pipeline_path, wfc_root):
    """Load → sort → expand a pipeline where csv_filter and feature_qc each
    appear twice.  Every stage should key on node_id, not method_name."""

    # -- Load (through the composer, so it needs a reachable database;
    # wfc_root's is empty, which is not a failure) --
    pipeline = load_pipeline_from_path(dup_pipeline_path)
    assert len(pipeline.steps) == 4

    node_ids = [s.node_id for s in pipeline.steps]
    assert set(node_ids) == {"filter_rep2", "filter_rep3", "qc_rep2", "qc_rep3"}

    # method_name still holds the real method (for script lookup / DB)
    methods = {s.node_id: s.method_name for s in pipeline.steps}
    assert methods["filter_rep2"] == "csv_filter"
    assert methods["qc_rep3"] == "feature_qc"

    # depends_on references node_ids, not method names
    deps = {s.node_id: s.depends_on for s in pipeline.steps}
    assert deps["filter_rep2"] == []
    assert deps["qc_rep2"] == ["filter_rep2"]
    assert deps["qc_rep3"] == ["filter_rep3"]

    # -- Topo sort --
    ordered = topo_sort_steps(pipeline.steps)
    ordered_ids = [s.node_id for s in ordered]
    assert len(ordered_ids) == 4
    assert ordered_ids.index("filter_rep2") < ordered_ids.index("qc_rep2")
    assert ordered_ids.index("filter_rep3") < ordered_ids.index("qc_rep3")

    # -- Expand per-step rows --
    resolved_params: dict[str, dict[str, dict]] = {}
    for step in ordered:
        resolved_params[step.node_id] = pipeline.param_sets.get(
            step.node_id,
            pipeline.param_sets.get(step.method_name, {"default": step.params}))

    rows = expand_step_combos(ordered, pipeline.samples, resolved_params, None)
    assert len(rows) >= 1
    step, combo = rows[0]
    assert step is ordered[0]
    # Unified scheme: keys are "sample" and "variant", not per-node keys
    assert "sample" in combo and "variant" in combo
    assert "csv_filter" not in combo and "feature_qc" not in combo


# =============================================================================
# Test 2: Snakefile generation with duplicate methods
# =============================================================================

@workflow(
    purpose="Verify generate_snakefile emits one rule per node_id with correct "
            "sentinel paths and input→output wiring between branches")
def test_snakefile_duplicate_methods(dup_pipeline_path, wfc_root):
    """Snakefile should have four distinct rules — not two collapsed by method name."""

    pipeline = load_pipeline_from_path(dup_pipeline_path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Four rules by node_id, zero by method name
    for nid in ("filter_rep2", "filter_rep3", "qc_rep2", "qc_rep3"):
        assert f"rule {nid}:" in snakefile
        # Snakemake-visible outputs are sentinels.
        assert f".runs/sentinels/test-pid/{nid}/" in snakefile
    assert "rule csv_filter:" not in snakefile
    assert "rule feature_qc:" not in snakefile

    # Each qc rule reads from its own filter, not the other branch
    rules = snakefile.split("rule ")
    qc_rep2_rule = next(r for r in rules if r.startswith("qc_rep2:"))
    qc_rep3_rule = next(r for r in rules if r.startswith("qc_rep3:"))
    assert "filter_rep2" in qc_rep2_rule and "filter_rep3" not in qc_rep2_rule
    assert "filter_rep3" in qc_rep3_rule and "filter_rep2" not in qc_rep3_rule

    # Python preamble is syntactically valid
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


def test_legacy_integer_ids_with_unique_methods_key_steps_on_the_method_name():
    """Integer-string ids and unique methods: each node id is its method name."""
    from wfc.graph import load_pipeline

    document = {
        "nodes": [
            {"id": "1", "type": "method", "env": "demo", "method": "load",
             "module": "m", "params": {}},
            {"id": "2", "type": "method", "env": "demo", "method": "filter",
             "module": "m", "params": {}},
        ],
        "links": [{"source": "1", "target": "2"}],
        "samples": ["s1"],
    }

    pipeline = load_pipeline(document, contract_map={}, reference_outputs={})

    steps = {s.method_name: s for s in pipeline.steps}
    assert {m: s.node_id for m, s in steps.items()} == {"load": "load",
                                                         "filter": "filter"}
    assert steps["filter"].depends_on == ["load"]
