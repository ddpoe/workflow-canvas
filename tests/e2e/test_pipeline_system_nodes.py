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

from axiom_annotations import workflow, Step

from wfc.cli import run_pipeline
from wfc.database import get_session
from wfc.models import Run, RunOutput
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv
from tests.conftest import requires_docker

WFC_ROOT = Path(__file__).resolve().parent.parent.parent

# ADR-019 Cycle H: these tests execute pipelines end-to-end through the
# container dispatch path (register_fixture_methods builds a real image).
# Deselected from the default suite (integration) and skipped without Docker.
pytestmark = [pytest.mark.integration, requires_docker]


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
        name="Verify the SELECTED sample's identity propagated through",
        purpose="Output must carry sel_sample's actual id/value rows — proving the "
                "input_selector fed THAT sample's data, not an empty or wrong table",
    )
    with open(output_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    assert "computed_s" in fieldnames, f"Expected computed_s column, got {fieldnames}"
    # create_sample_csv seeds sel_sample with rows id=0..2, value=id*10. If the
    # input_selector merged the SELECTED sample, that exact content flows into
    # transform's output. Asserting the id/value pairs (not just the row count)
    # ties the output to sel_sample's identity — a mis-selected or empty sample
    # would not reproduce this content.
    assert {"id", "value"}.issubset(fieldnames), f"sample columns missing: {fieldnames}"
    seen = {(r["id"], r["value"]) for r in rows}
    assert seen == {("0", "0"), ("1", "10"), ("2", "20")}, (
        f"selected sample's id/value content did not propagate: {sorted(seen)}"
    )
    # And transform's computed column is derived per-row from the selected ids.
    assert all(r["computed_s"] == f"v_{r['id']}" for r in rows), (
        f"computed_s not derived from selected sample ids: "
        f"{[(r['id'], r['computed_s']) for r in rows]}"
    )


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
        name="Verify transform_2 consumed THE referenced run's content",
        purpose="transform_2 output must carry the prior run's computed_init column — "
                "content unique to the referenced run, not just any 4-row table",
    )
    transform_2_output = _find_run_output_path(
        nf_process_name="transform", sample="ref_sample", output_name="output.csv",
    )
    assert transform_2_output.exists(), f"transform_2 output missing: {transform_2_output}"
    with open(transform_2_output, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    # transform_1 (the referenced run) tagged its output with `computed_init`
    # (suffix=_init). If the run_reference resolved to the wired prior run, that
    # column survives into transform_2's output. A bad/mis-wired reference would
    # feed transform_2 some OTHER table lacking computed_init — so this column,
    # not the row count, is the load-bearing identity of the referenced run.
    assert "computed_init" in fieldnames, (
        f"transform_2 output lacks the referenced run's computed_init column — "
        f"run_reference did not resolve to the wired prior run. Columns: {fieldnames}"
    )
    assert "computed_ref" in fieldnames, f"Expected transform_2's own computed_ref column, got {fieldnames}"
    assert len(rows) == 4, f"Expected 4 rows from prior transform, got {len(rows)}"
    # The prior run's per-row computed values (v_<id>) must be carried verbatim,
    # tying the served content byte-for-byte to the referenced run's data.
    assert all(r["computed_init"] == f"v_{r['id']}" for r in rows), (
        f"computed_init values are not the referenced run's per-row content: "
        f"{[(r['id'], r['computed_init']) for r in rows]}"
    )


# =============================================================================
# Negative/edge (US-6)
# =============================================================================

@workflow(
    purpose="Negative/edge: run_reference pointing at a missing output_path FAILs the pipeline",
)
def test_run_reference_bad_path_fails(pipeline_factory, register_fixture_methods):
    """A run_reference whose output_path does not exist must fail, not silently
    feed an empty/garbage input downstream.

    The reference points at a path that was never produced. The runtime must
    refuse to resolve it — wrong bytes (or no bytes) are never served as if valid.
    """
    project_dir = register_fixture_methods
    _create_sample_csv(project_dir, "bad_ref_sample", num_rows=3)

    missing_path = str(project_dir / "does_not_exist" / "phantom.csv")
    pipeline_path = pipeline_factory(
        name="badref",
        nodes=[
            {"id": "runref_1", "type": "run_reference",
             "run_id": "", "output_slot": "output",
             "output_path": missing_path},
            {"id": "transform_2", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_ref"}},
        ],
        links=[
            {"source": "runref_1", "target": "transform_2", "target_slot": "data"},
        ],
        samples=["bad_ref_sample"],
    )

    with pytest.raises((RuntimeError, FileNotFoundError, ValueError)):
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=False,
        )


@workflow(
    purpose="Negative/edge: input_selector with an empty samples list runs no method work",
)
def test_input_selector_empty_samples(pipeline_factory, register_fixture_methods):
    """An input_selector with samples=[] selects nothing — the downstream method
    must not execute against a phantom sample.

    A correct selector produces zero downstream runs (nothing selected); it must
    never fabricate a run over a non-existent sample. We assert no transform
    RunOutput rows are produced.
    """
    project_dir = register_fixture_methods

    pipeline_path = pipeline_factory(
        name="empty_selector",
        nodes=[
            {"id": "selector_1", "type": "input_selector", "samples": []},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_s"}},
        ],
        links=[
            {"source": "selector_1", "target": "transform_1"},
        ],
        samples=[],
    )

    # An empty selection yields no work. Whether the runtime no-ops or raises,
    # the invariant is the same: no transform output is produced over a phantom
    # sample.
    try:
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=False,
        )
    except (RuntimeError, ValueError):
        pass  # acceptable: nothing-to-run is allowed to surface as an error

    with get_session() as session:
        from wfc.models import Method
        rows = session.exec(
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform")
            .where(Run.status == "completed")
        ).all()
        produced = [r.output_name for r in rows]
    assert not produced, (
        f"input_selector with empty samples produced transform output over a "
        f"phantom sample: {produced}"
    )
