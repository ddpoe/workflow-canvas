"""``wfc cache push``: push every output, sample and blob awaiting a push, now.

The background worker pushes only while a pipeline runs, gives each entry
:data:`~wfc.storage.push_worker.PUSH_MAX_ATTEMPTS` tries, and never moves a
row left ``deferred`` (written while no remote was configured). The verb is
where a user asks for all of that at once: with a remote configured it moves
deferred rows to pending, re-arms the attempt count of every entry it
pushes, and runs the worker's own tick until a tick finds nothing it can
push. Each status change is a compare-and-set on the status read, so a
worker running beside it is never overwritten.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlalchemy import update
from sqlmodel import col, select

from ..persistence import Blob, PushStatus, RunOutput, Sample, get_session
from .push_worker import _push_worker_tick

_WAITING = (PushStatus.pending.value, PushStatus.failed.value)

#: The report's source names, in the order they are counted.
SOURCES = ("outputs", "samples", "blobs")


class NoRemoteConfiguredError(Exception):
    """``wfc cache push`` was asked to push with no remote configured."""


@dataclass(frozen=True)
class PushFailure:
    """One entry the push could not send.

    Attributes:
        content_hash: The entry's hash.
        kind: ``output``, ``sample`` or the blob's kind.
        reason: The error its row records.
    """

    content_hash: str
    kind: str
    reason: str


@dataclass
class CachePushReport:
    """What ``wfc cache push`` pushed.

    Attributes:
        queued: The entries it set out to push, per source (``outputs``,
            ``samples``, ``blobs``).
        pushed: How many of them are recorded pushed afterwards.
        failures: Each one whose row ended failed, with its reason.
    """

    queued: dict[str, int] = field(default_factory=dict)
    pushed: int = 0
    failures: list[PushFailure] = field(default_factory=list)


_MODELS: tuple[type[RunOutput] | type[Sample] | type[Blob], ...] = (RunOutput, Sample, Blob)


def _hash_column(model):
    return col(Blob.hash) if model is Blob else col(model.content_hash)


@task(
    purpose="Re-arm every output, sample and blob awaiting a push: deferred rows "
            "go pending and pending or failed rows get their attempt count back, "
            "each a compare-and-set on the status read",
    inputs="the database",
    outputs="the hashes to push per source, each with its kind",
)
def rearm_for_push() -> dict[str, dict[str, str]]:
    """Move deferred rows to pending and reset the attempts of the rows to push.

    Returns:
        Per source (``outputs``, ``samples``, ``blobs``), each hash to push
        with its kind (``output``, ``sample`` or the blob's kind).
    """
    targets: dict[str, dict[str, str]] = {}
    with get_session() as session:
        for source, model in zip(SOURCES, _MODELS, strict=True):
            column = _hash_column(model)
            session.execute(
                update(model)
                .where(col(model.push_status) == PushStatus.deferred.value)
                .where(column.isnot(None))
                .values(push_status=PushStatus.pending.value)
            )
            session.execute(
                update(model)
                .where(col(model.push_status).in_(_WAITING))
                .where(column.isnot(None))
                .values(push_attempts=0)
            )
            if model is Blob:
                rows = session.exec(
                    select(Blob.hash, Blob.kind)
                    .where(col(Blob.push_status).in_(_WAITING))
                ).all()
                targets[source] = dict(rows)
            else:
                kind = source[:-1]
                rows = session.exec(
                    select(column)
                    .where(col(model.push_status).in_(_WAITING))
                    .where(column.isnot(None))
                ).all()
                targets[source] = {h: kind for h in rows}
        session.commit()
    return targets


@task(
    purpose="Read back each entry the push set out to send: count those recorded "
            "pushed and list each failed one with its kind and recorded error",
    inputs="the hashes to push per source, with their kinds",
    outputs="a CachePushReport",
)
def push_report(targets: dict[str, dict[str, str]]) -> CachePushReport:
    """Build the report from the rows' statuses after the ticks.

    Args:
        targets: Per source, each hash pushed with its kind.

    Returns:
        The counts queued per source, the number pushed and each failure.
    """
    report = CachePushReport(queued={s: len(targets.get(s, {})) for s in SOURCES})
    with get_session() as session:
        for source, model in zip(SOURCES, _MODELS, strict=True):
            kinds = targets.get(source, {})
            if not kinds:
                continue
            column = _hash_column(model)
            statuses: dict[str, tuple[str, str | None]] = {}
            for h, status, error in session.exec(
                select(column, model.push_status, model.push_error)
                .where(column.in_(list(kinds)))
            ).all():
                if statuses.get(h, ("",))[0] != PushStatus.failed.value:
                    statuses[h] = (status, error)
            for h, kind in sorted(kinds.items()):
                status, error = statuses.get(h, ("", None))
                if status == PushStatus.pushed.value:
                    report.pushed += 1
                elif status == PushStatus.failed.value:
                    report.failures.append(
                        PushFailure(h, kind, error or "push failed"))
    return report


@workflow(
    purpose="wfc cache push: refuse with no remote; otherwise move deferred rows "
            "to pending, re-arm the attempt count of every entry it pushes, run "
            "the push worker's tick until a tick finds nothing it can push, and "
            "report the counts and each failure",
    inputs="project root; an optional callback told the counts before pushing",
    outputs="CachePushReport (queued per source, pushed, failures)",
)
def cache_push(
    project_dir: Path | str,
    *,
    announce: Callable[[dict[str, int]], None] | None = None,
) -> CachePushReport:
    """Push every output, sample and blob awaiting a push to the remote, now.

    Asks no confirmation: it copies bytes already in the cache to the remote
    and records the copy, removing nothing.

    Args:
        project_dir: Root directory of the wfc project.
        announce: Called with the counts per source before the first push.

    Returns:
        The report: entries queued per source, how many ended pushed, and
        each failure with its hash, kind and reason.

    Raises:
        NoRemoteConfiguredError: No remote is configured; nothing changed.
    """
    from .transport import has_remote_configured

    project_dir = Path(project_dir)
    口 = Step(step_num=1, name="Refuse without a remote",
             purpose="A push needs a remote; with none, nothing is changed")
    if not has_remote_configured(project_dir):
        raise NoRemoteConfiguredError(
            "no remote is configured in .dvc/config; wfc cache push needs a "
            "remote to push to. Nothing was changed.")

    口 = AutoStep(step_num=2, name="Re-arm the entries to push")
    targets = rearm_for_push()
    if announce is not None:
        announce({s: len(targets[s]) for s in SOURCES})

    口 = Step(step_num=3, name="Tick until nothing pushable is left",
             purpose="Each tick pushes what awaits a push and records each "
                     "outcome; a failed entry is retried until its attempts "
                     "run out, so the loop ends while the remote is down")
    for _tick in itertools.count(1):
        口 = AutoStep(step_num=3.1, name="One push-worker tick")
        pushed, failed = _push_worker_tick(project_dir)
        if not (pushed or failed):
            break

    口 = AutoStep(step_num=4, name="Report each entry's outcome")
    report = push_report(targets)
    return report
