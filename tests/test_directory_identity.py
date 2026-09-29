"""A directory's identity is DVC's ``.dir`` digest, and unstorable content is refused.

Covers the identity half of directory reproducibility: the digest wfc
computes with the standard library equals the one ``dvc_data`` computes
over the same tree (so DVC takes its directory branch on our hash), and a
symlink, a case-only name collision and an empty directory are refused by
path. A file and a directory never share an identity.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.identity import (
    DirectoryContentError,
    check_case_collisions,
    directory_manifest,
    hash_file,
    hash_path,
    is_directory_hash,
)


def _tree(root: Path) -> Path:
    """A directory exercising nesting, dotfiles, CRLF bytes and a non-ASCII name."""
    src = root / "src"
    (src / "sub" / "deep").mkdir(parents=True)
    (src / "a.csv").write_bytes(b"id,v\r\n1,2\n")
    (src / ".hidden").write_bytes(b"x")
    (src / "sub" / "B.txt").write_bytes(b"hello")
    (src / "sub" / "deep" / "z").write_bytes(b"")
    (src / "sub-a").mkdir()
    (src / "sub-a" / "f").write_bytes(b"q")
    (src / "é.txt").write_bytes(b"u")
    (src / "empty-subdir").mkdir()
    return src


@workflow(
    purpose="wfc's directory digest equals dvc_data's own tree digest over the "
            "same directory, and the manifest bytes wfc stores are the bytes "
            "DVC would store, so DVC takes its directory branch on wfc's hash",
    inputs="a directory with nesting, a dotfile, CRLF bytes, a non-ASCII name "
           "and an empty subdirectory",
    outputs="equal .dir hashes and byte-identical manifests",
)
def test_directory_digest_matches_dvc_data(tmp_path):
    from dvc_data.hashfile.build import build
    from dvc_data.hashfile.db import HashFileDB
    from dvc_objects.fs.local import LocalFileSystem

    src = _tree(tmp_path)
    fs = LocalFileSystem()
    odb = HashFileDB(fs, str(tmp_path / "odb"))
    _, _, obj = build(odb, str(src), fs, "md5")

    ours = directory_manifest(src)
    assert ours.hash == obj.hash_info.value
    assert ours.manifest_bytes == obj.as_bytes()


def test_directory_manifest_counts_every_file_and_ignores_empty_subdirs(tmp_path):
    src = _tree(tmp_path)
    m = directory_manifest(src)
    rels = [rel for rel, _ in m.entries]
    assert ".hidden" in rels
    assert "sub/deep/z" in rels
    assert not any(r.startswith("empty-subdir") for r in rels)
    assert m.file_count == 6
    assert m.total_size == sum(
        p.stat().st_size for p in src.rglob("*") if p.is_file()
    )
    assert m.newest_mtime == max(
        p.stat().st_mtime for p in src.rglob("*") if p.is_file()
    )


def test_a_file_and_a_directory_never_share_an_identity(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "only").write_bytes(b"same")
    f = tmp_path / "f"
    f.write_bytes(b"same")
    assert is_directory_hash(hash_path(d))
    assert not is_directory_hash(hash_path(f))
    assert hash_path(f) == hash_file(f)


def test_an_empty_directory_is_refused_by_path(tmp_path):
    d = tmp_path / "empty"
    (d / "only-dirs").mkdir(parents=True)
    with pytest.raises(DirectoryContentError) as exc:
        hash_path(d)
    assert str(d) in str(exc.value)


def test_a_symlink_inside_is_refused_by_path(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "real").write_bytes(b"x")
    link = d / "link"
    try:
        os.symlink(d / "real", link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this OS/user cannot create symlinks ({exc})")
    with pytest.raises(DirectoryContentError) as exc:
        directory_manifest(d)
    assert str(link) in str(exc.value)


def test_names_differing_only_by_case_are_refused(tmp_path):
    # The pure rule: Windows and macOS default filesystems cannot hold the
    # pair, so the check is driven with the names directly.
    with pytest.raises(DirectoryContentError) as exc:
        check_case_collisions(["Data.csv", "data.csv"], tmp_path)
    assert "Data.csv" in str(exc.value) and "data.csv" in str(exc.value)
    check_case_collisions(["a.csv", "b.csv"], tmp_path)


def test_names_differing_only_by_case_are_refused_on_disk(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "Data.csv").write_bytes(b"1")
    if (d / "data.csv").exists():
        pytest.skip("case-insensitive filesystem: the pair cannot exist here")
    (d / "data.csv").write_bytes(b"2")
    with pytest.raises(DirectoryContentError):
        directory_manifest(d)
