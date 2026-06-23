"""E2E tests for pipeline topology wiring.

Tests verify that the pipeline runtime correctly wires data between nodes
for the three basic topologies: linear, fan-out, and fan-in. Each test
builds a pipeline from fixture methods using system nodes (input_selector)
as roots, runs it via run_pipeline(), and verifies output artifacts exist
with expected content.

Tier 3: @workflow + Step() markers (stakeholder-recognizable pipeline scenarios).
"""

import csv
from pathlib import Path

import pytest
from sqlmodel import select

from dflow.core.decorators import workflow, Step

from wfc.cli import run_pipeline
from wfc.database import get_session
from wfc.models import Run, RunOutput
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv

# wfc package root -- needed so Snakemake subprocesses can find `python -m wfc`
WFC_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_run_output_path(
    *,
    pipeline_id_substr: str | None = None,
    nf_process_name: str,
    sample: str,
    output_name: str,
) -> Path:
    """Locate a RunOutput.artifact_path for a given (node, sample) pair.

    ADR-018 deleted ``.runs/workspace/`` — content now lives in the
    run-archive directory tracked by ``RunOutput.artifact_path``.  The
    pipeline_id substring is optional; node/sample/output_name uniquely
    identify the row within a single-pipeline test.
    """
    # Join on Method.name so we can target a specific step regardless of
    # whether the runtime tagged Run.nf_process_name with the node_id.
    from wfc.models import Method
    with get_session() as session:
        stmt = (
            select(RunOutput, Run, Method)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == nf_process_name)
            .where(Run.sample == sample)
            .where(RunOutput.output_name == output_name)
        )
        rows = session.exec(stmt).all()
    assert len(rows) >= 1, (
        f"No RunOutput for node='{nf_process_name}' sample='{sample}' "
        f"output='{output_name}'"
    )
    return Path(rows[-1][0].artifact_path)


@workflow(
    purpose="Verify a linear 2-node pipeline (input_selector -> transform) produces correct output",
)
def test_linear_pipeline(pipeline_factory, register_fixture_methods):
    """Linear topology: input_selector provides sample, transform adds a column."""
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data",
        purpose="Write a sample CSV file for the input_selector to provide",
    )
    _create_sample_csv(project_dir, "sample_a", num_rows=3)

    s = Step(
        step_num=2,
        name="Build linear pipeline",
        purpose="Create a 2-node pipeline: input_selector -> transform",
    )
    pipeline_path = pipeline_factory(
        name="linear",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_t"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
        ],
        samples=[],
    )

    s = Step(
        step_num=3,
        name="Run pipeline",
        purpose="Execute the linear pipeline via run_pipeline()",
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(
        step_num=4,
        name="Verify transform output exists",
        purpose="ADR-018: workspace is gone; assert sentinel + RunOutput row exists",
    )
    sentinel = project_dir.rglob(".runs/sentinels/*/transform_1/sample_a/default/.complete")
    sentinels = list(sentinel)
    assert len(sentinels) == 1, f"Expected 1 sentinel, found {len(sentinels)}"
    output_path = _find_run_output_path(
        nf_process_name="transform", sample="sample_a", output_name="output.csv",
    )

    s = Step(
        step_num=5,
        name="Verify output content",
        purpose="Check the transformed CSV has the original columns plus the computed column",
    )
    with open(output_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    assert "id" in fieldnames
    assert "value" in fieldnames
    assert "computed_t" in fieldnames, f"Expected computed_t column, got {fieldnames}"


@workflow(
    purpose="Verify a fan-out pipeline (input_selector -> transform_a, transform_b) produces both outputs",
)
def test_fan_out_pipeline(pipeline_factory, register_fixture_methods):
    """Fan-out topology: input_selector feeds two independent transforms."""
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data",
        purpose="Write a sample CSV file for the input_selector to provide",
    )
    _create_sample_csv(project_dir, "sample_a", num_rows=4)

    s = Step(
        step_num=2,
        name="Build fan-out pipeline",
        purpose="Create a 3-node pipeline: input_selector -> transform_a AND input_selector -> transform_b",
    )
    pipeline_path = pipeline_factory(
        name="fan_out",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_a", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_a"}},
            {"id": "transform_b", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_b"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_a"},
            {"source": "selector_1", "target": "transform_b"},
        ],
        samples=[],
    )

    s = Step(
        step_num=3,
        name="Run pipeline",
        purpose="Execute the fan-out pipeline via run_pipeline()",
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(
        step_num=4,
        name="Verify both transform outputs exist",
        purpose="ADR-018: assert sentinels + RunOutput rows for both fan-out branches",
    )
    sentinels_a = list(project_dir.rglob(".runs/sentinels/*/transform_a/sample_a/default/.complete"))
    sentinels_b = list(project_dir.rglob(".runs/sentinels/*/transform_b/sample_a/default/.complete"))
    assert len(sentinels_a) == 1, f"Expected 1 transform_a sentinel, found {len(sentinels_a)}"
    assert len(sentinels_b) == 1, f"Expected 1 transform_b sentinel, found {len(sentinels_b)}"
    # Both fan-out branches share method='transform' / sample='sample_a';
    # collect both RunOutput artifact paths and verify the two suffixes
    # appear across them.
    from wfc.models import Method
    with get_session() as session:
        stmt = (
            select(RunOutput, Run, Method)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform")
            .where(Run.sample == "sample_a")
            .where(RunOutput.output_name == "output.csv")
        )
        rows = session.exec(stmt).all()
    assert len(rows) == 2, f"Expected 2 fan-out RunOutput rows, found {len(rows)}"
    artifacts = [Path(r[0].artifact_path) for r in rows]

    s = Step(
        step_num=5,
        name="Verify output content differs by suffix",
        purpose="Each transform should have added a different computed column",
    )
    fieldnames_seen: list[str] = []
    for ap in artifacts:
        with open(ap, newline="") as f:
            fieldnames_seen.extend(csv.DictReader(f).fieldnames or [])
    assert "computed_a" in fieldnames_seen, f"missing computed_a in: {fieldnames_seen}"
    assert "computed_b" in fieldnames_seen, f"missing computed_b in: {fieldnames_seen}"


@workflow(
    purpose="Verify a fan-in pipeline (transform_a, transform_b -> merge) merges inputs correctly",
)
def test_fan_in_pipeline(pipeline_factory, register_fixture_methods):
    """Fan-in topology: input_selector feeds two transforms that merge."""
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data",
        purpose="Write a sample CSV file for the input_selector to provide",
    )
    _create_sample_csv(project_dir, "sample_a", num_rows=3)

    s = Step(
        step_num=2,
        name="Build fan-in pipeline",
        purpose="Create a 4-node pipeline: input_selector -> transform_a, transform_b -> merge",
    )
    pipeline_path = pipeline_factory(
        name="fan_in",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_a", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_a"}},
            {"id": "transform_b", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_b"}},
            {"id": "merge_1", "method": "merge", "module": "test_pipeline",
             "params": {}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_a"},
            {"source": "selector_1", "target": "transform_b"},
            {"source": "transform_a", "target": "merge_1", "target_slot": "sources"},
            {"source": "transform_b", "target": "merge_1", "target_slot": "sources"},
        ],
        samples=[],
    )

    s = Step(
        step_num=3,
        name="Run pipeline",
        purpose="Execute the fan-in pipeline via run_pipeline()",
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(
        step_num=4,
        name="Verify merged output exists",
        purpose="ADR-018: sentinel + RunOutput row for the fan-in merge",
    )
    sentinels = list(project_dir.rglob(".runs/sentinels/*/merge_1/sample_a/default/.complete"))
    assert len(sentinels) == 1, f"Expected 1 merge sentinel, found {len(sentinels)}"
    merge_output = _find_run_output_path(
        nf_process_name="merge", sample="sample_a", output_name="merged.csv",
    )

    s = Step(
        step_num=5,
        name="Verify merged content has rows from both sources",
        purpose="The merged CSV should contain 3 + 3 = 6 rows from both transforms",
    )
    with open(merge_output, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 6, f"Expected 6 merged rows (3 + 3), got {len(rows)}"


@workflow(
    purpose="Verify a fan-in selector (fan_mode='in') bundles multiple samples into a single collapsed run of merge",
)
def test_fan_in_selector_pipeline(pipeline_factory, register_fixture_methods):
    """Fan-in selector topology: one input_selector with fan_mode='in'
    and three samples feeds directly into merge. The sample axis collapses
    to '__all__', merge runs once per variant (not per sample), and its
    'sources' slot receives all three samples' CSVs as --ref-input
    entries. This exercises the path (system-node → method, collapsed
    run, slot-named multi-value --ref-input) that pure compile-layer
    tests cannot validate.
    """
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create three sample CSVs and their restore sentinels",
        purpose="Each sample contributes 3 rows; merge should produce 9. The "
                ".sample_ready sentinel files satisfy the dependency ordering "
                "that collapsed root steps declare in their input: block (the "
                "test bypasses DVC-backed sample registration).",
    )
    for sample in ("s1", "s2", "s3"):
        _create_sample_csv(project_dir, sample, num_rows=3)
        (project_dir / "data" / "samples" / sample / ".sample_ready").write_text("")

    s = Step(
        step_num=2,
        name="Build fan-in selector pipeline",
        purpose="Single selector (fan_mode='in', 3 samples) -> merge on slot 'sources'",
    )
    pipeline_path = pipeline_factory(
        name="fan_in_selector",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["s1", "s2", "s3"], "fan_mode": "in"},
            {"id": "merge_1", "method": "merge", "module": "test_pipeline",
             "params": {}},
        ],
        links=[
            {"source": "selector_1", "target": "merge_1", "target_slot": "sources"},
        ],
        samples=[],
    )

    s = Step(
        step_num=3,
        name="Run pipeline",
        purpose="Execute via run_pipeline() -- exercises the real Snakemake + run-step path",
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(
        step_num=4,
        name="Verify collapsed merged output exists under __all__ sample segment",
        purpose="ADR-018: sentinel + RunOutput for the collapsed fan-in run",
    )
    sentinels = list(project_dir.rglob(".runs/sentinels/*/merge_1/__all__/default/.complete"))
    assert len(sentinels) == 1, f"Expected 1 collapsed merge sentinel, found {len(sentinels)}"
    # The helper joins on Method.name (the registered method), not the
    # canvas node_id. The merge_1 node uses the 'merge' method.
    merge_output = _find_run_output_path(
        nf_process_name="merge", sample="__all__", output_name="merged.csv",
    )

    s = Step(
        step_num=5,
        name="Verify merged content has rows from all three samples",
        purpose="Fan-in collapses 3 samples into a single run; merge concatenates all source CSVs",
    )
    with open(merge_output, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 9, f"Expected 9 merged rows (3 x 3 samples), got {len(rows)}"
