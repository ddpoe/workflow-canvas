"""Run bundle selection over plain run records: which runs a bundle holds, in order.

Tier 1: the selection is pure over the records Lineage reads, so these cases
hand it records shaped like Persistence's reader returns. The selection over
runs production made is ``tests/test_run_bundle_plan.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from wfc.run_bundle import select_runs


@dataclass(frozen=True)
class _Record:
    id: str
    parentRunIds: tuple[str, ...] = ()
    cacheSourceRunId: str | None = None
    cancelledDueToRunId: str | None = None


def _records(*records: _Record) -> dict[str, _Record]:
    return {r.id: r for r in records}


def test_shared_upstream_and_fan_in_are_each_one_step_in_dependency_order():
    """Two leaves over one upstream and a fan-in hold each run once, upstream first."""
    records = _records(
        _Record("1"),
        _Record("2", ("1",)), _Record("3", ("1",)),
        _Record("4", ("3", "2")),          # fan-in, right slot first
        _Record("5", ("1",)),
    )
    selection = select_runs(records, [4, 5])

    assert selection.steps == (1, 2, 3, 4, 5)
    assert selection.parents[4] == (3, 2), "slot order is kept"
    assert selection.substitutions == () and selection.missing == ()


def test_a_cache_hit_row_is_replaced_by_its_original_where_it_is_selected_and_read():
    """A selected hit row and a hit row a consumer reads both become the original."""
    records = _records(
        _Record("10"),
        _Record("11", cacheSourceRunId="10"),   # a hit of 10
        _Record("12", ("11",)),                 # reads the hit row
        _Record("13", ("12",)),
        _Record("14", ("12",), cacheSourceRunId="13"),
    )
    selection = select_runs(records, [14, 11])

    assert selection.steps == (10, 12, 13)
    assert selection.parents[12] == (10,), "the consumer reads the original"
    assert selection.substitutions == ((11, 10), (14, 13))


def test_ties_are_broken_by_run_id_and_unknown_ids_are_listed():
    """Independent runs are ordered by id; an id with no record is missing."""
    records = _records(_Record("7"), _Record("3"), _Record("9", ("7", "3")))
    selection = select_runs(records, ["9", 42])

    assert selection.steps == (3, 7, 9)
    assert selection.missing == (42,)
