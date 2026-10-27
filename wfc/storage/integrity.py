"""Integrity: the one check that a thing is what wfc recorded.

Every read of a restored sample, a pre-archive output, a cache object or a
checkout asks :func:`check_entry` before it uses the bytes. The check owns
the rule end to end:

- **Which stat fields** a platform records (:func:`stat_fields`): size,
  ``st_mtime_ns`` and a change time that every write and chmod moves:
  ``st_ctime_ns`` on Linux and macOS, NTFS's ChangeTime on Windows
  (:func:`ntfs_change_time_ns`, beside the file index ``st_ino``; a FAT32
  or exFAT volume keeps none, and the other fields decide). A directory's
  record is each member's fields, from Identity's stat-only listing
  (:func:`entry_record`).
- **Which attribute names** a file carries (:func:`extended_attribute_names`),
  recorded beside its stat fields. The one comparison
  (:func:`records_match`) counts a change time moved while every other
  field stayed as a metadata write, not a change, when the name set moved
  with it: an on-access scanner's tag (Windows Defender's
  ``$KERNEL.PURGE.*`` EAs). The same move with the same names is a
  change; a record holding no names compares on the rest exactly.
- **The setting** ``[integrity] inputs`` (:func:`inputs_setting`), read
  through Persistence's config reader: under ``stat-then-hash`` matching
  stat fields vouch for the read and a mismatch re-hashes; under ``hash``
  every read with a recorded hash re-hashes. The setting never enters a
  cache key.
- **The stamp** (:func:`read_stamp`, :func:`write_stamp`): a machine-local
  JSON file at a Layout path recording the hash it vouches for and the stat
  record a verified read last saw. Plain and unsigned; written atomically.
  A missing, unreadable or foreign stamp means re-hash, then stamp, never
  trust.
- **The re-hash**, through Identity's ``hash_path``: Storage never computes
  its own md5.

With no recorded hash (a pre-archive output collected under the default),
nothing can confirm a stat mismatch, so any difference from the recorded
stat fields is a change.

A run's outputs before the archive pass reaches them:

- :func:`record_output` is collect's half: it makes an output read-only,
  then takes its stat record and writes a directory output's member stamp
  (``layout.output_members_stamp``). Collect writes the row's stat columns
  from it; nothing else writes them.
- :func:`check_output` is every reader's half: the output against the
  record collect wrote (the row's own fields, or the member stamp), and
  against collect's hash when collect took one under ``hash``. A row from
  before this release is compared on the size and float mtime it carries,
  and a row with neither on existence alone.
- :func:`mark_changed` writes the one-way ``changed`` mark the first time a
  reader finds an output changed: a compare-and-set, never a rewrite of the
  row's stat fields.
"""

from __future__ import annotations

import errno
import json
import os
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from axiom_annotations import Step, task

from .. import layout
from ..identity import DirectoryContentError, directory_listing, hash_path
from ..persistence import (
    INTEGRITY_INPUTS_HASH,
    INTEGRITY_INPUTS_STAT_THEN_HASH,
    OutputIntegrity,
    read_config,
)
from ..persistence import project_root as get_project_root

if TYPE_CHECKING:
    from ..persistence import RunOutput

#: The entry is what wfc recorded.
INTACT = "intact"
#: The entry exists but is not what wfc recorded (or cannot be confirmed).
CHANGED = "changed"
#: Nothing is at the entry's path.
MISSING = "missing"


@dataclass(frozen=True)
class IntegrityVerdict:
    """What :func:`check_entry` found.

    Attributes:
        status: :data:`INTACT`, :data:`CHANGED` or :data:`MISSING`.
        hashed: Whether the check read the entry's bytes to decide.
    """

    status: str
    hashed: bool

    @property
    def intact(self) -> bool:
        """Whether the entry is what wfc recorded."""
        return self.status == INTACT


#: FILETIME (100 ns ticks since 1601-01-01) of the Unix epoch.
_FILETIME_EPOCH = 116_444_736_000_000_000


def change_time_from_filetime(ticks: int) -> int | None:
    """An NTFS ChangeTime, as FILE_BASIC_INFO carries it, in ns since the epoch.

    Args:
        ticks: The ChangeTime in 100 ns ticks since 1601-01-01.

    Returns:
        Nanoseconds since the Unix epoch, or None for 0: a volume that does
        not keep a change time (FAT32, exFAT) reports 0.
    """
    if ticks == 0:
        return None
    return (ticks - _FILETIME_EPOCH) * 100


_win32_api = None


def _kernel32():
    """kernel32's CreateFileW, GetFileInformationByHandleEx and CloseHandle, typed once."""
    if sys.platform != "win32":
        raise OSError("kernel32 is Windows-only")
    global _win32_api
    if _win32_api is None:
        import ctypes
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [("CreationTime", ctypes.c_int64),
                        ("LastAccessTime", ctypes.c_int64),
                        ("LastWriteTime", ctypes.c_int64),
                        ("ChangeTime", ctypes.c_int64),
                        ("FileAttributes", wintypes.DWORD)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     wintypes.LPVOID, wintypes.DWORD]
        k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _win32_api = (ctypes, k32, FileBasicInfo)
    return _win32_api


def _long_path(path: str) -> str:
    """A Win32 path CreateFileW accepts past MAX_PATH."""
    if path.startswith("\\\\?\\") or len(path) < 260:
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def ntfs_change_time_ns(path: Path | str) -> int | None:
    """A file's NTFS ChangeTime, read through an attribute-only handle.

    NTFS (and ReFS) move ChangeTime on every data write and every metadata
    change, as POSIX moves ``st_ctime``, and no ordinary API sets it. The
    handle asks for ``FILE_READ_ATTRIBUTES`` only and shares read, write
    and delete, so it never blocks or is blocked by a reader or a writer;
    ``FILE_FLAG_BACKUP_SEMANTICS`` lets it open a directory too.

    Args:
        path: The file or directory.

    Returns:
        The ChangeTime in ns since the Unix epoch, or None when the volume
        keeps none (FAT32 and exFAT report 0), the call fails, or the
        platform is not Windows.
    """
    try:
        _kernel32()
    except (AttributeError, ImportError, OSError):
        return None
    handle = _ntfs_open(path, _FILE_READ_ATTRIBUTES)
    if handle is None:
        return None
    try:
        return _ntfs_change_time_of(handle)
    finally:
        _kernel32()[1].CloseHandle(handle)


#: CreateFileW access rights: the attributes (ChangeTime) and the EAs.
_FILE_READ_ATTRIBUTES = 0x80
_FILE_READ_EA = 0x8


def _ntfs_open(path: Path | str, access: int):
    """An attribute-only handle on a file or directory, sharing everything; None on failure."""
    ctypes, k32, _ = _kernel32()
    handle = k32.CreateFileW(_long_path(os.path.abspath(os.fspath(path))),
                             access,
                             0x1 | 0x2 | 0x4,  # FILE_SHARE_READ | WRITE | DELETE
                             None,
                             3,             # OPEN_EXISTING
                             0x02000000,    # FILE_FLAG_BACKUP_SEMANTICS
                             None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None
    return handle


def _ntfs_change_time_of(handle) -> int | None:
    """The ChangeTime of an open handle (FileBasicInfo), or None."""
    ctypes, k32, file_basic_info = _kernel32()
    info = file_basic_info()
    if not k32.GetFileInformationByHandleEx(handle, 0,  # FileBasicInfo
                                            ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return change_time_from_filetime(info.ChangeTime)


#: The stat-record key holding an entry's extended-attribute names, sorted.
XATTR_NAMES = "xattr_names"

#: NtQueryEaFile statuses that mean the entry has no EAs: none on the file,
#: none left to return, or a volume without EA support (FAT32, exFAT).
_NT_NO_EAS = frozenset({0xC0000052,   # STATUS_NO_EAS_ON_FILE
                        0x80000012,   # STATUS_NO_MORE_EAS
                        0xC000004F})  # STATUS_EAS_NOT_SUPPORTED
#: NtQueryEaFile statuses that ask for a larger buffer.
_NT_GROW = frozenset({0x80000005,     # STATUS_BUFFER_OVERFLOW
                      0xC0000023})    # STATUS_BUFFER_TOO_SMALL
#: An entry's EAs total at most 64 KiB packed; the chain read back adds
#: alignment, so the second buffer always holds them.
_EA_BUFFER_SIZES = (4096, 1 << 17)

_nt_query_ea = None


def _ntdll_query_ea():
    """The NtQueryEaFile call from ntdll, typed once."""
    if sys.platform != "win32":
        raise OSError("ntdll is Windows-only")
    global _nt_query_ea
    if _nt_query_ea is None:
        import ctypes
        from ctypes import wintypes

        query = ctypes.WinDLL("ntdll").NtQueryEaFile
        query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.ULONG, wintypes.BOOLEAN, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.c_void_p, wintypes.BOOLEAN]
        query.restype = ctypes.c_long
        _nt_query_ea = query
    return _nt_query_ea


def ea_chain_names(raw: bytes) -> list[str]:
    """The names in a FILE_FULL_EA_INFORMATION chain, as NtQueryEaFile returns it.

    Each entry is ``NextEntryOffset`` (ULONG), ``Flags`` (UCHAR),
    ``EaNameLength`` (UCHAR), ``EaValueLength`` (USHORT), then the name;
    an offset of 0 ends the chain.

    Args:
        raw: The buffer NtQueryEaFile filled.

    Returns:
        The distinct names, sorted.
    """
    names: set[str] = set()
    offset = 0
    while offset + 8 <= len(raw):
        next_offset, _flags, name_length, _value_length = struct.unpack_from(
            "<IBBH", raw, offset)
        names.add(raw[offset + 8:offset + 8 + name_length].decode("latin-1"))
        if next_offset == 0:
            break
        offset += next_offset
    return sorted(names)


def _ntfs_ea_names_of(handle) -> list[str] | None:
    """The EA names of a handle opened with FILE_READ_EA; [] for none, None on failure."""
    import ctypes

    query = _ntdll_query_ea()
    for size in _EA_BUFFER_SIZES:
        buf = ctypes.create_string_buffer(size)
        status_block = (ctypes.c_void_p * 2)()
        status = query(handle, ctypes.byref(status_block), buf, size,
                       False, None, 0, None, True) & 0xFFFFFFFF
        if status in _NT_NO_EAS:
            return []
        if status in _NT_GROW:
            continue
        if status != 0:
            return None
        return ea_chain_names(buf.raw)
    return None


def _ntfs_metadata(path: Path | str) -> tuple[int | None, list[str] | None]:
    """A file's EA names and its NTFS ChangeTime, through one handle.

    The names are read first: an EA written between the two reads moves
    the ChangeTime read after it, so the record never pairs the old
    ChangeTime with the new names. Where the handle cannot have
    ``FILE_READ_EA``, the ChangeTime is still read, and no name set.

    Args:
        path: The file or directory.

    Returns:
        ``(change_time_ns, names)``: either None when it cannot be read, or
        both None off Windows.
    """
    try:
        _kernel32()
        _ntdll_query_ea()
    except (AttributeError, ImportError, OSError):
        return None, None
    handle = _ntfs_open(path, _FILE_READ_ATTRIBUTES | _FILE_READ_EA)
    if handle is None:
        return ntfs_change_time_ns(path), None
    try:
        names = _ntfs_ea_names_of(handle)
        return _ntfs_change_time_of(handle), names
    finally:
        _kernel32()[1].CloseHandle(handle)


#: errno values that mean a filesystem keeps no extended attributes.
_XATTR_UNSUPPORTED = frozenset(
    code for code in (getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None))
    if code is not None)

_darwin_listxattr_fn = None


def _darwin_listxattr(path: Path | str) -> list[str] | None:
    """The names through libc ``listxattr`` on macOS, which has no ``os.listxattr``."""
    import ctypes

    global _darwin_listxattr_fn
    if _darwin_listxattr_fn is None:
        try:
            fn = ctypes.CDLL(None, use_errno=True).listxattr
        except (AttributeError, OSError):
            return None
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
        fn.restype = ctypes.c_ssize_t
        _darwin_listxattr_fn = fn
    raw_path = os.fsencode(os.fspath(path))
    for _attempt in range(3):
        size = _darwin_listxattr_fn(raw_path, None, 0, 0)
        if size < 0:
            return [] if ctypes.get_errno() in _XATTR_UNSUPPORTED else None
        if size == 0:
            return []
        buf = ctypes.create_string_buffer(size)
        got = _darwin_listxattr_fn(raw_path, buf, size, 0)
        if got >= 0:
            return sorted({name.decode("utf-8", "surrogateescape")
                           for name in buf.raw[:got].split(b"\0") if name})
        code = ctypes.get_errno()
        if code != errno.ERANGE:  # ERANGE: the list grew between the calls
            return [] if code in _XATTR_UNSUPPORTED else None
    return None


def extended_attribute_names(path: Path | str) -> list[str] | None:
    """The names of a file's extended attributes, reading no value.

    ``os.listxattr`` where Python has it (Linux), libc ``listxattr`` on
    macOS, and NTFS EAs through ``NtQueryEaFile`` on Windows.

    Args:
        path: The file or directory.

    Returns:
        The names, sorted; ``[]`` for none or a filesystem that keeps none;
        None when the read failed otherwise or the platform has no call.
    """
    if hasattr(os, "listxattr"):
        try:
            return sorted(os.listxattr(path))
        except OSError as exc:
            return [] if exc.errno in _XATTR_UNSUPPORTED else None
    if sys.platform == "darwin":
        return _darwin_listxattr(path)
    if sys.platform == "win32":
        return _ntfs_metadata(path)[1]
    return None


def stat_fields(st: os.stat_result, platform: str | None = None, *,
                path: Path | str | None = None) -> dict:
    """The stat fields this platform records for one file.

    Size and ``st_mtime_ns`` everywhere; ``st_ctime_ns`` (the inode change
    time, which any write or chmod moves) on Linux and macOS. On Windows,
    where ``st_ctime`` is the creation time: the file index (``st_ino``),
    which a copy or a replace changes, and NTFS's ChangeTime, read from
    ``path`` (:func:`ntfs_change_time_ns`) and kept as ``ctime_ns``. A
    volume that keeps no change time, or a failed read, leaves ``ctime_ns``
    out, and the other fields decide; a record with one compared with a
    read without one is a mismatch.

    With ``path``, the file's extended-attribute names are recorded too
    (:func:`extended_attribute_names`, as ``xattr_names``), so a change
    time moved by an attribute write alone is told apart from a change
    (:func:`records_match`); a failed read records none.

    Args:
        st: The file's ``os.stat`` result.
        platform: A ``sys.platform`` value; the running platform when None.
        path: The file ``st`` describes; its attribute names and, on
            Windows, its change time are read from it, and with None
            neither is recorded.

    Returns:
        The fields by name: ``size``, ``mtime_ns``, ``ctime_ns`` (when the
        platform has one), on Windows ``file_index``, and ``xattr_names``
        when they were read.
    """
    platform = sys.platform if platform is None else platform
    fields: dict = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    names = None
    if platform == "win32":
        fields["file_index"] = st.st_ino
        change = None
        if path is not None:
            change, names = _ntfs_metadata(path)
        if change is not None:
            fields["ctime_ns"] = change
    else:
        fields["ctime_ns"] = st.st_ctime_ns
        if path is not None:
            names = extended_attribute_names(path)
    if names is not None:
        fields[XATTR_NAMES] = names
    return fields


def entry_record(path: Path | str) -> dict:
    """The stat record of a file or directory as it is now, reading no byte.

    Args:
        path: The file or directory.

    Returns:
        ``{"file": fields}`` for a file, or ``{"files": {relpath: fields}}``
        for a directory, one entry per member of Identity's listing.

    Raises:
        OSError: The path cannot be stat-ed.
        DirectoryContentError: A directory holds a symlink, a case-only
            name collision, or no files.
    """
    path = Path(path)
    if path.is_dir():
        listing = directory_listing(path)
        return {"files": {rel: stat_fields(st, path=path / rel)
                          for rel, st in listing.members}}
    return {"file": stat_fields(path.stat(), path=path)}


def record_keeps_change_time(record: dict | None) -> bool:
    """Whether a stat record holds a change time for every file it names.

    A chmod moves the change time, so a record that holds one shows any
    chmod since it was taken. A volume that keeps no change time (FAT32,
    exFAT) leaves it out, and such a record cannot show one.

    Args:
        record: A record as :func:`entry_record` shapes it, or None.

    Returns:
        True when the record names at least one file and every one carries
        ``ctime_ns``.
    """
    if not record:
        return False
    members = [record["file"]] if "file" in record else list(record.get("files", {}).values())
    return bool(members) and all("ctime_ns" in fields for fields in members)


def _fields_match(known: dict, current: dict) -> bool:
    """Whether one file's recorded stat fields vouch for its fields now.

    Equal fields match. The attribute names never decide on their own:
    with either side's missing, the rest is compared exactly. With both,
    a ChangeTime that moved while every other field stayed is a metadata
    write when the name set changed (an on-access scanner's tag), and a
    change when it did not.
    """
    if known == current:
        return True
    rest_known = {k: v for k, v in known.items() if k != XATTR_NAMES}
    rest_now = {k: v for k, v in current.items() if k != XATTR_NAMES}
    if rest_known == rest_now:
        return True
    names_known, names_now = known.get(XATTR_NAMES), current.get(XATTR_NAMES)
    if names_known is None or names_now is None or set(names_known) == set(names_now):
        return False
    if "ctime_ns" not in rest_known or "ctime_ns" not in rest_now:
        return False
    rest_known.pop("ctime_ns")
    rest_now.pop("ctime_ns")
    return rest_known == rest_now


def records_match(known: dict, current: dict) -> bool:
    """Whether a recorded stat record vouches for an entry's record now.

    The one stat comparison every check makes. The records must name the
    same file or the same members, and each file's fields must match: equal,
    or equal but for a ChangeTime moved by an extended-attribute write that
    changed the file's attribute names. A record holding no names, made
    before names were recorded or where the read failed, is compared on its
    other fields exactly.

    Args:
        known: The recorded record, shaped as :func:`entry_record` shapes it.
        current: The record taken now.

    Returns:
        True when the recorded record vouches for the entry.
    """
    if known == current:
        return True
    if set(known) != set(current):
        return False
    if "file" in known:
        pairs = [(known["file"], current["file"])]
    else:
        recorded_files, files_now = known.get("files"), current.get("files")
        if (not isinstance(recorded_files, dict) or not isinstance(files_now, dict)
                or set(recorded_files) != set(files_now)):
            return False
        pairs = [(recorded_files[rel], files_now[rel]) for rel in recorded_files]
    return all(isinstance(a, dict) and isinstance(b, dict) and _fields_match(a, b)
               for a, b in pairs)


def read_stamp(stamp: Path | str, recorded_hash: str | None = None) -> dict | None:
    """The stat record a stamp vouches for, when it vouches for this hash.

    Args:
        stamp: The stamp file.
        recorded_hash: The hash the reader expects the stamp to record, or
            None for a stamp that records no hash (a directory output's
            member stamp).

    Returns:
        The stamp's record (as :func:`entry_record` shapes it), or None when
        the stamp is missing, unreadable, malformed or for another hash.
    """
    try:
        data = json.loads(Path(stamp).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("hash") != recorded_hash:
        return None
    record = {key: data[key] for key in ("file", "files") if key in data}
    if len(record) != 1 or not isinstance(next(iter(record.values())), dict):
        return None
    return record


#: The longest a record waits for the filesystem's clock to pass the change
#: time it holds: one step of that clock is a few ms (Linux 1-10 ms, Windows
#: up to 15.6 ms). A volume whose clock steps slower (FAT's 2 s mtime) is
#: not waited out; ``[integrity] inputs = "hash"`` is its setting.
_CLOCK_WAIT_LIMIT_S = 0.1


def _newest_change_ns(record: dict) -> int | None:
    """The latest change time a stat record holds (its mtime where it keeps none)."""
    members = [record["file"]] if "file" in record else list(record.get("files", {}).values())
    times = [fields.get("ctime_ns", fields.get("mtime_ns")) for fields in members]
    times = [t for t in times if isinstance(t, int)]
    return max(times) if times else None


def _wait_past(probe: Path, payload: str, newest_ns: int | None) -> None:
    """Rewrite *probe* until the filesystem stamps it later than *newest_ns*.

    A filesystem takes change times from a clock that steps, so an edit in
    the step a record was taken in carries the change time the record holds
    and matches it. Once a file written now is stamped later than the
    record, every later edit moves the change time past it. The probe's own
    write is the clock read, so the step is never assumed.
    """
    if newest_ns is None:
        return
    deadline = time.monotonic() + _CLOCK_WAIT_LIMIT_S
    while probe.stat().st_mtime_ns <= newest_ns and time.monotonic() < deadline:
        time.sleep(0.001)
        probe.write_text(payload, encoding="utf-8")


def _wait_past_record(project_dir: Path, record: dict) -> None:
    """Hold, with a scratch file in the stamps directory, until the clock is past *record*.

    For a record kept with no stamp (collect's columns for a file output).
    Best-effort: a scratch file that cannot be written waits for nothing.
    """
    probe = layout.stamps_dir(project_dir) / (
        f".clock.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("clock", encoding="utf-8")
        _wait_past(probe, "clock", _newest_change_ns(record))
    except OSError:
        pass
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


_batch = threading.local()


@contextmanager
def stamps_batched(project_dir: Path | str):
    """Write the stamps of one batch together, after one wait for the clock.

    Inside the block :func:`write_stamp` queues each stamp; on leaving it
    the clock is waited past the newest record queued, once, and every
    queued stamp is written. A loop over a directory's members writes one
    stamp per member, each record just changed (a copy, a link, a chmod),
    so one wait replaces one per member. A stamp read inside the block
    before it is written is missing, which costs a re-hash and is never
    trusted. A block inside another joins the outer one.

    Args:
        project_dir: The project root, whose stamps directory holds the
            clock probe.
    """
    if getattr(_batch, "pending", None) is not None:
        yield
        return
    _batch.pending = []
    try:
        yield
    finally:
        pending, _batch.pending = _batch.pending, None
        newest = [n for n in (_newest_change_ns(record) for _, record, _ in pending)
                  if n is not None]
        if newest:
            _wait_past_record(Path(project_dir), {"file": {"ctime_ns": max(newest)}})
        for stamp, record, recorded_hash in pending:
            _write_stamp_now(stamp, record, recorded_hash, wait=False)


def write_stamp(stamp: Path | str, record: dict, recorded_hash: str | None = None) -> bool:
    """Write a stamp atomically (tmp file, then replace), once the clock is past its record.

    The stamp is put in place only when the filesystem stamps its tmp file
    later than the newest change time in *record* (:func:`_wait_past`), so
    an edit made after the stamp exists never carries a change time the
    record holds. An edit between taking the record and that moment can.
    A stamp that cannot be written only costs the next read a re-hash, so a
    failure warns on stderr and is not raised.

    Args:
        stamp: The stamp file (a Layout path).
        record: The stat record from :func:`entry_record`.
        recorded_hash: The hash the stamp vouches for, or None.

    Returns:
        True when the stamp was written, or queued inside
        :func:`stamps_batched`.
    """
    pending = getattr(_batch, "pending", None)
    if pending is not None:
        pending.append((Path(stamp), record, recorded_hash))
        return True
    return _write_stamp_now(Path(stamp), record, recorded_hash, wait=True)


def _write_stamp_now(stamp: Path, record: dict, recorded_hash: str | None, *,
                     wait: bool) -> bool:
    """Write one stamp now; with *wait*, once the clock is past its record."""
    # One tmp name per process and thread: two writers of one stamp never
    # share a tmp file.
    tmp = stamp.with_name(f"{stamp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"hash": recorded_hash, **record})
        tmp.write_text(payload, encoding="utf-8")
        if wait:
            _wait_past(tmp, payload, _newest_change_ns(record))
        tmp.replace(stamp)
        return True
    except OSError as exc:
        print(f"WARNING: could not write integrity stamp {stamp}: {exc}",
              file=sys.stderr)
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def inputs_setting(project_dir: Path | str | None = None) -> str:
    """The project's ``[integrity] inputs`` setting.

    Args:
        project_dir: The project root; the active project when None.

    Returns:
        ``stat-then-hash`` or ``hash``; ``stat-then-hash`` when the project
        has no config file.

    Raises:
        ValueError: The config names another value.
    """
    root = Path(project_dir) if project_dir is not None else get_project_root()
    try:
        return read_config(root)["integrity"]["inputs"]
    except FileNotFoundError:
        return INTEGRITY_INPUTS_STAT_THEN_HASH


@task(purpose="The one integrity check: whether a file or directory is what wfc "
              "recorded, by its stat fields and, when they do not vouch, by a "
              "re-hash against the recorded hash",
      inputs="the entry's path; its recorded hash, its stamp or its recorded stat "
             "record; the [integrity] inputs setting",
      outputs="an IntegrityVerdict (intact, changed or missing); a fresh stamp "
              "after a verified re-hash")
def check_entry(
    path: Path | str,
    *,
    recorded_hash: str | None = None,
    stamp: Path | str | None = None,
    recorded: dict | None = None,
    setting: str | None = None,
) -> IntegrityVerdict:
    """Check that an entry is what wfc recorded.

    The stat record compared is ``recorded`` when the caller holds it (a
    pre-archive output's collect record), else the one ``stamp`` vouches
    for. Under ``stat-then-hash`` a matching record means intact without a
    byte read; under ``hash`` an entry with a recorded hash is always
    re-hashed. A mismatch, or no usable record, re-hashes and compares with
    ``recorded_hash``: equal is intact and re-stamps ``stamp`` (never a
    caller's ``recorded``, whose writer is the caller), different is
    changed. With no recorded hash nothing can confirm a mismatch, so it is
    changed. An entry that cannot be listed or read is changed.

    Args:
        path: The file or directory to check.
        recorded_hash: The content hash the entry must have, or None when
            wfc recorded none.
        stamp: The entry's stamp (a Layout path), read and, after a verified
            re-hash, rewritten.
        recorded: A stat record the caller holds, shaped as
            :func:`entry_record` shapes one; takes the place of the stamp.
        setting: ``[integrity] inputs``; read from the project when None and
            a recorded hash makes it matter.

    Returns:
        The :class:`IntegrityVerdict`.
    """
    口 = Step(step_num=1, name="Missing is missing",
             purpose="An entry with nothing at its path is missing, not changed")
    path = Path(path)
    if not os.path.lexists(path):
        return IntegrityVerdict(MISSING, hashed=False)

    口 = Step(step_num=2, name="Read the stat record",
             purpose="Take the entry's stat record now, before any byte is read, "
                     "so a write during the re-hash leaves a record that no "
                     "longer matches",
             critical="An entry Identity's listing refuses is changed")
    try:
        current = entry_record(path)
    except (OSError, DirectoryContentError):
        return IntegrityVerdict(CHANGED, hashed=False)

    口 = Step(step_num=3, name="Compare the stat record",
             purpose="Matching stat fields vouch for the entry under "
                     "stat-then-hash, and always when no hash was recorded; "
                     "with no recorded hash a mismatch is a change",
             critical="A ChangeTime moved by an extended-attribute write that "
                      "changed the name set, every other field equal, still "
                      "matches (records_match)")
    known: dict | None
    if recorded is not None:
        known = recorded
    elif stamp is not None:
        known = read_stamp(stamp, recorded_hash)
    else:
        known = None
    vouched = known is not None and records_match(known, current)
    if recorded_hash is None:
        return IntegrityVerdict(INTACT if vouched else CHANGED, hashed=False)
    if setting is None:
        setting = inputs_setting()
    if setting != INTEGRITY_INPUTS_HASH and vouched:
        return IntegrityVerdict(INTACT, hashed=False)

    口 = Step(step_num=4, name="Re-hash and compare",
             purpose="Hash the entry through Identity and compare with the "
                     "recorded hash; equal re-stamps, different is changed")
    try:
        actual = hash_path(path)
    except (OSError, DirectoryContentError):
        return IntegrityVerdict(CHANGED, hashed=True)
    if actual != recorded_hash:
        return IntegrityVerdict(CHANGED, hashed=True)
    if stamp is not None and recorded is None:
        write_stamp(stamp, current, recorded_hash)
    return IntegrityVerdict(INTACT, hashed=True)


@dataclass(frozen=True)
class OutputRecord:
    """The stat record collect writes for one output, taken after the chmod.

    Attributes:
        file_size: A file's size, or a directory's total size.
        file_mtime: A file's ``st_mtime``, or a directory's newest member's.
        stat_mtime_ns: The same as ``st_mtime_ns``.
        stat_ctime_ns: A file's change time: ``st_ctime_ns`` on Linux and
            macOS, NTFS's ChangeTime on Windows; None where the volume keeps
            none, and for a directory.
        stat_file_index: A file's index (``st_ino``) as text on Windows;
            None elsewhere and for a directory.
        member_count: A directory's number of members; None for a file.
        stat_xattr_names: A file's extended-attribute names, sorted; None
            where the read failed, and for a directory (each member's are in
            its member stamp).
    """

    file_size: int
    file_mtime: float
    stat_mtime_ns: int
    stat_ctime_ns: int | None
    stat_file_index: str | None
    member_count: int | None
    stat_xattr_names: list[str] | None = None


@task(purpose="Collect's stat record of one output: make it read-only, then take "
              "its platform stat fields and write a directory output's member stamp",
      inputs="the output's path in its run archive; the project root, run id and slot "
             "that place the member stamp",
      outputs="an OutputRecord for the row's stat columns; a directory's member "
              "stamp written atomically")
def record_output(path: Path | str, *, project_dir: Path | str, run_id: int,
                  slot: str) -> OutputRecord:
    """Make an output read-only and take the stat record collect writes.

    The chmod comes first: it moves the change time the record holds on
    Linux, macOS and Windows (NTFS's or ReFS's ChangeTime), so a record
    taken before it would never match again. No byte of
    the output is read.

    Args:
        path: The output file or directory.
        project_dir: The project root.
        run_id: The run that wrote the output.
        slot: The output slot it fills.

    Returns:
        The :class:`OutputRecord`.

    Raises:
        OSError: The output cannot be stat-ed.
        DirectoryContentError: A directory output holds a symlink, a
            case-only name collision, or no files.
    """
    from .cache import _make_read_only

    口 = Step(step_num=1, name="Make the output read-only",
             purpose="Protect the output from an accidental write, best-effort, "
                     "before its stat fields are read")
    path = Path(path)
    _make_read_only(path)

    口 = Step(step_num=2, name="Take the stat record",
             purpose="A file's own platform stat fields; a directory's member list "
                     "from Identity's stat-only listing, with each member's fields")
    if not path.is_dir():
        st = path.stat()
        fields = stat_fields(st, path=path)
        _wait_past_record(Path(project_dir), {"file": fields})
        return OutputRecord(
            file_size=st.st_size, file_mtime=st.st_mtime,
            stat_mtime_ns=st.st_mtime_ns, stat_ctime_ns=fields.get("ctime_ns"),
            stat_file_index=(str(fields["file_index"]) if "file_index" in fields
                             else None),
            member_count=None, stat_xattr_names=fields.get(XATTR_NAMES),
        )
    listing = directory_listing(path)

    口 = Step(step_num=3, name="Write the member stamp",
             purpose="The member list with each member's stat fields, in the "
                     "directory output's Layout stamp, written atomically")
    record = {"files": {rel: stat_fields(st, path=path / rel)
                        for rel, st in listing.members}}
    write_stamp(layout.output_members_stamp(Path(project_dir), run_id, slot), record)
    return OutputRecord(
        file_size=listing.total_size, file_mtime=listing.newest_mtime,
        stat_mtime_ns=listing.newest_mtime_ns, stat_ctime_ns=None,
        stat_file_index=None, member_count=listing.file_count,
    )


def output_record(row: RunOutput, project_dir: Path | str) -> dict | None:
    """The stat record collect wrote for an output, shaped as :func:`entry_record`.

    Args:
        row: An output row collected by this release (``stat_mtime_ns`` set).
        project_dir: The project root.

    Returns:
        A file's fields from the row, or a directory's member stamp; None
        when the member stamp is missing or unreadable.
    """
    if row.member_count is not None:
        return read_stamp(layout.output_members_stamp(Path(project_dir), row.run_id,
                                                      row.slot or ""))
    fields: dict = {"size": row.file_size, "mtime_ns": row.stat_mtime_ns}
    if row.stat_file_index is not None:
        fields["file_index"] = int(row.stat_file_index)
    if row.stat_ctime_ns is not None:
        fields["ctime_ns"] = row.stat_ctime_ns
    if row.stat_xattr_names is not None:
        fields[XATTR_NAMES] = list(row.stat_xattr_names)
    return {"file": fields}


def _legacy_check(row: RunOutput, path: Path) -> IntegrityVerdict:
    """A row from before this release: its size and float mtime, read as collect read them."""
    try:
        if path.is_dir():
            listing = directory_listing(path)
            size, mtime = listing.total_size, listing.newest_mtime
        else:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
    except (OSError, DirectoryContentError):
        return IntegrityVerdict(CHANGED, hashed=False)
    same = ((row.file_size is None or row.file_size == size)
            and (row.file_mtime is None or row.file_mtime == mtime))
    return IntegrityVerdict(INTACT if same else CHANGED, hashed=False)


@task(purpose="Check a pre-archive output against what collect recorded: its stat "
              "record and, when collect hashed it, its collect hash",
      inputs="the output row and its run-archive path; the [integrity] inputs setting",
      outputs="an IntegrityVerdict; nothing is written")
def check_output(row: RunOutput, path: Path | str, project_dir: Path | str,
                 setting: str | None = None) -> IntegrityVerdict:
    """Check a run output the archive pass has not reached.

    A row collected by this release goes through :func:`check_entry` with
    collect's record: with no collect hash any difference is a change;
    with one, ``stat-then-hash`` re-hashes on a mismatch and ``hash``
    always re-hashes. The row is never rewritten, so a touched output is
    re-checked on every read until it is archived. A row from before this
    release is compared on the ``file_size`` and float ``file_mtime`` it
    carries (a directory's total size and newest mtime), and a row with
    neither only on existence.

    Args:
        row: The output row (no ``content_hash``).
        path: Its run-archive path.
        project_dir: The project root.
        setting: ``[integrity] inputs``; the project's when None and the
            row carries a collect hash.

    Returns:
        The :class:`IntegrityVerdict`.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return IntegrityVerdict(MISSING, hashed=False)
    if row.stat_mtime_ns is None:
        if row.file_size is None and row.file_mtime is None:
            return IntegrityVerdict(INTACT, hashed=False)
        return _legacy_check(row, path)
    if row.collect_hash is not None and setting is None:
        setting = inputs_setting(project_dir)
    return check_entry(path, recorded_hash=row.collect_hash,
                       recorded=output_record(row, project_dir), setting=setting)


@task(purpose="Mark an output row changed after its run wrote it: one-way, a "
              "compare-and-set with no lock",
      inputs="the output row",
      outputs="integrity_status = changed on the row (when it was NULL), and on the "
              "caller's object")
def mark_changed(row: RunOutput) -> bool:
    """Write the ``changed`` mark on an output row, once.

    The write is ``UPDATE ... WHERE integrity_status IS NULL``, so several
    writers finding the same change agree and nothing else on the row
    moves. The caller's object carries the mark afterwards without being
    made dirty. A failed write warns on stderr: the next reader finds the
    change again.

    Args:
        row: The output row.

    Returns:
        True when this call wrote the mark.
    """
    from sqlalchemy import update
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm.attributes import set_committed_value
    from sqlmodel import col

    from ..persistence import RunOutput as _RunOutput
    from ..persistence import get_session

    row_id = row.id
    wrote = False
    try:
        with get_session() as session:
            result = session.execute(
                update(_RunOutput)
                .where(col(_RunOutput.id) == row_id)
                .where(col(_RunOutput.integrity_status).is_(None))
                .values(integrity_status=OutputIntegrity.changed.value)
            )
            session.commit()
            wrote = bool(result.rowcount)
    except SQLAlchemyError as exc:
        print(f"WARNING: could not mark output record {row_id} changed: {exc}",
              file=sys.stderr)
    set_committed_value(row, "integrity_status", OutputIntegrity.changed.value)
    return wrote
