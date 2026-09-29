"""Content hash -- what are these bytes.

One MD5 with dispatch by kind.

- A file is streamed in chunks; its hash is the MD5 of its bytes.
- A directory is DVC's representation: an ordered manifest of
  ``(relpath, md5)`` entries, serialized exactly as DVC serializes a
  ``.dir`` object, whose MD5 plus the ``.dir`` suffix is the directory's
  identity.  The same one function produces the digest and the manifest
  bytes the cache stores, so the two cannot drift.

The 32-character hex format (plus ``.dir`` for a directory) is DVC's, so one
value both identifies a sample and addresses its cache entry, and DVC takes
its directory branch on a ``.dir`` value.

This module also decides what counts as a directory's content, and refuses
what it cannot store faithfully (:class:`DirectoryContentError`): a symlink
anywhere inside, two names differing only by case, and a directory with no
files.  Dotfiles count; empty subdirectories and file modes do not.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_CHUNK_SIZE = 1 << 20  # 1 MiB -- stream large files without loading into memory

#: The suffix that marks a directory's content hash (DVC's ``.dir``).
DIR_HASH_SUFFIX = ".dir"


class DirectoryContentError(ValueError):
    """A directory holds content wfc cannot store faithfully.

    Raised for a symlink inside the directory, two names that differ only
    by case, or a directory with no files.  A ``ValueError``, so surfaces
    that map a refusal to a user error (the canvas route's 400) already do.

    Attributes:
        path: The offending path (the symlink, the colliding names' parent,
            or the empty directory).
        reason: A short description of what was refused.
    """

    def __init__(self, path: Path | str, reason: str):
        """Build the refusal naming the path and the reason.

        Args:
            path: The offending path.
            reason: What was refused, e.g. ``"a symlink"``.
        """
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            f"Refusing directory content at {self.path}: {reason}. wfc stores "
            f"a directory's files by their bytes and cannot store this "
            f"faithfully; fix the directory and try again."
        )


@dataclass(frozen=True)
class DirectoryManifest:
    """A directory's content in DVC's ``.dir`` representation.

    Attributes:
        entries: ``(relpath, md5)`` per file, sorted by relpath; relpaths
            use ``/`` separators.
        manifest_bytes: The serialized manifest, byte-identical to DVC's.
        hash: The directory's identity: MD5 of ``manifest_bytes`` plus
            ``.dir``.
        total_size: Sum of the files' sizes in bytes.
        file_count: Number of files.
        newest_mtime: The newest file's modification time.
    """

    entries: tuple[tuple[str, str], ...]
    manifest_bytes: bytes
    hash: str
    total_size: int
    file_count: int
    newest_mtime: float


def hash_file(path: Path | str) -> str:
    """Compute the MD5 hex digest of a file's content.

    Streams in chunks to handle large files without excessive memory use.

    Args:
        path: Path to the file to hash.

    Returns:
        32-character hex MD5 digest.

    Raises:
        FileNotFoundError: If path does not exist.
        IsADirectoryError: If path is a directory (use hash_directory instead).
    """
    path = Path(path)
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_directory_hash(content_hash: str | None) -> bool:
    """Whether a content hash names a directory (carries the ``.dir`` suffix).

    Args:
        content_hash: A content hash, or None.

    Returns:
        True for a directory's hash.
    """
    return bool(content_hash) and content_hash.endswith(DIR_HASH_SUFFIX)


def manifest_bytes(entries: Iterable[tuple[str, str]]) -> bytes:
    """Serialize ``(relpath, md5)`` entries exactly as DVC serializes a ``.dir`` object.

    DVC writes a JSON list of ``{"md5": ..., "relpath": ...}`` objects
    sorted by relpath, with sorted keys and ``json.dumps``' default
    separators, UTF-8 encoded.

    Args:
        entries: ``(relpath, md5)`` pairs; relpaths use ``/``.

    Returns:
        The manifest bytes.
    """
    ordered = sorted(entries, key=lambda e: e[0])
    return json.dumps(
        [{"md5": md5, "relpath": rel} for rel, md5 in ordered], sort_keys=True
    ).encode("utf-8")


def manifest_hash(data: bytes) -> str:
    """The ``.dir`` hash of serialized manifest bytes.

    Args:
        data: Manifest bytes from :func:`manifest_bytes`.

    Returns:
        MD5 hex digest of ``data`` plus ``.dir``.
    """
    return hashlib.md5(data).hexdigest() + DIR_HASH_SUFFIX


def parse_manifest_bytes(data: bytes) -> list[tuple[str, str]]:
    """Read a stored ``.dir`` manifest back into ``(relpath, md5)`` entries.

    Args:
        data: The manifest object's bytes.

    Returns:
        The entries in stored order.

    Raises:
        ValueError: The bytes are not a DVC ``.dir`` manifest.
    """
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("a directory manifest must be a JSON list")
    entries: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or "relpath" not in item or "md5" not in item:
            raise ValueError("a directory manifest entry needs relpath and md5")
        entries.append((str(item["relpath"]), str(item["md5"])))
    return entries


def check_case_collisions(names: Iterable[str], where: Path | str) -> None:
    """Refuse two names in one directory that differ only by case.

    Pure: the names are handed in, so the rule is checkable on a filesystem
    that cannot hold such a pair (Windows and macOS defaults).

    Args:
        names: The entry names (files and subdirectories) of one directory.
        where: The directory, for the message.

    Raises:
        DirectoryContentError: Two names collide case-insensitively.
    """
    seen: dict[str, str] = {}
    for name in names:
        key = name.casefold()
        other = seen.get(key)
        if other is not None and other != name:
            raise DirectoryContentError(
                where,
                f"two names differ only by case ({other!r} and {name!r}), "
                f"which cannot both exist on a case-insensitive filesystem",
            )
        seen[key] = name


def _is_link(path: str) -> bool:
    """Whether a path is a symlink or (on Windows) a directory junction."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def directory_manifest(path: Path | str) -> DirectoryManifest:
    """Walk a directory into its DVC ``.dir`` manifest, refusing unstorable content.

    Every file counts, dotfiles included; empty subdirectories and file
    modes do not.  A symlink (or junction) anywhere inside, two names in
    one directory that differ only by case, and a directory with no files
    are refused by path before anything is hashed.

    Args:
        path: The directory.

    Returns:
        The directory's :class:`DirectoryManifest`.

    Raises:
        NotADirectoryError: ``path`` is not a directory, including when it
            does not exist.
        DirectoryContentError: The directory holds a symlink, a case-only
            name collision, or no files.
    """
    path = Path(path)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    if _is_link(str(path)):
        raise DirectoryContentError(path, "the directory itself is a symlink")

    files: list[tuple[str, Path]] = []
    for root, dirs, fnames in os.walk(path, followlinks=False):
        root_path = Path(root)
        check_case_collisions(list(dirs) + list(fnames), root_path)
        for name in list(dirs) + list(fnames):
            if _is_link(os.path.join(root, name)):
                raise DirectoryContentError(root_path / name, "a symlink")
        for fname in fnames:
            fpath = root_path / fname
            rel = posixpath.join(*fpath.relative_to(path).parts)
            files.append((rel, fpath))

    if not files:
        raise DirectoryContentError(
            path, "the directory holds no files (an empty directory has no "
                  "content to store)"
        )

    entries: list[tuple[str, str]] = []
    total_size = 0
    newest = 0.0
    for rel, fpath in files:
        st = fpath.stat()
        total_size += st.st_size
        newest = max(newest, st.st_mtime)
        entries.append((rel, hash_file(fpath)))

    data = manifest_bytes(entries)
    return DirectoryManifest(
        entries=tuple(sorted(entries, key=lambda e: e[0])),
        manifest_bytes=data,
        hash=manifest_hash(data),
        total_size=total_size,
        file_count=len(entries),
        newest_mtime=newest,
    )


def hash_directory(path: Path | str) -> str:
    """Compute a directory's content hash: DVC's ``.dir`` digest.

    Args:
        path: Path to the directory to hash.

    Returns:
        The MD5 of the directory's DVC manifest plus ``.dir``.

    Raises:
        NotADirectoryError: If path is not a directory, including when it
            does not exist.
        DirectoryContentError: The directory holds a symlink, a case-only
            name collision, or no files.
    """
    return directory_manifest(path).hash


def hash_path(path: Path | str) -> str:
    """Hash a file or directory, dispatching to the appropriate function.

    Args:
        path: Path to hash.

    Returns:
        A file's 32-character hex MD5, or a directory's ``.dir`` hash.

    Raises:
        DirectoryContentError: A directory holds content wfc refuses.
    """
    path = Path(path)
    if path.is_dir():
        return hash_directory(path)
    return hash_file(path)
