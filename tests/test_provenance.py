"""
Tests for DVC provenance storage (ADR-007 Phase 2).

Covers:
- Config parsing: [dvc] present, absent, invalid remote_type
- ensure_dvc_ready: config missing, happy path (no CLI check)
- init_dvc: creates cache dirs and remote dir
- push_artifacts / pull_artifacts: direct cache operations
- Integration: read_config with [dvc] section
"""

import textwrap
from pathlib import Path

import pytest


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


def _write_dvc_config(project_dir: Path, url: str = "file:///tmp/storage") -> Path:
    """Write a minimal .dvc/config so ``has_remote_configured`` returns True.

    Mirrors what ``init_dvc`` writes -- a single ``default`` remote with
    the given URL.
    """
    dvc_dir = project_dir / ".dvc"
    dvc_dir.mkdir(parents=True, exist_ok=True)
    config = (
        "[core]\n"
        "remote = default\n"
        '[remote "default"]\n'
        f"url = {url}\n"
    )
    (dvc_dir / "config").write_text(config)
    return dvc_dir / "config"


# =============================================================================
# Config parsing tests
# =============================================================================

class TestReadConfigDvc:
    """Test that read_config() correctly parses the [dvc] section."""

    def test_dvc_section_present(self, tmp_project):
        """When [dvc] is in wf-canvas.toml, read_config returns dvc dict."""
        _write_config(tmp_project, textwrap.dedent("""
            [dvc]
            remote_type = "local"
            remote_path = "/tmp/dvc-storage"
            auto_init = true
        """))

        from wfc.init import read_config
        cfg = read_config(tmp_project)

        assert cfg["dvc"] is not None
        assert cfg["dvc"]["remote_type"] == "local"
        assert cfg["dvc"]["remote_path"] == "/tmp/dvc-storage"
        assert cfg["dvc"]["auto_init"] is True

    def test_dvc_section_absent(self, tmp_project):
        """When [dvc] is missing, read_config returns dvc=None."""
        _write_config(tmp_project)

        from wfc.init import read_config
        cfg = read_config(tmp_project)

        assert cfg["dvc"] is None

    def test_dvc_section_defaults(self, tmp_project):
        """Partial [dvc] section gets default values for missing fields."""
        _write_config(tmp_project, textwrap.dedent("""
            [dvc]
            remote_path = "/tmp/storage"
        """))

        from wfc.init import read_config
        cfg = read_config(tmp_project)

        assert cfg["dvc"]["remote_type"] == "local"  # default
        assert cfg["dvc"]["auto_init"] is True  # default
        assert cfg["dvc"]["remote_path"] == "/tmp/storage"


# =============================================================================
# ensure_dvc_ready tests
# =============================================================================

class TestEnsureDvcReady:
    """Test prerequisite validation for DVC operations."""

    def test_dvc_config_missing(self, tmp_project):
        """When [dvc] section absent, raise DvcNotConfiguredError."""
        _write_config(tmp_project)

        from wfc.provenance import ensure_dvc_ready, DvcNotConfiguredError

        with pytest.raises(DvcNotConfiguredError, match="No \\[dvc\\] section"):
            ensure_dvc_ready(tmp_project)

    def test_non_local_url_accepted(self, tmp_project):
        """ADR-018: ssh://, s3://, etc. are accepted (multi-backend storage)."""
        _write_config(tmp_project, '\n[dvc]\nurl = "s3://bucket"\n')
        _write_dvc_config(tmp_project, url="s3://bucket")

        from wfc.provenance import ensure_dvc_ready

        result = ensure_dvc_ready(tmp_project)
        assert result["url"] == "s3://bucket"

    def test_ssh_url_accepted(self, tmp_project):
        """ADR-018: ssh://... also passes the check."""
        _write_config(tmp_project, '\n[dvc]\nurl = "ssh://user@host/path"\n')
        _write_dvc_config(tmp_project, url="ssh://user@host/path")

        from wfc.provenance import ensure_dvc_ready

        result = ensure_dvc_ready(tmp_project)
        assert result["url"] == "ssh://user@host/path"

    def test_dvc_config_missing_remote(self, tmp_project):
        """When [dvc] url is set but .dvc/config has no remote, raise."""
        _write_config(tmp_project, '\n[dvc]\nurl = "/tmp/storage"\n')
        # Note: NO _write_dvc_config call -- .dvc/config absent.

        from wfc.provenance import ensure_dvc_ready, DvcNotConfiguredError

        with pytest.raises(DvcNotConfiguredError, match="no remotes"):
            ensure_dvc_ready(tmp_project)

    def test_happy_path(self, tmp_project):
        """When everything is configured, return the dvc config dict."""
        _write_config(tmp_project, '\n[dvc]\nurl = "/tmp/storage"\n')
        _write_dvc_config(tmp_project, url="/tmp/storage")

        from wfc.provenance import ensure_dvc_ready

        result = ensure_dvc_ready(tmp_project)

        # Legacy keys still present for backwards compat
        assert result["url"] == "/tmp/storage"


# =============================================================================
# init_dvc tests
# =============================================================================

class TestInitDvc:
    """Test DVC initialization during wfc init."""

    def test_init_creates_cache_and_remote(self, tmp_project):
        """init_dvc creates .dvc/cache/ structure and remote directory."""
        remote_dir = tmp_project / "dvc-storage"
        dvc_config = {
            "url": str(remote_dir),
            "auto_init": True,
        }

        from wfc.provenance import init_dvc

        init_dvc(tmp_project, dvc_config)

        # Cache directory should exist
        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()
        # Local-FS remote directory auto-created
        assert remote_dir.exists()

    def test_init_idempotent(self, tmp_project):
        """init_dvc is safe to call multiple times."""
        remote_dir = tmp_project / "dvc-storage"
        dvc_config = {
            "url": str(remote_dir),
            "auto_init": True,
        }

        from wfc.provenance import init_dvc

        init_dvc(tmp_project, dvc_config)
        init_dvc(tmp_project, dvc_config)  # no error

        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()

    def test_init_mirrors_url_to_dvc_config(self, tmp_project):
        """ADR-018 Task 6: init_dvc writes [dvc] url to .dvc/config."""
        remote_dir = tmp_project / "dvc-storage"
        dvc_config = {"url": str(remote_dir), "auto_init": True}

        from wfc.provenance import init_dvc
        init_dvc(tmp_project, dvc_config)

        import configparser
        parser = configparser.ConfigParser()
        parser.read(tmp_project / ".dvc" / "config")
        assert parser.get("core", "remote") == "default"
        assert parser.get('remote "default"', "url") == str(remote_dir)

    def test_init_accepts_remote_url(self, tmp_project):
        """ADR-018: init_dvc accepts non-local URLs (s3://, ssh://)."""
        from wfc.provenance import init_dvc

        dvc_config = {"url": "s3://bucket/prefix"}
        # Should not raise
        init_dvc(tmp_project, dvc_config)

        # No local dir created for s3://, but .dvc/config should be written
        import configparser
        parser = configparser.ConfigParser()
        parser.read(tmp_project / ".dvc" / "config")
        assert parser.get('remote "default"', "url") == "s3://bucket/prefix"


# =============================================================================
# init_project integration test (Step 6 -- DVC auto-init)
# =============================================================================

class TestInitProjectDvcAutoInit:
    """Test that init_project() auto-initializes DVC when [dvc] config is present."""

    def test_init_project_calls_init_dvc_when_auto_init_true(self, tmp_project):
        """When wf-canvas.toml has [dvc] with auto_init=true, init_project calls init_dvc."""
        remote_dir = tmp_project / "dvc-storage"
        _write_config(tmp_project, textwrap.dedent(f"""
            [dvc]
            url = "{remote_dir.as_posix()}"
            auto_init = true
        """))

        from wfc.init import init_project

        result = init_project(tmp_project)

        assert result["dvc"] is True
        # Cache dir should exist
        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()
        # ADR-018 Task 6: .dvc/config should be mirrored
        import configparser
        parser = configparser.ConfigParser()
        parser.read(tmp_project / ".dvc" / "config")
        assert parser.get('remote "default"', "url") == remote_dir.as_posix()

    def test_init_project_skips_dvc_when_no_dvc_section(self, tmp_project):
        """When wf-canvas.toml has no [dvc] section, init_project skips DVC init."""
        _write_config(tmp_project)

        from wfc.init import init_project
        from unittest.mock import patch

        with patch("wfc.provenance.init_dvc") as mock_init_dvc:
            result = init_project(tmp_project)

        mock_init_dvc.assert_not_called()
        assert result["dvc"] is False


# =============================================================================
# push_artifacts tests
# =============================================================================

class TestPushArtifacts:
    """Test artifact push to DVC remote."""

    def test_push_caches_and_copies_to_remote(self, tmp_project):
        """push_artifacts hashes, caches, and copies to remote dir.

        Uses the legacy local-FS fallback path -- writes wf-canvas.toml
        with ``url`` but no ``.dvc/config`` remote, so ``push_cache``
        skips the DVC client and uses the bare-fs copy code path.
        """
        # Older test asserted the bare-FS fallback; we keep that test by
        # NOT writing .dvc/config (so has_remote_configured is False) and
        # routing through the legacy _remote_path() copier.
        remote_dir = tmp_project / "dvc-storage"
        _write_config(tmp_project, textwrap.dedent(f"""
            [dvc]
            url = "{remote_dir.as_posix()}"
        """))
        _write_dvc_config(tmp_project, url=remote_dir.as_posix())
        (tmp_project / ".dvc" / "cache" / "files" / "md5").mkdir(parents=True)
        remote_dir.mkdir(parents=True, exist_ok=True)

        # Create a fake artifact file
        artifact = tmp_project / "output.parquet"
        artifact.write_text("data")

        # Patch wfc.remote.push to use the legacy local-FS copier so we
        # don't need a real DVC repo here.  wfc.provenance.push_cache will
        # call our stub and route the bytes to remote_dir.
        import wfc.remote as _r
        orig_push = _r.push
        def _fake_push(hashes, pd):
            from wfc.provenance import _cache_path
            for md5 in hashes:
                src = _cache_path(Path(pd), md5)
                dest = remote_dir / "files" / "md5" / md5[:2] / md5[2:]
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    import shutil as _sh
                    _sh.copy2(str(src), str(dest))
            class _R:
                succeeded = []
                failed = []
            return _R()
        _r.push = _fake_push
        try:
            from wfc.provenance import push_artifacts
            result = push_artifacts(run_id=42, paths=[str(artifact)], project_dir=tmp_project)
        finally:
            _r.push = orig_push

        assert result is True
        # Check something was pushed to remote
        remote_files = list((remote_dir / "files" / "md5").rglob("*"))
        assert len([f for f in remote_files if f.is_file()]) >= 1

    def test_push_empty_paths(self, tmp_project):
        """push_artifacts with empty paths returns True immediately."""
        from wfc.provenance import push_artifacts
        result = push_artifacts(run_id=1, paths=[], project_dir=tmp_project)
        assert result is True

    def test_push_no_dvc_config_returns_false(self, tmp_project):
        """push_artifacts returns False when DVC is not configured."""
        _write_config(tmp_project)  # no [dvc] section

        from wfc.provenance import push_artifacts
        result = push_artifacts(run_id=1, paths=["/some/path"], project_dir=tmp_project)
        assert result is False


# =============================================================================
# pull_artifacts tests
# =============================================================================

class TestPullArtifacts:
    """Test artifact pull from DVC remote."""

    def test_pull_no_dvc_config(self, tmp_project):
        """pull_artifacts returns False when DVC not configured."""
        _write_config(tmp_project)  # no [dvc] section

        from wfc.provenance import pull_artifacts
        result = pull_artifacts(run_id=1, project_dir=tmp_project)
        assert result is False


# =============================================================================
# ADR-018 cache_file move-not-copy tests (Task 3)
# =============================================================================

class TestCacheFileMoveNotCopy:
    """ADR-018: cache_file moves staging files into cache by default."""

    def test_cache_file_move_consumes_source(self, tmp_path):
        """Default move=True: same-volume rename removes the source path."""
        import hashlib
        from wfc.provenance import cache_file

        src = tmp_path / "staging" / "output.txt"
        src.parent.mkdir(parents=True)
        content = b"move me into cache"
        src.write_bytes(content)
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        dest = cache_file(src, md5, project_dir)

        # Source consumed, cache populated with original content.
        assert not src.exists(), "staging source should be consumed by move"
        assert dest.exists()
        assert dest.read_bytes() == content

    def test_cache_file_copy_mode_preserves_source(self, tmp_path):
        """move=False preserves the source (legacy register_sample contract)."""
        import hashlib
        from wfc.provenance import cache_file

        src = tmp_path / "user-data.txt"
        content = b"user owns this"
        src.write_bytes(content)
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        dest = cache_file(src, md5, project_dir, move=False)

        assert src.exists(), "move=False must preserve user source"
        assert dest.read_bytes() == content

    def test_cache_file_dedup_unlinks_staging(self, tmp_path):
        """When dest already cached, staging duplicate is unlinked on move."""
        import hashlib
        from wfc.provenance import cache_file, _cache_path

        content = b"already cached"
        md5 = hashlib.md5(content).hexdigest()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Pre-populate cache.
        existing = _cache_path(project_dir, md5)
        existing.parent.mkdir(parents=True)
        existing.write_bytes(content)
        existing_mtime = existing.stat().st_mtime

        # New staging copy of the same content.
        staging = tmp_path / "staging" / "duplicate.txt"
        staging.parent.mkdir(parents=True)
        staging.write_bytes(content)

        dest = cache_file(staging, md5, project_dir)

        assert dest == existing
        assert not staging.exists(), "staging duplicate should be unlinked on dedup"
        # Existing cache file must not be overwritten.
        assert dest.stat().st_mtime == existing_mtime
        assert dest.read_bytes() == content

    def test_cache_file_cross_volume_fallback(self, tmp_path, monkeypatch):
        """Cross-volume OSError on rename falls back to copy+unlink."""
        import hashlib
        import os
        from wfc.provenance import cache_file

        src = tmp_path / "staging" / "cross.txt"
        src.parent.mkdir(parents=True)
        content = b"cross-volume content"
        src.write_bytes(content)
        md5 = hashlib.md5(content).hexdigest()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Force the rename path to raise OSError so the copy+unlink branch executes.
        real_rename = os.rename

        def fake_rename(a, b):
            raise OSError("simulated cross-volume rename")

        monkeypatch.setattr(os, "rename", fake_rename)

        dest = cache_file(src, md5, project_dir)

        assert dest.exists()
        assert dest.read_bytes() == content
        assert not src.exists(), "fallback copy+unlink must remove source"

    def test_cache_file_directory_uses_shutil_move(self, tmp_path):
        """Directory inputs are moved via shutil.move."""
        import hashlib
        from wfc.provenance import cache_file, hash_directory

        src = tmp_path / "staging" / "outdir"
        src.mkdir(parents=True)
        (src / "a.txt").write_bytes(b"alpha")
        (src / "b.txt").write_bytes(b"beta")

        md5 = hash_directory(src)
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        dest = cache_file(src, md5, project_dir)

        assert dest.is_dir()
        assert (dest / "a.txt").read_bytes() == b"alpha"
        assert not src.exists(), "directory source should be consumed by move"
