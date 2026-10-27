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
from wfc.storage import links
from wfc.storage.cache import (
    ChangedEntryError,
    MalformedEntryError,
    cache_file,
    checkout,
    entry_is_complete,
    local_path,
    open_dirs,
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


def _replace_view(p: Path, data: bytes, *, keep_mtime: bool = False) -> None:
    """Save other bytes at a view's path as a new file, as an editor that saves by rename does.

    A view may share its inode with the cache object, so the edit never
    writes into it or chmods it: the cache object stays intact. With
    ``keep_mtime`` the new file is given the old one's mtime.
    """
    before = p.stat()
    open_dirs(p.parent)
    tmp = p.with_name(p.name + ".edited")
    tmp.write_bytes(data)
    if keep_mtime:
        os.utime(tmp, ns=(before.st_atime_ns, before.st_mtime_ns))
    links.replace_over(tmp, p)


def _assert_view(path: Path, project: Path, md5: str) -> None:
    """``path`` is a read-only view of its cache object, the same inode where links work."""
    obj = layout.dvc_cache_entry(project, md5)
    assert path.read_bytes() == obj.read_bytes()
    assert not os.access(path, os.W_OK), f"{path} is read-only"
    assert not os.access(obj, os.W_OK), "the cache object stays read-only"
    # The view's own folder is read-only; the tier is probed beside it.
    beside = next(d for d in path.parents if os.access(d, os.W_OK))
    if links.probe_pair(obj.parent, beside).tier == links.TIER_HARDLINK:
        assert os.path.samefile(path, obj), f"{path} is a hardlink of its cache object"


def test_an_edit_the_moment_a_directory_is_cached_matches_no_member_stamp(tmp_path):
    """A directory's member stamps go in together, once the clock is past every member's record."""
    from wfc.storage.integrity import entry_record, read_stamp

    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "sub/b.txt": b"bb", "c.txt": b"ccc"})
    cache_file(src, hash_path(src), project, move=False)

    for rel, md5 in directory_manifest(src).entries:
        obj = layout.dvc_cache_entry(project, md5)
        stamped = read_stamp(layout.cache_object_stamp(project, md5), md5)
        assert stamped is not None, f"{rel}'s object is stamped"
        st = obj.stat()
        os.chmod(obj, 0o644)
        obj.write_bytes(bytes(b ^ 0x01 for b in obj.read_bytes()))
        os.utime(obj, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert entry_record(obj) != stamped, f"an edit of {rel}'s object right after is seen"


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
    members = dict(directory_manifest(src).entries)
    for rel, md5 in members.items():
        _assert_view(out / rel, project, md5)
    _replace_view(out / "a.csv", b"stale")
    assert restore_from_cache(h, out, project)
    assert _tree_bytes(out) == _tree_bytes(src)
    _assert_view(out / "a.csv", project, members["a.csv"])


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
    """Replace a checkout member with other bytes of its length, its mtime put back."""
    before = p.stat()
    assert len(data) == before.st_size
    _replace_view(p, data, keep_mtime=True)
    after = p.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def _append_config(root: Path, text: str) -> None:
    """Append a section to the project's wf-canvas.toml, as a user does."""
    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")


@pytest.mark.parametrize("setting", ["stat-then-hash", "hash"])
@workflow(
    purpose="a same-size edit of a checkout member with its mtime restored is "
            "caught and the checkout rebuilt: by the stat fields' change time "
            "under the default (ctime on Linux and macOS, NTFS's ChangeTime "
            "on Windows), by the re-hash under hash",
    inputs="a cached directory, its stamped checkout, one member replaced at "
           "its path by bytes of the same length with its mtime set back (the "
           "cache entry intact)",
    outputs="the next read serves the genuine bytes, the member a read-only "
            "view again, and the checkout is stamped again",
)
def test_a_same_size_checkout_edit_with_the_mtime_restored_is_caught(tmp_project, setting):
    """Flips adversarial B3d: the stamp no longer vouches for size and mtime alone."""
    project = tmp_project
    if setting == "hash":
        _append_config(project, '[integrity]\ninputs = "hash"\n')
    src = _source(project.parent, "src", {"a.csv": b"1\n", "nested/b.txt": b"bb"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)

    p = local_path(project, h)
    stamp = layout.checkout_stamp(project, h)
    assert stamp.is_file(), "a built checkout is stamped"
    assert not (p / stamp.name).exists(), "the stamp is beside the checkout, not in it"
    assert local_path(project, h) == p

    _same_size_tamper_keeping_mtime(p / "a.csv", b"X\n")
    assert local_path(project, h) == p
    assert _tree_bytes(p) == _tree_bytes(src), "the edited checkout was rebuilt"
    _assert_view(p / "a.csv", project, hash_file(src / "a.csv"))
    assert stamp.is_file()


@workflow(
    purpose="a checkout is built only from member objects whose copied bytes "
            "match their manifest entries: a changed member object is refused, "
            "nothing is placed or stamped, and the next read refuses again",
    inputs="a cached directory whose member object is rewritten in the cache",
    outputs="ChangedEntryError naming the member; no checkout, no stamp",
)
def test_a_checkout_over_a_changed_member_object_is_refused_and_not_stamped(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "b.txt": b"bb"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)
    member = hash_file(src / "a.csv")
    obj = layout.dvc_cache_entry(project, member)
    os.chmod(obj, 0o644)
    obj.write_bytes(b"9\n")

    for _attempt in range(2):
        with pytest.raises(ChangedEntryError) as refusal:
            local_path(project, h)
        assert refusal.value.objects == [member]
        assert member in str(refusal.value)
        assert not layout.checkout_dir(project, h).exists()
        assert not layout.checkout_stamp(project, h).exists()


def test_the_hashing_copy_refuses_bytes_that_are_not_the_callers_hash(tmp_path):
    """A caller's known md5 must be the md5 of the bytes copied; nothing is stored."""
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "f.csv"
    src.write_bytes(b"now\n")
    stale = hash_file(src)
    src.write_bytes(b"changed after hashing\n")

    with pytest.raises(ValueError, match="changed while it was being cached"):
        cache_file(src, stale, project, move=False)
    assert not layout.dvc_cache_entry(project, stale).exists()
    assert src.read_bytes() == b"changed after hashing\n"
    leftovers = [p for p in layout.dvc_cache_files_dir(project).rglob("*") if p.is_file()]
    assert leftovers == [], "no tmp file is left in the cache"


def test_a_changed_object_at_the_address_is_replaced_by_the_verified_copy(tmp_path):
    """An existing address is checked: changed bytes are replaced, then stamped."""
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "f.csv"
    src.write_bytes(b"genuine\n")
    md5 = hash_file(src)
    dest = cache_file(src, md5, project, move=False)
    os.chmod(dest, 0o644)
    dest.write_bytes(b"edited!\n")

    assert cache_file(src, md5, project, move=False) == dest
    assert dest.read_bytes() == b"genuine\n"
    assert layout.cache_object_stamp(project, md5).is_file()


@pytest.mark.parametrize("damage", ["missing", "extra", "size", "mtime"])
def test_a_damaged_stamped_checkout_is_rebuilt(tmp_path, damage):
    project = tmp_path / "proj"
    project.mkdir()
    src = _source(tmp_path, "src", {"a.csv": b"1\n", "nested/b.txt": b"bb"})
    h = hash_path(src)
    cache_file(src, h, project, move=False)
    p = local_path(project, h)

    # Damage the checkout, never its cache objects: a member is removed or
    # replaced at its path, never written through (a write through a
    # hardlinked view changes the cache entry itself).
    target = p / "nested" / "b.txt"
    open_dirs(target.parent)
    if damage == "missing":
        links.delete_file(target)
    elif damage == "extra":
        (p / "nested" / "stray.txt").write_bytes(b"s")
    elif damage == "size":
        _replace_view(target, b"longer")
    else:
        # Same size, new mtime: an ordinary edit.
        _replace_view(target, b"XX")
        st = target.stat()
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    assert local_path(project, h) == p
    assert _tree_bytes(p) == _tree_bytes(src)
    _assert_view(target, project, hash_file(src / "nested" / "b.txt"))
