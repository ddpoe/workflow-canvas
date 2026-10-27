"""A method's version list: the git-tracked record of every version it has had.

The list is ``<method>.versions.json`` beside the method's snapshot (Layout
derives the path). Registration appends an entry when the snapshot's
content (its code fingerprint and env fingerprint) differs from the newest
entry's, and commits the list with the snapshot in one commit. Each entry
holds its label (1, 2, 3 ... shown as v1, v2, v3), the code fingerprint,
the env's name, fingerprint and image reference, the registration time and
the snapshot's git tree hash. The newest version is the highest label. A
version's content identity is derived from its two fingerprints
(``wfc.identity.build_version_identity``), never stored.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axiom_annotations import task

from .. import layout

#: The version list file's format version.
VERSION_LIST_FORMAT = 1


@task(purpose="Compute a snapshot's code fingerprint: every file of the copy and "
              "the contract its method.yaml declares, the one recipe registration "
              "records a version under and the claim keys on",
      inputs="a method snapshot directory",
      outputs="the 64-hex code fingerprint")
def snapshot_code_fingerprint(snapshot_dir: Path) -> str:
    """Return the code fingerprint of a method snapshot.

    The contract that enters the fingerprint is the snapshot's
    ``method.yaml``, never the MethodContract row: the snapshot is the
    authority for a method's code and its contract alike.

    Args:
        snapshot_dir: The snapshot directory.

    Returns:
        The code fingerprint.

    Raises:
        ValueError: The snapshot cannot be fingerprinted or holds no
            ``method.yaml``.
    """
    from ..contracts import parse_method_yaml, render_contract_projection
    from ..identity import build_code_fingerprint

    projection = render_contract_projection(parse_method_yaml(snapshot_dir))
    return build_code_fingerprint(snapshot_dir, projection)


@dataclass(frozen=True)
class VersionEntry:
    """One entry of a method's version list.

    Attributes:
        label: The version number (1 for v1).
        code_fingerprint: The snapshot's code fingerprint.
        env_name: The env the version runs in.
        env_fingerprint: The env's fingerprint when the version was registered.
        image_ref: The env's recorded image reference (the manifest record's
            container ref) when the version was registered.
        registered_at: The registration time, ISO 8601 UTC; strictly later
            than every earlier entry's.
        tree_hash: The git tree hash of the snapshot this version committed.
    """

    label: int
    code_fingerprint: str
    env_name: str
    env_fingerprint: str
    image_ref: str
    registered_at: str
    tree_hash: str

    @property
    def identity(self) -> str:
        """The version's content identity (its code and env fingerprints)."""
        from ..identity import build_version_identity
        return build_version_identity(self.code_fingerprint, self.env_fingerprint)

    @property
    def registered_datetime(self) -> datetime:
        """The registration time as an aware UTC datetime."""
        return as_utc(datetime.fromisoformat(self.registered_at))


def as_utc(value: datetime) -> datetime:
    """Return *value* as an aware UTC datetime (SQLite hands back naive ones)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def read_version_list(root: Path, module: str, method: str) -> list[VersionEntry] | None:
    """Read a method's version list, entries in label order.

    Args:
        root: The project root.
        module: The method's module name.
        method: The method's name.

    Returns:
        The entries, or ``None`` when the method has no list file.

    Raises:
        ValueError: The file is not a version list wfc can read.
    """
    path = layout.method_versions_path(root, module, method)
    if not path.is_file():
        return None
    return parse_version_list(path.read_bytes(), module, method, str(path))


def parse_version_list(data: bytes, module: str, method: str,
                       where: str) -> list[VersionEntry]:
    """Parse a version list's bytes into entries, in label order.

    Args:
        data: The list file's bytes (from disk, or a commit's blob).
        module: The method's module name, for the message.
        method: The method's name, for the message.
        where: Where the bytes came from, for the message.

    Returns:
        The entries.

    Raises:
        ValueError: The bytes are not a version list wfc can read.
    """
    try:
        parsed = json.loads(data.decode("utf-8"))
        entries = [VersionEntry(**raw) for raw in parsed["versions"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"The version list of method '{module}/{method}' at {where} cannot be "
            f"read ({exc}). Restore it with git, or register the method again "
            f"with `wfc register-method`."
        ) from exc
    return sorted(entries, key=lambda e: e.label)


def render_version_list(entries: list[VersionEntry]) -> bytes:
    """Return a version list's bytes: sorted keys, LF newlines, a trailing newline.

    Args:
        entries: Every entry, in any order (written in label order).

    Returns:
        The UTF-8 bytes registration commits and writes.
    """
    data = {"format": VERSION_LIST_FORMAT,
            "versions": [asdict(e) for e in sorted(entries, key=lambda e: e.label)]}
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_version_list_bytes(root: Path, module: str, method: str,
                             data: bytes) -> Path:
    """Put *data* at a method's version list path: written aside, then renamed over.

    A reader sees the old list or the new one, never a partial file; a
    write that fails leaves the old list and no temporary file in the
    registry (the temporary file is under Layout's staging directory).

    Args:
        root: The project root.
        module: The method's module name.
        method: The method's name.
        data: The list's bytes.

    Returns:
        The list file's path.
    """
    import os
    import tempfile

    path = layout.method_versions_path(root, module, method)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = layout.registry_staging_dir(root)
    staging.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=staging, prefix=f"{module}-{method}-list-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_version_list(root: Path, module: str, method: str,
                       entries: list[VersionEntry]) -> Path:
    """Write a method's version list (:func:`render_version_list`), then rename it over.

    Args:
        root: The project root.
        module: The method's module name.
        method: The method's name.
        entries: Every entry, in any order (written in label order).

    Returns:
        The list file's path.
    """
    return write_version_list_bytes(root, module, method, render_version_list(entries))


def newest_entry(entries: list[VersionEntry] | None) -> VersionEntry | None:
    """Return the newest entry (the highest label), or ``None`` for no entries."""
    return max(entries, key=lambda e: e.label) if entries else None


def next_entry(entries: list[VersionEntry] | None, *, code_fingerprint: str,
               env_name: str, env_fingerprint: str, image_ref: str,
               tree_hash: str) -> VersionEntry:
    """Return the entry a registration appends: the next label, the time now.

    The label is the highest label plus one. The registration time is read
    from ``time.time()``, or one microsecond after the newest entry's when
    the clock has not moved past it, so entries order by time as by label
    and no two entries of one list share a time (the row sync matches on
    it).

    Args:
        entries: The list's current entries, or ``None``.
        code_fingerprint: The new version's code fingerprint.
        env_name: Its env's name.
        env_fingerprint: Its env's fingerprint.
        image_ref: Its env's recorded image reference.
        tree_hash: Its snapshot's git tree hash.

    Returns:
        The new entry.
    """
    newest = newest_entry(entries)
    now = datetime.fromtimestamp(time.time(), UTC)
    if newest is not None and newest.registered_datetime >= now:
        now = newest.registered_datetime + timedelta(microseconds=1)
    return VersionEntry(
        label=(newest.label + 1) if newest is not None else 1,
        code_fingerprint=code_fingerprint,
        env_name=env_name,
        env_fingerprint=env_fingerprint,
        image_ref=image_ref,
        registered_at=now.isoformat(),
        tree_hash=tree_hash,
    )
