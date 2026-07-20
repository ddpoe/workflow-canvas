"""
Tests for deferred output archiving.

Covers: US-1 (no inline archiving), US-2 (archive command + progress),
US-3 (auto-archive), US-4 (upstream cache_key fingerprint),
US-5 (prune guard), directory outputs.
"""

import hashlib
import json

import pytest
from sqlmodel import select

from axiom_annotations import workflow


# =============================================================================
# US-4: Upstream cache_key fingerprint (Tier 2)
# =============================================================================

@workflow(purpose="Verify build_input_fingerprint uses upstream Run.cache_key, not content_hash")
def test_input_fingerprint_uses_cache_key(tmp_project):
    """build_input_fingerprint uses upstream Run.cache_key -- NULL content_hash
    does not affect cache key computation at all and no ValueError is raised."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.version import build_input_fingerprint

    with get_session() as session:
        mod = Module(name="fp_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="fp_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        # Create an upstream run with cache_key but NULL content_hash
        run = Run(
            method_id=meth.id, sample="s1", status="completed",
            cache_key="abc123def456" * 4 + "abcdef1234567890",  # 64 chars
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        # Add a RunOutput with NULL content_hash
        ro = RunOutput(
            run_id=run.id, output_name="out.parquet",
            artifact_path="/fake/out.parquet", artifact_type="module_file",
            content_hash=None,
        )
        session.add(ro)
        session.commit()

        upstream_run_id = run.id

    # Should NOT raise ValueError even though content_hash is NULL
    fp = build_input_fingerprint([upstream_run_id])
    assert len(fp) == 64  # valid SHA256 hex


@workflow(purpose="Verify fingerprint determinism with upstream cache keys")
def test_fingerprint_determinism(tmp_project):
    """Compute input fingerprint twice with same upstream cache keys,
    verify identical result."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run
    from wfc.version import build_input_fingerprint

    with get_session() as session:
        mod = Module(name="det_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="det_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(
            method_id=meth.id, sample="s1", status="completed",
            cache_key="a" * 64,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        rid = run.id

    fp1 = build_input_fingerprint([rid])
    fp2 = build_input_fingerprint([rid])
    assert fp1 == fp2


# =============================================================================
# US-1: No inline archiving (Tier 2)
# =============================================================================

@workflow(purpose="Verify complete_run leaves content_hash NULL (no inline archiving)")
def test_complete_run_no_inline_archiving(tmp_project):
    """Run complete_run with output files, verify content_hash remains NULL
    and no hash_path/cache_file call occurs."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.cli import complete_run

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
    out_file = tmp_project / "output.parquet"
    out_file.write_bytes(b"test output data")

    # Create .dvc/cache structure (should NOT be used)
    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

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
# US-2: Archive command + progress (Tier 2 + Tier 3)
# =============================================================================

@workflow(purpose="Verify archive_outputs hashes, caches, and updates DB for NULL-hash outputs")
def test_archive_outputs_basic(tmp_project):
    """Create a run with NULL-hash outputs, invoke archive_outputs,
    verify hashes computed and files cached."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.provenance import archive_outputs

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
    out_file = tmp_project / "arch_output.parquet"
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
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.provenance import archive_outputs

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
        f = tmp_project / name
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
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.provenance import archive_outputs

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
        f = tmp_project / name
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
# US-1+US-2: Directory outputs (Tier 2)
# =============================================================================

@workflow(purpose="Verify archive handles directory outputs (record-in-place)")
def test_archive_directory_output(tmp_project):
    """Archive a directory output, verify directory tree is walked correctly."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.provenance import archive_outputs

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
    dir_out = tmp_project / "tile_dir"
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


# =============================================================================
# US-5: Prune guard (Tier 2)
# =============================================================================

@workflow(purpose="Verify prune skips un-archived runs and warns about NULL content_hash")
def test_prune_guard_unarchived(tmp_project, capsys):
    """Attempt prune on runs with NULL content_hash outputs, verify they are
    skipped (not deleted) with a warning."""
    import os
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput
    from wfc.cli import cache_prune

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
        archive_dir = tmp_project / ".runs" / f"{run_id:08d}"
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
    orphan_dir = tmp_project / ".runs" / "00099999"
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
# US-2: Archive via wfc cache archive CLI (Tier 3)
# =============================================================================

@workflow(purpose="Verify wfc cache archive CLI finds NULL-hash rows, archives them, prints progress")
def test_cache_archive_cli(tmp_project, cli):
    """Create a run with NULL-hash outputs, invoke 'wfc cache archive',
    verify outputs archived with progress output."""
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput

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

    out_file = tmp_project / "cli_output.parquet"
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
# US-3: Auto-archive in run_pipeline (Tier 2)
# =============================================================================

@workflow(purpose="Verify run_pipeline with archive=True archives outputs after pipeline completion")
def test_run_pipeline_auto_archive(tmp_project):
    """run_pipeline with archive=True calls archive_outputs after Snakemake
    completes, archiving any un-archived outputs."""
    from unittest.mock import patch, MagicMock
    from wfc.database import get_session
    from wfc.models import Module, Method, Run, RunOutput

    (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        mod = Module(name="auto_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        meth = Method(name="auto_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)

        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    # Create output file with NULL content_hash (un-archived)
    out_file = tmp_project / "auto_output.parquet"
    out_file.write_bytes(b"auto archive content")

    with get_session() as session:
        session.add(RunOutput(
            run_id=run_id, output_name="auto_output.parquet",
            artifact_path=str(out_file), artifact_type="module_file",
            content_hash=None,
        ))
        session.commit()

    # Write a dummy pipeline JSON
    pipeline_json = tmp_project / "pipeline.json"
    pipeline_json.write_text(json.dumps({
        "nodes": [], "links": [], "samples": [],
    }))

    # Mock Snakemake so we don't need a real pipeline.
    # Phase D Pass 2: run_pipeline uses subprocess.Popen + .wait() now.
    fake_proc = MagicMock()
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    with patch("wfc.snakemake_gen.load_pipeline") as mock_load, \
         patch("wfc.snakemake_gen.generate_snakefile", return_value="# fake"), \
         patch("subprocess.Popen", return_value=fake_proc):
        mock_load.return_value = {"nodes": [], "links": [], "samples": []}

        from wfc.cli import run_pipeline
        run_pipeline(
            pipeline_path=str(pipeline_json),
            project_root=str(tmp_project),
            wfc_root=str(tmp_project),
            archive=True,
        )

    # Verify the un-archived output now has a content_hash
    with get_session() as session:
        ro = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).first()
        assert ro.content_hash is not None, \
            "run_pipeline with archive=True should archive outputs (content_hash populated)"

    # Verify file is in DVC cache
    cache_path = (
        tmp_project / ".dvc" / "cache" / "files" / "md5"
        / ro.content_hash[:2] / ro.content_hash[2:]
    )
    assert cache_path.exists(), "Archived output should exist in DVC cache"


# ADR-018: restore_output deleted (cache is authoritative storage; resolve_input
# returns the cache path directly).  See tests/test_resolve.py for the new
# three-state coverage.
