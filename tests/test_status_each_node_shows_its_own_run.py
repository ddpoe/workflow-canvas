"""The status route shows each canvas node its own run.

Two nodes of one pipeline run the same method under different params. Their
runs are claimed through production's ``run_claim`` on the harness's stub
rung, so every row carries the node id the claim recorded; the status route
then reports each node from its own rows.
"""

from __future__ import annotations

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import seed_active_job
from tests.fixtures.routes import canvas_client
from tests.harness import Scenario, completed, exits, node, run_scenario, selector, wire


@workflow(purpose="Each node shows its own run: two nodes of one method, one "
                  "failing and one served from a cached run, read back "
                  "through GET /api/workflow/status each with its own status, "
                  "run ids, error and cache source")
def test_each_node_of_one_method_shows_its_own_run_on_the_status_route(
        git_project, monkeypatch):
    口 = Step(step_num=1, name="Run two nodes of one method",
             purpose="left fails; right's params match a run seeded ahead of "
                     "the pipeline, so its claim is a cache hit on that run")
    scn = Scenario(
        nodes=[
            selector(),
            node("left", method="m", params={"k": 1}, inputs=[wire("sel")],
                 behavior=exits(1)),
            node("right", method="m", params={"k": 2}, inputs=[wire("sel")]),
        ],
        prior_runs=[completed("right")],
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    pipeline_id = obs.project.pipeline_id
    [left_run] = [r for r in obs.rows_for_node("left")
                  if r["pipeline_id"] == pipeline_id]
    [right_audit] = [r for r in obs.rows_for_node("right")
                     if r["pipeline_id"] == pipeline_id
                     and r["cache_source_run_id"] is not None]
    right_ids = {str(r["id"]) for r in obs.rows_for_node("right")
                 if r["pipeline_id"] == pipeline_id}

    口 = Step(step_num=2, name="Read the pipeline's status",
             purpose="The job record carries the step map a submission "
                     "stores: both nodes name method m")
    client = canvas_client(obs.project.root, monkeypatch)
    seed_active_job(pipeline_id, alive=False,
                    step_map={"sel": None, "left": "m", "right": "m"})
    resp = client.get(f"/api/workflow/status/{pipeline_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    states = body["node_states"]

    口 = Step(step_num=3, name="Each node reads its own run",
             purpose="Neither node is a mixed blend of the other's rows: "
                     "left is failed with its own error, right is completed "
                     "as a cache hit on the seeded run")
    left, right = states["left"], states["right"]
    assert left["status"] == "failed"
    assert left["run_ids"] == [str(left_run["id"])]
    assert left["error_run_id"] == str(left_run["id"])
    assert left["error"]
    assert left.get("cache_hit") is None
    assert right["status"] == "completed"
    assert set(right["run_ids"]) == right_ids
    assert str(left_run["id"]) not in right["run_ids"]
    assert right["cache_hit"] is True
    assert right["original_run_id"] == str(right_audit["cache_source_run_id"])
    assert right.get("error") is None
    assert body["overall_status"] == "failed"


@workflow(purpose="Each cache-hit node names its own cache source: two nodes "
                  "of one method, both served from runs seeded ahead of the "
                  "pipeline, read back through GET /api/workflow/status each "
                  "with the original run its own claim hit")
def test_two_cache_hit_nodes_of_one_method_each_name_their_own_cache_source(
        git_project, monkeypatch):
    口 = Step(step_num=1, name="Run two cache-hit nodes of one method",
             purpose="left's and right's params each match a run seeded ahead "
                     "of the pipeline, so both claims are cache hits, on "
                     "different source runs")
    scn = Scenario(
        nodes=[
            selector(),
            node("left", method="m", params={"k": 1}, inputs=[wire("sel")]),
            node("right", method="m", params={"k": 2}, inputs=[wire("sel")]),
        ],
        prior_runs=[completed("left"), completed("right")],
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    pipeline_id = obs.project.pipeline_id
    [left_audit] = [r for r in obs.rows_for_node("left")
                    if r["pipeline_id"] == pipeline_id
                    and r["cache_source_run_id"] is not None]
    [right_audit] = [r for r in obs.rows_for_node("right")
                     if r["pipeline_id"] == pipeline_id
                     and r["cache_source_run_id"] is not None]
    assert left_audit["cache_source_run_id"] != right_audit["cache_source_run_id"]

    口 = Step(step_num=2, name="Read the pipeline's status",
             purpose="The job record carries the step map a submission "
                     "stores: both nodes name method m")
    client = canvas_client(obs.project.root, monkeypatch)
    seed_active_job(pipeline_id, alive=False,
                    step_map={"sel": None, "left": "m", "right": "m"})
    resp = client.get(f"/api/workflow/status/{pipeline_id}")
    assert resp.status_code == 200, resp.text
    states = resp.json()["node_states"]

    口 = Step(step_num=3, name="Each node names its own cache source",
             purpose="Both nodes are completed cache hits, and each reports "
                     "the source run its own claim hit, not the other's")
    for node_id, audit in (("left", left_audit), ("right", right_audit)):
        state = states[node_id]
        assert state["status"] == "completed"
        assert state["cache_hit"] is True
        assert state["original_run_id"] == str(audit["cache_source_run_id"])
