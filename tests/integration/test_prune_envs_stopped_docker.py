"""wfc prune-envs removes a real container wfc made that never finished.

A real container created and never started, carrying this (temporary)
project's label and a creator label naming a wfc process that has exited
(as a crashed copy-out leaves it), is planned under "To remove" and removed
by the apply's plain remove. The same container with another project's label
is never listed and stays. The creator is a real process the test started and
waited for; the process table and the daemon are the real ones. Every
container here is the test's own, and it removes them all.
"""

from __future__ import annotations

import subprocess
import sys

import psutil
import pytest
from axiom_annotations import workflow

from tests.conftest import requires_docker
from wfc.environments import apply_prune, plan_prune
from wfc.environments.containers import CREATOR_LABEL, PROJECT_LABEL, creator_label, project_label

pytestmark = [pytest.mark.integration, requires_docker]


def _docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def _dead_creator() -> str:
    """The creator label of a real process that has exited."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        created = psutil.Process(proc.pid).create_time()
    finally:
        proc.kill()
        proc.wait(timeout=30)
    return creator_label(proc.pid, created)


def _create(labels: dict[str, str]) -> str:
    """``docker create`` a never-started container with *labels*; return its id."""
    flags = [f for k, v in labels.items() for f in ("--label", f"{k}={v}")]
    made = _docker("create", *flags, "alpine:3.20", "true")
    assert made.returncode == 0, made.stderr
    return made.stdout.strip()


def _exists(container_id: str) -> bool:
    return _docker("inspect", "--format", "{{.Id}}", container_id).returncode == 0


@workflow(purpose="A real created container with this project's label and a "
                  "creator label naming an exited process is planned under To "
                  "remove and removed by the apply's plain remove; one with "
                  "another project's label is never listed and stays")
def test_a_created_container_whose_creator_is_gone_is_removed(tmp_path):
    """The crashed copy-out's container goes; another project's stays."""
    project, other = tmp_path / "proj", tmp_path / "other"
    project.mkdir()
    other.mkdir()
    creator = _dead_creator()
    left = _create({PROJECT_LABEL: project_label(project), CREATOR_LABEL: creator})
    theirs = _create({PROJECT_LABEL: project_label(other), CREATOR_LABEL: creator})
    try:
        plan = plan_prune(project, run_running=lambda pipeline, run: True)

        assert [c.id for c in plan.unfinished] == [left] and plan.containers == ()
        assert "To remove: 1 container wfc made that never finished." in plan.lines()

        result = apply_prune(plan)

        assert [c.id for c in result.removed] == [left] and not result.errors
        assert not _exists(left) and _exists(theirs)
    finally:
        _docker("rm", "--force", left, theirs)
