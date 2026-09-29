"""Canvas API route tests: the History tab's editable form, pickers and run edits.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``history-editable-form``,
``history-pickers`` and ``history-run-edits``.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from axiom_annotations import workflow
from sqlmodel import Session

from tests.fixtures.fakes import bind_provider
from wfc.canvas.wfc_provider import WfcProvider
from wfc.layout import pipeline_run_dir
from wfc.persistence import Method, Module, Run, RunOutput, Sample


@pytest.fixture
def provider(canvas_db, tmp_project, monkeypatch):
    """A provider over the test project, bound as the server's loaded project.

    Returns:
        The bound ``WfcProvider``.
    """
    return bind_provider(monkeypatch, WfcProvider(str(tmp_project)))


def _write_pipeline_files(project_root, pipeline_id: str, files: dict[str, str]) -> None:
    """Write the named files into one pipeline's run directory."""
    run_dir = pipeline_run_dir(project_root, pipeline_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (run_dir / name).write_text(text, encoding="utf-8")


EDITABLE = {"name": "p", "nodes": [], "links": [],
            "variables": {"quality": {"type": "number", "value": 0.9}}}
FROZEN = {"name": "p", "nodes": [], "links": []}


@pytest.mark.parametrize(
    "files, expected",
    [
        pytest.param({"pipeline.editable.json": json.dumps(EDITABLE),
                      "pipeline.json": json.dumps(FROZEN)}, EDITABLE, id="editable-form"),
        pytest.param({"pipeline.json": json.dumps(FROZEN)}, FROZEN, id="frozen-only"),
    ],
)
@workflow(
    purpose="A pipeline's editable form comes back with its variables, and a "
            "pipeline with only a frozen document gets that document instead",
)
def test_editable_form_falls_back_to_the_frozen_document(
    canvas_client, provider, files, expected
):
    _write_pipeline_files(provider.project_root, "pipe-1", files)

    resp = canvas_client.get("/api/workflow/pipe-1/editable")

    assert resp.status_code == 200, resp.text
    assert resp.json() == expected


@pytest.mark.parametrize(
    "files, expected_status",
    [
        pytest.param({}, 404, id="neither"),
        pytest.param({"pipeline.editable.json": "{not json"}, 500, id="unreadable"),
    ],
)
@workflow(
    purpose="The editable-form route answers 404 when a pipeline has neither "
            "document and 500 when the editable form cannot be parsed",
)
def test_editable_form_refuses_a_missing_or_unreadable_document(
    canvas_client, provider, files, expected_status
):
    _write_pipeline_files(provider.project_root, "pipe-1", files)

    resp = canvas_client.get("/api/workflow/pipe-1/editable")

    assert resp.status_code == expected_status, resp.text


@workflow(
    purpose="Over a loaded provider, the input selector's route lists every "
            "registered sample with its file details, and the run reference's "
            "route lists only completed runs with their method, module, sample, "
            "params, output slots and pipeline id",
)
def test_pickers_list_samples_and_completed_runs(canvas_client, canvas_db, provider):
    with Session(canvas_db) as session:
        mod = Module(name="features")
        session.add(mod)
        session.flush()
        meth = Method(name="regionprops", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        session.add(Sample(name="s1", source_path="/raw/s1.tif",
                           registered_path="data/samples/s1/s1.tif", file_type="tif",
                           file_size=10, registered_at=datetime(2026, 1, 2, 3, 4, 5)))
        completed = Run(method_id=meth.id, sample="s1", status="completed",
                        params={"channels": ["dapi"]}, pipeline_id="pipe-1")
        failed = Run(method_id=meth.id, sample="s1", status="failed",
                     params={"channels": ["gfp"]}, pipeline_id="pipe-1")
        session.add_all([completed, failed])
        session.flush()
        session.add_all([
            RunOutput(run_id=run.id, slot=name, output_name=name,
                      artifact_type="method_file")
            for run in (completed, failed) for name in ("table", "masks")
        ])
        session.commit()
        completed_id = completed.id

    samples = canvas_client.get("/api/wfc/samples")
    runs = canvas_client.get("/api/wfc/completed-runs")

    assert samples.status_code == 200, samples.text
    assert samples.json() == [{
        "name": "s1", "file_type": "tif", "registered_path": "data/samples/s1/s1.tif",
        "file_size": 10, "registered_at": "2026-01-02 03:04:05.000000",
        "description": None, "file_count": None,
    }]
    assert runs.status_code == 200, runs.text
    assert runs.json() == [{
        "id": str(completed_id), "method": "regionprops", "module": "features",
        "sample": "s1", "params": {"channels": ["dapi"]},
        "output_slots": ["table", "masks"], "pipeline_id": "pipe-1", "finished_at": "",
    }]


@pytest.fixture
def run_id(canvas_db):
    """One completed run with no label and no annotation.

    Returns:
        The run's id.
    """
    with Session(canvas_db) as session:
        mod = Module(name="features")
        session.add(mod)
        session.flush()
        meth = Method(name="regionprops", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        return run.id


@pytest.mark.parametrize(
    "set_patch, set_view, clear_patch, clear_view",
    [
        pytest.param({"nid": "best"}, {"nid": "best"}, {"nid": ""}, {"nid": "v1"}, id="nid"),
        pytest.param({"favorite": True}, {"favorite": True},
                     {"favorite": False}, {"favorite": False}, id="favorite"),
        pytest.param({"tags": ["keep", "qc"]}, {"tags": ["keep", "qc"]},
                     {"tags": []}, {"tags": []}, id="tags"),
        pytest.param({"archived": True}, {"archived": True},
                     {"archived": False}, {"archived": False}, id="archived"),
    ],
)
@workflow(
    purpose="Patching a run sets and then clears its label, favorite, tags and "
            "archive state, and each edit shows in the next runs payload: an "
            "empty label falls back to the auto version, and archiving sets the "
            "archive time that unarchiving clears",
)
def test_run_edits_are_written_and_reflected_in_the_runs_payload(
    canvas_client, provider, run_id, set_patch, set_view, clear_patch, clear_view
):
    def view_after(patch: dict) -> dict:
        patched = canvas_client.patch(f"/api/wfc/run/{run_id}", json=patch)
        assert patched.status_code == 200, patched.text
        assert patched.json() == {"ok": True}
        runs = canvas_client.get("/api/wfc/runs")
        assert runs.status_code == 200, runs.text
        (run,) = [r for r in runs.json() if r["id"] == str(run_id)]
        field = next(iter(patch))
        if field == "archived":
            return {"archived": run["archivedAt"] is not None}
        return {field: run[field]}

    assert view_after(set_patch) == set_view
    assert view_after(clear_patch) == clear_view


@pytest.mark.parametrize(
    "path_id, expected_status",
    [pytest.param("abc", 400, id="not-an-integer"), pytest.param("999999", 404, id="unknown-run")],
)
@workflow(
    purpose="Patching a run refuses an id that is not an integer with 400 and an "
            "unknown run with 404",
)
def test_run_edits_refuse_a_malformed_or_unknown_id(
    canvas_client, provider, run_id, path_id, expected_status
):
    resp = canvas_client.patch(f"/api/wfc/run/{path_id}", json={"favorite": True})

    assert resp.status_code == expected_status, resp.text
