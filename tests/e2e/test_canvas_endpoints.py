"""E2E tests for Canvas workflow API endpoints.

Tests verify the HTTP contract for pipeline submission and status tracking:
empty pipelines are rejected with 400, unknown job IDs return 404, and valid
pipeline submissions return the expected response shape.

Tier 2: @workflow(purpose=...) only (subsystem-level endpoint tests).
"""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from axiom_annotations import workflow

# Ensure static dist directory exists before importing the canvas server
# (the server mounts it at import time via StaticFiles).
_dist = Path(__file__).resolve().parent.parent.parent / "wfc" / "canvas" / "static" / "dist"
_dist.mkdir(parents=True, exist_ok=True)

from wfc.canvas.server import app, _active_jobs  # noqa: E402
from wfc.models import Module, Method, MethodContract  # noqa: E402


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def canvas_db(tmp_path, monkeypatch):
    """SQLite DB with registered fixture methods for canvas tests."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="test_pipeline", description="Test pipeline module")
        session.add(mod)
        session.flush()

        transform = Method(
            name="transform", module_id=mod.id,
            script_path="methods/transform/transform.py",
            env="container:demo",
        )
        session.add(transform)
        session.flush()

        mc = MethodContract(
            method_id=transform.id,
            input_slots={"data": {"type": ".csv", "required": True}},
            output_slots={"output": {"type": ".csv"}},
            params_schema={"suffix": {"type": "str", "default": "_transformed"}},
        )
        session.add(mc)
        session.commit()

    yield engine
    reset_engine()


def _git_init_committed(repo_dir: Path) -> None:
    """Initialize ``repo_dir`` as a git repo with a clean, committed HEAD."""
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo_dir, check=True,
                   capture_output=True)
    (repo_dir / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True,
                   capture_output=True, env=env)


@pytest.fixture
def canvas_client(canvas_db, tmp_path, monkeypatch):
    """FastAPI test client for canvas endpoints.

    Makes ``tmp_path`` (the canvas project_root) a real git repo with a clean
    HEAD so the D-6 run-readiness gate's ``check_git(project_root)`` probe
    passes against the resolved project_root — this is the cross-check that the
    gate scopes git to the canvas project, not the server process cwd.  Docker
    is stubbed to ``ok`` because the lifecycle test must not depend on a live
    daemon.
    """
    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_path))

    _git_init_committed(tmp_path)

    from wfc import preflight
    monkeypatch.setattr(
        preflight, "check_docker",
        lambda *a, **k: preflight.CheckResult("docker", "ok", "ok"),
    )

    _active_jobs.clear()
    return TestClient(app, raise_server_exceptions=False)


# =============================================================================
# Tests
# =============================================================================


@workflow(
    purpose="Verify POST /api/workflow/run with empty pipeline returns 400",
)
def test_empty_pipeline_returns_400(canvas_client):
    """Empty pipeline (no nodes) should be rejected with HTTP 400."""
    response = canvas_client.post("/api/workflow/run", json={
        "name": "empty",
        "nodes": [],
        "links": [],
        "samples": [],
    })
    assert response.status_code == 400, (
        f"Expected 400 for empty pipeline, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "detail" in body, f"Expected error detail in response: {body}"


@workflow(
    purpose="Verify GET /api/workflow/status/<unknown_id> returns 404",
)
def test_unknown_job_returns_404(canvas_client):
    """Unknown job_id should return HTTP 404."""
    response = canvas_client.get("/api/workflow/status/nonexistent-job-id")
    assert response.status_code == 404, (
        f"Expected 404 for unknown job, got {response.status_code}: {response.text}"
    )


@workflow(
    purpose="Verify valid pipeline submission returns job_id and correct response shape",
)
def test_valid_pipeline_run_lifecycle(canvas_client):
    """Valid pipeline submit returns 200 with job_id and status fields.

    We verify the immediate submission response shape. Polling until
    completion is not tested here because it requires a real Snakemake
    execution environment which is out of scope for Tier 2 endpoint tests.
    """
    response = canvas_client.post("/api/workflow/run", json={
        "name": "test_run",
        "nodes": [
            {"id": "selector_1", "type": "input_selector",
             "samples": ["sample_a"]},
            {"id": "transform_1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": "_t"}},
        ],
        "links": [
            {"source": "selector_1", "target": "transform_1"},
        ],
        "samples": [],
    })
    assert response.status_code == 200, (
        f"Expected 200 for valid pipeline, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "job_id" in body, f"Expected job_id in response: {body}"
    assert "status" in body, f"Expected status in response: {body}"
    assert body["status"] == "submitted", f"Expected status='submitted', got '{body['status']}'"
    assert "step_map" in body, f"Expected step_map in response: {body}"

    # Verify status endpoint is reachable for this job
    job_id = body["job_id"]
    # Brief wait for the background thread to start
    time.sleep(0.5)
    status_response = canvas_client.get(f"/api/workflow/status/{job_id}")
    assert status_response.status_code == 200, (
        f"Expected 200 for status check, got {status_response.status_code}"
    )
    status_body = status_response.json()
    assert "overall_status" in status_body, f"Expected overall_status: {status_body}"
    assert "steps" in status_body, f"Expected steps dict: {status_body}"
    assert "job_id" in status_body, f"Expected job_id: {status_body}"
