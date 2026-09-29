"""A directory's cache entry is DVC's shape, and every reader goes through the one checkout.

``cache_file`` stores a directory as one object per file plus a ``.dir``
manifest and writes no tree; ``checkout`` rebuilds the directory; a partial
entry (manifest present, a member missing) is not complete; and a tree at an
unsuffixed address is refused as a malformed cache entry, never converted.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc import layout
from wfc.identity import directory_manifest, hash_file, hash_path
from wfc.storage.cache import (
    MalformedEntryError,
    cache_file,
    checkout,
    entry_is_complete,
    local_path,
    restore_from_cache,
)


def _source(root: Path, name: str, files: dict[str, bytes]) -> Path:
    d = root / name
    for rel, data in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


def _tree_bytes(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes()
            for p in d.rglob("*") if p.is_file()}


def _force_remove(p: Path) -> None:
    import os
    os.chmod(p, 0o644)
    p.unlink()


@workflow(
    purpose="a cached directory is a .dir manifest plus one object per file, "
            "no tree; the checkout rebuilds the identical tree",
    inputs="a nested directory with a dotfile, cached in copy mode",
    outputs="the manifest at the .dir address, members at their md5 "
            "addresses, and a checkout identical to the source",
)
def test_directory_entry_is_members_plus_manifest(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "sub/b.txt": b"b", ".dot": b"d"})
    h = hash_path(src)

    dest = cache_file(src, h, project, move=False)

    assert dest == layout.dvc_cache_entry(project, h)
    assert dest.is_file(), "a directory's entry is its manifest object, not a tree"
    assert dest.read_bytes() == directory_manifest(src).manifest_bytes
    for rel, md5 in directory_manifest(src).entries:
        assert layout.dvc_cache_entry(project, md5).is_file(), rel
    assert src.is_dir(), "copy mode leaves the source in place"

    out = tmp_path / "restored"
    assert restore_from_cache(h, out, project)
    assert _tree_bytes(out) == _tree_bytes(src)
    # Idempotent over a verified destination; replaces a stale one.
    assert restore_from_cache(h, out, project)
    (out / "a.csv").write_bytes(b"stale")
    assert restore_from_cache(h, out, project)
    assert _tree_bytes(out) == _tree_bytes(src)


@workflow(
    purpose="a file that appears in two directories is stored once in the cache",
    inputs="two directories sharing one file's bytes",
    outputs="one object for the shared file, two manifests",
)
def test_a_file_shared_by_two_directories_is_stored_once(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    shared = b"shared bytes"
    one = _source(tmp_path, "one", {"s.bin": shared, "x": b"1"})
    two = _source(tmp_path, "two", {"deep/s.bin": shared, "y": b"2"})
    cache_file(one, hash_path(one), project, move=False)
    cache_file(two, hash_path(two), project, move=False)

    objects = [p for p in layout.dvc_cache_files_dir(project).rglob("*") if p.is_file()]
    shared_md5 = hash_file(one / "s.bin")
    shared_objects = [p for p in objects if p == layout.dvc_cache_entry(project, shared_md5)]
    assert len(shared_objects) == 1
    # 2 manifests + shared + x + y
    assert len(objects) == 5


def test_a_partial_directory_entry_is_not_complete(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a": b"1", "b": b"2"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)
    assert entry_is_complete(project, h)

    _force_remove(layout.dvc_cache_entry(project, hash_file(src / "b")))
    assert not entry_is_complete(project, h)
    assert local_path(project, h) is None
    assert not checkout(h, tmp_path / "co", project)
    assert not (tmp_path / "co").exists()


def test_local_path_serves_a_read_only_checkout_under_the_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a": b"1"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)
    p = local_path(project, h)
    assert p == layout.checkout_dir(project, h)
    assert p.is_relative_to(project)
    assert _tree_bytes(p) == _tree_bytes(src)


def test_move_mode_consumes_the_staging_directory(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "staging", {"a": b"1"})
    h = hash_path(src)
    cache_file(src, h, project, move=True)
    assert not src.exists()
    assert entry_is_complete(project, h)


def test_a_tree_at_an_unsuffixed_address_is_a_malformed_cache_entry(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    legacy_hash = "0123456789abcdef0123456789abcdef"
    legacy = layout.dvc_cache_entry(project, legacy_hash)
    legacy.mkdir(parents=True)
    (legacy / "a.csv").write_bytes(b"old shape")

    with pytest.raises(MalformedEntryError) as exc:
        restore_from_cache(legacy_hash, tmp_path / "out", project)
    assert "malformed cache entry" in str(exc.value)
    assert legacy.is_dir() and (legacy / "a.csv").read_bytes() == b"old shape", (
        "nothing is converted"
    )
    with pytest.raises(MalformedEntryError):
        entry_is_complete(project, legacy_hash)


def _files_stat(d: Path) -> dict[str, tuple[int, int]]:
    return {p.relative_to(d).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in d.rglob("*") if p.is_file()}


def _same_size_tamper_keeping_mtime(p: Path, data: bytes) -> None:
    before = p.stat()
    assert len(data) == before.st_size
    os.chmod(p, 0o644)
    p.write_bytes(data)
    os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_a_second_read_of_an_intact_checkout_trusts_its_stamp(tmp_path):
    """The hot path: ``local_path`` on an intact checkout checks names, sizes
    and mtimes against the stamp and reads no member's bytes.

    The trade-off, recorded as a decision: an edit that keeps a member's size
    and restores its mtime (a deliberate act on a read-only checkout) is not
    seen by the cheap check. Deleting the stamp forces a full re-verify.
    """
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "nested/b.txt": b"bb"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)

    p = local_path(project, h)
    stamp = layout.checkout_stamp(project, h)
    assert stamp.is_file(), "a built checkout is stamped"
    assert not (p / stamp.name).exists(), "the stamp is beside the checkout, not in it"
    before = _files_stat(p)
    stamp_before = stamp.stat().st_mtime_ns

    assert local_path(project, h) == p
    assert _files_stat(p) == before, "an intact checkout is not rebuilt"
    assert stamp.stat().st_mtime_ns == stamp_before

    _same_size_tamper_keeping_mtime(p / "a.csv", b"X\n")
    assert local_path(project, h) == p
    assert (p / "a.csv").read_bytes() == b"X\n", (
        "the stamped read did not re-read member contents"
    )

    stamp.unlink()
    assert local_path(project, h) == p
    assert _tree_bytes(p) == _tree_bytes(src), (
        "without a stamp the checkout is fully verified"
    )
    assert stamp.is_file()


@pytest.mark.parametrize("damage", ["missing", "extra", "size", "mtime"])
def test_a_damaged_stamped_checkout_is_rebuilt(tmp_path, damage):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "nested/b.txt": b"bb"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)
    p = local_path(project, h)

    target = p / "nested" / "b.txt"
    os.chmod(target.parent, 0o755)
    os.chmod(target, 0o644)
    if damage == "missing":
        target.unlink()
    elif damage == "extra":
        (p / "nested" / "stray.txt").write_bytes(b"s")
    elif damage == "size":
        target.write_bytes(b"longer")
    else:
        # Same size, new mtime: an ordinary edit.
        target.write_bytes(b"XX")
        st = target.stat()
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    assert local_path(project, h) == p
    assert _tree_bytes(p) == _tree_bytes(src)
