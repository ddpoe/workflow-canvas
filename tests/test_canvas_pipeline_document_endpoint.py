"""Tests for the GET /api/pipelines/{pipeline_id}/document endpoint.

Read the literal pipeline.json that
was written to ``.runs/pipelines/<pipeline_id>/pipeline.json`` at submission
time and return it as JSON. 404 when the file does not exist (pipeline
never reached the run-generation stage).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, create_engine, select

from tests.fixtures.fakes import (
    bind_provider,
    fake_engine_process,
    stub_readiness_probes,
)
from wfc.canvas.state import _active_jobs
from wfc.persistence import Method, Run
from tests.fixtures.routes import canvas_client
from tests.harness import Scenario, build_project, node


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI test client over a project registration built at ``tmp_path``.

    Declares the one method the submission names, with the ``.csv`` output
    slot its enrichment reads, so the contract is one registration derived
    from the method directory. A WfcProvider rooted at the project is bound
    at the site as this module's declared shortcut: the document endpoint
    reads only ``_require_provider().project_root``, and binding a provider
    is the load endpoint's job, which the client does not prove.
    """
    build_project(
        Scenario(nodes=[node("preprocess", module="data_preprocessing",
                             outputs={"data": ".csv"})],
                 samples=["s1"]),
        root=tmp_path, monkeypatch=monkeypatch,
    )

    from wfc.canvas.wfc_provider import WfcProvider
    provider = WfcProvider(str(tmp_path))
    provider.load()
    bind_provider(monkeypatch, provider)

    return canvas_client(tmp_path, monkeypatch)


# =============================================================================
# Endpoint behaviour
# =============================================================================


def test_submitted_pipeline_flows_to_document_and_provider(client, tmp_path, monkeypatch):
    """Writer-reader agreement on a real submission.

    A real POST /api/workflow/run writes both pipeline.json and the
    pipeline.editable.json sidecar. The document endpoint must then return
    the submitted doc, and the history provider must surface the submitted
    name off the sidecar — the two readers agreeing with the one writer,
    with only the Snakemake spawn and the readiness probes stubbed.
    """
    stub_readiness_probes(monkeypatch, git="ok", docker="ok")

    payload = {
        "name": "demo",
        "nodes": [
            {"id": "sel_1", "type": "input_selector", "samples": ["s1"]},
            {"id": "preprocess_1", "type": "method", "method": "preprocess",
             "module": "data_preprocessing", "params": {"normalize": True}},
        ],
        "links": [{"source": "sel_1", "target": "preprocess_1"}],
        "samples": [],
    }

    with fake_engine_process():
        resp = client.post("/api/workflow/run", json=payload)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        # Drain the background thread so it can't race the Run seed below.
        _active_jobs[job_id]["thread"].join(timeout=15)

    # Reader 1: the document endpoint returns the submitted-and-enriched doc.
    doc = client.get(f"/api/pipelines/{job_id}/document")
    assert doc.status_code == 200
    body = doc.json()
    assert body["name"] == "demo"
    method_nodes = [n for n in body["nodes"] if n.get("type") != "input_selector"]
    assert [n["method"] for n in method_nodes] == ["preprocess"]

    # Reader 2: the provider surfaces the submitted name off the sidecar.
    # Seed the Run row the (stubbed) engine would have written so
    # get_all_runs includes this pipeline.
    engine = create_engine(f"sqlite:///{tmp_path / '.wfc' / 'wfc.db'}")
    with Session(engine) as session:
        method = session.exec(select(Method)).first()
        session.add(Run(
            method_id=method.id, pipeline_id=job_id,
            status="completed", sample="s1",
        ))
        session.commit()

    from wfc.canvas.wfc_provider import WfcProvider
    provider = WfcProvider(str(tmp_path))
    provider.load()
    by_pid = {r["pipelineId"]: r for r in provider.get_all_runs()}
    assert by_pid[job_id]["pipelineName"] == "demo"

    _active_jobs.clear()


def test_returns_404_when_pipeline_json_missing(client, tmp_path):
    """A pipeline_id with no on-disk document → 404 (covers the 'never
    reached run-generation' case the SPEC's empty-state copy refers to)."""
    # No file written for pipe_zzz.
    resp = client.get("/api/pipelines/pipe_zzz/document")
    assert resp.status_code == 404
