"""Tier 3 test for `wfc doctor` exit-code gating and CLI run-readiness reframes.

`wfc doctor` is the one command that exits non-zero on a failing run-readiness
check (D-4). A WARN (e.g. missing DVC, D-5) does not flip the gate. The
run-step / register-env / pre_run CLI reframes turn the specific git-gate and
Docker-down shapes into the friendly "run `wfc doctor`" message.
"""

from __future__ import annotations

import pytest

from axiom_annotations import workflow, Step

import wfc.cli as cli
import wfc.preflight as preflight


def _stub_checks(monkeypatch, git="ok", dvc="ok", docker="ok"):
    monkeypatch.setattr(
        preflight, "check_git",
        lambda *a, **k: preflight.CheckResult("git", git, "git msg", "git hint"),
    )
    monkeypatch.setattr(
        preflight, "check_dvc",
        lambda *a, **k: preflight.CheckResult("dvc", dvc, "dvc msg", "dvc hint"),
    )
    monkeypatch.setattr(
        preflight, "check_docker",
        lambda *a, **k: preflight.CheckResult("docker", docker, "docker msg", "docker hint"),
    )


@workflow(purpose="wfc doctor exits 0 when all checks pass")
def test_doctor_exit_zero_healthy(monkeypatch, capsys):
    _stub_checks(monkeypatch)
    rc = cli.cli_main(["doctor"])
    assert rc == 0


@workflow(purpose="wfc doctor exits non-zero and prints a Docker hint when Docker is down")
def test_doctor_exit_nonzero_docker_down(monkeypatch, capsys):
    口 = Step(step_num=1, name="Docker down", purpose="Force the docker check to fail")
    _stub_checks(monkeypatch, docker="fail")

    口 = Step(step_num=2, name="Exit gate", purpose="doctor returns non-zero with the docker hint")
    rc = cli.cli_main(["doctor"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "FAIL" in out
    assert "docker hint" in out


def test_doctor_warn_does_not_flip_exit(monkeypatch, capsys):
    # A WARN (e.g. missing DVC, D-5) must NOT make doctor exit non-zero.
    _stub_checks(monkeypatch, dvc="warn")
    rc = cli.cli_main(["doctor"])
    assert rc == 0


@workflow(purpose="run-step reframes a Docker-down daemon into the one-door message")
def test_run_step_docker_down_reframed(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight, "check_docker",
        lambda *a, **k: preflight.CheckResult(
            "docker", "fail", "Docker is installed but the daemon is not running.",
            "Start Docker Desktop and try again.",
        ),
    )
    rc = cli.cli_main([
        "run-step", "--node-id", "n1", "--sample", "s1",
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "wfc doctor" in err
    assert "isn't ready to run" in err


@workflow(purpose="pre_run reframes the git no-commit gate into the one-door message")
def test_pre_run_git_gate_reframed(monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("git rev-parse HEAD failed in '/x': fatal")
    monkeypatch.setattr(cli, "pre_run", _boom)
    rc = cli.cli_main([
        "pre_run", "--method", "m", "--module", "mod", "--sample", "s",
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "wfc doctor" in err
