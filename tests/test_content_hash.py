"""
Tests for ADR-007 Phase 2: content-addressed output storage.

Covers:
- US-1: Content hashing produces correct md5, files stored in .dvc/cache/
- US-2: restore_output retrieves from cache by content_hash; errors on null hash
- US-3: build_input_fingerprint requires content_hash; errors on null hash
- US-4: No .runs/{run_id}/ archive directories created for new runs
- US-5: No subprocess calls to dvc in provenance.py
"""

import hashlib
import os
import textwrap
from pathlib import Path

import pytest
from sqlmodel import select


# =============================================================================
# Helpers
# =============================================================================

_BASE_CONFIG = textwrap.dedent("""\
    [database]
    url = "sqlite:///{db_path}"

    [project]
    name = "test"

    [pixi]
    root = ".pixi"
""")


def _write_config(project_dir: Path, extra: str = "") -> Path:
    """Write a minimal wf-canvas.toml with optional extra sections."""
    config_path = project_dir / ".wfc" / "wf-canvas.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    db_path = (project_dir / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(_BASE_CONFIG.format(db_path=db_path) + extra)
    return config_path


# =============================================================================
# US-1: Content hashing correctness
# =============================================================================

class TestContentHashing:
    """Test that hash_file and hash_directory produce correct md5 digests."""

    def test_hash_file_correct_md5(self, tmp_path):
        """hash_file returns the correct md5 hex digest for a file."""
        f = tmp_path / "data.txt"
        content = b"hello world content"
        f.write_bytes(content)

        from wfc.provenance import hash_file
        result = hash_file(f)

        expected = hashlib.md5(content).hexdigest()
        assert result == expected
        assert len(result) == 32

    def test_hash_file_same_content_same_hash(self, tmp_path):
        """Same content in different files produces the same hash."""
        content = b"identical content"
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(content)
        f2.write_bytes(content)

        from wfc.provenance import hash_file
        assert hash_file(f1) == hash_file(f2)

    def test_hash_directory_stable(self, tmp_path):
        """hash_directory produces a stable digest for a directory tree."""
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "a.txt").write_bytes(b"aaa")
        (d / "b.txt").write_bytes(b"bbb")
        sub = d / "sub"
        sub.mkdir()
        (sub / "c.txt").write_bytes(b"ccc")

        from wfc.provenance import hash_directory
        h1 = hash_directory(d)
        h2 = hash_directory(d)
        assert h1 == h2
        assert len(h1) == 32

    def test_hash_path_dispatches(self, tmp_path):
        """hash_path dispatches to hash_file or hash_directory."""
        f = tmp_path / "file.txt"
        f.write_bytes(b"data")
        d = tmp_path / "dir"
        d.mkdir()
        (d / "x.txt").write_bytes(b"x")

        from wfc.provenance import hash_path, hash_file, hash_directory
        assert hash_path(f) == hash_file(f)
        assert hash_path(d) == hash_directory(d)


# =============================================================================
# US-1 + US-4: Cache population and no archive
# =============================================================================

class TestCacheOperations:
    """Test that cache_file stores in .dvc/cache/ and restore_from_cache retrieves."""

    def test_cache_file_creates_two_level_structure(self, tmp_path):
        """cache_file stores file at .dvc/cache/files/md5/{hash[:2]}/{hash[2:]}."""
        f = tmp_path / "output.parquet"
        content = b"parquet data here"
        f.write_bytes(content)
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from wfc.provenance import cache_file
        result = cache_file(f, md5, project_dir)

        expected_path = project_dir / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
        assert result == expected_path
        assert expected_path.exists()
        assert expected_path.read_bytes() == content

    def test_cache_file_idempotent(self, tmp_path):
        """Caching the same content twice does not error."""
        f = tmp_path / "output.txt"
        content = b"test content"
        f.write_bytes(content)
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from wfc.provenance import cache_file
        p1 = cache_file(f, md5, project_dir)
        p2 = cache_file(f, md5, project_dir)
        assert p1 == p2

    def test_restore_from_cache(self, tmp_path):
        """restore_from_cache copies cached file to destination."""
        content = b"restore me"
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Manually place in cache
        cache_path = project_dir / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        dest = tmp_path / "workspace" / "output.parquet"

        from wfc.provenance import restore_from_cache
        assert restore_from_cache(md5, dest, project_dir) is True
        assert dest.read_bytes() == content

    def test_restore_from_cache_missing(self, tmp_path):
        """restore_from_cache returns False when cache entry is missing."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True)

        from wfc.provenance import restore_from_cache
        assert restore_from_cache("deadbeef" * 4, tmp_path / "out.txt", project_dir) is False

    def test_restore_from_cache_skips_when_hash_matches(self, tmp_path):
        """restore_from_cache returns True without copying when dest has correct content."""
        from unittest.mock import patch
        content = b"already correct"
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Place in cache
        cache_path = project_dir / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(content)

        # Create dest with same content
        dest = tmp_path / "workspace" / "output.parquet"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(content)

        from wfc.provenance import restore_from_cache
        # Patch shutil.copy2 to detect if a copy actually happens
        with patch("wfc.provenance.shutil.copy2") as mock_copy:
            assert restore_from_cache(md5, dest, project_dir) is True
            # copy2 should NOT have been called — file was already correct
            mock_copy.assert_not_called()
        assert dest.read_bytes() == content

    def test_restore_from_cache_replaces_when_hash_mismatches(self, tmp_path):
        """restore_from_cache replaces dest when content hash does not match."""
        correct_content = b"correct content"
        md5 = hashlib.md5(correct_content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Place correct content in cache
        cache_path = project_dir / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(correct_content)

        # Create dest with WRONG content
        dest = tmp_path / "workspace" / "output.parquet"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"stale or corrupted data")

        from wfc.provenance import restore_from_cache
        assert restore_from_cache(md5, dest, project_dir) is True
        assert dest.read_bytes() == correct_content

    def test_restore_from_cache_missing_cache_with_existing_dest(self, tmp_path):
        """restore_from_cache returns False when cache is missing, even if dest exists."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True)

        dest = tmp_path / "workspace" / "output.parquet"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"some existing content")

        from wfc.provenance import restore_from_cache
        assert restore_from_cache("deadbeef" * 4, dest, project_dir) is False
        # Existing file should not be touched
        assert dest.read_bytes() == b"some existing content"


# =============================================================================
# US-2: complete_run stores content_hash, restore_output uses it
# =============================================================================

class TestCompleteRunContentHash:
    """Integration: complete_run hashes outputs and stores content_hash in DB."""

    def test_complete_run_stores_content_hash(self, tmp_project):
        """complete_run leaves content_hash NULL (deferred archiving).

        Content hashing is deferred to the post-pipeline archive pass.
        complete_run still creates RunOutput rows but without content_hash.
        """
        from wfc.database import get_session
        from wfc.models import Module, Method, Run, RunOutput
        from wfc.cli import complete_run

        # Seed DB with a run
        with get_session() as session:
            mod = Module(name="test_mod")
            session.add(mod)
            session.commit()
            session.refresh(mod)

            meth = Method(name="test_meth", module_id=mod.id)
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
        content = b"output content for hashing"
        out_file.write_bytes(content)

        # Create .dvc/cache structure
        (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True, exist_ok=True)

        # Call complete_run
        complete_run(
            run_id=run_id,
            status="completed",
            output_files=[str(out_file)],
        )

        # Verify content_hash is NULL (deferred archiving)
        with get_session() as session:
            ro = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).first()
            assert ro is not None
            assert ro.content_hash is None, (
                "content_hash should be NULL — archiving is deferred"
            )


# ADR-018: restore_output deleted (cache is authoritative; resolve_input
# returns the cache path directly).  See tests/test_resolve.py for the
# three-state coverage of resolve_input / resolve_sample.


# =============================================================================
# US-3: build_input_fingerprint uses content_hash
# =============================================================================

class TestFingerprintContentHash:
    """Test that build_input_fingerprint prefers content_hash over mtime."""

    def test_fingerprint_uses_content_hash(self, tmp_project):
        """When content_hash is set, fingerprint uses it instead of path:size:mtime."""
        from wfc.database import get_session
        from wfc.models import Module, Method, Run, RunOutput
        from wfc.version import build_input_fingerprint

        with get_session() as session:
            mod = Module(name="test_mod")
            session.add(mod)
            session.commit()
            session.refresh(mod)

            meth = Method(name="test_meth", module_id=mod.id)
            session.add(meth)
            session.commit()
            session.refresh(meth)

            run = Run(method_id=meth.id, sample="s1", status="completed")
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

            # Create RunOutput with content_hash
            ro = RunOutput(
                run_id=run_id, output_name="output.parquet",
                artifact_path="/some/path/output.parquet",
                artifact_type="module_file",
                file_size=100, file_mtime=1234.0,
                content_hash="abcdef1234567890abcdef1234567890",
            )
            session.add(ro)
            session.commit()

        fp1 = build_input_fingerprint(upstream_run_ids=[run_id])

        # Change mtime but keep same content_hash — fingerprint should NOT change
        with get_session() as session:
            ro2 = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).first()
            ro2.file_mtime = 9999.0  # different mtime
            session.commit()

        fp2 = build_input_fingerprint(upstream_run_ids=[run_id])
        assert fp1 == fp2, "Fingerprint should be stable when content_hash is same"

    def test_fingerprint_ok_when_no_content_hash(self, tmp_project):
        """When content_hash is null, build_input_fingerprint still works
        because it uses upstream Run.cache_key (deferred archiving)."""
        from wfc.database import get_session
        from wfc.models import Module, Method, Run, RunOutput
        from wfc.version import build_input_fingerprint

        with get_session() as session:
            mod = Module(name="test_mod")
            session.add(mod)
            session.commit()
            session.refresh(mod)

            meth = Method(name="test_meth", module_id=mod.id)
            session.add(meth)
            session.commit()
            session.refresh(meth)

            run = Run(method_id=meth.id, sample="s1", status="completed",
                      cache_key="b" * 64)
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

            ro = RunOutput(
                run_id=run_id, output_name="output.parquet",
                artifact_path="/some/path/output.parquet",
                artifact_type="module_file",
                file_size=100, file_mtime=1234.0,
                content_hash=None,  # no content hash -- deferred
            )
            session.add(ro)
            session.commit()

        # Should NOT raise -- uses Run.cache_key instead of content_hash
        fp = build_input_fingerprint(upstream_run_ids=[run_id])
        assert len(fp) == 64

    def test_sample_fingerprint_uses_content_hash(self, tmp_project):
        """Two Samples with identical path/size/mtime but different content_hash
        produce different fingerprints — the DVC hash is the primary key."""
        from wfc.database import get_session
        from wfc.models import Sample
        from wfc.version import build_input_fingerprint

        with get_session() as session:
            s1 = Sample(
                name="s1", source_path="/x.csv",
                registered_path="data/samples/s1/x.csv",
                file_type="csv", file_size=100, file_mtime=1.0,
                content_hash="a" * 32,
            )
            s2 = Sample(
                name="s2", source_path="/x.csv",
                registered_path="data/samples/s1/x.csv",
                file_type="csv", file_size=100, file_mtime=1.0,
                content_hash="b" * 32,
            )
            session.add_all([s1, s2])
            session.commit()
            id1, id2 = s1.id, s2.id

        fp1 = build_input_fingerprint([], sample_ids=[id1])
        fp2 = build_input_fingerprint([], sample_ids=[id2])
        assert fp1 != fp2, (
            "fingerprint must differ when content_hash differs, "
            "even with identical path/size/mtime"
        )

    def test_sample_fingerprint_legacy_fallback(self, tmp_project):
        """Legacy samples (content_hash=NULL) fall back to path:size:mtime."""
        from wfc.database import get_session
        from wfc.models import Sample
        from wfc.version import build_input_fingerprint

        with get_session() as session:
            s = Sample(
                name="legacy", source_path="/legacy.csv",
                registered_path="data/samples/legacy/legacy.csv",
                file_type="csv", file_size=500, file_mtime=42.0,
                content_hash=None,
            )
            session.add(s)
            session.commit()
            sid = s.id

        fp_before = build_input_fingerprint([], sample_ids=[sid])

        # Change mtime -> fingerprint should change (legacy path)
        with get_session() as session:
            row = session.get(Sample, sid)
            row.file_mtime = 99.0
            session.commit()

        fp_after = build_input_fingerprint([], sample_ids=[sid])
        assert fp_before != fp_after, (
            "legacy samples without content_hash must still be mtime-sensitive"
        )


# =============================================================================
# US-5: No subprocess calls to dvc
# =============================================================================

class TestNoSubprocessCalls:
    """Verify provenance.py has no subprocess calls to DVC."""

    def test_no_subprocess_in_provenance(self):
        """provenance.py should not import or use subprocess."""
        import wfc.provenance as prov
        source = Path(prov.__file__).read_text()
        # Check that subprocess is not imported (ignore mentions in comments/docstrings)
        assert "import subprocess" not in source, "provenance.py should not import subprocess"
        assert "subprocess.run" not in source, "provenance.py should not call subprocess.run"
        assert "def _run_dvc" not in source, "provenance.py should not have _run_dvc"

    def test_no_dvc_pointer_files_after_cache(self, tmp_path):
        """Caching a file should not create any .dvc pointer files."""
        f = tmp_path / "data.txt"
        f.write_bytes(b"test data")

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from wfc.provenance import hash_file, cache_file
        md5 = hash_file(f)
        cache_file(f, md5, project_dir)

        # Check no .dvc pointer files (files, not directories)
        dvc_files = [p for p in project_dir.rglob("*.dvc") if p.is_file()]
        assert dvc_files == [], f"Found .dvc pointer files: {dvc_files}"
