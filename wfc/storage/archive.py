"""The archive pass: hash and cache every un-archived run output.

:func:`unarchived_outputs` is the one selection of un-archived outputs:
rows with no content hash and no changed mark, on completed runs.
:func:`archive_outputs` copies each output into the cache with one hashing
read and records the md5 of the bytes it copied. An output that changed
after its run wrote it (against collect's stat record, before or during
the copy, or against collect's hash under ``[integrity] inputs = "hash"``)
is refused: the row is marked changed and records no hash. Each row is
committed as soon as its verified copy lands, so an interrupted pass keeps
its progress; once committed, the output leaves the run archive, so the
cache entry is its one local copy. The pass ends by marking every cache
entry read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from axiom_annotations import AutoStep, Step, task

from ..identity import DirectoryContentError
from ..persistence import project_root as get_project_root
from .cache import ChangedSourceError, output_repair, protect_entry, store_copy
from .integrity import check_output, inputs_setting, mark_changed
from .prune import scan_dvc_cache_entries
from .resolve import malformed_record_message, outside_run_archive, run_archive_path

if TYPE_CHECKING:
    from sqlmodel import Session

    from ..persistence import Run, RunOutput


@task(
    purpose="Select the un-archived outputs: output rows with no content hash "
            "and no changed mark, on completed runs",
    inputs="an open session; optional run_id filter",
    outputs="(RunOutput, Run) pairs, bound to the session",
)
def unarchived_outputs(
    session: Session,
    *,
    run_id: int | None = None,
) -> list[tuple[RunOutput, Run]]:
    """Select the un-archived outputs: rows with no content hash on completed runs.

    A row marked changed after its run wrote it is left out: the archive
    pass never archives it, so it is not pending (``wfc verify`` still
    lists it). The one definition of "un-archived" read by the archive pass and by the
    canvas's archive status and progress snapshot.

    Args:
        session: An open database session. The rows stay bound to it, so a
            caller can update them in place.
        run_id: Optional filter to one run's outputs.

    Returns:
        One ``(RunOutput, Run)`` pair per un-archived output.
    """
    from sqlmodel import col, select

    from ..persistence import Run, RunOutput

    query = (
        select(RunOutput, Run)
        .join(Run, col(RunOutput.run_id) == col(Run.id))
        .where(col(RunOutput.content_hash).is_(None))
        .where(col(RunOutput.integrity_status).is_(None))
        .where(Run.status == "completed")
    )
    if run_id is not None:
        query = query.where(RunOutput.run_id == run_id)
    return list(session.exec(query).all())


class _ChangedOutput(Exception):
    """An output is not what its run wrote: refused, marked, not archived."""


def _changed_message(ro: RunOutput, artifact: Path) -> str:
    """The refusal: the file, the run that wrote it, and the re-run."""
    return (
        f"{artifact} changed after run {ro.run_id} wrote it; it was not "
        f"archived. {output_repair(ro.run_id)}"
    )


@task(purpose="Archive one output with one verified read: check it against "
              "collect's record, copy it into the cache with the hashing copy, "
              "check it again, and return the hash of the bytes copied",
      inputs="the output row (no content hash, not marked); its run-archive path; "
             "[integrity] inputs, read once per pass",
      outputs="the content hash to record; or _ChangedOutput when the output is "
              "not what its run wrote")
def archive_one(ro: RunOutput, artifact: Path, project_dir: Path, setting: str) -> str:
    """Archive one output with one verified read; return the hash to record.

    Without a collect hash the output is checked against collect's stat
    record before a byte is read, copied with the hashing copy, then
    checked again, so a change before or during the copy is refused. With
    a collect hash (``[integrity] inputs = "hash"``) the hashing copy's
    streamed md5 must equal it, which is the check: nothing is hashed a
    second time.

    Args:
        ro: The output row (no content hash, not marked).
        artifact: Its run-archive path.
        project_dir: The project root.
        setting: ``[integrity] inputs``, read once per pass.

    Returns:
        The content hash of the verified copy.

    Raises:
        _ChangedOutput: The output is not what its run wrote, including a
            directory that no longer passes Identity's listing (a symlink, a
            case-only collision, no files).
    """
    口 = Step(step_num=1, name="Check against collect's record",
             purpose="Without a collect hash, the output must match collect's "
                     "stat record before a byte is read")
    if ro.collect_hash is None and not check_output(ro, artifact, project_dir,
                                                    setting).intact:
        raise _ChangedOutput(_changed_message(ro, artifact))

    口 = Step(step_num=2, name="Copy with the hashing copy",
             purpose="Copy the output into the cache, hashing the bytes written; "
                     "with a collect hash they must have it",
             critical="Bytes that are not collect's hash, or a directory the "
                      "listing now refuses, are a change: refused and marked")
    try:
        content_hash = store_copy(artifact, ro.collect_hash, project_dir, setting)
    except (ChangedSourceError, DirectoryContentError) as exc:
        raise _ChangedOutput(_changed_message(ro, artifact)) from exc

    口 = Step(step_num=3, name="Check again after the copy",
             purpose="Without a collect hash, a change during the copy is caught "
                     "on collect's stat record; only then is the hash returned")
    if ro.collect_hash is None and not check_output(ro, artifact, project_dir,
                                                    setting).intact:
        # Changed during the copy: only content-correct orphan objects stay.
        raise _ChangedOutput(_changed_message(ro, artifact))
    return content_hash


@task(purpose="Remove an archived output from its run archive: the file, or the "
              "directory with every member, and a directory's member stamp; "
              "the run folder keeps its logs and sidecars",
      inputs="the output row, its hash committed; its run-archive path",
      outputs="the output gone from the run archive; a removal that fails is "
              "named on stderr with how to remove the copy left in the run "
              "folder, and the row stays archived",
      critical="Runs only after the row's hash is committed, so the cache "
               "entry is the output's one local copy; no file is chmodded")
def remove_from_run_archive(ro: RunOutput, artifact: Path, project_dir: Path) -> None:
    """Remove an archived output from the run archive, leaving the cache as its copy.

    The output is a file, a directory (its folders opened first; collect
    left them read-only), or a ``_workdir/`` path the results manifest
    named. A directory output's member stamp describes the removed
    members, so it goes too. The row keeps its ``artifact_path``: the place
    the run wrote the output, which a reader of an archived row never
    opens. A removal that fails (a file held open) is named on stderr; the
    row stays archived (the cache holds the output) and the leftover copy
    stays in the run folder. ``wfc cache prune`` keeps the folder of a run
    whose outputs are recorded, so the copy goes when it is deleted by hand
    or with ``wfc cache prune --all``, which removes every run folder.

    Args:
        ro: The output row; its hash is committed.
        artifact: Its run-archive path.
        project_dir: The project root.
    """
    import sys

    from .. import layout
    from . import links

    try:
        links.delete_tree(artifact)
        if ro.slot:
            layout.output_members_stamp(project_dir, ro.run_id, ro.slot).unlink(
                missing_ok=True)
    except OSError as exc:
        print(f"WARNING: run {ro.run_id}'s output is archived (the cache holds "
              f"it), but its copy {artifact} could not be removed: {exc}. The "
              f"copy stays in the run folder: delete it once nothing holds it "
              f"open, or run `wfc cache prune --all` (removes every run "
              f"folder).", file=sys.stderr)


@task(
    purpose="The archive pass: hash and cache every un-archived output of a "
            "completed run, one commit per row, remove it from the run "
            "archive, then mark every cache entry read-only",
    inputs="project_dir; optional run_id filter; optional per-file progress callback",
    outputs="one dict per row considered: run_id, output_name, content_hash, status",
)
def archive_outputs(
    project_dir: Path | str,
    *,
    run_id: int | None = None,
    progress_fn: Callable[[int, str, str], object] | None = None,
) -> list[dict]:
    """Hash and cache all un-archived RunOutput rows.

    Queries the DB for RunOutput rows where content_hash IS NULL (deferred
    archiving) and no changed mark, copies each output into the DVC cache
    with one hashing read, and records the md5 of the bytes copied.  Each row is committed individually, immediately
    after its cache copy lands — an interrupted pass keeps all completed
    rows and a re-run picks up only the remainder.  Once a row's hash is
    committed its output leaves the run archive
    (:func:`remove_from_run_archive`): the cache entry is its one local
    copy, and the run folder keeps its logs and sidecars.

    Args:
        project_dir: Root directory of the wfc project.
        run_id: Optional filter to archive only outputs from a specific run.
        progress_fn: Optional callback called per file with
            (run_id, output_name, status).  Status is "hashing" (before
            hashing starts), then one of "archived", "missing", or
            "error:{message}", or "changed:{message}" when the output is not
            what its run wrote (checked against collect's record before and
            after the copy, or against collect's hash by the copy itself):
            the row is then marked changed and records no hash, the message
            naming the file, the run and the re-run.  A record whose path
            lies outside its run's
            archive is a malformed record (the record-validity rule): it is
            never hashed or cached, and its one status is
            "malformed:{message}", the message naming the run to re-run.
            An exception raised by the callback aborts the pass; rows
            committed so far are kept.

    Returns:
        List of dicts with keys: run_id, output_name, content_hash, status.
    """
    from ..persistence import get_session

    project_dir = Path(project_dir).resolve()
    results: list[dict] = []

    口 = Step(step_num=1, name="Select the un-archived rows",
             purpose="Read [integrity] inputs once for the pass and select the "
                     "rows to archive; a row with a content hash or a changed "
                     "mark, or on a run that is not completed, is skipped")
    setting = inputs_setting(project_dir)

    with get_session() as session:
        pairs = unarchived_outputs(session, run_id=run_id)

        口 = Step(step_num=2, name="Archive each row",
                 purpose="Per row: a malformed record or a missing file gets its "
                         "status and is never read; any other row is archived with "
                         "one verified read and committed on its own, then "
                         "removed from the run archive; or marked changed, or "
                         "given its error",
                 critical="A row's hash is committed only after its verified "
                          "cache copy landed, and its output is removed only "
                          "after that commit; a raising progress callback "
                          "aborts the pass and keeps the rows committed so far")
        for ro, run in pairs:
            entry: dict = {
                "run_id": ro.run_id,
                "output_name": ro.output_name,
                "content_hash": None,
                "status": "unknown",
            }

            # The record-validity rule: a path outside the run's archive is
            # a malformed record, never hashed or cached.
            if outside_run_archive(ro, project_dir):
                entry["status"] = f"malformed:{malformed_record_message(session, run)}"
                if progress_fn:
                    progress_fn(ro.run_id, ro.output_name or "<unknown>", entry["status"])
                results.append(entry)
                continue

            artifact = run_archive_path(ro, project_dir)
            if artifact is None or not artifact.exists():
                entry["status"] = "missing"
                if progress_fn:
                    progress_fn(ro.run_id, ro.output_name or "<unknown>", "missing")
                results.append(entry)
                continue

            # Outside the try: a raising callback aborts the whole pass,
            # leaving rows committed so far intact (resume via re-run).
            if progress_fn:
                progress_fn(ro.run_id, ro.output_name or "<unknown>", "hashing")

            try:
                口 = AutoStep(step_num=2.1, name="Archive one output")
                content_hash = archive_one(ro, artifact, project_dir, setting)
                ro.content_hash = content_hash
                session.add(ro)
                # Per-row commit, only after the verified cache copy above
                # landed: a kill can never leave a committed hash whose blob
                # is absent.
                session.commit()
                entry["content_hash"] = content_hash
                entry["status"] = "archived"
            except _ChangedOutput as exc:
                session.rollback()
                mark_changed(ro)
                entry["status"] = f"changed:{exc}"
            except Exception as exc:
                # Drop any pending mutation from the failed row so it can't
                # ride along with a later row's commit.
                session.rollback()
                entry["status"] = f"error:{exc}"

            if entry["status"] == "archived":
                # Only after the hash is committed: the cache entry is then
                # the output's one local copy, and every reader of an
                # archived row reads it there.
                口 = AutoStep(step_num=2.2, name="Remove the output from the run archive")
                remove_from_run_archive(ro, artifact, project_dir)

            if progress_fn:
                progress_fn(ro.run_id, ro.output_name or "<unknown>", entry["status"])

            results.append(entry)

    口 = Step(step_num=3, name="Protection sweep",
             purpose="Mark every still-writable cache entry read-only "
                     "(best-effort, idempotent)")
    # Protection sweep (best-effort, idempotent): mark every cache entry
    # read-only. Covers entries an older cache left writable and entries
    # materialized by DVC pulls, which bypass cache_file. Runs
    # on both `wfc cache archive` and the run_pipeline auto-archive.
    # Every well-formed entry is a file (a member or a directory's
    # manifest); a tree at an entry address is a malformed legacy record,
    # left untouched here and refused by name when something reads it.
    # Only an entry that is still writable is chmodded: a chmod moves the
    # ctime an integrity stamp records, so chmodding a protected, stamped
    # object would cost its next read a re-hash.
    for entry_path in scan_dvc_cache_entries(project_dir).values():
        protect_entry(entry_path)

    return results


@task(purpose="Hash and cache un-archived outputs from completed runs")
def cache_archive(*, run_id: int | None = None) -> int:
    """Hash and cache un-archived outputs from completed runs.

    Queries RunOutput rows with NULL content_hash, hashes each file,
    copies into DVC cache, and updates the DB.  Prints per-file progress.

    Args:
        run_id: Optional filter to archive only outputs from a specific run.

    Returns:
        0 on success.
    """
    project_dir = get_project_root()

    def _progress(_run_id, name: str, status: str) -> None:
        if status != "hashing":
            print(f"  {name}: {status}")

    print("Archiving un-archived outputs...")
    results = archive_outputs(project_dir, run_id=run_id, progress_fn=_progress)

    if not results:
        print("Nothing to archive.")
    else:
        archived = sum(1 for r in results if r["status"] == "archived")
        missing = sum(1 for r in results if r["status"] == "missing")
        malformed = sum(1 for r in results if r["status"].startswith("malformed:"))
        errors = sum(1 for r in results if r["status"].startswith("error:"))
        changed = sum(1 for r in results if r["status"].startswith("changed:"))
        print(
            f"Done: {archived} archived, {missing} missing, "
            f"{malformed} malformed, {changed} changed, {errors} errors."
        )

    return 0
