"""The archive pass: hash and cache every un-archived run output.

:func:`unarchived_outputs` is the one selection of un-archived outputs:
rows with no content hash, on completed runs. :func:`archive_outputs`
commits each row as soon as its cache copy lands, so an interrupted pass
keeps its progress, and ends by marking every cache entry read-only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from axiom_annotations import task

from ..identity import hash_path
from ..persistence import project_root as get_project_root
from .cache import _make_read_only, cache_file
from .prune import scan_dvc_cache_entries

if TYPE_CHECKING:
    from sqlmodel import Session

    from ..persistence import Run, RunOutput


@task(
    purpose="Select the un-archived outputs: output rows with no content hash, "
            "on completed runs",
    inputs="an open session; optional run_id filter",
    outputs="(RunOutput, Run) pairs, bound to the session",
)
def unarchived_outputs(
    session: Session,
    *,
    run_id: int | None = None,
) -> list[tuple[RunOutput, Run]]:
    """Select the un-archived outputs: rows with no content hash on completed runs.

    The one definition of "un-archived" read by the archive pass and by the
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
        .where(Run.status == "completed")
    )
    if run_id is not None:
        query = query.where(RunOutput.run_id == run_id)
    return list(session.exec(query).all())


@task(
    purpose="The archive pass: hash and cache every un-archived output of a "
            "completed run, one commit per row, then mark every cache entry read-only",
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
    archiving), computes hashes, copies files into the DVC cache, and
    updates the DB rows.  Each row is committed individually, immediately
    after its cache copy lands — an interrupted pass keeps all completed
    rows and a re-run picks up only the remainder.

    Args:
        project_dir: Root directory of the wfc project.
        run_id: Optional filter to archive only outputs from a specific run.
        progress_fn: Optional callback called per file with
            (run_id, output_name, status).  Status is "hashing" (before
            hashing starts), then one of "archived", "missing", or
            "error:{message}".  An exception raised by the callback aborts
            the pass; rows committed so far are kept.

    Returns:
        List of dicts with keys: run_id, output_name, content_hash, status.
    """
    from ..persistence import get_session

    project_dir = Path(project_dir).resolve()
    results: list[dict] = []

    with get_session() as session:
        outputs = [ro for ro, _run in unarchived_outputs(session, run_id=run_id)]

        for ro in outputs:
            artifact = Path(ro.artifact_path) if ro.artifact_path else None
            entry: dict = {
                "run_id": ro.run_id,
                "output_name": ro.output_name,
                "content_hash": None,
                "status": "unknown",
            }

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
                content_hash = hash_path(artifact)
                # archive caches existing artifacts; preserve source.
                cache_file(artifact, content_hash, project_dir, move=False)
                ro.content_hash = content_hash
                session.add(ro)
                # Per-row commit, only after the cache copy above landed: a
                # kill can never leave a committed hash whose blob is absent.
                session.commit()
                entry["content_hash"] = content_hash
                entry["status"] = "archived"
            except Exception as exc:
                # Drop any pending mutation from the failed row so it can't
                # ride along with a later row's commit.
                session.rollback()
                entry["status"] = f"error:{exc}"

            if progress_fn:
                progress_fn(ro.run_id, ro.output_name or "<unknown>", entry["status"])

            results.append(entry)

    # Protection sweep (best-effort, idempotent): mark every cache entry
    # read-only. Covers entries written before write-time protection landed
    # and entries materialized by DVC pulls, which bypass cache_file. Runs
    # on both `wfc cache archive` and the run_pipeline auto-archive.
    # Every well-formed entry is a file (a member or a directory's
    # manifest); a tree at an entry address is a malformed legacy record,
    # left untouched here and refused by name when something reads it.
    for entry_path in scan_dvc_cache_entries(project_dir).values():
        if entry_path.is_file():
            _make_read_only(entry_path)

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
        errors = sum(1 for r in results if r["status"].startswith("error:"))
        print(f"Done: {archived} archived, {missing} missing, {errors} errors.")

    return 0
