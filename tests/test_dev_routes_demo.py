"""Smoke tests for the dev toolbar's demo topologies (scripts/dev_routes.py).

Every topology the dropdown lists is fetched and pushed through the canvas's
structural validation against the real fixture methods, so a typo'd slot or
method name fails here rather than one dropdown entry at a time. The two
deliberately refused shapes must be refused by the rule they exist to show.
The reference seeding is driven with the submission seam replaced by a
stand-in that records a completed run, so no Docker is needed.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.conftest import pin_project_root
from tests.fixtures.conftest import init_test_project, register_test_method, write_env_record
from tests.fixtures.fakes import stub_reference_seeding, stub_seed_submission
from tests.fixtures.routes import canvas_client

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "methods"
IMAGING_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "methods_imaging"


@pytest.fixture(scope="module")
def demo_project(tmp_path_factory) -> Path:
    """A project with every dev.py fixture method registered, built once."""
    root = tmp_path_factory.mktemp("demo_project")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    for key, value in (("user.email", "wfc@wfc"), ("user.name", "wfc")):
        subprocess.run(["git", "config", key, value], cwd=root, check=True,
                       capture_output=True)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", f"sqlite:///{root / '.wfc' / 'wfc.db'}")
        pin_project_root(mp, root)
        init_test_project(root)
        write_env_record(root, "fixture-env")
        for fixtures_dir, module in ((RAW_FIXTURES, "test_pipeline"),
                                     (IMAGING_FIXTURES, "imaging_demo")):
            for src in sorted(p for p in fixtures_dir.iterdir() if p.is_dir()
                              and (p / "method.yaml").exists()):
                dest = root / "methods" / src.name
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
                register_test_method(root, module_name=module, method_dir=dest)
    return root


@pytest.fixture
def pinned_project(demo_project, monkeypatch) -> Iterator[Path]:
    """The demo project pinned as the served project, dev routes mounted."""
    from scripts import dev_routes
    from wfc.canvas.server import app
    from wfc.canvas.state import _active_jobs

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{demo_project / '.wfc' / 'wfc.db'}")
    pin_project_root(monkeypatch, demo_project)
    monkeypatch.chdir(demo_project)
    _active_jobs.clear()
    # The app object is shared by every test in the process, and
    # mount_dev_routes replaces production routes with dev mocks (the
    # cache-status route among them). Mount on the test's own copy of the
    # route table and put the production table back afterwards, so no later
    # test is served a dev mock.
    saved_routes = list(app.router.routes)
    saved_schema = app.openapi_schema
    dev_routes.mount_dev_routes(app)
    try:
        yield demo_project
    finally:
        app.router.routes[:] = saved_routes
        app.openapi_schema = saved_schema


@pytest.fixture
def client(pinned_project, monkeypatch) -> TestClient:
    """A test client whose reference topologies skip seeding.

    Validation never reads a reference's run id, so the seeding path is
    stubbed here and driven on its own further down.
    """
    stub_reference_seeding(monkeypatch, "1")
    return canvas_client(pinned_project, monkeypatch)


def _validate(client: TestClient, topology: str) -> dict:
    fetched = client.get("/api/dev/demo-pipeline", params={"topology": topology})
    assert fetched.status_code == 200, fetched.text
    verdict = client.post("/api/workflow/validate", json=fetched.json())
    assert verdict.status_code == 200, verdict.text
    return verdict.json()


def _accepted_topologies() -> list[str]:
    from scripts.dev_routes import DEMO_TOPOLOGIES, REFUSED_TOPOLOGIES
    return sorted(set(DEMO_TOPOLOGIES) - REFUSED_TOPOLOGIES)


@pytest.mark.parametrize("topology", _accepted_topologies())
def test_every_accepted_topology_validates_against_the_fixtures(client, topology):
    verdict = _validate(client, topology)
    assert verdict["valid"], f"{topology}: {verdict['errors']}"


def test_collapsed_beside_per_sample_is_refused_by_the_sole_upstream_rule(client):
    verdict = _validate(client, "collapsed_beside_per_sample")
    assert not verdict["valid"]
    assert any("sole upstream" in e for e in verdict["errors"]), verdict["errors"]


def test_two_method_edges_into_one_slot_are_refused_by_the_one_edge_rule(client):
    verdict = _validate(client, "fan_in_one_slot")
    assert not verdict["valid"]
    assert any("only one edge per slot" in e for e in verdict["errors"]), verdict["errors"]


def test_required_slot_unfed_validates_with_a_warning_naming_the_slot(client):
    verdict = _validate(client, "required_slot_unfed")
    assert verdict["valid"]
    assert any("'corrected'" in w for w in verdict["warnings"]), verdict["warnings"]


def test_unknown_topology_is_a_400_listing_the_choices(client):
    resp = client.get("/api/dev/demo-pipeline", params={"topology": "nope"})
    assert resp.status_code == 400
    assert "selector_root" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Reference seeding
# ---------------------------------------------------------------------------

def _finished_job(error: dict | None = None) -> dict:
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return {"thread": thread, "error": error}


def _record_completed_run(method_name: str, sample: str) -> int:
    from sqlmodel import select
    from wfc.persistence import get_session, Method, Run

    with get_session() as session:
        method = session.exec(select(Method).where(Method.name == method_name)).first()
        run = Run(method_id=method.id, sample=sample, status="completed", params={})
        session.add(run)
        session.commit()
        return int(run.id)


def test_reference_seeding_submits_once_and_binds_the_completed_run(pinned_project, monkeypatch):
    from scripts import dev_routes
    from wfc.canvas.state import _active_jobs

    submitted: list[dict] = []

    def fake_submit(payload):
        submitted.append(payload)
        run_id = _record_completed_run(payload["nodes"][1]["method"], payload["samples"][0])
        _active_jobs[f"seed-{run_id}"] = _finished_job()
        return f"seed-{run_id}"

    stub_seed_submission(monkeypatch, fake_submit)

    first = dev_routes._ensure_reference_run("transform")
    second = dev_routes._ensure_reference_run("transform")

    assert len(submitted) == 1, "the second call found the seeded run; nothing to submit"
    assert first == second
    assert submitted[0]["samples"] == [dev_routes.SEED_SAMPLE]
    assert submitted[0]["nodes"][1]["method"] == "transform"


def test_a_seed_that_fails_surfaces_its_pipeline_id(pinned_project, monkeypatch):
    from scripts import dev_routes
    from wfc.canvas.state import _active_jobs

    def failing_submit(payload):
        _active_jobs["seed-broken"] = _finished_job(error={"kind": "method_failed",
                                                          "message": "boom"})
        return "seed-broken"

    stub_seed_submission(monkeypatch, failing_submit)

    with pytest.raises(HTTPException) as excinfo:
        dev_routes._ensure_reference_run("scale")
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail["seed_pipeline_id"] == "seed-broken"
    assert excinfo.value.detail["message"] == "boom"
