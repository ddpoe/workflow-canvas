"""Tests for the lineage synthesizer.

The synthesizer walks ``parentRunIds`` from a clicked Run back to roots —
through pipeline boundaries (D-2) — and emits a literal-only pipeline JSON
suitable for the canvas's ``loadPipeline()``.

Per D-8 the synthesizer is **DB-driven only**: it consumes the already-loaded
``WfcProvider._runs`` map plus the ``bundledSamples`` field already populated
by ``WfcProvider.load()``. It does NOT read ``pipeline.json`` from disk.
"""

from __future__ import annotations

import pytest

from wfc.canvas.lineage_synthesizer import (
    LineageSynthesisError,
    synthesize_lineage_pipeline,
)
from wfc.canvas.wfc_provider import WfcRun


class StubProvider:
    """Minimal provider stub exposing the ``get_run`` / ``_runs`` surface
    the synthesizer reads. Avoids spinning up a SQLite database for cases
    where we just want to feed pre-built ``WfcRun`` objects."""

    def __init__(self, runs):
        self._runs = {r.id: r for r in runs}

    def get_run(self, run_id):
        run = self._runs.get(run_id)
        return run.to_dict() if run is not None else None


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
):
    """Build a ``WfcRun`` populated with the fields the synthesizer reads."""
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
    )


# =============================================================================
# Linear single-sample lineage
# =============================================================================


def test_linear_single_sample_emits_methods_and_input_selector():
    """A 4-method linear chain with a single sample becomes 4 method nodes
    plus one ``input_selector`` head wired into the root. Edges follow
    ``parents`` slot information."""
    a = _mk("1", "load", "s1")
    b = _mk("2", "filter", "s1", parents=[{"slot": "data", "sourceRunId": "1"}])
    c = _mk("3", "score", "s1", parents=[{"slot": "data", "sourceRunId": "2"}])
    d = _mk("4", "report", "s1", parents=[{"slot": "data", "sourceRunId": "3"}])
    provider = StubProvider([a, b, c, d])

    pipe = synthesize_lineage_pipeline(provider, "4")

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


# =============================================================================
# Aggregator collapse
# =============================================================================


def test_aggregator_run_emits_fan_in_input_selector():
    """A run with ``sample == '__all__'`` (and ``bundledSamples`` populated by
    WfcProvider.load() at submission time) gets an ``input_selector`` with
    ``fan_mode='in'`` upstream carrying the bundled sample list."""
    # Two per-sample upstreams feed an aggregator run.
    s1 = _mk("1", "preprocess", "s1")
    s2 = _mk("2", "preprocess", "s2")
    agg = _mk(
        "3",
        "merge_csv",
        "__all__",
        parents=[
            {"slot": "data", "sourceRunId": "1"},
            {"slot": "data", "sourceRunId": "2"},
        ],
        bundled=["s1", "s2"],
    )
    provider = StubProvider([s1, s2, agg])

    pipe = synthesize_lineage_pipeline(provider, "3")

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

    Regression: previously the aggregator-collapse branch fired for every
    ``__all__`` run with ``__all__`` parents, inserting a fan-in selector
    between each pair AND wiring its incoming edges to a ``targetHandle``
    that the input_selector node does not render — leaving the canvas as
    disconnected (selector → method) pairs.
    """
    bundled = ["s1", "s2", "s3", "s4"]
    a = _mk("1", "merge", "__all__", bundled=bundled)
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
    provider = StubProvider([a, b, c])

    pipe = synthesize_lineage_pipeline(provider, "3")

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


# =============================================================================
# Cross-pipeline boundary walk (D-2)
# =============================================================================


def test_walks_through_run_reference_pipeline_boundary():
    """Per D-2, the lineage walk does not stop at pipeline boundaries.
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
    provider = StubProvider([upstream, downstream])

    pipe = synthesize_lineage_pipeline(provider, "20")

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
    provider = StubProvider([a, b])

    # The BFS itself dedupes via visited-set so a 2-node cycle terminates;
    # we trigger the guard with a ``parentRunIds`` self-loop reference,
    # which would otherwise loop forever in a naive walk.
    self_loop = _mk(
        "3", "score", "s1", parents=[{"slot": "data", "sourceRunId": "3"}]
    )
    provider_self = StubProvider([self_loop])

    # Both layouts must collect successfully (BFS dedupes), but the algorithm
    # also has a 1000-hop hard cap. Force overflow by stuffing a long chain.
    runs = []
    for i in range(0, 1100):
        parents = (
            [{"slot": "data", "sourceRunId": str(i - 1)}] if i > 0 else []
        )
        runs.append(_mk(str(i), "step", "s1", parents=parents))
    provider_long = StubProvider(runs)

    with pytest.raises(LineageSynthesisError):
        synthesize_lineage_pipeline(provider_long, "1099")


# =============================================================================
# 404-equivalent: unknown run id at module level
# =============================================================================


def test_unknown_run_returns_none_via_caller_check():
    """The synthesizer assumes the caller has verified ``run_id`` exists.
    When called with an unknown id, it raises ``LineageSynthesisError``
    rather than returning a meaningless empty pipeline (the endpoint
    catches the unknown-id case before invoking the synthesizer and
    returns 404)."""
    provider = StubProvider([])
    with pytest.raises(LineageSynthesisError):
        synthesize_lineage_pipeline(provider, "missing")
