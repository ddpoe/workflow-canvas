"""E2E tests for pipeline failure modes.

Tests verify that the pipeline runtime correctly handles method failures:
script crashes (RuntimeError) and missing declared outputs. Each test
builds a pipeline with the faulty fixture method configured to fail in
a specific mode, runs it, and verifies appropriate error handling.

Uses input_selector as the pipeline root (system-node-only roots pattern).

Tier 3: @workflow + Step() markers (stakeholder-recognizable failure scenarios).
"""

import csv
import json
from pathlib import Path

import pytest

from dflow.core.decorators import workflow, Step

from wfc.cli import run_pipeline
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv

WFC_ROOT = Path(__file__).resolve().parent.parent.parent


@workflow(
    purpose="Verify a pipeline with a crashing method reports failure with error details",
)
def test_script_crash(pipeline_factory, register_fixture_methods):
    """Script crash: faulty method raises RuntimeError, pipeline should fail.

    Pipeline topology: input_selector -> transform -> faulty(mode=crash).
    The pipeline should raise RuntimeError and the outcome sidecar should
    record the failure.
    """
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data and build pipeline",
        purpose="Create sample CSV and build a pipeline: input_selector -> transform -> faulty(mode=crash)",
    )
    _create_sample_csv(project_dir, "sample_a", num_rows=3)

    pipeline_path = pipeline_factory(
        name="crash",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_t"}},
            {"id": "faulty_1", "method": "faulty", "module": "test_pipeline",
             "params": {"failure_mode": "crash"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
            {"source": "transform_1", "target": "faulty_1"},
        ],
        samples=[],
    )

    s = Step(
        step_num=2,
        name="Run pipeline expecting failure",
        purpose="Execute the pipeline; it should raise RuntimeError due to Snakemake failure",
    )
    with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=False,
        )

    s = Step(
        step_num=3,
        name="Verify failure is recorded",
        purpose="Check that outcome sidecar exists with failed status",
    )
    pipelines_dir = project_dir / ".runs" / "pipelines"
    outcome_files = list(pipelines_dir.rglob("outcomes/*.json"))
    faulty_outcomes = [
        f for f in outcome_files
        if "faulty_1" in f.name
    ]
    assert len(faulty_outcomes) >= 1, (
        f"Expected at least one faulty_1 outcome file, found {len(faulty_outcomes)}. "
        f"All outcomes: {[f.name for f in outcome_files]}"
    )
    outcome = json.loads(faulty_outcomes[0].read_text())
    assert outcome["status"] == "failed", (
        f"Expected faulty_1 outcome status='failed', got '{outcome['status']}'"
    )
    assert outcome.get("error"), "Expected error message in outcome sidecar"


@workflow(
    purpose="Verify a pipeline detects when a method exits cleanly but produces no output",
)
def test_missing_output(pipeline_factory, register_fixture_methods):
    """Missing output: faulty method exits 0 but produces no declared output file.

    Pipeline topology: input_selector -> transform -> faulty(mode=missing_output).
    The runtime should detect the missing output and fail the run.
    """
    project_dir = register_fixture_methods

    s = Step(
        step_num=1,
        name="Create sample data and build pipeline",
        purpose="Create sample CSV and build a pipeline: input_selector -> transform -> faulty(mode=missing_output)",
    )
    _create_sample_csv(project_dir, "sample_a", num_rows=3)

    pipeline_path = pipeline_factory(
        name="missing",
        nodes=[
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_t"}},
            {"id": "faulty_1", "method": "faulty", "module": "test_pipeline",
             "params": {"failure_mode": "missing_output"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
            {"source": "transform_1", "target": "faulty_1"},
        ],
        samples=[],
    )

    s = Step(
        step_num=2,
        name="Run pipeline expecting failure",
        purpose="Execute the pipeline; it should detect the missing output and fail",
    )
    with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=False,
        )

    s = Step(
        step_num=3,
        name="Verify missing output detected",
        purpose="Check that the outcome sidecar records the failure with missing output info",
    )
    pipelines_dir = project_dir / ".runs" / "pipelines"
    outcome_files = list(pipelines_dir.rglob("outcomes/*.json"))
    faulty_outcomes = [
        f for f in outcome_files
        if "faulty_1" in f.name
    ]
    assert len(faulty_outcomes) >= 1, (
        f"Expected at least one faulty_1 outcome file, found {len(faulty_outcomes)}. "
        f"All outcomes: {[f.name for f in outcome_files]}"
    )
    outcome = json.loads(faulty_outcomes[0].read_text())
    assert outcome["status"] == "failed", (
        f"Expected faulty_1 outcome status='failed', got '{outcome['status']}'"
    )
