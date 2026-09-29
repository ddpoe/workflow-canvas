"""The DVC transport: push cache entries to the configured remote and pull them back.

This is the only module in wfc that imports ``dvc``.  The import is deferred
to first use, so its cost (about 4 seconds) is paid once by a process that
transfers, not at CLI startup.

- :func:`push` transfers each entry on its own and returns every entry's
  outcome and error (:class:`PushOutcome`); a failed entry fails alone.
  :func:`pull` transfers a list of content hashes and returns DVC's
  ``TransferResult``.  A directory's ``.dir`` hash passes through intact.
- :func:`pull_cache` wraps :func:`pull` for callers that want a yes-or-no
  answer and a warning instead of an exception.
- :func:`has_remote_configured` is a pure INI parse of ``.dvc/config`` that
  never imports DVC, so hot paths (pipeline startup, the run record,
  preflight) call it freely.
"""

from __future__ import annotations

import configparser
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from .. import layout
from ..identity import is_directory_hash

# Lazy module-level cache for DVC imports.  First call to _dvc() pays the
# ~4s cost; every subsequent call returns the cached tuple.
#
# Thread-safety: no explicit lock is needed.  CPython's import system
# (``_imp`` lock) serializes the actual ``import dvc.repo`` so concurrent
# first-callers see the same fully-initialized modules; the subsequent
# assignment of the resulting tuple to ``_DVC_IMPORTS`` is a single bytecode
# (``STORE_GLOBAL``) and is atomic under the GIL.  Worst case is multiple
# threads race to do the import once and then all observe the same tuple.
_DVC_IMPORTS: tuple | None = None


def _dvc():
    """Lazy import of ``dvc.repo.Repo`` + ``dvc_data.hashfile.hash_info.HashInfo``.

    Returns:
        Tuple of ``(Repo, HashInfo)``.  Cached after first call.
    """
    global _DVC_IMPORTS
    if _DVC_IMPORTS is None:
        from dvc.repo import Repo
        from dvc_data.hashfile.hash_info import HashInfo

        _DVC_IMPORTS = (Repo, HashInfo)
    return _DVC_IMPORTS


class PushOutcome(NamedTuple):
    """Each entry's outcome from :func:`push`.

    Attributes:
        succeeded: The hashes that reached the remote.
        failed: The hashes that did not.
        errors: Each failed hash's own error message.
    """

    succeeded: list[str]
    failed: list[str]
    errors: dict[str, str]


def _local_entry_problem(project_dir: Path, md5: str, repair: str | None) -> str | None:
    """Why an entry cannot be pushed from the local cache, or None.

    DVC skips an object missing from the local cache without reporting it as
    failed, so an incomplete entry is refused here instead of counted as
    pushed.

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.
        repair: The sentence naming the repair for a malformed record.

    Returns:
        The error message, or None when the entry is whole and well-formed.
    """
    from .cache import MalformedEntryError, check_entry_shape, entry_is_complete

    try:
        check_entry_shape(project_dir, md5, repair)
        if entry_is_complete(project_dir, md5):
            return None
    except MalformedEntryError as exc:
        return str(exc)
    return (
        f"Cache entry {md5} is not complete in the local cache (its object, "
        f"or a member of its directory manifest, is missing), so it cannot "
        f"be pushed."
    )


def _entry_objects(project_dir: Path, md5: str, HashInfo) -> list:
    """The DVC object ids one entry's transfer names.

    DVC's cloud transfer is shallow for a ``.dir`` id: it moves the manifest
    object and none of its members unless they are named too (DVC's own
    ``push`` expands them from the manifest the same way).

    Args:
        project_dir: Root directory of the wfc project.
        md5: The entry's content hash.
        HashInfo: DVC's ``HashInfo`` class.

    Returns:
        The entry's own id, plus each member's for a directory.
    """
    from .cache import manifest_members

    objs = [HashInfo(name="md5", value=md5)]
    if is_directory_hash(md5):
        objs += [HashInfo(name="md5", value=m)
                 for m in sorted(manifest_members(project_dir, md5))]
    return objs


def push(
    hashes: Iterable[str],
    project_dir: Path | str,
    repairs: dict[str, str] | None = None,
) -> PushOutcome:
    """Push each entry to the configured DVC remote in its own transfer.

    A directory's ``.dir`` hash is passed through intact, which is how DVC
    takes its directory branch (members, then the manifest).  Every entry
    gets its own transfer, outcome and error, so a failed entry fails
    alone; one DVC ``Repo`` is shared across the calls.  An entry that is
    malformed or incomplete in the local cache is refused before DVC sees
    it.

    Args:
        hashes: Content hashes of entries in the local cache.
        project_dir: Root directory of the wfc project (the DVC repo root).
        repairs: Optional repair sentence per hash, used when that entry is
            refused as a malformed record.

    Returns:
        The per-entry outcome.

    Raises:
        Any error opening the DVC repo escapes -- no entry can be pushed
        then, and callers (the push worker) catch broadly to drive
        retry/backoff.
    """
    hash_list = list(dict.fromkeys(hashes))
    if not hash_list:
        return PushOutcome([], [], {})
    Repo, HashInfo = _dvc()
    root = Path(project_dir)
    repairs = repairs or {}
    errors: dict[str, str] = {}
    with Repo(str(root)) as repo:
        for h in hash_list:
            problem = _local_entry_problem(root, h, repairs.get(h))
            if problem is not None:
                errors[h] = problem
                continue
            try:
                result = repo.cloud.push(_entry_objects(root, h, HashInfo))
            except Exception as exc:  # noqa: BLE001 -- the entry's own error
                errors[h] = str(exc) or type(exc).__name__
                continue
            failed = getattr(result, "failed", None) or ()
            if failed:
                errors[h] = (
                    f"DVC failed to transfer {len(failed)} object(s) of "
                    f"cache entry {h}."
                )
    return PushOutcome(
        succeeded=[h for h in hash_list if h not in errors],
        failed=[h for h in hash_list if h in errors],
        errors=errors,
    )


def pull(hashes: Iterable[str], project_dir: Path | str):
    """Pull the given content hashes from the configured DVC remote.

    A directory's manifest is pulled first; its members, which only the
    manifest names, are pulled next.  An object the remote lacks is skipped
    by DVC without being reported, so callers judge the outcome by
    completeness (:func:`pull_cache` does).

    Args:
        hashes: Content hashes to retrieve; a directory's keeps ``.dir``.
        project_dir: Root directory of the wfc project.

    Returns:
        A TransferResult-like object with ``.transferred`` and ``.failed``.
    """
    Repo, HashInfo = _dvc()
    hash_list = list(dict.fromkeys(hashes))
    if not hash_list:
        return _empty_result()
    root = Path(project_dir)
    transferred: set = set()
    failed: set = set()
    with Repo(str(root)) as repo:
        result = repo.cloud.pull([HashInfo(name="md5", value=h) for h in hash_list])
        transferred |= set(getattr(result, "transferred", ()) or ())
        failed |= set(getattr(result, "failed", ()) or ())
        dirs = [h for h in hash_list if is_directory_hash(h)]
        if dirs:
            members = {m for h in dirs for m in _members_of(root, h)}
            if members:
                result = repo.cloud.pull(
                    [HashInfo(name="md5", value=m) for m in sorted(members)]
                )
                transferred |= set(getattr(result, "transferred", ()) or ())
                failed |= set(getattr(result, "failed", ()) or ())
    return _PullResult(transferred, failed)


def _members_of(project_dir: Path, dir_hash: str) -> set[str]:
    from .cache import manifest_members

    return manifest_members(project_dir, dir_hash)


class _PullResult(NamedTuple):
    """The combined outcome of a directory-aware pull."""

    transferred: set
    failed: set


def pull_cache(md5s: list[str], project_dir: Path | str) -> bool:
    """Pull cache objects from the configured DVC remote.

    Delegates to :func:`pull`.  When ``.dvc/config`` has no
    remotes, returns False with a warning (callers should pre-flight
    with ``ensure_dvc_ready``).

    Args:
        md5s: List of MD5 hex digests to pull.
        project_dir: Root directory of the wfc project.

    Returns:
        True when every entry is complete in the local cache afterwards
        (a directory: its manifest and every member); False on any
        failure, an entry left incomplete, or no remote configured.

    Raises:
        MalformedEntryError: A hash's local address holds a directory
            tree (a malformed record).
    """
    project_dir = Path(project_dir).resolve()

    if not md5s:
        return True

    from .cache import check_entry_shape

    for h in md5s:
        # A malformed record is refused before DVC is asked for anything.
        check_entry_shape(project_dir, h)

    if not has_remote_configured(project_dir):
        print("WARNING: DVC remote not configured, pull skipped.", file=sys.stderr)
        return False

    try:
        result = pull(md5s, project_dir)
        failed = getattr(result, "failed", None) or []
        if failed:
            print(
                f"WARNING: DVC pull reported {len(failed)} failures.",
                file=sys.stderr,
            )
            return False
    except Exception as exc:
        print(f"WARNING: DVC pull failed: {exc}", file=sys.stderr)
        return False
    # DVC skips an object the remote lacks without reporting it, and a
    # directory entry is only local with its manifest and every member.
    from .cache import entry_is_complete

    incomplete = [h for h in md5s if not entry_is_complete(project_dir, h)]
    if incomplete:
        print(
            f"WARNING: DVC pull left {len(incomplete)} entr"
            f"{'y' if len(incomplete) == 1 else 'ies'} incomplete in the "
            f"local cache: {', '.join(incomplete)}",
            file=sys.stderr,
        )
        return False
    return True


def probe_remote(project_dir: Path | str) -> tuple[bool, str]:
    """Ask the configured default DVC remote whether it answers.

    The probe for a non-local remote (S3, SSH, GCS, Azure, ...): DVC opens
    the remote's filesystem with the project's own ``.dvc/config`` (its
    endpoint and credentials) and checks that the remote's root, or the
    nearest parent of it, exists.  A fresh object-store archive has no
    objects under its prefix until the first push creates them, so the
    bucket answering is enough; DVC creates the root on push.

    A failure names the remote and the URL DVC itself loaded for it, which
    is the URL probed (``.dvc/config`` can drift from ``wf-canvas.toml``).

    Args:
        project_dir: Root directory of the wfc project (the DVC repo root).

    Returns:
        ``(True, "")`` when the remote answers and its root exists;
        ``(False, reason)`` otherwise, the reason naming the remote.
    """
    Repo, _ = _dvc()
    name = "default"
    label = f"'{name}'"
    try:
        with Repo(str(project_dir)) as repo:
            name = repo.config.get("core", {}).get("remote") or name
            url = repo.config.get("remote", {}).get(name, {}).get("url")
            label = f"'{name}' ({url})" if url else f"'{name}'"
            remote = repo.cloud.get_remote()
            path = remote.path
            while True:
                if remote.fs.exists(path):
                    return True, ""
                parent = remote.fs.parent(path)
                if not parent or parent == path:
                    break
                path = parent
            return False, (f"DVC remote {label} answered, but neither "
                           f"its root nor any parent of it exists")
    except Exception as exc:  # noqa: BLE001 -- every failure names the remote
        detail = str(exc) or type(exc).__name__
        return False, f"DVC remote {label} is not reachable: {detail}"


def has_remote_configured(project_dir: Path | str) -> bool:
    """Return True iff ``.dvc/config`` declares at least one remote.

    Pure INI parse -- never imports DVC.  Safe to call from hot paths
    (run_pipeline startup, the run record's push enqueue).

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        True if ``.dvc/config`` exists and has at least one
        ``[remote "..."]`` section; False otherwise.
    """
    config_path = layout.dvc_config_path(Path(project_dir))
    if not config_path.exists():
        return False
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except configparser.Error:
        return False
    return bool(remote_sections(parser))


def remote_name_of(section: str) -> str | None:
    """Return the remote name a ``.dvc/config`` section header declares.

    DVC writes a remote's header as ``[remote "name"]``; once DVC itself
    rewrites the file (``dvc remote modify``, ``dvc remote default``) its
    config writer quotes it as ``['remote "name"']``.  The legacy
    ``[remote.name]`` spelling is accepted too.  configparser keeps the
    header text verbatim, so all three reach here as written.

    Args:
        section: A section name as configparser reports it.

    Returns:
        The remote's name, or None when the section is not a remote.
    """
    text = section.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    if text.startswith('remote "') and text.endswith('"') and len(text) > 9:
        return text[len('remote "'):-1]
    if text.startswith("remote.") and len(text) > len("remote."):
        return text[len("remote."):]
    return None


def remote_sections(parser: configparser.ConfigParser) -> dict[str, str]:
    """Map each remote declared in a parsed ``.dvc/config`` to its header.

    The one reader of remote section headers: every spelling DVC writes is
    recognized by :func:`remote_name_of`, and the returned header is the
    section name to read or update that remote through.

    Args:
        parser: A ConfigParser that has read ``.dvc/config``.

    Returns:
        ``{remote name: section name as written}``, in file order.
    """
    found: dict[str, str] = {}
    for section in parser.sections():
        name = remote_name_of(section)
        if name is not None:
            found.setdefault(name, section)
    return found


class _EmptyResult:
    """Minimal TransferResult stand-in for the no-op case."""

    succeeded: list = []
    failed: list = []


def _empty_result() -> _EmptyResult:
    return _EmptyResult()
