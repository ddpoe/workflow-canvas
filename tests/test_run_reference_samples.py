"""Focused tests for run_reference sample inheritance and multi-output wiring.

Covers three load-bearing behaviours introduced to close the bug where a
pipeline rooted solely at a ``run_reference`` generated a zero-job Snakefile
(``SAMPLES = []`` → silent exit 0 → no Run rows):

1. ``_resolve_run_reference_paths`` populates ``sample`` from ``Run.sample``
   and ``output_paths`` as a {slot: artifact_path} dict for every RunOutput
   of the referenced run.
2. ``load_pipeline`` merges each run_reference's resolved sample into the
   pipeline sample list the same way ``input_selector`` already does, and
   raises when a method-node pipeline would end up with zero samples.
3. ``load_pipeline``'s run_ref_links loop picks the artifact path for each
   edge by the link's ``source_slot``, so a multi-output run_reference fans
   different outputs into different downstream inputs correctly.
"""

import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from wfc.models import Method, Module, Run, RunOutput
from wfc.snakemake_gen import _resolve_run_reference_paths, load_pipeline


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """SQLite DB with a completed Run owning two outputs on sample 'SJ011'."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="analysis")
        session.add(mod)
        session.flush()

        meth = Method(
            name="regionprops_quantification",
            module_id=mod.id,
            script_path="methods/regionprops_quantification/x.py",
        )
        session.add(meth)
        session.flush()

        run = Run(
            method_id=meth.id,
            sample="SJ011",
            status="completed",
        )
        session.add(run)
        session.flush()

        session.add_all([
            RunOutput(
                run_id=run.id,
                output_name="measurements",
                artifact_path="/work/run62/measurements.csv",
                artifact_type="method_file",
            ),
            RunOutput(
                run_id=run.id,
                output_name="labels",
                artifact_path="/work/run62/labels.csv",
                artifact_type="method_file",
            ),
        ])
        session.commit()
        run_id = run.id

    yield engine, run_id
    reset_engine()


def test_resolve_populates_sample_and_all_output_paths(db_engine):
    """DB lookup fills in Run.sample and every RunOutput keyed by slot name."""
    _, run_id = db_engine
    out = _resolve_run_reference_paths({
        "node_ref": {"run_id": str(run_id), "output_slot": "", "output_path": ""},
    })

    info = out["node_ref"]
    assert info["sample"] == "SJ011"
    assert info["output_paths"] == {
        "measurements": "/work/run62/measurements.csv",
        "labels": "/work/run62/labels.csv",
    }


def test_run_reference_root_inherits_sample(tmp_path, db_engine):
    """A run_reference-rooted pipeline compiles to SAMPLES=[Run.sample].

    Regression: before the fix, samples stayed [] and the generated
    Snakefile's rule all expand(..., sample=[]) ran zero jobs.
    """
    _, run_id = db_engine
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "method": "", "module": "", "params": {},
                "run_id": str(run_id),
            },
            {
                "id": "method_1", "type": "method",
                "method": "binary_feature_labeling", "module": "analysis",
                "script": "methods/binary_feature_labeling/x.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1",
             "source_slot": "measurements", "target_slot": "measurements"},
        ],
        "samples": [],
    }
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(pipeline_json))

    pipeline = load_pipeline(path)

    assert pipeline.samples == ["SJ011"]
    assert len(pipeline.steps) == 1
    step = pipeline.steps[0]
    # Label is the link's target_slot so the downstream method finds the
    # artifact under the slot it actually reads (not a synthetic label).
    assert step.run_ref_inputs == {"measurements": "/work/run62/measurements.csv"}


def test_load_pipeline_raises_when_pipeline_resolves_to_zero_samples(tmp_path):
    """No input_selector, run_reference with unknown run_id → clear error.

    This is the defensive gate: a zero-sample method-node pipeline would
    otherwise silently generate an empty Snakefile DAG.
    """
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "method": "", "module": "", "params": {},
                "run_id": "999999",  # intentionally does not exist in DB
            },
            {
                "id": "method_1", "type": "method",
                "method": "foo", "module": "analysis",
                "script": "methods/foo/foo.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1"},
        ],
        "samples": [],
    }
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(pipeline_json))

    with pytest.raises(ValueError, match="zero samples"):
        load_pipeline(path)


def test_multi_output_per_edge_source_slot(tmp_path, db_engine):
    """Each outgoing run_reference edge picks its own artifact by source_slot.

    Two method nodes each wire a different output of the same run. The
    engine must inject distinct run_ref paths — not duplicate the same
    one — on each downstream StepDef.
    """
    _, run_id = db_engine
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "method": "", "module": "", "params": {},
                "run_id": str(run_id),
            },
            {
                "id": "method_m", "type": "method",
                "method": "consume_measurements", "module": "analysis",
                "script": "methods/consume_measurements/x.py",
                "params": {},
            },
            {
                "id": "method_l", "type": "method",
                "method": "consume_labels", "module": "analysis",
                "script": "methods/consume_labels/x.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_m",
             "source_slot": "measurements", "target_slot": "data"},
            {"source": "ref_1", "target": "method_l",
             "source_slot": "labels", "target_slot": "data"},
        ],
        "samples": [],
    }
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(pipeline_json))

    pipeline = load_pipeline(path)

    step_by_id = {s.node_id: s for s in pipeline.steps}
    assert step_by_id["method_m"].run_ref_inputs == {
        "data": "/work/run62/measurements.csv",
    }
    assert step_by_id["method_l"].run_ref_inputs == {
        "data": "/work/run62/labels.csv",
    }


def test_legacy_output_slot_still_resolves(tmp_path, db_engine):
    """Old pipelines authored with singular ``output_slot`` still work.

    Backcompat check: the pre-multi-output format had ``output_slot`` on
    the node and no ``source_slot`` on the edge. The engine falls back to
    the legacy per-node single output path.
    """
    _, run_id = db_engine
    pipeline_json = {
        "nodes": [
            {
                "id": "ref_1", "type": "run_reference",
                "method": "", "module": "", "params": {},
                "run_id": str(run_id),
                "output_slot": "measurements",
            },
            {
                "id": "method_1", "type": "method",
                "method": "foo", "module": "analysis",
                "script": "methods/foo/foo.py",
                "params": {},
            },
        ],
        "links": [
            {"source": "ref_1", "target": "method_1"},
        ],
        "samples": [],
    }
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(pipeline_json))

    pipeline = load_pipeline(path)
    assert pipeline.samples == ["SJ011"]
    # Legacy link with no target_slot falls back to the synthetic label.
    assert pipeline.steps[0].run_ref_inputs == {
        "run_ref_0": "/work/run62/measurements.csv",
    }
