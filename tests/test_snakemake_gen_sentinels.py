"""ADR-018 Task 2 — Sentinel-only Snakemake outputs.

The Snakemake-visible `output:` declaration collapses to a single zero-byte
sentinel per (pipeline, node, sample, variant). The real data outputs stay
in `.runs/<run_id>/<slot>/` (staging) — Snakemake never sees them.

These tests pin the new generator contract.
"""

from wfc.snakemake_gen import StepDef, PipelineDef, generate_snakefile, _output_path


def _step_map(pipeline: PipelineDef) -> dict[str, StepDef]:
    return {s.node_id: s for s in pipeline.steps}


def test_output_path_emits_sentinel_path():
    """_output_path returns a `.runs/sentinels/.../.complete` path."""
    pipeline = PipelineDef(
        steps=[StepDef(
            method_name="preprocess",
            module_name="demo",
            script_path="methods/preprocess/preprocess.py",
            params={},
        )],
        samples=["S1"],
    )
    sm = _step_map(pipeline)
    out = _output_path("preprocess", sm, pipeline_id="pid42")
    assert out == ".runs/sentinels/pid42/preprocess/{sample}/{variant}/.complete"


def test_output_path_collapsed_uses_all_segment():
    """Collapsed (fan-in) steps bake __all__ into the sample segment."""
    step = StepDef(
        method_name="merge",
        module_name="demo",
        script_path="methods/merge.py",
        params={},
        sample_collapsed=True,
        collapsed_samples=["S1", "S2"],
    )
    pipeline = PipelineDef(steps=[step], samples=["S1", "S2"])
    sm = _step_map(pipeline)
    out = _output_path("merge", sm, pipeline_id="pid42")
    assert out == ".runs/sentinels/pid42/merge/__all__/{variant}/.complete"


def test_generate_rule_emits_single_sentinel_output(wfc_root):
    """Multi-slot output method should declare ONE sentinel, not per-slot files."""
    pipeline = PipelineDef(
        steps=[StepDef(
            method_name="multi_out",
            module_name="demo",
            script_path="methods/multi/multi.py",
            params={},
            slot_outputs={"primary": "out.csv", "report": "report.html"},
        )],
        samples=["S1"],
    )
    snakefile = generate_snakefile(pipeline, wfc_root)
    rule_block = snakefile.split("rule multi_out:")[1].split("\nrule ")[0]

    # Old multi-slot output declaration must not appear
    assert "primary=" not in rule_block
    assert "report=" not in rule_block
    assert "directory(" not in rule_block

    # New: single sentinel output line
    assert ".runs/sentinels/" in rule_block
    assert ".complete" in rule_block


def test_generate_rule_single_output_uses_sentinel(wfc_root):
    """Single-output method also collapses to a sentinel (uniform contract)."""
    pipeline = PipelineDef(
        steps=[StepDef(
            method_name="step",
            module_name="demo",
            script_path="methods/step.py",
            params={},
        )],
        samples=["S1"],
    )
    snakefile = generate_snakefile(pipeline, wfc_root)
    rule_block = snakefile.split("rule step:")[1].split("\nrule ")[0]
    assert ".runs/sentinels/" in rule_block
    assert ".complete" in rule_block
    # No .runs/workspace/ output any more
    assert ".runs/workspace/" not in rule_block.split("input:")[0]


def test_downstream_input_references_upstream_sentinel(wfc_root):
    """A downstream rule's input is the upstream rule's sentinel path."""
    pipeline = PipelineDef(
        steps=[
            StepDef("preprocess", "demo", "methods/preprocess.py", {}, depends_on=[]),
            StepDef("filter", "demo", "methods/filter.py", {}, depends_on=["preprocess"]),
        ],
        samples=["S1"],
    )
    snakefile = generate_snakefile(pipeline, wfc_root)
    filter_block = snakefile.split("rule filter:")[1].split("\nrule ")[0]
    # The input points at the preprocess sentinel, not a workspace data file
    assert ".runs/sentinels/" in filter_block
    assert "/preprocess/" in filter_block
    assert ".complete" in filter_block
