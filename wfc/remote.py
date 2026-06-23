"""DVC remote adapter (ADR-018).

This module is the ONLY place in wfc that imports from ``dvc.*``.  The
imports are deferred to first use (lazy) so the 4-second DVC import cost
is amortized over an entire pipeline run rather than paid at CLI startup.

Public API:
    push(hashes, project_dir) -> TransferResult-like
    pull(hashes, project_dir) -> TransferResult-like
    has_remote_configured(project_dir) -> bool

``has_remote_configured`` does a pure INI parse and never imports DVC,
so it can be called from hot paths (run_pipeline startup, run_step
step 7) without paying the import cost.
"""

from __future__ import annotations

import configparser
from collections.abc import Iterable
from pathlib import Path

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


def push(hashes: Iterable[str], project_dir: Path | str):
    """Push the given md5 content-hashes to the configured DVC remote.

    Args:
        hashes: Iterable of MD5 hex digests already present in the local
            ``.dvc/cache/files/md5/`` cache.
        project_dir: Root directory of the wfc project (the DVC repo root).

    Returns:
        DVC's ``TransferResult``-like object (has ``.succeeded`` and
        ``.failed`` lists).

    Raises:
        Any DVC exception escapes -- callers (the push worker) catch
        broadly to drive retry/backoff.
    """
    Repo, HashInfo = _dvc()
    hash_list = list(hashes)
    if not hash_list:
        return _empty_result()
    with Repo(str(project_dir)) as repo:
        objs = [HashInfo(name="md5", value=h) for h in hash_list]
        return repo.cloud.push(objs)


def pull(hashes: Iterable[str], project_dir: Path | str):
    """Pull the given md5 content-hashes from the configured DVC remote.

    Symmetric to :func:`push`.  Returns the same TransferResult-like
    object so callers can inspect ``.succeeded`` / ``.failed``.

    Args:
        hashes: Iterable of MD5 hex digests to retrieve.
        project_dir: Root directory of the wfc project.
    """
    Repo, HashInfo = _dvc()
    hash_list = list(hashes)
    if not hash_list:
        return _empty_result()
    with Repo(str(project_dir)) as repo:
        objs = [HashInfo(name="md5", value=h) for h in hash_list]
        return repo.cloud.pull(objs)


def has_remote_configured(project_dir: Path | str) -> bool:
    """Return True iff ``.dvc/config`` declares at least one remote.

    Pure INI parse -- never imports DVC.  Safe to call from hot paths
    (run_pipeline startup, run_step step 7).

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        True if ``.dvc/config`` exists and has at least one
        ``[remote "..."]`` section; False otherwise.
    """
    config_path = Path(project_dir) / ".dvc" / "config"
    if not config_path.exists():
        return False
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except configparser.Error:
        return False
    for section in parser.sections():
        # configparser preserves the original section name including
        # the quoted sub-name.  DVC writes either ``remote "name"`` or
        # legacy ``remote.name``; accept both.
        if section.startswith('remote "') or section.startswith("remote."):
            return True
    return False


class _EmptyResult:
    """Minimal TransferResult stand-in for the no-op case."""

    succeeded: list = []
    failed: list = []


def _empty_result() -> _EmptyResult:
    return _EmptyResult()
