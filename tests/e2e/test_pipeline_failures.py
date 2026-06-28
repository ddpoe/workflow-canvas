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

from axiom_annotations import workflow, Step

from wfc.cli import run_pipeline
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv
from tests.conftest import requires_docker

WFC_ROOT = Path(__file__).resolve().parent.parent.parent

# ADR-019 Cycle H: these tests execute pipelines end-to-end through the
# container dispatch path (register_fixture_methods builds a real image).
# Deselected from the default suite (integration) and skipped without Docker.
pytestmark = [pytest.mark.integration, requires_docker]


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

    s = Step(
        step_num=4,
        name="Verify the failure is pinned to faulty_1 and upstream output survived",
        purpose="The crash must be attributed to faulty_1 (not transform_1); the "
                "upstream transform_1 output must persist — a downstream crash must "
                "not corrupt or roll back already-produced upstream provenance",
    )
    # Pin: no transform_1 (the upstream node) outcome is marked failed.
    transform_outcomes = [
        json.loads(f.read_text()) for f in outcome_files if "transform_1" in f.name
    ]
    assert all(o["status"] != "failed" for o in transform_outcomes), (
        f"transform_1 should not be failed — only faulty_1 crashed. "
        f"transform_1 outcomes: {transform_outcomes}"
    )
    # The upstream transform_1 output must survive the downstream crash: its
    # RunOutput row exists and its staging artifact is still on disk.
    from wfc.database import get_session
    from wfc.models import Method, Run, RunOutput
    from sqlmodel import select
    with get_session() as session:
        rows = session.exec(
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform")
            .where(Run.status == "completed")
        ).all()
        survived = [r.artifact_path for r in rows]
    assert survived, "transform_1 produced no surviving RunOutput despite completing before the crash"
    assert any(Path(p).exists() for p in survived), (
        f"transform_1's upstream output was lost when faulty_1 crashed: {survived}"
    )


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

    s = Step(
        step_num=4,
        name="Verify it is a MISSING DECLARED OUTPUT detection, not a generic crash",
        purpose="faulty(missing_output) exits 0; the runtime must fail it specifically "
                "because the declared output file is absent — distinct from the "
                "exception-crash path. The error must name the missing output.",
    )
    # The faulty method exited 0 with no exception. A generic-crash detector
    # would let this pass silently; the contract is that the declared output's
    # absence is itself the failure. The error text must reference the missing
    # output (the declared slot/filename), not a Python traceback.
    error_text = (outcome.get("error") or "").lower()
    assert error_text, "missing-output failure produced no error message"
    assert ("output" in error_text and "missing" in error_text) or "output.csv" in error_text, (
        f"error does not identify a missing DECLARED output (looks like a generic "
        f"crash): {outcome.get('error')!r}"
    )


# =============================================================================
# Negative/edge (US-6): non-zero method exit (no Python exception raised)
# =============================================================================

@workflow(
    purpose="Negative/edge: a method that exits non-zero WITHOUT raising fails the pipeline",
)
def test_nonzero_method_exit_fails_pipeline(pipeline_factory, register_fixture_methods):
    """faulty(mode=nonzero_exit) calls sys.exit(3) with no exception.

    Distinct from test_script_crash (which raises a RuntimeError): here the
    method process simply returns a non-zero status. The runtime must still
    fail the pipeline and record faulty_1 as failed — a non-zero exit is never
    silently treated as success.
    """
    project_dir = register_fixture_methods
    _create_sample_csv(project_dir, "sample_a", num_rows=3)

    pipeline_path = pipeline_factory(
        name="nonzero_exit",
        nodes=[
            {"id": "selector_1", "type": "input_selector", "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_t"}},
            {"id": "faulty_1", "method": "faulty", "module": "test_pipeline",
             "params": {"failure_mode": "nonzero_exit", "exit_code": 3}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
            {"source": "transform_1", "target": "faulty_1"},
        ],
        samples=[],
    )

    with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=False,
        )

    pipelines_dir = project_dir / ".runs" / "pipelines"
    outcome_files = list(pipelines_dir.rglob("outcomes/*.json"))
    faulty_outcomes = [
        json.loads(f.read_text()) for f in outcome_files if "faulty_1" in f.name
    ]
    assert faulty_outcomes, "no faulty_1 outcome recorded for non-zero exit"
    assert all(o["status"] == "failed" for o in faulty_outcomes), (
        f"non-zero method exit not recorded as failed: {faulty_outcomes}"
    )
