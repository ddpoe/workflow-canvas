"""The runs payload carries the lineage relation, over one diverse history.

Rows three pipelines produced over one project go through the provider's
load. The history covers the axes that change Lineage's control flow: a
collapsed fan-in over bundled samples, a run reference that crosses
pipelines, a failure that cancelled a row, and a re-run that cache-hit a
pipeline's head and executed a new tail. Every consumer then answers from
those loaded records: the runs payload's input edges and resolved upstreams,
ancestors and descendants walked over the payload's own records (the walk the
canvas's Descendants tree makes), the cancelled-descendants route, and
synthesis from the tail.

A second test covers the payload's sample reads: a run's recorded sample
rows are reported as ``sampleInputs`` and never as parents, and an input row
whose source run ``wfc demo --remove`` deleted appears in neither list.

Requirement: ``docs/system/lineage.json``, section ``testing.requirements``
(the agreement oracle and the one diverse end-to-end path).
"""
from __future__ import annotations

from types import SimpleNamespace

from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client
from tests.harness import (
    ModuleSpec, Scenario, exits, node, reference, run_scenario, selector, wire,
)
from wfc.canvas import state as canvas_state
from wfc.demo.remove import remove_demo
from wfc.lineage import ancestors, descendants, upstreams
from wfc.persistence import RunInput, get_session, reset_engine


def _produce_history(root, monkeypatch) -> dict[str, str]:
    """Run the history as three pipelines over one root; run ids by label.

    Pipeline ``p1`` runs ``load -> norm -> qc -> summarize`` per sample over
    s1 and s2 beside a fan-in selector bundling both samples into the
    collapsed ``merge/__all__``, which feeds ``report/__all__``; ``qc``
    fails on s2, so the pipeline-end walk cancels ``summarize/s2``.
    Pipeline ``p2``'s ``plot`` is fed by a run reference declared by
    locator on p1's ``report``; rooted only in a reference to a collapsed
    run, it runs at ``__all__``. Pipeline ``p3`` re-declares
    ``load -> norm`` over s1, which the claim cache-hits from p1's rows
    (the norm hit carries its own input edge from the load hit, as the
    claim phase writes it), and runs ``score`` on top.

    A collapsed node takes its bundle straight off the fan-in selector: a
    collapsed node fed by a per-sample method step is a shape the engine
    cannot schedule, so the bundle's recorded lineage begins at ``merge``.

    Args:
        root: The project root the three pipelines run over.
        monkeypatch: The test's ``MonkeyPatch``.

    Returns:
        Run ids (as the canvas spells them) by label.
    """
    p1 = run_scenario(Scenario(
        nodes=[
            selector("sel"),
            node("load", inputs=[wire("sel")]),
            node("norm", inputs=[wire("load")]),
            node("qc", inputs=[wire("norm")], behavior_by_sample={"s2": exits(1)}),
            node("summarize", inputs=[wire("qc")]),
            selector("bundle", fan_mode="in"),
            node("merge", inputs=[wire("bundle", target_slot="tables", bundle=True)]),
            node("report", inputs=[wire("merge", target_slot="merged")]),
        ],
        samples=["s1", "s2"], pipeline_id="p1", name="p1",
    ), root=root, monkeypatch=monkeypatch)
    p2 = run_scenario(Scenario(
        nodes=[
            reference("ref", run_of=("report", "__all__", "default"), pipeline="p1"),
            node("plot", inputs=[wire("ref", target_slot="table")]),
        ],
        samples=[], pipeline_id="p2", name="p2",
    ), root=root, monkeypatch=monkeypatch)
    p3 = run_scenario(Scenario(
        nodes=[
            selector("sel"),
            node("load", inputs=[wire("sel")]),
            node("norm", inputs=[wire("load")]),
            node("score", inputs=[wire("norm")]),
        ],
        samples=["s1"], pipeline_id="p3", name="p3",
    ), root=root, monkeypatch=monkeypatch)

    def run_id(obs, node_id, sample="s1"):
        return obs.runs[(node_id, sample, "default")].run_id

    ids = {
        "load_s1": run_id(p1, "load"), "load_s2": run_id(p1, "load", "s2"),
        "norm_s1": run_id(p1, "norm"), "norm_s2": run_id(p1, "norm", "s2"),
        "qc_s1": run_id(p1, "qc"), "qc": run_id(p1, "qc", "s2"),
        "summarize_s1": run_id(p1, "summarize"),
        "summarize": p1.rows_for_node("summarize", sample="s2",
                                      status="cancelled")[0]["id"],
        "merge": run_id(p1, "merge", "__all__"),
        "report": run_id(p1, "report", "__all__"),
        "plot": run_id(p2, "plot", "__all__"),
        "load_hit": run_id(p3, "load"), "norm_hit": run_id(p3, "norm"),
        "score": run_id(p3, "score"),
    }
    return {label: str(rid) for label, rid in ids.items()}


@workflow(purpose="Over one history with a collapsed fan-in, a cross-pipeline "
                  "reference, a cancelled row and a re-run that cache-hit a "
                  "pipeline's head, loaded from real rows, the runs payload's "
                  "resolved upstreams, ancestors and descendants walked over "
                  "the payload's records, the cancelled-descendants route and "
                  "synthesis from the tail all answer from the same records",
          inputs="Rows three pipelines produced over one project, served by "
                 "a canvas launched on it",
          outputs="Each consumer's answer, compared with the history's shape")
def test_lineage_consumers_agree_over_a_diverse_history(git_project, monkeypatch):
    """Lineage requirements: the agreement oracle and the diverse path."""
    ids = _produce_history(git_project, monkeypatch)
    bind_provider(monkeypatch, None)

    def named(*labels):
        return {ids[label] for label in labels}

    try:
        with canvas_client(git_project, monkeypatch) as client:
            # The runs payload carries the records every answer below is
            # computed from: recorded input edges in slot order, the bundle.
            # The collapsed merge takes its bundle off the selector, so its
            # only recorded relation is the one report draws from it.
            runs = {r["id"]: r for r in client.get("/api/wfc/runs").json()}
            assert runs[ids["merge"]]["parentRunIds"] == []
            assert runs[ids["report"]]["parentRunIds"] == [ids["merge"]]
            assert runs[ids["plot"]]["parentRunIds"] == [ids["report"]]
            assert runs[ids["merge"]]["bundledSamples"] == ["s1", "s2"]

            # Each run carries its resolved upstreams: input edges in slot
            # order, then the reuse edge of a cache-hit row. For every run the
            # field is the relation over the payload's own records.
            assert runs[ids["report"]]["upstreamRunIds"] == [ids["merge"]]
            assert runs[ids["load_hit"]]["upstreamRunIds"] == [ids["load_s1"]]
            assert runs[ids["norm_hit"]]["upstreamRunIds"] == [ids["load_hit"], ids["norm_s1"]]
            records = {rid: SimpleNamespace(**r) for rid, r in runs.items()}
            for record in records.values():
                assert record.upstreamRunIds == upstreams(record)

            # Ancestors over the payload cross the pipeline boundary into the
            # collapsed chain, which begins at the merge the selector's bundle
            # fed.
            above = ancestors(records, ids["plot"])
            assert set(above) == named("report", "merge")
            assert len(above) == len(set(above))
            assert descendants(records, ids["plot"]) == []

            # The re-run's tail reaches through its cache-hit rows to the runs
            # they reused.
            above = ancestors(records, ids["score"])
            assert set(above) == named("norm_hit", "load_hit", "norm_s1", "load_s1")
            assert len(above) == len(set(above))
            assert descendants(records, ids["score"]) == []

            # The head's descendants follow its sample's chain down to the
            # failure, each run once; the cancellation pointer is no edge, so
            # the cancelled summarize/s2 is not among them.
            below = descendants(records, ids["load_s2"])
            assert set(below) == named("norm_s2", "qc")
            assert len(below) == len(set(below))

            # A reused head's descendants reach the re-run through the reuse
            # edges, cache-hit rows included, each run once.
            below = descendants(records, ids["load_s1"])
            assert set(below) == named("norm_s1", "qc_s1", "summarize_s1",
                                       "load_hit", "norm_hit", "score")
            assert len(below) == len(set(below))

            # Cancelled descendants are a filter on the pointer, not a walk.
            cancelled = client.get(f"/api/wfc/run/{ids['qc']}/cancelled-descendants").json()
            assert [r["id"] for r in cancelled] == [ids["summarize"]]
            assert client.get(
                f"/api/wfc/run/{ids['norm_s2']}/cancelled-descendants").json() == []

            # Synthesis from the tail: three method nodes, one fan-in selector
            # carrying the bundle above the collapse.
            pipe = client.get(f"/api/runs/{ids['plot']}/lineage-pipeline").json()
            methods = sorted(n["method"] for n in pipe["nodes"] if n["type"] == "method")
            assert methods == ["merge", "plot", "report"]
            selectors = sorted(
                (n.get("fan_mode") or "out", tuple(n["samples"]))
                for n in pipe["nodes"] if n["type"] == "input_selector"
            )
            assert selectors == [("in", ("s1", "s2"))]
            # One edge out of the fan-in selector, one each for report and plot.
            # The tail is a __all__ run rooted only in a reference to a
            # collapsed run, and that is the sample the synthesis carries.
            assert len(pipe["links"]) == 3
            assert pipe["samples"] == ["__all__"]

            # Synthesis follows input edges only: from the re-run's tail it
            # collects the re-run's three rows, not the runs they reused.
            pipe = client.get(f"/api/runs/{ids['score']}/lineage-pipeline").json()
            methods = sorted(n["method"] for n in pipe["nodes"] if n["type"] == "method")
            assert methods == ["load", "norm", "score"]
    finally:
        reset_engine()


@workflow(purpose="A run's recorded sample reads reach the runs payload as "
                  "sampleInputs entries of slot and sample, never as parents; "
                  "an input row whose source run was deleted by wfc demo "
                  "--remove appears in neither list",
          inputs="One pipeline whose quantify reads its sample, a user "
                 "method's output and a demo method's output, after the demo "
                 "is removed, served by a canvas launched on it",
          outputs="The payload's parents, upstreams and sampleInputs for each "
                  "surviving run")
def test_sample_reads_reach_the_payload_apart_from_parents(git_project, monkeypatch):
    """Sample reads are reported as recorded; an orphaned parent is dropped."""
    obs = run_scenario(Scenario(
        nodes=[
            selector("sel"),
            node("segment", method="__demo__segment", module="__demo__",
                 inputs=[wire("sel")]),
            node("norm", inputs=[wire("sel")]),
            node("quantify", inputs=[wire("sel", target_slot="raw"),
                                     wire("segment", target_slot="mask"),
                                     wire("norm", target_slot="table")]),
        ],
        samples=["s1"], modules={"__demo__": ModuleSpec(demo_owned=True)},
    ), root=git_project, monkeypatch=monkeypatch)
    norm = str(obs.runs[("norm", "s1", "default")].run_id)
    quantify = str(obs.runs[("quantify", "s1", "default")].run_id)
    segment = str(obs.runs[("segment", "s1", "default")].run_id)

    # The demo's teardown deletes the demo method's run and nulls the source
    # of the input row that recorded it, leaving quantify a row that names
    # neither a run nor a sample.
    assert remove_demo(git_project, assume_yes=True) == 0
    bind_provider(monkeypatch, None)

    try:
        with canvas_client(git_project, monkeypatch) as client:
            with get_session() as session:
                rows = session.exec(select(RunInput).where(
                    RunInput.run_id == int(quantify))).all()
            assert sorted((r.input_name, r.source_run_id, r.sample_name)
                          for r in rows) == [
                ("mask", None, None), ("raw", None, "s1"),
                ("table", int(norm), None)]

            runs = {r["id"]: r for r in client.get("/api/wfc/runs").json()}
            assert segment not in runs

            # The upstream method's run is quantify's one parent; the sample
            # read is listed apart, and the orphaned row is in neither.
            q = runs[quantify]
            assert q["parents"] == [
                {"slot": "table", "sourceRunId": norm, "sourceSlot": "data"}]
            assert q["parentRunIds"] == [norm]
            assert q["upstreamRunIds"] == [norm]
            assert q["sampleInputs"] == [{"slot": "raw", "sample": "s1"}]

            # A root reads its sample and has no parents.
            n = runs[norm]
            assert n["parents"] == [] and n["parentRunIds"] == []
            assert n["upstreamRunIds"] == []
            assert n["sampleInputs"] == [{"slot": "data", "sample": "s1"}]
    finally:
        reset_engine()
