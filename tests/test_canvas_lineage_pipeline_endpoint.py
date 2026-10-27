"""Tests for GET /api/runs/{run_id}/lineage-pipeline.

Synthesize a literal-only lineage pipeline from the run-DAG.
Returns 200 with the synthesized JSON, 404 if the run id is unknown,
422 if synthesis fails (cycle defense, or a run with no recorded version).
"""

from __future__ import annotations

import pytest
from axiom_annotations import workflow

from wfc import layout
from wfc.canvas import state as canvas_state
from wfc.canvas.wfc_provider import WfcProvider
from wfc.persistence import reset_engine
from wfc.registration import register_method
from wfc.registration.method_version import read_version_list
from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client, completed_run
from tests.fixtures.schema_snapshots import (
    BACKFILL_RELEASE,
    build_release_database,
    seed_sql,
)
from tests.harness import Scenario, node, selector, wire
from tests.harness.project import method_source_dir


@pytest.fixture
def served_chain(tmp_project, monkeypatch):
    """Test client over ``tmp_project`` serving two runs the routes produced.

    A ``load`` run feeds a ``filter`` run, so the ``run_inputs`` edge the
    provider reads into a run's parents is the one the claim recorded. The
    real provider is loaded over the project and bound as the served one
    through the registry's shortcut; binding is the load endpoint's job,
    which the client does not prove.
    """
    chain = Scenario(
        nodes=[
            selector(),
            node("load", inputs=[wire("sel")]),
            node("filter", inputs=[wire("load")], params={"min": 0.5}),
        ],
        samples=["s1"],
        pipeline_id="p1",
        name="lineage",
    )
    completed_run(tmp_project, monkeypatch=monkeypatch, scenario=chain, target="load")
    filter_run = completed_run(tmp_project, monkeypatch=monkeypatch,
                               scenario=chain, target="filter")
    test_client = canvas_client(tmp_project, monkeypatch)
    provider = WfcProvider(str(tmp_project))
    provider.load()
    bind_provider(monkeypatch, provider)
    return test_client, filter_run.run_id


@pytest.fixture
def client(served_chain):
    """The served chain's test client."""
    return served_chain[0]


def test_returns_synthesized_pipeline_for_known_run(served_chain):
    """Parent-linkage case over routed runs → real WfcProvider → endpoint.

    Two linked runs (load → filter) that production's own phases ran, with
    the run_inputs edge the claim recorded, are loaded through the
    production provider; the endpoint synthesizes the ancestor chain from
    that parent linkage (not a stub's hand-built parents list).
    """
    client, filter_run_id = served_chain

    resp = client.get(f"/api/runs/{filter_run_id}/lineage-pipeline")
    assert resp.status_code == 200
    data = resp.json()
    methods = sorted(n["method"] for n in data["nodes"] if n.get("type") == "method")
    assert methods == ["filter", "load"]
    assert data["samples"] == ["s1"]


@workflow(
    purpose="Opening a lineage pins every method node to the version its run "
            "ran: after a newer version of the upstream method is registered, "
            "the lineage of a run made at v1 still names v1's content "
            "identity on every node",
)
def test_opening_a_lineage_pins_every_node_to_its_runs_version(
    tmp_project, monkeypatch
):
    chain = Scenario(
        nodes=[
            selector(),
            node("load", inputs=[wire("sel")]),
            node("filter", inputs=[wire("load")], params={"min": 0.5}),
        ],
        samples=["s1"],
        pipeline_id="p1",
        name="lineage",
    )
    completed_run(tmp_project, monkeypatch=monkeypatch, scenario=chain, target="load")
    filter_run = completed_run(tmp_project, monkeypatch=monkeypatch,
                               scenario=chain, target="filter")
    ran_at = {}
    for spec in filter_run.project.scenario.method_nodes():
        entries = read_version_list(tmp_project, spec.module, spec.method_name)
        ran_at[spec.method_name] = entries[-1].identity
    load_spec = next(s for s in filter_run.project.scenario.method_nodes()
                     if s.method_name == "load")
    method_dir = method_source_dir(tmp_project, load_spec)
    script = method_dir / "load.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n",
                      encoding="utf-8")
    register_method(method_dir=method_dir, module_name=load_spec.module, assume_yes=True)
    newest_load = read_version_list(tmp_project, load_spec.module, "load")[-1]
    assert newest_load.label == 2 and newest_load.identity != ran_at["load"]
    client = canvas_client(tmp_project, monkeypatch)
    provider = WfcProvider(str(tmp_project))
    provider.load()
    bind_provider(monkeypatch, provider)

    resp = client.get(f"/api/runs/{filter_run.run_id}/lineage-pipeline")

    assert resp.status_code == 200, resp.text
    pins = {n["method"]: n.get("version") for n in resp.json()["nodes"]
            if n.get("type") == "method"}
    assert pins == ran_at


@workflow(
    purpose="A lineage holding runs made before versions carried an "
            "environment fingerprint is refused by name: the lineage of such "
            "a run answers 422, and its detail names every run in the chain "
            "that cannot be pinned, by run id with its module.method",
    inputs="A database an older release created (the frozen 0.6.1 "
           "snapshot with its seed rows: run 2 align.filter fed by run 1 "
           "qc.filter, both on version rows with no env fingerprint), opened "
           "through the bootstrap and served by the real provider",
    outputs="422 whose detail names run 1 (qc.filter) and run 2 (align.filter)",
)
def test_lineage_of_runs_with_no_recorded_version_is_refused_by_name(
    tmp_project, monkeypatch
):
    """Legacy-shape fixture: the testing SOP's declared 0.6.1 snapshot exception.

    Production no longer writes a version row without an env fingerprint, so
    an older release's database is the only honest source of such runs.
    """
    reset_engine()
    db_path = layout.db_path(tmp_project)
    for leftover in (db_path, db_path.with_name(db_path.name + "-wal"),
                     db_path.with_name(db_path.name + "-shm")):
        leftover.unlink(missing_ok=True)
    build_release_database(db_path, seed_sql(), release=BACKFILL_RELEASE)
    client = canvas_client(tmp_project, monkeypatch)
    provider = WfcProvider(str(tmp_project))
    provider.load()  # the process engine opens the database through the bootstrap
    bind_provider(monkeypatch, provider)

    resp = client.get("/api/runs/2/lineage-pipeline")

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "1 (qc.filter)" in detail
    assert "2 (align.filter)" in detail
    assert "palette" in detail


def test_returns_404_for_unknown_run(client):
    """An unknown run id is the lineage endpoint's 404 case (D — distinct
    from 422 which is reserved for synthesis failure)."""
    resp = client.get("/api/runs/9999/lineage-pipeline")
    assert resp.status_code == 404
