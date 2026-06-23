"""
Workflow Test: Node-ID-Based Pipeline Identity (Gap 1)

Validates that pipelines with duplicate method names (same method used
by multiple nodes) correctly produce distinct node_ids, topo-sort
independently, and generate separate Snakemake rules.

Scenario: Two parallel branches — each uses ``csv_filter`` → ``feature_qc``,
but the four nodes have unique string IDs: ``filter_rep2``, ``filter_rep3``,
``qc_rep2``, ``qc_rep3``.

This exercises the ``has_duplicate_methods=True`` code path in
``load_pipeline()``, which was not tested by the existing suite (all
legacy pipeline JSONs have unique method names per node).
"""

import json
from pathlib import Path

import pytest
from dflow.core.decorators import workflow

from wfc.snakemake_gen import load_pipeline, topo_sort_steps, expand_variant_combos
from wfc.snakemake_gen import generate_snakefile


# =============================================================================
# Fixtures
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def dup_pipeline_path(tmp_path):
    """Copy the duplicate-methods pipeline JSON into a temp directory."""
    src = PROJECT_ROOT / "pipeline_duplicate_methods.json"
    dst = tmp_path / "pipeline_duplicate_methods.json"
    dst.write_text(src.read_text())
    return dst


# =============================================================================
# Test 1: Engine handles duplicate methods end-to-end
# =============================================================================

@workflow(
    purpose="Verify load_pipeline, topo_sort, and expand_variant_combos all use "
            "node_id (not method_name) when methods repeat across nodes")
def test_engine_duplicate_methods(dup_pipeline_path):
    """Load → sort → expand a pipeline where csv_filter and feature_qc each
    appear twice.  Every stage should key on node_id, not method_name."""

    # -- Load --
    pipeline = load_pipeline(dup_pipeline_path)
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

    # -- Expand variant combos --
    resolved_params: dict[str, dict[str, dict]] = {}
    for step in ordered:
        resolved_params[step.node_id] = pipeline.param_sets.get(
            step.node_id,
            pipeline.param_sets.get(step.method_name, {"default": step.params}))

    combos = expand_variant_combos(ordered, pipeline.samples, resolved_params, None)
    assert len(combos) >= 1
    combo = combos[0]
    # Unified scheme: keys are "sample" and "variant", not per-node keys
    assert "sample" in combo and "variant" in combo
    assert "csv_filter" not in combo and "feature_qc" not in combo


# =============================================================================
# Test 2: Snakefile generation with duplicate methods
# =============================================================================

@workflow(
    purpose="Verify generate_snakefile emits one rule per node_id with correct "
            "workspace paths and input→output wiring between branches")
def test_snakefile_duplicate_methods(dup_pipeline_path, wfc_root):
    """Snakefile should have four distinct rules — not two collapsed by method name."""

    pipeline = load_pipeline(dup_pipeline_path)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Four rules by node_id, zero by method name
    for nid in ("filter_rep2", "filter_rep3", "qc_rep2", "qc_rep3"):
        assert f"rule {nid}:" in snakefile
        # ADR-018: Snakemake-visible outputs are sentinels, not workspace files.
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
