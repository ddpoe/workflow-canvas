"""Filesystem primitives for views of cache objects: the link tiers and their probe.

A view of a cache object is a reflink, a hardlink or a copy, tried in that
order by :func:`wfc.storage.cache.make_view`; this module holds the
platform calls it makes, through the standard library and ``ctypes`` only:

- :func:`reflink`: the FICLONE ioctl on Linux, ``clonefile`` on macOS; on
  any other platform (and on a filesystem that cannot clone) it raises
  ``OSError``. There is no reflink tier on Windows.
- :func:`hardlink`: ``os.link``.
- :func:`replace_over` and :func:`delete_file`: rename over, and delete, a
  file without clearing its read-only attribute. A hardlink shares its
  inode, mode and attributes with the cache object, so on Windows clearing
  the attribute to remove a view would clear it on the cache object too;
  these use the ignore-read-only flags of ``SetFileInformationByHandle``
  instead. On POSIX neither needs the file's own write bit.
- :func:`probe_pair`: which tier works from one directory to another, with
  a scratch file, and why a copy is the fallback when it is.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import layout

#: A view sharing the cache object's blocks copy-on-write (its own inode).
TIER_REFLINK = "reflink"
#: A view sharing the cache object's inode.
TIER_HARDLINK = "hardlink"
#: A view holding bytes of its own.
TIER_COPY = "copy"

#: Linux's FICLONE ioctl request: _IOW(0x94, 9, int).
_FICLONE = 0x40049409


def token() -> str:
    """A name part unique to this process and thread, for temporary files."""
    return f"{os.getpid()}.{threading.get_ident()}"


def _ficlone(src: Path, dst: Path) -> None:
    """Clone ``src`` to a new file ``dst`` through the FICLONE ioctl (Linux)."""
    if sys.platform == "win32":
        raise OSError(errno.ENOTSUP, "no FICLONE on Windows", str(src))
    import fcntl

    with open(src, "rb") as fin:
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            fcntl.ioctl(fd, _FICLONE, fin.fileno())
        except OSError:
            os.close(fd)
            os.unlink(dst)
            raise
        os.close(fd)


def _clonefile(src: Path, dst: Path) -> None:
    """Clone ``src`` to a new file ``dst`` through ``clonefile(2)`` (macOS)."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        clonefile = libc.clonefile
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "clonefile is not available") from exc
    clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    clonefile.restype = ctypes.c_int
    if clonefile(os.fsencode(src), os.fsencode(dst), 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(src))


def reflink(src: Path | str, dst: Path | str) -> None:
    """Make ``dst`` a reflink of ``src``: its own inode over the same blocks.

    Args:
        src: The file to clone.
        dst: The new file (must not exist).

    Raises:
        OSError: The platform has no reflink call (Windows), or the
            filesystem cannot clone (ext4, NTFS, across filesystems).
    """
    src, dst = Path(src), Path(dst)
    if sys.platform.startswith("linux"):
        _ficlone(src, dst)
    elif sys.platform == "darwin":
        _clonefile(src, dst)
    else:
        raise OSError(errno.ENOTSUP, f"no reflink on {sys.platform}", str(src))


def hardlink(src: Path | str, dst: Path | str) -> None:
    """Make ``dst`` a hardlink of ``src``.

    Args:
        src: The file to link.
        dst: The new link (must not exist).

    Raises:
        OSError: The filesystem refuses the link, or ``dst`` is on another
            filesystem.
    """
    os.link(src, dst)


# --- Windows: rename over and delete without clearing read-only -----------

_win32 = None

#: FILE_RENAME_FLAG_REPLACE_IF_EXISTS | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
_RENAME_FLAGS = 0x1 | 0x2 | 0x40
#: FILE_DISPOSITION_FLAG_DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
_DELETE_FLAGS = 0x1 | 0x2 | 0x10


def _kernel32():
    """kernel32's CreateFileW, SetFileInformationByHandle and CloseHandle, typed once."""
    if sys.platform != "win32":
        raise OSError(errno.ENOTSUP, "kernel32 is Windows-only")
    global _win32
    if _win32 is None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   wintypes.LPVOID, wintypes.DWORD]
        k32.SetFileInformationByHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _win32 = (ctypes, wintypes, k32)
    return _win32


def _win_set_information(path: Path, info_class: int, info) -> None:
    """Open ``path`` for DELETE and apply one SetFileInformationByHandle call."""
    ctypes, _wintypes, k32 = _kernel32()
    handle = k32.CreateFileW(os.path.abspath(os.fspath(path)),
                             0x00010000 | 0x80,      # DELETE | FILE_READ_ATTRIBUTES
                             0x1 | 0x2 | 0x4,        # share read, write, delete
                             None,
                             3,                      # OPEN_EXISTING
                             0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                             None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not k32.SetFileInformationByHandle(handle, info_class, ctypes.byref(info),
                                              ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(handle)


def _win_rename_ignoring_read_only(src: Path, dst: Path) -> None:
    """Rename ``src`` over a read-only ``dst`` without touching its attribute."""
    import ctypes
    from ctypes import wintypes
    name = os.path.abspath(os.fspath(dst))

    class FileRenameInfoEx(ctypes.Structure):
        _fields_ = [("Flags", wintypes.ULONG),
                    ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD),
                    ("FileName", ctypes.c_wchar * (len(name) + 1))]

    info = FileRenameInfoEx(_RENAME_FLAGS, None, len(name) * 2, name)
    _win_set_information(src, 22, info)  # FileRenameInfoEx


def _win_delete_ignoring_read_only(path: Path) -> None:
    """Delete a read-only file without clearing its attribute."""
    import ctypes
    from ctypes import wintypes

    class FileDispositionInfoEx(ctypes.Structure):
        _fields_ = [("Flags", wintypes.ULONG)]

    _win_set_information(path, 21, FileDispositionInfoEx(_DELETE_FLAGS))  # FileDispositionInfoEx


def _shares_inode(path: Path) -> bool:
    """Whether a file has more than one link, so its attribute is not its own."""
    try:
        return os.lstat(path).st_nlink > 1
    except OSError:
        return False


def replace_over(src: Path | str, dst: Path | str) -> None:
    """Rename ``src`` over ``dst`` in one step, never deleting ``dst`` first.

    On Windows a read-only ``dst`` refuses ``os.replace``; it is replaced
    with the ignore-read-only rename instead, so the attribute is never
    cleared on an inode ``dst`` shares with a cache object. Where that call
    is unsupported (before Windows 10 1809, or a volume without it), a
    ``dst`` with one link is made writable and replaced; one sharing its
    inode is refused.

    Args:
        src: The file to move.
        dst: Its destination (a file, a symlink, or absent).

    Raises:
        OSError: The rename failed.
    """
    src, dst = Path(src), Path(dst)
    try:
        os.replace(src, dst)
        return
    except PermissionError:
        if sys.platform != "win32" or not os.path.lexists(dst):
            raise
    try:
        _win_rename_ignoring_read_only(src, dst)
    except OSError:
        if _shares_inode(dst):
            raise
        os.chmod(dst, 0o644)
        os.replace(src, dst)


def delete_file(path: Path | str) -> None:
    """Delete a file or symlink without clearing a read-only attribute it shares.

    On POSIX ``unlink`` needs no write bit on the file. On Windows a
    read-only file is deleted with the ignore-read-only disposition; where
    that is unsupported, a file with one link is made writable and deleted,
    and one sharing its inode is refused.

    Args:
        path: The file to delete; an absent one is left absent.

    Raises:
        OSError: The delete failed.
    """
    path = Path(path)
    try:
        os.unlink(path)
        return
    except FileNotFoundError:
        return
    except PermissionError:
        if sys.platform != "win32":
            raise
    try:
        _win_delete_ignoring_read_only(path)
    except OSError:
        if _shares_inode(path):
            raise
        os.chmod(path, 0o644)
        os.unlink(path)


def delete_tree(path: Path | str) -> None:
    """Delete a directory tree whose files may be views, never chmodding a file.

    Directories are made writable (none is shared with a cache object);
    every file goes through :func:`delete_file`, so a hardlink's read-only
    attribute stays on the cache object. Symlinks are removed, never
    followed.

    Args:
        path: The directory (or a file or symlink) to delete.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        delete_file(path)
        return
    os.chmod(path, 0o755)
    for root, dirs, _files in os.walk(path, topdown=True):
        for name in dirs:
            sub = os.path.join(root, name)
            if not os.path.islink(sub):
                os.chmod(sub, 0o755)
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            delete_file(os.path.join(root, name))
        for name in dirs:
            sub = os.path.join(root, name)
            if os.path.islink(sub):
                delete_file(sub)
            else:
                os.rmdir(sub)
    os.rmdir(path)


# --- the probe -------------------------------------------------------------

@dataclass(frozen=True)
class TierProbe:
    """Which view tier works from one directory to another.

    Attributes:
        tier: :data:`TIER_REFLINK`, :data:`TIER_HARDLINK` or :data:`TIER_COPY`.
        reason: For a copy, why no link was made (another filesystem, or
            the filesystem refusing links); None otherwise.
    """

    tier: str
    reason: str | None = None


def link_refusal(exc: OSError, src_dir: Path, dst_dir: Path) -> str:
    """Say why a link between two directories was refused.

    Args:
        exc: The hardlink's error.
        src_dir: The directory linked from.
        dst_dir: The directory linked into.

    Returns:
        ``on different filesystems`` for a cross-device error (or two
        directories on different devices), else the filesystem's refusal.
    """
    try:
        other_device = os.stat(src_dir).st_dev != os.stat(dst_dir).st_dev
    except OSError:
        other_device = False
    if exc.errno == errno.EXDEV or other_device:
        return "they are on different filesystems"
    return f"the filesystem refuses links ({exc.strerror or exc})"


def probe_pair(src_dir: Path | str, dst_dir: Path | str) -> TierProbe:
    """Try each tier with a scratch file from ``src_dir`` into ``dst_dir``.

    Both scratch files are removed again; nothing else is written.

    Args:
        src_dir: The directory views are made from (the cache's); it exists.
        dst_dir: The directory views are made in; it exists.

    Returns:
        The first tier that worked and, for a copy, why no link was made.

    Raises:
        OSError: The scratch file could not be written at all.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    src = layout.view_probe_path(src_dir, f"{token()}.src")
    dst = layout.view_probe_path(dst_dir, f"{token()}.dst")
    src.write_bytes(b"wfc")
    try:
        try:
            reflink(src, dst)
            return TierProbe(TIER_REFLINK)
        except OSError:
            delete_file(dst)
        try:
            hardlink(src, dst)
            return TierProbe(TIER_HARDLINK)
        except OSError as exc:
            delete_file(dst)
            reason = link_refusal(exc, src_dir, dst_dir)
        shutil.copyfile(src, dst)
        return TierProbe(TIER_COPY, reason)
    finally:
        delete_file(dst)
        delete_file(src)
