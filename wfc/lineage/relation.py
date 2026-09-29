"""The realized-lineage relation over run records.

A run's upstreams are its input edges, in slot order, followed by the run a
cache-hit row reused. Ancestors are the transitive closure of that relation
and descendants its inverse. A run reached through a reuse edge is as much an
ancestor as one reached through an input edge, and cache-hit rows stay nodes.
The runs a failure cancelled are a filter on each record's cancellation
pointer, not a walk.

Every function here is pure: it takes run records as values and touches no
database, disk or provider. Turning rows into records is the caller's job.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Mapping, Optional, Protocol, Sequence


class RunRecord(Protocol):
    """A realized run, as the lineage rules read it.

    Attributes:
        id: The run's id.
        parentRunIds: The upstream run of each input edge, in slot order.
        cacheSourceRunId: For a cache-hit row, the run whose outputs it
            reused; ``None`` for an executed run.
        cancelledDueToRunId: For a cancelled row, the failed run that caused
            it; ``None`` otherwise.
    """

    id: str
    parentRunIds: Sequence[str]
    cacheSourceRunId: Optional[str]
    cancelledDueToRunId: Optional[str]


def upstreams(record: RunRecord) -> List[str]:
    """Return a run's upstreams: input edges in slot order, then the reuse edge.

    Each upstream is listed once, at its first position. The first upstream
    of a run with input edges is therefore its first slot's parent, and the
    first upstream of a root cache-hit row is the run it reused.

    Args:
        record: The run whose upstreams are wanted.

    Returns:
        Upstream run ids, input edges first, without repeats.
    """
    result: List[str] = []
    for parent in record.parentRunIds:
        if parent not in result:
            result.append(parent)
    source = record.cacheSourceRunId
    if source is not None and source not in result:
        result.append(source)
    return result


def ancestors(records: Mapping[str, RunRecord], run_id: str) -> List[str]:
    """Return every run upstream of ``run_id``, breadth-first, each once.

    Args:
        records: The loaded runs, by id.
        run_id: The run whose ancestors are wanted. It is not listed.

    Returns:
        Ancestor ids, nearest first. An upstream missing from ``records``
        ends its branch and is not listed, and a cycle in hand-edited
        records terminates.
    """
    start = records.get(run_id)
    if start is None:
        return []
    seen = {run_id}
    found: List[str] = []
    frontier = deque(upstreams(start))
    while frontier:
        current = frontier.popleft()
        if current in seen:
            continue
        seen.add(current)
        record = records.get(current)
        if record is None:
            continue
        found.append(current)
        frontier.extend(upstreams(record))
    return found


def descendants(records: Mapping[str, RunRecord], run_id: str) -> List[str]:
    """Return every run downstream of ``run_id``, depth-first, each once.

    The inverse of :func:`ancestors`: a run is a child of each of its
    upstreams.

    Args:
        records: The loaded runs, by id. Siblings are visited in this order.
        run_id: The run whose descendants are wanted. It is not listed.

    Returns:
        Descendant ids in pre-order. A cycle in hand-edited records
        terminates.
    """
    children: Dict[str, List[str]] = {}
    for record in records.values():
        for upstream in upstreams(record):
            children.setdefault(upstream, []).append(record.id)
    seen = {run_id}
    found: List[str] = []
    stack = list(reversed(children.get(run_id, [])))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        found.append(current)
        stack.extend(reversed(children.get(current, [])))
    return found


def cancelled_descendants(records: Mapping[str, RunRecord], run_id: str) -> List[str]:
    """Return the runs cancelled because ``run_id`` failed.

    The answer is a filter on each record's cancellation pointer, not a walk.
    The writer collapses a cancelled chain, so every row in it names the failed
    run at its root rather than an intermediate.

    Args:
        records: The loaded runs, by id. Matches are listed in this order.
        run_id: The failed run.

    Returns:
        The ids of the runs whose cancellation pointer names ``run_id``.
    """
    return [record.id for record in records.values()
            if record.cancelledDueToRunId == run_id]
