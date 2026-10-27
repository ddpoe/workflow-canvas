"""The blob table: cache entries no output or sample row owns, and their pushes.

Storage is the only writer of ``blobs``. A row is entered when its bytes are
already in the cache (:func:`enter_blob`, insert if absent on the hash), with
the first push status :func:`wfc.storage.first_push_status` decides. The push
worker reads the rows awaiting a push (:func:`snapshot_pending_blobs`), claims
them in flight (:func:`claim_blobs_in_flight`) and records each outcome
(:func:`record_blob_outcomes`); before a pipeline starts,
:func:`reset_orphan_blobs` re-arms the recent ones a stopped worker left.
``wfc cache push`` (:func:`wfc.storage.cache_push.rearm_for_push`) moves
deferred rows to pending and gives the rows it pushes their attempts back.

Several processes may share one database, so every status change is
conditioned on the status read: an ``UPDATE ... WHERE push_status = <old>``
(or ``IN (<old>, ...)``) that applies only when the row still holds a status
the writer expects. A pushed row is never set back to pending, in flight or
failed. A row another live worker holds in flight can be re-armed to pending
by :func:`reset_orphan_blobs` at a pipeline start (it cannot tell a live
worker's claim from a stopped one's); the entry may then be pushed a second
time, which is harmless: a push copies the same bytes under the same hash,
and an outcome is recorded only while the row is still in flight. Nothing
is ordered by the local clock, nothing takes a file lock, and no SQL is
particular to one database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from ..persistence import Blob, PushStatus, get_session

#: The kind of a pipeline document kept by its canonical-JSON hash.
KIND_PIPELINE_DOCUMENT = "pipeline-document"

#: The kind of an environment's content blob, its env fingerprint.
KIND_ENV_FINGERPRINT = "env-fingerprint"

_AWAITING = (PushStatus.pending.value, PushStatus.failed.value)
_ORPHANED = (PushStatus.pending.value, PushStatus.in_flight.value, PushStatus.failed.value)


def enter_blob(
    content_hash: str, kind: str, project_dir: Path | str, *, size: int | None = None
) -> bool:
    """Enter a blob row for bytes already in the cache, unless one exists.

    Insert if absent on the hash: an existing row (this process's or another
    writer's) is left exactly as it is, so a pushed blob stays pushed.

    Args:
        content_hash: The entry's hash (an md5 hex, or a ``.dir`` hash).
        kind: What the bytes are, e.g. :data:`KIND_PIPELINE_DOCUMENT`.
        project_dir: Root directory of the wfc project (for the first push
            status: pending with a remote, deferred without).
        size: The content's size in bytes, when known.

    Returns:
        True when this call inserted the row, False when it already existed.
    """
    from .push_worker import first_push_status

    status = first_push_status(project_dir).value
    with get_session() as session:
        if session.get(Blob, content_hash) is not None:
            return False
        session.add(Blob(hash=content_hash, kind=kind, push_status=status, size=size))
        try:
            session.commit()
        except IntegrityError:
            # Another writer inserted it between the read and the commit.
            session.rollback()
            return False
    return True


def snapshot_pending_blobs(max_attempts: int) -> dict[str, str]:
    """Read the blob rows awaiting a push: pending or failed, under the limit.

    Args:
        max_attempts: Rows with this many attempts or more are left out.

    Returns:
        Each awaiting blob's kind, by hash.
    """
    with get_session() as session:
        rows = session.exec(
            select(Blob)
            .where(col(Blob.push_status).in_(_AWAITING))
            .where(Blob.push_attempts < max_attempts)
        ).all()
        return {row.hash: row.kind for row in rows}


def claim_blobs_in_flight(kinds_by_hash: Mapping[str, str]) -> dict[str, str]:
    """Move each snapshot blob from pending or failed to in flight.

    A compare-and-set per row: a row another worker claimed (or finished)
    since the snapshot is not claimed again, and this worker leaves it alone.

    Args:
        kinds_by_hash: The snapshot's blobs, kind by hash.

    Returns:
        The blobs this call claimed, kind by hash.
    """
    claimed: dict[str, str] = {}
    with get_session() as session:
        for content_hash, kind in kinds_by_hash.items():
            result = session.execute(
                update(Blob)
                .where(col(Blob.hash) == content_hash)
                .where(col(Blob.push_status).in_(_AWAITING))
                .values(push_status=PushStatus.in_flight.value)
            )
            if result.rowcount:
                claimed[content_hash] = kind
        session.commit()
    return claimed


def record_blob_outcomes(
    hashes: Iterable[str], errors: Mapping[str, str | None]
) -> int:
    """Record each claimed blob's push outcome, only while it is still in flight.

    Pushed: status pushed, the time, no error. Failed: status failed, one
    more attempt and the entry's own error. A row no longer in flight (an
    orphan reset re-armed it meanwhile) is left as it is.

    Args:
        hashes: The blobs this worker claimed in flight.
        errors: Each hash's error, or None when the remote accepted it; a
            hash missing from it counts as failed.

    Returns:
        Number of blob rows marked pushed.
    """
    now = datetime.now(UTC)
    pushed = 0
    with get_session() as session:
        for content_hash in hashes:
            error = errors.get(content_hash, "push failed")
            stmt = (
                update(Blob)
                .where(col(Blob.hash) == content_hash)
                .where(col(Blob.push_status) == PushStatus.in_flight.value)
            )
            if error is None:
                stmt = stmt.values(
                    push_status=PushStatus.pushed.value, pushed_at=now, push_error=None,
                )
            else:
                stmt = stmt.values(
                    push_status=PushStatus.failed.value,
                    push_attempts=Blob.push_attempts + 1,
                    push_error=error,
                )
            result = session.execute(stmt)
            if error is None and result.rowcount:
                pushed += 1
        session.commit()
    return pushed


def reset_orphan_blobs(cutoff: datetime) -> int:
    """Re-arm recent blob rows left pending, in flight or failed.

    A row stored after *cutoff* gets its attempts back and goes pending; an
    older one is left alone, so years-old failures are not retried on every
    pipeline start. Each reset is a compare-and-set on the status read.

    Args:
        cutoff: Rows stored at or before this time are not reset.

    Returns:
        Number of blob rows reset.
    """
    n = 0
    with get_session() as session:
        rows = session.exec(
            select(Blob)
            .where(col(Blob.push_status).in_(_ORPHANED))
            .where(col(Blob.stored_at).isnot(None))
        ).all()
        seen = [(row.hash, row.push_status, row.stored_at) for row in rows]
        for content_hash, status, stored_at in seen:
            if _aware(stored_at) <= cutoff:
                continue
            result = session.execute(
                update(Blob)
                .where(col(Blob.hash) == content_hash)
                .where(col(Blob.push_status) == status)
                .values(push_status=PushStatus.pending.value, push_attempts=0)
            )
            n += result.rowcount or 0
        session.commit()
    return n


def _aware(moment: datetime) -> datetime:
    """A stored time as UTC-aware (SQLite hands datetimes back naive)."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
