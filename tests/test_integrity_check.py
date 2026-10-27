"""Storage's one integrity check, its setting, its stamps and its stat fields.

Tier 1: the foundations every reader of a sample, output, cache object or
checkout goes through (``wfc.storage.integrity``), the ``[integrity] inputs``
setting Persistence reads, and Identity's stat-only listing, which shares the
manifest's walk.
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

from wfc import layout
from wfc.identity import (
    DirectoryContentError,
    content_hasher,
    directory_listing,
    directory_manifest,
    hash_file,
    hash_path,
)
from wfc.persistence import read_config
from wfc.storage.integrity import (
    CHANGED,
    INTACT,
    MISSING,
    change_time_from_filetime,
    check_entry,
    entry_record,
    inputs_setting,
    ntfs_change_time_ns,
    read_stamp,
    stat_fields,
    write_stamp,
)

STAT = "stat-then-hash"
HASH = "hash"


def _append_config(root, text: str) -> None:
    """Append a section to the project's wf-canvas.toml."""
    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")


def _touch_later(path) -> None:
    """Move a file's mtime five seconds on without changing its bytes."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


# --- the setting -------------------------------------------------------------

def test_the_inputs_setting_defaults_to_stat_then_hash(tmp_project):
    """A project with no [integrity] section reads stat-then-hash."""
    assert read_config(tmp_project)["integrity"] == {"inputs": STAT}
    assert inputs_setting(tmp_project) == STAT


def test_the_inputs_setting_reads_hash(tmp_project):
    """[integrity] inputs = "hash" comes back as hash."""
    _append_config(tmp_project, '[integrity]\ninputs = "hash"\n')
    assert read_config(tmp_project)["integrity"] == {"inputs": HASH}
    assert inputs_setting(tmp_project) == HASH


def test_an_unknown_inputs_setting_is_refused_naming_both_values(tmp_project):
    """Any other value is refused, naming it and the two values wfc accepts."""
    _append_config(tmp_project, '[integrity]\ninputs = "paranoid"\n')
    with pytest.raises(ValueError) as exc:
        read_config(tmp_project)
    message = str(exc.value)
    assert "paranoid" in message
    assert '"stat-then-hash"' in message and '"hash"' in message


# --- the platform's stat fields ---------------------------------------------

_FAKE_STAT = SimpleNamespace(st_size=5, st_mtime_ns=11, st_ctime_ns=22, st_ino=33)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_records_size_mtime_and_ctime(platform):
    """Linux and macOS record size, mtime_ns and ctime_ns."""
    assert stat_fields(_FAKE_STAT, platform) == {"size": 5, "mtime_ns": 11, "ctime_ns": 22}


def test_windows_records_size_mtime_and_the_file_index_not_its_creation_time():
    """Windows records size, mtime_ns and the file index; st_ctime (creation) is never kept."""
    assert stat_fields(_FAKE_STAT, "win32") == {"size": 5, "mtime_ns": 11, "file_index": 33}


@pytest.mark.parametrize("ticks, expected", [
    (0, None),
    (116_444_736_000_000_000, 0),
    (116_444_736_000_000_000 + 17_921_504_172_603_996, 1_792_150_417_260_399_600),
])
def test_an_ntfs_change_time_of_zero_is_none(ticks, expected):
    """FILE_BASIC_INFO's ChangeTime converts to epoch ns; 0 (FAT32, exFAT) is no change time."""
    assert change_time_from_filetime(ticks) == expected


def test_a_change_time_that_cannot_be_read_is_left_out(tmp_path):
    """A failed read records no change time; the other fields remain."""
    assert ntfs_change_time_ns(tmp_path / "absent.txt") is None
    assert "ctime_ns" not in stat_fields(_FAKE_STAT, "win32", path=tmp_path / "absent.txt")


#: Longer than one tick of the clock a change time is taken from (Windows'
#: coarsest is 15.625 ms; Linux's 1-10 ms): an edit inside the tick of the
#: record's write carries the same change time, which is the stamp's job
#: (``write_stamp`` waits it out), not what these tests show.
_PAST_A_TICK = 0.05


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS's ChangeTime is a Windows field")
def test_windows_records_ntfs_change_time_which_a_same_size_edit_moves(tmp_path):
    """On NTFS the record carries ChangeTime; a same-size edit with the mtime put back moves it."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    before = entry_record(f)
    assert before["file"]["ctime_ns"] == ntfs_change_time_ns(f)
    assert ntfs_change_time_ns(tmp_path) is not None, "a directory opens too"
    st = f.stat()
    time.sleep(_PAST_A_TICK)
    f.write_bytes(b"GENUINE")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = entry_record(f)
    assert {k: v for k, v in after["file"].items() if k != "ctime_ns"} == {
        k: v for k, v in before["file"].items() if k != "ctime_ns"}
    assert after != before, "only the change time moved, and it is enough"


def test_a_chmod_moves_the_record(tmp_path):
    """A chmod moves the change time, so a record taken before it no longer matches."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"data")
    before = entry_record(f)
    time.sleep(_PAST_A_TICK)
    os.chmod(f, 0o444)
    assert entry_record(f) != before


def test_a_change_time_on_one_side_only_is_a_mismatch(tmp_path):
    """A record and a read that disagree on having a change time re-hash, never trust.

    The read here carries one on every platform this suite runs on (NTFS
    under Windows), so the record without one stands for a stamp written
    where the volume kept none, and the record with another value for the
    reverse direction's dict inequality; with no recorded hash either is
    a change.
    """
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    md5 = hash_file(f)
    now = entry_record(f)["file"]
    assert "ctime_ns" in now
    other_ctime = {"file": {**now, "ctime_ns": now["ctime_ns"] + 1}}
    without = {"file": {k: v for k, v in now.items() if k != "ctime_ns"}}
    for recorded in (other_ctime, without):
        assert recorded != {"file": now}
        verdict = check_entry(f, recorded_hash=md5, recorded=recorded, setting=STAT)
        assert (verdict.status, verdict.hashed) == (INTACT, True)
        assert check_entry(f, recorded=recorded, setting=STAT).status == CHANGED


@pytest.mark.parametrize("recorded_as, expected", [
    # The change time moved and the name set changed: metadata only.
    ({"ctime_ns": -1, "xattr_names": ["SCANNER.TAG"]}, INTACT),
    # The change time moved with the same names: a change.
    ({"ctime_ns": -1}, CHANGED),
    # A record holding no names compares on the rest exactly.
    ({"ctime_ns": -1, "xattr_names": None}, CHANGED),
    ({"xattr_names": None}, INTACT),
    # The names alone never decide: an unmoved change time still matches.
    ({"xattr_names": ["SCANNER.TAG"]}, INTACT),
    # Any other field that moved is a change, whatever the names.
    ({"ctime_ns": -1, "mtime_ns": -1, "xattr_names": ["SCANNER.TAG"]}, CHANGED),
    ({"ctime_ns": -1, "size": -1, "xattr_names": ["SCANNER.TAG"]}, CHANGED),
])
def test_a_change_time_moved_with_the_attribute_names_is_metadata_only(
        tmp_path, recorded_as, expected):
    """Storage's one comparison: a ChangeTime move is a change unless the name set moved too.

    The record is the file's own read with fields shifted (``-1`` moves a
    field by one; ``None`` drops the names), compared with no recorded
    hash, where any mismatch is a change.
    """
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    now = entry_record(f)["file"]
    if "xattr_names" not in now:
        pytest.skip("this filesystem's attribute names cannot be read")
    if "ctime_ns" not in now:
        pytest.skip("this volume keeps no change time")
    recorded = dict(now)
    for key, shift in recorded_as.items():
        if shift is None:
            recorded.pop(key)
        elif isinstance(shift, list):
            recorded[key] = sorted(set(now[key]) ^ set(shift))
        else:
            recorded[key] = now[key] + shift
    assert check_entry(f, recorded={"file": recorded}, setting=STAT).status == expected


# --- stamps ------------------------------------------------------------------

def test_a_stamp_round_trips_for_its_own_hash_only(tmp_path):
    """A written stamp reads back for its hash, for no other, and leaves no tmp."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"data")
    stamp = tmp_path / "stamps" / "a.verified"
    record = entry_record(f)
    assert write_stamp(stamp, record, "abc")
    assert read_stamp(stamp, "abc") == record
    assert read_stamp(stamp, "other") is None
    assert not list(stamp.parent.glob("*.tmp"))


def _same_size_edit(f) -> None:
    """Rewrite a file in place at once, same size, its mtime put back."""
    st = f.stat()
    os.chmod(f, 0o644)
    f.write_bytes(bytes(b ^ 0x01 for b in f.read_bytes()))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_an_edit_the_moment_a_stamp_is_written_does_not_match_it(tmp_path):
    """A stamp is in place only once the clock is past its record: an edit right after misses it.

    A filesystem takes change times from a clock that steps (a few ms on
    Linux and Windows), and an edit inside the step of the record would carry
    the same change time; the stamp waits the step out.
    """
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    record = entry_record(f)
    assert write_stamp(tmp_path / "stamps" / "a.verified", record, "abc")
    _same_size_edit(f)
    assert entry_record(f) != record


def test_an_edit_the_moment_an_output_is_recorded_does_not_match_its_record(tmp_path):
    """Collect's record of a file output is taken once the clock is past it, as a stamp is."""
    from wfc.storage.integrity import record_output

    out = tmp_path / "out.csv"
    out.write_bytes(b"genuine")
    recorded = record_output(out, project_dir=tmp_path, run_id=1, slot="out")
    _same_size_edit(out)
    st = out.stat()
    now = stat_fields(st, path=out)
    assert (st.st_size, st.st_mtime_ns, now.get("ctime_ns")) != (
        recorded.file_size, recorded.stat_mtime_ns, recorded.stat_ctime_ns)
    assert not list(layout.stamps_dir(tmp_path).glob("*.tmp")), "no clock probe is left"


@pytest.mark.parametrize("content", [b"", b"not json", b"[1, 2]", b'{"hash": "abc"}'])
def test_an_unreadable_stamp_vouches_for_nothing(tmp_path, content):
    """An empty, garbled or incomplete stamp vouches for nothing."""
    stamp = tmp_path / "a.verified"
    stamp.write_bytes(content)
    assert read_stamp(stamp, "abc") is None


# --- the check ---------------------------------------------------------------

def test_a_missing_stamp_re_hashes_then_stamps_and_the_next_read_trusts_it(tmp_path):
    """No stamp: the check re-hashes and stamps; the next read trusts the stamp."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    stamp = tmp_path / "a.verified"

    first = check_entry(f, recorded_hash=hash_file(f), stamp=stamp, setting=STAT)
    assert (first.status, first.hashed) == (INTACT, True)
    assert stamp.is_file()

    second = check_entry(f, recorded_hash=hash_file(f), stamp=stamp, setting=STAT)
    assert (second.status, second.hashed) == (INTACT, False)


def test_under_hash_every_read_re_hashes(tmp_path):
    """Under hash a matching stamp does not vouch: every read re-hashes."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    stamp = tmp_path / "a.verified"
    md5 = hash_file(f)
    first = check_entry(f, recorded_hash=md5, stamp=stamp, setting=HASH)
    again = check_entry(f, recorded_hash=md5, stamp=stamp, setting=HASH)
    assert (first.hashed, again.status, again.hashed) == (True, INTACT, True)


def test_an_edit_is_changed_and_leaves_the_stamp_alone(tmp_path):
    """Edited bytes are changed, and the stamp still vouches for the old record."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    stamp = tmp_path / "a.verified"
    md5 = hash_file(f)
    check_entry(f, recorded_hash=md5, stamp=stamp, setting=STAT)
    vouched = read_stamp(stamp, md5)
    f.write_bytes(b"edited!!")

    verdict = check_entry(f, recorded_hash=md5, stamp=stamp, setting=STAT)
    assert (verdict.status, verdict.hashed) == (CHANGED, True)
    assert read_stamp(stamp, md5) == vouched


def test_a_touched_but_unchanged_entry_re_hashes_and_re_stamps(tmp_path):
    """A moved mtime re-hashes; the same bytes pass and the stamp is renewed."""
    f = tmp_path / "a.txt"
    f.write_bytes(b"genuine")
    stamp = tmp_path / "a.verified"
    md5 = hash_file(f)
    check_entry(f, recorded_hash=md5, stamp=stamp, setting=STAT)
    _touch_later(f)

    verdict = check_entry(f, recorded_hash=md5, stamp=stamp, setting=STAT)
    assert (verdict.status, verdict.hashed) == (INTACT, True)
    assert read_stamp(stamp, md5) == entry_record(f)


def test_with_no_recorded_hash_a_stat_mismatch_is_changed_without_a_read(tmp_path):
    """No recorded hash: the stat record decides, under either setting."""
    f = tmp_path / "out.txt"
    f.write_bytes(b"output")
    recorded = entry_record(f)
    assert check_entry(f, recorded=recorded, setting=HASH).status == INTACT

    _touch_later(f)
    verdict = check_entry(f, recorded=recorded, setting=HASH)
    assert (verdict.status, verdict.hashed) == (CHANGED, False)


def test_with_no_record_and_no_hash_the_entry_is_changed(tmp_path):
    """No record and no hash: nothing vouches, so the entry is changed."""
    f = tmp_path / "out.txt"
    f.write_bytes(b"output")
    assert check_entry(f, stamp=tmp_path / "absent.json", setting=STAT).status == CHANGED


def test_a_caller_held_record_is_never_rewritten(tmp_path):
    """A verified re-hash against a caller's record writes no stamp."""
    f = tmp_path / "out.txt"
    f.write_bytes(b"output")
    recorded = entry_record(f)
    _touch_later(f)
    stamp = tmp_path / "unused.verified"
    verdict = check_entry(f, recorded_hash=hash_file(f), recorded=recorded,
                          stamp=stamp, setting=STAT)
    assert (verdict.status, verdict.hashed) == (INTACT, True)
    assert not stamp.exists()


def test_a_missing_entry_is_missing(tmp_path):
    """Nothing at the path is missing, not changed."""
    verdict = check_entry(tmp_path / "gone.txt", recorded_hash="0" * 32, setting=STAT)
    assert verdict.status == MISSING


def test_a_directory_member_added_after_its_stamp_is_changed(tmp_path):
    """A directory stamped as verified, then given an extra file, is changed."""
    d = tmp_path / "tiles"
    d.mkdir()
    (d / "a.txt").write_bytes(b"a")
    (d / "b.txt").write_bytes(b"b")
    stamp = tmp_path / "tiles.verified"
    dir_hash = hash_path(d)
    assert check_entry(d, recorded_hash=dir_hash, stamp=stamp, setting=STAT).hashed
    assert not check_entry(d, recorded_hash=dir_hash, stamp=stamp, setting=STAT).hashed

    (d / "extra.txt").write_bytes(b"injected")
    verdict = check_entry(d, recorded_hash=dir_hash, stamp=stamp, setting=STAT)
    assert (verdict.status, verdict.hashed) == (CHANGED, True)


# --- Identity's listing shares the manifest's walk --------------------------

def test_the_listing_and_the_manifest_see_the_same_members(tmp_path):
    """Dotfiles count, empty subdirectories do not, for the listing and the manifest alike."""
    d = tmp_path / "out"
    (d / "sub").mkdir(parents=True)
    (d / "empty").mkdir()
    (d / ".hidden").write_bytes(b"h")
    (d / "sub" / "x.bin").write_bytes(b"xyz")
    listing = directory_listing(d)
    manifest = directory_manifest(d)
    assert [rel for rel, _st in listing.members] == [rel for rel, _m in manifest.entries]
    assert (listing.file_count, listing.total_size) == (manifest.file_count, manifest.total_size)
    assert listing.newest_mtime == manifest.newest_mtime


def test_the_listing_refuses_what_the_manifest_refuses(tmp_path):
    """A directory with no files is refused by both; a missing path is not a directory."""
    empty = tmp_path / "empty"
    (empty / "only-dirs").mkdir(parents=True)
    with pytest.raises(DirectoryContentError):
        directory_manifest(empty)
    with pytest.raises(DirectoryContentError):
        directory_listing(empty)
    with pytest.raises(NotADirectoryError):
        directory_listing(tmp_path / "absent")


def test_the_incremental_hasher_equals_the_file_hash(tmp_path):
    """Feeding the incremental hasher a file's bytes in pieces gives its file hash."""
    f = tmp_path / "a.bin"
    data = os.urandom(3 << 20)
    f.write_bytes(data)
    h = content_hasher()
    for i in range(0, len(data), 1000):
        h.update(data[i:i + 1000])
    assert h.hexdigest() == hash_file(f)
