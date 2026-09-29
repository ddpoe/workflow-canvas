"""Tier 2 tests for the run-readiness probes (wfc/execution/readiness.py)
and the health table that renders them (wfc/preflight.py).

These drive check_git / check_docker / check_dvc through their ok/warn/fail
branches using monkeypatched ``subprocess.run`` and ``shutil.which`` so they
run in the default selection (no real git/docker/dvc required).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import (
    fake_subprocess_run,
    stub_binary_lookup,
    stub_dvc_available,
)
from wfc import preflight
from wfc.execution import readiness


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _git(cwd, *args):
    """Run a git command in ``cwd`` with a deterministic identity."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _committed_repo(root):
    """Init ``root`` as a git repo with one committed tracked file (clean HEAD)."""
    _git(root, "init")
    (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")


# --------------------------------------------------------------------------
# check_git — real tmp repos through the real git probe
# --------------------------------------------------------------------------

@workflow(purpose="check_git classifies a clean committed repo as ok")
def test_check_git_healthy(tmp_path):
    _committed_repo(tmp_path)
    res = readiness.check_git(tmp_path)
    assert res.status == "ok"
    assert res.name == "git"


@workflow(purpose="check_git fails when the git binary is missing")
def test_check_git_no_binary(monkeypatch):
    # Binary presence is a true external edge — keep it faked.
    stub_binary_lookup(monkeypatch, git=None)
    res = readiness.check_git(".")
    assert res.status == "fail"
    assert "not installed" in res.message
    assert res.fix_hint


@workflow(purpose="check_git fails when not inside a git repository")
def test_check_git_no_repo(tmp_path):
    # tmp_path is not under any git work tree — no `git init` here.
    res = readiness.check_git(tmp_path)
    assert res.status == "fail"
    assert res.fix_hint


@workflow(purpose="check_git fails when the repo has no HEAD commit")
def test_check_git_no_commit(tmp_path):
    _git(tmp_path, "init")  # initialized but never committed
    res = readiness.check_git(tmp_path)
    assert res.status == "fail"
    assert "no commit" in res.message.lower()


@workflow(purpose="check_git fails when the tracked tree is dirty")
def test_check_git_dirty(tmp_path):
    _committed_repo(tmp_path)
    (tmp_path / "tracked.py").write_text("x = 2\n", encoding="utf-8")  # modify tracked
    res = readiness.check_git(tmp_path)
    assert res.status == "fail"
    assert "uncommitted" in res.message.lower()


@workflow(purpose="check_git is ok when only untracked files are present")
def test_check_git_untracked_only_ok(tmp_path):
    # Untracked files (porcelain '??') do not block a run — this mirrors
    # get_git_commit's porcelain filtering.
    _committed_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("not added\n", encoding="utf-8")
    res = readiness.check_git(tmp_path)
    assert res.status == "ok"


# --------------------------------------------------------------------------
# check_docker
# --------------------------------------------------------------------------

@workflow(purpose="check_docker is ok when the daemon answers `docker info`")
def test_check_docker_healthy(monkeypatch):
    stub_binary_lookup(monkeypatch, docker="/usr/bin/docker")
    fake_subprocess_run(monkeypatch, lambda *a, **k: _FakeProc(0))
    res = readiness.check_docker()
    assert res.status == "ok"


@workflow(purpose="check_docker fails with a start hint when the daemon is down")
def test_check_docker_daemon_down(monkeypatch):
    stub_binary_lookup(monkeypatch, docker="/usr/bin/docker")
    fake_subprocess_run(
        monkeypatch,
        lambda *a, **k: _FakeProc(1, stderr="Cannot connect to the Docker daemon"),
    )
    res = readiness.check_docker()
    assert res.status == "fail"
    assert "not running" in res.message.lower()
    assert res.fix_hint


@workflow(purpose="check_docker fails when the docker binary is missing")
def test_check_docker_no_binary(monkeypatch):
    stub_binary_lookup(monkeypatch, docker=None)
    res = readiness.check_docker()
    assert res.status == "fail"
    assert "not installed" in res.message


# --------------------------------------------------------------------------
# check_dvc — missing DVC is WARN, never fail
# --------------------------------------------------------------------------

@workflow(purpose="check_dvc warns (never fails) when DVC is not importable")
def test_check_dvc_missing_is_warn(monkeypatch):
    stub_dvc_available(monkeypatch, False)
    res = readiness.check_dvc(".")
    assert res.status == "warn"
    assert "poetry install" in res.fix_hint.lower()


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def test_render_health_table_shows_fix_hints():
    results = [
        readiness.CheckResult("git", "ok", "all good"),
        readiness.CheckResult("docker", "fail", "daemon down", "start it"),
    ]
    table = preflight.render_health_table(results)
    assert "OK" in table
    assert "FAIL" in table
    assert "start it" in table
    # ok rows do not print a hint line
    assert table.count("->") == 1
