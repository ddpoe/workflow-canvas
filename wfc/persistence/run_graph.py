"""The run-graph reader: run rows as values, for the readers that walk them.

Two reads, both returning plain values:

- :func:`read_run_records` gives each run its edges: the parent run of each
  input edge in slot order, the run a cache-hit row reused, and the failed
  run a cancelled row names. Its records satisfy Lineage's ``RunRecord``
  protocol by shape, so Lineage walks them without this package importing
  Lineage.
- :func:`list_runs` gives each run's id, pipeline, node, method, sample and
  status, reading ``runs`` with its method, module and method version.

This module owns how the rows are read, not what they mean: which runs a
walk reaches, or what a status says to a user, is the caller's. The one
composed value is a listing's method, ``<module>.<method> v<N>``, the
method's identity as the registry names it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from axiom_annotations import Step, task
from sqlmodel import col, select

from .engine import get_session
from .schema import Method, MethodVersion, Module, Run, RunInput


@dataclass(frozen=True)
class RunGraphRecord:
    """One run's edges, as Lineage's ``RunRecord`` reads them.

    The field names are the protocol's, so a record satisfies it as is.

    Attributes:
        id: The run's id.
        parentRunIds: The parent run of each input edge, in slot order.
        cacheSourceRunId: For a cache-hit row, the run whose outputs it
            reused; ``None`` for an executed run.
        cancelledDueToRunId: For a cancelled row, the failed run that
            caused it; ``None`` otherwise.
    """

    id: str
    parentRunIds: tuple[str, ...]
    cacheSourceRunId: str | None
    cancelledDueToRunId: str | None


@dataclass(frozen=True)
class RunListing:
    """One run as ``wfc runs`` lists it.

    Attributes:
        run_id: The run's id.
        pipeline_id: The pipeline execution the run belongs to, or ``None``
            for a run no pipeline launched.
        node_id: The pipeline node the run executed, or ``None`` for a run
            no node wrote (``register_run``).
        method: The method the run ran, as ``<module>.<method> v<N>``
            (without `` v<N>`` when the run records no version label), or
            ``None`` for a run with no method.
        sample: The sample the run ran on, or ``None``.
        status: The row's status, as recorded.
    """

    run_id: int
    pipeline_id: str | None
    node_id: str | None
    method: str | None
    sample: str | None
    status: str


def _optional_id(value: int | None) -> str | None:
    """A nullable run-id column as Lineage's string id."""
    return str(value) if value is not None else None


@task(purpose="Read run and run-input rows into records Lineage walks: each run's "
              "parents in slot order, the run a cache hit reused and the run a "
              "cancellation names, for the whole project or a set of ids",
      inputs="Optional run ids; the process engine's database",
      outputs="Records by run id")
def read_run_records(
    run_ids: Iterable[int | str] | None = None,
) -> dict[str, RunGraphRecord]:
    """Read runs as records, by id.

    An input row that names a source run is a parent edge, and the rows are
    read in insertion order, which is the slot order the claim wrote them
    in. A sample row and a row whose source run was deleted are not edges.

    Args:
        run_ids: The runs to read. ``None`` reads every run in the project.
            An id with no row is left out.

    Returns:
        The records by run id (a string), in run-id order.
    """
    wanted = None if run_ids is None else sorted({int(r) for r in run_ids})

    口 = Step(step_num=1, name="Read the run rows",
             purpose="Each run's id and its reuse and cancellation pointers",
             outputs="The run rows, in id order")
    with get_session() as session:
        runs_query = select(Run.id, Run.cache_source_run_id,
                            Run.cancelled_due_to_run_id).order_by(col(Run.id))
        inputs_query = (select(RunInput.run_id, RunInput.source_run_id)
                        .where(col(RunInput.source_run_id).is_not(None))
                        .order_by(col(RunInput.id)))
        if wanted is not None:
            runs_query = runs_query.where(col(Run.id).in_(wanted))
            inputs_query = inputs_query.where(col(RunInput.run_id).in_(wanted))
        run_rows = session.exec(runs_query).all()

        口 = Step(step_num=2, name="Read the parent edges",
                 purpose="Each input row naming a source run, in the order the "
                         "claim wrote it",
                 outputs="Parent run ids per run, in slot order")
        parents: dict[int, list[str]] = {}
        for run_id, source_run_id in session.exec(inputs_query):
            parents.setdefault(run_id, []).append(str(source_run_id))

    口 = Step(step_num=3, name="Assemble the records",
             purpose="One value per run with string ids, as Lineage reads them",
             outputs="Records by run id")
    return {
        str(run_id): RunGraphRecord(
            id=str(run_id),
            parentRunIds=tuple(parents.get(run_id, ())),
            cacheSourceRunId=_optional_id(cache_source),
            cancelledDueToRunId=_optional_id(cancelled_due),
        )
        for run_id, cache_source, cancelled_due in run_rows
    }


def _method_label(module_name: str | None, method_name: str | None,
                  version_label: int | None) -> str | None:
    """A run's method as ``wfc runs`` prints it: ``<module>.<method> v<N>``."""
    if method_name is None:
        return None
    version = f" v{version_label}" if version_label is not None else ""
    return f"{module_name}.{method_name}{version}"


@task(purpose="List every run's id, pipeline, node, method, sample and status, "
              "optionally for one pipeline or one method only",
      inputs="An optional pipeline id and method name; the process engine's database",
      outputs="One listing per run, in run-id order")
def list_runs(pipeline_id: str | None = None,
              method: str | None = None) -> list[RunListing]:
    """List the project's runs.

    Args:
        pipeline_id: Keep only this pipeline's runs. ``None`` lists every run.
        method: Keep only the runs of this method, named with its module
            (``mod.plot``). ``None`` lists every run. Combines with
            *pipeline_id*.

    Returns:
        One :class:`RunListing` per run, oldest first.

    Raises:
        ValueError: *method* has no module part.
    """
    query = (select(Run, Module.name, Method.name, MethodVersion.label)
             .outerjoin(Method, col(Run.method_id) == col(Method.id))
             .outerjoin(Module, col(Method.module_id) == col(Module.id))
             .outerjoin(MethodVersion, col(Run.version_id) == col(MethodVersion.id))
             .order_by(col(Run.id)))
    if pipeline_id is not None:
        query = query.where(Run.pipeline_id == pipeline_id)
    if method is not None:
        module_name, dot, method_name = method.partition(".")
        if not (dot and module_name and method_name):
            raise ValueError(f"name the method with its module (module.method), "
                             f"not {method!r}")
        query = query.where(Method.name == method_name, Module.name == module_name)
    with get_session() as session:
        return [RunListing(run_id=int(run.id or 0), pipeline_id=run.pipeline_id,
                           node_id=run.node_id,
                           method=_method_label(module, name, label),
                           sample=run.sample, status=run.status)
                for run, module, name, label in session.exec(query)]
