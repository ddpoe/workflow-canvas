"""
Tests for DVC provenance storage.

Covers:
- Config parsing: [dvc] present, absent, defaults
- ensure_dvc_ready: missing config or remote, non-local and inside-project
  URLs, a [dvc] table without a url, remote-URL drift
- init_dvc: cache and remote dirs, idempotence, URL mirroring into
  .dvc/config; init_project's DVC auto-init
- cache_file: move vs copy, dedup, cross-volume fallback, directories,
  read-only entries
- remote path derivation
"""

import textwrap
from pathlib import Path

import pytest

from tests.fixtures.fakes import fake_os_chmod, fake_os_rename, stub_dvc_setup


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

        from wfc.persistence import read_config
        cfg = read_config(tmp_project)

        assert cfg["dvc"] is not None
        assert cfg["dvc"]["remote_type"] == "local"
        assert cfg["dvc"]["remote_path"] == "/tmp/dvc-storage"
        assert cfg["dvc"]["auto_init"] is True

    def test_returns_only_the_environment_roots_and_dvc(self, tmp_project):
        """One call answers the environment roots and the [dvc] entry only.

        The file carries a ``[database]`` table and a project name; neither
        a database URL nor the name comes back.
        """
        _write_config(tmp_project, textwrap.dedent("""
            [dvc]
            url = "file:///tmp/storage"
        """))

        from wfc.persistence import read_config
        cfg = read_config(tmp_project)

        assert set(cfg) == {"pixi_root", "conda_root", "dvc"}
    def test_dvc_section_absent(self, tmp_project):
        """When [dvc] is missing, read_config returns dvc=None."""
        _write_config(tmp_project)

        from wfc.persistence import read_config
        cfg = read_config(tmp_project)

        assert cfg["dvc"] is None

    def test_dvc_section_defaults(self, tmp_project):
        """Partial [dvc] section gets default values for missing fields."""
        _write_config(tmp_project, textwrap.dedent("""
            [dvc]
            remote_path = "/tmp/storage"
        """))

        from wfc.persistence import read_config
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

        from wfc.storage import ensure_dvc_ready, DvcNotConfiguredError

        with pytest.raises(DvcNotConfiguredError, match="No \\[dvc\\] section"):
            ensure_dvc_ready(tmp_project)

    @pytest.mark.parametrize("url", ["s3://bucket", "ssh://user@host/path"])
    def test_non_local_url_accepted(self, tmp_project, url):
        """Non-local remotes (s3://, ssh://, ...) are accepted for multi-backend storage."""
        from wfc.storage import ensure_dvc_ready, init_dvc

        # Production init writes .dvc/config for non-local remotes too.
        init_dvc(tmp_project, {"url": url})
        _write_config(tmp_project, f'\n[dvc]\nurl = "{url}"\n')

        result = ensure_dvc_ready(tmp_project)
        assert result["url"] == url

    def test_dvc_config_missing_remote(self, tmp_project):
        """When [dvc] url is set but .dvc/config has no remote, raise."""
        _write_config(tmp_project, '\n[dvc]\nurl = "/tmp/storage"\n')
        # tmp_project is a real init_project output, so .dvc/config already
        # declares a remote. Remove it to reach the state under test: a
        # project whose wf-canvas.toml names an archive that .dvc/config
        # never got mirrored into.
        (tmp_project / ".dvc" / "config").unlink(missing_ok=True)

        from wfc.storage import ensure_dvc_ready, DvcNotConfiguredError

        with pytest.raises(DvcNotConfiguredError, match="no remotes"):
            ensure_dvc_ready(tmp_project)

    def test_happy_path(self, tmp_project):
        """When everything is configured, return the dvc config dict."""
        from wfc.storage import ensure_dvc_ready, init_dvc

        # Drive the shipped init path (writes .dvc/config) against an
        # out-of-project local-FS remote instead of hand-writing the config.
        outside = tmp_project.parent / f"{tmp_project.name}-storage"
        init_dvc(tmp_project, {"url": str(outside)})
        _write_config(tmp_project, f'\n[dvc]\nurl = "{outside.as_posix()}"\n')

        result = ensure_dvc_ready(tmp_project)
        assert result["url"] == outside.as_posix()

    def test_inside_project_url_rejected(self, tmp_project):
        """A url hand-edited to point inside the project is caught at runtime."""
        inside = tmp_project / "archive"
        _write_config(tmp_project, f'\n[dvc]\nurl = "{inside.as_posix()}"\n')
        _write_dvc_config(tmp_project, url=inside.as_posix())

        from wfc.storage import ensure_dvc_ready, DvcNotConfiguredError

        with pytest.raises(DvcNotConfiguredError, match="inside the project"):
            ensure_dvc_ready(tmp_project)


# =============================================================================
# init_dvc tests
# =============================================================================

class TestInitDvc:
    """Test DVC initialization during wfc init."""

    def test_init_creates_cache_and_remote(self, tmp_project):
        """init_dvc creates .dvc/cache/ structure and remote directory."""
        remote_dir = tmp_project.parent / f"{tmp_project.name}-dvc-storage"
        dvc_config = {
            "url": str(remote_dir),
            "auto_init": True,
        }

        from wfc.storage import init_dvc

        init_dvc(tmp_project, dvc_config)

        # Cache directory should exist
        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()
        # Local-FS remote directory auto-created
        assert remote_dir.exists()

    def test_init_idempotent(self, tmp_project):
        """init_dvc is safe to call multiple times."""
        remote_dir = tmp_project.parent / f"{tmp_project.name}-dvc-storage"
        dvc_config = {
            "url": str(remote_dir),
            "auto_init": True,
        }

        from wfc.storage import init_dvc

        init_dvc(tmp_project, dvc_config)
        init_dvc(tmp_project, dvc_config)  # no error

        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()

    def test_init_mirrors_url_to_dvc_config(self, tmp_project):
        """init_dvc writes [dvc] url to .dvc/config."""
        remote_dir = tmp_project.parent / f"{tmp_project.name}-dvc-storage"
        dvc_config = {"url": str(remote_dir), "auto_init": True}

        from wfc.storage import init_dvc
        init_dvc(tmp_project, dvc_config)

        import configparser
        parser = configparser.ConfigParser()
        parser.read(tmp_project / ".dvc" / "config")
        assert parser.get("core", "remote") == "default"
        assert parser.get('remote "default"', "url") == str(remote_dir)

    def test_init_rejects_inside_project_url(self, tmp_project):
        """A relative url resolving inside the project raises before any mkdir."""
        from wfc.storage import init_dvc, DvcNotConfiguredError

        # A project with a registered env already carries .dvc/cache (the
        # env-blob cache registration writes), so ".dvc absent" is not a
        # producible pre-state. The guard's claim is that it fires before
        # creating or writing anything — assert the state is untouched.
        dvc_dir = tmp_project / ".dvc"
        pre = (sorted(p.as_posix() for p in dvc_dir.rglob("*"))
               if dvc_dir.exists() else None)

        with pytest.raises(DvcNotConfiguredError, match="inside the project"):
            init_dvc(tmp_project, {"url": ".dvc"})

        post = (sorted(p.as_posix() for p in dvc_dir.rglob("*"))
                if dvc_dir.exists() else None)
        assert post == pre

    def test_init_accepts_remote_url(self, tmp_project):
        """init_dvc accepts non-local URLs (s3://, ssh://)."""
        from wfc.storage import init_dvc

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
        """A project whose [dvc] section has auto_init=true and no DVC set up
        yet gets DVC set up by init (confirmed with ``assume_yes``: re-running
        init in an existing project changes nothing unconfirmed)."""
        (tmp_project / ".dvc" / "config").unlink()
        remote_dir = tmp_project.parent / f"{tmp_project.name}-dvc-storage"
        _write_config(tmp_project, textwrap.dedent(f"""
            [dvc]
            url = "{remote_dir.as_posix()}"
            auto_init = true
        """))

        from wfc.init import init_project

        result = init_project(tmp_project, assume_yes=True)

        assert result["dvc"] is True
        # Cache dir should exist
        assert (tmp_project / ".dvc" / "cache" / "files" / "md5").exists()
        # .dvc/config should be mirrored
        import configparser
        parser = configparser.ConfigParser()
        parser.read(tmp_project / ".dvc" / "config")
        assert parser.get('remote "default"', "url") == remote_dir.as_posix()

    def test_init_project_skips_dvc_when_no_dvc_section(self, tmp_project, monkeypatch):
        """When wf-canvas.toml has no [dvc] section, init_project skips DVC init."""
        _write_config(tmp_project)

        from wfc.init import init_project

        dvc_inits = []
        stub_dvc_setup(monkeypatch, init=lambda *a, **k: dvc_inits.append(a))
        result = init_project(tmp_project, assume_yes=True)

        assert dvc_inits == []
        assert result["dvc"] is False


# =============================================================================
# cache_file move-not-copy tests
# =============================================================================

class TestCacheFileMoveNotCopy:
    """cache_file moves staging files into cache by default."""

    def test_cache_file_move_consumes_source(self, tmp_path):
        """Default move=True: same-volume rename removes the source path."""
        import hashlib
        from wfc.storage import cache_file

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
        """move=False preserves the source (the register_sample contract)."""
        import hashlib
        from wfc.storage import cache_file

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
        from wfc.storage import cache_file
        from wfc.storage.cache import _cache_path

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
        from wfc.storage import cache_file

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

        fake_os_rename(monkeypatch, fake_rename)

        dest = cache_file(src, md5, project_dir)

        assert dest.exists()
        assert dest.read_bytes() == content
        assert not src.exists(), "fallback copy+unlink must remove source"

    def test_cache_file_directory_uses_shutil_move(self, tmp_path):
        """A directory input is consumed: its members and manifest land in the cache."""
        from wfc.identity import hash_directory
        from wfc.storage import cache_file, local_path

        src = tmp_path / "staging" / "outdir"
        src.mkdir(parents=True)
        (src / "a.txt").write_bytes(b"alpha")
        (src / "b.txt").write_bytes(b"beta")

        md5 = hash_directory(src)
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        dest = cache_file(src, md5, project_dir)

        assert dest.is_file() and dest.name.endswith(".dir"), dest
        tree = local_path(project_dir, md5)
        assert (tree / "a.txt").read_bytes() == b"alpha"
        assert not src.exists(), "directory source should be consumed by move"


# =============================================================================
# Remote-path derivation
# =============================================================================

from axiom_annotations import workflow as _workflow


@_workflow(
    purpose="A new cache entry is read-only: a cached file is 0444, a cached "
            "directory's manifest and member objects are 0444, and the "
            "checkout a reader is handed has files 0444 and directories 0555, "
            "with no archive pass in between; a mode that cannot be set warns on "
            "stderr and the write still succeeds (Tier 2).",
    inputs="A file and a directory, each cached for the first time",
    outputs="The entries' permission bits, and the warning when chmod fails",
)
def test_cache_file_leaves_new_entries_read_only(tmp_path, monkeypatch, capsys):
    """``cache_file`` protects every entry it writes."""
    import os
    import stat
    from wfc import layout
    from wfc.identity import hash_path
    from wfc.storage import cache_file, local_path
    from wfc.storage.cache import manifest_members

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    src_file = tmp_path / "staging" / "table.csv"
    src_file.parent.mkdir(parents=True)
    src_file.write_bytes(b"id,value\n1,2\n")
    file_entry = cache_file(src_file, hash_path(src_file), project_dir, move=False)
    assert stat.S_IMODE(file_entry.stat().st_mode) == 0o444

    src_dir = tmp_path / "staging" / "bundle"
    (src_dir / "nested").mkdir(parents=True)
    (src_dir / "a.txt").write_bytes(b"alpha")
    (src_dir / "nested" / "b.txt").write_bytes(b"beta")
    dir_hash = hash_path(src_dir)
    manifest = cache_file(src_dir, dir_hash, project_dir, move=False)
    members = [layout.dvc_cache_entry(project_dir, m)
               for m in manifest_members(project_dir, dir_hash)]
    assert len(members) == 2
    for f in (manifest, *members):
        assert stat.S_IMODE(f.stat().st_mode) == 0o444, f
    # The tree a reader is handed is the read-only checkout.
    tree = local_path(project_dir, dir_hash)
    for d in (tree, tree / "nested"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o555, d
    for f in (tree / "a.txt", tree / "nested" / "b.txt"):
        assert stat.S_IMODE(f.stat().st_mode) == 0o444, f

    # A filesystem that refuses the read-only modes: the write still lands.
    real_chmod = os.chmod

    def _refuse_read_only(path, mode, *args, **kwargs):
        if mode in (0o444, 0o555):
            raise PermissionError("chmod refused")
        return real_chmod(path, mode, *args, **kwargs)

    fake_os_chmod(monkeypatch, _refuse_read_only)
    src_late = tmp_path / "staging" / "late.csv"
    src_late.write_bytes(b"late bytes\n")
    late_entry = cache_file(src_late, hash_path(src_late), project_dir, move=False)
    assert late_entry.read_bytes() == b"late bytes\n"
    assert "could not mark cache entry read-only" in capsys.readouterr().err


@_workflow(
    purpose="A [dvc] table that names neither url nor remote_path is refused "
            "by the readiness check with DvcNotConfiguredError naming the url "
            "field, even when .dvc/config declares a remote (Tier 2).",
    inputs="A [dvc] table without url or remote_path, and a .dvc/config remote",
    outputs="DvcNotConfiguredError naming the url field",
)
def test_ensure_dvc_ready_refuses_dvc_table_without_url(tmp_project):
    """The URL check refuses on its own."""
    outside = (tmp_project.parent / f"{tmp_project.name}-storage").as_posix()
    _write_config(tmp_project, "\n[dvc]\nauto_init = true\n")
    _write_dvc_config(tmp_project, url=outside)

    from wfc.storage import ensure_dvc_ready, DvcNotConfiguredError

    with pytest.raises(DvcNotConfiguredError, match="No `url` field"):
        ensure_dvc_ready(tmp_project)


@_workflow(
    purpose="When [dvc] url and .dvc/config's default remote name different "
            "locations, the readiness check passes and returns the config, "
            "warning on stderr with both URLs (Tier 2).",
    inputs="A [dvc] url and a .dvc/config default remote that disagree",
    outputs="The parsed [dvc] config, and a stderr warning naming both URLs",
)
def test_ensure_dvc_ready_warns_on_remote_url_drift(tmp_project, capsys):
    """Drift between the two URLs warns and passes."""
    declared = (tmp_project.parent / f"{tmp_project.name}-storage-a").as_posix()
    mirrored = (tmp_project.parent / f"{tmp_project.name}-storage-b").as_posix()
    _write_config(tmp_project, f'\n[dvc]\nurl = "{declared}"\n')
    _write_dvc_config(tmp_project, url=mirrored)

    from wfc.storage import ensure_dvc_ready

    result = ensure_dvc_ready(tmp_project)

    assert result["url"] == declared
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert declared in err and mirrored in err



@_workflow(
    purpose="Derive the configured DVC remote's filesystem path across all "
            "four documented branches: file:// stripped, non-local scheme "
            "yields no path, relative resolved project-relative, result "
            "resolved absolute (Tier 2).",
    inputs="A project whose [dvc] url and .dvc/config name one remote",
    outputs="The resolved local path, or None for a non-local scheme",
)
@pytest.mark.parametrize(
    "url_template, expected_template",
    [
        pytest.param("file://{parent}/{name}-archive", "{parent}/{name}-archive",
                     id="file-scheme-stripped"),
        pytest.param("s3://bucket/prefix", None,
                     id="non-local-scheme-has-no-filesystem-path"),
        pytest.param("../{name}-archive", "{parent}/{name}-archive",
                     id="relative-is-project-relative"),
        pytest.param("{parent}/nested/../{name}-archive", "{parent}/{name}-archive",
                     id="result-is-resolved-absolute"),
    ],
)
def test_remote_path_derivation(tmp_project, monkeypatch, url_template,
                                expected_template):
    """``_remote_path`` turns a configured remote URL into a local path.

    The derivation reads the URL back through ``ensure_dvc_ready``, so each
    branch is declared the way a project declares it -- the ``[dvc] url``
    in wf-canvas.toml plus a matching ``.dvc/config`` remote -- rather than
    by handing the helper a string. Nothing runs and no container is needed.
    """
    # The relative branch says *project*-relative, and ``tmp_project`` leaves
    # the cwd inside the project -- where project-relative and cwd-relative
    # give the same answer and the branch cannot be failed. Every input the
    # derivation reads is passed explicitly, so stepping out is safe.
    monkeypatch.chdir(tmp_project.parent)
    fields = {"parent": tmp_project.parent.as_posix(), "name": tmp_project.name}
    url = url_template.format(**fields)
    _write_config(tmp_project, f'\n[dvc]\nurl = "{url}"\n')
    _write_dvc_config(tmp_project, url=url)

    from wfc.storage.setup import _remote_path

    result = _remote_path(tmp_project)

    if expected_template is None:
        assert result is None, (
            f"a non-local scheme is not a filesystem path (callers route "
            f"through the remote layer); got {result!r} for url={url!r}"
        )
    else:
        expected = Path(expected_template.format(**fields)).resolve()
        assert result == expected, (
            f"url={url!r} should resolve to {expected}; got {result}"
        )
