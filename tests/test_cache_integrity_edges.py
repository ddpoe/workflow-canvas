"""Edges of Storage's integrity rules for cache entries, stamps and restored copies.

A directory checkout is never built from a manifest object whose bytes
changed; a crash-left tmp of the hashing copy is swept by the cache prune,
never a live one, and one it cannot remove is not counted pruned; an intact
restored copy that is still writable is protected, and a write after the
check's hash is found by the next check; a copy a stamp with a change time
vouches for is not walked for a write bit, one with none is; two threads
writing one stamp never share a tmp file.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from sqlmodel import select

from wfc import layout
from wfc.persistence import Sample, get_session


def _register(root: Path, name: str, files: dict[str, bytes]) -> Sample:
    """Register a directory sample through production registration."""
    from wfc.registration import register_sample

    src = root.parent / f"{root.name}-src" / name
    for rel, content in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(content)
    register_sample(name, src, project_root=root)
    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


def _writable(path: Path) -> None:
    """Declared deviation: strip the read-only bits, as a hand edit would."""
    from wfc.storage.cache import _make_writable

    _make_writable(path)


def test_a_re_pull_leaves_an_intact_members_stamp_matching(tmp_project):
    """A pull again chmods only what it wrote: the intact member keeps its change
    time, so its stamp still vouches without a re-hash.

    This discriminates on POSIX only: there a chmod of the already read-only
    intact member moves its ctime, so a pull that chmodded everything would
    fail the record comparison. On Windows the same-mode chmod does not move
    NTFS's ChangeTime, so the test passes either way there; the defect it
    guards against cannot occur on Windows.
    """
    from wfc.identity import hash_file, hash_path
    from wfc.storage.integrity import check_entry, entry_record, read_stamp
    from wfc.storage.resolve import pull_again
    from wfc.storage.transport import push

    files = {"a.csv": b"id\n1\n", "b.csv": b"id\n2\n"}
    _register(tmp_project, "D1", files)
    src = tmp_project.parent / f"{tmp_project.name}-src" / "D1"
    dir_hash = hash_path(src)
    assert not push([dir_hash], tmp_project).failed
    edited, intact = (hash_file(src / rel) for rel in files)
    intact_obj = layout.dvc_cache_entry(tmp_project, intact)
    intact_stamp = layout.cache_object_stamp(tmp_project, intact)
    assert check_entry(intact_obj, recorded_hash=intact, stamp=intact_stamp,
                       setting="stat-then-hash").intact
    before = read_stamp(intact_stamp, intact)
    assert before == entry_record(intact_obj)

    edited_obj = layout.dvc_cache_entry(tmp_project, edited)
    _writable(edited_obj)
    edited_obj.write_bytes(b"id\n9\n")
    pull_again(tmp_project, dir_hash, [edited], "sample 'D1'", "re-register it",
               "test", "stat-then-hash")

    assert edited_obj.read_bytes() == files["a.csv"], "the changed member was pulled"
    assert not os.access(edited_obj, os.W_OK), "what the pull wrote is protected"
    assert entry_record(intact_obj) == before, "the intact member was not chmodded"
    verdict = check_entry(intact_obj, recorded_hash=intact, stamp=intact_stamp,
                          setting="stat-then-hash")
    assert (verdict.status, verdict.hashed) == ("intact", False)


def test_a_checkout_from_a_changed_manifest_object_is_refused_and_not_stamped(tmp_project):
    """A manifest object whose bytes are not its address (as an unstamped pull brings
    it) builds nothing and stamps nothing."""
    from wfc.storage.cache import ChangedEntryError, restore_from_cache

    row = _register(tmp_project, "D1", {"a.csv": b"id\n1\n", "b.csv": b"id\n2\n"})
    manifest = layout.dvc_cache_entry(tmp_project, row.content_hash)
    # A manifest that names only one of the two members: valid JSON, wrong bytes.
    data = manifest.read_bytes()
    _writable(manifest)
    manifest.write_bytes(data.replace(b'"b.csv"', b'"c.csv"'))
    layout.cache_object_stamp(tmp_project, row.content_hash).unlink(missing_ok=True)

    dest = tmp_project / "restored" / "D1"
    stamp = layout.sample_stamp(tmp_project, "D1")
    with pytest.raises(ChangedEntryError) as exc:
        restore_from_cache(row.content_hash, dest, tmp_project, stamp=stamp)
    assert row.content_hash in str(exc.value)
    assert not dest.exists() and not stamp.exists()


def test_the_cache_prune_sweeps_a_crash_left_copy_tmp_and_never_a_live_one(tmp_project):
    from wfc.storage.cache import COPY_TMP_PREFIXES, STALE_COPY_TMP_SECONDS
    from wfc.storage.prune import prune_dvc_cache

    _register(tmp_project, "D1", {"a.csv": b"id\n1\n"})
    root = layout.dvc_cache_files_dir(tmp_project)
    old = root / f"{COPY_TMP_PREFIXES[0]}crashed.tmp"
    old_manifest = root / f"{COPY_TMP_PREFIXES[1]}crashed.tmp"
    live = root / f"{COPY_TMP_PREFIXES[0]}live.tmp"
    for p in (old, old_manifest, live):
        p.write_bytes(b"partial")
    past = time.time() - STALE_COPY_TMP_SECONDS - 60
    for p in (old, old_manifest):
        os.utime(p, (past, past))

    listed = prune_dvc_cache(tmp_project, dry_run=True)
    assert old in listed and old_manifest in listed and live not in listed
    assert old.exists(), "a dry run removes nothing"
    prune_dvc_cache(tmp_project)
    assert not old.exists() and not old_manifest.exists()
    assert live.exists(), "a tmp a live copy may be writing is kept"


@pytest.mark.parametrize("refused_index", [0, 1], ids=["file-tmp", "manifest-tmp"])
def test_a_stale_tmp_that_cannot_be_removed_is_named_and_the_prune_goes_on(
        tmp_project, monkeypatch, capfd, refused_index):
    """An unremovable stale tmp is named on stderr; the rest is pruned.

    Each of the two tmps is the refused one in turn, so the one removed is
    reached after the refusal whatever order the sweep visits them in.
    """
    from tests.fixtures import fakes
    from wfc.storage.prune import prune_dvc_cache

    _register(tmp_project, "D1", {"a.csv": b"id\n1\n"})
    tmps = _stale_tmps(tmp_project)
    refused, other = tmps[refused_index], tmps[1 - refused_index]
    fakes.refuse_path_unlink(monkeypatch, refused)
    capfd.readouterr()

    removed = prune_dvc_cache(tmp_project)

    err = capfd.readouterr().err
    assert f"could not remove the stale cache tmp {refused}" in err
    assert refused.exists() and refused not in removed
    assert not other.exists() and other in removed, "the prune went on"


def _stale_tmps(root: Path) -> list[Path]:
    """Two hashing-copy tmps at the cache root, untouched for over a day."""
    from wfc.storage.cache import COPY_TMP_PREFIXES, STALE_COPY_TMP_SECONDS

    cache_root = layout.dvc_cache_files_dir(root)
    cache_root.mkdir(parents=True, exist_ok=True)
    tmps = [cache_root / f"{prefix}crashed.tmp" for prefix in COPY_TMP_PREFIXES[:2]]
    past = time.time() - STALE_COPY_TMP_SECONDS - 60
    for p in tmps:
        p.write_bytes(b"partial")
        os.utime(p, (past, past))
    return tmps


def test_the_prune_count_leaves_out_a_stale_tmp_it_could_not_remove(
        tmp_project, monkeypatch, capfd):
    """``wfc cache prune`` counts what it removed, not what it listed.

    Both stale tmps are listed for removal; one is refused and left, so one
    item is pruned.
    """
    from tests.fixtures import fakes
    from wfc.cli import cli_main

    refused, other = _stale_tmps(tmp_project)
    fakes.refuse_path_unlink(monkeypatch, refused)
    capfd.readouterr()

    assert cli_main(["cache", "prune", "--include-local", "--force"]) == 0

    out = capfd.readouterr().out
    assert "DVC cache entries to remove: 2" in out
    assert "Pruned 1 item(s)." in out
    assert refused.exists() and not other.exists()


def test_an_intact_writable_directory_sample_is_made_read_only_and_re_hashed_once(
        tmp_project):
    """A copy restored writable before this release, still intact, is protected.

    The stamp the check wrote holds the record taken before its hash; the
    chmod moves the change time, so the next check re-hashes once and
    stamps the read-only copy, and the one after that vouches.
    """
    from wfc.storage import SAMPLE_INTACT, check_sample, restore_sample
    from wfc.storage.integrity import check_entry

    row = _register(tmp_project, "D1", {"a.csv": b"id\n1\n", "sub/b.csv": b"id\n2\n"})
    restore_sample("D1", project_root=tmp_project)
    copy = layout.restored_sample_path(tmp_project, "D1", row.registered_path)
    stamp = layout.sample_stamp(tmp_project, "D1")
    _writable(copy)
    stamp.unlink()

    assert check_sample("D1", tmp_project).status == SAMPLE_INTACT
    assert not os.access(copy / "a.csv", os.W_OK)
    assert not os.access(copy / "sub" / "b.csv", os.W_OK)
    verdicts = [check_entry(copy, recorded_hash=row.content_hash, stamp=stamp,
                            setting="stat-then-hash") for _ in range(2)]
    assert [(v.intact, v.hashed) for v in verdicts] == [(True, True), (True, False)], \
        "the chmod moved the change time: one re-hash, then the stamp vouches"


def test_a_write_between_the_check_and_the_protect_step_is_found_by_the_next_check(
        tmp_project, monkeypatch):
    """A write after the check's hash is never stamped intact.

    The write lands inside the protect step, before its chmod. The stamp is
    the one the check wrote from the record it took before hashing, so the
    next check finds the record changed, re-hashes and repairs the copy.
    """
    from tests.fixtures import fakes
    from wfc.registration import register_sample
    from wfc.storage import SAMPLE_INTACT, SAMPLE_REPAIRED, check_sample, restore_sample

    src = tmp_project.parent / "s.csv"
    src.write_bytes(b"id\n1\n")
    register_sample("S1", src, project_root=tmp_project)
    restore_sample("S1", project_root=tmp_project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "S1")).one()
    copy = layout.restored_sample_path(tmp_project, "S1", row.registered_path)
    _writable(copy)
    layout.sample_stamp(tmp_project, "S1").unlink()
    writes: list[str] = []

    def write_then_chmod(real, path):
        if not writes:
            writes.append(str(path))
            Path(path).write_bytes(b"id\n9\n")
        real(path)

    fakes.fault_at_protect_chmod(monkeypatch, write_then_chmod)
    assert check_sample("S1", tmp_project, "stat-then-hash").status == SAMPLE_INTACT
    assert writes == [str(copy)], "the write landed in the protect step"

    checked = check_sample("S1", tmp_project, "stat-then-hash")
    assert checked.status == SAMPLE_REPAIRED, "the next check re-hashed and found it"
    assert copy.read_bytes() == b"id\n1\n"


def test_an_intact_copy_is_walked_for_a_write_bit_only_when_re_hashed(
        tmp_project, monkeypatch):
    """A vouching stamp means no chmod since it was written.

    A check the stamp vouches for does not walk the copy; a check of an
    unstamped copy re-hashes it and walks it.
    """
    from tests.fixtures import fakes
    from wfc.storage import SAMPLE_INTACT, check_sample, restore_sample

    row = _register(tmp_project, "D1", {"a.csv": b"id\n1\n", "sub/b.csv": b"id\n2\n"})
    restore_sample("D1", project_root=tmp_project)
    copy = layout.restored_sample_path(tmp_project, "D1", row.registered_path)
    walked = fakes.spy_writable_walks(monkeypatch)

    assert check_sample("D1", tmp_project, "stat-then-hash").status == SAMPLE_INTACT
    assert walked == [], "the stamp vouched: no walk"

    layout.sample_stamp(tmp_project, "D1").unlink()
    assert check_sample("D1", tmp_project, "stat-then-hash").status == SAMPLE_INTACT
    assert walked == [str(copy)], "an unstamped copy is re-hashed and walked"


def test_a_copy_vouched_for_by_a_stamp_with_no_change_time_is_walked_and_protected(
        tmp_project, monkeypatch):
    """On a volume with no change time a chmod moves no field the stamp records.

    The stamp still vouches for a copy made writable, so the check walks it
    and makes it read-only again.
    """
    from tests.fixtures import fakes
    from wfc.storage import SAMPLE_INTACT, check_sample, restore_sample
    from wfc.storage.integrity import check_entry, read_stamp

    fakes.volume_without_change_time(monkeypatch)
    row = _register(tmp_project, "D1", {"a.csv": b"id\n1\n", "sub/b.csv": b"id\n2\n"})
    restore_sample("D1", project_root=tmp_project)
    copy = layout.restored_sample_path(tmp_project, "D1", row.registered_path)
    stamp = layout.sample_stamp(tmp_project, "D1")
    assert all("ctime_ns" not in fields
               for fields in read_stamp(stamp, row.content_hash)["files"].values())
    _writable(copy)
    verdict = check_entry(copy, recorded_hash=row.content_hash, stamp=stamp,
                          setting="stat-then-hash")
    assert (verdict.intact, verdict.hashed) == (True, False), "the stamp still vouches"
    walked = fakes.spy_writable_walks(monkeypatch)

    assert check_sample("D1", tmp_project, "stat-then-hash").status == SAMPLE_INTACT
    assert walked == [str(copy)], "a stamp with no change time cannot show a chmod"
    assert not os.access(copy / "a.csv", os.W_OK)
    assert not os.access(copy / "sub" / "b.csv", os.W_OK)


def test_two_threads_writing_one_stamp_never_share_a_tmp(tmp_path, capfd):
    from wfc.storage.integrity import read_stamp, write_stamp

    stamp = tmp_path / "stamps" / "x.json"
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def writer(i: int) -> None:
        barrier.wait()
        for _ in range(50):
            results.append(write_stamp(stamp, {"file": {"size": i}}, "h"))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    err = capfd.readouterr().err
    # A shared tmp shows as a writer whose tmp another thread already moved
    # (file not found). On Windows a replace racing another replace of the
    # same stamp can still be refused (access denied): a warning, and the
    # next read re-hashes, but never a tmp two writers share.
    assert "[WinError 2]" not in err and "[Errno 2]" not in err, err[:500]
    assert read_stamp(stamp, "h") is not None
    assert not [p for p in stamp.parent.iterdir() if p.suffix == ".tmp"]
    assert any(results)
