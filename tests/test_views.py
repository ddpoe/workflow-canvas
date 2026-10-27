"""A view of a cache object is read-only, and making or removing one costs the object no re-hash.

``make_view`` makes a restored sample or a checkout member a reflink, a
hardlink or a copy of its cache object. A hardlink shares the object's
inode, so a link made or removed moves the change time the object's stamp
records: the stamp is kept current when only that moved, and a real write
through the view is still found by the object's next check.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from wfc import layout
from wfc.identity import hash_file
from wfc.storage import links
from wfc.storage.cache import (
    ChangedEntryError,
    _check_object,
    cache_file,
    make_view,
    remove_view,
)
from wfc.storage.integrity import CHANGED, INTACT

SETTING = "stat-then-hash"


def _cached_file(tmp_path: Path, data: bytes = b"id,value\n1,A\n") -> tuple[Path, str]:
    """A project whose cache holds one stamped file object; returns (project, md5)."""
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "f.csv"
    src.write_bytes(data)
    md5 = hash_file(src)
    cache_file(src, md5, project, move=False)
    assert layout.cache_object_stamp(project, md5).is_file()
    return project, md5


def _write_through(view: Path) -> None:
    """Unprotect a view and rewrite it in place, same size, mtime put back."""
    st = view.stat()
    os.chmod(view, 0o644)
    view.write_bytes(bytes(b ^ 0x01 for b in view.read_bytes()))
    os.utime(view, ns=(st.st_atime_ns, st.st_mtime_ns))


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _save_over(path: Path, data: bytes) -> None:
    """Save other bytes at a view's path as a new file, never writing into the view."""
    from wfc.storage.cache import open_dirs

    open_dirs(path.parent)
    tmp = path.with_name(path.name + ".edited")
    tmp.write_bytes(data)
    links.replace_over(tmp, path)


def _tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


def _view_temps(directory: Path) -> list[Path]:
    """Every view under construction left under ``directory``."""
    from wfc.layout.tree import VIEW_TEMP_SUFFIX

    return [p for p in directory.rglob("*") if p.name.endswith(VIEW_TEMP_SUFFIX)]


def _assert_read_only_view(path: Path, obj: Path) -> None:
    assert path.read_bytes() == obj.read_bytes()
    assert not os.access(path, os.W_OK), f"{path} is read-only"
    assert not os.access(obj, os.W_OK), "the cache object stays read-only"
    # The view's own folder is read-only; the tier is probed beside it.
    beside = next(d for d in path.parents if os.access(d, os.W_OK))
    if links.probe_pair(obj.parent, beside).tier == links.TIER_HARDLINK:
        assert os.path.samefile(path, obj), f"{path} is a hardlink of its cache object"


def test_a_view_made_and_removed_costs_its_object_no_re_hash(tmp_path):
    project, md5 = _cached_file(tmp_path)
    obj = layout.dvc_cache_entry(project, md5)
    assert _check_object(project, md5, SETTING).hashed is False

    dest = tmp_path / "work" / "view.csv"
    tier = make_view(md5, dest, project, setting=SETTING)
    assert tier in (links.TIER_REFLINK, links.TIER_HARDLINK, links.TIER_COPY)
    assert dest.read_bytes() == obj.read_bytes()
    assert not os.access(dest, os.W_OK), "a view is read-only"
    verdict = _check_object(project, md5, SETTING)
    assert (verdict.status, verdict.hashed) == (INTACT, False), "making the view cost a re-hash"

    remove_view(dest, project, md5)
    assert not dest.exists()
    verdict = _check_object(project, md5, SETTING)
    assert (verdict.status, verdict.hashed) == (INTACT, False), "removing the view cost a re-hash"
    assert not os.access(obj, os.W_OK), "the cache object stays read-only after the view goes"


def test_a_write_through_a_hardlinked_view_is_still_found_after_the_view_is_removed(tmp_path):
    project, md5 = _cached_file(tmp_path)
    dest = tmp_path / "work" / "view.csv"
    if make_view(md5, dest, project, setting=SETTING) != links.TIER_HARDLINK:
        pytest.skip("this filesystem makes no hardlink view")
    _write_through(dest)
    remove_view(dest, project, md5)

    verdict = _check_object(project, md5, SETTING)
    assert (verdict.status, verdict.hashed) == (CHANGED, True), (
        "the stamp must not be refreshed over a real write")
    with pytest.raises(ChangedEntryError):
        make_view(md5, tmp_path / "work" / "again.csv", project, setting=SETTING)
    assert not (tmp_path / "work" / "again.csv").exists(), "a changed object is never viewed"


@pytest.mark.skipif(sys.platform != "darwin", reason="the reflink tier is exercised on macOS (APFS clonefile)")
def test_a_view_on_apfs_is_a_reflink(tmp_path):
    project, md5 = _cached_file(tmp_path)
    obj = layout.dvc_cache_entry(project, md5)
    dest = tmp_path / "work" / "view.csv"
    assert make_view(md5, dest, project, setting=SETTING) == links.TIER_REFLINK
    assert dest.read_bytes() == obj.read_bytes()
    assert not os.path.samefile(dest, obj), "a reflink is an inode of its own"
    assert not os.access(dest, os.W_OK)


@pytest.mark.skipif(_is_root(), reason="root bypasses mode bits")
@workflow(
    purpose="a restored sample view cannot be opened for writing by its "
            "owner, so the cache entry it views is unchanged; its folder "
            "refuses a new file (POSIX; Windows ignores the read-only "
            "attribute on directories, so that leg is skipped there)",
    inputs="a file sample registered and restored through production",
    outputs="PermissionError on opening the view for writing and on adding "
            "a file to its folder; the cache entry's bytes unchanged",
)
def test_a_restored_sample_view_refuses_a_write_and_its_entry_is_unchanged(tmp_project):
    from wfc.persistence import Sample, get_session
    from wfc.registration import register_sample
    from wfc.storage import restore_sample
    from sqlmodel import select

    _ = Step(step_num=1, name="Register and restore a file sample",
             purpose="Production registration and restore make the view")
    src = tmp_project.parent / "s1.csv"
    src.write_bytes(b"id,value\n1,S1\n")
    register_sample("S1", src, project_root=tmp_project)
    restore_sample("S1", project_root=tmp_project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "S1")).one()
    view = layout.restored_sample_path(tmp_project, "S1", row.registered_path)
    obj = layout.dvc_cache_entry(tmp_project, row.content_hash)
    genuine = obj.read_bytes()

    _ = Step(step_num=2, name="Open the view for writing",
             purpose="The write fails; the cache entry keeps its bytes")
    with pytest.raises(PermissionError):
        with open(view, "r+b") as handle:
            handle.write(b"X")
    assert obj.read_bytes() == genuine and view.read_bytes() == genuine

    _ = Step(step_num=3, name="Add a file to the view's folder",
             purpose="The restored sample's folder is read-only (POSIX)")
    if sys.platform == "win32":
        return  # Windows ignores the read-only attribute on directories.
    with pytest.raises(PermissionError):
        (view.parent / "added.csv").write_bytes(b"x")


def _register_and_restore(project: Path, name: str, data: bytes) -> tuple[Path, Path]:
    """Register a file sample and restore it through production; returns (view, object)."""
    from sqlmodel import select

    from wfc.persistence import Sample, get_session
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    src = project.parent / f"{name}.csv"
    src.write_bytes(data)
    register_sample(name, src, project_root=project)
    restore_sample(name, project_root=project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).one()
    return (layout.restored_sample_path(project, name, row.registered_path),
            layout.dvc_cache_entry(project, row.content_hash))


@workflow(
    purpose="a prune that removes a pushed sample's cache entry takes the "
            "entry's views with it: the restored sample's folder (data and "
            "readiness sentinel) and stamp, and its directory checkout with "
            "its stamp; a kept entry's object stays read-only and its view stays",
    inputs="a restored directory sample recorded pushed, with its checkout "
           "built; a restored file sample never pushed",
    outputs="the pushed entry, its objects and both its views gone; the kept "
            "sample untouched",
)
def test_a_pruned_entry_takes_its_restored_view_and_checkout(tmp_project, capsys):
    from sqlmodel import select

    from wfc.persistence import InUseKind, PushStatus, Sample, get_session
    from wfc.registration import register_sample
    from wfc.storage import restore_sample
    from wfc.storage.cache import local_path, manifest_members
    from wfc.storage.in_use import hold_for_pipeline
    from wfc.storage.prune import cache_prune, prune_dvc_cache

    口 = Step(step_num=1, name="Restore a directory sample and build its checkout",
             purpose="Both views of one entry, made by production")
    src = tmp_project.parent / "D1"
    (src / "sub").mkdir(parents=True)
    (src / "a.csv").write_bytes(b"a\n")
    (src / "sub" / "b.csv").write_bytes(b"b\n")
    register_sample("D1", src, project_root=tmp_project)
    restore_sample("D1", project_root=tmp_project)
    held_view, _held_obj = _register_and_restore(tmp_project, "K1", b"held\n")
    hold_for_pipeline(InUseKind.sample_view, "K1", "pipeline-reading-k1")
    with get_session() as session:
        rows = {r.name: r for r in session.exec(select(Sample)).all()}
    assert {r.push_status for r in rows.values()} == {PushStatus.pushed.value}, (
        "registration pushes to the project's archive")
    h = rows["D1"].content_hash
    checkout = local_path(tmp_project, h)
    members = manifest_members(tmp_project, h)
    assert checkout.is_dir() and members
    assert layout.sample_ready_sentinel(tmp_project, "D1").exists()

    口 = Step(step_num=2, name="Dry-run the prune",
             purpose="The dry run lists the views the prune would remove with "
                     "the entry, not a held one, and removes nothing")
    assert cache_prune(include_local=True, dry_run=True) == 0
    out = capsys.readouterr().out
    view_lines = out.split("Views removed with them", 1)[1].splitlines()
    assert view_lines[0].endswith(": 2"), out
    listed = {line.strip() for line in view_lines[1:3]}
    assert listed == {str(layout.sample_dir(tmp_project, "D1")), str(checkout)}, out
    assert str(layout.sample_dir(tmp_project, "K1")) not in out
    assert checkout.is_dir() and layout.sample_dir(tmp_project, "D1").is_dir()

    口 = Step(step_num=3, name="Prune the local cache",
             purpose="The pushed entry goes, and its views with it")
    prune_dvc_cache(tmp_project)
    assert not layout.dvc_cache_entry(tmp_project, h).exists()
    assert not any(layout.dvc_cache_entry(tmp_project, m).exists() for m in members)
    assert not layout.sample_dir(tmp_project, "D1").exists(), "the restored view outlived its entry"
    assert not layout.sample_stamp(tmp_project, "D1").exists()
    assert not checkout.exists(), "the checkout outlived its entry"
    assert not layout.checkout_stamp(tmp_project, h).exists()

    口 = Step(step_num=4, name="A held view stays",
             purpose="A sample a pipeline holds keeps its view, read-only, with "
                     "the bytes its pruned entry held")
    assert held_view.read_bytes() == b"held\n"
    assert not os.access(held_view, os.W_OK)
    assert layout.sample_ready_sentinel(tmp_project, "K1").exists()


@workflow(
    purpose="a restored sample's repair interrupted between the view's build "
            "and its rename leaves the old copy whole at its path, never a gap "
            "or a half-built file, and no view under construction; the next "
            "check repairs it as a read-only view",
    inputs="a restored file sample saved over with other bytes, and the rename "
           "of its rebuilt view interrupted once",
    outputs="the check raising with the old bytes still at the path and no "
            "temporary left; then a repaired read-only view of the cache entry",
)
def test_an_interrupted_sample_repair_leaves_the_old_copy_whole(tmp_project, monkeypatch):
    from tests.fixtures import fakes
    from wfc.storage.restore import SAMPLE_REPAIRED, check_sample

    _ = Step(step_num=1, name="Restore a sample and save other bytes at its path",
             purpose="The copy is no longer what wfc registered; the cache "
                     "entry is intact")
    genuine = b"id,value\n1,S1\n"
    view, obj = _register_and_restore(tmp_project, "S1", genuine)
    _save_over(view, b"id,value\n1,EDITED-AND-LONGER\n")
    edited = view.read_bytes()

    _ = Step(step_num=2, name="Interrupt the repair's rename",
             purpose="The check raises; the old copy stands whole at its path")
    interrupted = fakes.interrupt_view_rename(monkeypatch)
    with pytest.raises(OSError):
        check_sample("S1", project_root=tmp_project)
    assert [dst for _src, dst in interrupted] == [view], "the repair reached its rename"
    assert view.read_bytes() == edited, "the old copy is whole, never absent or partial"
    assert _view_temps(view.parent) == [], "no view under construction is left"
    assert obj.read_bytes() == genuine

    _ = Step(step_num=3, name="Check again",
             purpose="The next check repairs the copy as a read-only view")
    assert check_sample("S1", project_root=tmp_project).status == SAMPLE_REPAIRED
    _assert_read_only_view(view, obj)


def _folder_read_only(folder: Path) -> bool:
    """Whether a folder carries no write bit (on Windows, its read-only attribute)."""
    import stat

    return not stat.S_IMODE(folder.stat().st_mode) & 0o222


@pytest.mark.skipif(_is_root(), reason="root bypasses mode bits")
@workflow(
    purpose="a restored sample's folder is read-only again after a step's "
            "check repairs the copy and after wfc verify --repair restores it; "
            "it refuses a new file (POSIX; Windows ignores the read-only "
            "attribute on directories, so that leg is skipped there)",
    inputs="a file sample registered and restored through production, saved "
           "over with other bytes before each repair",
    outputs="after each repair, the folder without a write bit and refusing "
            "a new file with PermissionError",
)
def test_a_repaired_sample_folder_is_read_only_again(tmp_project, capsys):
    from wfc.cli import cli_main
    from wfc.storage.restore import SAMPLE_REPAIRED, check_sample

    _ = Step(step_num=1, name="Restore a sample and save other bytes at its path",
             purpose="Saving over the view opens its folder, as an editor's save does")
    view, _obj = _register_and_restore(tmp_project, "S1", b"id,value\n1,S1\n")
    folder = layout.sample_dir(tmp_project, "S1")
    _save_over(view, b"id,value\n1,EDITED\n")
    assert not _folder_read_only(folder), "saving over the view opened the folder"

    _ = Step(step_num=2, name="A step's check repairs the copy",
             purpose="The folder is read-only again after the repair")
    assert check_sample("S1", project_root=tmp_project).status == SAMPLE_REPAIRED
    assert _folder_read_only(folder), "the check's repair left the folder writable"
    if sys.platform != "win32":  # Windows ignores the read-only attribute on directories.
        with pytest.raises(PermissionError):
            (folder / "added.csv").write_bytes(b"x")

    _ = Step(step_num=3, name="wfc verify --repair restores the copy",
             purpose="The folder is read-only again after the verify repair")
    _save_over(view, b"id,value\n1,EDITED-AGAIN\n")
    assert not _folder_read_only(folder)
    capsys.readouterr()
    assert cli_main(["verify", "--repair"]) == 0, capsys.readouterr().out
    assert "sample 'S1' restored copy" in capsys.readouterr().out
    assert _folder_read_only(folder), "wfc verify --repair left the folder writable"
    if sys.platform != "win32":  # Windows ignores the read-only attribute on directories.
        with pytest.raises(PermissionError):
            (folder / "added.csv").write_bytes(b"x")


@workflow(
    purpose="a checkout's rebuild interrupted between a member's build and its "
            "rename leaves every member whole at its path, the interrupted one "
            "with its old bytes, no view under construction and no stamp; the "
            "next read rebuilds it as read-only views",
    inputs="a cached directory's stamped checkout with one member saved over, "
           "and the rename of that member's rebuilt view interrupted once",
    outputs="the read raising with each member holding its old or its new "
            "bytes, never absent; then the registered tree of read-only views",
)
def test_an_interrupted_checkout_rebuild_leaves_each_member_whole(tmp_project, monkeypatch):
    from tests.fixtures import fakes
    from wfc.identity import hash_path
    from wfc.storage.cache import local_path

    _ = Step(step_num=1, name="Check out a directory and save over one member",
             purpose="The checkout is no longer the entry; the cache objects "
                     "are intact")
    files = {"a.csv": b"1\n", "nested/b.txt": b"bb"}
    src = tmp_project.parent / "src"
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    h = hash_path(src)
    cache_file(src, h, tmp_project, move=False)
    checkout_dir = local_path(tmp_project, h)
    stamp = layout.checkout_stamp(tmp_project, h)
    assert stamp.is_file()
    _save_over(checkout_dir / "a.csv", b"stale\n")
    before = _tree(checkout_dir)

    _ = Step(step_num=2, name="Interrupt the rebuild's rename",
             purpose="The read raises; every member stands whole")
    interrupted = fakes.interrupt_view_rename(monkeypatch)
    with pytest.raises(OSError):
        local_path(tmp_project, h)
    assert len(interrupted) == 1, "the rebuild reached a member's rename"
    rel = interrupted[0][1].relative_to(checkout_dir).as_posix()
    after = _tree(checkout_dir)
    assert set(after) == set(files), "no member is absent"
    assert after[rel] == before[rel], "the interrupted member keeps its old bytes"
    for name, data in after.items():
        assert data in (before[name], files[name]), f"{name} is old or new, never partial"
    assert _view_temps(checkout_dir.parent) == [], "no view under construction is left"
    assert not stamp.exists(), "nothing vouches for a checkout mid-rebuild"

    _ = Step(step_num=3, name="Read again",
             purpose="The next read rebuilds the checkout as read-only views")
    assert local_path(tmp_project, h) == checkout_dir
    assert _tree(checkout_dir) == files
    for name in files:
        _assert_read_only_view(checkout_dir / name,
                               layout.dvc_cache_entry(tmp_project, hash_file(src / name)))
    assert stamp.is_file()


@workflow(
    purpose="where no link can be made from the cache (another filesystem), a "
            "restored sample is a read-only copy of its cache entry, and wfc "
            "doctor's views row warns naming the reason and that each item "
            "takes twice its size, without failing doctor",
    inputs="every reflink and hardlink refused with EXDEV; a file sample "
           "registered and restored through production; wfc doctor with the "
           "four machine probes stubbed ok",
    outputs="a read-only view that is not the cache object's inode; doctor "
            "exit 0 with a WARN views row naming different filesystems and "
            "twice its size",
)
def test_with_no_link_possible_views_are_copies_and_doctor_warns(
        tmp_project, monkeypatch, capsys):
    import wfc.cli as cli
    from tests.fixtures import fakes

    _ = Step(step_num=1, name="Refuse every link",
             purpose="The cache and the workspace behave as two filesystems")
    fakes.refuse_view_links(monkeypatch)

    _ = Step(step_num=2, name="Register and restore a file sample",
             purpose="The view falls back to a read-only copy")
    view, obj = _register_and_restore(tmp_project, "S1", b"id,value\n1,S1\n")
    assert view.read_bytes() == obj.read_bytes()
    assert not os.path.samefile(view, obj), "the view is a copy of its own"
    assert not os.access(view, os.W_OK), "a copy view is read-only"
    again = tmp_project.parent / "work" / "again.csv"
    assert make_view(hash_file(obj), again, tmp_project) == links.TIER_COPY

    _ = Step(step_num=3, name="Run wfc doctor",
             purpose="The views row warns naming the reason and the cost; "
                     "doctor does not fail on it")
    fakes.stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok", samples="ok")
    capsys.readouterr()
    assert cli.cli_main(["doctor"]) == 0
    row = [line for line in capsys.readouterr().out.splitlines() if "] views" in line]
    assert len(row) == 1, "doctor shows the views row"
    assert "WARN" in row[0]
    assert "different filesystems" in row[0] and "twice its size" in row[0]


def _sample_row(name: str):
    from sqlmodel import select

    from wfc.persistence import Sample, get_session

    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


def _objects_of(project: Path, content_hash: str) -> list[str]:
    from wfc.storage.cache import is_directory_hash, manifest_members

    return (sorted(manifest_members(project, content_hash))
            if is_directory_hash(content_hash) else [content_hash])


def _assert_view_gone_entry_kept(project: Path, name: str) -> None:
    """The sample's folder, sentinel and stamp are gone; each cache object reads intact with no re-hash."""
    row = _sample_row(name)
    assert not layout.sample_dir(project, name).exists(), f"{name}'s view is removed"
    assert not layout.sample_ready_sentinel(project, name).exists(), f"{name}'s sentinel is removed"
    assert not layout.sample_stamp(project, name).exists(), f"{name}'s stamp is removed"
    for md5 in _objects_of(project, row.content_hash):
        verdict = _check_object(project, md5, SETTING)
        assert (verdict.status, verdict.hashed) == (INTACT, False), (
            f"removing {name}'s view cost its cache object {md5} a re-hash")


@workflow(
    purpose="two pipelines hold one sample: the end of the first, which "
            "restored it, keeps the view the second's step reads (the second's "
            "restore rule never ran: the first's sentinel stood) and removes "
            "the sample only it held; the end of the second removes the shared "
            "view with its sentinel and stamp, the cache entry untouched",
    inputs="a file sample and a directory sample registered through production; "
           "pipeline p1 restores both, pipeline p2's step checks the file sample "
           "before it reads",
    outputs="p1's end removes only the directory sample; p2 reads the file "
            "sample whole; p2's end removes it; no in-use record is left",
)
def test_a_sample_two_pipelines_hold_stays_until_the_second_ends(tmp_project, monkeypatch):
    from wfc.persistence import InUseKind
    from wfc.registration import register_sample
    from wfc.storage import check_sample, end_pipeline_samples, restore_sample
    from wfc.storage.in_use import held_keys
    from wfc.storage.restore import SAMPLE_INTACT

    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    口 = Step(step_num=1, name="Pipeline p1 restores a file and a directory sample",
             purpose="Production registration, then the restore rule's body for p1")
    sources = tmp_project.parent / f"{tmp_project.name}-sources"
    sources.mkdir()
    src = sources / "S1.csv"
    src.write_bytes(b"id,value\n1,S1\n")
    register_sample("S1", src, project_root=tmp_project)
    tree = sources / "D1"
    (tree / "sub").mkdir(parents=True)
    (tree / "a.csv").write_bytes(b"a\n")
    (tree / "sub" / "b.csv").write_bytes(b"b\n")
    register_sample("D1", tree, project_root=tmp_project)
    restore_sample("S1", project_root=tmp_project, pipeline_id="p1")
    restore_sample("D1", project_root=tmp_project, pipeline_id="p1")
    view = layout.restored_sample_path(tmp_project, "S1", _sample_row("S1").registered_path)
    genuine = view.read_bytes()
    assert held_keys(InUseKind.sample_view) == {"S1", "D1"}

    口 = Step(step_num=2, name="Pipeline p2's step checks S1 before it reads",
             purpose="p1's sentinel stands, so p2's restore rule does not run; "
                     "the check before the read is p2's record")
    assert layout.sample_ready_sentinel(tmp_project, "S1").exists()
    assert check_sample("S1", tmp_project, SETTING, pipeline_id="p2").status == SAMPLE_INTACT

    口 = Step(step_num=3, name="Pipeline p1 ends while p2 reads S1",
             purpose="Only the sample no other pipeline holds goes")
    assert end_pipeline_samples(tmp_project, "p1") == ["D1"]
    _assert_view_gone_entry_kept(tmp_project, "D1")
    assert layout.sample_ready_sentinel(tmp_project, "S1").exists(), "p2 still needs S1's sentinel"
    assert view.read_bytes() == genuine, "p2 reads S1 whole after p1 ended"

    口 = Step(step_num=4, name="Pipeline p2 ends",
             purpose="The last holder's end removes the view, its sentinel and stamp")
    assert end_pipeline_samples(tmp_project, "p2") == ["S1"]
    _assert_view_gone_entry_kept(tmp_project, "S1")
    assert held_keys(InUseKind.sample_view) == set(), "every record of both pipelines is released"


@workflow(
    purpose="a step whose pipeline's restore rule was skipped (another "
            "pipeline's sentinel stood) and whose view that pipeline's end "
            "then removed restores the view before it reads; a restore "
            "outside a pipeline records no holder",
    inputs="a file sample restored standalone, then held by pipeline p1, whose "
           "end removes it",
    outputs="the standalone restore leaves no record; pipeline p2's check "
            "finds the view gone, restores it read-only with its sentinel, and "
            "p2's end removes it again",
)
def test_a_step_whose_view_another_pipeline_removed_restores_it(tmp_project, monkeypatch):
    from wfc.persistence import InUseKind
    from wfc.storage import check_sample, end_pipeline_samples, restore_sample
    from wfc.storage.in_use import held_keys
    from wfc.storage.restore import SAMPLE_INTACT

    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)

    口 = Step(step_num=1, name="Restore a sample standalone",
             purpose="wfc restore-sample outside a pipeline records no holder")
    view, obj = _register_and_restore(tmp_project, "S1", b"id,value\n1,S1\n")
    assert held_keys(InUseKind.sample_view) == set()

    口 = Step(step_num=2, name="Pipeline p1 reads it and ends",
             purpose="p1's end removes the view p2's rule saw ready")
    assert check_sample("S1", tmp_project, SETTING, pipeline_id="p1").status == SAMPLE_INTACT
    assert end_pipeline_samples(tmp_project, "p1") == ["S1"]
    assert not view.exists()

    口 = Step(step_num=3, name="Pipeline p2's step checks before it reads",
             purpose="The view is restored from the cache entry, read-only")
    assert check_sample("S1", tmp_project, SETTING, pipeline_id="p2").status == SAMPLE_INTACT
    _assert_read_only_view(view, obj)
    assert layout.sample_ready_sentinel(tmp_project, "S1").exists()
    assert layout.sample_stamp(tmp_project, "S1").exists()
    assert end_pipeline_samples(tmp_project, "p2") == ["S1"]
    _assert_view_gone_entry_kept(tmp_project, "S1")
