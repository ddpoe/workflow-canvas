"""The one lineage relation, answered over literal run records.

A run's upstreams are its input edges in slot order, then the run a cache-hit
row reused. Ancestors are the closure of that relation and descendants its
inverse. Every fixture here is literal records: no database, no provider.

Requirement: ``docs/system/lineage.json``, cases ``catalog.relation-input-edges``,
``catalog.relation-reuse-edge`` and ``catalog.relation-cancellation``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from axiom_annotations import workflow

from wfc.lineage import ancestors, cancelled_descendants, descendants, upstreams


@dataclass
class Record:
    """A literal run record carrying the fields the relation reads."""

    id: str
    parentRunIds: List[str] = field(default_factory=list)
    cacheSourceRunId: Optional[str] = None
    cancelledDueToRunId: Optional[str] = None


def _by_id(*records: Record) -> Dict[str, Record]:
    """Index literal records by id, keeping their order."""
    return {record.id: record for record in records}


@workflow(purpose="Over input edges alone, a fan-in run's upstreams are both its "
                  "parents in slot order, a shared ancestor or descendant is "
                  "listed once, and walks over a hand-edited cycle terminate",
          inputs="Literal records: a diamond with a skip edge, and a cyclic set "
                 "with an edge to a run that is not loaded",
          outputs="The upstream, ancestor and descendant answers")
def test_relation_follows_every_input_edge_and_ends_on_a_cycle():
    """Lineage case ``relation-input-edges``."""
    # root feeds left and right, which fill join's two slots; tail is fed by
    # root and by join, so it is both a child and a grandchild of root.
    diamond = _by_id(
        Record("root"),
        Record("left", ["root"]),
        Record("right", ["root"]),
        Record("join", ["left", "right"]),
        Record("tail", ["root", "join"]),
    )
    assert upstreams(diamond["join"]) == ["left", "right"]
    above = ancestors(diamond, "tail")
    assert sorted(above) == ["join", "left", "right", "root"]
    assert len(above) == len(set(above))
    below = descendants(diamond, "root")
    assert sorted(below) == ["join", "left", "right", "tail"]
    assert len(below) == len(set(below))

    # x -> y -> z -> x by hand edit; w hangs off y and names a run that is
    # not loaded, which ends that branch.
    cyclic = _by_id(
        Record("x", ["z"]),
        Record("y", ["x"]),
        Record("z", ["y"]),
        Record("w", ["y", "gone"]),
    )
    assert sorted(ancestors(cyclic, "w")) == ["x", "y", "z"]
    assert sorted(descendants(cyclic, "x")) == ["w", "y", "z"]


@workflow(purpose="A cache-hit row reaches the run it reused: over a first run and "
                  "a re-run that cache-hit it, ancestors and descendants cross "
                  "the reuse edge, the cache-hit rows stay nodes, and upstreams "
                  "list input edges before the reuse edge",
          inputs="Literal records: A -> B executed; a re-run with cache hits "
                 "A' and B' and an executed C fed by both",
          outputs="Both directions of the relation, before and after the re-run")
def test_relation_crosses_the_reuse_edge_and_keeps_cache_hit_rows():
    """Lineage case ``relation-reuse-edge``."""
    first_run = [Record("A"), Record("B", ["A"])]
    re_run = [
        Record("A'", cacheSourceRunId="A"),
        Record("B'", ["A'"], cacheSourceRunId="B"),
        Record("C", ["A'", "B'"]),
    ]

    # Before any reuse, the relation is the input edges.
    alone = _by_id(*first_run)
    assert ancestors(alone, "B") == ["A"]
    assert descendants(alone, "A") == ["B"]
    assert descendants(alone, "B") == []

    history = _by_id(*first_run, *re_run)
    # Input edges first, in slot order, then the reuse edge; a root cache hit
    # with no input edge of its own still reaches its source.
    assert upstreams(history["B'"]) == ["A'", "B"]
    assert upstreams(history["A'"]) == ["A"]
    assert sorted(ancestors(history, "C")) == ["A", "A'", "B", "B'"]
    assert sorted(descendants(history, "B")) == ["B'", "C"]
    assert sorted(descendants(history, "A")) == ["A'", "B", "B'", "C"]


@workflow(purpose="The runs a failure cancelled are exactly the rows whose "
                  "cancellation pointer names it: a filter on the pointer, "
                  "with no walk",
          inputs="Literal records: a failed run, a collapsed chain of rows "
                 "cancelled because of it, and an unrelated cancelled row",
          outputs="The cancelled-descendants answers")
def test_cancelled_descendants_filter_on_the_pointer_without_a_walk():
    """Lineage case ``relation-cancellation``."""
    # fail's downstream chain was cancelled. The writer collapses the chain,
    # so leaf names fail rather than its parent mid. other failed apart, and
    # its cancelled row stray has nothing to do with fail.
    history = _by_id(
        Record("fail"),
        Record("mid", ["fail"], cancelledDueToRunId="fail"),
        Record("leaf", ["mid"], cancelledDueToRunId="fail"),
        Record("other"),
        Record("stray", ["other"], cancelledDueToRunId="other"),
    )
    assert cancelled_descendants(history, "fail") == ["mid", "leaf"]
    assert cancelled_descendants(history, "other") == ["stray"]
    # mid is upstream of leaf, but no pointer names it: the answer does not
    # walk the relation.
    assert cancelled_descendants(history, "mid") == []
