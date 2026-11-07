"""A pipeline's stored outcome: its ``pipeline_runs`` row.

Persistence owns the ``pipeline_runs`` table; this module is the only code
that writes its ``status`` and ``ended_at`` columns.

- :func:`record_pipeline_launch` writes a pipeline's row at its launch, as
  ``running``, with its name and start (``prepare_launch`` calls it, so the
  canvas run route, ``wfc run-pipeline`` and a bundle rerun all do).
- :func:`store_pipeline_outcome` is the one status helper: it works out a
  pipeline's status and end from its run rows through ``pipeline_status`` and
  writes both to the row. Every path that changes a pipeline's outcome calls
  it after its last row write: the success and failure endings, failing and
  cancelling a pipeline's running rows, the restart close-out and the end of
  ``run_pipeline``.
- :func:`backfill_pipeline_runs` is the canvas server's start-up pass: a row
  for each pipeline the run rows name that has none (a pipeline from before
  the table), then the helper's computation for every row.
- :func:`discard_pipeline_launch` removes the row of a launch that never
  started (its pipeline record could not be written).

The status rule: a pipeline's runs as ``(status, cancel_reason)`` and its
pending targets go to ``pipeline_status``. A pipeline has pending targets
only while it has a pipeline record: the runs its frozen document expects
minus the rows it has, never below zero. The end is its rows' latest finish
time once the status is not ``running``, else none.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import Step, task, workflow
from sqlmodel import col, select

from .. import layout
from ..persistence import PipelineRun, Run, get_session, write_transaction
from .lifecycle import PIPELINE_RUNNING, expected_targets, pipeline_status
from .pipeline_record import (
    PipelineRecordError,
    list_pipeline_records,
    read_pipeline_record,
)


def document_name(document: dict | None) -> str | None:
    """A pipeline document's name, when it carries a usable one.

    Args:
        document: The pipeline document, or None.

    Returns:
        The ``name`` field when it is a non-blank string, else None.
    """
    if not isinstance(document, dict):
        return None
    name = document.get("name")
    return name if isinstance(name, str) and name.strip() else None


def pipeline_document_name(project_root: Path | str, pipeline_id: str) -> str | None:
    """The name a pipeline was launched under, read from its frozen documents.

    ``pipeline.editable.json`` (the document as the user submitted it) is
    read first, then ``pipeline.json``; Layout names both.

    Args:
        project_root: The wfc project root.
        pipeline_id: The pipeline.

    Returns:
        The first usable name, or None when neither file carries one.
    """
    root = Path(project_root)
    for path in (layout.pipeline_editable_doc_path(root, pipeline_id),
                 layout.pipeline_doc_path(root, pipeline_id)):
        if not path.exists():
            continue
        try:
            name = document_name(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
        if name is not None:
            return name
    return None


@task(purpose="Write a pipeline's pipeline_runs row at its launch: running, "
              "with its name and start, no end",
      inputs="pipeline id, the launched document",
      outputs="the row, written or rewritten",
      critical="a row the id already has is rewritten as a fresh launch")
def record_pipeline_launch(pipeline_id: str, document: dict | None) -> None:
    """Record that a pipeline was launched.

    Args:
        pipeline_id: The launch's pipeline id.
        document: The launched document (its ``name`` is kept).
    """
    drafts = launched_drafts(document)
    with write_transaction() as session:
        row = session.get(PipelineRun, pipeline_id)
        if row is None:
            row = PipelineRun(pipeline_id=pipeline_id)
        row.name = document_name(document)
        row.started_at = datetime.now(UTC)
        row.status = PIPELINE_RUNNING
        row.ended_at = None
        row.draft_mode = bool(drafts)
        row.drafts = drafts or None
        row.draft_step_count = 0
        row.cleared_reason = None
        row.cleared_at = None
        session.add(row)


def launched_drafts(document: dict | None) -> list[str]:
    """The drafts a pipeline document runs, as ``<module>.<method>`` in document order.

    Args:
        document: The pipeline document, or None.

    Returns:
        Each draft node's method once; empty for a pipeline with no draft.
    """
    from ..graph import is_draft_node

    if not isinstance(document, dict):
        return []
    found: list[str] = []
    for node in document.get("nodes") or []:
        if isinstance(node, dict) and is_draft_node(node):
            name = f"{node.get('module')}.{node.get('method')}"
            if name not in found:
                found.append(name)
    return found


def discard_pipeline_launch(pipeline_id: str) -> bool:
    """Remove the row of a launch that never started.

    ``run_pipeline`` calls it when the pipeline record cannot be written:
    the pipeline is refused before any run row, so nothing ran.

    Args:
        pipeline_id: The refused launch's pipeline id.

    Returns:
        True when a row was removed.
    """
    with write_transaction() as session:
        row = session.get(PipelineRun, pipeline_id)
        if row is None:
            return False
        session.delete(row)
    return True


def pipeline_outcome(
    runs: Iterable[tuple[str, str | None, datetime | None]], pending: int = 0,
) -> tuple[str, datetime | None]:
    """A pipeline's status and end, from its runs and its pending targets.

    Args:
        runs: Each run row as ``(status, cancel_reason, finished_at)``.
        pending: How many expected targets have no row yet.

    Returns:
        ``(status, ended_at)``: the status by ``pipeline_status``, and the
        latest finish time among the rows when the status is not
        ``running`` (None while it runs, or when no row has a finish time).
    """
    rows = list(runs)
    status = pipeline_status(((s, r) for s, r, _ in rows), pending)
    if status == PIPELINE_RUNNING:
        return status, None
    finished = [f for _, _, f in rows if f is not None]
    # Naive and aware times compare as UTC (a draft step's beside the rows').
    return status, (max(finished, key=lambda f: f if f.tzinfo else f.replace(tzinfo=UTC))
                    if finished else None)


def _has_record(project_root: Path | str, pipeline_id: str) -> bool:
    """Whether the pipeline has a pipeline record (one that cannot be read has none)."""
    try:
        return read_pipeline_record(project_root, pipeline_id) is not None
    except PipelineRecordError:
        return False


def _expected_count(project_root: Path | str,
                    pipeline_id: str) -> tuple[int, int] | None:
    """How many runs and draft steps the frozen document expects, or None without one."""
    expected = expected_targets(pipeline_id, str(project_root))
    if expected is None:
        return None
    return len(expected.targets), len(expected.draft_targets)


def _draft_step_run(step: dict) -> tuple[str, str | None, datetime | None]:
    """A draft step as ``pipeline_status`` reads a run: status, cancel reason, finish.

    The finish is naive UTC, as the run rows' finish times come back.
    """
    finished: datetime | None = None
    stamp = step.get("finished_at")
    if stamp:
        try:
            moment = datetime.fromisoformat(str(stamp))
            if moment.tzinfo is not None:
                moment = moment.astimezone(UTC).replace(tzinfo=None)
            finished = moment
        except ValueError:
            finished = None
    return str(step.get("status") or "running"), step.get("cancel_reason"), finished


@dataclass(frozen=True)
class StoredOutcome:
    """What the helper wrote to one pipeline's row.

    Attributes:
        pipeline_id: The pipeline.
        status: The status written.
        ended_at: The end written.
    """

    pipeline_id: str
    status: str
    ended_at: datetime | None


def _store_outcomes(project_root: Path | str, pipeline_ids: list[str] | None,
                    recorded: set[str]) -> list[StoredOutcome]:
    """Work out and write the status and end of the given pipelines' rows.

    The expected counts are read first, for the recorded pipelines only;
    then one write transaction reads the run rows and writes each row.

    Args:
        project_root: The wfc project root.
        pipeline_ids: The pipelines, or None for every row of the table.
        recorded: The pipelines that have a pipeline record.

    Returns:
        What was written, one entry per row that exists.
    """
    from .draft import draft_steps_by_pipeline

    targets = (sorted(recorded) if pipeline_ids is None
               else [p for p in pipeline_ids if p in recorded])
    counts = {pid: _expected_count(project_root, pid) for pid in targets}
    # The draft steps each pipeline still has in its drafts' draft-run.json.
    drafted = draft_steps_by_pipeline(project_root)
    stored: list[StoredOutcome] = []
    with write_transaction() as session:
        query = select(PipelineRun)
        if pipeline_ids is not None:
            query = query.where(col(PipelineRun.pipeline_id).in_(pipeline_ids))
        rows = list(session.exec(query).all())
        if not rows:
            return stored
        run_query = select(Run.pipeline_id, Run.status, Run.cancel_reason,
                           Run.finished_at)
        if pipeline_ids is not None:
            run_query = run_query.where(col(Run.pipeline_id).in_(pipeline_ids))
        else:
            run_query = run_query.where(col(Run.pipeline_id).is_not(None))
        by_pipeline: dict[str, list[tuple[str, str | None, datetime | None]]] = {}
        for pid, status, reason, finished in session.exec(run_query).all():
            by_pipeline.setdefault(pid, []).append((status, reason, finished))
        for row in rows:
            runs = by_pipeline.get(row.pipeline_id, [])
            steps = [step for _draft, step in drafted.get(row.pipeline_id, [])]
            count = counts.get(row.pipeline_id)
            pending = 0
            if count is not None:
                recorded_count, draft_count = count
                pending = (max(recorded_count - len(runs), 0)
                           + max(draft_count - len(steps), 0))
            status, ended_at = pipeline_outcome(
                [*runs, *(_draft_step_run(step) for step in steps)], pending)
            row.status = status
            row.ended_at = ended_at
            if steps:
                row.draft_step_count = max(row.draft_step_count or 0, len(steps))
            session.add(row)
            stored.append(StoredOutcome(row.pipeline_id, status, ended_at))
    return stored


@task(purpose="The one status helper: work out a pipeline's status and end "
              "through pipeline_status from its run rows and its pending "
              "targets, and write both to its pipeline_runs row",
      inputs="project root, pipeline id; the pipeline's run rows and record",
      outputs="the status written, or None when the pipeline has no row",
      critical="the only writer of pipeline_runs.status after the launch and "
               "of pipeline_runs.ended_at; every outcome path calls it after "
               "its last row write")
def store_pipeline_outcome(project_root: Path | str, pipeline_id: str) -> str | None:
    """Store a pipeline's status and end, as its rows now give them.

    Args:
        project_root: The wfc project root (its frozen document and record).
        pipeline_id: The pipeline.

    Returns:
        The status written, or None when the pipeline has no row.
    """
    recorded = {pipeline_id} if _has_record(project_root, pipeline_id) else set()
    stored = _store_outcomes(project_root, [pipeline_id], recorded)
    return stored[0].status if stored else None


@dataclass(frozen=True)
class PipelineBackfill:
    """What the start-up pass did.

    Attributes:
        rows_added: The pipelines that got a row.
        outcomes: The status and end stored for every row.
    """

    rows_added: list[str]
    outcomes: list[StoredOutcome]


@workflow(purpose="Bring pipeline_runs up to the run rows at the canvas "
                  "server's start: a row for each pipeline the runs name that "
                  "has none, then the status helper's computation for every row",
          inputs="project root; the run rows, the pipeline_runs rows, the "
                 "pipeline records and frozen documents",
          outputs="PipelineBackfill: the pipelines given a row, the outcomes "
                  "stored",
          critical="idempotent: a second pass adds no row and stores the same "
                   "outcomes. Runs before History reads the table")
def backfill_pipeline_runs(project_root: Path | str) -> PipelineBackfill:
    """Give every past pipeline its row, then store every row's outcome.

    Args:
        project_root: The wfc project root.

    Returns:
        What the pass did.
    """
    口 = Step(step_num=1, name="Find the pipelines without a row",
             purpose="Every pipeline id the run rows name that pipeline_runs "
                     "lacks, with its first start (else its first finish)")
    with get_session() as session:
        have = set(session.exec(select(PipelineRun.pipeline_id)).all())
        firsts: dict[str, datetime | None] = {}
        for pid, started, finished in session.exec(
            select(Run.pipeline_id, Run.started_at, Run.finished_at)
            .where(col(Run.pipeline_id).is_not(None))
        ).all():
            if pid in have:
                continue
            when = started or finished
            prior = firsts.get(pid)
            if pid not in firsts or (when is not None
                                     and (prior is None or when < prior)):
                firsts[pid] = when

    口 = Step(step_num=2, name="Write their rows",
             purpose="One row per missing pipeline: its name from its frozen "
                     "documents through Layout, its start; the status and end "
                     "are left to the helper's computation")
    added = sorted(firsts)
    if added:
        with write_transaction() as session:
            for pid in added:
                if session.get(PipelineRun, pid) is not None:
                    continue
                session.add(PipelineRun(
                    pipeline_id=pid,
                    name=pipeline_document_name(project_root, pid),
                    started_at=firsts[pid],
                ))

    口 = Step(step_num=3, name="Store every row's outcome",
             purpose="The helper's computation over every row at once: the "
                     "recorded pipelines' expected counts, the run rows in "
                     "one read, the status and end in one write")
    recorded = {r.pipeline_id for r in list_pipeline_records(project_root)}
    outcomes = _store_outcomes(project_root, None, recorded)
    return PipelineBackfill(rows_added=added, outcomes=outcomes)


__all__ = [
    "PipelineBackfill",
    "StoredOutcome",
    "backfill_pipeline_runs",
    "discard_pipeline_launch",
    "document_name",
    "pipeline_document_name",
    "pipeline_outcome",
    "record_pipeline_launch",
    "store_pipeline_outcome",
]
