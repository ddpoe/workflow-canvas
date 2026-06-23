"""E2E tests for pipeline system nodes (input_selector, run_reference).

Tests verify that system nodes are correctly resolved during pipeline
execution: input_selector merges selected samples into the pipeline,
and run_reference injects a prior run artifact as a static dependency.

All pipelines use system nodes as roots (no method-node roots).

Tier 3: @workflow + Step() markers (stakeholder-recognizable pipeline scenarios).
"""

import csv
import json
from pathlib import Path

import pytest
from sqlmodel import select

from dflow.core.decorators import workflow, Step

from wfc.cli import run_pipeline
from wfc.database import get_session
from wfc.models import Run, RunOutput
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv

WFC_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_run_output_path(
    *,
    nf_process_name: str,
    sample: str,
    output_name: str,
) -> Path:
    """Locate RunOutput.artifact_path (ADR-018: workspace is gone)."""
    # Join Method.name -- Run.nf_process_name may not be tagged with node_id.
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
    assert rows, (
        f"No RunOutput for node='{nf_process_name}' sample='{sample}' "
        f"output='{output_name}'"
    )
    return Path(rows[-1][0].artifact_path)


@workflow(
    purpose="Verify input_selector system node merges selected samples into pipeline execution",
)
def test_input_selector_pipeline(pipeline_factory, register_fixture_methods):
    """Input selector provides sample names that the pipeline runs on.

    Pipeline topology: input_selector(samples=["sel_sample"]) -> transform.
    The pipeline has no samples of its own; they come from input_selector.
    After running, transform output must exist for "sel_sample".
    """
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data and build pipeline",
        purpose="Write sample CSV and create pipeline with input_selector -> transform",
    )
    _create_sample_csv(project_dir, "sel_sample", num_rows=3)

    pipeline_path = pipeline_factory(
        name="selector",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sel_sample"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_s"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
        ],
        samples=[],
    )

    s = Step(
        step_num=2,
        name="Run pipeline",
        purpose="Execute the pipeline with input selector providing samples",
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    s = Step(
        step_num=3,
        name="Verify output exists for selected sample",
        purpose="ADR-018: sentinel + RunOutput row for transform_1 / sel_sample",
    )
    sentinels = list(project_dir.rglob(".runs/sentinels/*/transform_1/sel_sample/default/.complete"))
    assert len(sentinels) == 1, f"Expected 1 sentinel for sel_sample, found {len(sentinels)}"
    output_path = _find_run_output_path(
        nf_process_name="transform", sample="sel_sample", output_name="output.csv",
    )

    s = Step(
        step_num=4,
        name="Verify output content",
        purpose="Check the transformed CSV has the correct computed column",
    )
    with open(output_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    assert len(rows) == 3
    assert "computed_s" in fieldnames, f"Expected computed_s column, got {fieldnames}"


@workflow(
    purpose="Verify run_reference system node makes a prior run output available as input",
)
def test_run_reference_pipeline(pipeline_factory, register_fixture_methods):
    """Run reference provides a prior run's output as input to a downstream step.

    Pipeline: input_selector -> transform (run 1) -> FINISH,
    then run_reference(pointing to transform output) -> transform_2.
    The run_reference node's output_path points directly to the prior output.
    """
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Run initial pipeline to create prior output",
        purpose="Execute an input_selector -> transform pipeline whose output will be referenced later",
    )
    _create_sample_csv(project_dir, "ref_sample", num_rows=4)

    initial_pipeline_path = pipeline_factory(
        name="initial_transform",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["ref_sample"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_init"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
        ],
        samples=[],
    )
    run_pipeline(
        pipeline_path=str(initial_pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=False,
    )

    # ADR-018: find the transform output via RunOutput row (workspace is gone)
    prior_output = _find_run_output_path(
        nf_process_name="transform", sample="ref_sample", output_name="output.csv",
    )
    assert prior_output.exists(), f"Initial transform output missing: {prior_output}"

    s = Step(
        step_num=2,
        name="Build pipeline with run reference",
        purpose="Create a pipeline where run_reference points to the prior transform output",
    )
    pipeline_path = pipeline_factory(
        name="runref",
        nodes=[
            {"id": "runref_1", "type": "run_reference",
             "run_id": "", "output_slot": "output",
             "output_path": str(prior_output)},
            {"id": "transform_2", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_ref"}},
        ],
        links=[
            # Declare target_slot so the orchestrator passes
            # --ref-input data=<path> rather than --ref-input run_ref_0=<path>
            # (transform's method.yaml declares its input slot as "data").
            {"source": "runref_1", "target": "transform_2",
             "target_slot": "data"},
        ],
        samples=["ref_sample"],
    )

    s = Step(
        step_num=3,
        name="Run pipeline with run reference",
        purpose="Execute the pipeline; transform_2 should receive the referenced output",
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
        name="Verify transform used referenced output",
        purpose="Check transform_2 output exists and has the expected content from prior transform",
    )
    transform_2_output = _find_run_output_path(
        nf_process_name="transform", sample="ref_sample", output_name="output.csv",
    )
    assert transform_2_output.exists(), f"transform_2 output missing: {transform_2_output}"
    with open(transform_2_output, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    assert len(rows) == 4, f"Expected 4 rows from prior transform, got {len(rows)}"
    assert "computed_ref" in fieldnames, f"Expected computed_ref column, got {fieldnames}"
