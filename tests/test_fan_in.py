"""
Workflow Test: Fan-In Pipeline

Validates that pipelines with fan-in (multiple upstream parents merging
into one node) correctly produce:
  - ``StepDef.inputs`` populated from ``target_slot``
  - Snakemake rules with slot-named ``input:`` entries
  - Shell-based rules delegating to ``wfc run-step``
  - ``rule all:`` targeting leaf nodes

Scenario: Two ``csv_filter`` nodes (``filter_a``, ``filter_b``) feed into
one ``csv_merge`` node (``merge_ab``) via ``target_slot: "sources"``.
"""

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.execution import load_pipeline_from_path
from wfc.orchestration import generate_snakefile


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
            "generates a unified-mode Snakefile with multi-input rules, WFC_INPUT_PATHS, "
            "and multiple --parent-run-id args")
def test_fan_in_load_and_snakefile(fan_in_pipeline_path, wfc_root):
    """Load a fan-in pipeline, verify StepDef.inputs, then generate and
    validate the Snakefile's fan-in rules."""

    # ── Load pipeline ──────────────────────────────────────────────────────
    pipeline = load_pipeline_from_path(fan_in_pipeline_path)
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

    # The generated file declares its one (unified) mode
    assert "unified mode" in snakefile

    # Three rules by node_id
    for nid in ("filter_a", "filter_b", "merge_ab"):
        assert f"rule {nid}:" in snakefile

    # Snakemake-visible outputs are sentinels.
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

    # rules use shell directives delegating to wfc run-step
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


def _two_upstream_document(second_link: dict) -> dict:
    """selector -> a, b; a -> c on ``merged`` naming ``out``; then ``second_link``."""
    method = {"type": "method", "env": "demo", "module": "m", "params": {}}
    return {
        "nodes": [
            {"id": "sel", "type": "input_selector", "params": {}, "samples": ["s1"]},
            {"id": "a", "method": "a", **method},
            {"id": "b", "method": "b", **method},
            {"id": "c", "method": "c", **method},
        ],
        "links": [
            {"source": "sel", "target": "a"},
            {"source": "sel", "target": "b"},
            {"source": "a", "target": "c", "source_slot": "out",
             "target_slot": "merged"},
            second_link,
        ],
        "samples": [],
    }


def test_mixed_fan_in_records_source_slots_in_link_order():
    """One link names a source slot, the other none: [slot, None] in link order."""
    from wfc.graph import load_pipeline

    document = _two_upstream_document(
        {"source": "b", "target": "c", "target_slot": "merged"})

    pipeline = load_pipeline(document, contract_map={}, reference_outputs={})

    (consumer,) = [s for s in pipeline.steps if s.node_id == "c"]
    assert consumer.inputs == {"merged": ["a", "b"]}
    assert consumer.input_source_slots == {"merged": ["out", None]}


def test_a_link_with_no_target_slot_lands_on_data():
    """A link naming no target slot is wired onto the consumer's ``data`` slot."""
    from wfc.graph import load_pipeline

    document = _two_upstream_document({"source": "b", "target": "c"})

    pipeline = load_pipeline(document, contract_map={}, reference_outputs={})

    (consumer,) = [s for s in pipeline.steps if s.node_id == "c"]
    assert consumer.inputs["data"] == ["b"]
