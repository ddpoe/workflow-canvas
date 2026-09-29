"""Tests for GET /api/runs/{run_id}/lineage-pipeline.

Synthesize a literal-only lineage pipeline from the run-DAG.
Returns 200 with the synthesized JSON, 404 if the run id is unknown,
422 if synthesis fails (cycle defense).
"""

from __future__ import annotations

import pytest

from wfc.canvas import state as canvas_state
from wfc.canvas.wfc_provider import WfcProvider
from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client, completed_run
from tests.harness import Scenario, node, selector, wire


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


def test_returns_404_for_unknown_run(client):
    """An unknown run id is the lineage endpoint's 404 case (D — distinct
    from 422 which is reserved for synthesis failure)."""
    resp = client.get("/api/runs/9999/lineage-pipeline")
    assert resp.status_code == 404
