"""E2E tests for Canvas workflow API endpoints.

Tests verify the HTTP contract for pipeline submission validation:
empty pipelines are rejected with 400 and unknown job IDs return 404.

Tier 2: @workflow(purpose=...) only (subsystem-level endpoint tests).
"""

import pytest

from axiom_annotations import workflow

from tests.conftest import register_test_method, stub_readiness_probes
from tests.fixtures.routes import canvas_client


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client(tmp_project, monkeypatch):
    """A canvas client over a project holding the registered ``transform``.

    The project is ``tmp_project`` -- a git repo with a committed HEAD --
    and ``transform`` (the fixture method the project already carries)
    goes in through production registration. Docker is stubbed to ``ok``
    because these tests must not depend on a live daemon; git stays real,
    so the run-readiness gate's ``check_git(project_root)`` probe runs
    against the served project -- the cross-check that the gate scopes git
    to the canvas project, not the server process cwd.
    """
    register_test_method(
        tmp_project, module_name="test_pipeline",
        method_dir=tmp_project / "methods" / "transform",
    )
    stub_readiness_probes(monkeypatch, docker="ok", git=None)
    return canvas_client(tmp_project, monkeypatch)


# =============================================================================
# Tests
# =============================================================================


@workflow(
    purpose="Verify POST /api/workflow/run with empty pipeline returns 400",
)
def test_empty_pipeline_returns_400(client):
    """Empty pipeline (no nodes) should be rejected with HTTP 400."""
    response = client.post("/api/workflow/run", json={
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
def test_unknown_job_returns_404(client):
    """Unknown job_id should return HTTP 404."""
    response = client.get("/api/workflow/status/nonexistent-job-id")
    assert response.status_code == 404, (
        f"Expected 404 for unknown job, got {response.status_code}: {response.text}"
    )
