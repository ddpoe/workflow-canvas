"""Tests for GET /api/runs/{run_id}/lineage-pipeline.

Action 2: synthesize a literal-only lineage pipeline from the run-DAG.
Returns 200 with the synthesized JSON, 404 if the run id is unknown,
422 if synthesis fails (cycle defense).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from wfc.canvas import server as canvas_server
from wfc.canvas.server import app, _active_jobs
from wfc.canvas.wfc_provider import WfcRun


class _StubProvider:
    """Provider double that exposes the surface the lineage endpoint reads.

    The endpoint calls ``provider.get_run(run_id)`` (404 if None) and
    ``synthesize_lineage_pipeline(provider, run_id)`` which reads
    ``provider._runs``.
    """

    def __init__(self, runs):
        self._runs = {r.id: r for r in runs}
        self.project_root = None

    def get_run(self, run_id):
        run = self._runs.get(run_id)
        return run.to_dict() if run is not None else None


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with a stub WfcProvider seeded directly into the server."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))
    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)

    a = WfcRun(
        id="1", module="m", method="load", dataSource="s1",
        parentRunIds=[], parents=[], inputs={}, pipelineId="p1",
    )
    b = WfcRun(
        id="2", module="m", method="filter", dataSource="s1",
        parentRunIds=["1"], parents=[{"slot": "data", "sourceRunId": "1"}],
        inputs={"min": 0.5}, pipelineId="p1",
    )
    monkeypatch.setattr(canvas_server, "_wfc_provider", _StubProvider([a, b]))
    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


def test_returns_synthesized_pipeline_for_known_run(client):
    """A known run id returns 200 with a literal-only pipeline JSON whose
    method nodes match the ancestor chain."""
    resp = client.get("/api/runs/2/lineage-pipeline")
    assert resp.status_code == 200
    data = resp.json()
    methods = sorted(n["method"] for n in data["nodes"] if n.get("type") == "method")
    assert methods == ["filter", "load"]
    assert data["samples"] == ["s1"]


def test_returns_404_for_unknown_run(client):
    """An unknown run id is the lineage endpoint's 404 case (D — distinct
    from 422 which is reserved for synthesis failure)."""
    resp = client.get("/api/runs/9999/lineage-pipeline")
    assert resp.status_code == 404
