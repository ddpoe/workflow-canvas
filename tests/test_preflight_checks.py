"""Tier 2 tests for the run-readiness health-check engine (wfc/preflight.py).

These drive check_git / check_docker / check_dvc through their ok/warn/fail
branches using monkeypatched ``subprocess.run`` and ``shutil.which`` so they
run in the default selection (no real git/docker/dvc required).
"""

from __future__ import annotations

import subprocess

import pytest

from axiom_annotations import workflow

from wfc import preflight


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _git_subprocess(*, inside=True, has_head=True, dirty=False):
    """Build a fake subprocess.run that simulates a git repo state."""

    def _run(cmd, *args, **kwargs):
        sub = cmd[1:]
        if sub[:2] == ["rev-parse", "--is-inside-work-tree"]:
            return _FakeProc(0 if inside else 128)
        if sub[:2] == ["rev-parse", "HEAD"]:
            return _FakeProc(0 if has_head else 128, stdout="abc123\n")
        if sub[:2] == ["status", "--porcelain"]:
            out = " M tracked.py\n" if dirty else ""
            return _FakeProc(0, stdout=out)
        return _FakeProc(0)

    return _run


# --------------------------------------------------------------------------
# check_git
# --------------------------------------------------------------------------

@workflow(purpose="check_git classifies a clean committed repo as ok")
def test_check_git_healthy(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", _git_subprocess())
    res = preflight.check_git(".")
    assert res.status == "ok"
    assert res.name == "git"


@workflow(purpose="check_git fails when the git binary is missing")
def test_check_git_no_binary(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    res = preflight.check_git(".")
    assert res.status == "fail"
    assert "not installed" in res.message
    assert res.fix_hint


@workflow(purpose="check_git fails when not inside a git repository")
def test_check_git_no_repo(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", _git_subprocess(inside=False))
    res = preflight.check_git(".")
    assert res.status == "fail"
    assert res.fix_hint


@workflow(purpose="check_git fails when the repo has no HEAD commit")
def test_check_git_no_commit(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", _git_subprocess(has_head=False))
    res = preflight.check_git(".")
    assert res.status == "fail"
    assert "no commit" in res.message.lower()


@workflow(purpose="check_git fails when the tracked tree is dirty")
def test_check_git_dirty(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", _git_subprocess(dirty=True))
    res = preflight.check_git(".")
    assert res.status == "fail"
    assert "uncommitted" in res.message.lower()


# --------------------------------------------------------------------------
# check_docker
# --------------------------------------------------------------------------

@workflow(purpose="check_docker is ok when the daemon answers `docker info`")
def test_check_docker_healthy(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeProc(0)
    )
    res = preflight.check_docker()
    assert res.status == "ok"


@workflow(purpose="check_docker fails with a start hint when the daemon is down")
def test_check_docker_daemon_down(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _FakeProc(1, stderr="Cannot connect to the Docker daemon"),
    )
    res = preflight.check_docker()
    assert res.status == "fail"
    assert "not running" in res.message.lower()
    assert res.fix_hint


@workflow(purpose="check_docker fails when the docker binary is missing")
def test_check_docker_no_binary(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    res = preflight.check_docker()
    assert res.status == "fail"
    assert "not installed" in res.message


# --------------------------------------------------------------------------
# check_dvc — D-5: missing DVC is WARN, never fail
# --------------------------------------------------------------------------

@workflow(purpose="check_dvc warns (never fails) when DVC is not importable")
def test_check_dvc_missing_is_warn(monkeypatch):
    monkeypatch.setattr(preflight, "_dvc_available", lambda: False)
    res = preflight.check_dvc(".")
    assert res.status == "warn"
    assert "poetry install" in res.fix_hint.lower()


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

def test_render_health_table_shows_fix_hints():
    results = [
        preflight.CheckResult("git", "ok", "all good"),
        preflight.CheckResult("docker", "fail", "daemon down", "start it"),
    ]
    table = preflight.render_health_table(results)
    assert "OK" in table
    assert "FAIL" in table
    assert "start it" in table
    # ok rows do not print a hint line
    assert table.count("->") == 1
