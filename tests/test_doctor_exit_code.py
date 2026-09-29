"""Tier 3 test for `wfc doctor` exit-code gating and CLI run-readiness reframes.

`wfc doctor` is the one command that exits non-zero on a failing run-readiness
check. A WARN (e.g. missing DVC) does not flip the gate. The
run-step / register-env / pre_run CLI reframes turn the specific git-gate and
Docker-down shapes into the friendly "run `wfc doctor`" message.
"""

from __future__ import annotations

import pytest

from axiom_annotations import workflow, Step

import wfc.cli as cli
import wfc.execution.readiness as readiness

from tests.conftest import stub_readiness_probes


@workflow(purpose="wfc doctor exits 0 when all checks pass")
def test_doctor_exit_zero_healthy(monkeypatch, capsys):
    # Every probe stubbed so the exit gate is the only thing under test; the
    # sample probe too, because pytest's cwd is not a wfc project and the
    # real one would report "no wfc project here".
    stub_readiness_probes(monkeypatch, dvc="ok", samples="ok")
    rc = cli.cli_main(["doctor"])
    assert rc == 0


@workflow(purpose="wfc doctor exits non-zero and prints a Docker hint when Docker is down")
def test_doctor_exit_nonzero_docker_down(monkeypatch, capsys):
    口 = Step(step_num=1, name="Docker down", purpose="Force the docker check to fail")
    stub_readiness_probes(monkeypatch, docker="fail", dvc="ok", samples="ok")

    口 = Step(step_num=2, name="Exit gate", purpose="doctor returns non-zero with the docker hint")
    rc = cli.cli_main(["doctor"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "FAIL" in out
    assert "docker hint" in out


def test_doctor_warn_does_not_flip_exit(monkeypatch, capsys):
    # A WARN (e.g. missing DVC) must NOT make doctor exit non-zero.
    stub_readiness_probes(monkeypatch, dvc="warn", samples="ok")
    rc = cli.cli_main(["doctor"])
    assert rc == 0


@workflow(purpose="run-step reframes a Docker-down daemon into the one-door message")
def test_run_step_docker_down_reframed(monkeypatch, capsys):
    stub_readiness_probes(monkeypatch, git=None, docker="fail")
    rc = cli.cli_main([
        "run-step", "--node-id", "n1", "--sample", "s1",
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "wfc doctor" in err
    assert "isn't ready to run" in err


@workflow(purpose="pre_run reframes the git no-commit gate into the one-door message")
def test_pre_run_git_gate_reframed(tmp_path, monkeypatch, capsys):
    """A real git repo with NO commits drives the real ``get_git_commit``
    inside ``pre_run`` (step 1, before any method lookup) to raise the
    ``git rev-parse HEAD failed`` shape, which the CLI reframes into the
    one-door message — exercising both sides of the version.py <-> cli.py
    string contract for real instead of fabricating it."""
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".wfc").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / '.wfc' / 'wfc.db'}")
    from wfc.persistence import reset_engine
    reset_engine()

    rc = cli.cli_main([
        "pre_run", "--method", "m", "--module", "mod", "--sample", "s",
    ])
    err = capsys.readouterr().err
    assert rc == 1
    assert "wfc doctor" in err
    assert "isn't ready to run" in err
