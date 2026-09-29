"""The cache-status preview: what a pipeline would do if it ran now.

``wfc.execution.cache_status`` prepares and loads the posted document as a
run does, walks the engine's target expansion in order, predicts each key
with the claim's key core over the upstreams' predicted keys, and asks the
claim's hit rule. These tests hold it to the engine: the key it predicts is
the key the claim composes when the pipeline then runs, cold and warm; each
status is the one the run would bear out; and the route that serves it
writes nothing and reports a refusal as data.

Every run here is produced by production's five phases through the
scenario harness; lost outputs are removed from disk the way ``dvc gc`` or
a pruned archive leaves them, and the pushed state is the declared
``flip_push_status`` shortcut.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest
from sqlmodel import func, select

from axiom_annotations import Step, workflow

from tests.fixtures.fakes.shortcuts import flip_push_status
from tests.fixtures.routes import canvas_client, completed_run
from tests.harness import Scenario, build_project, node, reference, selector, wire
from tests.harness.drivers import run_scenario
from tests.harness.scenario import SELECTOR_ID

_ENRICHED_ONLY = ("script", "slot_outputs", "slot_types", "env")


# =============================================================================
# Helpers
# =============================================================================

def _canvas_form(project) -> dict:
    """The document the canvas would post for a built project.

    The harness writes the enriched document; the canvas posts the sparse
    form (no script, slot maps or env, links spelled with handles), which
    the preview prepares exactly as the run route does.
    """
    enriched = json.loads(Path(project.pipeline_json).read_text(encoding="utf-8"))
    nodes = [{k: v for k, v in n.items() if k not in _ENRICHED_ONLY}
             for n in enriched["nodes"]]
    links = []
    for link in enriched["links"]:
        entry = {"source": link["source"], "target": link["target"]}
        if link.get("source_slot"):
            entry["sourceHandle"] = link["source_slot"]
        if link.get("target_slot"):
            entry["targetHandle"] = link["target_slot"]
        links.append(entry)
    document = {k: v for k, v in enriched.items() if k not in ("nodes", "links")}
    return {**document, "nodes": nodes, "links": links}


def _preview(project) -> dict:
    """Run the preview over the project's canvas form; rows keyed by target."""
    from wfc.execution.cache_status import cache_status

    report = cache_status(_canvas_form(project))
    assert report.blocked_reason is None, report.blocked_reason
    return {(r.node_id, r.sample, r.variant): r for r in report.rows}


def _claimed_keys(obs) -> dict:
    """The key the claim phase composed for each target of a scenario run."""
    from wfc.persistence import Run, get_session

    with get_session() as session:
        return {target: session.get(Run, int(run_id)).cache_key
                for target, run_id in obs.run_id_sidecars.items()}


def _source_run(obs, target) -> int:
    """The run holding a target's outputs: the run itself or the one it hit."""
    from wfc.persistence import Run, get_session

    with get_session() as session:
        run = session.get(Run, int(obs.run_id_sidecars[target]))
        return run.cache_source_run_id or run.id


def _remove(path: Path) -> None:
    """Delete a file or directory, clearing DVC's read-only bit first."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        path.unlink()


def _lose_outputs(project_root: Path, run_id: int) -> None:
    """Remove every output of a run from this machine, and none is pushed."""
    from wfc import layout
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
    assert rows, f"run {run_id} recorded no outputs"
    for row in rows:
        if row.content_hash:
            _remove(layout.dvc_cache_entry(project_root, row.content_hash))
        if row.artifact_path:
            _remove(Path(row.artifact_path))
    flip_push_status(run_id, "failed")


def _make_remote(project_root: Path, run_id: int) -> None:
    """Archive a run's outputs, record them pushed, and prune the local copies."""
    from wfc import layout
    from wfc.persistence import RunOutput, get_session
    from wfc.storage.archive import archive_outputs

    archive_outputs(project_root, run_id=run_id)
    flip_push_status(run_id, "pushed")
    with get_session() as session:
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
    for row in rows:
        assert row.content_hash, "output not archived"
        _remove(layout.dvc_cache_entry(project_root, row.content_hash))


def _make_sample_unreachable(project_root: Path, name: str) -> None:
    """Prune a sample's DVC cache entry and record it never pushed."""
    from wfc import layout
    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
    assert row is not None and row.content_hash
    _remove(layout.dvc_cache_entry(project_root, row.content_hash))
    # A field update on the row production wrote: never pushed.
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
        row.push_status = "failed"
        session.add(row)
        session.commit()


# =============================================================================
# Case 15 (+ A1, A3, D-11): the preview's key is the claim's key
# =============================================================================

def _chain(samples=("s1", "s2")) -> Scenario:
    # A3: b's link names no output of a, a single-output upstream.
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"},
                    params={"k": 1}),
               node("b", inputs=[wire("a", source_slot=None)], params={"k": 2})],
        samples=list(samples), pipeline_id="cs-chain", name="cs_chain",
    )


def _fan_in(_root, _mp) -> Scenario:
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)]),
               node("b", inputs=[wire(SELECTOR_ID)], params={"k": 3}),
               node("c", inputs=[wire("a", target_slot="left"),
                                 wire("b", target_slot="right")])],
        samples=["s1"], pipeline_id="cs-fanin", name="cs_fanin",
    )


def _bundle(_root, _mp) -> Scenario:
    return Scenario(
        nodes=[selector(fan_mode="in", samples=["s1", "s2"]),
               node("m_bundle", inputs=[wire(SELECTOR_ID, bundle=True)]),
               node("m_after", inputs=[wire("m_bundle")])],
        samples=["s1", "s2"], pipeline_id="cs-bundle", name="cs_bundle",
    )


def _run_reference(root, mp) -> Scenario:
    referenced = completed_run(root, monkeypatch=mp, method="m_ref",
                               module="ref_mod", sample="s_ref",
                               outputs={"result": ".csv"})
    return Scenario(
        nodes=[reference("ref", run_id=str(referenced.run_id)),
               node("m_use", inputs=[wire("ref", source_slot="result")])],
        pipeline_id="cs-ref", name="cs_ref",
    )


def _variants(_root, _mp) -> Scenario:
    scn = _chain(samples=("s1",))
    scn.variants = {"low": {"k": 1}, "high": {"k": 9}}
    scn.pipeline_id, scn.name = "cs-variants", "cs_variants"
    return scn


_CORPUS = {
    "chain": lambda _r, _m: _chain(),
    "fan-in": _fan_in,
    "collapsed-bundle": _bundle,
    "run-reference": _run_reference,
    "variants": _variants,
}


@pytest.mark.parametrize("shape", list(_CORPUS))
@workflow(purpose="For every corpus shape the preview predicts the key the "
                  "claim composes when the pipeline then runs: cold, where "
                  "every row is new and the downstream keys chain on predicted "
                  "upstream keys, and warm, where the upstreams are cache hits "
                  "and every row is cached with the same key")
def test_preview_key_equals_the_claimed_key_cold_and_warm(tmp_project, monkeypatch,
                                                           shape):
    口 = Step(step_num=1, name="Build the shape",
             purpose="Registered methods, samples and the document, nothing run")
    scenario = _CORPUS[shape](tmp_project, monkeypatch)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Preview cold",
             purpose="Nothing of this pipeline has run: roots are new because "
                     "the step changed, steps below them because an upstream "
                     "re-runs")
    cold = _preview(project)
    assert cold, "the preview expanded no targets"
    for row in cold.values():
        assert row.status in ("new_step_changed", "new_upstream_reruns"), row

    口 = Step(step_num=3, name="Run it; every claimed key is the predicted one",
             purpose="The claim composes each key from the rows the upstream "
                     "runs left; the preview composed it from predicted keys")
    obs = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    claimed = _claimed_keys(obs)
    assert set(claimed) == set(cold)
    for target, key in claimed.items():
        assert cold[target].cache_key == key, target

    口 = Step(step_num=4, name="Preview warm, then run again",
             purpose="A1: the downstream's recorded parent is now the "
                     "upstream's cache-hit row; its key must still be the one "
                     "predicted, and every row is a local hit")
    warm = _preview(project)
    for target, row in warm.items():
        assert row.status == "cached_local", row
        assert row.cache_key == claimed[target]
        assert row.source_run_id == _source_run(obs, target)
        assert row.outputs and all(loc == "local" for _, loc in row.outputs)
    again = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    assert _claimed_keys(again) == claimed


@workflow(purpose="An upstream whose outputs are gone re-runs under the same "
                  "key, so the step below it keeps its key and stays cached: "
                  "the preview says outputs missing for the upstream and "
                  "cached for the downstream, and the run bears both out")
def test_upstream_outputs_missing_keeps_the_downstream_cached(tmp_project,
                                                              monkeypatch):
    from wfc.persistence import Run, get_session

    scenario = _chain(samples=("s1",))
    target_a, target_b = ("a", "s1", "default"), ("b", "s1", "default")

    口 = Step(step_num=1, name="Run the chain, then lose a's outputs",
             purpose="The completed run of a keeps its key; its bytes are in "
                     "neither place")
    first = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    keys = _claimed_keys(first)
    _lose_outputs(tmp_project, _source_run(first, target_a))

    口 = Step(step_num=2, name="Preview",
             purpose="a is outputs missing with its source run and a missing "
                     "output; b keeps its key and is a local hit, not "
                     "'upstream re-runs'")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    rows = _preview(project)
    assert rows[target_a].status == "outputs_missing"
    assert rows[target_a].source_run_id == _source_run(first, target_a)
    assert ("table", "missing") in rows[target_a].outputs
    assert rows[target_b].status == "cached_local"
    assert rows[target_b].cache_key == keys[target_b]

    口 = Step(step_num=3, name="Run again: a re-runs, b is served from cache",
             purpose="The engine agrees with both rows")
    second = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    assert _claimed_keys(second) == keys
    with get_session() as session:
        run_a = session.get(Run, int(second.run_id_sidecars[target_a]))
        run_b = session.get(Run, int(second.run_id_sidecars[target_b]))
    assert run_a.cache_source_run_id is None
    assert run_b.cache_source_run_id is not None


# =============================================================================
# Case 17a: the reason a row is new
# =============================================================================

@pytest.mark.parametrize(
    "a_params,b_params,a_status,b_status",
    [
        pytest.param({"k": 1}, {"k": 5}, "cached_local", "new_step_changed",
                     id="own params changed under a cached upstream"),
        pytest.param({"k": 7}, {"k": 2}, "new_step_changed", "new_upstream_reruns",
                     id="unchanged under a new upstream"),
        pytest.param({"k": 7}, {"k": 5}, "new_step_changed", "new_upstream_reruns",
                     id="both changed"),
    ],
)
@workflow(purpose="A new row says why: its own params changed under a cached "
                  "upstream is 'this step changed'; an upstream whose key is new "
                  "makes it 'upstream re-runs', whether or not its own params "
                  "changed too")
def test_reason_a_row_is_new(tmp_project, monkeypatch, a_params, b_params,
                             a_status, b_status):
    scenario = _chain(samples=("s1",))
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)

    document = _canvas_form(project)
    for n in document["nodes"]:
        if n["id"] == "a":
            n["params"] = a_params
        elif n["id"] == "b":
            n["params"] = b_params
    from wfc.execution.cache_status import cache_status

    rows = {(r.node_id, r.sample): r for r in cache_status(document).rows}
    assert rows[("a", "s1")].status == a_status
    assert rows[("b", "s1")].status == b_status


# =============================================================================
# Case 21: two nodes of one method are two rows
# =============================================================================

@workflow(purpose="Two nodes running the same method with different params each "
                  "carry their own status: changing one node's params leaves the "
                  "other node's row cached")
def test_two_nodes_of_one_method_keep_their_own_status(tmp_project, monkeypatch):
    scenario = Scenario(
        nodes=[selector(),
               node("p1", method="shared", inputs=[wire(SELECTOR_ID)],
                    params={"k": 1}),
               node("p2", method="shared", inputs=[wire(SELECTOR_ID)],
                    params={"k": 2})],
        samples=["s1"], pipeline_id="cs-shared", name="cs_shared",
    )
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    document = _canvas_form(project)
    for n in document["nodes"]:
        if n["id"] == "p2":
            n["params"] = {"k": 3}
    from wfc.execution.cache_status import cache_status

    rows = {r.node_id: r for r in cache_status(document).rows}
    assert rows["p1"].status == "cached_local"
    assert rows["p2"].status == "new_step_changed"


# =============================================================================
# Case 17 (+ A4): the route's statuses, refusals as data, read-only
# =============================================================================

def _table_counts() -> dict:
    from wfc.persistence import Method, Module, Run, RunInput, RunOutput, Sample
    from wfc.persistence import get_session

    with get_session() as session:
        return {model.__name__: session.exec(select(func.count()).select_from(model)).one()
                for model in (Run, RunInput, RunOutput, Method, Module, Sample)}


@workflow(purpose="Over a real project the route reports each status: local and "
                  "remote hits, outputs missing, a new row, a row blocked by an "
                  "unreachable sample and the row under it blocked naming it; a "
                  "document the load refuses is one pipeline-level reason with a "
                  "200, never a 500; and nothing in the database or the env "
                  "manifest changes")
def test_route_reports_each_status_and_writes_nothing(tmp_project, monkeypatch):
    from wfc import layout

    scenario = Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)]),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire(SELECTOR_ID)])],
        samples=["s1", "s2", "s3"], pipeline_id="cs-route", name="cs_route",
    )

    口 = Step(step_num=1, name="Run the pipeline, then move its outputs about",
             purpose="s1: a remote, b missing. s2: sample unreachable. c on s3 "
                     "untouched (local); c gets new params on s1 only by the "
                     "posted document below")
    obs = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    _make_remote(tmp_project, _source_run(obs, ("a", "s1", "default")))
    _lose_outputs(tmp_project, _source_run(obs, ("b", "s1", "default")))
    _make_sample_unreachable(tmp_project, "s2")
    document = _canvas_form(project)
    for n in document["nodes"]:
        if n["id"] == "c":
            n["params"] = {"changed": 1}

    envs = layout.envs_manifest_path(tmp_project) \
        if hasattr(layout, "envs_manifest_path") else tmp_project / ".wfc" / "envs.json"
    envs_before = envs.read_bytes() if envs.exists() else None
    counts_before = _table_counts()

    口 = Step(step_num=2, name="Post the document",
             purpose="One row per target, keyed node::sample::variant")
    client = canvas_client(tmp_project, monkeypatch)
    resp = client.post("/api/wfc/cache-status", json=document)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["blocked_reason"] is None
    rows = {r["key"]: r for r in body["rows"]}

    口 = Step(step_num=3, name="Each status is the one the run would bear out",
             purpose="Remote a, missing b, unreachable s2 blocking a and b under "
                     "it, changed c new")
    assert rows["a::s1::default"]["status"] == "cached_remote"
    assert {o["location"] for o in rows["a::s1::default"]["outputs"]} == {"remote"}
    assert rows["b::s1::default"]["status"] == "outputs_missing"
    assert rows["b::s1::default"]["source_run_id"] == \
        _source_run(obs, ("b", "s1", "default"))
    assert rows["a::s3::default"]["status"] == "cached_local"
    assert rows["b::s3::default"]["status"] == "cached_local"
    assert rows["a::s2::default"]["status"] == "blocked"
    assert "s2" in rows["a::s2::default"]["reason"]
    assert rows["b::s2::default"]["status"] == "blocked"
    assert "'a'" in rows["b::s2::default"]["reason"]
    assert rows["c::s1::default"]["status"] == "new_step_changed"

    口 = Step(step_num=4, name="A refused document is data, not a 500",
             purpose="An unregistered method is the load's refusal; every row "
                     "is blocked by that one reason")
    refused = json.loads(json.dumps(document))
    for n in refused["nodes"]:
        if n["id"] == "c":
            n["method"] = "no_such_method"
    resp = client.post("/api/wfc/cache-status", json=refused)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == []
    assert resp.json()["blocked_reason"]

    口 = Step(step_num=5, name="Nothing was written",
             purpose="No run, lineage, output, method, module or sample row; "
                     "the env manifest is byte-identical")
    assert _table_counts() == counts_before
    assert (envs.read_bytes() if envs.exists() else None) == envs_before


def test_unreachable_database_is_a_blocked_reason_not_a_500(tmp_project, monkeypatch):
    """An unreachable database during preparation is the load's refusal, as data.

    The preparation reads the contract map before the load does; the route
    answers the same typed refusal the load raises as ``blocked_reason``
    with a 200 and no rows.
    """
    from wfc.persistence import reset_engine

    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1"], pipeline_id="cs-nodb", name="cs_nodb")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    document = _canvas_form(project)
    client = canvas_client(tmp_project, monkeypatch)
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{tmp_project.as_posix()}/no-such-dir/x.db")
    reset_engine()
    try:
        resp = client.post("/api/wfc/cache-status", json=document)
    finally:
        reset_engine()

    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == []
    assert "database is unreachable" in resp.json()["blocked_reason"]
