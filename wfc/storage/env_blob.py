"""The environment-content blob: store it in the cache, return its fingerprint, read it back.

:func:`store_env_content` writes a blob and returns its md5; :func:`read_env_content`
reads one by that md5.  A malformed md5, or a path that would leave the cache,
raises :class:`InvalidEnvBlobHashError`; a blob the local cache lacks raises
:class:`EnvBlobNotFoundError`.
"""

from __future__ import annotations

from pathlib import Path

from .. import layout

_HEX32 = frozenset("0123456789abcdef")


class EnvBlobError(Exception):
    """Base error for :func:`read_env_content`."""


class InvalidEnvBlobHashError(EnvBlobError):
    """The md5 is not 32 lowercase hex characters, or its path leaves the cache."""


class EnvBlobNotFoundError(EnvBlobError):
    """No blob with this md5 is in the local cache."""


def store_env_content(content: str, project_dir: Path | str) -> str:
    """Write ``content`` to a temp file, hash it, cache it in DVC, return md5.

    The md5 returned is the environment fingerprint used as the 4th arg to
    :func:`wfc.identity.build_cache_key` and persisted as ``Run.env_fingerprint``.

    The blob is stored under ``.dvc/cache/files/md5/{first2}/{rest}`` so it
    can be retrieved later by the returned md5.

    Args:
        content: Deterministic env content blob (from
            :func:`capture_env_content`).
        project_dir: Root directory of the wfc project.

    Returns:
        32-character hex MD5 digest of ``content``.

    Raises:
        Whatever :func:`wfc.storage.cache.cache_file` raises — the temp file is
        always cleaned up, success OR failure.
    """
    import os
    import tempfile

    from ..identity import hash_file
    from .cache import cache_file

    # Write to a NamedTemporaryFile with delete=False so we control cleanup.
    # encoding=utf-8 and newline="" keep the bytes deterministic across
    # platforms; the caller is expected to provide canonical content.
    fd, tmp_path = tempfile.mkstemp(prefix="wfc-env-", suffix=".blob")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        md5 = hash_file(tmp_path)
        cache_file(tmp_path, md5, project_dir)
        return md5
    finally:
        # Clean up the temp file on ALL paths — success, hash_file failure,
        # cache_file failure.  Use try/except to stay defensive on Windows
        # where the file could be held open briefly.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def _valid_md5(md5: str) -> bool:
    """Return True if ``md5`` is a 32-char lowercase hex string."""
    return len(md5) == 32 and all(c in _HEX32 for c in md5)


def read_env_content(md5: str, project_dir: Path) -> str:
    """Read a cached env-content blob by md5, with a path-traversal guard.

    The read side of :func:`store_env_content`, shared by the canvas's
    ``GET .../blob/<md5>`` and ``GET .../packages`` routes.

    Args:
        md5: 32-char lowercase hex content hash.
        project_dir: Root directory of the wfc project (containing ``.dvc/``).

    Returns:
        The decoded blob text.

    Raises:
        InvalidEnvBlobHashError: The md5 is malformed, or its resolved path
            leaves the cache.
        EnvBlobNotFoundError: The blob is absent from the local cache.
    """
    if not _valid_md5(md5):
        raise InvalidEnvBlobHashError("malformed md5 (expect 32 lowercase hex)")

    cache_root = layout.dvc_cache_files_dir(project_dir.resolve()).resolve()
    blob_path = (cache_root / md5[:2] / md5[2:]).resolve()

    # Path-traversal guard: the resolved path must stay under cache_root.
    try:
        blob_path.relative_to(cache_root)
    except ValueError as exc:
        raise InvalidEnvBlobHashError("path traversal rejected") from exc

    if not blob_path.is_file():
        raise EnvBlobNotFoundError(f"blob not found: {md5}")

    return blob_path.read_text(encoding="utf-8")
