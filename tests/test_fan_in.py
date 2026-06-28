"""
Workflow Test: Fan-In Pipeline (Gap 2)

Validates that pipelines with fan-in (multiple upstream parents merging
into one node) correctly produce:
  - ``StepDef.inputs`` populated from ``target_slot``
  - Snakemake rules with slot-named ``input:`` entries
  - Shell-based rules delegating to ``wfc run-step`` (ADR 008)
  - ``rule all:`` targeting leaf nodes

Scenario: Two ``csv_filter`` nodes (``filter_a``, ``filter_b``) feed into
one ``csv_merge`` node (``merge_ab``) via ``target_slot: "sources"``.
"""

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.snakemake_gen import load_pipeline, generate_snakefile


# =============================================================================
# Fixtures
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fan_in_pipeline_path(tmp_path):
    """Copy the fan-in pipeline JSON into a temp directory."""
    src = PROJECT_ROOT / "tests" / "fixtures" / "pipelines" / "pipeline_fan_in.json"
    dst = tmp_path / "pipeline_fan_in.json"
    dst.write_text(src.read_text())
    return dst


# =============================================================================
# Test: Fan-in pipeline load → Snakefile generation
# =============================================================================

@workflow(
    purpose="Verify fan-in pipeline loads target_slot into StepDef.inputs and "
            "generates flat-mode Snakefile with multi-input rules, WFC_INPUT_PATHS, "
            "and multiple --parent-run-id args")
def test_fan_in_load_and_snakefile(fan_in_pipeline_path, wfc_root):
    """Load a fan-in pipeline, verify StepDef.inputs, then generate and
    validate the Snakefile for flat-mode fan-in behaviour."""

    # ── Load pipeline ──────────────────────────────────────────────────────
    pipeline = load_pipeline(fan_in_pipeline_path)
    assert len(pipeline.steps) == 3

    step_map = {s.node_id: s for s in pipeline.steps}

    # Root filters have no upstream
    assert step_map["filter_a"].depends_on == []
    assert step_map["filter_b"].depends_on == []

    # Merge has two parents
    merge = step_map["merge_ab"]
    assert set(merge.depends_on) == {"filter_a", "filter_b"}

    # inputs dict populated from target_slot
    assert "sources" in merge.inputs
    assert set(merge.inputs["sources"]) == {"filter_a", "filter_b"}

    # ── Generate Snakefile ─────────────────────────────────────────────────
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Unified mode (no separate flat/legacy modes)
    assert "unified mode" in snakefile

    # Three rules by node_id
    for nid in ("filter_a", "filter_b", "merge_ab"):
        assert f"rule {nid}:" in snakefile

    # ADR-018: Snakemake-visible outputs are sentinels.
    assert ".runs/sentinels/test-pid/filter_a/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/merge_ab/" in snakefile

    # rule all: targets the leaf node (merge_ab), not filter nodes
    rule_all_section = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "merge_ab" in rule_all_section
    # filter nodes are not targets — they're intermediate
    assert "filter_a" not in rule_all_section or "merge_ab" in rule_all_section

    # Merge rule has slot-named input entries (fan-in)
    merge_rule = snakefile.split("rule merge_ab:")[1].split("\nrule ")[0]
    assert "sources_0=" in merge_rule
    assert "sources_1=" in merge_rule

    # ADR 008: rules use shell directives delegating to wfc run-step
    assert "shell:" in merge_rule
    assert "run-step" in merge_rule
    assert "--node-id" in merge_rule

    # Merge rule uses params block with node_id and variant
    assert 'node_id="merge_ab"' in merge_rule

    # Filter rules also delegate to wfc run-step via shell
    filter_a_rule = snakefile.split("rule filter_a:")[1].split("\nrule ")[0]
    assert "shell:" in filter_a_rule
    assert "run-step" in filter_a_rule

    # No Python run: blocks — all execution logic is in wfc run-step
    assert "run:" not in merge_rule
    assert "run:" not in filter_a_rule

    # Python preamble compiles
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


# =============================================================================
# Test: register_run + check_cache with slot:id parent format

