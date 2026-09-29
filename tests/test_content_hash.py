"""Tests for content-addressed output storage.

Covers:
- Content hashing produces correct md5; files are stored in .dvc/cache/.
- cache_file stores + restore_from_cache retrieves (hash-verified).
- complete_run defers archiving, so it leaves content_hash NULL.
- Caching writes no .dvc pointer files.
"""

import hashlib
import os
import textwrap
from pathlib import Path

import pytest
from sqlmodel import select

from tests.fixtures.fakes import recording_file_copy


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
# Content hashing correctness
# =============================================================================

class TestContentHashing:
    """Test that hash_file and hash_directory produce correct md5 digests."""

    def test_hash_file_correct_md5(self, tmp_path):
        """hash_file returns the correct md5 hex digest for a file."""
        f = tmp_path / "data.txt"
        content = b"hello world content"
        f.write_bytes(content)

        from wfc.identity import hash_file
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

        from wfc.identity import hash_file
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

        from wfc.identity import hash_directory
        h1 = hash_directory(d)
        h2 = hash_directory(d)
        assert h1 == h2
        # DVC's directory identity: a 32-hex md5 of the manifest plus ".dir".
        assert len(h1) == 36 and h1.endswith(".dir")

    def test_hash_path_dispatches(self, tmp_path):
        """hash_path dispatches to hash_file or hash_directory."""
        f = tmp_path / "file.txt"
        f.write_bytes(b"data")
        d = tmp_path / "dir"
        d.mkdir()
        (d / "x.txt").write_bytes(b"x")

        from wfc.identity import hash_path, hash_file, hash_directory
        assert hash_path(f) == hash_file(f)
        assert hash_path(d) == hash_directory(d)


# =============================================================================
# Cache population and no archive
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

        from wfc.storage import cache_file
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

        from wfc.storage import cache_file
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

        from wfc.storage import restore_from_cache
        assert restore_from_cache(md5, dest, project_dir) is True
        assert dest.read_bytes() == content

    def test_restore_from_cache_missing(self, tmp_path):
        """restore_from_cache returns False when cache entry is missing."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True)

        from wfc.storage import restore_from_cache
        assert restore_from_cache("deadbeef" * 4, tmp_path / "out.txt", project_dir) is False

    def test_restore_from_cache_skips_when_hash_matches(self, tmp_path):
        """restore_from_cache returns True without copying when dest has correct content."""
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

        from wfc.storage import restore_from_cache
        # Patch shutil.copy2 to detect if a copy actually happens
        with recording_file_copy() as copies:
            assert restore_from_cache(md5, dest, project_dir) is True
            # copy2 should NOT have been called — file was already correct
            assert copies == []
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

        from wfc.storage import restore_from_cache
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

        from wfc.storage import restore_from_cache
        assert restore_from_cache("deadbeef" * 4, dest, project_dir) is False
        # Existing file should not be touched
        assert dest.read_bytes() == b"some existing content"


# =============================================================================
# complete_run defers archiving: content_hash stays NULL
# =============================================================================

class TestCompleteRunContentHash:
    """Integration: complete_run defers archiving and leaves content_hash NULL."""

    def test_complete_run_leaves_content_hash_null(self, tmp_project):
        """complete_run leaves content_hash NULL (deferred archiving).

        Content hashing is deferred to the post-pipeline archive pass.
        complete_run still creates RunOutput rows but without content_hash.
        """
        from wfc.persistence import get_session, Module, Method, Run, RunOutput
        from wfc.execution.record import complete_run

        # Seed DB with a run
        with get_session() as session:
            mod = Module(name="test_mod")
            session.add(mod)
            session.commit()
            session.refresh(mod)

            meth = Method(name="test_meth", module_id=mod.id, env="container:demo")
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

        # The row the collect phase writes for the file.
        from wfc.persistence import RunOutput as _RunOutput
        with get_session() as session:
            session.add(_RunOutput(run_id=run_id, slot="output",
                                   output_name=out_file.name,
                                   artifact_path=str(out_file),
                                   artifact_type="method_file"))
            session.commit()

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


# =============================================================================
# Caching writes no .dvc pointer files
# =============================================================================

class TestCachingWritesNoPointerFiles:
    """Caching content writes no .dvc pointer files."""

    def test_no_dvc_pointer_files_after_cache(self, tmp_path):
        """Caching a file should not create any .dvc pointer files."""
        f = tmp_path / "data.txt"
        f.write_bytes(b"test data")

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        from wfc.identity import hash_file
        from wfc.storage import cache_file
        md5 = hash_file(f)
        cache_file(f, md5, project_dir)

        # Check no .dvc pointer files (files, not directories)
        dvc_files = [p for p in project_dir.rglob("*.dvc") if p.is_file()]
        assert dvc_files == [], f"Found .dvc pointer files: {dvc_files}"
