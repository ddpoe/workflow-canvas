"""Archive-status + manual-archive endpoint tests.

GET /api/wfc/archive-status reports unarchived-output counts straight from
the DB (progress truth is the DB); POST /api/wfc/cache/archive runs
archive_outputs on a background thread and 409s while a pipeline is in
flight (the end-of-run pass archives on its own).
"""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from axiom_annotations import workflow


@pytest.fixture
def client(tmp_project, monkeypatch):
    from wfc.canvas import server

    monkeypatch.setenv("WFC_CANVAS_PROJECT_ROOT", str(tmp_project))
    server._active_jobs.clear()
    server._archive_job["thread"] = None
    server._archive_job["progress"] = None
    return TestClient(server.app, raise_server_exceptions=False)


def _seed_unarchived_run(tmp_project, n_outputs=2, name="arch_mod"):
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(
        parents=True, exist_ok=True
    )
    with get_session() as session:
        mod = Module(name=name)
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name=f"{name}_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    for i in range(n_outputs):
        f = tmp_project / f"{name}_{i}.parquet"
        f.write_bytes(f"content-{i}".encode())
        with get_session() as session:
            session.add(RunOutput(
                run_id=run_id, output_name=f.name,
                artifact_path=str(f), artifact_type="module_file",
            ))
            session.commit()
    return run_id


@workflow(purpose="archive-status reports unarchived counts; POST cache/archive clears them")
def test_archive_status_counts_and_manual_archive(client, tmp_project):
    """Seeded NULL-hash outputs show up in archive-status; a manual archive
    pass clears the counts and writes hashes."""
    from wfc.canvas import server
    from wfc.database import get_session
    from wfc.models import RunOutput

    run_id = _seed_unarchived_run(tmp_project, n_outputs=2)

    body = client.get("/api/wfc/archive-status").json()
    assert body["state"] == "idle"
    assert body["unarchived_runs"] == 1
    assert body["unarchived_outputs"] == 2
    assert body["pipeline_running"] is False
    assert body["progress"] is None

    resp = client.post("/api/wfc/cache/archive")
    assert resp.status_code == 200

    server._archive_job["thread"].join(timeout=30)
    assert not server._archive_job["thread"].is_alive()

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
    from wfc.canvas import server

    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    server._active_jobs["pipe-1"] = {"thread": t}
    try:
        resp = client.post("/api/wfc/cache/archive")
        assert resp.status_code == 409
        assert server._archive_job["thread"] is None

        body = client.get("/api/wfc/archive-status").json()
        assert body["pipeline_running"] is True
    finally:
        release.set()
        t.join(timeout=5)
