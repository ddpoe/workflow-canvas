"""Persistence's run-graph reader: run rows as the records Lineage walks.

The reader turns ``runs`` and ``run_inputs`` rows into values that satisfy
``wfc.lineage.relation.RunRecord``: the run's id, the parent run of each
input edge in slot order, the run a cache-hit row reused, and the failed run
a cancelled row names. It reads the whole project or a set of ids, and
returns values only: what an edge means is Lineage's.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung, so every edge is the one the claim wrote.
"""

from __future__ import annotations

from axiom_annotations import workflow

from tests.fixtures.routes import completed_run
from tests.harness import Scenario, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc.lineage import ancestors
from wfc.persistence import read_run_records


def _fan_in() -> Scenario:
    """Two roots feeding one join, wired ``b`` before ``a``."""
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)]),
               node("b", inputs=[wire(SELECTOR_ID)], params={"k": 3}),
               node("join", inputs=[wire("b", target_slot="left"),
                                    wire("a", target_slot="right")])],
        samples=["s1"], pipeline_id="rg-fanin", name="rg_fanin",
    )


@workflow(purpose="The run-graph reader gives each run its parents in slot order "
                  "and a cache-hit row the run it reused, so Lineage's ancestor "
                  "walk over the records reaches every run above; given ids, it "
                  "reads only those runs",
          inputs="A fan-in run over two upstream runs, then the same join again "
                 "(a cache hit), all driven through production's phases",
          outputs="The records' edges equal what the claim recorded, the walk "
                  "from the cache hit reaches the join and both roots, and a "
                  "read by ids holds exactly those ids")
def test_the_reader_gives_lineage_each_runs_edges(tmp_project, monkeypatch):
    scenario = _fan_in()
    first = completed_run(tmp_project, monkeypatch=monkeypatch,
                          scenario=scenario, target="a")
    second = completed_run(tmp_project, monkeypatch=monkeypatch,
                           scenario=scenario, target="b")
    join = completed_run(tmp_project, monkeypatch=monkeypatch,
                         scenario=scenario, target="join")
    hit = completed_run(tmp_project, monkeypatch=monkeypatch,
                        scenario=scenario, target="join")
    assert hit.run_row["cache_source_run_id"] == join.run_id, (
        "the second join of the same key is a cache hit"
    )
    a, b, j, h = (str(r.run_id) for r in (first, second, join, hit))

    records = read_run_records()

    assert set(records) == {a, b, j, h}
    assert list(records[j].parentRunIds) == [b, a], "slot order, as wired"
    assert records[j].cacheSourceRunId is None
    assert records[h].cacheSourceRunId == j
    assert list(records[a].parentRunIds) == [], "a sample read is no parent"
    assert all(r.cancelledDueToRunId is None for r in records.values())
    assert all(r.id == key for key, r in records.items())
    assert set(ancestors(records, h)) == {j, a, b}

    subset = read_run_records([int(h), j])
    assert set(subset) == {h, j}
    assert subset[h] == records[h]
