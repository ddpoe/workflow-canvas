"""
Regression tests for project-root resolution (bug: wfc subprocesses inherit
foreign cwd from Snakemake under Windows UNC paths and try to mkdir
``C:\\Windows\\.wfc``).

The fix introduces ``wfc.database.project_root()``: an explicit resolver that
prefers ``WFC_PROJECT_ROOT`` env var, falls back to walking upward for the
``.wfc/wf-canvas.toml`` marker, and raises if neither succeeds. ``_default_db_url``
and ``runs_dir`` route through it instead of ``Path.cwd()``.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from wfc.database import (
    project_root,
    _default_db_url,
    runs_dir,
    reset_engine,
)


def _make_project(root: Path) -> Path:
    """Create a minimal wfc project marker (.wfc/wf-canvas.toml)."""
    wfc_dir = root / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    (wfc_dir / "wf-canvas.toml").write_text(
        '[project]\nname = "test"\n'
    )
    return root


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test starts with a clean env — no inherited PM_* / DATABASE_URL."""
    monkeypatch.delenv("WFC_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine()
    yield
    reset_engine()


# =============================================================================
# project_root() resolver
# =============================================================================

class TestProjectRootResolver:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        """WFC_PROJECT_ROOT env var is the canonical override."""
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

        assert project_root() == proj.resolve()

    def test_walks_up_to_find_marker(self, tmp_path, monkeypatch):
        """When env var not set, walk up from cwd looking for .wfc/wf-canvas.toml."""
        proj = _make_project(tmp_path / "proj")
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert project_root() == proj.resolve()

    def test_raises_when_no_marker_and_no_env(self, tmp_path, monkeypatch):
        """No env var, no marker found by walking up → raise, don't silently
        create .wfc/ in the wrong place."""
        nowhere = tmp_path / "nowhere"
        nowhere.mkdir()
        monkeypatch.chdir(nowhere)

        with pytest.raises(RuntimeError, match="project root"):
            project_root()

    def test_env_var_pointing_at_non_project_raises(self, tmp_path, monkeypatch):
        """WFC_PROJECT_ROOT must point at a real wfc project (have wf-canvas.toml)."""
        bogus = tmp_path / "bogus"
        bogus.mkdir()
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(bogus))

        with pytest.raises(RuntimeError, match="wf-canvas.toml"):
            project_root()


# =============================================================================
# _default_db_url and runs_dir route through project_root
# =============================================================================

class TestDatabaseRoutesThroughProjectRoot:
    def test_default_db_url_uses_project_root_not_cwd(self, tmp_path, monkeypatch):
        """The exact bug: cwd is foreign (e.g. C:\\Windows under cmd.exe UNC
        rejection), but WFC_PROJECT_ROOT points at the real project. The DB URL
        must point inside the project, and no stray .wfc/ may appear under cwd.
        """
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

        url = _default_db_url()

        expected_db = proj.resolve() / ".wfc" / "wfc.db"
        assert url == f"sqlite:///{expected_db}"
        assert not (foreign / ".wfc").exists(), \
            "must not create .wfc/ under foreign cwd"

    def test_runs_dir_uses_project_root_not_cwd(self, tmp_path, monkeypatch):
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))

        d = runs_dir()

        assert d == proj.resolve() / ".runs"
        assert d.exists()
        assert not (foreign / ".runs").exists()


# =============================================================================
# End-to-end: real subprocess inherits foreign cwd + WFC_PROJECT_ROOT
# =============================================================================

class TestCLIRoutesThroughProjectRoot:
    """Regression for the second wave of the UNC-cwd bug: wfc.cli functions
    computed their own `project_root = Path.cwd()` and passed that into the
    DVC/provenance layer. On Windows-UNC-cmd.exe scenarios cwd is C:\\Windows,
    which caused `wfc restore-sample` to raise FileNotFoundError:
    'No wfc project found at C:\\Windows'.

    These tests simulate the scenario by chdir-ing to a foreign tmp directory
    and setting WFC_PROJECT_ROOT to point at the real project, then verifying
    that the cli functions resolve the project root via wfc.database.project_root()
    and hand the real path to the layers they delegate to.
    """

    def _setup_foreign_cwd(self, tmp_path, monkeypatch):
        proj = _make_project(tmp_path / "real_project")
        foreign = tmp_path / "foreign_cwd"
        foreign.mkdir()
        monkeypatch.chdir(foreign)
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
        return proj, foreign

    def test_restore_sample_uses_project_root_not_cwd(
        self, tmp_path, monkeypatch
    ):
        """restore_sample must pass the real project_root — not Path.cwd() —
        into provenance.restore_from_cache / pull_cache."""
        from unittest.mock import patch
        from wfc.models import Sample
        from wfc.database import get_session
        from wfc.cli import restore_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)

        with get_session() as session:
            session.add(Sample(
                name="sample_x",
                source_path="/orig/data.csv",
                registered_path=str(proj / "data" / "samples" / "sample_x" / "data.csv"),
                file_type="csv",
                registration_mode="copy",
                content_hash="abc123",
            ))
            session.commit()

        seen_roots: list[Path] = []

        def fake_restore(hash_val, dest, project_root):
            seen_roots.append(project_root)
            return True

        with patch("wfc.provenance.restore_from_cache", side_effect=fake_restore):
            restore_sample(name="sample_x")

        assert seen_roots, "restore_from_cache was not called"
        assert Path(seen_roots[0]).resolve() == proj.resolve()
        assert Path(seen_roots[0]).resolve() != foreign.resolve()

    def test_restore_sample_creates_sentinel_under_project_root(
        self, tmp_path, monkeypatch
    ):
        """After a successful restore, wfc.cli.restore_sample must touch
        ``<project_root>/data/samples/<name>/.sample_ready`` so the Snakemake
        rule's declared output appears. The parent directory must be created
        if missing (ADR-009 doesn't materialize data/samples/<name>/ eagerly).

        This replaces the old inline ``python -c 'Path(...).touch()'`` in the
        generated shell rule, which was cwd-dependent (broke under Windows UNC
        cmd.exe cwd rewrites) and didn't mkparents."""
        from unittest.mock import patch
        from wfc.models import Sample
        from wfc.database import get_session
        from wfc.cli import restore_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)

        with get_session() as session:
            session.add(Sample(
                name="sample_sentinel",
                source_path="/orig/data.csv",
                registered_path=str(proj / "data" / "samples" / "sample_sentinel" / "data.csv"),
                file_type="csv",
                registration_mode="copy",
                content_hash="abc123",
            ))
            session.commit()

        expected_sentinel = proj / "data" / "samples" / "sample_sentinel" / ".sample_ready"
        foreign_sentinel = foreign / "data" / "samples" / "sample_sentinel" / ".sample_ready"
        assert not expected_sentinel.exists()
        assert not expected_sentinel.parent.exists(), (
            "parent dir must not pre-exist — restore_sample is required to mkparents"
        )

        with patch("wfc.provenance.restore_from_cache", return_value=True):
            restore_sample(name="sample_sentinel")

        assert expected_sentinel.exists(), (
            f"sentinel not created at expected path {expected_sentinel}"
        )
        assert not foreign_sentinel.exists(), (
            "sentinel must not appear under foreign cwd"
        )

    def test_register_sample_uses_project_root_not_cwd(
        self, tmp_path, monkeypatch
    ):
        """register_sample's default project_root must come from
        wfc.database.project_root(), not Path.cwd()."""
        from unittest.mock import patch
        from wfc.cli import register_sample

        proj, foreign = self._setup_foreign_cwd(tmp_path, monkeypatch)
        src = tmp_path / "input.csv"
        src.write_text("a,b\n1,2\n")

        seen_roots: list[Path] = []

        def fake_ensure_dvc_ready(project_root):
            seen_roots.append(project_root)
            raise SystemExit(0)  # bail out — we only care about the root arg

        with patch("wfc.provenance.ensure_dvc_ready", side_effect=fake_ensure_dvc_ready):
            with pytest.raises(SystemExit):
                register_sample(name="sample_y", source_path=src)

        assert seen_roots, "ensure_dvc_ready was not called"
        assert Path(seen_roots[0]).resolve() == proj.resolve()

    # ADR-018: restore_output deleted (cache is authoritative; resolve_input
    # returns the cache path directly).  The equivalent project-root contract
    # is exercised in tests/test_resolve.py.


@pytest.mark.slow
def test_subprocess_with_foreign_cwd_honors_env_var(tmp_path):
    """Spawn a real python subprocess with cwd=foreign tempdir and
    WFC_PROJECT_ROOT=real project. Importing wfc.database and calling
    _default_db_url must use the project, not cwd. Mirrors the Snakemake
    shell-rule scenario from the bug report.
    """
    proj = _make_project(tmp_path / "real_project")
    foreign = tmp_path / "foreign_cwd"
    foreign.mkdir()

    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env.pop("DATABASE_URL", None)

    code = (
        "from wfc.database import _default_db_url, runs_dir; "
        "print(_default_db_url()); print(runs_dir())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=foreign,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout.strip().splitlines()
    expected_db = (proj.resolve() / ".wfc" / "wfc.db")
    assert str(expected_db) in out[0]
    assert str(proj.resolve() / ".runs") in out[1]
    assert not (foreign / ".wfc").exists()
    assert not (foreign / ".runs").exists()
