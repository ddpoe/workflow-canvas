"""Prune: remove run archives and cache entries that nothing references.

The scans enumerate ``.runs/`` and the cache; the reference queries read
``RunOutput`` rows.  When a remote is configured, :func:`prune_dvc_cache`
keeps every entry whose row has not been pushed yet, unless forced.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from axiom_annotations import task
from sqlmodel import col, select

from .. import layout
from ..identity import is_directory_hash
from ..persistence import get_session
from ..persistence import project_root as get_project_root
from .cache import _cache_dir, _make_writable, manifest_members


def referenced_run_ids() -> set[int]:
    """Return the set of run IDs still referenced by RunOutput records.

    Returns:
        Set of integer run IDs that have at least one RunOutput row.
    """
    from sqlmodel import select

    from ..persistence import RunOutput, get_session

    with get_session() as session:
        rows = session.exec(select(RunOutput.run_id)).all()
        return set(rows)


def referenced_content_hashes() -> set[str]:
    """Return the set of content hashes still referenced by RunOutput records.

    Returns:
        Set of MD5 hex strings from all RunOutput rows with non-null content_hash.
    """
    from sqlmodel import select

    from ..persistence import RunOutput, get_session

    with get_session() as session:
        rows = session.exec(select(RunOutput.content_hash)).all()
        return {h for h in rows if h is not None}


def scan_run_archives(project_dir: Path) -> dict[int, Path]:
    """Scan .runs/ and return a mapping of run_id -> archive directory path.

    Only directories whose names are all digits are included (the standard
    zero-padded format like 00000001).

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict mapping integer run ID to its archive Path.
    """
    runs = layout.artifact_store(project_dir)
    if not runs.exists():
        return {}
    result = {}
    for entry in runs.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            result[int(entry.name)] = entry
    return result


def scan_dvc_cache_entries(project_dir: Path) -> dict[str, Path]:
    """Scan .dvc/cache/files/md5/ and return a mapping of hash -> cache path.

    Reconstructs the full MD5 hex digest from the two-level directory
    layout: {hash[:2]}/{hash[2:]}.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict mapping MD5 hex string to its cache entry Path.
    """
    cache = _cache_dir(project_dir)
    if not cache.exists():
        return {}
    result = {}
    for prefix_dir in cache.iterdir():
        if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
            continue
        for entry in prefix_dir.iterdir():
            md5 = prefix_dir.name + entry.name
            result[md5] = entry
    return result


def prune_run_archives(
    project_dir: Path,
    *,
    all_archives: bool = False,
    dry_run: bool = False,
    exclude_run_ids: set[int] | None = None,
) -> list[Path]:
    """Remove unreferenced run archive directories from .runs/.

    Args:
        project_dir: Root directory of the wfc project.
        all_archives: If True, remove all archives regardless of reference status.
        dry_run: If True, return the list of paths that would be deleted
            without actually deleting them.
        exclude_run_ids: Optional set of run IDs to exclude from pruning
            (e.g., runs with un-archived outputs).

    Returns:
        List of archive paths that were (or would be) deleted.
    """
    project_dir = Path(project_dir).resolve()
    archives = scan_run_archives(project_dir)
    if not archives:
        return []

    if all_archives:
        to_delete = list(archives.values())
    else:
        referenced = referenced_run_ids()
        to_delete = [
            path for rid, path in archives.items() if rid not in referenced
        ]

    # Filter out excluded run IDs (e.g., un-archived outputs)
    if exclude_run_ids:
        excluded_paths = {
            archives[rid] for rid in exclude_run_ids if rid in archives
        }
        to_delete = [p for p in to_delete if p not in excluded_paths]

    if not dry_run:
        for path in to_delete:
            shutil.rmtree(path)

    return to_delete


def prune_dvc_cache(
    project_dir: Path,
    *,
    all_entries: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> list[Path]:
    """Remove DVC cache entries from .dvc/cache/files/md5/.

    By default, only unreferenced entries are removed (entries whose MD5
    hash does not appear in any RunOutput.content_hash record).  When
    ``all_entries`` is True, all cache entries are removed regardless of
    reference status.

    A kept directory entry keeps its members: every ``.dir`` hash in the
    referenced or unpushed set is expanded through its manifest, so a file
    shared by a live directory survives the prune of another.  A pruned
    directory's checkout under ``.runs/checkouts/`` is removed with it.

    Unpushed guard: when a remote is configured and ``force`` is
    False, the prune skips any cache entry whose corresponding RunOutput
    or Sample row has ``pushed_at IS NULL`` (i.e., not yet pushed to the
    remote).  This prevents data loss when the worker is still draining.
    In local-only mode (no remote in ``.dvc/config``) the guard does not
    apply.

    Args:
        project_dir: Root directory of the wfc project.
        all_entries: If True, remove all cache entries regardless of
            reference status.
        dry_run: If True, return the list of paths that would be deleted
            without actually deleting them.
        force: Bypass the ``pushed_at IS NULL`` guard.

    Returns:
        List of cache entry paths that were (or would be) deleted.
    """
    project_dir = Path(project_dir).resolve()
    entries = scan_dvc_cache_entries(project_dir)
    if not entries:
        return []

    # Unpushed guard: collect hashes that are referenced but not yet pushed.
    unpushed_hashes: set[str] = set()
    try:
        from .transport import has_remote_configured
        remote_active = has_remote_configured(project_dir)
    except Exception:
        remote_active = False
    if remote_active and not force:
        from sqlmodel import select as _sel

        from ..persistence import RunOutput as _RO
        from ..persistence import Sample as _S
        from ..persistence import get_session

        with get_session() as session:
            for r in session.exec(
                _sel(_RO).where(col(_RO.pushed_at).is_(None))
            ).all():
                if r.content_hash:
                    unpushed_hashes.add(r.content_hash)
            for s in session.exec(
                _sel(_S).where(col(_S.pushed_at).is_(None))
            ).all():
                if s.content_hash:
                    unpushed_hashes.add(s.content_hash)

    # A directory entry is its manifest plus one object per member; keeping
    # the manifest while deleting a member would leave a partial entry, so
    # a kept directory keeps its members too.
    keep = _with_members(project_dir, unpushed_hashes)
    if not all_entries:
        keep |= _with_members(project_dir, referenced_content_hashes())
    to_delete = [path for md5, path in entries.items() if md5 not in keep]
    deleted_dirs = [md5 for md5, path in entries.items()
                    if md5 not in keep and is_directory_hash(md5)]

    if not dry_run:
        for path in to_delete:
            # Cache entries are read-only (footgun guard); on Windows the
            # read-only attribute blocks unlink/rmtree, so make each
            # selected entry deletable first (best-effort).
            _make_writable(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        # A pruned directory's checkout is a derived copy of its members;
        # it goes with the entry so no reader is served a stale tree.
        for md5 in deleted_dirs:
            stamp = layout.checkout_stamp(project_dir, md5)
            if stamp.exists():
                stamp.unlink()
            stale = layout.checkout_dir(project_dir, md5)
            if stale.exists():
                _make_writable(stale)
                shutil.rmtree(stale)

    return to_delete


def _with_members(project_dir: Path, hashes: set[str]) -> set[str]:
    """Expand each directory hash in a set to include its members' hashes.

    Args:
        project_dir: Root directory of the wfc project.
        hashes: Content hashes; ``.dir`` hashes are expanded through their
            local manifest (a manifest that is not local adds nothing).

    Returns:
        The input hashes plus every member hash their manifests name.
    """
    expanded = set(hashes)
    for h in hashes:
        if is_directory_hash(h):
            expanded |= manifest_members(project_dir, h)
    return expanded


@task(purpose="Remove old run archives and optionally prune DVC local cache entries")
def cache_prune(
    *,
    prune_all: bool = False,
    include_local: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Remove old run archives and optionally DVC local cache entries.

    Args:
        prune_all: Remove all archives regardless of reference status.
        include_local: Also prune .dvc/cache/ entries for unreferenced hashes.
        dry_run: Print what would be deleted without deleting.
        force: Two things, not one. It skips the confirmation prompt, and
            it is forwarded to ``prune_dvc_cache``, where it bypasses the
            unpushed guard: without it a hash whose ``Sample`` or
            ``RunOutput`` row has no ``pushed_at`` is kept out of the
            delete set, because those bytes never reached the archive and
            deleting them is permanent loss. With it they are deleted too.
            It also overrides the unreachable-remote abort.

    Returns:
        0 on success, 1 on user abort.
    """
    from .setup import check_remote_reachable

    project_dir = get_project_root()

    # Safety check: verify DVC remote is reachable before pruning
    if not dry_run:
        reachable, reason = check_remote_reachable(project_dir)
        if not reachable:
            if include_local and not force:
                print(
                    f"ERROR: DVC remote is unreachable ({reason}). "
                    f"With --include-local, pruning will make outputs unrecoverable. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
                return 1
            elif not force:
                print(
                    f"WARNING: DVC remote is unreachable ({reason}). "
                    f"Pruned archives may not be recoverable from remote. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
                return 1

    # Prune guard: refuse to prune runs with un-archived outputs
    from ..persistence import RunOutput as _RunOutput
    _exclude_run_ids: set[int] = set()
    with get_session() as _guard_session:
        unarchived = _guard_session.exec(
            select(_RunOutput).where(col(_RunOutput.content_hash).is_(None))
        ).all()
        if unarchived:
            _exclude_run_ids = {ro.run_id for ro in unarchived}
            print(
                f"WARNING: {len(unarchived)} output(s) from run(s) "
                f"{sorted(_exclude_run_ids)} have not been archived "
                f"(content_hash is NULL). Skipping those runs to prevent "
                f"data loss. Run 'wfc cache archive' first.",
                file=sys.stderr,
            )

    # Compute what would be pruned (always dry_run first for summary)
    archive_paths = prune_run_archives(
        project_dir, all_archives=prune_all, dry_run=True,
        exclude_run_ids=_exclude_run_ids or None,
    )
    cache_paths = (
        prune_dvc_cache(project_dir, all_entries=prune_all, dry_run=True,
                        force=force)
        if include_local else []
    )

    if not archive_paths and not cache_paths:
        print("Nothing to prune.")
        return 0

    # Summary
    print(f"Run archives to remove: {len(archive_paths)}")
    for p in archive_paths:
        print(f"  {p}")
    if include_local:
        print(f"DVC cache entries to remove: {len(cache_paths)}")
        for p in cache_paths:
            print(f"  {p}")

    if dry_run:
        print("(dry run -- no files deleted)")
        return 0

    # Confirmation prompt
    if not force:
        try:
            answer = input("Proceed? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    # Actually prune
    prune_run_archives(
        project_dir, all_archives=prune_all, dry_run=False,
        exclude_run_ids=_exclude_run_ids or None,
    )
    if include_local:
        prune_dvc_cache(project_dir, all_entries=prune_all, dry_run=False,
                        force=force)

    total = len(archive_paths) + len(cache_paths)
    print(f"Pruned {total} item(s).")
    return 0
