"""
Atomic-swap reload for the WfcProvider registries.

FastAPI serves sync endpoints from a threadpool, and ``get_all_runs()`` reloads
on every call, so the History tab's parallel runs/modules/methods fetches
overlap reloads. A reload that cleared ``_runs`` / ``_modules`` / ``_methods``
in place before its DB scan would let a GET landing mid-reload read the cleared
dicts and return an empty 200 (the "No methods loaded" dropdown). Readers must
see the old-complete state until the reload has fully finished, then the
new-complete state.
"""

from __future__ import annotations

from sqlmodel import Session

from wfc.canvas.wfc_provider import WfcProvider
from tests.fixtures.fakes import probing_provider_session
from tests.fixtures.routes import completed_run


def test_readers_mid_reload_see_previous_complete_state(tmp_path, monkeypatch):
    """While a reload's DB scan is in flight, the public getters keep serving
    the previous complete registries — never an empty/partial snapshot."""
    # One module, one method and one completed run, all written by
    # registration and the run's own lifecycle over a project built at
    # tmp_path; the reload under test is what reads them back.
    run = completed_run(tmp_path, monkeypatch=monkeypatch, method="align_reads",
                        module="analysis", sample="sample_001",
                        params={"threads": 8}, pipeline_id="pipe-001")
    project_root, run_id = str(tmp_path), str(run.run_id)
    provider = WfcProvider(project_root)
    provider.load()

    # Sanity: first load populated the registries.
    assert provider.get_modules() == ["analysis"]
    assert [m["name"] for m in provider.get_methods()] == ["align_reads"]
    assert provider.get_run(run_id) is not None

    # Hook the reload at the point the DB scan begins — where an in-place
    # clear would already have emptied the dicts — and record what a
    # concurrent reader would observe.
    observed = {}

    class ProbingSession(Session):
        def __enter__(self):
            observed["modules"] = provider.get_modules()
            observed["method_names"] = [m["name"] for m in provider.get_methods()]
            observed["run"] = provider.get_run(run_id)
            return super().__enter__()

    probing_provider_session(monkeypatch, ProbingSession)
    provider.load()

    # Mid-reload readers saw the old complete state, not a cleared one.
    assert observed["modules"] == ["analysis"]
    assert observed["method_names"] == ["align_reads"]
    assert observed["run"] is not None

    # And the reload itself still lands the fresh state.
    assert provider.get_modules() == ["analysis"]
    assert provider.get_run(run_id) is not None
