"""
Atomic-swap reload for the WfcProvider registries.

``load()`` used to clear ``_runs`` / ``_modules`` / ``_methods`` in place and
then repopulate them with a full DB scan. FastAPI serves sync endpoints from a
threadpool, and ``get_all_runs()`` reloads on every call, so the History tab's
parallel runs/modules/methods fetches raced each other: a GET landing mid-reload
read the cleared dicts and returned an empty 200 (the "No methods loaded"
dropdown). Readers must instead see the old-complete state until the reload has
fully finished, then the new-complete state.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import SQLModel, Session, create_engine

from wfc.models import Module, Method, Run
from wfc.canvas.wfc_provider import WfcProvider


@pytest.fixture
def seeded_project(tmp_path, monkeypatch):
    """Project dir with a .wfc/wfc.db holding one module, method, and run."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine
    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="analysis", description="Analysis module")
        session.add(mod)
        session.flush()
        meth = Method(
            name="align_reads", module_id=mod.id,
            script_path="methods/align_reads/align_reads.py",
            env="container:demo",
        )
        session.add(meth)
        session.flush()
        run = Run(
            method_id=meth.id,
            params=json.dumps({"threads": 8}),
            sample="sample_001",
            status="completed",
            pipeline_id="pipe-001",
        )
        session.add(run)
        session.flush()
        run_id = str(run.id)
        session.commit()

    yield str(tmp_path), run_id
    reset_engine()


def test_readers_mid_reload_see_previous_complete_state(
    seeded_project, monkeypatch
):
    """While a reload's DB scan is in flight, the public getters keep serving
    the previous complete registries — never an empty/partial snapshot."""
    project_root, run_id = seeded_project
    provider = WfcProvider(project_root)
    provider.load()

    # Sanity: first load populated the registries.
    assert provider.get_modules() == ["analysis"]
    assert [m["name"] for m in provider.get_methods()] == ["align_reads"]
    assert provider.get_run(run_id) is not None

    # Hook the reload at the point the DB scan begins — after the buggy
    # implementation had already cleared the in-place dicts — and record
    # what a concurrent reader would observe.
    observed = {}

    class ProbingSession(Session):
        def __enter__(self):
            observed["modules"] = provider.get_modules()
            observed["method_names"] = [m["name"] for m in provider.get_methods()]
            observed["run"] = provider.get_run(run_id)
            return super().__enter__()

    monkeypatch.setattr("wfc.canvas.wfc_provider.Session", ProbingSession)
    provider.load()

    # Mid-reload readers saw the old complete state, not a cleared one.
    assert observed["modules"] == ["analysis"]
    assert observed["method_names"] == ["align_reads"]
    assert observed["run"] is not None

    # And the reload itself still lands the fresh state.
    assert provider.get_modules() == ["analysis"]
    assert provider.get_run(run_id) is not None
