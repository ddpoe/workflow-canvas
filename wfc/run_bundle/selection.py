"""Which runs a bundle holds: the selected runs, every run above them, originals only.

A bundle ships each run that produced something the selected runs read, once.
Lineage's ancestor walk reaches every run above a selected run, by input
edges and by reuse edges; a cache-hit row is then replaced by the run it
reused, so every step is a run that executed. A consumer whose input edge
names a cache-hit row reads the original's outputs, so its parents are named
by their originals too. Steps are ordered so that each comes after the steps
it reads from, ties broken by run id.

This module is pure: it reads the records it is handed and nothing else.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from axiom_annotations import Step, task

from ..lineage import RunRecord, ancestors


@dataclass(frozen=True)
class Selection:
    """The runs a bundle holds, in dependency order.

    Attributes:
        steps: Every executed run the bundle holds, each once, each after
            the runs it reads from; ties are broken by run id.
        parents: For each step, the steps its input edges read from, in slot
            order without repeats, each named by its original run.
        substitutions: ``(cache-hit run, original run)`` for every cache-hit
            row the walk reached (a selected one included), by hit run id.
        missing: Requested run ids the records do not hold.
    """

    steps: tuple[int, ...]
    parents: Mapping[int, tuple[int, ...]]
    substitutions: tuple[tuple[int, int], ...]
    missing: tuple[int, ...]


def _original(records: Mapping[str, RunRecord], run_id: str) -> str:
    """The executed run behind *run_id*: its reuse edge followed while it is held.

    One hop is the rule (a cache hit never reuses a cache-hit row), but the
    walk follows the chain and stops at a cycle, so hand-edited records end.

    Args:
        records: The loaded runs, by id.
        run_id: A run in *records*.

    Returns:
        The id of the first run on the chain with no reuse edge, or the last
        one held when the chain leaves *records*.
    """
    seen = {run_id}
    current = run_id
    while True:
        source = records[current].cacheSourceRunId
        if source is None or source not in records or source in seen:
            return current
        seen.add(source)
        current = source


def _dependency_order(steps: Iterable[int],
                      parents: Mapping[int, tuple[int, ...]]) -> tuple[int, ...]:
    """Order steps so each follows the steps it reads from; ties by run id.

    Args:
        steps: The step run ids.
        parents: Each step's parent steps.

    Returns:
        The steps in dependency order. A cycle in hand-edited records leaves
        its runs out of the dependency order and appends them by id.
    """
    pending = {step: set(parents.get(step, ())) for step in steps}
    children: dict[int, list[int]] = {step: [] for step in pending}
    for step, ups in pending.items():
        for up in ups:
            children[up].append(step)
    ready = [step for step, ups in pending.items() if not ups]
    heapq.heapify(ready)
    ordered: list[int] = []
    while ready:
        step = heapq.heappop(ready)
        ordered.append(step)
        for child in children[step]:
            pending[child].discard(step)
            if not pending[child]:
                heapq.heappush(ready, child)
    placed = set(ordered)
    ordered.extend(sorted(step for step in pending if step not in placed))
    return tuple(ordered)


@task(purpose="Select a bundle's runs: the requested runs plus every ancestor, each "
              "once, with each cache-hit row replaced by the run it reused, in "
              "dependency order with ties by run id",
      inputs="The run records by id (Persistence's run-graph reader) and the "
             "requested run ids",
      outputs="The selection: steps in order, each step's parents, the "
              "substitutions made and the requested ids not found")
def select_runs(records: Mapping[str, RunRecord],
                run_ids: Iterable[int | str]) -> Selection:
    """Select the runs a bundle of *run_ids* holds.

    Args:
        records: Every run, by id, as Lineage reads them.
        run_ids: The runs the user asked for.

    Returns:
        The :class:`Selection`.
    """
    口 = Step(step_num=1, name="Walk up from each requested run",
             purpose="The requested runs and every ancestor Lineage reaches, by "
                     "input and reuse edges; a requested id with no record is "
                     "missing",
             outputs="The reached run ids and the missing ones")
    requested = list(dict.fromkeys(str(r) for r in run_ids))
    missing = tuple(sorted(int(r) for r in requested if r not in records))
    reached: list[str] = []
    for run_id in requested:
        if run_id in records:
            reached.append(run_id)
            reached.extend(ancestors(records, run_id))

    口 = Step(step_num=2, name="Replace cache hits by their originals",
             purpose="Every reached run is named by the executed run behind it, "
                     "and each replacement is kept for the README",
             outputs="The step ids and the substitutions")
    substitutions: dict[int, int] = {}
    steps: set[int] = set()
    for run_id in reached:
        original = _original(records, run_id)
        if original != run_id:
            substitutions[int(run_id)] = int(original)
        steps.add(int(original))

    口 = Step(step_num=3, name="Name each step's parents by their originals",
             purpose="A consumer whose input edge names a cache-hit row reads the "
                     "original's outputs, so the edge points at the original",
             outputs="Parents per step, in slot order without repeats")
    parents: dict[int, tuple[int, ...]] = {}
    for step in steps:
        named = [int(_original(records, p)) for p in records[str(step)].parentRunIds
                 if p in records]
        parents[step] = tuple(p for p in dict.fromkeys(named) if p in steps)

    口 = Step(step_num=4, name="Order the steps",
             purpose="Each step after the steps it reads from, ties by run id",
             outputs="The selection")
    return Selection(steps=_dependency_order(steps, parents), parents=parents,
                     substitutions=tuple(sorted(substitutions.items())),
                     missing=missing)
