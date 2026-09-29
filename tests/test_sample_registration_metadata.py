"""Sample registration: directory row fields, the manifest, refusals before caching,
and a restore path that follows the project.

Every row here is written by ``register_sample`` (the production path).
Declared fixture deviation: ``_make_path_absolute`` rewrites one row's
``registered_path`` to the absolute shape rows had before this cycle, which
production no longer writes.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from wfc import layout
from wfc.identity import DirectoryContentError, hash_path
from wfc.persistence import Sample, get_session
from wfc.registration import register_sample
from wfc.registration.sample_manifest import SampleManifestError, read_sample_manifest
from tests.fixtures.routes import sample_source_dir


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _row(name: str) -> Sample:
    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


def _cache_objects(project: Path) -> set[Path]:
    cache = project / ".dvc" / "cache" / "files" / "md5"
    return {p for p in cache.rglob("*") if p.is_file()} if cache.exists() else set()


@workflow(purpose="A directory sample's row records file_type directory, the total "
                  "size, the file count and the newest mtime, each equal to the "
                  "source's own values read before the copy, and a registered "
                  "path relative to the project root")
def test_a_directory_sample_row_carries_the_source_metadata(tmp_project):
    """P3-6 (D-162 row fields)."""
    _ = Step(step_num=1, name="A source directory with known sizes and mtimes",
             purpose="Three files, one nested, one dotfile")
    src = _write(sample_source_dir(tmp_project) / "plates",
                 {"a.csv": b"12345", "sub/b.csv": b"123", ".meta": b"1"})
    os.utime(src / "sub" / "b.csv", (1_700_000_000, 1_700_000_000))
    files = [p for p in src.rglob("*") if p.is_file()]
    newest = max(p.stat().st_mtime for p in files)

    _ = Step(step_num=2, name="Register it", purpose="The production path")
    register_sample(name="plates", source_path=src, project_root=tmp_project)

    _ = Step(step_num=3, name="The row matches the source",
             purpose="Directory fields from the source; the path is relative")
    row = _row("plates")
    assert row.file_type == "directory"
    assert row.file_size == 9
    assert row.file_count == 3
    assert row.file_mtime == newest
    assert row.registered_path == "data/samples/plates/plates"
    assert row.content_hash == hash_path(src)


def test_a_file_sample_row_leaves_file_count_null(tmp_project):
    """D-7: a file row keeps its fields; file_count is not applicable."""
    src = _write(sample_source_dir(tmp_project), {"one.csv": b"x\n"}) / "one.csv"
    register_sample(name="one", source_path=src, project_root=tmp_project)
    row = _row("one")
    assert (row.file_type, row.file_size, row.file_count) == ("csv", 2, None)


@workflow(purpose="The same content registered with and without a manifest "
                  "description: the description round-trips, and the content hash "
                  "and the run input fingerprint are identical")
def test_a_description_is_metadata_never_identity(tmp_project, tmp_path):
    """P3-7."""
    from wfc.execution.claim import input_fingerprint_from_rows

    _ = Step(step_num=1, name="Register one directory twice",
             purpose="Once with a manifest description, once with none")
    src = _write(sample_source_dir(tmp_project) / "tree", {"a.csv": b"a\n"})
    manifest = tmp_path / "sample.yaml"
    manifest.write_text("description: Plate 3, day 2 imaging\n", encoding="utf-8")
    register_sample(name="described", source_path=src, project_root=tmp_project,
                    manifest_path=manifest)
    register_sample(name="plain", source_path=src, project_root=tmp_project)

    _ = Step(step_num=2, name="Metadata differs; identity does not",
             purpose="The claim phase's own fingerprint reader")
    described, plain = _row("described"), _row("plain")
    assert described.description == "Plate 3, day 2 imaging"
    assert plain.description is None
    assert described.content_hash == plain.content_hash
    assert (input_fingerprint_from_rows([], [("data", "described")], step="s")
            == input_fingerprint_from_rows([], [("data", "plain")], step="s"))


@workflow(purpose="A manifest with an unknown key is refused by the key's name, "
                  "and nothing is cached and no row is written")
def test_an_unknown_manifest_key_is_refused_before_caching(tmp_project, tmp_path):
    """P3-8 (no manifest gives NULL: see the P3-7 witness's plain row)."""
    _ = Step(step_num=1, name="A manifest naming a key registration does not know",
             purpose="Strict: only description")
    src = _write(sample_source_dir(tmp_project), {"s.csv": b"s\n"}) / "s.csv"
    manifest = tmp_path / "sample.yaml"
    manifest.write_text("description: ok\norganism: human\n", encoding="utf-8")
    before = _cache_objects(tmp_project)

    _ = Step(step_num=2, name="Registration refuses it by name",
             purpose="Before any cache write")
    with pytest.raises(SampleManifestError, match="'organism'"):
        register_sample(name="s", source_path=src, project_root=tmp_project,
                        manifest_path=manifest)
    assert _cache_objects(tmp_project) == before
    with get_session() as session:
        assert session.exec(select(Sample)).all() == []


@pytest.mark.parametrize("text, match", [
    ("- a\n- b\n", "mapping"),
    ("description: [1, 2]\n", "must be a string"),
    ("description: 'x\n", "not valid YAML"),
])
def test_the_manifest_reader_refuses_malformed_manifests(tmp_path, text, match):
    """Tier 1: the reader's refusals, each naming the file."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(SampleManifestError, match=match) as exc:
        read_sample_manifest(manifest)
    assert exc.value.path == manifest


def test_an_empty_manifest_has_no_description(tmp_path):
    """Tier 1: an empty file is a manifest with no keys."""
    manifest = tmp_path / "m.yaml"
    manifest.write_text("", encoding="utf-8")
    assert read_sample_manifest(manifest) is None


@workflow(purpose="A directory whose content cannot be identified is refused at "
                  "registration by path, before anything is cached: an empty "
                  "directory, a symlink inside, a case-only name collision (each "
                  "skipped, with its reason, where the OS cannot express it)")
@pytest.mark.parametrize("case", ["empty", "symlink", "case-collision"])
def test_unidentifiable_directory_content_is_refused_at_registration(tmp_project, case):
    """P3-5, registration leg (the collection leg is in the execution tests)."""
    _ = Step(step_num=1, name="Build the offending directory",
             purpose="Skip where the filesystem cannot hold it")
    src = sample_source_dir(tmp_project) / "bad"
    src.mkdir(parents=True)
    if case == "empty":
        (src / "only_dirs" / "nested").mkdir(parents=True)
        offender = src
    elif case == "symlink":
        _write(src, {"real.csv": b"r\n"})
        offender = src / "link.csv"
        try:
            offender.symlink_to(src / "real.csv")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"this host cannot create a symlink without privilege: {exc}")
    else:
        _write(src, {"Data.csv": b"1\n"})
        try:
            (src / "data.csv").write_bytes(b"2\n")
        except OSError as exc:
            pytest.skip(f"cannot create a case-only pair: {exc}")
        if len(list(src.iterdir())) < 2:
            pytest.skip("case-insensitive filesystem: a case-only pair cannot exist "
                        "(the pure check is witnessed in test_directory_identity)")
        offender = src
    before = _cache_objects(tmp_project)

    _ = Step(step_num=2, name="Registration refuses by path",
             purpose="DirectoryContentError; the cache is unchanged; no row")
    with pytest.raises(DirectoryContentError) as exc:
        register_sample(name="bad", source_path=src, project_root=tmp_project)
    assert Path(exc.value.path) == offender or str(offender) in str(exc.value)
    assert _cache_objects(tmp_project) == before
    with get_session() as session:
        assert session.exec(select(Sample)).all() == []


def _make_path_absolute(name: str, project: Path) -> None:
    """Declared deviation: the pre-cycle absolute registered_path shape."""
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).one()
        row.registered_path = str(project / row.registered_path)
        session.add(row)
        session.commit()


@workflow(purpose="A sample restores under the project's current root after the "
                  "project directory moves, because its registered path is "
                  "relative; a row whose registered path is absolute is refused "
                  "as a malformed record naming re-registration")
def test_restore_follows_a_moved_project(tmp_project, monkeypatch, capsys):
    """P3-10 (D-90 / D-6)."""
    from wfc.storage import restore_sample

    _ = Step(step_num=1, name="Register a file and a directory sample",
             purpose="Rows written with relative registered paths")
    src_root = sample_source_dir(tmp_project)
    f = _write(src_root, {"f.csv": b"f\n"}) / "f.csv"
    d = _write(src_root / "d", {"x.csv": b"x\n", "y/z.csv": b"z\n"})
    register_sample(name="f", source_path=f, project_root=tmp_project)
    register_sample(name="d", source_path=d, project_root=tmp_project)

    _ = Step(step_num=2, name="Move the project",
             purpose="The tree is copied to a new root (Windows cannot move a "
                     "directory whose database is open); the rows are unchanged")
    moved = tmp_project.parent / "moved_project"
    shutil.copytree(tmp_project, moved)

    _ = Step(step_num=3, name="Restore both under the new root",
             purpose="The bytes land under the moved project, not the old one")
    restore_sample(name="f", project_root=moved)
    restore_sample(name="d", project_root=moved)
    assert (layout.sample_dir(moved, "f") / "f.csv").read_bytes() == b"f\n"
    tree = layout.sample_dir(moved, "d") / "d"
    assert (tree / "y" / "z.csv").read_bytes() == b"z\n"
    assert not (layout.sample_dir(tmp_project, "f") / "f.csv").exists()

    _ = Step(step_num=4, name="An absolute registered path is refused",
             purpose="Malformed record; the repair is re-registration")
    _make_path_absolute("f", moved)
    with pytest.raises(SystemExit):
        restore_sample(name="f", project_root=moved)
    err = capsys.readouterr().err
    assert "malformed record" in err and "Re-register the sample 'f'" in err
