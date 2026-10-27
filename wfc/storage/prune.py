"""Prune: remove run archives and cache entries that nothing needs locally.

The scans enumerate ``.wfc/runs/`` and the cache. A run archive is kept while
a ``RunOutput`` row names its run. A cache entry is kept while a record names
it and it is not recorded pushed (:func:`unpushed_record_hashes`): the records
are run output and sample rows, blob rows, every run's and method version's
env fingerprint, every run's pipeline document hash, and the env manifest's
hashes the caller hands in (Storage never reads the manifest itself). Nothing
unpushed is removed, with or without a remote, and no option overrides that.
Extracted method code copies under ``.wfc/runs/code/`` are removed when no
holder has them in use (Storage's in-use records).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

from axiom_annotations import task
from sqlmodel import col, select

from .. import layout
from ..identity import is_directory_hash
from ..persistence import get_session
from ..persistence import project_root as get_project_root
from . import links
from .cache import (
    _cache_dir,
    _make_writable,
    manifest_members,
    remove_view,
    stale_copy_tmps,
)


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


def unpushed_record_hashes(env_hashes: Iterable[str] = ()) -> set[str]:
    """Return every hash a record names that the record does not show pushed.

    A record with a push status of its own (a ``RunOutput``, ``Sample`` or
    ``Blob`` row) is unpushed unless its status is ``pushed``. A record that
    only names a hash (a run's or method version's env fingerprint, a run's
    pipeline document hash, a hash of the env manifest) is unpushed unless
    the blob row for that hash is ``pushed``; a hash with no blob row (an
    env blob an earlier release cached) is never pushed. One unpushed record
    keeps a hash, whatever the others say.

    Args:
        env_hashes: The env manifest's hashes (``env_fingerprint`` and
            ``source_fingerprint`` values), read by the caller.

    Returns:
        The hashes a prune must keep.
    """
    from ..persistence import Blob, MethodVersion, PushStatus, Run, RunOutput, Sample

    pushed = PushStatus.pushed.value
    unpushed: set[str] = set()
    with get_session() as session:
        for model in (RunOutput, Sample):
            for content_hash, status in session.exec(
                select(model.content_hash, model.push_status)
            ).all():
                if content_hash and status != pushed:
                    unpushed.add(content_hash)
        blob_status = dict(session.exec(select(Blob.hash, Blob.push_status)).all())
        named = set(env_hashes)
        for column in (Run.env_fingerprint, MethodVersion.env_fingerprint,
                       Run.pipeline_doc_hash):
            named.update(session.exec(select(column)).all())
    unpushed.update(h for h, status in blob_status.items() if status != pushed)
    unpushed.update(h for h in named if h and blob_status.get(h) != pushed)
    return unpushed


def scan_run_archives(project_dir: Path) -> dict[int, Path]:
    """Scan .wfc/runs/ and return a mapping of run_id -> archive directory path.

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
    """Remove unreferenced run archive directories from .wfc/runs/.

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
            # Collect leaves outputs read-only (the Windows read-only
            # attribute blocks rmtree), and a run's member stamps live
            # outside its archive: both go with it.
            _make_writable(path)
            shutil.rmtree(path)
            stamps = layout.output_stamps_dir(project_dir, int(path.name))
            if stamps.exists():
                shutil.rmtree(stamps, ignore_errors=True)

    return to_delete


def prune_dvc_cache(
    project_dir: Path,
    *,
    dry_run: bool = False,
    env_hashes: Iterable[str] = (),
) -> list[Path]:
    """Remove the DVC cache entries no unpushed record names.

    An entry is kept while a record names it and does not show it pushed
    (:func:`unpushed_record_hashes`); every other entry is removed: one no
    record names, and one every naming record shows pushed (its reader pulls
    it back from the remote). The rule holds with or without a remote, and
    nothing overrides it.

    A hashing copy's tmp file at the cache root that a crash left behind
    (``cache.stale_copy_tmps``: untouched for a day) is removed too; one
    that cannot be removed is named on stderr, left out of the returned
    list, and the prune goes on.

    A kept directory entry keeps its members: every ``.dir`` hash in the
    kept set is expanded through its manifest, so a file shared by a live
    directory survives the prune of another.  A removed entry takes its
    views with it, at the paths wfc made them: a pruned directory's checkout
    under ``.wfc/runs/checkouts/`` with its stamp, and the restored view of
    each sample whose row names the entry (its folder under
    ``.wfc/workspace/samples/`` and its stamp), unless a pipeline holds that
    sample (:func:`_remove_sample_views`). No cache object is chmodded.

    Args:
        project_dir: Root directory of the wfc project.
        dry_run: If True, return the list of paths that would be deleted
            without actually deleting them.
        env_hashes: The env manifest's hashes, read by the caller.

    Returns:
        List of cache entry paths that were (or would be) deleted.
    """
    project_dir = Path(project_dir).resolve()
    # A hashing copy's tmp at the cache root that a crash left behind:
    # only one untouched for a day, never one a live copy is writing.
    tmps = stale_copy_tmps(project_dir)
    if not dry_run:
        # One tmp that cannot be removed (held open, or refused by the OS)
        # is named and left; the prune goes on.
        removed: list[Path] = []
        for tmp in tmps:
            try:
                _make_writable(tmp)
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                print(f"WARNING: could not remove the stale cache tmp {tmp}: "
                      f"{exc}; it is left for the next prune.", file=sys.stderr)
                continue
            removed.append(tmp)
        tmps = removed
    entries = scan_dvc_cache_entries(project_dir)
    if not entries:
        return tmps

    # A directory entry is its manifest plus one object per member; keeping
    # the manifest while deleting a member would leave a partial entry, so
    # a kept directory keeps its members too.
    keep = _with_members(project_dir, unpushed_record_hashes(env_hashes))
    to_delete = [path for md5, path in entries.items() if md5 not in keep]
    deleted_dirs = [md5 for md5, path in entries.items()
                    if md5 not in keep and is_directory_hash(md5)]

    pruned = {md5 for md5 in entries if md5 not in keep}
    if not dry_run:
        # An entry's views go first, then the entry: the views wfc made at
        # their known paths. A restored sample's view is removed only when
        # no pipeline holds it (Storage's in-use records); one that does
        # keeps it, and keeps reading the bytes it shares with the entry.
        _remove_sample_views(project_dir, pruned)
        # A pruned directory's checkout is a view of its members; it goes
        # with the entry so no reader is served a stale tree.
        for md5 in deleted_dirs:
            layout.checkout_stamp(project_dir, md5).unlink(missing_ok=True)
            remove_view(layout.checkout_dir(project_dir, md5))
        for path in to_delete:
            # Never chmod an object: a member hardlinked into a kept
            # directory's checkout shares its inode and read-only attribute.
            if path.is_dir() and not path.is_symlink():
                links.delete_tree(path)  # a malformed legacy tree
            else:
                links.delete_file(path)
        # Each removed object's integrity stamp goes with it.
        for md5 in pruned:
            layout.cache_object_stamp(project_dir, md5).unlink(missing_ok=True)

    return tmps + to_delete


def _remove_sample_views(project_dir: Path, pruned: set[str]) -> list[str]:
    """Remove the restored views of the samples whose cache entry a prune removes.

    A sample's view is its folder under ``.wfc/workspace/samples/`` (the
    restored data and its readiness sentinel) with its stamp. Each is
    removed through :func:`~wfc.storage.in_use.remove_if_unheld`, so a
    pipeline holding the sample keeps it; the next restore of a sample whose
    view is gone pulls the entry again.

    Args:
        project_dir: Root directory of the wfc project.
        pruned: The hashes whose entries the prune removes.

    Returns:
        The names of the samples whose views were removed.
    """
    from ..persistence import InUseKind
    from .in_use import remove_if_unheld
    from .restore import remove_restored_view

    removed: list[str] = []
    for name in _samples_naming(pruned):
        view = layout.sample_dir(project_dir, name)
        if not view.exists() and not layout.sample_stamp(project_dir, name).exists():
            continue

        def remove(name: str = name) -> None:
            # The entry goes too, so no object stamp is kept.
            remove_restored_view(project_dir, name)

        if remove_if_unheld(InUseKind.sample_view, name, remove):
            removed.append(name)
    return removed


def _samples_naming(pruned: set[str]) -> list[str]:
    """The names of the samples whose row names one of the pruned hashes, sorted."""
    from ..persistence import Sample

    if not pruned:
        return []
    with get_session() as session:
        return sorted({name for name, content_hash in session.exec(
            select(Sample.name, Sample.content_hash)).all()
            if content_hash in pruned})


def views_a_cache_prune_removes(project_dir: Path, *,
                                env_hashes: Iterable[str] = ()) -> list[Path]:
    """The views a cache prune would remove with its entries, for the dry run's listing.

    The same selection as :func:`prune_dvc_cache`: each restored sample's
    folder whose row names an entry the prune removes, unless a pipeline
    holds that sample, and each removed directory entry's checkout. Nothing
    is removed and no record is written.

    Args:
        project_dir: Root directory of the wfc project.
        env_hashes: The env manifest's hashes, read by the caller.

    Returns:
        The view paths that exist and would be removed.
    """
    from ..persistence import InUseKind
    from .in_use import held_keys

    project_dir = Path(project_dir).resolve()
    entries = scan_dvc_cache_entries(project_dir)
    keep = _with_members(project_dir, unpushed_record_hashes(env_hashes))
    pruned = {md5 for md5 in entries if md5 not in keep}
    held = set(held_keys(InUseKind.sample_view))
    views = [layout.sample_dir(project_dir, name) for name in _samples_naming(pruned)
             if name not in held and layout.sample_dir(project_dir, name).exists()]
    views += [layout.checkout_dir(project_dir, md5) for md5 in sorted(pruned)
              if is_directory_hash(md5) and layout.checkout_dir(project_dir, md5).exists()]
    return views


def prune_code_copies(project_dir: Path, *, dry_run: bool = False) -> list[Path]:
    """Remove the extracted code copies under ``.wfc/runs/code/`` no holder has in use.

    A claim records the copy it runs as in use before it first looks at it,
    and its record phase releases the record. A copy is derived from git
    (the version's registered tree), so a later run whose copy is gone
    extracts it again. Each copy is removed inside Storage's one
    remove-if-unheld transaction (:func:`~wfc.storage.in_use.remove_if_unheld`),
    so a claim that records the copy after that transaction commits
    extracts it again, and one that recorded it before keeps it. Records
    bound to a run that is no longer ``running`` are released first (their
    run ended without its record phase). Each removed copy's stamp goes
    with it, and a stamp whose copy is already gone is removed too. Names
    starting with ``.`` are an extraction in progress and are left alone.

    Args:
        project_dir: Root directory of the wfc project.
        dry_run: If True, return the copies that would be removed without
            removing them (or releasing any record).

    Returns:
        The copy directories that were (or would be) removed.
    """
    from ..persistence import InUseKind
    from .in_use import held_keys, release_finished_runs, remove_if_unheld

    project_dir = Path(project_dir).resolve()
    root = layout.code_copies_dir(project_dir)
    if not root.is_dir():
        return []
    if not dry_run:
        release_finished_runs()
    in_use = held_keys(InUseKind.code_copy)
    copies = [p for p in root.iterdir()
              if p.is_dir() and not p.name.startswith(".") and p.name not in in_use]
    orphan_stamps = [p for p in root.iterdir()
                     if p.is_file() and p.stem not in in_use
                     and layout.code_copy_stamp(project_dir, p.stem) == p
                     and not layout.code_copy_dir(project_dir, p.stem).is_dir()]
    if dry_run:
        return sorted(copies)

    removed: list[Path] = []
    for copy in sorted(copies):
        def remove(copy: Path = copy) -> None:
            # The stamp first: a copy without a stamp is re-verified before use.
            layout.code_copy_stamp(project_dir, copy.name).unlink(missing_ok=True)
            _make_writable(copy)
            shutil.rmtree(copy)

        if remove_if_unheld(InUseKind.code_copy, copy.name, remove):
            removed.append(copy)
    for stamp in orphan_stamps:
        stamp.unlink(missing_ok=True)
    return removed


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


def _print_code_copies(code_paths: list[Path]) -> None:
    """Print the code copies a prune removes.

    Args:
        code_paths: The copy directories.
    """
    print(f"Unused code copies to remove: {len(code_paths)}")
    for p in code_paths:
        print(f"  {p}")


def _confirmed() -> bool:
    """Ask ``Proceed? [y/N]``; return whether the answer was yes."""
    try:
        answer = input("Proceed? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return False
    if answer.strip().lower() != "y":
        print("Aborted.")
        return False
    return True


def _prune_code_copies_only(project_dir: Path, code_paths: list[Path]) -> None:
    """Remove the unused code copies alone, without asking.

    The path ``cache_prune`` takes when the unreachable-remote guard holds
    back the archives and cache entries. No prompt: a copy is derived from
    git, so removing one loses nothing a later run cannot extract again,
    and the guard's refusal has already ended the interactive prune.

    Args:
        project_dir: Root directory of the wfc project.
        code_paths: The copies a dry run selected.
    """
    removed = prune_code_copies(project_dir)
    print(f"Removed {len(removed)} unused code copy(ies) (a later run "
          f"extracts them again from git); run archives and cache entries "
          f"were not pruned.")


def _print_kept_unpushed(project_dir: Path, env_hashes: Iterable[str]) -> None:
    """Count the local entries the prune keeps unpushed, and say how to push them.

    With a remote configured the line names ``wfc cache push``; with none it
    says a remote is needed first, since ``wfc cache push`` refuses without
    one.

    Args:
        project_dir: Root directory of the wfc project.
        env_hashes: The env manifest's hashes, read by the caller.
    """
    from .transport import has_remote_configured

    local = scan_dvc_cache_entries(project_dir)
    kept = sum(1 for h in unpushed_record_hashes(env_hashes) if h in local)
    if not kept:
        return
    noun = "entry" if kept == 1 else "entries"
    if has_remote_configured(project_dir):
        print(f"Kept {kept} unpushed cache {noun}: run `wfc cache push` to push "
              f"them to the remote, then prune again.")
    else:
        print(f"Kept {kept} unpushed cache {noun}: no remote is configured in "
              f".dvc/config to push them to; a remote is needed first (`wfc "
              f"init` wires the archive), then they can be pushed and pruned.")


@task(purpose="Remove old run archives and unused method code copies, and "
              "optionally the local cache entries no unpushed record names; "
              "count the unpushed entries kept and name wfc cache push, or "
              "with no remote say one is needed first")
def cache_prune(
    *,
    prune_all: bool = False,
    include_local: bool = False,
    dry_run: bool = False,
    force: bool = False,
    env_hashes: Iterable[str] = (),
) -> int:
    """Remove old run archives, unused code copies and optionally DVC local cache entries.

    Extracted method-version code copies under ``.wfc/runs/code/`` that no
    holder has in use are always removed (:func:`prune_code_copies`); a later
    run extracts its copy again from git. The unreachable-remote guard does
    not apply to them: a copy is restored from git, never from the remote.
    When the guard refuses, the copies are still removed (without a prompt:
    nothing is lost), the archives and cache entries are left alone, and
    the exit code is 1.

    With ``include_local``, the cache entries no unpushed record names are
    removed (:func:`prune_dvc_cache`); the unpushed ones are kept whatever
    the options, and the message counts them and names ``wfc cache push``
    (with no remote configured it says a remote is needed first instead).

    Args:
        prune_all: Remove all archives regardless of reference status. The
            cache selection is the same either way: every entry but the
            unpushed ones a record names.
        include_local: Also prune .dvc/cache/ entries no unpushed record names.
        dry_run: Print what would be deleted without deleting.
        force: Skips the confirmation prompt and overrides the
            unreachable-remote abort. It never removes an unpushed entry.
        env_hashes: The env manifest's hashes (``env_fingerprint`` and
            ``source_fingerprint``), read by the caller: Storage never reads
            the manifest.

    Returns:
        0 on success, 1 on user abort or when the unreachable-remote guard
        refused the archives and cache entries.
    """
    from .setup import check_remote_reachable

    project_dir = get_project_root()

    # Code copies come back from git, not the remote: the guard below never
    # holds them back.
    code_paths = prune_code_copies(project_dir, dry_run=True)

    # Safety check: verify DVC remote is reachable before pruning
    if not dry_run:
        reachable, reason = check_remote_reachable(project_dir)
        if not reachable and not force:
            if include_local:
                print(
                    f"ERROR: DVC remote is unreachable ({reason}). "
                    f"With --include-local, pruning will make outputs unrecoverable. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"WARNING: DVC remote is unreachable ({reason}). "
                    f"Pruned archives may not be recoverable from remote. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
            if code_paths:
                _prune_code_copies_only(project_dir, code_paths)
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
    env_hashes = set(env_hashes)
    cache_paths = (
        prune_dvc_cache(project_dir, dry_run=True, env_hashes=env_hashes)
        if include_local else []
    )
    if include_local:
        _print_kept_unpushed(project_dir, env_hashes)

    if not archive_paths and not cache_paths and not code_paths:
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
        view_paths = views_a_cache_prune_removes(project_dir, env_hashes=env_hashes)
        if view_paths:
            print(f"Views removed with them (restored samples, checkouts): "
                  f"{len(view_paths)}")
            for p in view_paths:
                print(f"  {p}")
    if code_paths:
        _print_code_copies(code_paths)

    if dry_run:
        print("(dry run -- no files deleted)")
        return 0

    # Confirmation prompt
    if not force and not _confirmed():
        return 1

    # Actually prune; the count is what each pass removed, so an entry one
    # refused (a stale tmp left in place) is not counted.
    archives_removed = prune_run_archives(
        project_dir, all_archives=prune_all, dry_run=False,
        exclude_run_ids=_exclude_run_ids or None,
    )
    cache_removed = (
        prune_dvc_cache(project_dir, dry_run=False, env_hashes=env_hashes)
        if include_local else []
    )
    code_removed = prune_code_copies(project_dir) if code_paths else []

    total = len(archives_removed) + len(cache_removed) + len(code_removed)
    print(f"Pruned {total} item(s).")
    return 0
