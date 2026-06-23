"""Tests for the GET /api/pipelines/{pipeline_id}/document endpoint.

Action 1 of the load-in-canvas cycle: read the literal pipeline.json that
was written to ``.runs/pipelines/<pipeline_id>/pipeline.json`` at submission
time and return it as JSON. 404 when the file does not exist (pipeline
never reached the run-generation stage).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from wfc.canvas import server as canvas_server
from wfc.canvas.server import app, _active_jobs


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI test client with an empty wfc DB rooted at ``tmp_path``.

    Action 1 reads pipeline.json from disk; we don't need DB seeding for
    these endpoint tests, only a project_root that has been registered
    with the server's WfcProvider so ``_require_provider().project_root``
    resolves to ``tmp_path``.
    """
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    # Install a WfcProvider rooted at tmp_path. The Action 1 endpoint only
    # uses provider.project_root; load() needs to succeed so the provider
    # is in a consistent state, but no rows are required.
    from wfc.canvas.wfc_provider import WfcProvider
    provider = WfcProvider(str(tmp_path))
    provider.load()
    monkeypatch.setattr(canvas_server, "_wfc_provider", provider)

    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


def _write_pipeline_json(project_root: Path, pipeline_id: str, payload: dict) -> Path:
    """Write a pipeline.json under ``.runs/pipelines/<id>/`` and return the path."""
    target = project_root / ".runs" / "pipelines" / pipeline_id / "pipeline.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# =============================================================================
# Endpoint behaviour
# =============================================================================


def test_returns_literal_pipeline_json(client, tmp_path):
    """When pipeline.json exists on disk the endpoint returns it verbatim."""
    payload = {
        "name": "demo_pipeline",
        "nodes": [
            {
                "id": "node_1",
                "type": "method",
                "method": "preprocess",
                "module": "data_preprocessing",
                "params": {"normalize": True},
                "position": {"x": 100, "y": 200},
            }
        ],
        "links": [],
        "samples": ["s1", "s2"],
    }
    _write_pipeline_json(tmp_path, "pipe_abc", payload)

    resp = client.get("/api/pipelines/pipe_abc/document")
    assert resp.status_code == 200
    assert resp.json() == payload


def test_returns_404_when_pipeline_json_missing(client, tmp_path):
    """A pipeline_id with no on-disk document → 404 (covers the 'never
    reached run-generation' case the SPEC's empty-state copy refers to)."""
    # No file written for pipe_zzz.
    resp = client.get("/api/pipelines/pipe_zzz/document")
    assert resp.status_code == 404
