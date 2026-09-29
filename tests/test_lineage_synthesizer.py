"""Tests for the lineage synthesizer.

The synthesizer walks ``parentRunIds`` from a clicked Run back to roots —
through pipeline boundaries — and emits a literal-only pipeline JSON
suitable for the canvas's ``loadPipeline()``.

Synthesis reads only the run records it is handed: literal ``WfcRun``
records here, or the records ``WfcProvider.load()`` built from real rows,
with ``bundledSamples`` already resolved. It does NOT read
``pipeline.json`` from disk.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import workflow
from sqlmodel import SQLModel, Session, create_engine

from wfc.lineage import LineageSynthesisError, synthesize_lineage_pipeline
from wfc.canvas.wfc_provider import WfcRun
from wfc.persistence import Module, Method, Run, RunInput


def _records(runs):
    """Key literal ``WfcRun`` records by id, the shape synthesis reads."""
    return {r.id: r for r in runs}


def _mk(
    run_id,
    method,
    sample,
    *,
    module="m",
    parents=None,
    params=None,
    nid="",
    pipeline_id="p1",
    bundled=None,
    reads=None,
):
    """Build a ``WfcRun`` populated with the fields the synthesizer reads.

    ``reads`` is the run's recorded sample reads as ``(slot, sample)`` pairs,
    the ``sampleInputs`` the provider builds from sample rows. Omitted, the
    run has none, as a run recorded before sample rows existed.
    """
    parents = parents or []
    return WfcRun(
        id=run_id,
        module=module,
        method=method,
        dataSource=sample,
        parentRunIds=[p["sourceRunId"] for p in parents],
        parents=list(parents),
        inputs=params or {},
        nid=nid,
        pipelineId=pipeline_id,
        bundledSamples=list(bundled or []),
        sampleInputs=[{"slot": slot, "sample": name} for slot, name in reads or []],
    )


def _heads(pipe):
    """The document's input-selector nodes."""
    return [n for n in pipe["nodes"] if n.get("type") == "input_selector"]


def _head_links(pipe):
    """Each link out of an input selector, as ``(selector id, target, slot)``."""
    head_ids = {n["id"] for n in _heads(pipe)}
    return [
        (ln["source"], ln["target"], ln["targetHandle"])
        for ln in pipe["links"]
        if ln["source"] in head_ids
    ]


# =============================================================================
# Linear single-sample lineage
# =============================================================================


def test_linear_single_sample_emits_methods_and_input_selector():
    """A 4-method linear chain with a single sample becomes 4 method nodes
    plus one ``input_selector`` head wired into the root on the slot the
    root recorded reading its sample. Edges follow ``parents`` slot
    information."""
    a = _mk("1", "load", "s1", reads=[("image", "s1")])
    b = _mk("2", "filter", "s1", parents=[{"slot": "data", "sourceRunId": "1"}])
    c = _mk("3", "score", "s1", parents=[{"slot": "data", "sourceRunId": "2"}])
    d = _mk("4", "report", "s1", parents=[{"slot": "data", "sourceRunId": "3"}])
    records = _records([a, b, c, d])

    pipe = synthesize_lineage_pipeline(records, "4")

    methods = [n for n in pipe["nodes"] if n.get("type") == "method"]
    selectors = [n for n in pipe["nodes"] if n.get("type") == "input_selector"]
    assert len(methods) == 4, "one method node per Run"
    assert len(selectors) == 1, "exactly one input_selector head for a single root"
    assert pipe["samples"] == ["s1"]
    # The selector's samples list is the clicked run's sample.
    assert selectors[0].get("samples") == ["s1"]
    assert selectors[0].get("fan_mode") in (None, "out")

    # Every parent->child relationship in the input becomes a link.
    method_methods = {n["method"] for n in methods}
    assert method_methods == {"load", "filter", "score", "report"}

    # Three parent->child edges plus one selector->root edge.
    assert len(pipe["links"]) == 4
    root = next(n["id"] for n in methods if n["method"] == "load")
    assert _head_links(pipe) == [(selectors[0]["id"], root, "image")]


def test_roots_on_one_sample_share_one_input_selector():
    """Two root runs that read the same sample -- two segmentations of one
    image, compared downstream -- get one input selector feeding both roots,
    not a selector each. Each root is fed on the slot it recorded."""
    cyto = _mk("20", "segment", "s1", params={"model": "cyto2"},
               reads=[("image", "s1")])
    tissue = _mk("21", "segment", "s1", params={"model": "tissuenet"},
                 reads=[("image", "s1")])
    compare = _mk("23", "compare", "s1", parents=[
        {"slot": "mask_a", "sourceRunId": "20", "sourceSlot": "mask"},
        {"slot": "mask_b", "sourceRunId": "21", "sourceSlot": "mask"},
    ])

    pipe = synthesize_lineage_pipeline(_records([cyto, tissue, compare]), "23")

    selectors = [n for n in pipe["nodes"] if n.get("type") == "input_selector"]
    assert len(selectors) == 1
    assert selectors[0]["samples"] == ["s1"]
    node_of = {n["id"]: n for n in pipe["nodes"]}
    fed = sorted(
        (node_of[target]["params"]["model"], slot)
        for _, target, slot in _head_links(pipe)
    )
    assert fed == [("cyto2", "image"), ("tissuenet", "image")]


def test_roots_on_different_samples_get_a_selector_each():
    """Root runs that read different samples keep one selector per sample.
    A root with recorded reads is fed on its recorded slot; a root recorded
    before sample rows existed keeps the unslotted head link."""
    left = _mk("30", "segment", "s1", reads=[("image", "s1")])
    right = _mk("31", "segment", "s2")
    merge = _mk("32", "merge", "s1", parents=[
        {"slot": "a", "sourceRunId": "30", "sourceSlot": "mask"},
        {"slot": "b", "sourceRunId": "31", "sourceSlot": "mask"},
    ])

    pipe = synthesize_lineage_pipeline(_records([left, right, merge]), "32")

    selectors = [n for n in pipe["nodes"] if n.get("type") == "input_selector"]
    assert sorted(s["samples"][0] for s in selectors) == ["s1", "s2"]
    sample_of_head = {s["id"]: s["samples"][0] for s in selectors}
    assert sorted(
        (sample_of_head[head], slot) for head, _, slot in _head_links(pipe)
    ) == [("s1", "image"), ("s2", None)]


@workflow(purpose="Every recorded sample read is wired from one shared head per "
                  "sample into the slot it was read on, for roots and for steps "
                  "that read a sample beside an upstream; a run with no recorded "
                  "reads keeps the unslotted root head")
def test_recorded_sample_reads_are_wired_from_one_head_per_sample():
    """A root that read ``s1`` on ``dapi_image``, and a quantifier that read
    ``s1`` on ``raw`` beside the root's mask, are both fed from one ``s1``
    head. A legacy root on ``s2`` with no recorded reads keeps its head link
    with no slot. The report reads no sample and has in-set parents, so no
    head feeds it."""
    seg = _mk("40", "segment", "s1", reads=[("dapi_image", "s1")])
    quant = _mk("41", "quantify", "s1",
                parents=[{"slot": "mask", "sourceRunId": "40", "sourceSlot": "mask"}],
                reads=[("raw", "s1")])
    legacy = _mk("42", "load", "s2")
    report = _mk("43", "report", "s1", parents=[
        {"slot": "table", "sourceRunId": "41", "sourceSlot": "table"},
        {"slot": "reference", "sourceRunId": "42", "sourceSlot": "data"},
    ])

    pipe = synthesize_lineage_pipeline(_records([seg, quant, legacy, report]), "43")

    node_for = {n["method"]: n["id"] for n in pipe["nodes"] if n["type"] == "method"}
    heads = _heads(pipe)
    assert sorted(h["samples"] for h in heads) == [["s1"], ["s2"]], (
        "one head per sample, even though two runs read s1 in different roles"
    )
    sample_of_head = {h["id"]: h["samples"][0] for h in heads}
    assert sorted(
        (sample_of_head[head], target, slot)
        for head, target, slot in _head_links(pipe)
    ) == sorted([
        ("s1", node_for["segment"], "dapi_image"),
        ("s1", node_for["quantify"], "raw"),
        ("s2", node_for["load"], None),
    ])
    # The recorded root is fed once: no legacy head link beside its slot.
    assert [ln["targetHandle"] for ln in pipe["links"]
            if ln["target"] == node_for["segment"]] == ["dapi_image"]
    # Three input edges plus three head links.
    assert len(pipe["links"]) == 6


# =============================================================================
# Aggregator collapse
# =============================================================================


def test_aggregator_run_emits_fan_in_input_selector(tmp_path, monkeypatch):
    """Aggregator/bundledSamples case over REAL DB rows → WfcProvider.load()
    → synthesize.

    Two per-sample runs feed an ``__all__`` aggregator through real run_inputs
    edges; the bundled sample list is resolved from the on-disk pipeline.json
    fan-in selector exactly as ``WfcProvider.load()`` does in production. The
    aggregator must then emit one ``fan_mode='in'`` selector carrying that
    list.
    """
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    from wfc.persistence import reset_engine
    reset_engine()
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        mod = Module(name="m", description="x")
        s.add(mod)
        s.flush()
        pre = Method(name="preprocess", module_id=mod.id,
                     script_path="methods/preprocess/preprocess.py", env="container:demo")
        mrg = Method(name="merge_csv", module_id=mod.id,
                     script_path="methods/merge_csv/merge_csv.py", env="container:demo")
        s.add_all([pre, mrg])
        s.flush()
        r1 = Run(method_id=pre.id, pipeline_id="p1", status="completed", sample="s1")
        r2 = Run(method_id=pre.id, pipeline_id="p1", status="completed", sample="s2")
        s.add_all([r1, r2])
        s.flush()
        agg = Run(method_id=mrg.id, pipeline_id="p1", status="completed", sample="__all__")
        s.add(agg)
        s.flush()
        s.add(RunInput(run_id=agg.id, source_run_id=r1.id, input_name="data"))
        s.add(RunInput(run_id=agg.id, source_run_id=r2.id, input_name="data"))
        s.commit()
        agg_id = str(agg.id)

    # bundledSamples for the __all__ run come from the fan-in input_selector on
    # the frozen pipeline.json — the same file WfcProvider._load_bundled_samples
    # reads.
    pdir = tmp_path / ".runs" / "pipelines" / "p1"
    pdir.mkdir(parents=True)
    (pdir / "pipeline.json").write_text(json.dumps({
        "nodes": [{"id": "sel", "type": "input_selector",
                   "fan_mode": "in", "samples": ["s1", "s2"]}],
        "links": [],
        "samples": ["s1", "s2"],
    }))

    from wfc.canvas.wfc_provider import WfcProvider
    provider = WfcProvider(str(tmp_path))
    provider.load()

    pipe = synthesize_lineage_pipeline(provider.run_records(), agg_id)

    fan_in = [
        n
        for n in pipe["nodes"]
        if n.get("type") == "input_selector" and n.get("fan_mode") == "in"
    ]
    assert len(fan_in) == 1, "aggregator run must emit one fan_mode='in' selector"
    assert fan_in[0].get("samples") == ["s1", "s2"]

    # Top-level samples list is the bundled list (clicked run is __all__).
    assert pipe["samples"] == ["s1", "s2"]


def test_all_all_chain_emits_single_head_selector_no_midstream_collapse():
    """A linear chain of ``__all__`` runs (input_selector(fan_in) head feeds
    methods that each consume bundled samples) collapses to ONE fan-in
    selector at the head and direct method-to-method links downstream — not
    a redundant fan-in selector at every ``__all__→__all__`` boundary.

    A fan-in selector between each ``__all__`` pair would take its incoming
    edges on a ``targetHandle`` that the input_selector node does not
    render, leaving the canvas as disconnected (selector → method) pairs.
    """
    bundled = ["s1", "s2", "s3", "s4"]
    # The head run recorded one row per bundled sample, all on its bundle slot.
    a = _mk("1", "merge", "__all__", bundled=bundled,
            reads=[("tables", s) for s in bundled])
    b = _mk(
        "2",
        "filter",
        "__all__",
        parents=[{"slot": "data", "sourceRunId": "1"}],
        bundled=bundled,
    )
    c = _mk(
        "3",
        "scale",
        "__all__",
        parents=[{"slot": "data", "sourceRunId": "2"}],
        bundled=bundled,
    )
    records = _records([a, b, c])

    pipe = synthesize_lineage_pipeline(records, "3")

    selectors = [n for n in pipe["nodes"] if n.get("type") == "input_selector"]
    assert len(selectors) == 1, (
        f"expected one head fan-in selector, got {len(selectors)}: "
        f"{[s.get('id') for s in selectors]}"
    )
    assert selectors[0].get("fan_mode") == "in"
    assert selectors[0].get("samples") == bundled

    # 3 method nodes, 1 head selector → 4 nodes, 3 edges (linear chain).
    methods = [n for n in pipe["nodes"] if n.get("type") == "method"]
    assert len(methods) == 3
    assert len(pipe["nodes"]) == 4
    assert len(pipe["links"]) == 3

    # No edge targets the input_selector — the head selector is a pure
    # source. Otherwise edges silently drop in SvelteFlow.
    selector_ids = {s["id"] for s in selectors}
    assert not any(
        ln["target"] in selector_ids for ln in pipe["links"]
    ), "no synthesized edge should target an input_selector node"

    # Four bundle rows make one link, into the recorded bundle slot.
    head_run = next(n["id"] for n in methods if n["method"] == "merge")
    assert _head_links(pipe) == [(selectors[0]["id"], head_run, "tables")]


# =============================================================================
# Cross-pipeline boundary walk
# =============================================================================


def test_walks_through_run_reference_pipeline_boundary():
    """The lineage walk does not stop at pipeline boundaries.
    Two runs with different ``pipelineId`` connected via ``parentRunIds``
    must both appear in the synthesized graph."""
    upstream = _mk("10", "preprocess", "s1", pipeline_id="pA")
    downstream = _mk(
        "20",
        "score",
        "s1",
        pipeline_id="pB",
        parents=[{"slot": "data", "sourceRunId": "10"}],
    )
    records = _records([upstream, downstream])

    pipe = synthesize_lineage_pipeline(records, "20")

    methods = [n["method"] for n in pipe["nodes"] if n.get("type") == "method"]
    assert "preprocess" in methods, "ancestor in another pipeline must still be walked"
    assert "score" in methods


# =============================================================================
# Cycle defense
# =============================================================================


def test_cycle_in_parents_raises_synthesis_error():
    """A degenerate cycle in ``parentRunIds`` must surface as
    ``LineageSynthesisError`` so the endpoint can return 422."""
    a = _mk("1", "load", "s1", parents=[{"slot": "data", "sourceRunId": "2"}])
    b = _mk("2", "filter", "s1", parents=[{"slot": "data", "sourceRunId": "1"}])
    records = _records([a, b])

    # The BFS itself dedupes via visited-set so a 2-node cycle terminates;
    # we trigger the guard with a ``parentRunIds`` self-loop reference,
    # which would otherwise loop forever in a naive walk.
    self_loop = _mk(
        "3", "score", "s1", parents=[{"slot": "data", "sourceRunId": "3"}]
    )
    records_self = _records([self_loop])

    # Both layouts must collect successfully (BFS dedupes), but the algorithm
    # also has a 1000-hop hard cap. Force overflow by stuffing a long chain.
    runs = []
    for i in range(0, 1100):
        parents = (
            [{"slot": "data", "sourceRunId": str(i - 1)}] if i > 0 else []
        )
        runs.append(_mk(str(i), "step", "s1", parents=parents))
    records_long = _records(runs)

    with pytest.raises(LineageSynthesisError):
        synthesize_lineage_pipeline(records_long, "1099")


# =============================================================================
# 404-equivalent: unknown run id at module level
# =============================================================================


def test_unknown_run_returns_none_via_caller_check():
    """The synthesizer assumes the caller has verified ``run_id`` exists.
    When called with an unknown id, it raises ``LineageSynthesisError``
    rather than returning a meaningless empty pipeline (the endpoint
    catches the unknown-id case before invoking the synthesizer and
    returns 404)."""
    records = _records([])
    with pytest.raises(LineageSynthesisError):
        synthesize_lineage_pipeline(records, "missing")
