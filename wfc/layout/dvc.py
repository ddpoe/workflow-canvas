"""DVC shapes: the local cache under the project and the remote's location.

Catalog rows 12 (cache entry), ``dvc-remote-path`` and ``default-archive-url``
of ``docs/system/layout.json``. The remote and archive derivations take the
config value and the user's home as values — reading the config and
``Path.home()`` stay with the callers.
"""
from __future__ import annotations

from pathlib import Path

from .tree import STATE_DIR_NAME

#: The DVC state dir under the project root.
DVC_DIR_NAME = ".dvc"
#: The subdirectory of the user's home holding default per-project archives.
ARCHIVES_DIR_NAME = "archives"


def dvc_dir(root: Path) -> Path:
    """Return ``<root>/.dvc/``."""
    return Path(root) / DVC_DIR_NAME


def dvc_config_path(root: Path) -> Path:
    """Return DVC's own config file ``<root>/.dvc/config``."""
    return dvc_dir(root) / "config"


def dvc_cache_dir(root: Path) -> Path:
    """Return the local content-addressed cache ``<root>/.dvc/cache/`` — the mount root."""
    return dvc_dir(root) / "cache"


def dvc_cache_files_dir(root: Path) -> Path:
    """Return the md5-keyed file store ``<root>/.dvc/cache/files/md5/``."""
    return dvc_cache_dir(root) / "files" / "md5"


def dvc_cache_entry(root: Path, md5: str) -> Path:
    """Return the cache entry for a hash: ``files/md5/<md5[:2]>/<md5[2:]>``.

    Args:
        root: The project root.
        md5: The 32-hex content hash; it splits two-then-thirty.

    Returns:
        The entry's path under the cache.
    """
    return dvc_cache_files_dir(root) / md5[:2] / md5[2:]


def remote_path_from_url(url: str, project_dir: Path) -> Path | None:
    """Normalize a configured remote URL to a local filesystem path.

    A ``file://`` prefix is stripped; any other scheme is not a filesystem
    path and yields ``None`` (callers route through the remote layer); a
    relative path is project-relative; the result is resolved absolute.

    Args:
        url: The config ``url`` value (or the legacy ``remote_path``).
        project_dir: The project root that anchors a relative value.

    Returns:
        The absolute local path, or ``None`` for a non-local scheme or an
        empty value.
    """
    if not url:
        return None
    if url.startswith("file://"):
        url = url[len("file://"):]
    if "://" in url:
        return None
    rp = Path(url)
    if not rp.is_absolute():
        rp = Path(project_dir) / rp
    return rp.resolve()


def default_archive_url(home: Path, project_dir: Path) -> str:
    """Return the default archive location ``<home>/.wfc/archives/<project-name>``.

    A plain absolute path with forward slashes, never a ``file://`` URL:
    DVC's config schema rejects the ``file://C:/...`` form drive-letter
    paths would produce, and backslashes are escape characters inside the
    config's quoted strings.

    Args:
        home: The user's home directory, handed in as a value.
        project_dir: The project root; its name is the archive subdirectory.

    Returns:
        The absolute POSIX-rendered path string.
    """
    return (Path(home) / STATE_DIR_NAME / ARCHIVES_DIR_NAME
            / Path(project_dir).name).resolve().as_posix()
