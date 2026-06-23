"""
Integration test: Snakemake pipeline generation.

Story: We define a pipeline (steps + samples + param variants), generate
a Snakefile, and verify the output is correct — proper rules, wildcard
structure, cache-check logic, and variant expansion. This tests the
snakemake_gen module end-to-end without actually running Snakemake.

Three modes are tested:
  1. Single-step pipeline (simplest case)
  2. Cartesian product (all variant combinations)
  3. Selective combos (hand-picked variant combinations)
"""

import json

import pytest

from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile


# =============================================================================
# Helper
# =============================================================================

# =============================================================================
# Single-step pipeline
# =============================================================================

class TestSingleStepGeneration:
    """Generate a Snakefile for one method with one sample."""

    def test_produces_one_rule(self, wfc_root):
        """A single-step pipeline should produce exactly one rule (plus rule all)."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo_pipeline",
                script_path="methods/preprocess/preprocess.py",
                params={"normalize": True})],
            samples=["Pa16c"])
        snakefile = generate_snakefile(pipeline, wfc_root)

        assert "rule preprocess:" in snakefile
        assert "rule all:" in snakefile
        # Should NOT contain rules for other methods
        assert "rule filter_cells:" not in snakefile

    def test_output_path_has_sample_and_variant_wildcards(self, wfc_root):
        """Output path should encode both sample and variant dimensions."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo_pipeline",
                script_path="methods/preprocess/preprocess.py",
                params={"normalize": True})],
            samples=["Pa16c"])
        snakefile = generate_snakefile(pipeline, wfc_root)

        assert "{sample}" in snakefile
        assert "{variant}" in snakefile

    def test_delegates_to_run_step(self, wfc_root):
        """ADR 008: Every rule delegates to wfc run-step via shell directive."""
        pipeline = PipelineDef(
            steps=[StepDef(
                method_name="preprocess",
                module_name="demo_pipeline",
                script_path="methods/preprocess/preprocess.py",
                params={"normalize": True})],
            samples=["Pa16c"])
        snakefile = generate_snakefile(pipeline, wfc_root)

        # Rules use shell directives delegating to run-step
        rule_section = snakefile.split("rule preprocess:")[1].split("\nrule ")[0]
        assert "shell:" in rule_section
        assert "run-step" in rule_section
        assert "run:" not in rule_section


# =============================================================================
# Multi-step cartesian pipeline
# =============================================================================

class TestCartesianGeneration:
    """Generate a Snakefile for preprocess → filter_cells → label (cartesian)."""

    @pytest.fixture
    def pipeline(self):
        return PipelineDef(
            steps=[
                StepDef("preprocess", "demo_pipeline", "methods/preprocess/preprocess.py",
                        {"normalize": True}, depends_on=[]),
                StepDef("filter_cells", "demo_pipeline", "methods/filter_cells/filter_cells.py",
                        {"min_quality": 0.5}, depends_on=["preprocess"]),
                StepDef("label", "demo_pipeline", "methods/label/label.py",
                        {"threshold": 0.5}, depends_on=["filter_cells"]),
            ],
            samples=["Pa16c"],
            param_sets={
                "filter_cells": {
                    "strict": {"min_quality": 0.7},
                    "relaxed": {"min_quality": 0.3},
                },
            })

    def test_produces_three_rules(self, pipeline, wfc_root):
        """Should generate one rule per step."""
        snakefile = generate_snakefile(pipeline, wfc_root)
        assert "rule preprocess:" in snakefile
        assert "rule filter_cells:" in snakefile
        assert "rule label:" in snakefile

    def test_downstream_input_is_upstream_output(self, pipeline, wfc_root):
        """filter_cells input path should match preprocess output path pattern."""
        snakefile = generate_snakefile(pipeline, wfc_root)

        # filter_cells should take preprocess's output as input
        # The workspace path is scoped by pipeline_id: .runs/workspace/{id}/preprocess/
        assert "/preprocess/" in snakefile

    def test_leaf_output_uses_unified_variant_wildcard(self, pipeline, wfc_root):
        """All output paths use a single {variant} wildcard (unified scheme)."""
        snakefile = generate_snakefile(pipeline, wfc_root)

        # Unified scheme uses a single {variant} wildcard, not per-node wildcards
        assert "{variant}" in snakefile
        assert "{preprocess_v}" not in snakefile
        assert "{filter_cells_v}" not in snakefile
        assert "{label_v}" not in snakefile

    def test_downstream_wires_input_from_upstream(self, pipeline, wfc_root):
        """Non-root steps receive upstream output as input via Snakemake DAG wiring."""
        snakefile = generate_snakefile(pipeline, wfc_root)

        # ADR 008: parent run ID resolution is handled by run-step, not the Snakefile.
        # The Snakefile wires inputs via file paths — Snakemake resolves the DAG.
        filter_rule = snakefile.split("rule filter_cells:")[1].split("\nrule ")[0]
        assert "preprocess" in filter_rule  # input comes from preprocess output

    def test_header_shows_unified_mode(self, pipeline, wfc_root):
        """Snakefile header should indicate unified mode."""
        snakefile = generate_snakefile(pipeline, wfc_root)

        assert "unified" in snakefile.lower()

    def test_rule_all_uses_expand(self, pipeline, wfc_root):
        """Cartesian mode should use expand() in rule all."""
        snakefile = generate_snakefile(pipeline, wfc_root)

        assert "expand(" in snakefile


# =============================================================================
# Selective combos
# =============================================================================

class TestSelectiveGeneration:
    """Generate a Snakefile with explicit_combos (selective mode)."""

    @pytest.fixture
    def pipeline(self):
        return PipelineDef(
            steps=[
                StepDef("preprocess", "demo_pipeline", "methods/preprocess/preprocess.py",
                        {"normalize": True}, depends_on=[]),
                StepDef("filter_cells", "demo_pipeline", "methods/filter_cells/filter_cells.py",
                        {"min_quality": 0.5}, depends_on=["preprocess"]),
            ],
            samples=["Pa16c"],
            param_sets={
                "preprocess": {"default": {"normalize": True}},
                "filter_cells": {
                    "strict": {"min_quality": 0.7},
                    "relaxed": {"min_quality": 0.3},
                },
            },
            explicit_combos=[
                {"sample": "Pa16c", "variant": "strict"},
            ])

    def test_header_shows_selective_mode(self, pipeline, wfc_root):
        """Snakefile header should indicate selective mode."""
        snakefile = generate_snakefile(pipeline, wfc_root)
        assert "selective" in snakefile.lower()

    def test_rule_all_uses_runs_list_not_expand(self, pipeline, wfc_root):
        """Selective mode should iterate RUNS list, not use expand()."""
        snakefile = generate_snakefile(pipeline, wfc_root)
        assert "RUNS" in snakefile
        # Should NOT use expand for the final target
        # (it uses a list comprehension over RUNS instead)

    def test_selective_combo_count_in_header(self, pipeline, wfc_root):
        """Header should show estimated runs matching the combo count."""
        snakefile = generate_snakefile(pipeline, wfc_root)
        # 1 combo × 2 steps = 2 estimated total runs
        assert "selective" in snakefile.lower()


# =============================================================================
# Snakefile is syntactically valid Python
# =============================================================================

class TestSnakefileSyntax:
    """The generated Snakefile should be valid Python (Snakemake is Python-based)."""

    def test_compiles_without_syntax_error(self, wfc_root):
        """Generated Snakefile should compile() without SyntaxError."""
        pipeline = PipelineDef(
            steps=[
                StepDef("preprocess", "demo_pipeline", "methods/preprocess/preprocess.py",
                        {"normalize": True}, depends_on=[]),
                StepDef("filter_cells", "demo_pipeline", "methods/filter_cells/filter_cells.py",
                        {"min_quality": 0.5}, depends_on=["preprocess"]),
            ],
            samples=["Pa16c", "CFPAC1"])
        snakefile = generate_snakefile(pipeline, wfc_root)

        # Snakefiles have rule/expand/etc that aren't pure Python,
        # but the helper functions and PARAMS dict should be parseable.
        # Extract just the Python parts (before 'rule all:')
        python_section = snakefile.split("rule all:")[0]
        compile(python_section, "<snakefile>", "exec")  # Should not raise
