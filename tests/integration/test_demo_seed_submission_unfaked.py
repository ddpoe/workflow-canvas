"""The demo's reference seed submitted through the canvas for real.

Everywhere else the seed submission is replaced by a stand-in that records a
completed run. Here the demo route for a reference-rooted topology finds no
completed run of the method it references, submits the seed pipeline through
the canvas's own submission path, waits for it to run in the fixture
container, and binds the run the engine recorded.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from tests.conftest import pin_project_root, requires_docker
from tests.fixtures.conftest import create_sample_csv

pytestmark = [pytest.mark.integration, requires_docker]


@pytest.fixture
def app_with_dev_routes():
    """The canvas app with the ``/api/dev/*`` routes mounted, for one test.

    ``mount_dev_routes`` pops the catch-all static mount and re-appends it
    after the new routes, on the app object every test in the process
    shares. The route table and the cached OpenAPI schema as they stood
    are put back when the test ends, so the mount does not outlive it. A
    mount already present (another module mounted it) is left as found.

    Yields:
        The canvas ``FastAPI`` app with the dev routes mounted.
    """
    from scripts import dev_routes
    from wfc.canvas.server import app

    routes = list(app.router.routes)
    schema = app.openapi_schema
    if not any(getattr(r, "path", "") == "/api/dev/demo-pipeline"
               for r in routes):
        dev_routes.mount_dev_routes(app)
    yield app
    app.router.routes[:] = routes
    app.openapi_schema = schema


def test_the_reference_root_demo_seeds_a_real_run_through_the_canvas(
        register_fixture_methods, app_with_dev_routes, monkeypatch):
    """The fetched topology's reference names a completed run the seed produced."""
    from scripts import dev_routes
    from wfc.canvas.state import _active_jobs
    from wfc.persistence import Method, Run, get_session

    project_dir = register_fixture_methods
    create_sample_csv(project_dir, dev_routes.SEED_SAMPLE, num_rows=3)
    pin_project_root(monkeypatch, project_dir)
    _active_jobs.clear()
    client = TestClient(app_with_dev_routes, raise_server_exceptions=False)

    res = client.get("/api/dev/demo-pipeline",
                     params={"topology": "reference_root"})

    assert res.status_code == 200, res.text
    ref = next(n for n in res.json()["nodes"] if n["type"] == "run_reference")
    with get_session() as session:
        run = session.get(Run, int(ref["run_id"]))
        method = session.get(Method, run.method_id)
        seeds = session.exec(select(Run).where(Run.method_id == run.method_id)).all()
    assert (method.name, run.sample, run.status) == (
        "transform", dev_routes.SEED_SAMPLE, "completed")
    assert run.pipeline_id in _active_jobs, "the run came from a canvas submission"
    assert len(seeds) == 1
    _active_jobs.clear()
