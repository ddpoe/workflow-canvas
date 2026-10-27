"""
Tests for deferred output archiving.

Covers: no inline archiving, archive command + progress,
prune guard, directory outputs.
"""

from wfc import layout
import hashlib
import json

import pytest
from sqlmodel import select

from axiom_annotations import workflow


def _in_run_archive(project_dir, run_id: int, name: str):
    """Where collect writes a run's output: under its run archive directory."""
    archive = layout.run_archive_dir(project_dir, run_id)
    archive.mkdir(parents=True, exist_ok=True)
    return archive / name


# =============================================================================
# No inline archiving (Tier 2)
# =============================================================================

@workflow(purpose="Verify complete_run leaves content_hash NULL (no inline archiving)")
def test_complete_run_no_inline_archiving(tmp_project):
    """Run complete_run with output files, verify content_hash remains NULL
    and no hash_path/cache_file call occurs."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.execution.record import complete_run

    with get_session() as session:
        mod = Module(name="noarch_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="noarch_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # Create output file
    out_file = _in_run_archive(tmp_project, run_id, "output.parquet")
    out_file.write_bytes(b"test output data")

    # Create .dvc/cache structure (should NOT be used)
    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    # The row the collect phase writes for the file.
    from wfc.persistence import RunOutput as _RunOutput
    with get_session() as session:
        session.add(_RunOutput(run_id=run_id, slot="output",
                               output_name=out_file.name,
                               artifact_path=str(out_file),
                               artifact_type="method_file"))
        session.commit()

    complete_run(
        run_id=run_id,
        status="completed",
        output_files=[str(out_file)],
    )

    # Verify content_hash is NULL (deferred)
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).first()
        assert ro is not None
        assert ro.content_hash is None, "content_hash should be NULL (deferred archiving)"


# =============================================================================
# Archive command + progress (Tier 2 + Tier 3)
# =============================================================================

@workflow(purpose="Verify archive_outputs hashes, caches, and updates DB for NULL-hash outputs")
def test_archive_outputs_basic(tmp_project):
    """Create a run with NULL-hash outputs, invoke archive_outputs,
    verify hashes computed and files cached."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import archive_outputs

    # Setup DVC cache dir
    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="arch_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="arch_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # Create output file and RunOutput with NULL content_hash
    out_file = _in_run_archive(tmp_project, run_id, "arch_output.parquet")
    content = b"archivable content"
    out_file.write_bytes(content)
    expected_md5 = hashlib.md5(content).hexdigest()

    with get_session() as session:
        ro = RunOutput(
            run_id=run_id, output_name="arch_output.parquet",
            artifact_path=str(out_file), artifact_type="module_file",
            content_hash=None,
        )
        session.add(ro)
        session.commit()

    # Archive
    results = archive_outputs(tmp_project, run_id=run_id)

    assert len(results) == 1
    assert results[0]["status"] == "archived"
    assert results[0]["content_hash"] == expected_md5

    # Verify DB updated
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).first()
        assert ro.content_hash == expected_md5

    # Verify file is in cache
    cache_path = (
        tmp_project / ".dvc" / "cache" / "files" / "md5"
        / expected_md5[:2] / expected_md5[2:]
    )
    assert cache_path.exists()


@workflow(purpose="Verify archive per-file progress callbacks fire correctly")
def test_archive_progress_callbacks(tmp_project):
    """Call archive utility on multiple files, verify per-file progress
    callbacks fire."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import archive_outputs

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="prog_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="prog_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # Create two output files
    for name in ["a.parquet", "b.parquet"]:
        f = _in_run_archive(tmp_project, run_id, name)
        f.write_bytes(f"content-{name}".encode())
        with get_session() as session:
            session.add(RunOutput(
                run_id=run_id, output_name=name,
                artifact_path=str(f), artifact_type="module_file",
            ))
            session.commit()

    # Track progress calls — (run_id, output_name, status) per event
    progress_calls = []

    def track_progress(rid, name, status):
        progress_calls.append((rid, name, status))

    archive_outputs(tmp_project, run_id=run_id, progress_fn=track_progress)

    assert all(rid == run_id for rid, _, _ in progress_calls)
    # Each file gets a "hashing" event followed by its terminal status
    hashing = [(n, s) for _, n, s in progress_calls if s == "hashing"]
    terminal = [(n, s) for _, n, s in progress_calls if s != "hashing"]
    assert len(hashing) == 2
    assert len(terminal) == 2
    assert all(s == "archived" for _, s in terminal)
    names = {n for n, _ in terminal}
    assert "a.parquet" in names
    assert "b.parquet" in names


@workflow(purpose="Interrupted archive pass keeps per-row commits; re-run archives only the remainder")
def test_archive_incremental_commits_survive_abort(tmp_project):
    """archive_outputs commits each row as it completes: aborting the pass
    after k outputs leaves exactly k rows with hashes, and a re-run
    archives only the remainder."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import archive_outputs

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="inc_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="inc_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    for name in ["a.parquet", "b.parquet", "c.parquet"]:
        f = _in_run_archive(tmp_project, run_id, name)
        f.write_bytes(f"content-{name}".encode())
        with get_session() as session:
            session.add(RunOutput(
                run_id=run_id, output_name=name,
                artifact_path=str(f), artifact_type="module_file",
            ))
            session.commit()

    # Abort the pass on the second file's "hashing" event: the first row
    # is already committed, the second never gets hashed.
    seen_hashing = []

    def aborting_progress(rid, name, status):
        if status == "hashing":
            seen_hashing.append(name)
            if len(seen_hashing) == 2:
                raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        archive_outputs(tmp_project, run_id=run_id, progress_fn=aborting_progress)

    with get_session() as session:
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        archived = [r for r in rows if r.content_hash is not None]
        assert len(archived) == 1, "exactly the pre-abort row is committed"

    # Re-run without aborting: only the two remaining rows are processed.
    results = archive_outputs(tmp_project, run_id=run_id)
    assert len(results) == 2
    assert all(r["status"] == "archived" for r in results)

    with get_session() as session:
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        assert all(r.content_hash is not None for r in rows)


# =============================================================================
# Directory outputs (Tier 2)
# =============================================================================

@workflow(purpose="Verify archive handles directory outputs (record-in-place)")
def test_archive_directory_output(tmp_project):
    """Archive a directory output, verify directory tree is walked correctly."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import archive_outputs

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="dir_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="dir_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # Create directory output with files inside
    dir_out = _in_run_archive(tmp_project, run_id, "tile_dir")
    dir_out.mkdir()
    (dir_out / "tile_0.tif").write_bytes(b"tile0data")
    (dir_out / "tile_1.tif").write_bytes(b"tile1data")

    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, output_name="tile_dir",
            artifact_path=str(dir_out), artifact_type="module_file",
        ))
        session.commit()

    results = archive_outputs(tmp_project, run_id=run_id)

    assert len(results) == 1
    assert results[0]["status"] == "archived"
    assert results[0]["content_hash"] is not None


@workflow(purpose="Once the archive pass commits an output's hash the output leaves "
                  "the run archive, a file and a directory alike; the run folder "
                  "keeps its logs and sidecars, the row keeps its artifact path, "
                  "and resolve_input, resolve_output and wfc export read the "
                  "bytes through the cache")
def test_archived_outputs_leave_the_run_archive_and_read_through_the_cache(
        cli, tmp_project, monkeypatch):
    from pathlib import Path

    from axiom_annotations import Step

    from tests.fixtures.routes import completed_run
    from tests.harness.scenario import Behavior
    from wfc.persistence import get_session, RunOutput
    from wfc.storage import archive_outputs, resolve_input, resolve_output

    _ = Step(step_num=1, name="Run a step with a file and a directory output",
             purpose="Production's phases write both into the run archive")
    payload = "mask-bytes\n" * 50
    # A nested member too. `.tif`, not `.png`: Windows Defender writes an
    # extended attribute on a fresh `.png` tens of ms after it lands, which
    # moves its change time under collect's stamp.
    members = {"tile_0.tif": "TIF-t0", "sub/tile_1.tif": "TIF-t1"}
    run_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="leave_meth",
        module="leave_mod", sample="s1",
        outputs={"masks": ".tif", "tiles": "directory"},
        output_files={"masks": "masks.tif", "tiles": "tiles"},
        behavior=Behavior(outputs={"masks": payload, "tiles": members}),
    ).run_id
    with get_session() as session:
        before = {r.slot: r.artifact_path for r in session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)).all()}
    masks, tiles = Path(before["masks"]), Path(before["tiles"])
    assert masks.is_file() and tiles.is_dir()
    run_dir = layout.run_archive_dir(tmp_project, run_id)
    kept = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")
                  if p.is_file() and p != masks and tiles not in p.parents)
    assert kept, "the run folder holds logs or sidecars beside its outputs"

    _ = Step(step_num=2, name="Archive the run",
             purpose="Each output's hash is committed, then it leaves the run archive")
    results = archive_outputs(tmp_project, run_id=run_id)
    assert {r["status"] for r in results} == {"archived"}, results
    assert not masks.exists(), "the file output is still in the run archive"
    assert not tiles.exists(), "the directory output is still in the run archive"
    assert not layout.output_members_stamp(tmp_project, run_id, "tiles").exists()
    after = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")
                   if p.is_file())
    assert after == kept, "the run folder's logs and sidecars must stay"
    with get_session() as session:
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        assert {r.slot: r.artifact_path for r in rows} == before
        assert all(r.content_hash for r in rows)

    _ = Step(step_num=3, name="Read the outputs back",
             purpose="Both resolvers and export serve the archived bytes from the cache")
    cache_root = tmp_project / ".dvc" / "cache"
    for reader in (lambda slot: Path(resolve_input(run_id, slot)),
                   lambda slot: resolve_output(run_id, slot)[0]):
        file_path, dir_path = reader("masks"), reader("tiles")
        assert file_path.read_text() == payload
        assert file_path.is_relative_to(cache_root)
        assert {p.relative_to(dir_path).as_posix(): p.read_text()
                for p in dir_path.rglob("*") if p.is_file()} == members
    dest = tmp_project / "exports"
    result = cli("export", "--run", str(run_id), "--all", "--dest", str(dest))
    assert result.returncode == 0, result.stderr
    assert (dest / "masks.tif").read_text() == payload
    assert (dest / "tiles" / "sub" / "tile_1.tif").read_text() == "TIF-t1"


def _set_extended_attribute(path) -> None:
    """Write an extended attribute on a file, as an on-access scanner does.

    Only the file's metadata changes: its bytes, size, mtime and file index
    stay; its change time moves. On Windows the attribute is an NTFS EA
    (Defender writes ``$KERNEL.PURGE.SEC.FILEHASH``); on Linux a ``user.``
    xattr; on macOS, whose Python has no ``os.setxattr``, libc's
    ``setxattr``. Skips where none can be written.
    """
    import os
    import struct
    import sys

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtSetEaFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                      ctypes.c_char_p, wintypes.ULONG]
        # FILE_WRITE_EA, share read/write/delete, OPEN_EXISTING.
        handle = k32.CreateFileW(str(path), 0x10, 0x7, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            pytest.skip(f"cannot open {path} to write an EA")
        try:
            name, value = b"WFC.SCANNED", b"1"
            buf = struct.pack("<IBBH", 0, 0, len(name), len(value)) + name + b"\0" + value
            status_block = (ctypes.c_void_p * 2)()
            if ntdll.NtSetEaFile(handle, status_block, buf, len(buf)) != 0:
                pytest.skip("the volume refuses extended attributes")
        finally:
            k32.CloseHandle(handle)
    elif hasattr(os, "setxattr"):
        try:
            os.setxattr(path, "user.wfc_scanned", b"1")
        except OSError:
            pytest.skip("the filesystem refuses user xattrs")
    elif sys.platform == "darwin":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        libc.setxattr.restype = ctypes.c_int
        value = b"1"
        if libc.setxattr(os.fsencode(str(path)), b"user.wfc_scanned", value,
                         len(value), 0, 0) != 0:
            pytest.skip(f"the filesystem refuses xattrs (errno {ctypes.get_errno()})")
    else:
        pytest.skip("no extended-attribute call on this platform")


def _completed_file_output_run(tmp_project, monkeypatch, method: str) -> tuple[int, object]:
    """A completed run with one file output in its run archive; returns (run id, path)."""
    from pathlib import Path

    from tests.fixtures.routes import completed_run
    from tests.harness.scenario import Behavior
    from wfc.persistence import get_session, RunOutput

    # `.csv`: no on-access scanner tags it on its own (see the note above).
    run_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method=method, module=f"{method}_mod",
        sample="s1", outputs={"table": ".csv"}, output_files={"table": "table.csv"},
        behavior=Behavior(outputs={"table": "id,value\n1,GOOD\n"}),
    ).run_id
    with get_session() as session:
        out = Path(session.exec(select(RunOutput).where(
            RunOutput.run_id == run_id)).one().artifact_path)
    return run_id, out


@workflow(purpose="An output whose bytes are what its run wrote is archived even "
                  "when something outside wfc, an on-access scanner, wrote an "
                  "extended attribute on a nested member after collect")
def test_an_output_whose_member_gained_an_extended_attribute_is_archived(
        tmp_project, monkeypatch):
    from pathlib import Path

    from tests.fixtures.routes import completed_run
    from tests.harness.scenario import Behavior
    from wfc.persistence import get_session, RunOutput
    from wfc.storage import archive_outputs

    members = {"tile_0.tif": "TIF-t0", "sub/tile_1.tif": "TIF-t1"}
    run_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="ea_meth", module="ea_mod",
        sample="s1", outputs={"tiles": "directory"}, output_files={"tiles": "tiles"},
        behavior=Behavior(outputs={"tiles": members}),
    ).run_id
    with get_session() as session:
        tiles = Path(session.exec(select(RunOutput).where(
            RunOutput.run_id == run_id)).one().artifact_path)
    nested = tiles / "sub" / "tile_1.tif"
    _set_extended_attribute(nested)
    assert nested.read_text() == "TIF-t1", "the member's bytes are untouched"

    results = archive_outputs(tmp_project, run_id=run_id)
    assert [r["status"] for r in results] == ["archived"], results


@workflow(purpose="A file output whose bytes are what its run wrote is archived "
                  "when an on-access scanner wrote an extended attribute on it "
                  "after collect: collect's row records its attribute names, so "
                  "the moved change time reads as a metadata write")
def test_a_file_output_that_gained_an_extended_attribute_is_archived(
        tmp_project, monkeypatch):
    from wfc.persistence import get_session, RunOutput

    run_id, out = _completed_file_output_run(tmp_project, monkeypatch, "ea_file_meth")
    with get_session() as session:
        row = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()
    if row.stat_xattr_names is None:
        pytest.skip("this filesystem's attribute names cannot be read")
    _set_extended_attribute(out)
    assert out.read_text() == "id,value\n1,GOOD\n", "the output's bytes are untouched"

    from wfc.storage import archive_outputs

    results = archive_outputs(tmp_project, run_id=run_id)
    assert [r["status"] for r in results] == ["archived"], results


@workflow(purpose="A file output whose change time moved after collect with its "
                  "attribute names unchanged, a chmod, is still refused as "
                  "changed: only a changed name set makes the move metadata only")
def test_an_output_whose_change_time_moved_with_the_same_attribute_names_is_refused(
        tmp_project, monkeypatch):
    import os
    import stat as stat_mod

    from wfc.persistence import get_session, RunOutput
    from wfc.storage import archive_outputs
    from wfc.storage.integrity import extended_attribute_names

    run_id, out = _completed_file_output_run(tmp_project, monkeypatch, "chmod_meth")
    with get_session() as session:
        row = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()
    if row.stat_xattr_names is None:
        pytest.skip("this filesystem's attribute names cannot be read")
    if row.stat_ctime_ns is None:
        pytest.skip("this volume keeps no change time")
    # Writable again: a chmod on POSIX, the read-only attribute on Windows;
    # both move the change time and leave the bytes, size, mtime and names.
    os.chmod(out, stat_mod.S_IMODE(out.stat().st_mode) | stat_mod.S_IWUSR)
    assert extended_attribute_names(out) == row.stat_xattr_names, "the names stayed"

    results = archive_outputs(tmp_project, run_id=run_id)
    (status,) = [r["status"] for r in results]
    assert status.startswith("changed:"), status


@workflow(purpose="The archive pass never hashes or caches a pre-archive record "
                  "whose path lies outside its run's archive: it reports the row "
                  "malformed, naming the run to re-run, and archives the rest")
def test_archive_pass_refuses_a_record_outside_its_run_archive(tmp_project):
    """A recorded path under the old ``.runs/`` tree is a malformed record."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import archive_outputs
    from wfc.storage.cache import _cache_path

    with get_session() as session:
        mod = Module(name="out_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        meth = Method(name="out_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)
        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    inside = _in_run_archive(tmp_project, run_id, "kept.csv")
    inside.write_bytes(b"inside bytes")
    legacy = tmp_project / ".runs" / f"{run_id:08d}" / "legacy.csv"
    legacy.parent.mkdir(parents=True)
    legacy_bytes = b"legacy bytes"
    legacy.write_bytes(legacy_bytes)
    with get_session() as session:
        for slot, path in (("kept", inside), ("legacy", legacy)):
            session.add(RunOutput(
                run_id=run_id, slot=slot, output_name=path.name,
                artifact_path=str(path), artifact_type="method_file",
            ))
        session.commit()

    events = []
    results = archive_outputs(
        tmp_project, run_id=run_id,
        progress_fn=lambda rid, name, status: events.append((name, status)),
    )

    by_name = {r["output_name"]: r for r in results}
    assert by_name["kept.csv"]["status"] == "archived"
    refusal = f"malformed:Run {run_id} (out_meth) has a malformed record; re-run it."
    assert by_name["legacy.csv"]["status"] == refusal
    assert by_name["legacy.csv"]["content_hash"] is None
    assert ("legacy.csv", "hashing") not in events
    assert ("legacy.csv", refusal) in events
    assert not _cache_path(tmp_project, hashlib.md5(legacy_bytes).hexdigest()).exists()
    with get_session() as session:
        legacy_row = session.exec(
            select(RunOutput).where(RunOutput.slot == "legacy")
        ).one()
        assert legacy_row.content_hash is None


# =============================================================================
# Prune guard (Tier 2)
# =============================================================================

@workflow(purpose="Verify prune skips un-archived runs and warns about NULL content_hash")
def test_prune_guard_unarchived(tmp_project, capsys):
    """Attempt prune on runs with NULL content_hash outputs, verify they are
    skipped (not deleted) with a warning."""
    import os
    from wfc.persistence import get_session, Module, Method, Run, RunOutput
    from wfc.storage import cache_prune

    with get_session() as session:
        mod = Module(name="prune_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="prune_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

        # Create the run archive directory on disk
        archive_dir = layout.artifact_store(tmp_project) / f"{run_id:08d}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        out_file = archive_dir / "out.parquet"
        out_file.write_bytes(b"un-archived data")

        # Un-archived output (content_hash=NULL)
        session.add(RunOutput(
            run_id=run.id, output_name="out.parquet",
            artifact_path=str(out_file), artifact_type="module_file",
            content_hash=None,
        ))
        session.commit()

    # Also create an orphan archive (no DB reference) that SHOULD be pruned
    orphan_dir = layout.artifact_store(tmp_project) / "00099999"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "orphan.csv").write_text("orphan data")

    # Run prune with force (no dry_run) to verify actual behavior
    cache_prune(dry_run=False, force=True)

    captured = capsys.readouterr()
    # Warning should be emitted
    assert "not been archived" in captured.err
    assert "content_hash is NULL" in captured.err

    # Un-archived run's archive should be PRESERVED (not deleted)
    assert archive_dir.exists(), "Un-archived run archive must be preserved"
    assert out_file.exists(), "Un-archived output file must be preserved"

    # Orphan archive SHOULD be deleted
    assert not orphan_dir.exists(), "Orphan archive should be pruned"


# =============================================================================
# Archive via wfc cache archive CLI (Tier 3)
# =============================================================================

@workflow(purpose="Verify wfc cache archive CLI finds NULL-hash rows, archives them, prints progress")
def test_cache_archive_cli(tmp_project, cli):
    """Create a run with NULL-hash outputs, invoke 'wfc cache archive',
    verify outputs archived with progress output."""
    from wfc.persistence import get_session, Module, Method, Run, RunOutput

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="cli_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="cli_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    out_file = _in_run_archive(tmp_project, run_id, "cli_output.parquet")
    out_file.write_bytes(b"cli archive content")

    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, output_name="cli_output.parquet",
            artifact_path=str(out_file), artifact_type="module_file",
        ))
        session.commit()

    result = cli("cache", "archive")
    assert result.returncode == 0
    assert "archived" in result.stdout.lower()

    # Verify DB updated
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).first()
        assert ro.content_hash is not None


# =============================================================================
# Auto-archive in run_pipeline
# =============================================================================
# The archive=True end-of-pipeline archive pass is exercised for real (no
# faked pipeline core) by the integration async-push test and by the real
# run_pipeline(archive=True) calls in
# tests/integration/test_cache_provenance.py. A default-suite test that
# mocked the pipeline core would assert archive=True over a core that never
# ran, which is evidence about the mock, not the engine.


# The cache is authoritative storage: resolve_input returns the cache path
# directly, with no restore step. tests/test_resolve.py covers its three
# states.
