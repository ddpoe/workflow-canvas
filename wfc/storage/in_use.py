"""In-use records: who holds a code copy or a sample view, and its removal.

A holder writes its record before it makes, checks or reuses what the record
names, so a removal that runs later sees it. The holder is a claim, by a token
drawn before its run row exists (the claim transaction binds the record to the
run), or a pipeline, by its id. The record phase releases a run's records, the
end of a pipeline its pipeline's, and a refused claim its token's.

A copy or a view is removed only inside :func:`remove_if_unheld`'s one write
transaction, which deletes the caller's own record (if any), finds no other,
and removes the path while it holds the database's write lock: a holder whose
record commits after that transaction makes the copy or view again.

Storage is the only writer of the table. There is no clock: a record never
expires by age. Prune releases only records bound to a run that is no longer
``running`` (:func:`release_finished_runs`), so a hard-killed claim's record
goes at the next prune once its run is ended. A pipeline's records are bound
to no run: only the pipeline's end (its finish or its cancel) releases them,
so a pipeline that ends any other way (a hard kill, a crash) leaves its
records, and the sample views they name stay.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from axiom_annotations import task
from sqlalchemy import delete, exists, or_
from sqlmodel import col, select

from ..persistence import InUseKind, InUseRecord, Run, get_session, write_transaction


def new_claim_token() -> str:
    """Return a fresh claim token, the holder name of a claim's records.

    Returns:
        A token no other claim draws.
    """
    return uuid.uuid4().hex


@task(purpose="Write a claim's in-use record for a code copy, committed before "
              "the claim extracts, checks or reuses the copy",
      inputs="the copy's tree hash, the claim's token",
      outputs="one in_use_records row (kind code-copy), committed")
def hold_code_copy(tree_hash: str, claim_token: str) -> None:
    """Record that a claim holds the code copy of a tree, in its own transaction.

    Args:
        tree_hash: The copy's tree hash (its directory name under
            ``.wfc/runs/code/``).
        claim_token: The claim's token (:func:`new_claim_token`).
    """
    with write_transaction() as session:
        session.add(InUseRecord(kind=InUseKind.code_copy.value, key=tree_hash,
                                claim_token=claim_token))


def hold_for_pipeline(kind: InUseKind, key: str, pipeline_id: str) -> None:
    """Record that a pipeline holds a copy or a view, in its own transaction.

    A pipeline that already holds *key* writes no second record: each of
    its steps that reads a sample asks again.

    Args:
        kind: What is held.
        key: The copy's tree hash or the sample's name.
        pipeline_id: The holding pipeline.
    """
    kind = InUseKind(kind)
    with write_transaction() as session:
        held = session.exec(select(InUseRecord.id).where(
            col(InUseRecord.kind) == kind.value, col(InUseRecord.key) == key,
            col(InUseRecord.pipeline_id) == pipeline_id)).first()
        if held is None:
            session.add(InUseRecord(kind=kind.value, key=key, pipeline_id=pipeline_id))


def pipeline_keys(kind: InUseKind, pipeline_id: str) -> list[str]:
    """Return the keys of *kind* a pipeline holds, sorted.

    Args:
        kind: What is held.
        pipeline_id: The holding pipeline.

    Returns:
        The tree hashes or sample names the pipeline recorded.
    """
    with get_session() as session:
        return sorted(set(session.exec(select(InUseRecord.key).where(
            col(InUseRecord.kind) == InUseKind(kind).value,
            col(InUseRecord.pipeline_id) == pipeline_id)).all()))


def bind_claim(session, claim_token: str, run_id: int) -> None:
    """Bind a claim's records to its run, inside the claim's transaction.

    Args:
        session: The claim transaction's session.
        claim_token: The claim's token.
        run_id: The run row the claim wrote.
    """
    for record in session.exec(select(InUseRecord)
                               .where(InUseRecord.claim_token == claim_token)).all():
        record.run_id = run_id
        session.add(record)


def release_claim(claim_token: str, session=None) -> None:
    """Delete a claim's records: a refused claim, or a hit that runs nothing.

    Args:
        claim_token: The claim's token.
        session: A write transaction's session to delete in; ``None`` opens
            one.
    """
    if session is None:
        with write_transaction() as own:
            release_claim(claim_token, own)
        return
    session.execute(delete(InUseRecord).where(col(InUseRecord.claim_token) == claim_token))


def release_run(session, run_id: int) -> None:
    """Delete a run's records, inside its record phase's transaction.

    Args:
        session: The record transaction's session.
        run_id: The run whose records go.
    """
    session.execute(delete(InUseRecord).where(col(InUseRecord.run_id) == run_id))


def release_pipeline(pipeline_id: str) -> None:
    """Delete a pipeline's records, in their own transaction.

    Args:
        pipeline_id: The pipeline whose records go.
    """
    with write_transaction() as session:
        session.execute(delete(InUseRecord)
                     .where(col(InUseRecord.pipeline_id) == pipeline_id))


def release_finished_runs() -> int:
    """Delete the records bound to a run that is no longer ``running``.

    Such a record outlived its holder: the run ended without its record
    phase (a failed or cancelled pipeline's walk, a killed process). A
    record bound to no run (a claim in progress, a pipeline) is kept.

    Returns:
        The number of records deleted.
    """
    with write_transaction() as session:
        finished = select(Run.id).where(Run.status != "running")
        result = session.execute(delete(InUseRecord).where(
            col(InUseRecord.run_id).in_(finished)))
        return result.rowcount or 0


def _held_clause(kind: InUseKind):
    """The SQL condition: a record of *kind* whose holder is still alive."""
    running = exists().where(col(Run.id) == col(InUseRecord.run_id),
                             col(Run.status) == "running")
    return (col(InUseRecord.kind) == InUseKind(kind).value,
            or_(col(InUseRecord.run_id).is_(None), running))


def held_keys(kind: InUseKind) -> set[str]:
    """Return the keys of *kind* some live holder has recorded.

    A record bound to a run that is no longer ``running`` does not count:
    prune releases those (:func:`release_finished_runs`).

    Args:
        kind: What is held.

    Returns:
        The held tree hashes or sample names.
    """
    with get_session() as session:
        return set(session.exec(select(InUseRecord.key)
                                .where(*_held_clause(kind))).all())


@task(purpose="Remove a code copy or a sample view only when no holder has it "
              "in use, in one write transaction that first deletes the "
              "caller's own record",
      inputs="what is held (kind, key), the removal, the caller's own claim "
             "token or pipeline id",
      outputs="whether the copy or view was removed",
      critical="The removal runs while the transaction holds the write lock: "
               "a holder that records after it commits finds the path gone "
               "and makes it again; one that recorded before keeps it")
def remove_if_unheld(kind: InUseKind, key: str, remove: Callable[[], None], *,
                     claim_token: str | None = None,
                     pipeline_id: str | None = None) -> bool:
    """Remove a copy or a view unless another holder has it in use.

    Args:
        kind: What is held.
        key: The copy's tree hash or the sample's name.
        remove: Removes the copy or view from disk; runs inside the
            transaction, so a raise keeps the caller's record.
        claim_token: The caller's own claim token, whose record for *key*
            is deleted first.
        pipeline_id: The caller's own pipeline id, likewise.

    Returns:
        True when *remove* ran; False when another holder has it.
    """
    kind = InUseKind(kind)
    with write_transaction() as session:
        own = []
        if claim_token is not None:
            own.append(col(InUseRecord.claim_token) == claim_token)
        if pipeline_id is not None:
            own.append(col(InUseRecord.pipeline_id) == pipeline_id)
        if own:
            session.execute(delete(InUseRecord).where(
                col(InUseRecord.kind) == kind.value,
                col(InUseRecord.key) == key, or_(*own)))
        other = session.exec(select(InUseRecord.id).where(
            *_held_clause(kind), col(InUseRecord.key) == key)).first()
        if other is not None:
            return False
        remove()
        return True


__all__ = [
    "bind_claim",
    "held_keys",
    "hold_code_copy",
    "hold_for_pipeline",
    "new_claim_token",
    "pipeline_keys",
    "release_claim",
    "release_finished_runs",
    "release_pipeline",
    "release_run",
    "remove_if_unheld",
]
