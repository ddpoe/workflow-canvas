"""Run tallies: how many runs each method, version, sample and env has.

One read for every door that shows run counts beside what it lists: the
Registry tab's methods, method versions and samples, and the Envs tab's env
specs. :func:`run_tallies` counts the project's ``Run`` rows per method, per
method version, per sample and per env spec (the ``env`` of the run's
method), each with the newest run's start time. Every row counts, a
cache-hit row included. Nothing is written.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from axiom_annotations import Step, task
from sqlmodel import col, func, select

from ..persistence import Method, Run, get_session


@dataclass(frozen=True)
class RunTally:
    """The runs one method, version, sample or env has.

    Attributes:
        count: How many ``Run`` rows name it.
        last_run_at: The newest of those rows' start times, or ``None`` when
            it has no run or no row records a start time.
    """

    count: int
    last_run_at: datetime | None


#: The tally of a key no run names.
NO_RUNS = RunTally(0, None)


@dataclass(frozen=True)
class RunTallies:
    """The project's run tallies, by method, version, sample and env spec.

    Read one key with the ``for_*`` methods; a key no run names reads as
    :data:`NO_RUNS`.

    Attributes:
        by_method: Tallies by ``Method`` row id.
        by_version: Tallies by ``MethodVersion`` row id (runs that record no
            version are in no entry).
        by_sample: Tallies by sample name (runs with no sample are in no
            entry).
        by_env: Tallies by env spec, the ``env`` of the run's method.
    """

    by_method: Mapping[int, RunTally] = field(default_factory=dict)
    by_version: Mapping[int, RunTally] = field(default_factory=dict)
    by_sample: Mapping[str, RunTally] = field(default_factory=dict)
    by_env: Mapping[str, RunTally] = field(default_factory=dict)

    def for_method(self, method_id: int | None) -> RunTally:
        """The tally of one method, by its row id."""
        return self.by_method.get(method_id, NO_RUNS) if method_id is not None else NO_RUNS

    def for_version(self, version_id: int | None) -> RunTally:
        """The tally of one method version, by its row id."""
        return self.by_version.get(version_id, NO_RUNS) if version_id is not None else NO_RUNS

    def for_sample(self, sample: str | None) -> RunTally:
        """The tally of one sample, by its name."""
        return self.by_sample.get(sample, NO_RUNS) if sample is not None else NO_RUNS

    def for_env(self, spec: str | None) -> RunTally:
        """The tally of one env spec."""
        return self.by_env.get(spec, NO_RUNS) if spec is not None else NO_RUNS


def _grouped(session, key, *, via_method: bool = False) -> dict[Any, RunTally]:
    """One GROUP BY over ``runs``: the count and newest start time per *key*.

    Rows whose key is null are left out.

    Args:
        session: An open session.
        key: The column to group by.
        via_method: *key* is a ``methods`` column (the env spec): each run is
            joined to its method.
    """
    query = select(key, func.count(col(Run.id)), func.max(col(Run.started_at)))
    if via_method:
        query = query.join(Method, col(Run.method_id) == col(Method.id))
    query = query.where(col(key).is_not(None)).group_by(key)
    return {value: RunTally(int(count), last) for value, count, last in session.exec(query)}


@task(purpose="Count the project's runs per method, method version, sample and env "
              "spec, each with the newest run's start time, for the doors that list "
              "them with their run counts",
      inputs="The process engine's database",
      outputs="The run tallies by method id, version id, sample name and env spec")
def run_tallies() -> RunTallies:
    """Count the project's runs per method, version, sample and env spec.

    Every ``Run`` row counts, a cache-hit row included. A run's env spec is
    its method's ``env``.

    Returns:
        The tallies, read in one session.
    """
    口 = Step(step_num=1, name="Group the run rows",
             purpose="One count and newest start time per method, version, "
                     "sample and env spec, null keys left out",
             outputs="Four maps of tallies")
    with get_session() as session:
        return RunTallies(
            by_method=_grouped(session, Run.method_id),
            by_version=_grouped(session, Run.version_id),
            by_sample=_grouped(session, Run.sample),
            by_env=_grouped(session, Method.env, via_method=True),
        )
