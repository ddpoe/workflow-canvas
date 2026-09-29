"""Canvas API route tests: the loaded project's status and refresh.

Catalog case in ``docs/system/canvas-api/catalog.json``: ``project-provider-status``.
"""

from __future__ import annotations

from axiom_annotations import workflow
from sqlmodel import Session

from wfc.canvas import state as canvas_state
from wfc.canvas.wfc_provider import WfcProvider
from wfc.persistence import Method, Module, Run


@workflow(
    purpose="With no project loaded the status route reports unloaded and refresh "
            "is refused with 400; once a provider is bound both report the "
            "project path and its module and run counts",
)
def test_project_status_and_refresh_before_and_after_a_load(
    canvas_client, canvas_db, tmp_project, monkeypatch
):
    with Session(canvas_db) as session:
        mod = Module(name="features")
        session.add(mod)
        session.flush()
        meth = Method(name="regionprops", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        session.add_all([
            Run(method_id=meth.id, sample="s1", status="completed"),
            Run(method_id=meth.id, sample="s2", status="failed"),
        ])
        session.commit()

    # No project loaded: status reports unloaded, and refresh has nothing to reload.
    from tests.fixtures.fakes import bind_provider

    bind_provider(monkeypatch, None)
    status = canvas_client.get("/api/wfc/status")
    refresh = canvas_client.post("/api/wfc/refresh")
    assert status.status_code == 200, status.text
    assert status.json() == {"loaded": False, "path": None}
    assert refresh.status_code == 400, refresh.text

    # A provider is bound: both routes name the project and count its module and runs.
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    status = canvas_client.get("/api/wfc/status")
    refresh = canvas_client.post("/api/wfc/refresh")
    assert status.status_code == 200, status.text
    assert status.json() == {"loaded": True, "path": str(tmp_project), "modules": 1, "runs": 2}
    assert refresh.status_code == 200, refresh.text
    assert refresh.json() == {"status": "refreshed", "path": str(tmp_project),
                              "modules": 1, "runs": 2}
