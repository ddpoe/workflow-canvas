"""
Workflow Test: Unified Path Scheme (Gap 12)

Validates the unified path scheme:

    .runs/workspace/test-pid/{node_id}/{sample}/{variant}/output{ext}

All pipelines — linear, fan-in, fan-out, with or without param_sets —
use the same path pattern. No separate flat/legacy modes.

Scenarios tested:
  1. Fan-in pipeline with no param_sets → all paths use /default/ variant
  2. Per-node param_sets on one node → only that node expands, others padded
  3. Explicit combos → sample-conditional variant binding
  4. Differential QC scenario → fan-in + per-node variants + explicit combos
"""

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.snakemake_gen import (
    StepDef, PipelineDef, generate_snakefile,
    load_pipeline, expand_variant_combos)


# =============================================================================
# Helpers
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# Test 1: Fan-in pipeline, no param_sets → default variant everywhere
# =============================================================================

@workflow(
    purpose="Verify fan-in pipeline with no param_sets uses /default/ variant "
            "in all output paths — unified scheme, no flat/legacy split")
def test_fan_in_no_variants_uses_default(wfc_root):
    """Fan-in DAG with no param sweeps. Every path includes /default/."""

    src = PROJECT_ROOT / "tests" / "fixtures" / "pipelines" / "pipeline_fan_in.json"
    pipeline = load_pipeline(src)
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Unified mode, not flat
    assert "unified mode" in snakefile
    assert "flat mode" not in snakefile

    # All paths include {variant} wildcard
    assert "{variant}" in snakefile

    # VARIANT_NAMES is just ["default"]
    assert "VARIANT_NAMES = ['default']" in snakefile

    # ADR-018: Snakemake-visible outputs are sentinels, not workspace files.
    # Real outputs live in DVC cache; sentinels gate the DAG.
    assert ".runs/sentinels/test-pid/filter_a/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/filter_b/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/merge_ab/" in snakefile

    # rule all uses expand() with variant=VARIANT_NAMES
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "expand(" in rule_all
    assert "variant=VARIANT_NAMES" in rule_all

    # ADR 008: every rule passes variant through params block for run-step
    for nid in ("filter_a", "filter_b", "merge_ab"):
        rule_section = snakefile.split(f"rule {nid}:")[1].split("\nrule ")[0]
        assert f'node_id="{nid}"' in rule_section
        assert 'variant="{variant}"' in rule_section
        assert "shell:" in rule_section
        assert "run-step" in rule_section

    # Python preamble compiles
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


# =============================================================================
# Test 2: Per-node param_sets on one node, others get defaults
# =============================================================================

@workflow(
    purpose="Verify per-node param_sets: one node has variants, others padded "
            "to use same default params for all variant names")
def test_per_node_param_sets(wfc_root):
    """Pipeline: preprocess → filter_cells → label.
    Only filter_cells has param_sets (strict, relaxed).
    preprocess and label should be padded with both variant names."""

    pipeline = PipelineDef(
        steps=[
            StepDef("preprocess", "demo", "methods/preprocess/preprocess.py",
                    {"normalize": True}, depends_on=[]),
            StepDef("filter_cells", "demo", "methods/filter_cells/filter_cells.py",
                    {"min_quality": 0.5}, depends_on=["preprocess"]),
            StepDef("label", "demo", "methods/label/label.py",
                    {"threshold": 0.5}, depends_on=["filter_cells"]),
        ],
        samples=["Pa16c"],
        param_sets={
            "filter_cells": {
                "strict": {"min_quality": 0.7},
                "relaxed": {"min_quality": 0.3},
            },
        })
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # VARIANT_NAMES should contain both param_sets variants + default
    # (filter_cells has strict/relaxed, preprocess/label get padded)
    assert "VARIANT_NAMES" in snakefile

    # ADR-018: Snakemake-visible outputs are sentinels, not workspace files.
    assert ".runs/sentinels/test-pid/preprocess/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/filter_cells/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/label/{sample}/{variant}/.complete" in snakefile

    # PARAMS for preprocess: both "strict" and "relaxed" map to same default params
    # (padded because preprocess has no param_sets entry)
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")

    # Check PARAMS dict contains padded entries
    assert "'strict'" in snakefile
    assert "'relaxed'" in snakefile

    # PARAMS for filter_cells should have different values per variant
    params_section = snakefile.split("PARAMS = {")[1].split("\n}")[0]
    assert "0.7" in params_section  # strict
    assert "0.3" in params_section  # relaxed

    # rule all expands over leaf node (label) × samples × variants
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "expand(" in rule_all
    assert "variant=VARIANT_NAMES" in rule_all

    # expand_variant_combos returns unified format
    resolved_params = {}
    for step in pipeline.steps:
        resolved_params[step.node_id] = pipeline.param_sets.get(
            step.node_id, pipeline.param_sets.get(
                step.method_name, {"default": step.params}
            )
        )
    combos = expand_variant_combos(pipeline.steps, pipeline.samples,
                                    resolved_params, None)
    # Should have 1 sample × (default + strict + relaxed) = 3 combos
    assert len(combos) == 3
    variants_in_combos = {c["variant"] for c in combos}
    assert variants_in_combos == {"default", "strict", "relaxed"}
    # All combos have sample + variant keys only
    for c in combos:
        assert set(c.keys()) == {"sample", "variant"}


# =============================================================================
# Test 3: Explicit combos with unified variant
# =============================================================================

@workflow(
    purpose="Verify explicit_combos bind specific variants to specific samples "
            "using the unified {variant} key")
def test_explicit_combos_unified(wfc_root):
    """Two samples, one node with param_sets, explicit combos bind variants
    to samples."""

    pipeline = PipelineDef(
        steps=[
            StepDef("preprocess", "demo", "methods/preprocess/preprocess.py",
                    {"normalize": True}, depends_on=[]),
            StepDef("filter_cells", "demo", "methods/filter_cells/filter_cells.py",
                    {"min_quality": 0.5}, depends_on=["preprocess"]),
        ],
        samples=["Rep2", "Rep3"],
        param_sets={
            "filter_cells": {
                "standard": {"min_quality": 0.5},
                "dim_corrected": {"min_quality": 0.3},
            },
        },
        explicit_combos=[
            {"sample": "Rep2", "variant": "standard"},
            {"sample": "Rep3", "variant": "dim_corrected"},
        ])
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Selective mode header
    assert "selective" in snakefile.lower()

    # RUNS list emitted
    assert "RUNS = " in snakefile

    # rule all uses list comprehension over RUNS, not expand
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "for r in RUNS" in rule_all
    assert "r['sample']" in rule_all
    assert "r['variant']" in rule_all

    # ADR-018: Snakemake-visible outputs are sentinels, not workspace files.
    assert ".runs/sentinels/test-pid/preprocess/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/filter_cells/{sample}/{variant}/.complete" in snakefile

    # Python preamble compiles
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")


# =============================================================================
# Test 4: Differential QC scenario (Appendix C validation)
# =============================================================================

@workflow(
    purpose="Validate the Differential QC scenario from Gap 12 Appendix C: "
            "fan-in DAG with per-node param_sets and sample-conditional "
            "variant binding via explicit_combos")
def test_differential_qc_scenario(wfc_root):
    """Minimal Differential QC scenario:
    - Two filter nodes (scr50, cycD1) → feature_qc nodes → merge → label
    - fqc nodes have param_sets (standard vs dim_corrected)
    - explicit_combos bind standard→Rep2, dim_corrected→Rep3
    """

    pipeline = PipelineDef(
        steps=[
            StepDef("scr50", "csv_tools", "modules/_builtin/csv_filter/csv_filter.py",
                    {"column": "condition", "values": ["scr_50nM"]},
                    depends_on=[], output_ext=".csv", node_id="scr50"),
            StepDef("cycD1", "csv_tools", "modules/_builtin/csv_filter/csv_filter.py",
                    {"column": "condition", "values": ["CycD1_50nM"]},
                    depends_on=[], output_ext=".csv", node_id="cycD1"),
            StepDef("fqc_scr50", "data_preprocessing", "methods/feature_qc/feature_qc.py",
                    {"filters": [{"column": "area", "min": 3.0}]},
                    depends_on=["scr50"], output_ext=".csv", node_id="fqc_scr50"),
            StepDef("fqc_cycD1", "data_preprocessing", "methods/feature_qc/feature_qc.py",
                    {"filters": [{"column": "area", "min": 3.0}]},
                    depends_on=["cycD1"], output_ext=".csv", node_id="fqc_cycD1"),
            StepDef("merge_cycD1", "csv_tools", "modules/_builtin/csv_merge/csv_merge.py",
                    {}, depends_on=["fqc_scr50", "fqc_cycD1"],
                    output_ext=".csv", node_id="merge_cycD1",
                    inputs={"sources": ["fqc_scr50", "fqc_cycD1"]}),
            StepDef("label_cycD1", "data_labeling", "methods/binary_labeling/binary_labeling.py",
                    {"threshold": 2.95}, depends_on=["merge_cycD1"],
                    output_ext=".csv", node_id="label_cycD1"),
        ],
        samples=["Rep2_siRNA", "Rep3_siRNA"],
        param_sets={
            "fqc_scr50": {
                "standard": {"filters": [{"column": "area", "min": 3.0},
                                          {"column": "R1_p27", "min": 2.5}]},
                "dim_corrected": {"filters": [{"column": "area", "min": 3.0},
                                               {"column": "R1_p27", "min": 2.3}]},
            },
            "fqc_cycD1": {
                "standard": {"filters": [{"column": "area", "min": 3.0},
                                          {"column": "R1_p27", "min": 2.5}]},
                "dim_corrected": {"filters": [{"column": "area", "min": 3.0},
                                               {"column": "R1_p27", "min": 2.3}]},
            },
        },
        explicit_combos=[
            {"sample": "Rep2_siRNA", "variant": "standard"},
            {"sample": "Rep3_siRNA", "variant": "dim_corrected"},
        ])
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="test-pid")

    # Selective mode (explicit_combos)
    assert "selective" in snakefile.lower()

    # All nodes present as rules
    for nid in ("scr50", "cycD1", "fqc_scr50", "fqc_cycD1",
                "merge_cycD1", "label_cycD1"):
        assert f"rule {nid}:" in snakefile

    # ADR-018: Snakemake-visible outputs are sentinels.
    assert ".runs/sentinels/test-pid/scr50/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/fqc_scr50/{sample}/{variant}/.complete" in snakefile
    assert ".runs/sentinels/test-pid/merge_cycD1/" in snakefile
    assert ".runs/sentinels/test-pid/label_cycD1/" in snakefile

    # PARAMS for fqc_scr50 has both standard and dim_corrected
    assert '"standard"' in snakefile
    assert '"dim_corrected"' in snakefile

    # Merge rule has fan-in slot-named inputs (ADR 008: shell-based, no WFC_INPUT_PATHS)
    merge_rule = snakefile.split("rule merge_cycD1:")[1].split("\nrule ")[0]
    assert "sources_0=" in merge_rule
    assert "sources_1=" in merge_rule
    assert "shell:" in merge_rule
    assert "run-step" in merge_rule

    # RUNS list contains the explicit combos
    assert "RUNS = " in snakefile

    # rule all targets leaf node (label_cycD1) with RUNS-driven list comprehension
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    assert "label_cycD1" in rule_all
    assert "for r in RUNS" in rule_all

    # ADR 008: every rule passes variant through params and delegates to run-step
    for nid in ("scr50", "fqc_scr50", "merge_cycD1", "label_cycD1"):
        rule_section = snakefile.split(f"rule {nid}:")[1].split("\nrule ")[0]
        assert 'variant="{variant}"' in rule_section
        assert "run-step" in rule_section

    # Python preamble compiles
    python_section = snakefile.split("rule all:")[0]
    compile(python_section, "<snakefile>", "exec")

    # Verify expand_variant_combos returns explicit combos as-is
    combos = expand_variant_combos(
        pipeline.steps, pipeline.samples, {}, pipeline.explicit_combos)
    assert len(combos) == 2
    assert combos[0]["variant"] == "standard"
    assert combos[1]["variant"] == "dim_corrected"


# =============================================================================
# Test 5: Canvas compile → engine end-to-end smoke
# (pev-2026-04-17-parameter-sweeps-chip-ux, Tier 3)
# =============================================================================

from axiom_annotations import Step


@workflow(
    purpose="End-to-end smoke: canvas-compiled authoring state with mixed "
            "sweeps and one per-sample override produces a Snakefile whose "
            "run matrix contains both the sweep and the override cells "
            "(US-1 + US-3).")
def test_canvas_sweep_compile_e2e(wfc_root, tmp_path):
    """Simulate what the canvas's compilePipelineToJSON would emit for an
    authoring state with (a) a sweep on filter_cells.min_quality (strict +
    relaxed) and (b) a per-sample override on SampleA.  Write it to JSON,
    load via load_pipeline, generate_snakefile, and confirm the run matrix."""

    _ = Step(step_num=1, name="Simulate canvas compile output",
             purpose="Mirror compilePipelineToJSON for a known authoring state")

    pipeline_json = {
        "name": "compile_e2e",
        "nodes": [
            {"id": "preprocess_1",
             "method": "preprocess", "module": "demo",
             "script": "methods/preprocess/preprocess.py",
             "params": {"normalize": True}, "env": "container:demo"},
            {"id": "filter_1",
             "method": "filter_cells", "module": "demo",
             "script": "methods/filter_cells/filter_cells.py",
             "params": {"min_quality": 0.5}, "env": "container:demo"},
        ],
        "links": [{"source": "preprocess_1", "target": "filter_1"}],
        "samples": ["SampleA", "SampleB"],
        # Compiled output: one sweep (strict/relaxed) + one override for
        # SampleA (named SampleA__o1 per the canvas convention).  Explicit
        # combos bind (SampleA,strict), (SampleB,relaxed), (SampleA,override).
        "param_sets": {
            "filter_1": {
                "strict":       {"min_quality": 0.7},
                "relaxed":      {"min_quality": 0.3},
                "SampleA__o1":  {"min_quality": 0.9},
            },
        },
        "explicit_combos": [
            {"sample": "SampleA", "variant": "strict"},
            {"sample": "SampleB", "variant": "relaxed"},
            {"sample": "SampleA", "variant": "SampleA__o1"},
        ],
    }

    _ = Step(step_num=2, name="Persist and re-load through the engine parser",
             purpose="Round-trip through load_pipeline to catch parser divergence")

    pipeline_path = tmp_path / "compile_e2e.json"
    pipeline_path.write_text(json.dumps(pipeline_json))
    pipeline = load_pipeline(pipeline_path)

    assert pipeline.param_sets == pipeline_json["param_sets"]
    assert pipeline.explicit_combos == pipeline_json["explicit_combos"]
    assert pipeline.samples == ["SampleA", "SampleB"]

    _ = Step(step_num=3, name="Generate Snakefile and assert run matrix",
             purpose="Verify both sweep cells and the override cell land in RUNS")

    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id="compile-e2e")

    # Selective mode — explicit_combos were provided.
    assert "selective" in snakefile.lower()
    # RUNS literal must list the three compiled combos.
    assert "RUNS = " in snakefile
    # Each variant name appears in the Snakefile text.
    assert "'strict'" in snakefile or '"strict"' in snakefile
    assert "'relaxed'" in snakefile or '"relaxed"' in snakefile
    assert "SampleA__o1" in snakefile  # the override variant
    # Override value landed in the PARAMS block.
    params_section = snakefile.split("PARAMS = {")[1].split("\n}")[0]
    assert "0.9" in params_section  # override min_quality
    assert "0.7" in params_section  # sweep: strict
    assert "0.3" in params_section  # sweep: relaxed

    _ = Step(step_num=4, name="Verify expand_variant_combos",
             purpose="Pure-function path: the compiled combos are returned as-is")

    combos = expand_variant_combos(
        pipeline.steps, pipeline.samples, {}, pipeline.explicit_combos)
    assert len(combos) == 3
    variants_seen = {c["variant"] for c in combos}
    samples_seen = {c["sample"] for c in combos}
    assert variants_seen == {"strict", "relaxed", "SampleA__o1"}
    assert samples_seen == {"SampleA", "SampleB"}
    # The override is specifically bound to SampleA.
    override_rows = [c for c in combos if c["variant"] == "SampleA__o1"]
    assert len(override_rows) == 1
    assert override_rows[0]["sample"] == "SampleA"
