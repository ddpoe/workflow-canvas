"""The content-addressed cache: write an entry, restore it, keep it read-only.

The cache uses DVC's two-level layout directly, with no DVC import and no
``.dvc`` pointer files; the layout is stable across DVC 2.x and 3.x::

    .dvc/cache/files/md5/{hash[:2]}/{hash[2:]}

- :func:`cache_file` stores a file or directory under its content hash.  Move
  mode (the default) consumes the source, such as a run's staging copy; copy
  mode leaves the source in place and serves the archive pass and sample
  registration.  A directory is stored as DVC stores it: one object per file
  at its own md5 address and a ``.dir`` manifest object; no tree is written.
  A new entry is marked read-only, best-effort.
- :func:`checkout` is the one function that turns a directory entry into a
  real directory; :func:`local_path` hands a reader a file's address or a
  directory's checkout under the project tree, and :func:`entry_is_complete`
  says whether the manifest and every member are local.
- :func:`restore_from_cache` copies an entry to a workspace path, and skips
  the copy when the destination already holds the same content.
- A directory tree at an address without the ``.dir`` suffix is a malformed
  cache entry (:class:`MalformedEntryError`), refused by
  :func:`check_entry_shape`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .. import layout
from ..identity import (
    directory_manifest,
    hash_file,
    hash_path,
    is_directory_hash,
    parse_manifest_bytes,
)


def _cache_dir(project_dir: Path) -> Path:
    """Return the DVC cache directory for files: .dvc/cache/files/md5/."""
    return layout.dvc_cache_files_dir(project_dir)


def _cache_path(project_dir: Path, md5: str) -> Path:
    """Return the cache path for a given md5: .dvc/cache/files/md5/{md5[:2]}/{md5[2:]}."""
    return layout.dvc_cache_entry(project_dir, md5)


def _make_read_only(path: Path | str) -> None:
    """Best-effort chmod a cache entry read-only (footgun guard).

    Files become 0444; directory entries are walked with files set to 0444
    and directories to 0555.  On Windows ``os.chmod(p, 0o444)`` sets the
    read-only attribute on files, which blocks accidental overwrites of
    cache entries handed out by path (e.g. ``wfc export --path``).

    Best-effort by design: a chmod failure (root-owned container outputs,
    exotic filesystems) warns to stderr and continues — archiving must
    never fail because protection could not be applied.  This is a footgun
    guard, not a security boundary.

    Args:
        path: Cache entry path (file or directory).
    """
    path = Path(path)
    try:
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                for name in files:
                    os.chmod(os.path.join(root, name), 0o444)
                for name in dirs:
                    os.chmod(os.path.join(root, name), 0o555)
            os.chmod(path, 0o555)
        else:
            os.chmod(path, 0o444)
    except OSError as exc:
        print(
            f"WARNING: could not mark cache entry read-only ({path}): {exc}",
            file=sys.stderr,
        )


def _make_writable(path: Path | str) -> None:
    """Best-effort chmod a path (recursively) back to owner-writable.

    Inverse of :func:`_make_read_only`, used before deleting or replacing
    protected entries/copies (the Windows read-only attribute blocks
    ``unlink``/``rmtree``).  Silent best-effort: failures surface later as
    the actual delete/replace error, which is more informative.
    A symlink is left alone, so replacing a checkout that holds one never
    changes the mode of what it points at.

    Args:
        path: Path (file or directory) to make writable.
    """
    path = Path(path)
    try:
        if path.is_dir():
            os.chmod(path, 0o755)
            for root, dirs, files in os.walk(path):
                for name in dirs:
                    if not os.path.islink(os.path.join(root, name)):
                        os.chmod(os.path.join(root, name), 0o755)
                for name in files:
                    if not os.path.islink(os.path.join(root, name)):
                        os.chmod(os.path.join(root, name), 0o644)
        else:
            os.chmod(path, 0o644)
    except OSError:
        pass


def _consume_staging(path: Path) -> None:
    """Best-effort delete of a staging source after a dedup.

    The content already lives at the cache dest, so the staging copy is a
    duplicate; failing to remove it must not fail the caller.
    """
    try:
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            path.unlink()
    except OSError:
        pass


def _copy_via_tmp(path: Path, dest: Path) -> None:
    """Copy ``path`` to ``dest`` atomically via a process-unique tmp file.

    The tmp name embeds the pid so concurrent writers of the same
    content-addressed ``dest`` never clobber each other's staging file.
    If the final replace fails but ``dest`` exists, another writer already
    landed the identical content (same md5 = same bytes) — that is a
    success, not an error.
    """
    tmp = dest.with_suffix(f".{os.getpid()}.tmp")
    try:
        shutil.copy2(str(path), str(tmp))
        tmp.replace(dest)
    except OSError:
        if dest.exists():
            return
        raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class MalformedEntryError(Exception):
    """A cache entry's shape does not match its hash: a malformed cache entry.

    A directory's hash carries the ``.dir`` suffix and its entry is a
    manifest object; a tree sitting at an unsuffixed address is a malformed
    cache entry. Nothing converts it. The message names the repair, which
    depends on what the entry belongs to: re-registering a sample, or
    re-running the run that produced an output.

    Attributes:
        content_hash: The entry's hash.
    """

    def __init__(self, content_hash: str, repair: str | None = None):
        """Build the refusal.

        Args:
            content_hash: The malformed entry's hash.
            repair: The sentence naming the repair; a generic one when None.
        """
        self.content_hash = content_hash
        repair = repair or (
            "Re-register the sample it belongs to, or re-run the run that "
            "produced it."
        )
        super().__init__(
            f"Cache entry {content_hash} is a malformed cache entry: a directory "
            f"is stored under an address without the '.dir' suffix, which "
            f"wfc cannot push, pull or restore. Nothing is converted. {repair}"
        )


def sample_repair(name: str | None = None) -> str:
    """The repair a malformed sample entry names: re-registration.

    Args:
        name: The sample's name, when the caller knows it.

    Returns:
        The sentence for :class:`MalformedEntryError`.
    """
    target = f"'{name}'" if name else "it belongs to"
    return (
        f"Re-register the sample {target} "
        f"(wfc register-sample --name {name or '<name>'} --source <path>)."
    )


def output_repair(run_id: int | None = None) -> str:
    """The repair a malformed run-output entry names: re-running its run.

    Args:
        run_id: The producing run's id, when the caller knows it.

    Returns:
        The sentence for :class:`MalformedEntryError`.
    """
    target = f"run {run_id}" if run_id is not None else "the run that produced it"
    return f"Re-run {target} to produce the output again."


def check_entry_shape(project_dir: Path | str, md5: str, repair: str | None = None) -> None:
    """Refuse a cache address that holds a directory tree: a malformed cache entry.

    This is the one place that detects the old directory shape; no reader
    accepts both shapes.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.
        repair: The sentence naming the repair, for the message.

    Raises:
        MalformedEntryError: The address holds a directory.
    """
    if _cache_path(Path(project_dir), md5).is_dir():
        raise MalformedEntryError(md5, repair)


def read_manifest(project_dir: Path | str, dir_hash: str) -> list[tuple[str, str]] | None:
    """Read a directory entry's manifest from the local cache.

    Args:
        project_dir: Root directory of the wfc project.
        dir_hash: The directory's ``.dir`` hash.

    Returns:
        The ``(relpath, md5)`` entries, or None when the manifest object is
        not local or does not parse.
    """
    obj = _cache_path(Path(project_dir), dir_hash)
    if not obj.is_file():
        return None
    try:
        return parse_manifest_bytes(obj.read_bytes())
    except (OSError, ValueError):
        return None


def manifest_members(project_dir: Path | str, dir_hash: str) -> set[str]:
    """The member object hashes a local directory manifest names.

    Args:
        project_dir: Root directory of the wfc project.
        dir_hash: The directory's ``.dir`` hash.

    Returns:
        The members' md5s; empty when the manifest is not local.
    """
    entries = read_manifest(project_dir, dir_hash) or []
    return {md5 for _rel, md5 in entries}


def entry_is_complete(project_dir: Path | str, md5: str) -> bool:
    """Whether the local cache holds a whole entry.

    A file entry is complete when its object exists.  A directory entry is
    complete only when its manifest and every member object exist: a
    manifest with a missing member (an interrupted pull) is not local.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.

    Returns:
        True for a complete entry.

    Raises:
        MalformedEntryError: The address holds a directory tree.
    """
    project_dir = Path(project_dir)
    check_entry_shape(project_dir, md5)
    if not is_directory_hash(md5):
        return _cache_path(project_dir, md5).is_file()
    entries = read_manifest(project_dir, md5)
    if entries is None:
        return False
    return all(_cache_path(project_dir, m).is_file() for _rel, m in entries)


def entry_is_local(project_dir: Path | str, md5: str) -> bool:
    """Whether the local cache holds a whole, usable entry; never raises.

    :func:`entry_is_complete` as a presence probe: a malformed entry (a
    directory tree at an unsuffixed address) is not a usable local copy, so
    it counts as not local. The readers refuse it loudly on their own.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.

    Returns:
        True for a complete, well-formed entry.
    """
    try:
        return entry_is_complete(project_dir, md5)
    except MalformedEntryError:
        return False


def _cache_directory(path: Path, dir_hash: str, project_dir: Path, dest: Path) -> None:
    """Store a directory as DVC does: one object per file, then the manifest.

    Members land at their own md5 addresses, so a file shared by two
    directories is stored once.  The manifest object is written last, so a
    visible manifest means every member landed.  No tree is written.

    Args:
        path: The source directory (left in place).
        dir_hash: Its ``.dir`` hash, as the caller computed it.
        project_dir: Root directory of the wfc project.
        dest: The manifest's cache address.

    Raises:
        ValueError: The directory's content no longer matches ``dir_hash``.
    """
    manifest = directory_manifest(path)
    if manifest.hash != dir_hash:
        raise ValueError(
            f"Directory {path} changed while it was being cached "
            f"(expected {dir_hash}, now {manifest.hash})"
        )
    for rel, md5 in manifest.entries:
        member_dest = _cache_path(project_dir, md5)
        if member_dest.exists():
            continue
        member_dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_via_tmp(path / Path(*rel.split("/")), member_dest)
        _make_read_only(member_dest)
    tmp = dest.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_bytes(manifest.manifest_bytes)
        tmp.replace(dest)
    except OSError:
        if not dest.exists():
            raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    _make_read_only(dest)


def _checkout_files(dest: Path) -> dict[str, Path]:
    """The files under a checkout, keyed by their POSIX relative path."""
    return {
        p.relative_to(dest).as_posix(): p
        for p in dest.rglob("*") if p.is_file()
    }


def _holds_symlink(dest: Path) -> bool:
    """Whether any entry under a checkout, file or directory, is a symlink.

    A checkout only ever holds copies, so a link is a hand edit.  The walk
    never follows a link, so a directory link is seen as itself even where
    ``rglob`` would skip it.
    """
    for root, dirs, files in os.walk(dest):
        for name in dirs + files:
            if os.path.islink(os.path.join(root, name)):
                return True
    return False


def _checkout_verifies(dest: Path, entries: list[tuple[str, str]]) -> bool:
    """Whether a destination directory holds exactly the manifest's files.

    The full check: every member's bytes are hashed, and a checkout that
    holds a symlink anywhere fails.  It runs when a checkout is built or
    when there is no trustworthy stamp.
    """
    if not dest.is_dir() or _holds_symlink(dest):
        return False
    want = dict(entries)
    have = _checkout_files(dest)
    if set(have) != set(want):
        return False
    return all(hash_file(have[rel]) == md5 for rel, md5 in want.items())


def _stamp_record(md5: str, dest: Path) -> dict:
    """What a stamp records: the ``.dir`` hash and each file's size and mtime."""
    files = {}
    for rel, p in _checkout_files(dest).items():
        st = p.stat()
        files[rel] = [st.st_size, st.st_mtime_ns]
    return {"hash": md5, "files": files}


def _stamp_matches(stamp: Path, md5: str, dest: Path,
                   entries: list[tuple[str, str]]) -> bool:
    """The cheap check: whether the stamp vouches for the checkout as it is now.

    The stamp must be for this hash, its names, sizes and mtimes must
    match the checkout, and the checkout must hold no symlink. No member's
    bytes are read.

    Args:
        stamp: The stamp file.
        md5: The directory's ``.dir`` hash.
        dest: The checkout.
        entries: The manifest's ``(relpath, md5)`` entries.

    Returns:
        True when the stamp vouches for the checkout.
    """
    if not (stamp.is_file() and dest.is_dir()) or _holds_symlink(dest):
        return False
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8"))
        if recorded.get("hash") != md5:
            return False
        files = recorded["files"]
        if set(files) != {rel for rel, _m in entries}:
            return False
        return _stamp_record(md5, dest)["files"] == files
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _drop_stamp(stamp: Path | None) -> None:
    """Remove a stamp before its checkout is touched, so a crash leaves none."""
    if stamp is None:
        return
    try:
        stamp.unlink()
    except FileNotFoundError:
        pass


def _write_stamp(stamp: Path, md5: str, dest: Path) -> None:
    """Write a stamp for a verified checkout, atomically (tmp + replace)."""
    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_name(f"{stamp.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(_stamp_record(md5, dest)), encoding="utf-8")
        tmp.replace(stamp)
    except OSError as exc:
        # A missing stamp only costs the next read a full verify.
        print(f"WARNING: could not stamp checkout {dest}: {exc}", file=sys.stderr)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def checkout(md5: str, dest: Path | str, project_dir: Path | str,
             *, read_only: bool = True, stamp: Path | str | None = None) -> bool:
    """Turn a directory entry into a real directory at ``dest``: the one checkout.

    Every reader of a directory entry goes through this.  It verifies that
    the manifest and every member are local, is idempotent when ``dest``
    already holds exactly the entry's files, and replaces a stale ``dest``.
    The copy is built beside ``dest`` and renamed into place.

    With a ``stamp`` (a read-only checkout that readers hit repeatedly), a
    verified checkout is stamped with its hash and each file's size and
    mtime.  A later call whose stamp still matches returns without reading
    any member's bytes; a missing, extra, resized or re-timed file fails the
    cheap check and the checkout is re-verified or rebuilt.  Without a
    stamp, every call hashes the members.

    Args:
        md5: The directory's ``.dir`` hash.
        dest: The directory to create.
        project_dir: Root directory of the wfc project.
        read_only: Mark the checkout read-only (resolver checkouts); a
            sample restore passes False.
        stamp: Where the checkout's stamp lives; only honoured for a
            read-only checkout.

    Returns:
        True when ``dest`` holds the entry; False when the entry is not
        complete in the local cache.

    Raises:
        MalformedEntryError: The address holds a directory tree.
    """
    project_dir = Path(project_dir)
    dest = Path(dest)
    stamp = Path(stamp) if (stamp is not None and read_only) else None
    if not entry_is_complete(project_dir, md5):
        return False
    entries = read_manifest(project_dir, md5) or []
    if stamp is not None and _stamp_matches(stamp, md5, dest, entries):
        return True
    _drop_stamp(stamp)
    if _checkout_verifies(dest, entries):
        if stamp is not None:
            _write_stamp(stamp, md5, dest)
        return True
    staging = dest.with_name(f"{dest.name}.{os.getpid()}.checkout")
    if staging.exists():
        _make_writable(staging)
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel, member in entries:
        target = staging / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_cache_path(project_dir, member), target)
    if dest.exists():
        _make_writable(dest)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    try:
        staging.replace(dest)
    except OSError:
        # A concurrent checkout of the same entry landed first.
        _make_writable(staging)
        shutil.rmtree(staging, ignore_errors=True)
        if not _checkout_verifies(dest, entries):
            raise
    if read_only:
        _make_read_only(dest)
    if stamp is not None:
        _write_stamp(stamp, md5, dest)
    return True


def local_path(project_dir: Path | str, md5: str) -> Path | None:
    """The path a reader reads for a complete local entry.

    A file entry is read at its cache address.  A directory entry is read
    from its checkout under the project tree (``layout.checkout_dir``),
    built and stamped on first use; a later read of an intact checkout is
    vouched for by its stamp (``layout.checkout_stamp``) without re-hashing.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.

    Returns:
        The path, or None when the entry is not complete locally.

    Raises:
        MalformedEntryError: The address holds a directory tree.
    """
    project_dir = Path(project_dir)
    if not entry_is_complete(project_dir, md5):
        return None
    if not is_directory_hash(md5):
        return _cache_path(project_dir, md5)
    dest = layout.checkout_dir(project_dir, md5)
    stamp = layout.checkout_stamp(project_dir, md5)
    return dest if checkout(md5, dest, project_dir, stamp=stamp) else None


def cache_file(
    path: Path | str,
    md5: str,
    project_dir: Path | str,
    move: bool = True,
) -> Path:
    """Move (or copy) a file into the DVC content-addressed cache.

    Cache is authoritative. Pipeline outputs are produced into a
    staging area and moved into the cache; the staging copy is consumed.
    Callers that pass a source they do not own (the archive pass, sample
    registration) set ``move=False`` to preserve it.

    Move strategy:
    - Try ``os.rename`` first (atomic, fast on same volume).
    - On cross-volume ``OSError``, fall back to ``shutil.copy2`` + ``unlink``.
    - A directory is stored as its members plus a ``.dir`` manifest
      (:func:`_cache_directory`); move mode then consumes the source tree.

    Idempotency:
    - If ``dest`` already exists, this is a dedup. Unlink the staging
      duplicate (when ``move=True``) and return ``dest`` without overwriting.
    - The same principle covers concurrent writers: the cache is
      content-addressed (same md5 = same bytes), so on ANY failure path
      where ``dest`` turns out to exist, another writer already landed
      this content and the call succeeds. On Windows the loser's
      ``os.rename`` onto a just-created dest raises (POSIX silently
      overwrites), so without this guard parallel jobs sharing an env
      blob crash mid-pipeline. An existing ``dest`` is never deleted,
      truncated, or overwritten.

    Args:
        path: Source file/directory path.
        md5: Pre-computed MD5 hex digest of the path.
        project_dir: Root directory of the wfc project.
        move: If True (default), consume the source. If False, copy and
            leave source intact.

    Returns:
        The cache path where the file was stored.
    """
    project_dir = Path(project_dir).resolve()
    path = Path(path)
    dest = _cache_path(project_dir, md5)

    if dest.exists() and (not path.is_dir() or entry_is_complete(project_dir, md5)):
        # Content-addressed idempotency: dedup. Unlink the staging
        # duplicate so callers don't leave orphan staging copies behind.
        if move and path.exists() and path != dest:
            _consume_staging(path)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    if path.is_dir():
        _cache_directory(path, md5, project_dir, dest)
        if move and path.exists():
            _consume_staging(path)
        return dest

    # File path
    if move:
        try:
            # os.rename is atomic on same volume; OSError on cross-volume.
            os.rename(str(path), str(dest))
        except OSError:
            if dest.exists():
                # Concurrent writer won the rename race: dedup, consume
                # the staging duplicate. (Not cross-volume — on Windows,
                # rename onto an existing file raises.)
                _consume_staging(path)
                return dest
            # Genuine cross-volume fallback: copy then unlink.
            _copy_via_tmp(path, dest)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    else:
        # Copy mode: atomic write via tmp+rename, preserve source.
        _copy_via_tmp(path, dest)

    _make_read_only(dest)
    return dest


def restore_from_cache(
    md5: str, dest: Path | str, project_dir: Path | str
) -> bool:
    """Restore a file from the DVC cache to a workspace path.

    Checkout-like behavior: if the destination already exists and its
    content hash matches ``md5``, the restore is skipped (idempotent).
    If the destination exists but the hash mismatches (stale or corrupted),
    the file is replaced from cache.

    Args:
        md5: Content hash of the file to restore.
        dest: Destination path in the workspace.
        project_dir: Root directory of the wfc project.

    Returns:
        True if restore succeeded (or was skipped because dest is valid),
        False if the cache entry is missing.
    """
    project_dir = Path(project_dir).resolve()
    dest = Path(dest)
    src = _cache_path(project_dir, md5)

    if is_directory_hash(md5):
        # A directory entry is a manifest plus members: the one checkout
        # builds it (writable -- this is a workspace copy, not a cache view).
        return checkout(md5, dest, project_dir, read_only=False)
    check_entry_shape(project_dir, md5)

    if not src.exists():
        return False

    # If dest already exists, verify its content hash
    if dest.exists():
        existing_hash = hash_path(dest)
        if existing_hash == md5:
            return True  # Already valid — skip restore
        # Stale/corrupted — remove before replacing. Workspace copies
        # restored from a protected cache entry inherit its read-only bits
        # (copy2 preserves mode), and on Windows the read-only
        # attribute blocks unlink — make the stale dest writable first.
        _make_writable(dest)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)
    # check_entry_shape refused a tree at this address, so src is a file.
    shutil.copy2(str(src), str(dest))
    return True
