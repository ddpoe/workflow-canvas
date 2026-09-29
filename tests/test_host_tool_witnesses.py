"""Host tools answered for real, where the default suite fakes them.

Each test here drives, with nothing faked, a boundary that a
``tests.fixtures.fakes`` entry replaces elsewhere, and proves the one thing
that entry says it does not prove. Each skips when its tool is absent, with a
reason naming the tool.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (requires_conda, requires_docker_cli, requires_dvc,
                            requires_pip)
from tests.fixtures.routes import run_cli


@requires_conda
def test_conda_lists_a_real_env_explicitly():
    """``conda list --explicit --md5`` over conda's own base env answers a list.

    The explicit format opens with ``@EXPLICIT`` and pins each package by URL
    and MD5 (``<url>#<md5>``).
    """
    from wfc.environments.introspect import conda_list_explicit

    base = subprocess.run(["conda", "info", "--base"], capture_output=True,
                          text=True, check=True).stdout.strip()

    listing = conda_list_explicit(base)

    assert "@EXPLICIT" in listing
    pinned = [line for line in listing.splitlines()
              if line.startswith(("http", "file:")) and "#" in line]
    assert pinned, listing[:500]


@requires_pip
def test_pip_freeze_of_the_running_interpreter_lists_pytest():
    """The live capture's pip freeze, run on this interpreter, lists pytest."""
    from wfc.environments.introspect import pip_freeze_best_effort

    freeze = pip_freeze_best_effort(sys.executable)

    names = {line.split("==", 1)[0].split(" @ ", 1)[0].strip().lower()
             for line in freeze.splitlines() if line.strip()}
    assert "pytest" in names, freeze[:500]


@requires_dvc
def test_check_dvc_is_ok_with_dvc_importable_and_an_archive_configured(
        tmp_project):
    """With DVC importable and the project's archive in place, the probe says ok."""
    from wfc.execution.readiness import check_dvc

    result = check_dvc(tmp_project)

    assert result.status == "ok", (result.message, result.fix_hint)


#: The probe run in the child: ``wfc/execution/readiness.py`` loaded from its
#: file (the package's ``__init__`` imports the run lifecycle, whose
#: dependencies live in site-packages, which this interpreter does not have),
#: the DVC preconditions reported alongside the probe's answer.
_CHILD_CHECK_DVC = """
import importlib.util, json, shutil, sys
try:
    import dvc  # noqa: F401
    importable = True
except ImportError:
    importable = False
spec = importlib.util.spec_from_file_location("readiness_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
result = module.check_dvc(sys.argv[2])
print(json.dumps({"importable": importable, "on_path": shutil.which("dvc"),
                  "result": [result.name, result.status, result.message,
                             result.fix_hint]}))
"""


def _check_dvc_in_an_interpreter_without_dvc(project_dir: Path):
    """Run the real ``check_dvc`` in an interpreter that has no DVC.

    The child is this interpreter started isolated and without
    site-packages (``-I -S``), so ``import dvc`` has nowhere to find the
    package, and with every PATH entry that holds a ``dvc`` executable
    removed, so the binary fallback finds nothing either. The child reports
    both conditions next to its answer, and they are asserted here.

    Args:
        project_dir: The directory the probe is pointed at.

    Returns:
        The child's ``CheckResult``.
    """
    from wfc.execution import readiness

    path = os.pathsep.join(
        entry for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and shutil.which("dvc", path=entry) is None)
    child = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _CHILD_CHECK_DVC,
         readiness.__file__, str(project_dir)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": path})
    assert child.returncode == 0, child.stderr
    answer = json.loads(child.stdout)
    assert (answer["importable"], answer["on_path"]) == (False, None), answer
    return readiness.CheckResult(*answer["result"])


def test_check_dvc_warns_in_an_interpreter_without_dvc(tmp_path):
    """An interpreter that cannot import DVC, with no ``dvc`` on PATH, is told so.

    The probe's own availability check sees the absence and answers a warn
    that names it, never a fail.
    """
    result = _check_dvc_in_an_interpreter_without_dvc(tmp_path)

    assert (result.name, result.status, result.message, result.fix_hint) == (
        "dvc", "warn",
        "DVC is not importable / not on PATH (it ships as a project "
        "dependency).",
        "Re-install dependencies with `poetry install`.")


#: What the probe tells a user whose daemon is stopped, per platform.
_DOCKER_START_HINT = {
    "Windows": "Start Docker Desktop and wait for it to report 'running', "
               "then try again.",
    "Darwin": "Start Docker Desktop and wait for it to report 'running', "
              "then try again.",
}.get(platform.system(),
      "Start the Docker daemon (e.g. `sudo systemctl start docker`), then "
      "try again.")


def _point_docker_at_a_dead_endpoint(monkeypatch) -> None:
    """Aim the Docker CLI at a port nothing listens on, so it cannot connect.

    ``DOCKER_HOST`` overrides the CLI's context; the context variable is
    cleared so it cannot compete.
    """
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)


def _stopped_daemon_at_run_step(tmp_path, monkeypatch):
    """``wfc run-step`` against a daemon it cannot reach: the one-door message."""
    _point_docker_at_a_dead_endpoint(monkeypatch)

    result = run_cli("run-step", "--node-id", "n1", "--sample", "s1")

    assert result.returncode == 1
    return result.stderr, (
        "This project isn't ready to run — Docker is installed but the "
        "daemon is not running.\n"
        f"  {_DOCKER_START_HINT}\n"
        "Run `wfc doctor` to see all run-readiness checks.\n")


def _missing_git_repo_at_doctor(tmp_path, monkeypatch):
    """``wfc doctor`` in a directory no git repository contains: the git rows."""
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    # Stop git's upward search at the temp root, so no enclosing repository
    # can answer for the directory.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(bare)
    _point_docker_at_a_dead_endpoint(monkeypatch)

    result = run_cli("doctor")

    assert result.returncode == 1
    lines = result.stdout.splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("  [") and line[9:].startswith("git "))
    return "\n".join(lines[at:at + 2]), (
        "  [FAIL] git      No git repository here — wfc versions every run "
        "by its git commit.\n"
        "                  -> Run `wfc init` (it initializes git "
        "automatically) or `git init`.")


def _absent_dvc_at_doctor_table(tmp_path, monkeypatch):
    """The probe in an interpreter without DVC, rendered by doctor's table."""
    from wfc.preflight import render_health_table

    result = _check_dvc_in_an_interpreter_without_dvc(tmp_path)

    return render_health_table([result]), (
        "Run-readiness:\n"
        "  [WARN] dvc  DVC is not importable / not on PATH (it ships as a "
        "project dependency).\n"
        "              -> Re-install dependencies with `poetry install`.")


@pytest.mark.parametrize("drive", [
    pytest.param(_stopped_daemon_at_run_step, id="stopped-daemon",
                 marks=requires_docker_cli),
    pytest.param(_missing_git_repo_at_doctor, id="missing-git-repo"),
    pytest.param(_absent_dvc_at_doctor_table, id="absent-dvc"),
])
def test_a_machine_that_is_not_ready_is_detected_and_reframed_at_the_door(
        drive, tmp_path, monkeypatch):
    """Each not-ready machine is detected by the real probe and said plainly.

    Nothing is stubbed. A stopped daemon is a Docker CLI that cannot reach
    its endpoint, and ``wfc run-step`` refuses with the one-door message
    that points at ``wfc doctor``. A missing git repository is a directory
    no repository encloses, and ``wfc doctor`` -- the door that message
    names -- reports it as a failing git row with its fix. An absent DVC is
    an interpreter that cannot import it, and the probe's warn renders
    through the same health table.
    """
    shown, expected = drive(tmp_path, monkeypatch)

    assert shown == expected


@requires_dvc
def test_a_step_run_with_a_remote_configured_leaves_its_outputs_pending(
        tmp_project, monkeypatch):
    """The record phase, not the test, marks a run's outputs pending a push.

    The project's ``.dvc/config`` declares its local-folder archive as the
    remote, so the push rule reads pending, and the step's own record phase
    writes it on every output row.
    """
    from wfc.storage import first_push_status
    from wfc.storage.transport import has_remote_configured

    from tests.fixtures.routes import completed_run

    assert has_remote_configured(tmp_project)
    run = completed_run(tmp_project, monkeypatch=monkeypatch,
                        method="pusher", outputs={"table": ".csv",
                                                  "notes": ".txt"})

    rows = run.output_rows
    assert {row["slot"] for row in rows} == {"table", "notes"}
    assert {row["push_status"] for row in rows} == {
        first_push_status(tmp_project)}
    assert {str(row["push_status"]) for row in rows} <= {
        "pending", "PushStatus.pending"}
