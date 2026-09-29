"""Archive-status + manual-archive endpoint tests.

GET /api/wfc/archive-status reports unarchived-output counts straight from
the DB (progress truth is the DB); POST /api/wfc/cache/archive runs
archive_outputs on a background thread and 409s while a pipeline is in
flight (the end-of-run pass archives on its own).
"""

import threading

import pytest
from sqlmodel import select

from axiom_annotations import workflow

from tests.fixtures.routes import canvas_client, completed_run


@pytest.fixture
def client(tmp_project, monkeypatch):
    return canvas_client(tmp_project, monkeypatch)


@workflow(purpose="archive-status reports unarchived counts; POST cache/archive clears them")
def test_archive_status_counts_and_manual_archive(client, tmp_project, monkeypatch):
    """A run's NULL-hash outputs, as the record phase leaves them, show up in
    archive-status; a manual archive pass clears the counts and writes hashes."""
    from wfc.canvas import state
    from wfc.persistence import get_session, RunOutput

    run_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="arch_meth",
        module="arch_mod", sample="s1",
        outputs={"o0": ".parquet", "o1": ".parquet"},
    ).run_id

    body = client.get("/api/wfc/archive-status").json()
    assert body["state"] == "idle"
    assert body["unarchived_runs"] == 1
    assert body["unarchived_outputs"] == 2
    assert body["pipeline_running"] is False
    assert body["progress"] is None

    resp = client.post("/api/wfc/cache/archive")
    assert resp.status_code == 200

    state._archive_job["thread"].join(timeout=30)
    assert not state._archive_job["thread"].is_alive()

    body = client.get("/api/wfc/archive-status").json()
    assert body["state"] == "idle"
    assert body["unarchived_runs"] == 0
    assert body["unarchived_outputs"] == 0

    with get_session() as session:
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        assert rows
        assert all(r.content_hash is not None for r in rows)


def test_cache_archive_409_while_pipeline_running(client):
    """POST cache/archive is rejected while a pipeline run thread is alive,
    and no archive job is started."""
    from wfc.canvas import state

    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    state._active_jobs["pipe-1"] = {"thread": t}
    try:
        resp = client.post("/api/wfc/cache/archive")
        assert resp.status_code == 409
        assert state._archive_job["thread"] is None

        body = client.get("/api/wfc/archive-status").json()
        assert body["pipeline_running"] is True
    finally:
        release.set()
        t.join(timeout=5)
