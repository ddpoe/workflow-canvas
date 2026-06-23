"""
Shared pytest fixtures for pm_mvp integration tests.

Fixtures:
  - tmp_project: temp dir simulating a pm_mvp project root (DB + method scripts)
  - cli: in-process CLI runner (calls wfc CLI without subprocess)
  - seeded_cli: cli + demo_pipeline seeded in DB (3 methods ready to use)
"""

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import SQLModel, Session, create_engine

# Re-export pipeline-fixture infrastructure so tests at any depth can use
# register_fixture_methods / pipeline_factory without having to live under
# tests/e2e/.  The fixtures themselves live in tests/fixtures/conftest.py.
from tests.fixtures.conftest import register_fixture_methods, pipeline_factory  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# Temporary project directory
# =============================================================================

@pytest.fixture
def git_project(tmp_path):
    """A temp directory initialised as a git repo with one initial commit.

    Used by tmp_project (for registration/cache tests) and wfc_root
    (for Snakefile-generation tests) so that git operations in production
    code never hit a non-repo path.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "wfc@wfc"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "wfc"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _make_wfc_marker(project_dir: Path) -> None:
    """Create the minimal .wfc/wf-canvas.toml marker a wfc project needs so that
    wfc.database.project_root() can resolve this directory as a project."""
    wfc_dir = project_dir / ".wfc"
    wfc_dir.mkdir(parents=True, exist_ok=True)
    marker = wfc_dir / "wf-canvas.toml"
    if not marker.exists():
        marker.write_text('[project]\nname = "test"\n')


@pytest.fixture
def wfc_root(git_project, monkeypatch):
    """Real git-repo path to pass as wfc_module_path to generate_snakefile().

    Snakefile generation calls get_git_commit(wfc_module_path) at generation
    time, which requires a valid git repo on Windows (WinError 267 otherwise).
    It also touches the DB via get_session(), so we set up a .wfc/ marker and
    point WFC_PROJECT_ROOT at this tmp project.
    """
    _make_wfc_marker(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    db_path = git_project / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from wfc.database import reset_engine
    reset_engine()
    yield str(git_project)
    reset_engine()


@pytest.fixture
def tmp_project(git_project, monkeypatch):
    """Temp directory that looks like a pm_mvp project root.

    - already a git repo (via git_project fixture)
    - .wfc/wf-canvas.toml marker so project_root() resolves here
    - WFC_PROJECT_ROOT pinned to this dir (subprocess-safe)
    - cwd set to git_project
    - DATABASE_URL points to in-process SQLite
    - method scripts copied from real methods/
    """
    _make_wfc_marker(git_project)
    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))

    db_path = git_project / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Copy lightweight fixture methods into tmp project
    fixture_methods = PROJECT_ROOT / "tests" / "fixtures" / "methods"
    if fixture_methods.exists():
        shutil.copytree(fixture_methods, git_project / "methods",
                        ignore=shutil.ignore_patterns("__pycache__", "__init__.py"))

    from wfc.database import reset_engine
    reset_engine()

    yield git_project

    reset_engine()


# =============================================================================
# CLI runner
# =============================================================================

@pytest.fixture
def cli(tmp_project):
    """In-process CLI runner. Returns a callable: cli("register_run", "--method", ...)

    Result has .returncode, .stdout, .stderr.
    """
    from wfc.cli import cli_main
    import io
    from contextlib import redirect_stdout, redirect_stderr

    class CLIResult:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

        def __repr__(self):
            return f"CLIResult(rc={self.returncode}, out={self.stdout!r})"

    def run(*args):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli_main(list(args))
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
        return CLIResult(rc, out.getvalue(), err.getvalue())

    return run


# =============================================================================
# Toolchain availability skip markers
# =============================================================================
#
# Live-env capture in `wfc register-env <name> conda:<env>` / `pixi:<...>` shells
# out to the local pixi/conda toolchain. Tests that exercise the capture path
# must skip cleanly when the toolchain isn't installed so CI on bare runners
# (and dev machines without one or the other) stay green.
#
# Usage:
#     pytestmark = requires_pixi   # whole module
#     @requires_conda              # single test


@functools.lru_cache(maxsize=None)
def _pixi_available() -> bool:
    return shutil.which("pixi") is not None


@functools.lru_cache(maxsize=None)
def _conda_available() -> bool:
    return any(shutil.which(b) is not None for b in ("conda", "mamba", "micromamba"))


@functools.lru_cache(maxsize=None)
def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


requires_pixi = pytest.mark.skipif(
    not _pixi_available(),
    reason="pixi not on PATH (required for live-env capture tests)",
)

requires_conda = pytest.mark.skipif(
    not _conda_available(),
    reason="conda/mamba/micromamba not on PATH (required for live-env capture tests)",
)

requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="docker not reachable (required for container build/run tests)",
)
