"""The content-addressed cache: write an entry, restore it, keep it read-only.

The cache uses DVC's two-level layout directly, with no DVC import and no
``.dvc`` pointer files; the layout is stable across DVC 2.x and 3.x::

    .dvc/cache/files/md5/{hash[:2]}/{hash[2:]}

- :func:`cache_file` stores a file or directory under its content hash.  Move
  mode (the default) consumes the source, such as a run's staging copy; copy
  mode leaves the source in place and serves the archive pass and sample
  registration; :func:`store_copy` is copy mode for a caller that holds no
  hash yet, returning the md5 of the bytes copied.  A directory is stored
  as DVC stores it: one object per file
  at its own md5 address and a ``.dir`` manifest object; no tree is written.
  Every copy is a hashing copy: the address is the md5 of the bytes
  written, a caller's known md5 must match, and an address already holding
  changed bytes is replaced by the verified copy.  A new object is marked
  read-only, best-effort, then stamped (``layout.cache_object_stamp``).
- :func:`changed_objects` and :func:`entry_changes` ask Storage's one
  integrity check which of an entry's objects (or its checkout) changed;
  :func:`set_aside` takes changed objects out of their addresses so a pull
  brings the genuine bytes again.
- :func:`checkout` is the one function that turns a directory entry into a
  real directory; :func:`local_path` hands a reader a file's address or a
  directory's checkout under the project tree, and :func:`entry_is_complete`
  says whether the manifest and every member are local.
- :func:`restore_from_cache` copies an entry to a workspace path, read-only,
  with the hashing copy (a file) or the checkout (a directory), stamps it
  only after the copy matches, and skips the copy when the destination
  already holds the same content.
- A directory tree at an address without the ``.dir`` suffix is a malformed
  cache entry (:class:`MalformedEntryError`), refused by
  :func:`check_entry_shape`.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import Step, task

from .. import layout
from ..identity import (
    DIR_HASH_SUFFIX,
    content_hasher,
    directory_listing,
    hash_file,
    hash_path,
    is_directory_hash,
    manifest_bytes,
    manifest_hash,
    parse_manifest_bytes,
)
from . import links
from .integrity import (
    CHANGED,
    INTACT,
    check_entry,
    entry_record,
    inputs_setting,
    read_stamp,
    stamps_batched,
    write_stamp,
)

#: Bytes per read of the hashing copy.
_COPY_CHUNK = 1 << 20


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

    A chmod moves a file's change time, one of the stat fields an
    integrity stamp records on Linux, macOS and Windows (NTFS's or ReFS's
    ChangeTime), so a caller chmods first and stamps after; a chmod over a
    stamped entry leaves its stamp stale, which costs the next read a
    re-hash.

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


def _object_hash(md5: str) -> str:
    """The md5 of the bytes stored at an address: a ``.dir`` hash without its suffix."""
    return md5[: -len(DIR_HASH_SUFFIX)] if is_directory_hash(md5) else md5


def _setting(project_dir: Path, setting: str | None) -> str:
    """The ``[integrity] inputs`` setting: the caller's, else the project's."""
    return setting if setting is not None else inputs_setting(project_dir)


def _check_object(project_dir: Path, md5: str, setting: str):
    """The one check, for one cache object against its own address and stamp."""
    return check_entry(
        _cache_path(project_dir, md5),
        recorded_hash=_object_hash(md5),
        stamp=layout.cache_object_stamp(project_dir, md5),
        setting=setting,
    )


def protect_entry(path: Path | str) -> bool:
    """Mark a cache object read-only, only when it still carries a write bit.

    A chmod moves the ctime an integrity stamp records (and, on Windows,
    NTFS's change time), so chmodding an object that is already protected
    would leave its stamp stale and cost its next read a re-hash.

    Args:
        path: A cache object (a file at its address).

    Returns:
        True when the object was chmodded.
    """
    path = Path(path)
    try:
        if not path.is_file() or not path.stat().st_mode & 0o222:
            return False
    except OSError:
        return False
    _make_read_only(path)
    return True


def _remove_quietly(path: Path) -> None:
    """Delete a file this module wrote and no longer needs, best-effort.

    A file that cannot be removed is named on stderr: a tmp left at the
    cache root is swept later by ``wfc cache prune``.
    """
    try:
        if path.exists():
            _make_writable(path)
            path.unlink()
    except OSError as exc:
        print(f"WARNING: could not remove {path}: {exc}", file=sys.stderr)


#: The name prefixes of the hashing copy's tmp files at the cache root.
COPY_TMP_PREFIXES = (".tmp-copy-", ".tmp-manifest-")
#: A tmp file at the cache root untouched this long is a crash's leftover:
#: a live copy writes its tmp as it goes and renames it within moments.
STALE_COPY_TMP_SECONDS = 24 * 60 * 60


def stale_copy_tmps(project_dir: Path | str, *, now: float | None = None) -> list[Path]:
    """The hashing copy's tmp files at the cache root that a crash left behind.

    A tmp counts as left behind when its mtime is older than
    :data:`STALE_COPY_TMP_SECONDS`: a copy in progress writes its tmp chunk
    by chunk (each write moves the mtime) and renames or removes it as soon
    as the copy ends, so a live one is never that old.

    Args:
        project_dir: Root directory of the wfc project.
        now: The time to measure age from (``time.time()`` when None).

    Returns:
        The stale tmp files, sorted.
    """
    import time

    root = _cache_dir(Path(project_dir))
    if not root.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - STALE_COPY_TMP_SECONDS
    stale: list[Path] = []
    for p in root.iterdir():
        if not p.name.startswith(COPY_TMP_PREFIXES) or not p.is_file():
            continue
        try:
            if p.stat().st_mtime < cutoff:
                stale.append(p)
        except OSError:
            continue
    return sorted(stale)


def _hashing_copy(src: Path, target: Path) -> str:
    """Copy a file's bytes to ``target``, hashing exactly the bytes written.

    One read of the source: each chunk is written and fed to Identity's
    incremental hasher, so the hash is that of the copy, not of a separate
    read. A failed read or write raises, so a partial copy never gets a
    hash.

    Args:
        src: The file to copy.
        target: The file to write (created or truncated).

    Returns:
        The md5 of the bytes written.
    """
    hasher = content_hasher()
    with open(src, "rb") as fin, open(target, "wb") as fout:
        while True:
            chunk = fin.read(_COPY_CHUNK)
            if not chunk:
                break
            fout.write(chunk)
            hasher.update(chunk)
    return hasher.hexdigest()


def _place_verified(tmp: Path, md5: str, project_dir: Path, setting: str) -> Path:
    """Put a verified tmp file at its address, read-only, then stamp it.

    An address that already holds the object is checked with the one
    check: intact, the tmp is not needed; changed, the verified tmp
    replaces it, since wfc holds the genuine bytes in hand. The stamp is
    written after the rename and the chmod (a chmod moves the ctime), from
    the object as it now sits.

    Args:
        tmp: The verified copy (its md5 is ``md5``'s object hash).
        md5: The address's hash (a directory's keeps ``.dir``).
        project_dir: Root directory of the wfc project.
        setting: ``[integrity] inputs``.

    Returns:
        The address.
    """
    dest = _cache_path(project_dir, md5)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_dir():
        # A malformed tree at the address: refused by name when it is read.
        return dest
    if dest.exists() and _check_object(project_dir, md5, setting).intact:
        return dest
    try:
        # Renamed over a changed object without chmodding it: a view linked
        # to it keeps its own (changed) inode and read-only attribute.
        links.replace_over(tmp, dest)
    except OSError:
        # A concurrent writer landed the same content first.
        if not (dest.exists() and _check_object(project_dir, md5, setting).intact):
            raise
        return dest
    _make_read_only(dest)
    write_stamp(layout.cache_object_stamp(project_dir, md5), entry_record(dest),
                _object_hash(md5))
    return dest


class ChangedSourceError(ValueError):
    """The bytes copied into the cache are not the hash the caller holds.

    The source changed after it was hashed (or after collect hashed it).
    Nothing is stored at an address for it, and no hash is recorded.
    """


def _copy_verified(src: Path, md5: str | None, project_dir: Path, setting: str) -> str:
    """Copy one file into the cache with the hashing copy: the address is its md5.

    The tmp file sits at the cache root, outside the two-level address
    space, so a scan of entries never sees it.

    Args:
        src: The file to copy (left in place).
        md5: The hash the caller knows, which the bytes copied must have;
            None when the caller knows none.
        project_dir: Root directory of the wfc project.
        setting: ``[integrity] inputs``.

    Returns:
        The md5 of the bytes copied, which is the object's address.

    Raises:
        ChangedSourceError: The bytes copied do not have the caller's hash
            (the source changed after it was hashed).
    """
    root = _cache_dir(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=root, prefix=".tmp-copy-", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        actual = _hashing_copy(src, tmp)
        if md5 is not None and actual != md5:
            raise ChangedSourceError(
                f"{src} changed while it was being cached (expected {md5}, "
                f"copied {actual}); nothing was stored"
            )
        _place_verified(tmp, actual, project_dir, setting)
        return actual
    finally:
        _remove_quietly(tmp)


class ChangedEntryError(Exception):
    """A cache entry's local bytes no longer match its address.

    Raised by the checkout when a member object's bytes, copied with the
    hashing copy, are not the bytes its manifest names. Nothing is placed
    or stamped. Storage's one reader pulls the entry again when it is
    recorded pushed, and refuses it otherwise.

    Attributes:
        content_hash: The entry's hash.
        objects: The hashes of the objects that changed.
    """

    def __init__(self, content_hash: str, objects: list[str]):
        """Build the refusal.

        Args:
            content_hash: The entry's hash.
            objects: The changed objects' hashes.
        """
        self.content_hash = content_hash
        self.objects = list(objects)
        super().__init__(
            f"Cache entry {content_hash} changed in the local cache: "
            f"{changed_phrase(self.objects)}"
        )


def changed_phrase(objects: list[str]) -> str:
    """Name changed cache objects in a refusal.

    Args:
        objects: The changed objects' hashes.

    Returns:
        A phrase such as ``object 1a2b... no longer matches its address``.
    """
    if len(objects) == 1:
        return f"object {objects[0]} no longer matches its address"
    return f"objects {', '.join(objects)} no longer match their addresses"


#: What a malformed cache entry is, in the words every message about one uses.
MALFORMED_SHAPE = ("a directory is stored under an address without the '.dir' "
                   "suffix, which wfc cannot push, pull or restore")


class ResolveOutputError(Exception):
    """Base error for a read of recorded bytes: correct bytes or a clear error.

    Every refusal a reader of a run's outputs can meet is one of these, so a
    caller that catches this one class reports Storage's own message. It is
    defined here rather than in ``resolve`` because ``resolve`` imports this
    module and :class:`MalformedEntryError` is one of them;
    ``wfc.storage.resolve`` and the package export it under the same name.
    """


class MalformedEntryError(ResolveOutputError):
    """A cache entry's shape does not match its hash: a malformed cache entry.

    A directory's hash carries the ``.dir`` suffix and its entry is a
    manifest object; a tree sitting at an unsuffixed address is a malformed
    cache entry. Nothing converts it. The message names the repair, which
    depends on what the entry belongs to: re-registering a sample, or
    re-running the run that produced an output. It is a
    :class:`ResolveOutputError`, so every reader of an output refuses it with
    this message.

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
            f"Cache entry {content_hash} is a malformed cache entry: "
            f"{MALFORMED_SHAPE}. Nothing is converted. {repair}"
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


def blob_repair(kind: str | None = None) -> str:
    """The repair a malformed blob entry names: what its kind says recreates it.

    Args:
        kind: The blob row's kind (``pipeline-document``, ``env-fingerprint``),
            when the caller knows it.

    Returns:
        The sentence for :class:`MalformedEntryError`.
    """
    if kind == "pipeline-document":
        return "Launch the pipeline again to store its document again."
    if kind == "env-fingerprint":
        return "Register the environment again to store its content blob again."
    return "Recreate the blob with the command that stored it."


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


def changed_objects(project_dir: Path | str, md5: str,
                    setting: str | None = None) -> list[str]:
    """The local objects of an entry that are not what their addresses say.

    Each object present locally goes through the one check against its own
    address and stamp: a file entry's object, or a directory's manifest and
    then every member its manifest names. A changed manifest is reported
    alone, since the members it names cannot be trusted. An object that is
    not local is not reported (completeness is :func:`entry_is_complete`'s
    question).

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        The changed objects' hashes; empty when every local object is intact.
    """
    project_dir = Path(project_dir)
    setting = _setting(project_dir, setting)
    if _check_object(project_dir, md5, setting).status == CHANGED:
        return [md5]
    if not is_directory_hash(md5):
        return []
    members = dict.fromkeys(m for _rel, m in (read_manifest(project_dir, md5) or []))
    return [m for m in members if _check_object(project_dir, m, setting).status == CHANGED]


def entry_changes(project_dir: Path | str, md5: str,
                  setting: str | None = None) -> list[str]:
    """What a read of a complete local entry would find changed.

    A file entry is read at its object. A directory entry is read from its
    checkout: an intact checkout (by its stamp, or by a re-hash) answers
    for the read, and otherwise the read rebuilds the checkout from the
    objects, so they are checked. A presence probe's answer: it builds and
    pulls nothing.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash (complete in the local cache).
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        The changed objects' hashes; empty when the read would be served.
    """
    project_dir = Path(project_dir)
    setting = _setting(project_dir, setting)
    if is_directory_hash(md5):
        verdict = check_entry(
            layout.checkout_dir(project_dir, md5),
            recorded_hash=md5,
            stamp=layout.checkout_stamp(project_dir, md5),
            setting=setting,
        )
        if verdict.intact:
            return []
    return changed_objects(project_dir, md5, setting)


def set_aside(project_dir: Path | str, hashes: list[str]) -> None:
    """Take changed objects out of their addresses before they are pulled again.

    DVC's fetch skips an object that is present locally, so a changed one
    must leave its address for a pull to bring the genuine bytes. Its stamp
    goes with it, so the pulled object is hashed in full before it is
    stamped or served.

    Args:
        project_dir: Root directory of the wfc project.
        hashes: The changed objects' hashes.
    """
    project_dir = Path(project_dir)
    for h in hashes:
        layout.cache_object_stamp(project_dir, h).unlink(missing_ok=True)
        obj = _cache_path(project_dir, h)
        if obj.is_file():
            # Never chmodded: a view still linked to it keeps its attribute.
            links.delete_file(obj)


def _cache_directory(path: Path, dir_hash: str | None, project_dir: Path,
                     setting: str) -> str:
    """Store a directory as DVC does: one object per file, then the manifest.

    Each member is copied with the hashing copy and lands at the md5 of the
    bytes copied, so a file shared by two directories is stored once. The
    manifest is built from those streamed hashes (no separate hashing
    read) and written last, so a visible manifest means every member
    landed. No tree is written.

    Args:
        path: The source directory (left in place).
        dir_hash: The ``.dir`` hash the caller knows, which the manifest
            must have; None when the caller knows none.
        project_dir: Root directory of the wfc project.
        setting: ``[integrity] inputs``.

    Returns:
        The directory's ``.dir`` hash: its manifest's.

    Raises:
        ChangedSourceError: The bytes copied are not the caller's
            ``dir_hash``.
        DirectoryContentError: The directory holds content wfc refuses.
    """
    listing = directory_listing(path)
    with stamps_batched(project_dir):
        entries = [
            (rel, _copy_verified(path / Path(*rel.split("/")), None, project_dir, setting))
            for rel, _st in listing.members
        ]
    data = manifest_bytes(entries)
    actual = manifest_hash(data)
    if dir_hash is not None and actual != dir_hash:
        raise ChangedSourceError(
            f"Directory {path} changed while it was being cached "
            f"(expected {dir_hash}, now {actual})"
        )
    root = _cache_dir(project_dir)
    fd, name = tempfile.mkstemp(dir=root, prefix=".tmp-manifest-", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _place_verified(tmp, actual, project_dir, setting)
    finally:
        _remove_quietly(tmp)
    return actual


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
    holds a symlink anywhere fails.  It runs for an unstamped checkout.
    """
    if not dest.is_dir() or _holds_symlink(dest):
        return False
    want = dict(entries)
    have = _checkout_files(dest)
    if set(have) != set(want):
        return False
    return all(hash_file(have[rel]) == md5 for rel, md5 in want.items())


def _drop_stamp(stamp: Path | None) -> None:
    """Remove a stamp before its checkout is touched, so a crash leaves none."""
    if stamp is None:
        return
    try:
        stamp.unlink()
    except FileNotFoundError:
        pass


def open_dirs(*dirs: Path | str) -> None:
    """Give the owner write on existing directories that lack it.

    Directories only: a directory is never shared with a cache object, so
    this cannot make a cache object writable. The caller protects them
    again when it is done (:func:`protect_dirs`).

    Args:
        dirs: The directories (absent ones are skipped).
    """
    for d in dict.fromkeys(Path(d) for d in dirs):
        try:
            mode = stat.S_IMODE(os.stat(d).st_mode)
            if not mode & stat.S_IWUSR:
                os.chmod(d, mode | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            continue


def _open_tree(path: Path) -> None:
    """Give the owner write on a directory and every directory under it, never a file."""
    open_dirs(path)
    for root, dirs, _files in os.walk(path):
        open_dirs(*(os.path.join(root, d) for d in dirs
                    if not os.path.islink(os.path.join(root, d))))


def protect_dirs(path: Path | str) -> None:
    """Make a directory and every directory under it read-only, never a file.

    The files of a view are read-only already (a hardlink shares the cache
    object's protection; a reflink or a copy is protected before it is
    renamed in), and a chmod of one would move the change time a cache
    object's stamp records. Best-effort, as :func:`_make_read_only` is. On
    Windows the read-only attribute does not stop a write into a
    directory.

    Args:
        path: The directory (a file or an absent path is left alone).
    """
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        return
    try:
        for root, dirs, _files in os.walk(path):
            for name in dirs:
                sub = os.path.join(root, name)
                if not os.path.islink(sub):
                    os.chmod(sub, 0o555)
        os.chmod(path, 0o555)
    except OSError as exc:
        print(f"WARNING: could not mark {path} read-only: {exc}", file=sys.stderr)


def _only_change_time_moved(before: dict, after: dict) -> bool:
    """Whether two stat records of one file differ in its change time alone.

    A link made or removed, or a rename of one, moves the change time a
    file's inode shares with every link (``st_ctime`` on POSIX, NTFS's
    ChangeTime on Windows); size, mtime and the file index stay. A write
    moves the mtime, so it is never mistaken for one.
    """
    if before == after:
        return False
    b, a = before.get("file"), after.get("file")
    if not isinstance(b, dict) or not isinstance(a, dict):
        return False
    return ({k: v for k, v in b.items() if k != "ctime_ns"}
            == {k: v for k, v in a.items() if k != "ctime_ns"}
            and ("ctime_ns" in b) == ("ctime_ns" in a))


def _is_view_of(dest: Path, src: Path) -> bool:
    """Whether ``dest`` is a hardlink of ``src`` already (the same inode)."""
    try:
        return (not dest.is_symlink() and dest.is_file()
                and os.path.samefile(dest, src))
    except OSError:
        return False


def _discard(tmp: Path) -> None:
    """Remove a view under construction, best-effort."""
    try:
        links.delete_file(tmp)
    except OSError as exc:
        print(f"WARNING: could not remove {tmp}: {exc}", file=sys.stderr)


def _build_view(src: Path, tmp: Path, md5: str) -> str:
    """Make ``tmp`` a view of ``src``: a reflink, else a hardlink, else a copy.

    A tier that raises ``OSError`` falls to the next. A reflink or a copy is
    an inode of its own and is made read-only here; a hardlink shares the
    cache object's protection. The copy is the hashing copy, compared with
    the object's hash.

    Returns:
        The tier made.

    Raises:
        ChangedEntryError: The bytes copied are not the object's.
    """
    try:
        links.reflink(src, tmp)
        _make_read_only(tmp)
        return links.TIER_REFLINK
    except OSError:
        _discard(tmp)
    try:
        links.hardlink(src, tmp)
        return links.TIER_HARDLINK
    except OSError:
        _discard(tmp)
    if _hashing_copy(src, tmp) != _object_hash(md5):
        raise ChangedEntryError(md5, [md5])
    _make_read_only(tmp)
    return links.TIER_COPY


def _rename_over(tmp: Path, dest: Path) -> None:
    """Rename a built view over its destination; never delete a file first.

    A directory where the file belongs cannot be renamed over and is the
    one thing removed before the rename.
    """
    if dest.is_dir() and not dest.is_symlink():
        links.delete_tree(dest)
    links.replace_over(tmp, dest)


@task(purpose="The one view: make a cache object readable at a destination as a "
              "reflink, a hardlink or a copy, built beside it and renamed over "
              "it, read-only, keeping the object's stamp when only its change "
              "time moved",
      inputs="the object's hash; the destination and the directory to build in; "
             "the object, its stamp and [integrity] inputs",
      outputs="the tier made; the destination a read-only view of the object; "
              "the object's stamp kept current")
def make_view(md5: str, dest: Path | str, project_dir: Path | str, *,
              setting: str | None = None,
              build_in: Path | str | None = None) -> str:
    """Make ``dest`` a read-only view of one cache object.

    The tiers are tried in order, each falling back on ``OSError``: a
    reflink (Linux FICLONE, macOS ``clonefile``), a hardlink, then the
    hashing copy (across filesystems, or where the filesystem refuses
    links). No caller chooses one. The view is built beside ``dest`` (in
    ``build_in``) and renamed over it, so a reader sees the old file or
    the new one, never a gap or a partial file; a crash leaves ``dest`` as
    it was.

    A hardlink shares the object's inode. Nothing here chmods a view or
    the object (the object is protected first, only when it still carries
    a write bit), and a link made or renamed moves only the shared change
    time: when the object's stamp vouched for it just before, and size,
    mtime and file index are unchanged after, the stamp is rewritten with
    the new change time, so the object's next check reads no byte. Any
    other difference leaves the stamp to the next check, which re-hashes.

    Args:
        md5: The object's hash (a directory member's, or a file entry's).
        dest: The destination file.
        project_dir: Root directory of the wfc project.
        setting: ``[integrity] inputs``; the project's when None.
        build_in: The directory the view is built in; ``dest``'s own when
            None (a checkout builds its members beside the checkout).

    Returns:
        :data:`~wfc.storage.links.TIER_REFLINK`, ``TIER_HARDLINK`` or
        ``TIER_COPY``.

    Raises:
        ChangedEntryError: The object is not its address's bytes;
            ``dest`` is left as it was.
        FileNotFoundError: The object is not in the local cache.
    """
    口 = Step(step_num=1, name="Check the object",
             purpose="The one check against the object's address and stamp; a "
                     "changed object is never viewed. Protect it when it still "
                     "carries a write bit, then take its stat record")
    project_dir = Path(project_dir)
    dest = Path(dest)
    setting = _setting(project_dir, setting)
    src = _cache_path(project_dir, md5)
    verdict = _check_object(project_dir, md5, setting)
    if verdict.status != INTACT:
        if not src.exists():
            raise FileNotFoundError(f"cache object {md5} is not in the local cache")
        raise ChangedEntryError(md5, [md5])
    protect_entry(src)
    stamp = layout.cache_object_stamp(project_dir, md5)
    before = entry_record(src)
    vouched = read_stamp(stamp, _object_hash(md5)) == before
    if _is_view_of(dest, src):
        return links.TIER_HARDLINK

    口 = Step(step_num=2, name="Build the view beside the destination",
             purpose="Reflink, else hardlink, else the hashing copy, into a "
                     "temporary name on the destination's filesystem",
             critical="Only directories are opened for writing, never a file")
    build_in = Path(build_in) if build_in is not None else dest.parent
    build_in.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    open_dirs(build_in, dest.parent)
    tmp = layout.view_temp_path(build_in, dest.name, links.token())
    _discard(tmp)
    try:
        tier = _build_view(src, tmp, md5)

        口 = Step(step_num=3, name="Rename it over the destination",
                 purpose="One rename replaces the old destination; it is never "
                         "deleted first")
        _rename_over(tmp, dest)
    except BaseException:
        _discard(tmp)
        raise

    口 = Step(step_num=4, name="Keep the object's stamp",
             purpose="Rewrite the object's stamp when it vouched just before and "
                     "only the shared change time moved since")
    if vouched:
        after = entry_record(src)
        if _only_change_time_moved(before, after):
            write_stamp(stamp, after, _object_hash(md5))
    return tier


def remove_view(path: Path | str, project_dir: Path | str | None = None,
                md5: str | None = None) -> None:
    """Remove a view (a file or a directory of views), leaving every cache object protected.

    Directories are opened for writing; no file is chmodded (on Windows a
    read-only view is deleted with the ignore-read-only disposition), so a
    hardlink's cache object stays read-only. With the entry's ``md5``,
    each object's stamp is kept as :func:`make_view` keeps it: rewritten
    when it vouched just before and only the shared change time moved. A
    ``.dir`` hash names its members' objects (the view is a directory of
    their views); a file hash names its one object.

    Args:
        path: The view, or a folder holding it.
        project_dir: Root directory of the wfc project, with ``md5``.
        md5: The cache entry the view is a view of: a file's hash, or a
            directory's ``.dir`` hash.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return
    vouched: dict[str, dict] = {}
    if md5 is not None and project_dir is not None:
        project_dir = Path(project_dir)
        objects = (sorted(manifest_members(project_dir, md5)) if is_directory_hash(md5)
                   else [md5])
        for obj in objects:
            try:
                record = entry_record(_cache_path(project_dir, obj))
                if read_stamp(layout.cache_object_stamp(project_dir, obj),
                              _object_hash(obj)) == record:
                    vouched[obj] = record
            except OSError:
                continue
    open_dirs(path.parent)
    links.delete_tree(path)
    if not vouched:
        return
    assert project_dir is not None  # vouched is filled only with a project_dir
    with stamps_batched(project_dir):
        for obj, before in vouched.items():
            try:
                after = entry_record(_cache_path(Path(project_dir), obj))
            except OSError:
                continue
            if _only_change_time_moved(before, after):
                write_stamp(layout.cache_object_stamp(Path(project_dir), obj), after,
                            _object_hash(obj))


def _place_members(dest: Path, entries: list[tuple[str, str]], project_dir: Path,
                   setting: str) -> None:
    """Make a directory hold exactly a manifest's files, each a view, member by member.

    What the manifest does not name (a symlink anywhere, an extra file, a
    directory where a file belongs) is removed; each member that is not
    already a hardlink of its object is made a view, built beside the
    checkout and renamed over its path. A reader sees each file old or new,
    never partial.
    """
    if os.path.lexists(dest) and (dest.is_symlink() or not dest.is_dir()):
        open_dirs(dest.parent)
        links.delete_file(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    open_dirs(dest.parent)
    dest.mkdir(exist_ok=True)
    _open_tree(dest)
    want = dict(entries)
    want_dirs = {"/".join(rel.split("/")[:i]) for rel in want
                 for i in range(1, rel.count("/") + 1)}
    for root, dirs, files in os.walk(dest, topdown=True):
        for name in list(dirs):
            p = os.path.join(root, name)
            rel = Path(p).relative_to(dest).as_posix()
            if os.path.islink(p) or rel not in want_dirs:
                links.delete_tree(p)
                dirs.remove(name)
        for name in files:
            p = os.path.join(root, name)
            rel = Path(p).relative_to(dest).as_posix()
            if os.path.islink(p) or rel not in want:
                links.delete_file(p)
    with stamps_batched(project_dir):
        for rel, member in entries:
            target = dest / Path(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            make_view(member, target, project_dir, setting=setting, build_in=dest.parent)


@dataclass(frozen=True)
class ViewProbe:
    """Which view tier works from the cache into one directory wfc makes views in.

    Attributes:
        target: ``samples`` (restored samples) or ``checkouts``.
        directory: The directory probed (the nearest existing one).
        tier: ``reflink``, ``hardlink`` or ``copy``.
        reason: For a copy, why no link was made; None otherwise.
    """

    target: str
    directory: Path
    tier: str
    reason: str | None = None


def _nearest_existing(path: Path) -> Path:
    """The path itself, or its nearest ancestor that exists."""
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


@task(purpose="Probe which view tier works between the cache and the directories "
              "wfc makes views in, with a scratch file each, writing nothing else",
      inputs="the project root; its cache, sample workspace and checkouts directories",
      outputs="one ViewProbe per directory: the tier, and why a copy when it is one")
def probe_view_tiers(project_dir: Path | str) -> list[ViewProbe]:
    """Try each view tier from the cache into the sample workspace and the checkouts.

    A directory not created yet is probed at its nearest existing ancestor,
    which is where it will be created. The scratch files are removed again.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        A :class:`ViewProbe` for ``samples`` and one for ``checkouts``.

    Raises:
        OSError: A scratch file could not be written.
    """
    root = Path(project_dir)
    src_dir = _nearest_existing(_cache_dir(root))
    probes: list[ViewProbe] = []
    for target, directory in (("samples", layout.samples_dir(root)),
                              ("checkouts", layout.checkouts_dir(root))):
        dst_dir = _nearest_existing(directory)
        found = links.probe_pair(src_dir, dst_dir)
        probes.append(ViewProbe(target, dst_dir, found.tier, found.reason))
    return probes


def _verified_manifest(project_dir: Path, md5: str, setting: str) -> list[tuple[str, str]]:
    """A directory entry's manifest, after its object passes the one check.

    The manifest object is checked against its own address (by its stamp
    under ``stat-then-hash``, by a re-hash otherwise and always for a
    freshly pulled, unstamped object), so a checkout is never built from,
    compared with or stamped against a manifest whose bytes changed.

    Raises:
        ChangedEntryError: The manifest object is not its address's bytes.
    """
    if _check_object(project_dir, md5, setting).status == CHANGED:
        raise ChangedEntryError(md5, [md5])
    return read_manifest(project_dir, md5) or []


@task(purpose="The one checkout: turn a directory entry into a real directory, "
              "after its manifest object passes the one check, building it "
              "with the hashing copy and stamping it only after every member "
              "matched",
      inputs="the entry's .dir hash; the destination and its stamp; the local "
             "cache's manifest and member objects; [integrity] inputs",
      outputs="True with the destination holding the entry (read-only, "
              "stamped); False when the entry is not complete locally")
def checkout(md5: str, dest: Path | str, project_dir: Path | str,
             *, stamp: Path | str | None = None,
             setting: str | None = None, check_dest: bool = True) -> bool:
    """Turn a directory entry into a directory of views at ``dest``: the one checkout.

    Every reader of a directory entry goes through this.  It verifies that
    the manifest and every member are local, is idempotent when ``dest``
    already holds exactly the entry's files, and replaces a changed
    ``dest`` member by member: every member object passes the one check
    first, then each member is a read-only view (:func:`make_view`) built
    beside the checkout and renamed over its path, and what the manifest
    does not name is removed. The old checkout is never deleted first.
    The checkout's directories are read-only; there is no writable
    checkout.

    With a ``stamp`` (a read-only checkout that readers hit repeatedly),
    ``dest`` is asked the one integrity check: under ``stat-then-hash`` a
    stamp whose stat fields (size, mtime and change time, plus the file
    index on Windows) still match vouches for it without reading a byte; otherwise,
    and always under ``hash``, it is re-hashed against ``md5``.  A built
    checkout is made read-only first and stamped after, since a chmod
    moves the ctime.  Without a stamp, every call hashes the members.

    Args:
        md5: The directory's ``.dir`` hash.
        dest: The directory to create.
        project_dir: Root directory of the wfc project.
        stamp: Where the checkout's stamp lives (``layout.checkout_stamp``,
            or a restored sample's ``layout.sample_stamp``).
        setting: ``[integrity] inputs``; the project's when None.
        check_dest: False when the caller already found ``dest`` changed,
            so it is rebuilt without being hashed again.

    Returns:
        True when ``dest`` holds the entry; False when the entry is not
        complete in the local cache.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        ChangedEntryError: The manifest object, or a member object, is not
            its address's bytes; nothing is placed or stamped.
    """
    口 = Step(step_num=1, name="Check the destination",
             purpose="A complete entry only; with a stamp, the one check "
                     "against the destination; without one, the destination "
                     "is compared with the checked manifest; a caller that "
                     "found it changed skips the check")
    project_dir = Path(project_dir)
    dest = Path(dest)
    stamp = Path(stamp) if stamp is not None else None
    setting = _setting(project_dir, setting)
    if not entry_is_complete(project_dir, md5):
        return False
    if check_dest and stamp is not None:
        if check_entry(dest, recorded_hash=md5, stamp=stamp, setting=setting).intact:
            return True
    entries = _verified_manifest(project_dir, md5, setting)
    if check_dest and stamp is None and _checkout_verifies(dest, entries):
        return True
    _drop_stamp(stamp)

    口 = Step(step_num=2, name="Check every member object",
             purpose="Each member object passes the one check against its own "
                     "address and stamp before any is viewed",
             critical="A changed member leaves the destination as it was and "
                      "nothing stamped")
    members = list(dict.fromkeys(m for _rel, m in entries))
    changed = [m for m in members
               if _check_object(project_dir, m, setting).status != INTACT]
    if changed:
        raise ChangedEntryError(md5, changed)

    口 = Step(step_num=3, name="View the members in place",
             purpose="Remove what the manifest does not name, then make each "
                     "member a view renamed over its path, member by member")
    _place_members(dest, entries, project_dir, setting)

    口 = Step(step_num=4, name="Protect and stamp",
             purpose="The checkout's directories read-only (its files are "
                     "already), then the stamp from the checkout as it sits")
    protect_dirs(dest)
    if stamp is not None:
        write_stamp(stamp, entry_record(dest), md5)
    return True


def local_path(project_dir: Path | str, md5: str,
               setting: str | None = None) -> Path | None:
    """The path a reader reads for a complete local entry.

    A file entry is read at its cache address; Storage's one reader
    (``resolve._local_entry_path``) has checked its object first.  A
    directory entry is read from its checkout under the project tree
    (``layout.checkout_dir``), built with every member checked as it is
    copied and stamped on first use; a later read is checked against its
    stamp (``layout.checkout_stamp``) and rebuilt when it changed.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        The path, or None when the entry is not complete locally.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        ChangedEntryError: A member object changed, so the checkout could
            not be built from it.
    """
    project_dir = Path(project_dir)
    if not entry_is_complete(project_dir, md5):
        return None
    if not is_directory_hash(md5):
        return _cache_path(project_dir, md5)
    dest = layout.checkout_dir(project_dir, md5)
    stamp = layout.checkout_stamp(project_dir, md5)
    return dest if checkout(md5, dest, project_dir, stamp=stamp, setting=setting) else None


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

    Every copy is the hashing copy: the source is streamed into a tmp file
    while the bytes written are hashed, and that md5 must equal ``md5``
    (a mismatch raises and stores nothing). The verified tmp is renamed
    into its address, made read-only, then stamped.

    Move strategy:
    - Try ``os.rename`` first (atomic, fast on same volume). Nothing is
      copied, so nothing is verified or stamped: the first read hashes it.
    - On cross-volume ``OSError``, fall back to the hashing copy + ``unlink``.
    - A directory is stored as its members plus a ``.dir`` manifest
      (:func:`_cache_directory`); move mode then consumes the source tree.

    Idempotency:
    - An address that already holds the object is checked with the one
      integrity check. Intact, it is kept (move mode drops the staging
      duplicate); changed, the verified copy replaces it.
    - The same principle covers concurrent writers: the cache is
      content-addressed (same md5 = same bytes), so on a failure path
      where ``dest`` turns out to exist and check intact, another writer
      already landed this content and the call succeeds. On Windows the
      loser's ``os.rename`` onto a just-created dest raises (POSIX silently
      overwrites), so without this guard parallel jobs sharing an env
      blob crash mid-pipeline.

    Args:
        path: Source file/directory path.
        md5: Pre-computed MD5 hex digest of the path; the bytes copied must
            have it.
        project_dir: Root directory of the wfc project.
        move: If True (default), consume the source. If False, copy and
            leave source intact.

    Returns:
        The cache path where the file was stored.

    Raises:
        ValueError: The bytes copied do not have ``md5``.
    """
    project_dir = Path(project_dir).resolve()
    path = Path(path)
    dest = _cache_path(project_dir, md5)
    setting = _setting(project_dir, None)

    if path.is_dir():
        _cache_directory(path, md5, project_dir, setting)
        if move and path.exists():
            _consume_staging(path)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        if dest.exists() and _check_object(project_dir, md5, setting).intact:
            # Content-addressed idempotency: dedup. Unlink the staging
            # duplicate so callers don't leave orphan staging copies behind.
            if path.exists() and path != dest:
                _consume_staging(path)
            return dest
        if not dest.exists():
            try:
                # os.rename is atomic on same volume; OSError on cross-volume.
                os.rename(str(path), str(dest))
                _make_read_only(dest)
                return dest
            except OSError:
                if dest.exists() and _check_object(project_dir, md5, setting).intact:
                    # Concurrent writer won the rename race: dedup, consume
                    # the staging duplicate. (Not cross-volume -- on Windows,
                    # rename onto an existing file raises.)
                    _consume_staging(path)
                    return dest
        # Cross-volume, or a changed object at the address: the hashing copy.
        _copy_verified(path, md5, project_dir, setting)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return dest

    # Copy mode: the hashing copy, preserving the source.
    _copy_verified(path, md5, project_dir, setting)
    return dest


def store_copy(path: Path | str, expected: str | None, project_dir: Path | str,
               setting: str | None = None) -> str:
    """Copy a file or directory into the cache with one hashing read; return its hash.

    :func:`cache_file`'s copy mode for a caller that holds no hash yet (the
    archive pass): the address and the returned hash are the md5 of the
    bytes copied (a directory's is its manifest's, built from its members'
    streamed hashes), so the source is read once. A caller that holds a
    hash (collect's, under ``[integrity] inputs = "hash"``) passes it as
    ``expected`` and the bytes copied must have it.

    Args:
        path: The source file or directory (left in place).
        expected: The hash the bytes copied must have, or None.
        project_dir: Root directory of the wfc project.
        setting: ``[integrity] inputs``; the project's when None.

    Returns:
        The content hash of what was stored.

    Raises:
        ChangedSourceError: The bytes copied are not ``expected``.
        DirectoryContentError: A directory holds content wfc refuses.
        OSError: A read or write failed; nothing is recorded for a partial
            copy.
    """
    project_dir = Path(project_dir).resolve()
    path = Path(path)
    setting = _setting(project_dir, setting)
    if path.is_dir():
        return _cache_directory(path, expected, project_dir, setting)
    return _copy_verified(path, expected, project_dir, setting)


def restore_from_cache(
    md5: str, dest: Path | str, project_dir: Path | str,
    *, stamp: Path | str | None = None, setting: str | None = None,
    check_dest: bool = True,
) -> bool:
    """Restore an entry from the DVC cache to a workspace path as a read-only view.

    Checkout-like behavior: a destination that already holds the entry is
    kept (idempotent): with a ``stamp`` it is asked the one integrity
    check, without one it is hashed. A destination that does not hold it
    is replaced by renaming a view over it, never by deleting it first.

    A file is the one view (:func:`make_view`): its object passes the one
    check, then a reflink, a hardlink or the hashing copy is built beside
    ``dest`` and renamed over it, and the view is stamped from what sits
    there. A changed object raises and leaves ``dest`` as it was. A
    directory entry is the one checkout, each member a view.

    Args:
        md5: Content hash of the entry to restore.
        dest: Destination path in the workspace.
        project_dir: Root directory of the wfc project.
        stamp: Where the restored copy's stamp lives (a restored sample's
            ``layout.sample_stamp``); written only over a verified copy.
        setting: ``[integrity] inputs``; the project's when None.
        check_dest: False when the caller already found ``dest`` missing
            or changed, so it is replaced without being hashed again.

    Returns:
        True if restore succeeded (or was skipped because dest is valid),
        False if the cache entry is missing.

    Raises:
        MalformedEntryError: The address holds a directory tree.
        ChangedEntryError: The object (or a member) is not its address's
            bytes; ``dest`` is left as it was and nothing is stamped.
    """
    project_dir = Path(project_dir).resolve()
    dest = Path(dest)
    stamp = Path(stamp) if stamp is not None else None
    setting = _setting(project_dir, setting)
    src = _cache_path(project_dir, md5)

    if is_directory_hash(md5):
        # A directory entry is a manifest plus members: the one checkout
        # makes each member a view, read-only like a restored file.
        return checkout(md5, dest, project_dir, stamp=stamp, setting=setting,
                        check_dest=check_dest)
    check_entry_shape(project_dir, md5)

    if not src.exists():
        return False

    if check_dest and os.path.lexists(dest):
        if stamp is not None:
            intact = check_entry(dest, recorded_hash=md5, stamp=stamp,
                                 setting=setting).intact
        else:
            intact = dest.is_file() and hash_path(dest) == md5
        if intact:
            return True  # Already valid — skip restore

    # A stamp vouches for nothing while its destination is replaced; the
    # view is renamed over the old destination, which is never deleted first.
    _drop_stamp(stamp)
    make_view(md5, dest, project_dir, setting=setting)
    if stamp is not None:
        write_stamp(stamp, entry_record(dest), md5)
    return True
