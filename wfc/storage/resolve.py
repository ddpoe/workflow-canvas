"""The resolvers: a run output's record to the path its reader reads.

Every reader selects a run's output by the contract slot it fills, and every
reader goes through one record-validity check: each output record must carry
its slot, and a run with an output record that does not has a malformed
record (:class:`MalformedRecordError`), named with the run to re-run.

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
from pathlib import Path
from typing import Sequence

from axiom_annotations import Step, task
from sqlmodel import select

from ..persistence import Method, Run, RunOutput, get_session
from ..persistence import project_root as get_project_root
from .cache import (
    MalformedEntryError,
    _cache_path,
    _make_read_only,
    check_entry_shape,
    entry_is_complete,
    entry_is_local,
    local_path,
    manifest_members,
    output_repair,
)

logger = logging.getLogger(__name__)


class ResolveOutputError(Exception):
    """Base error for :func:`resolve_output` — correct bytes or a clear error."""


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


def _records_without_slot(rows: Sequence[RunOutput]) -> list:
    """The records that break the record-validity rule: the ones with no slot.

    The rule's one test, shared by the check that raises and the predicate
    the cache lookup asks.

    Args:
        rows: A run's output records.

    Returns:
        The record ids at fault, in row order.
    """
    return [r.id for r in rows if r.slot is None]


def _check_records(session, run: Run, rows: Sequence[RunOutput]) -> None:
    """Apply the record-validity rule: every output record carries its slot.

    The one place the rule and its error live.  A run with no records passes
    (its readers report "no recorded outputs").  A run with a record that
    lacks a slot logs the records at fault and raises the user sentence.

    Args:
        session: Open DB session (for the run's method name).
        run: The run the reader asked about.
        rows: The records read for it (after any audit hop).

    Raises:
        MalformedRecordError: A record lacks a slot.
    """
    lacking = _records_without_slot(rows)
    if not lacking:
        return
    method = session.get(Method, run.method_id) if run.method_id else None
    method_name = method.name if method is not None else "unknown method"
    logger.error(
        "Run %s (%s): output records %s of run %s carry no slot; every output "
        "record must name the slot it fills",
        run.id, method_name, lacking, rows[0].run_id,
    )
    raise MalformedRecordError(
        f"Run {run.id} ({method_name}) has a malformed record; re-run it."
    )


def _checked_output_rows(session, run: Run) -> list[RunOutput]:
    """A run's output records after the audit hop, under the record-validity rule.

    Args:
        session: Open DB session.
        run: The run whose outputs are read.

    Returns:
        The records every reader selects from.

    Raises:
        MalformedRecordError: A record lacks a slot.
    """
    rows = _source_output_rows(session, run)
    _check_records(session, run, rows)
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
    return [r.slot for r in _checked_output_rows(session, run)]


def _slot_listing(rows: Sequence[RunOutput]) -> str:
    """``slot (file name)`` pairs for an error message."""
    return ", ".join(f"{r.slot} ({r.output_name})" for r in rows) or "(none)"


def has_malformed_output_records(run_id: int, *, session=None) -> bool:
    """Whether a run's output records fail the record-validity rule.

    The rule the readers enforce, as a predicate: the run (after a cache-hit
    audit hop) has output records and at least one lacks a slot.  A run
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
    return bool(_records_without_slot(_source_output_rows(session, run)))


#: Where one run output's bytes can be read from (:func:`output_location`).
OUTPUT_LOCAL = "local"
OUTPUT_REMOTE = "remote"
OUTPUT_MISSING = "missing"


def _run_archive_path(row: RunOutput, project_dir: Path) -> Path | None:
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


def output_location(row: RunOutput, project_dir: Path) -> str:
    """Place one output record: ``local``, ``remote`` or ``missing``.

    The one answer to "where are this output's bytes", read by the hit rule
    (Execution's ``classify_cache_key``), the Runs Preview, and both
    resolvers (through :func:`_local_entry_path` and :func:`resolve_input`),
    so a hit is served exactly when its consumer can read it.

    An archived record (it has a content hash) is local exactly when its
    cache entry is complete (``cache.entry_is_complete``: a file's object,
    or a directory's manifest and every member), and remote when it is not
    local but recorded pushed. A record the archive pass has not reached
    (no content hash) is local when its recorded run-archive path exists; it
    is never remote, because a push needs a hash. ``local`` therefore
    combines the DVC cache and the run archive on purpose.

    A presence probe only: it builds no checkout, pulls nothing and does no
    network I/O. A malformed cache entry (a directory tree at an unsuffixed
    address) is ``missing`` here, so the hit rule reports the step's
    outputs missing and the step re-runs; the reader refuses it loudly.

    Args:
        row: The output record.
        project_dir: The project root whose DVC cache and run archive are
            consulted.

    Returns:
        ``OUTPUT_LOCAL``, ``OUTPUT_REMOTE`` or ``OUTPUT_MISSING``.
    """
    from ..persistence import PushStatus

    if not row.content_hash:
        path = _run_archive_path(row, project_dir)
        return OUTPUT_LOCAL if path is not None and path.exists() else OUTPUT_MISSING
    if entry_is_local(project_dir, row.content_hash):
        return OUTPUT_LOCAL
    try:
        # A malformed entry is never remote either: the reader refuses it
        # before any pull, so promising a pull would be a hit it cannot serve.
        check_entry_shape(project_dir, row.content_hash)
    except MalformedEntryError:
        return OUTPUT_MISSING
    if row.push_status == PushStatus.pushed.value:
        return OUTPUT_REMOTE
    return OUTPUT_MISSING


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
            rows = _checked_output_rows(session, run)
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
                         "places it local; a file that is gone is a loud FAIL "
                         "raised as InputUnavailableError, naming the path and "
                         "the re-run",
                 critical="The hit rule serves a hit only when this resolver "
                          "can read it; deciding from the same rule is what "
                          "keeps the two from disagreeing")
        if not ro.content_hash:
            path = _run_archive_path(ro, project_dir)
            if output_location(ro, project_dir) == OUTPUT_LOCAL:
                print(f"resolve_input: ARCHIVE (run {run_id})", file=sys.stderr)
                return str(path)
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

    rows = _checked_output_rows(session, run)
    if not rows:
        raise UnknownOutputError(f"Run {run_id} has no recorded outputs.")
    available = [ro.slot for ro in rows]

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

    - ``local`` -- the complete entry's path (``cache.local_path``: a file's
      cache address, or a directory's read-only checkout).
    - ``remote`` -- recorded pushed and not local: pulled (when ``pull``),
      the pulled objects re-protected, then read as local.
    - ``missing`` -- neither local nor recorded pushed: refused without a
      pull, since a hash that was never pushed cannot be pulled.

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
        NotInCacheError: The entry is not local and not recorded pushed, or
            the pull was not allowed or did not bring it whole.
        CheckoutFailedError: The entry is complete in the cache but its
            directory checkout could not be built.
    """
    from .transport import pull_cache

    project_dir = Path(project_dir)
    content_hash = ro.content_hash
    check_entry_shape(project_dir, content_hash, output_repair(run_id))
    where = f"run {run_id}, output '{ro.slot}' (content_hash={content_hash})"
    location = output_location(ro, project_dir)

    if location == OUTPUT_MISSING:
        raise NotInCacheError(
            f"{where} is not complete in the local cache and was never "
            f"pushed, so there is nothing to pull. {output_repair(run_id)}"
        )

    pulled = False
    if location == OUTPUT_REMOTE:
        if not pull:
            raise NotInCacheError(
                f"{where} is not in the local cache; it is recorded pushed, "
                f"but this reader does not pull"
            )
        try:
            pull_cache([content_hash], project_dir)
        except MalformedEntryError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported, then a miss
            print(f"{who}: pull_cache raised for run {run_id}: {exc}",
                  file=sys.stderr)
        # Best-effort: DVC's fetch writer bypasses cache_file, so protect the
        # freshly pulled objects here (the archive sweep covers them eventually).
        for h in {content_hash} | manifest_members(project_dir, content_hash):
            if _cache_path(project_dir, h).is_file():
                _make_read_only(_cache_path(project_dir, h))
        if not entry_is_complete(project_dir, content_hash):
            raise NotInCacheError(
                f"{where} is recorded pushed, but the pull did not bring it "
                f"whole into the local cache ({_remote_state(project_dir)}). "
                f"{output_repair(run_id)}"
            )
        pulled = True

    path = local_path(project_dir, content_hash)
    if path is None:
        raise CheckoutFailedError(
            f"{where} is complete in the local cache, but its checkout "
            f"could not be built"
        )
    return path, pulled


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
    rather than failing the caller.

    Args:
        run_id: The run's integer id.
        project_dir: Project root whose cache is read.
        session: Open DB session the rows are read through.

    Returns:
        ``(export name, RunOutput, cache_path)`` for each resolvable row;
        empty for a run that does not exist.

    Raises:
        MalformedRecordError: The run has an output record without a slot.
        ResolveOutputError: The run's outputs cannot be named apart.
    """
    run = session.get(Run, run_id)
    if run is None:
        return []
    rows = _checked_output_rows(session, run)
    names = output_export_names(rows)
    results: list[tuple[str, RunOutput, Path]] = []
    for name, ro in zip(names, rows):
        try:
            cache_path = _resolve_row(project_dir, run_id, ro, pull=False)
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

    rows = _checked_output_rows(session, run)
    if not rows:
        raise UnknownOutputError(f"Run {run_id} has no recorded outputs.")

    return [(ro, _resolve_row(project_dir, run_id, ro, pull=True)) for ro in rows]


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
