"""The resolvers: a run output's record to the path its reader reads.

Every reader selects a run's output by the contract slot it fills, and every
reader goes through one record-validity check: each output record must carry
its slot and, until it is archived, record a path inside its run's archive
(``layout.run_archive_dir``); a run with an output record that does not has
a malformed record (:class:`MalformedRecordError`), named with the run to
re-run.

- :func:`output_location` is the one answer to "where are this output's
  bytes": ``local`` (a complete cache entry, or a pre-archive row's
  run-archive file), ``remote`` (recorded pushed) or ``missing``.  The hit
  rule and both resolvers read it; it never builds a checkout or pulls.
- :func:`resolve_input` serves a run's consumer (Execution's materialize
  phase and ``wfc resolve_input``).  A pre-archive row resolves to the run
  archive; an archived row resolves through :func:`_local_entry_path`, the
  one reader both resolvers share, which pulls only a remote entry.
- :func:`resolve_output` serves a user (``wfc export`` and the Canvas
  provider), and only for archived rows.  Every failure is a kind-carrying
  :class:`ResolveOutputError`.
- :func:`provider_outputs` is the Canvas provider's slice: every output of
  a run, named as ``wfc export --all`` names it and resolved from the local
  cache only.
- :func:`exportable_outputs` is ``wfc export --all``'s slice: the same
  enumeration, resolved strictly — a run with no rows is a typed refusal
  rather than a skip, because a partial export is worse than none.
  :func:`output_listing` is the same enumeration unresolved: ``wfc export``
  with no slot lists it. :func:`output_records` returns the records
  themselves, for a run bundle's plan.
- :func:`output_export_name` names one output for a user: the row's output
  name carrying the artifact's suffix.  :func:`output_export_names` names
  all of a run's outputs at once, placing names two outputs share under
  their slot folders.
- :func:`has_malformed_output_records` is the record-validity rule as a
  predicate, for Execution's cache lookup.
- :func:`resolve_run_reference_outputs` serves Execution's composer: the
  referenced runs' recorded output paths by slot, sample and method, in the
  composer's session, for the Graph load's reference binding.
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from axiom_annotations import Step, task
from sqlmodel import select

from ..layout import run_archive_dir
from ..persistence import Method, OutputIntegrity, PushStatus, Run, RunOutput, get_session
from ..persistence import project_root as get_project_root
from .cache import (
    ChangedEntryError,
    MalformedEntryError,
    ResolveOutputError,
    _cache_path,
    changed_objects,
    changed_phrase,
    check_entry_shape,
    entry_changes,
    entry_is_complete,
    entry_is_local,
    local_path,
    manifest_members,
    output_repair,
    protect_entry,
    set_aside,
)
from .integrity import CHANGED, check_output, inputs_setting, mark_changed

logger = logging.getLogger(__name__)


class UnknownRunError(ResolveOutputError):
    """The run id does not exist in the database."""


class UnknownOutputError(ResolveOutputError):
    """No/wrong output slot; the message lists the run's slots with file names.

    Attributes:
        available: The run's recorded output slots (discovery listing).
    """

    def __init__(self, message: str, available: list[str] | None = None):
        super().__init__(message)
        self.available = available or []


class MalformedRecordError(ResolveOutputError):
    """A run has an output record that does not carry its slot.

    The message names the run and its method and says to re-run it; the
    records at fault go to the log.
    """


class NotArchivedError(ResolveOutputError):
    """The output has no content_hash yet (the archive pass has not reached it)."""


class NotInCacheError(ResolveOutputError):
    """The content hash is not in the local cache (and pull did not help)."""


class InputUnavailableError(ResolveOutputError):
    """An output a consumer asked for can be read from nowhere.

    Either the row has no content hash yet and its run-archive file (the
    only place its bytes were) is gone, or the row is archived and its entry
    is neither complete in the local cache nor recorded pushed, or the pull
    of a pushed entry did not make it complete. The message names the run,
    the slot, the path or content hash, why, and the repair (re-running the
    run), so a consumer can put it on its own run row.
    """


class CheckoutFailedError(ResolveOutputError):
    """A directory entry is complete in the cache but its checkout was not built.

    Never a miss: the bytes are local, so nothing is pulled and the step is
    not reported missing.
    """


def _source_output_rows(session, run: Run) -> list[RunOutput]:
    """A run's output records, following a cache-hit audit row one hop.

    Cache-hit audit rows (``cache_source_run_id`` set) own no RunOutput rows
    — their outputs live on the source run they reference.  One hop always
    suffices: ``pre_run`` never selects an audit row as a cache source.

    Args:
        session: Open DB session.
        run: The run whose outputs are read.

    Returns:
        The run's rows, or its source run's rows for an audit row.
    """
    rows = session.exec(
        select(RunOutput).where(RunOutput.run_id == run.id)
    ).all()
    if not rows and run.cache_source_run_id is not None:
        rows = session.exec(
            select(RunOutput).where(
                RunOutput.run_id == run.cache_source_run_id
            )
        ).all()
    return list(rows)


def outside_run_archive(row: RunOutput, project_dir: Path) -> bool:
    """Whether a pre-archive record's path lies outside its run's archive.

    A record the archive pass has not reached (no content hash) is read from
    the path it records, and collect records every output inside its run's
    archive directory (``layout.run_archive_dir``).  A recorded path that
    resolves anywhere else (an old ``.runs/`` tree, another project, a hand
    edit) names bytes wfc does not own.  Both sides are resolved first, so
    ``..`` segments and, on Windows, letter case cannot carry a path out or
    keep one in.  An archived record is never outside: its bytes come from
    the cache.  A record that names no path is not outside either; it is
    missing.

    Args:
        row: An output record.
        project_dir: The project root; relative recorded paths are anchored
            at it, as :func:`run_archive_path` anchors them.

    Returns:
        True when the record is pre-archive and its path is not inside
        ``run_archive_dir(project_dir, row.run_id)``.
    """
    if row.content_hash:
        return False
    path = run_archive_path(row, project_dir)
    if path is None:
        return False
    archive =run_archive_dir(Path(project_dir), row.run_id).resolve()
    return archive not in path.resolve().parents


def _records_at_fault(rows: Sequence[RunOutput], project_dir: Path) -> list:
    """The records that break the record-validity rule.

    A record breaks it when it carries no slot, or when it has not been
    archived and its recorded path lies outside its run's archive
    (:func:`outside_run_archive`).  The rule's one test, shared by the
    check that raises, the predicate the cache lookup asks, and the archive
    pass.

    Args:
        rows: A run's output records.
        project_dir: The project root.

    Returns:
        The record ids at fault, in row order.
    """
    return [
        r.id for r in rows
        if r.slot is None or outside_run_archive(r, project_dir)
    ]


def malformed_record_message(session, run: Run) -> str:
    """The record-validity rule's user sentence: the run, its method, the re-run.

    Args:
        session: Open DB session (for the run's method name).
        run: The run whose record is malformed.

    Returns:
        The sentence :class:`MalformedRecordError` carries.
    """
    method = session.get(Method, run.method_id) if run.method_id else None
    method_name = method.name if method is not None else "unknown method"
    return f"Run {run.id} ({method_name}) has a malformed record; re-run it."


def _check_records(
    session, run: Run, rows: Sequence[RunOutput], project_dir: Path | None = None
) -> None:
    """Apply the record-validity rule to a run's output records.

    Every output record carries its slot, and a record the archive pass has
    not reached records a path inside its run's archive.  The one place the
    rule and its error live.  A run with no records passes (its readers
    report "no recorded outputs").  A run with a record at fault logs the
    records at fault and raises the user sentence.

    Args:
        session: Open DB session (for the run's method name).
        run: The run the reader asked about.
        rows: The records read for it (after any audit hop).
        project_dir: The project root; defaults to the configured one.

    Raises:
        MalformedRecordError: A record lacks a slot, or a pre-archive
            record's path lies outside its run's archive.
    """
    if not rows:
        return
    root = Path(project_dir) if project_dir is not None else get_project_root()
    at_fault = _records_at_fault(rows, root)
    if not at_fault:
        return
    message = malformed_record_message(session, run)
    logger.error(
        "%s Output records %s of run %s are malformed: every output record "
        "must name the slot it fills, and until it is archived its path must "
        "lie inside its run's archive %s",
        message, at_fault, rows[0].run_id, run_archive_dir(root, rows[0].run_id),
    )
    raise MalformedRecordError(message)


def _checked_output_rows(
    session, run: Run, project_dir: Path | None = None
) -> list[RunOutput]:
    """A run's output records after the audit hop, under the record-validity rule.

    Args:
        session: Open DB session.
        run: The run whose outputs are read.
        project_dir: The project root; defaults to the configured one.

    Returns:
        The records every reader selects from.

    Raises:
        MalformedRecordError: A record lacks a slot, or a pre-archive
            record's path lies outside its run's archive.
    """
    rows = _source_output_rows(session, run)
    _check_records(session, run, rows, project_dir)
    return rows


def recorded_output_slots(run_id: int, *, session=None) -> list[str]:
    """The output slots a run recorded, in row order, after the audit hop.

    Storage owns "which outputs does this run have" -- the audit hop and the
    record-validity rule are the same ones :func:`resolve_input` applies when
    it selects a record.  Execution's claim phase reads this to resolve a
    parent entry that names no source slot: one slot is the run's only output
    and qualifies the cache-key part; several or none is its refusal to make,
    not this reader's.

    Args:
        run_id: The run whose outputs are read.
        session: Open DB session override; defaults to a fresh session.

    Returns:
        The recorded slots, empty when the run does not exist or recorded
        nothing.

    Raises:
        MalformedRecordError: The run has an output record with no slot.
    """
    if session is None:
        with get_session() as _session:
            return recorded_output_slots(run_id, session=_session)
    run = session.get(Run, run_id)
    if run is None:
        return []
    # _checked_output_rows refuses a record without a slot, so none is dropped.
    return [r.slot for r in _checked_output_rows(session, run) if r.slot is not None]


def _slot_listing(rows: Sequence[RunOutput]) -> str:
    """``slot (file name)`` pairs for an error message."""
    return ", ".join(f"{r.slot} ({r.output_name})" for r in rows) or "(none)"


def has_malformed_output_records(run_id: int, *, session=None) -> bool:
    """Whether a run's output records fail the record-validity rule.

    The rule the readers enforce, as a predicate: the run (after a cache-hit
    audit hop) has output records and at least one lacks a slot or, not yet
    archived, records a path outside its run's archive.  A run
    that does not exist, or has no records, is not malformed.

    Args:
        run_id: The run's integer id.
        session: Open DB session override; defaults to a fresh session.

    Returns:
        True when the run has a malformed record.
    """
    if session is None:
        with get_session() as _session:
            return has_malformed_output_records(run_id, session=_session)
    run = session.get(Run, run_id)
    if run is None:
        return False
    rows = _source_output_rows(session, run)
    if not rows:
        return False
    return bool(_records_at_fault(rows, get_project_root()))


#: Where one run output's bytes can be read from (:func:`output_location`).
OUTPUT_LOCAL = "local"
OUTPUT_REMOTE = "remote"
OUTPUT_MISSING = "missing"


def run_archive_path(row: RunOutput, project_dir: Path) -> Path | None:
    """The run-archive path of a record the archive pass has not reached.

    Relative recorded paths are resolved against the project root, so the
    location rule and the resolver probe and return the same path.

    Args:
        row: A pre-archive output record (no content hash).
        project_dir: The project root.

    Returns:
        The path, or None when the record names no path.
    """
    if not row.artifact_path:
        return None
    artifact = Path(row.artifact_path)
    return artifact if artifact.is_absolute() else project_dir / artifact


def output_location(row: RunOutput, project_dir: Path, setting: str | None = None) -> str:
    """Place one output record: ``local``, ``remote`` or ``missing``.

    The one answer to "where are this output's bytes", read by the hit rule
    (Execution's ``classify_cache_key``), the Runs Preview, and both
    resolvers (through :func:`_local_entry_path` and :func:`resolve_input`),
    so a hit is served exactly when its consumer can read it.

    An archived record (it has a content hash) is local exactly when its
    cache entry is complete (``cache.entry_is_complete``: a file's object,
    or a directory's manifest and every member) and Storage's one integrity
    check finds it unchanged (a stamp's stat fields, or a re-hash), and
    remote when it is not local, or changed, but recorded pushed. A changed
    entry never pushed is missing. A record the archive pass has not reached
    (no content hash) is local when its run-archive output is what collect
    recorded (Storage's check against collect's stat record and, under
    ``hash``, its collect hash); a changed output marks the row and is
    missing, and a marked row is missing without a check. It is never
    remote, because a push needs a hash, and it is missing when its
    path lies outside its run's archive (a malformed record, which the
    readers refuse), so the hit rule never serves it. ``local`` therefore
    combines the DVC cache and the run archive on purpose.

    A probe only: it builds no checkout, pulls nothing and does no network
    I/O; the integrity check reads bytes only when the stat fields do not
    vouch (or always under ``[integrity] inputs = "hash"``). A malformed
    cache entry (a directory tree at an unsuffixed address) is ``missing``
    here, so the hit rule reports the step's outputs missing and the step
    re-runs; the reader refuses it loudly.

    Args:
        row: The output record.
        project_dir: The project root whose DVC cache and run archive are
            consulted.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        ``OUTPUT_LOCAL``, ``OUTPUT_REMOTE`` or ``OUTPUT_MISSING``.
    """
    if not row.content_hash:
        if outside_run_archive(row, project_dir):
            # A malformed record: its bytes are not wfc's to serve.
            return OUTPUT_MISSING
        return _pre_archive_location(row, project_dir, setting)
    location, _changed = _archived_location(row, project_dir, setting)
    return location


def _is_marked_changed(row: RunOutput) -> bool:
    """Whether a row carries the one-way ``changed`` mark."""
    return row.integrity_status == OutputIntegrity.changed.value


def _pre_archive_location(row: RunOutput, project_dir: Path,
                          setting: str | None = None) -> str:
    """Place a record the archive pass has not reached: its run-archive output.

    A row marked changed is missing without a check. Otherwise the output
    goes through Storage's check against what collect recorded
    (``integrity.check_output``): intact is local; changed marks the row
    (compare-and-set, one-way) and is missing, so the hit rule's
    ``outputs_missing`` re-runs the step. The row's stat fields are never
    rewritten here.

    Args:
        row: A pre-archive output record inside its run's archive.
        project_dir: The project root.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        ``OUTPUT_LOCAL`` or ``OUTPUT_MISSING``.
    """
    if _is_marked_changed(row):
        return OUTPUT_MISSING
    path = run_archive_path(row, project_dir)
    if path is None:
        return OUTPUT_MISSING
    verdict = check_output(row, path, project_dir, setting)
    if verdict.status == CHANGED:
        mark_changed(row)
        return OUTPUT_MISSING
    return OUTPUT_LOCAL if verdict.intact else OUTPUT_MISSING


def _archived_location(row: RunOutput, project_dir: Path,
                       setting: str | None = None) -> tuple[str, list[str]]:
    """Place an archived record, and name the local objects that changed.

    A complete local entry is local when the one integrity check finds what
    its read would serve intact (``cache.entry_changes``: a file's object,
    or a directory's checkout and, when that is not intact, its objects).
    A changed entry is remote when recorded pushed (its reader pulls it
    again) and missing otherwise. An entry that is not local is remote when
    recorded pushed. A malformed entry is missing.

    Args:
        row: An archived output record.
        project_dir: The project root.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        ``(location, changed objects)``.
    """
    content_hash = row.content_hash
    assert content_hash is not None, "callers pass an archived record"
    return _entry_location(project_dir, content_hash,
                           row.push_status == PushStatus.pushed.value, setting)


def _entry_location(project_dir: Path, content_hash: str, pushed: bool,
                    setting: str | None = None) -> tuple[str, list[str]]:
    """Place one cache entry by its hash, and name the local objects that changed.

    The rule :func:`_archived_location` applies to an output's entry, for any
    entry whose push state the caller read: local when complete and intact,
    remote when not (or changed) but recorded pushed, missing otherwise; a
    malformed entry is missing.

    Args:
        project_dir: The project root.
        content_hash: The entry's hash.
        pushed: Whether the entry is recorded pushed.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        ``(location, changed objects)``.
    """
    if entry_is_local(project_dir, content_hash):
        changed = entry_changes(project_dir, content_hash, setting)
        if not changed:
            return OUTPUT_LOCAL, []
        return (OUTPUT_REMOTE if pushed else OUTPUT_MISSING), changed
    try:
        # A malformed entry is never remote either: the reader refuses it
        # before any pull, so promising a pull would be a hit it cannot serve.
        check_entry_shape(project_dir, content_hash)
    except MalformedEntryError:
        return OUTPUT_MISSING, []
    return (OUTPUT_REMOTE if pushed else OUTPUT_MISSING), []


@task(
    purpose="Resolve a run's output for a consumer run: a pre-archive row to its "
            "run archive, an archived row to its complete cache entry, pulling "
            "only an entry recorded pushed",
    inputs="run_id; optional slot selecting one of the run's outputs",
    outputs="the path to read, or None when the run or output cannot be resolved",
)
def resolve_input(run_id: int, slot: str | None = None) -> str | None:
    """Resolve a run's output for a consumer run: the path the next step reads.

    The input resolver serves a run's consumer. It decides from
    :func:`output_location`, the rule the hit rule reads, so a cache hit is
    served exactly when this resolver can read it:

    - ``local`` -- an archived row resolves to its complete cache entry (a
      file's cache address, a directory's read-only checkout); a
      pre-archive row (no ``content_hash`` yet; the archive pass has not
      reached it) resolves to its run-archive path, resolved against the
      project root.
    - ``remote`` -- the row is pulled from the configured DVC remote, then
      resolves as local.
    - ``missing`` -- a loud FAIL naming the run, the slot and why, raised as
      :class:`InputUnavailableError` carrying the path (pre-archive) or the
      content hash (archived) and the re-run, so the consumer can record it
      on its run row. A pull that does not make a pushed entry complete is
      the same refusal. A path that does not exist is never returned.

    The archived branch is the one reader :func:`resolve_output` also uses
    (:func:`_local_entry_path`).  A malformed cache entry (a directory tree
    at an unsuffixed address) raises :class:`MalformedEntryError` naming a
    re-run; an entry complete in the cache whose checkout cannot be built
    is a FAIL, never a miss.

    :func:`resolve_output` serves a user, and only for archived rows.  The
    two agree on every archived row.

    Outcomes, reported on stderr:
      CACHE       -- archived and complete in the local DVC cache; return
                     its path (a directory's checkout).
      ARCHIVE     -- pre-archive and its run-archive file exists; return it.
      REMOTE-PULL -- pushed, not local; pulled, then return its path.
      FAIL        -- missing or the pull did not materialize it (raises
                     InputUnavailableError), or the checkout failed (None).

    A file's path is the cache location itself and a directory's is its
    read-only checkout; downstream consumers read them in place.

    A cache-hit audit row owns no output records; resolution follows it one
    hop to its source run, whose records carry the same slots (the source
    run is the same method by construction).  A run with a malformed record
    is reported on stderr naming the run to re-run.

    Output selection: with ``slot``, the record for that slot is selected; no
    match is a loud FAIL listing the run's slots with their file names.
    Without ``slot``, a single-output run resolves to its one output, but a
    multi-output run is a loud FAIL listing them — picking one silently
    would route the wrong artifact downstream.

    Args:
        run_id: The run whose output to resolve.
        slot: The output slot to select.  None means "the run's only
            output".

    Returns:
        The run-archive path for a pre-archive row; the cache path (or
        checkout) for an archived row available locally or pulled; None if
        the run or output is not found, the run has a malformed record, or
        a complete directory entry's checkout could not be built.

    Raises:
        MalformedEntryError: The output's cache address holds a directory
            tree (a malformed cache entry).
        InputUnavailableError: The output is in neither place: a pre-archive
            row whose run-archive file is gone, an archived row neither
            complete locally nor recorded pushed, or a pull that did not
            make it complete.
    """
    口 = Step(step_num=1, name="Select the output record",
             purpose="Load the run's output records, following a cache-hit audit "
                     "row one hop to its source run; report a malformed record "
                     "naming the run to re-run; pick the requested slot's record")
    project_dir = get_project_root()

    with get_session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return None

        try:
            rows = _checked_output_rows(session, run, project_dir)
        except MalformedRecordError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return None
        if rows and rows[0].run_id != run_id:
            print(
                f"resolve_input: AUDIT (run {run_id} -> source run "
                f"{run.cache_source_run_id})",
                file=sys.stderr,
            )
        if not rows:
            print(
                f"resolve_input: FAIL (run {run_id} has no recorded outputs)",
                file=sys.stderr,
            )
            return None

        if slot is not None:
            ro = next((r for r in rows if r.slot == slot), None)
            if ro is None:
                print(
                    f"resolve_input: FAIL (run {run_id} has no output "
                    f"'{slot}'; its outputs: {_slot_listing(rows)})",
                    file=sys.stderr,
                )
                return None
        elif len(rows) > 1:
            print(
                f"resolve_input: FAIL (run {run_id} has several outputs and "
                f"none was named; picking one silently would route the wrong "
                f"artifact. Its outputs: {_slot_listing(rows)})",
                file=sys.stderr,
            )
            return None
        else:
            ro = rows[0]

        口 = Step(step_num=2, name="Pre-archive row: the run archive",
                 purpose="A row the archive pass has not reached resolves to its "
                         "run-archive path when the one output-location rule "
                         "places it local (collect's record checked); a file "
                         "that is gone, changed or marked changed is a loud "
                         "FAIL raised as InputUnavailableError, naming the "
                         "path, the run and the re-run",
                 critical="The hit rule serves a hit only when this resolver "
                          "can read it; deciding from the same rule is what "
                          "keeps the two from disagreeing")
        if not ro.content_hash:
            path = run_archive_path(ro, project_dir)
            if output_location(ro, project_dir) == OUTPUT_LOCAL:
                print(f"resolve_input: ARCHIVE (run {run_id})", file=sys.stderr)
                return str(path)
            if _is_marked_changed(ro):
                reason = (
                    f"run {run_id}, output '{ro.slot}': its run-archive file "
                    f"{path} changed after run {ro.run_id} wrote it, so it is "
                    f"not that run's output. {output_repair(run_id)}"
                )
            else:
                reason = (
                    f"run {run_id}, output '{ro.slot}': it was never archived and "
                    f"its run-archive file "
                    f"{path if path is not None else '(none recorded)'} is gone. "
                    f"{output_repair(run_id)}"
                )
            print(f"resolve_input: FAIL ({reason})", file=sys.stderr)
            raise InputUnavailableError(reason)

        口 = Step(step_num=3, name="Archived row: the readable entry",
                 purpose="The complete local entry (a directory's checkout), pulled "
                         "first only when the rule places it remote; a malformed "
                         "entry is refused naming a re-run; a missing entry, or "
                         "a pull that did not make it complete, is a loud FAIL "
                         "raised as InputUnavailableError naming the run, slot, "
                         "content hash and the re-run; a failed checkout is a "
                         "loud FAIL naming the run and slot")
        try:
            path, pulled = _local_entry_path(
                project_dir, ro, run_id, True, "resolve_input"
            )
        except NotInCacheError as exc:
            print(f"resolve_input: FAIL ({exc})", file=sys.stderr)
            raise InputUnavailableError(str(exc)) from exc
        except CheckoutFailedError as exc:
            print(f"resolve_input: FAIL ({exc})", file=sys.stderr)
            return None
        label = "REMOTE-PULL" if pulled else "CACHE"
        print(f"resolve_input: {label} (run {run_id})", file=sys.stderr)
        return str(path)


def reference_read_path(ref_path: str) -> str:
    """Return the path a reference input reads: a run's output through :func:`resolve_input`.

    A ``run_reference`` hands its consumer the recorded ``artifact_path``
    of the referenced run's output, the place the run wrote it. Once that
    output is archived it has left the run archive and the cache holds its
    one local copy, so a path naming an output record is read through
    :func:`resolve_input`: a pre-archive output at its run-archive path
    (checked against collect's record), an archived one from its cache
    entry (pulled when it is recorded pushed). A path naming no output
    record is returned as given.

    Args:
        ref_path: The reference input's path, as the orchestrator passed it.

    Returns:
        The path to read.

    Raises:
        InputUnavailableError: The output is in neither place, or its
            entry could not be read.
        MalformedEntryError: Its cache address holds a directory tree.
    """
    from .. import layout

    project_dir = get_project_root()
    path = Path(ref_path).resolve()
    try:
        rel = path.relative_to(layout.artifact_store(project_dir).resolve())
    except ValueError:
        return ref_path
    if not rel.parts or not rel.parts[0].isdigit():
        return ref_path
    run_id = int(rel.parts[0])
    with get_session() as session:
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        slot = next((r.slot for r in rows if r.artifact_path
                     and Path(r.artifact_path).resolve() == path), None)
    if slot is None:
        return ref_path
    resolved = resolve_input(run_id, slot)
    if resolved is None:
        raise InputUnavailableError(
            f"run {run_id}, output '{slot}' could not be read from the cache. "
            f"{output_repair(run_id)}")
    return resolved


def resolve_output(
    run_id: int,
    slot: str | None = None,
    *,
    pull: bool = True,
    project_dir: Path | None = None,
    session=None,
) -> tuple[Path, RunOutput]:
    """Resolve a run's output, by slot, to its cache path, for a user.

    The output resolver serves a user: the ``wfc export`` verb (copy and
    ``--path`` modes) and the Canvas provider's artifact methods.  It
    answers only for archived rows; a row the archive pass has not reached
    raises :class:`NotArchivedError`, where :func:`resolve_input`, which
    serves a run's consumer, answers from the run archive.  The result is
    the correct archived bytes or a kind-carrying error — never a stale,
    adjacent, or guessed path.

    A cache-hit audit row owns no output records; resolution follows it one
    hop to its source run, and slot matching, the error contract and the
    discovery listing all operate on the source records.

    Args:
        run_id: The run's integer database id.
        slot: The output slot to resolve. ``None`` raises
            ``UnknownOutputError`` listing the run's slots (that error
            doubles as the discovery listing).
        pull: When True (default), an entry that is not local but is
            recorded pushed is pulled from the configured DVC remote
            before failing; an entry never pushed is not pulled. The Canvas
            provider passes ``pull=False`` (GUI never blocks on a pull).
        project_dir: Project root override; defaults to the ambient
            project root. The Canvas provider passes its own root.
        session: Open DB session override; defaults to a fresh session on
            the global engine. The Canvas provider passes the session it
            is already reading through.

    Returns:
        Tuple of (cache path, the matching ``RunOutput`` row).

    Raises:
        UnknownRunError: ``run_id`` does not exist.
        MalformedRecordError: The run has an output record without a slot.
        UnknownOutputError: ``slot`` is missing or not one of the run's
            outputs; the message lists the run's slots with file names.
        NotArchivedError: The output's ``content_hash`` is NULL — point
            the user at ``wfc cache archive``.
        NotInCacheError: The hash is not in the local cache and the
            remote pull (if attempted) did not materialize it.
    """
    if project_dir is None:
        project_dir = get_project_root()
    if session is None:
        with get_session() as _session:
            return _resolve_output_in_session(
                _session, project_dir, run_id, slot, pull
            )
    return _resolve_output_in_session(
        session, project_dir, run_id, slot, pull
    )


def _resolve_output_in_session(
    session, project_dir: Path, run_id: int, slot: str | None, pull: bool
) -> tuple[Path, RunOutput]:
    """Body of :func:`resolve_output`, operating in a caller-supplied session."""
    run = session.get(Run, run_id)
    if run is None:
        raise UnknownRunError(f"Run {run_id} not found.")

    rows = _checked_output_rows(session, run, project_dir)
    if not rows:
        raise UnknownOutputError(f"Run {run_id} has no recorded outputs.")
    # _checked_output_rows refuses a record without a slot, so none is dropped.
    available = [ro.slot for ro in rows if ro.slot is not None]

    if slot is None:
        raise UnknownOutputError(
            f"Run {run_id} requires an output name. Available outputs: "
            f"{_slot_listing(rows)}",
            available=available,
        )

    ro = next((r for r in rows if r.slot == slot), None)
    if ro is None:
        raise UnknownOutputError(
            f"Run {run_id} has no output '{slot}'. "
            f"Available outputs: {_slot_listing(rows)}",
            available=available,
        )
    return _resolve_row(project_dir, run_id, ro, pull), ro


def _local_entry_path(
    project_dir: Path, ro: RunOutput, run_id: int, pull: bool, who: str
) -> tuple[Path, bool]:
    """The path a reader reads for an archived output's entry: the one reader.

    Both resolvers go through this.  The entry's place is
    :func:`output_location`'s answer, the one the hit rule reads:

    - ``local`` -- the complete entry, checked unchanged by Storage's one
      integrity check, and its path (``cache.local_path``: a file's cache
      address, or a directory's read-only checkout, rebuilt when it
      changed).
    - ``remote`` -- recorded pushed and not local, or changed: the changed
      objects are set aside, the entry pulled (when ``pull``), the pulled
      objects re-protected and hashed in full, then read as local.
    - ``missing`` -- neither local nor recorded pushed, or changed and never
      pushed: refused without a pull, naming what changed, since a hash
      that was never pushed cannot be pulled.

    A member object that changed between the check and the checkout's
    hashing copy is pulled again the same way when recorded pushed, and
    refused otherwise.

    A malformed cache entry (a directory tree at an unsuffixed address) is
    refused before anything else, naming a re-run of its run; the location
    rule calls it ``missing`` so the hit rule re-runs the step, but a reader
    never reads it.

    Args:
        project_dir: Project root whose cache is read.
        ro: The selected, archived output record.
        run_id: The run the reader asked about (for messages and the repair).
        pull: Whether a remote entry is pulled.
        who: The resolver's name, for the stderr trace.

    Returns:
        ``(path, whether a pull produced it)``.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        NotInCacheError: The entry is not local, or changed, and not
            recorded pushed; or the pull was not allowed, did not bring it
            whole, or brought bytes that do not match their addresses.
        CheckoutFailedError: The entry is complete in the cache but its
            directory checkout could not be built.
    """
    content_hash = ro.content_hash
    assert content_hash is not None, "callers pass an archived record"
    return _read_local_entry(
        Path(project_dir), content_hash,
        pushed=ro.push_status == PushStatus.pushed.value, pull=pull,
        where=f"run {run_id}, output '{ro.slot}' (content_hash={content_hash})",
        repair=output_repair(run_id), who=who,
    )


def _missing_entry_error(where: str, changed: list[str], repair: str) -> NotInCacheError:
    """The refusal for an entry that is not local, or changed, and was never pushed.

    Args:
        where: The reader's description of the entry.
        changed: The local objects found changed, if any.
        repair: The sentence naming the repair.

    Returns:
        The error to raise.
    """
    if changed:
        return NotInCacheError(
            f"{where} changed in the local cache ({changed_phrase(changed)}) "
            f"and was never pushed, so there is nothing to pull it again "
            f"from. {repair}"
        )
    return NotInCacheError(
        f"{where} is not complete in the local cache and was never "
        f"pushed, so there is nothing to pull. {repair}"
    )


def _read_local_entry(project_dir: Path, content_hash: str, *, pushed: bool,
                      pull: bool, where: str, repair: str,
                      who: str) -> tuple[Path, bool]:
    """Read one cache entry: the core every checked entry read shares.

    The shape check with the entry's repair, the one integrity check, a pull
    again when the entry is not local or changed and recorded pushed, or a
    refusal naming the entry and the repair; then the entry's path
    (``cache.local_path``). :func:`_local_entry_path` reads an output's entry
    through it, and :func:`read_entry` a sample's, a pipeline document's or
    an env content blob's.

    Args:
        project_dir: Project root whose cache is read.
        content_hash: The entry's hash.
        pushed: Whether the entry is recorded pushed.
        pull: Whether a remote entry is pulled.
        where: The reader's description of the entry, for messages.
        repair: The sentence naming the repair, ending a refusal.
        who: The reader's name, for the stderr trace.

    Returns:
        ``(path, whether a pull produced it)``.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        NotInCacheError: The entry cannot be served (see :func:`_local_entry_path`).
        CheckoutFailedError: The entry is complete but its checkout failed.
    """
    check_entry_shape(project_dir, content_hash, repair)
    setting = inputs_setting(project_dir)
    location, changed = _entry_location(project_dir, content_hash, pushed, setting)

    if location == OUTPUT_MISSING:
        raise _missing_entry_error(where, changed, repair)

    pulled = False
    if location == OUTPUT_REMOTE:
        if not pull:
            state = (f"changed in the local cache ({changed_phrase(changed)})"
                     if changed else "not in the local cache")
            raise NotInCacheError(
                f"{where} is {state}; it is recorded pushed, but this reader "
                f"does not pull"
            )
        pull_again(project_dir, content_hash, changed, where,
                   repair, who, setting)
        pulled = True

    try:
        path = local_path(project_dir, content_hash, setting=setting)
    except ChangedEntryError as exc:
        # A member changed between the check and the checkout's copy.
        if not pushed or not pull:
            raise NotInCacheError(f"{where}: {exc}. {repair}") from exc
        pull_again(project_dir, content_hash, exc.objects, where,
                   repair, who, setting)
        pulled = True
        try:
            path = local_path(project_dir, content_hash, setting=setting)
        except ChangedEntryError as again:
            raise NotInCacheError(
                f"{where}: {again}, even after it was pulled again. "
                f"{repair}"
            ) from again
    if path is None:
        raise CheckoutFailedError(
            f"{where} is complete in the local cache, but its checkout "
            f"could not be built"
        )
    return path, pulled


#: The kind of a registered sample's entry, for :func:`read_entry`.
KIND_SAMPLE = "sample"

#: What each kind of entry is called in a message.
_ENTRY_NAMES = {KIND_SAMPLE: "sample", "pipeline-document": "pipeline document",
                "env-fingerprint": "env content blob"}


def _entry_facts(content_hash: str, kind: str, name: str | None) -> tuple[bool, str, str]:
    """Read an entry's push state from its row, and name it and its repair.

    Args:
        content_hash: The entry's hash.
        kind: :data:`KIND_SAMPLE`, ``pipeline-document`` or ``env-fingerprint``.
        name: The sample's name, for a sample.

    Returns:
        ``(recorded pushed, where, repair)``.

    Raises:
        ValueError: *kind* is not one of the three.
    """
    from ..persistence import Blob, Sample
    from .cache import blob_repair, sample_repair

    if kind not in _ENTRY_NAMES:
        raise ValueError(f"not an entry kind: {kind!r}")
    pushed_value = PushStatus.pushed.value
    with get_session() as session:
        if kind == KIND_SAMPLE:
            query = select(Sample).where(Sample.content_hash == content_hash)
            if name is not None:
                query = query.where(Sample.name == name)
            pushed = any(row.push_status == pushed_value
                         for row in session.exec(query).all())
        else:
            blob = session.get(Blob, content_hash)
            pushed = blob is not None and blob.push_status == pushed_value
    label = _ENTRY_NAMES[kind]
    if kind == KIND_SAMPLE and name:
        label = f"sample '{name}'"
    where = f"{label} (content_hash={content_hash})"
    repair = sample_repair(name) if kind == KIND_SAMPLE else blob_repair(kind)
    return pushed, where, repair


@task(purpose="Read a cache entry no output row owns (a sample, a pipeline document, "
              "an env content blob) by its hash: refuse a malformed entry with the "
              "kind's repair, run the one integrity check, pull it again when it is "
              "not local or changed and recorded pushed, else refuse naming it",
      inputs="the entry's hash and kind (and a sample's name); its Sample or Blob "
             "row's push status; the local cache; the DVC remote",
      outputs="the checked local path (a file's cache address, or a directory's "
              "read-only checkout); no workspace view and no row written")
def read_entry(content_hash: str, kind: str, *, name: str | None = None,
               project_dir: Path | str | None = None, pull: bool = True) -> Path:
    """Return a checked local path for a sample, pipeline document or env blob entry.

    The same read the output reader makes (:func:`_local_entry_path`), for an
    entry whose push state is on its ``Sample`` row (a sample) or its
    ``Blob`` row (a pipeline document or an env content blob). Pulling a
    pruned entry back into the local cache is the normal read: no workspace
    view is made and no row changes.

    Args:
        content_hash: The entry's hash.
        kind: :data:`KIND_SAMPLE`, ``KIND_PIPELINE_DOCUMENT`` or
            ``KIND_ENV_FINGERPRINT``.
        name: The sample's name, for a sample (its message and repair).
        project_dir: The project root; the ambient one when None.
        pull: Whether an entry recorded pushed is pulled.

    Returns:
        The path: a file's cache address, or a directory's checkout.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        NotInCacheError: The entry is not local, or changed, and not recorded
            pushed; or the pull did not bring it whole and intact. The
            message names the entry, its hash and the repair.
        CheckoutFailedError: A directory's checkout could not be built.
        ValueError: *kind* is not an entry kind.
    """
    root = Path(project_dir) if project_dir is not None else get_project_root()
    pushed, where, repair = _entry_facts(content_hash, kind, name)
    path, _pulled = _read_local_entry(root, content_hash, pushed=pushed, pull=pull,
                                      where=where, repair=repair, who="read_entry")
    return path


def entry_state(content_hash: str, kind: str, *, name: str | None = None,
                project_dir: Path | str | None = None) -> str:
    """Say whether :func:`read_entry` can serve an entry, without pulling it.

    A probe for a plan: it builds no checkout and pulls nothing.

    Args:
        content_hash: The entry's hash.
        kind: The entry's kind, as for :func:`read_entry`.
        name: The sample's name, for a sample.
        project_dir: The project root; the ambient one when None.

    Returns:
        ``OUTPUT_LOCAL`` (served from the local cache) or ``OUTPUT_REMOTE``
        (the read will pull it).

    Raises:
        MalformedEntryError: The address holds a directory tree.
        NotInCacheError: The read would refuse it: not local, or changed, and
            never pushed; the message is the read's own.
        ValueError: *kind* is not an entry kind.
    """
    root = Path(project_dir) if project_dir is not None else get_project_root()
    pushed, where, repair = _entry_facts(content_hash, kind, name)
    check_entry_shape(root, content_hash, repair)
    location, changed = _entry_location(root, content_hash, pushed, inputs_setting(root))
    if location == OUTPUT_MISSING:
        raise _missing_entry_error(where, changed, repair)
    return location


@task(purpose="Pull a pushed entry again: set the changed local objects aside, "
              "pull, protect what the pull wrote, and check every object before "
              "anything is served",
      inputs="the entry's hash and its changed objects; the DVC remote; "
             "[integrity] inputs",
      outputs="the entry complete and intact in the local cache, its pulled "
              "objects stamped; or NotInCacheError naming the repair")
def pull_again(project_dir: Path, content_hash: str, changed: list[str],
               where: str, repair: str, who: str, setting: str) -> None:
    """Pull a pushed entry (again), then check every object before it is served.

    The changed local objects leave their addresses first, because DVC's
    fetch skips an object that is present. After the pull the objects the
    pull wrote (those still writable) are made read-only; an intact object
    that stayed in place keeps its mode, so its stamp still matches. Then
    each goes through the one integrity check: a pulled object has no
    stamp, so it is hashed in full and stamped only when it matches its
    address. Shared by the output reader, the sample restore and
    ``wfc verify --repair``.

    Args:
        project_dir: Project root whose cache is read.
        content_hash: The entry's hash.
        changed: The local objects found changed (set aside first).
        where: The reader's description of the entry, for messages.
        repair: The sentence naming the repair (a re-run or a
            re-registration), ending a refusal.
        who: The reader's name, for the stderr trace.
        setting: ``[integrity] inputs``.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        NotInCacheError: The pull did not bring the entry whole, or brought
            bytes that do not match their addresses.
    """
    from .transport import pull_cache

    口 = Step(step_num=1, name="Set the changed objects aside",
             purpose="DVC's fetch skips a present object, so each changed one "
                     "leaves its address, stamp first")
    set_aside(project_dir, changed)

    口 = Step(step_num=2, name="Pull",
             purpose="Fetch the entry from the remote; a failure is reported "
                     "and judged by completeness below")
    try:
        pull_cache([content_hash], project_dir)
    except MalformedEntryError:
        raise
    except Exception as exc:  # noqa: BLE001 -- reported, then a miss
        print(f"{who}: pull_cache raised for {where}: {exc}",
              file=sys.stderr)

    口 = Step(step_num=3, name="Protect what the pull wrote",
             purpose="DVC's fetch writer bypasses cache_file, so the objects it "
                     "wrote are made read-only before they are checked and "
                     "stamped",
             critical="Only an object still writable is chmodded: a chmod moves "
                      "the change time an intact object's stamp records")
    for h in {content_hash} | manifest_members(project_dir, content_hash):
        protect_entry(_cache_path(project_dir, h))

    口 = Step(step_num=4, name="Check every object",
             purpose="The entry must be complete, and every object pass the one "
                     "check (the pulled ones are hashed in full and stamped)")
    if not entry_is_complete(project_dir, content_hash):
        raise NotInCacheError(
            f"{where} is recorded pushed, but the pull did not bring it "
            f"whole into the local cache ({_remote_state(project_dir)}). "
            f"{repair}"
        )
    still = changed_objects(project_dir, content_hash, setting)
    if still:
        raise NotInCacheError(
            f"{where} is recorded pushed, but the bytes pulled do not match "
            f"({changed_phrase(still)}). {repair}"
        )


def _remote_state(project_dir: Path) -> str:
    """A phrase naming whether a DVC remote is configured, for a miss message."""
    try:
        from .transport import has_remote_configured
        return (
            "remote configured but entry unavailable"
            if has_remote_configured(project_dir)
            else "no DVC remote configured"
        )
    except Exception:  # noqa: BLE001 -- a message detail only
        return "remote state unknown"


def _resolve_row(project_dir: Path, run_id: int, ro: RunOutput, pull: bool) -> Path:
    """Resolve one selected, archived output record to the path a user reads.

    Args:
        project_dir: Project root whose cache is read.
        run_id: The run the reader asked about (for messages).
        ro: The selected record.
        pull: Whether a remote entry is pulled.

    Returns:
        The cache path (a directory's checkout).

    Raises:
        NotArchivedError: The record has no content hash.
        NotInCacheError: The entry is neither local nor pulled.
        CheckoutFailedError: The entry is complete but its checkout failed.
        MalformedEntryError: The address holds a directory tree.
    """
    if not ro.content_hash:
        raise NotArchivedError(
            f"Output '{ro.slot}' of run {run_id} has not been archived "
            f"(no content hash). Run `wfc cache archive` first."
        )
    path, _pulled = _local_entry_path(project_dir, ro, run_id, pull, "resolve_output")
    return path


def provider_outputs(
    run_id: int, *, project_dir: Path, session
) -> list[tuple[str, RunOutput, Path]]:
    """Resolve and name every output of a run from the local cache, for the Canvas provider.

    Enumerates the run's output records, following a cache-hit audit row one
    hop to its source run's records, under the record-validity rule: a run
    with a malformed record raises rather than listing some of its outputs.
    :func:`output_export_names` names all of them, so a name is the one
    ``wfc export --all`` uses whether or not a sibling output is in the local
    cache.  Each record then resolves from the local cache only (the canvas
    never blocks an HTTP request on a remote pull).  Records that are
    un-archived or missing from the local cache are skipped with a log line
    rather than failing the caller.  A malformed cache entry is not skipped:
    its bytes are there in a shape nothing reads, so it raises, naming the
    re-run.

    Args:
        run_id: The run's integer id.
        project_dir: Project root whose cache is read.
        session: Open DB session the rows are read through.

    Returns:
        ``(export name, RunOutput, cache_path)`` for each resolvable row;
        empty for a run that does not exist.

    Raises:
        MalformedRecordError: The run has an output record without a slot.
        MalformedEntryError: An output's cache address holds a directory
            tree (a malformed cache entry).
        ResolveOutputError: The run's outputs cannot be named apart.
    """
    run = session.get(Run, run_id)
    if run is None:
        return []
    rows = _checked_output_rows(session, run, project_dir)
    names = output_export_names(rows)
    results: list[tuple[str, RunOutput, Path]] = []
    for name, ro in zip(names, rows):
        try:
            cache_path = _resolve_row(project_dir, run_id, ro, pull=False)
        except MalformedEntryError:
            # Not a miss: the entry is there in a shape nothing reads, and
            # skipping it would hide the re-run the message names.
            raise
        except ResolveOutputError as exc:
            print(
                f"[wfc_provider] run {run_id}: skipping output "
                f"'{ro.slot}': {exc}",
                file=sys.stderr,
            )
            continue
        results.append((name, ro, cache_path))
    return results


def output_export_name(ro: RunOutput) -> str:
    """Name one output for a user: its output name plus the artifact's suffix.

    The suffix comes from ``artifact_path`` — a cache entry is stored under
    a bare hash name and carries none of its own.  A row whose
    ``output_name`` already ends in that suffix (what the collect phase
    writes, since it records the saved file's own name) is not doubled, and
    a row with no name at all falls back to the artifact's own file name.

    A single-output ``wfc export`` names its copy with this.  Listings of a
    run's outputs use :func:`output_export_names`, which starts from this
    name.

    Args:
        ro: The ``RunOutput`` row to name.

    Returns:
        A filename-like display name for the output.
    """
    name = ro.output_name or (
        Path(ro.artifact_path).name if ro.artifact_path else "output"
    )
    suffix = Path(ro.artifact_path).suffix if ro.artifact_path else ""
    if suffix and not name.endswith(suffix):
        name += suffix
    return name


def output_export_names(rows: Sequence[RunOutput]) -> list[str]:
    """Name every output of one run for a user, keeping the names distinct.

    Each output keeps its :func:`output_export_name`.  When two outputs share
    that name, each of them is placed under its slot's folder
    (``qc/report.html``, ``final/report.html``).  ``wfc export --all`` names
    its copies with this and the Canvas provider names every artifact it
    lists and serves with it (a directory output's members sit under its
    name), so the terminal and the browser call a given output the same
    thing.

    Args:
        rows: One run's output records (after any audit hop).

    Returns:
        One name per row, in row order.

    Raises:
        ResolveOutputError: A slot folder has the same name as another of
            the run's outputs, so one name would stand for two outputs.
    """
    bases = [output_export_name(ro) for ro in rows]
    counts = Counter(bases)
    names = [
        f"{ro.slot}/{base}" if counts[base] > 1 else base
        for ro, base in zip(rows, bases)
    ]
    folders = {n.split("/", 1)[0] for n, b in zip(names, bases) if counts[b] > 1}
    plain = {n for n, b in zip(names, bases) if counts[b] == 1}
    collided = sorted(folders & plain)
    if collided:
        run_id = rows[0].run_id if rows else "?"
        raise ResolveOutputError(
            f"Outputs of run {run_id} cannot be named apart: the slot folder "
            f"{', '.join(repr(c) for c in collided)} has the same name as "
            f"another output of the run."
        )
    return names


def exportable_outputs(
    run_id: int,
    *,
    project_dir: Path | None = None,
    session=None,
) -> list[tuple[RunOutput, Path]]:
    """Resolve every output of a run, strictly, for ``wfc export --all``.

    The same enumeration as :func:`provider_outputs` — the run's output
    records, or, for a cache-hit audit row that owns none, the records of
    the run it reused, under the record-validity rule — with the opposite
    failure rule for a record that does not resolve.  The canvas skips a row
    it cannot resolve and renders the rest; an export refuses, because a
    directory holding some of a run's outputs and no word about the others
    is worse than no directory at all.  So a run that does not exist, a run
    with a malformed record and a run with no recorded outputs are typed
    errors raised before anything is resolved, and a row that resolves
    badly raises too.

    Every row is resolved in one session, with ``pull=True``: the terminal
    may wait on the remote, unlike the canvas.

    Args:
        run_id: The run's integer id.
        project_dir: Project root whose cache is read; defaults to the
            ambient project root.
        session: Open DB session override; defaults to a fresh session on
            the global engine.

    Returns:
        ``(RunOutput, cache_path)`` for every row, in row order.

    Raises:
        UnknownRunError: ``run_id`` does not exist.
        MalformedRecordError: The run has an output record without a slot.
        UnknownOutputError: The run records no outputs.
        NotArchivedError: A row's ``content_hash`` is NULL — point the user
            at ``wfc cache archive``.
        NotInCacheError: A row's hash is neither in the local cache nor
            pullable from the remote.
    """
    if project_dir is None:
        project_dir = get_project_root()
    if session is None:
        with get_session() as _session:
            return _exportable_outputs_in_session(_session, project_dir, run_id)
    return _exportable_outputs_in_session(session, project_dir, run_id)


def _exportable_outputs_in_session(
    session, project_dir: Path, run_id: int
) -> list[tuple[RunOutput, Path]]:
    """Body of :func:`exportable_outputs`, against an open session.

    Args:
        session: The open session every read and resolution goes through.
        project_dir: Project root whose cache is read.
        run_id: The run's integer id.

    Returns:
        ``(RunOutput, cache_path)`` for every row, in row order.
    """
    run = session.get(Run, run_id)
    if run is None:
        raise UnknownRunError(f"Run {run_id} not found.")

    rows = _checked_output_rows(session, run, project_dir)
    if not rows:
        raise UnknownOutputError(f"Run {run_id} has no recorded outputs.")

    return [(ro, _resolve_row(project_dir, run_id, ro, pull=True)) for ro in rows]


def output_listing(
    run_id: int, *, project_dir: Path | None = None, session=None
) -> list[tuple[str, str]]:
    """List a run's outputs by slot and file name, for ``wfc export`` with no slot.

    The same enumeration every reader selects from: the run's output
    records, or a cache-hit audit row's source records, under the
    record-validity rule.  Nothing is resolved, so an output that is not
    archived or not local is still listed; reading it is the export's
    business.

    Args:
        run_id: The run's integer id.
        project_dir: Project root; defaults to the ambient project root.
        session: Open DB session override; defaults to a fresh session.

    Returns:
        ``(slot, file name)`` per record, in row order, the file name being
        the one a single-output export writes (:func:`output_export_name`);
        empty when the run recorded no outputs.

    Raises:
        UnknownRunError: ``run_id`` does not exist.
        MalformedRecordError: The run has an output record without a slot.
    """
    if project_dir is None:
        project_dir = get_project_root()
    if session is None:
        with get_session() as _session:
            return output_listing(run_id, project_dir=project_dir, session=_session)
    run = session.get(Run, run_id)
    if run is None:
        raise UnknownRunError(f"Run {run_id} not found.")
    rows = _checked_output_rows(session, run, project_dir)
    # _checked_output_rows refuses a record without a slot, so every slot is set.
    return [(str(ro.slot), output_export_name(ro)) for ro in rows]


def output_records(
    run_id: int, *, project_dir: Path | None = None, session=None
) -> list[RunOutput]:
    """A run's output records, under the record-validity rule, for a run bundle.

    The enumeration :func:`output_listing` names, as the records themselves:
    the run's output rows, or a cache-hit audit row's source rows, each
    carrying its slot and, once archived, its content hash. Nothing is
    resolved, so a record that is not archived or not local is still
    returned; what is missing is the caller's to refuse.

    Args:
        run_id: The run's integer id.
        project_dir: Project root; defaults to the ambient project root.
        session: Open DB session override; defaults to a fresh session.

    Returns:
        The records in row order; empty when the run recorded no outputs.

    Raises:
        UnknownRunError: ``run_id`` does not exist.
        MalformedRecordError: A record lacks a slot, or a pre-archive
            record's path lies outside its run's archive.
    """
    if project_dir is None:
        project_dir = get_project_root()
    if session is None:
        with get_session() as _session:
            return output_records(run_id, project_dir=project_dir, session=_session)
    run = session.get(Run, run_id)
    if run is None:
        raise UnknownRunError(f"Run {run_id} not found.")
    return _checked_output_rows(session, run, project_dir)


def resolve_run_reference_outputs(
    run_ref_outputs: dict[str, dict],
    session,
) -> dict[str, dict]:
    """Resolve artifact paths, sample and method for run_reference nodes.

    The composer's read (``wfc.execution.load_pipeline_from_document``).
    For each run_reference entry, DB-resolves:
      - ``output_paths``: {slot: artifact_path} for every output record of
        the referenced Run. Each edge picks from here: the output it names,
        or the only one when it names none.
      - ``sample``: the referenced ``Run.sample``. Merged into the pipeline
        samples list so a run_reference-rooted pipeline actually has work to
        do.
      - ``method``: the referenced run's method name, for messages.
      - ``malformed``: True when the referenced run has an output record
        without a slot (the record-validity rule); ``output_paths`` stays
        empty then, and the Graph load's binding reports the reference.

    A referenced run with no output records leaves ``output_paths`` empty
    and logs a warning naming the node; the Graph load reports it as an
    invalid reference. A query that fails propagates as itself: the session
    is the composer's, whose policy is one failure before the launch.

    Args:
        run_ref_outputs: Dict mapping node_id to ``{"run_id": ...}``.
        session: An open database session.

    Returns:
        The same dict, mutated with ``output_paths``, ``sample``, ``method``
        and ``malformed`` resolved where possible.
    """
    # Default fields on every entry so callers can rely on them existing.
    for info in run_ref_outputs.values():
        info.setdefault("output_paths", {})
        info.setdefault("sample", "")
        info.setdefault("method", "")
        info.setdefault("malformed", False)

    if not run_ref_outputs:
        return run_ref_outputs

    for nid, info in run_ref_outputs.items():
        run_id = info.get("run_id", "")
        if not run_id:
            continue
        try:
            run_id_int = int(run_id)
        except (ValueError, TypeError):
            continue

        run_row = session.get(Run, run_id_int)
        if run_row is not None:
            method = (session.get(Method, run_row.method_id)
                      if run_row.method_id else None)
            info["method"] = method.name if method is not None else ""
            # Referenced Run's sample (single value — one run, one sample)
            if run_row.sample:
                info["sample"] = run_row.sample

        # All output records of the referenced run → {slot: path}
        output_rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id_int)
        ).all()
        if run_row is not None and output_rows:
            try:
                _check_records(session, run_row, output_rows)
            except MalformedRecordError:
                info["malformed"] = True
                continue
        for row in output_rows:
            if row.slot and row.artifact_path:
                info["output_paths"][row.slot] = row.artifact_path

        if not info["output_paths"]:
            logger.warning(
                "Run reference node %s: no artifacts found for run_id=%s",
                nid, run_id,
            )
    return run_ref_outputs
