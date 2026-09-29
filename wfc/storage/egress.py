"""Egress: copy a cache entry out to a user-owned destination, and make the copy writable."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _chmod_user_writable(path: Path) -> None:
    """Recursively add the owner-write bit to an exported copy.

    ``shutil.copy2``/``copytree`` preserve the cache's read-only mode bits,
    so every exported copy must be made writable afterwards — a read-only
    "mutable copy" silently breaks the export feature's whole point.

    Args:
        path: The exported file or directory.
    """
    import stat as _stat

    def _add_w(p) -> None:
        os.chmod(p, os.stat(p).st_mode | _stat.S_IWUSR)

    if path.is_dir():
        _add_w(path)
        for root, dirs, files in os.walk(path):
            for name in dirs:
                _add_w(os.path.join(root, name))
            for name in files:
                _add_w(os.path.join(root, name))
    else:
        _add_w(path)


def copy_out(src: Path, dest: Path, force: bool) -> None:
    """Copy a cache entry OUT to a user destination and make it writable.

    Mirrors ``restore_from_cache`` copy semantics (copy2 for files,
    copytree for directories). When ``force`` and ``dest`` exists, the
    stale dest is made writable and removed first (the Windows read-only
    attribute would otherwise block the replace).

    Args:
        src: Resolved local path (never mutated): a file entry's cache
            address, or a directory entry's read-only checkout.
        dest: Destination path owned by the user.
        force: Replace an existing destination.
    """
    if dest.exists() and force:
        from .cache import _make_writable
        _make_writable(dest)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    _chmod_user_writable(dest)
