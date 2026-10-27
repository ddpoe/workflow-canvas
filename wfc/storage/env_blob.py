"""The environment-content blob: store it in the cache and return its fingerprint.

:func:`store_env_content` writes a blob and returns its md5.
"""

from __future__ import annotations

from pathlib import Path


def store_env_content(
    content: str, project_dir: Path | str, kind: str | None = None
) -> str:
    """Write ``content`` to a temp file, hash it, cache it in DVC, return md5.

    The md5 returned is the environment fingerprint used as the 4th arg to
    :func:`wfc.identity.build_cache_key` and persisted as ``Run.env_fingerprint``.

    The blob is stored under ``.dvc/cache/files/md5/{first2}/{rest}`` so it
    can be retrieved later by the returned md5. When *kind* is given, the
    same call then enters the blob row (:func:`wfc.storage.blobs.enter_blob`,
    insert if absent), so the row names bytes already in the cache and the
    push worker carries them to the remote.

    Args:
        content: Deterministic env content blob (from
            :func:`capture_env_content`), or any canonical text to keep by
            its content (a pipeline document's canonical JSON).
        project_dir: Root directory of the wfc project.
        kind: What the bytes are (``env-fingerprint``, ``pipeline-document``),
            or None to cache them with no blob row.

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
        if kind is not None:
            from .blobs import enter_blob

            enter_blob(md5, kind, project_dir, size=len(content.encode("utf-8")))
        return md5
    finally:
        # Clean up the temp file on ALL paths — success, hash_file failure,
        # cache_file failure.  Use try/except to stay defensive on Windows
        # where the file could be held open briefly.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
