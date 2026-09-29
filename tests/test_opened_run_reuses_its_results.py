"""An opened run runs as-is: its synthesized lineage reuses every result.

A run the canvas opens from history is synthesized into a pipeline document
from the run records the provider loads. Posted unedited to the cache-status
preview, that document must predict the keys the original claims composed,
so every step is served from the original results and none re-runs.

The history here covers the two shapes where a sample read is not the
first-slot root read the legacy head wiring guessed: a root that reads its
sample into a named slot other than ``data``, and a step that reads its
sample beside an upstream method's output.

Tier 2 on the stub rung: the runs are produced by production's phases
through the scenario harness, and the document and the preview come from
the routes the canvas calls.
"""
from __future__ import annotations

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client
from tests.harness import Scenario, node, run_scenario, selector, wire
from wfc.persistence import reset_engine


@workflow(purpose="A run whose root reads its sample into a named slot and whose "
                  "terminal step reads its sample beside an upstream output, "
                  "opened from history and previewed unedited, is served "
                  "entirely from the original results: nothing is blocked and "
                  "every row is a local hit on the run that produced it",
          inputs="One completed pipeline over one sample, served by a canvas "
                 "launched on its project",
          outputs="The synthesized lineage document and the preview's rows "
                  "over it")
def test_an_opened_run_is_served_from_its_original_results(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run the pipeline to completion",
             purpose="segment reads the sample into dapi_image; quantify reads "
                     "the sample into raw beside segment's output on mask")
    obs = run_scenario(Scenario(
        nodes=[
            selector("sel"),
            node("segment", inputs=[wire("sel", target_slot="dapi_image")]),
            node("quantify", inputs=[wire("sel", target_slot="raw"),
                                     wire("segment", target_slot="mask")]),
        ],
        samples=["s1"], pipeline_id="opened", name="opened",
    ), root=git_project, monkeypatch=monkeypatch)
    produced = {node_id: obs.runs[(node_id, "s1", "default")].run_id
                for node_id in ("segment", "quantify")}
    assert all(run_id is not None for run_id in produced.values()), produced
    bind_provider(monkeypatch, None)

    try:
        with canvas_client(git_project, monkeypatch) as client:
            口 = Step(step_num=2, name="Open the terminal run",
                     purpose="The lineage route synthesizes the document from "
                             "the records the provider loaded")
            resp = client.get(f"/api/runs/{produced['quantify']}/lineage-pipeline")
            assert resp.status_code == 200, resp.text
            document = resp.json()
            methods = sorted(n["method"] for n in document["nodes"]
                             if n["type"] == "method")
            assert methods == ["quantify", "segment"]

            口 = Step(step_num=3, name="Preview the unedited document",
                     purpose="The cache-status route prepares and loads it as a "
                             "run would, and predicts each step's key")
            resp = client.post("/api/wfc/cache-status", json=document)
            assert resp.status_code == 200, resp.text
            body = resp.json()

            口 = Step(step_num=4, name="Every step reuses its original result",
                     purpose="No refusal, no blocked row; each row is a local "
                             "hit whose source is the run that first produced it")
            assert body["blocked_reason"] is None
            rows = body["rows"]
            assert len(rows) == 2, rows
            by_method = {}
            for row in rows:
                method = next(n["method"] for n in document["nodes"]
                              if n["id"] == row["node_id"])
                by_method[method] = row
            for method, row in by_method.items():
                assert row["status"] == "cached_local", row
                assert row["source_run_id"] == produced[method], row
    finally:
        reset_engine()
