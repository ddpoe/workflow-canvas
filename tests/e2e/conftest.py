"""
Shared fixtures for E2E workflow tests.

Fixtures provide ONLY physical resources — files on disk, database
connections, temp directories.  All production operations (init,
register-module, register-method, load-pipeline, run-pipeline) happen
in the test body with step markers so the workflow is fully documented.

Fixtures:
  - wfc_project:   Temp dir with method scripts and pipeline configs
                   pre-staged as test data.  cwd + DATABASE_URL wired.
  - cli:           In-process CLI runner wrapping wfc's argparse.
  - register_fixture_methods: (from tests/fixtures/conftest.py)
  - pipeline_factory: (from tests/fixtures/conftest.py)
"""

import io
import os
import shutil
import stat
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

# Re-export fixture method infrastructure from tests/fixtures/conftest.py
# so pytest discovers them in this conftest's scope.
from tests.fixtures.conftest import register_fixture_methods, pipeline_factory  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAST_RUN_DIR = Path(__file__).resolve().parent / "_last_run"


def pytest_addoption(parser):
    parser.addoption(
        "--keep-wfc-data",
        action="store_true",
        default=False,
        help=(
            "After the e2e test, copy the tmp wfc project to "
            "tests/e2e/_last_run/ so workflow_canvas can load it. "
            "Point canvas at that directory with POST /api/wfc/load."
        ))


# =============================================================================
# CLI result wrapper
# =============================================================================

class CLIResult:
    """Wraps CLI invocation output for assertions."""

    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self):
        return f"CLIResult(rc={self.returncode}, out={self.stdout!r})"


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def wfc_project(git_project, monkeypatch, request):
    """Temp directory with method scripts and pipeline configs pre-staged.

    Provides physical resources only:
      - cwd → git_project (a real git repo — required by register_method)
      - DATABASE_URL → git_project/.wfc/wfc.db
      - Method scripts copied from real methods/ (test data)
      - Pipeline configs copied from project root (test data)
      - DB engine reset for isolation

    Does NOT call init_project, register_module, or any production logic.
    Those belong in the test body.
    """
    tmp_path = git_project
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / ".wfc" / "wfc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Override PIXI_HOME so test method envs are created in-project
    # (not ~/.pixi detached) and get cleaned up with tmp_path.
    # Conda package cache is system-level, so no re-download penalty.
    test_pixi_home = tmp_path / ".pixi_home"
    test_pixi_home.mkdir()
    monkeypatch.setenv("PIXI_HOME", str(test_pixi_home))

    # NOTE: Stale methods/ and modules/ directories have been removed from the
    # project root. Tests now use lightweight fixture methods registered via
    # the register_fixture_methods fixture instead.

    # Pre-stage pipeline configs as test data
    for pattern in ("pipeline_*.json", "us*.json"):
        for src in PROJECT_ROOT.glob(pattern):
            shutil.copy2(src, tmp_path / src.name)

    from wfc.database import reset_engine
    reset_engine()

    yield tmp_path

    # Preserve the project directory for workflow_canvas inspection
    if request.config.getoption("--keep-wfc-data", default=False):
        if LAST_RUN_DIR.exists():
            def _force_remove(func, path, exc):
                os.chmod(path, stat.S_IWRITE)
                func(path)
            shutil.rmtree(LAST_RUN_DIR, onexc=_force_remove)
        shutil.copytree(tmp_path, LAST_RUN_DIR)
        print(f"\n[keep-wfc-data] Saved project to: {LAST_RUN_DIR}")
        print(f"[keep-wfc-data] DB path: {LAST_RUN_DIR / '.wfc' / 'wfc.db'}")
        print(f"[keep-wfc-data] Load in canvas: POST /api/wfc/load {{\"project_root\": \"{LAST_RUN_DIR}\"}}")  # noqa: E501

    reset_engine()


@pytest.fixture
def cli(wfc_project):
    """In-process CLI runner.

    Usage:
        result = cli("register_run", "--method", "preprocess", "--sample", "Pa16c")
        run_id = int(result.stdout.strip())

    Returns CLIResult with .returncode, .stdout, .stderr.
    """
    from wfc.cli import cli_main

    def run(*args):
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli_main(list(args))
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
        return CLIResult(rc, out.getvalue(), err.getvalue())

    return run


