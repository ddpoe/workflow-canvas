"""Ctrl-C during register-env's capture ends with one plain line, exit 130.

Every registration runs the production verb
(``wfc.environments.verbs.register_env`` -> ``wfc.environments.register``)
from a live conda env, off a terminal (pytest's captured stderr). The
capture is faked at its subprocess boundary (``conda info --base`` and
``conda list --explicit``; git runs for real), the image inspects at
``subprocess.run`` too, and the build at ``wfc.environments.docker.build``.
A Ctrl-C is the ``KeyboardInterrupt`` the faked command raises, as
``subprocess.run`` raises it when the console's Ctrl-C reaches wfc and the
command together.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_subprocess_run,
    real_subprocess_run,
    stub_docker_build,
    stub_host_conda_platform,
)
from wfc import layout
from wfc.environments import progress

ENV = "tools"
CONDA_LIST = ("@EXPLICIT\n"
              "https://conda.anaconda.org/conda-forge/linux-64/"
              "python-3.11.0-h.conda#0a\n")
# What the faked daemon answers `docker image inspect --format <f>` with.
INSPECT = {"{{.Id}}": "sha256:" + "c" * 64 + "\n",
           "{{json .}}": json.dumps({"RootFS": {"Layers": ["sha256:" + "1" * 64]},
                                     "Config": {"Env": ["PATH=/usr/bin"],
                                                "User": "", "WorkingDir": "/"}})}


def _live_conda(monkeypatch, tmp_path: Path, *, interrupt: str) -> list[list[str]]:
    """A conda env on the host whose command *interrupt* starts is stopped by Ctrl-C.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        tmp_path: Where the conda root is made.
        interrupt: The leading words of the command that raises
            ``KeyboardInterrupt`` (``conda list --explicit``).

    Returns:
        The conda and docker commands run, in order.
    """
    conda_root = tmp_path / "conda"
    (conda_root / "envs" / ENV / "conda-meta").mkdir(parents=True)
    ran: list[list[str]] = []

    def host(cmd, *args, **kwargs):
        if cmd[0] in ("conda", "docker"):
            ran.append(list(cmd))
        if " ".join(cmd).startswith(interrupt):
            raise KeyboardInterrupt
        if cmd[:3] == ["conda", "info", "--base"]:
            return canned_process(stdout=f"{conda_root}\n")(cmd)
        if cmd[:3] == ["conda", "list", "--explicit"]:
            return canned_process(stdout=CONDA_LIST)(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            return canned_process(stdout=INSPECT[cmd[cmd.index("--format") + 1]])(cmd)
        if cmd[0] in ("conda", "docker"):
            raise AssertionError(f"unexpected call {cmd}")
        return real_subprocess_run(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, host)
    stub_host_conda_platform(monkeypatch, "linux-64")
    return ran


def _register() -> int:
    from wfc.environments import verbs

    return verbs.register_env(ENV, spec=f"conda:{ENV}", docker_gate=lambda: None)


def _logs(project: Path) -> list[Path]:
    return sorted(layout.register_env_log_dir(project).glob(f"{ENV}-*.log"))


def _git_status(project: Path) -> str:
    """Porcelain status, one line per file (an untracked folder never collapses)."""
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                          cwd=project,
                          capture_output=True, text=True, check=True).stdout


def _recorded(project: Path) -> bool:
    manifest = layout.env_manifest_path(project)
    return manifest.exists() and ENV in json.loads(
        manifest.read_text(encoding="utf-8")).get("envs", {})


@workflow(purpose="Ctrl-C during a live conda env's capture ends register-env "
                  "with one plain line naming the log, no traceback, exit 130; "
                  "nothing is recorded, no captures folder is written, no "
                  "build starts, and the log names the capture call that was "
                  "stopped")
def test_ctrl_c_during_the_capture_ends_with_one_line_and_records_nothing(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """A Ctrl-C in the long capture ends as the build's does: one line, 130."""
    口 = Step(step_num=1, name="A live conda env whose list is stopped by Ctrl-C",
             purpose="`conda list --explicit` raises KeyboardInterrupt; the "
                     "build would fail the test if it were reached")
    ran = _live_conda(monkeypatch, tmp_path_factory.mktemp("host"),
                      interrupt="conda list --explicit")
    stub_docker_build(monkeypatch, AssertionError("the build was started"))
    before = set(_git_status(tmp_project).splitlines())

    口 = Step(step_num=2, name="Register the env", purpose="The production verb")
    rc = _register()
    err = capsys.readouterr().err

    口 = Step(step_num=3, name="One plain line naming the log, exit 130",
             purpose="Besides the phase lines, stderr holds only the Stopped "
                     "line, which names the log; no traceback")
    assert rc == 130, err
    (log,) = _logs(tmp_project)
    others = [ln for ln in err.splitlines()
              if not ln.startswith(progress.LINE_PREFIX)]
    assert others == ["Stopped: the capture was stopped and nothing was "
                      f"recorded. (log: {log})"]
    assert "Traceback" not in err

    口 = Step(step_num=4, name="The log names the stopped phase",
             purpose="The capture and its conda list call end as stopped, in "
                     "the log as on stderr, and the log keeps the Stopped line")
    text = log.read_text(encoding="utf-8")
    assert re.search(r"\bconda list --explicit stopped \(\d+\.\ds\)$", text,
                     re.MULTILINE), text
    assert re.search(r"\bcapture stopped \(\d+\.\ds\)$", text, re.MULTILINE), text
    assert "Stopped: the capture was stopped and nothing was recorded." in text

    口 = Step(step_num=5, name="Nothing recorded, nothing captured, nothing built",
             purpose="No record, no captures folder, no command after the "
                     "stopped one, and git status as it was")
    assert not _recorded(tmp_project)
    assert not layout.env_captures_dir(tmp_project, ENV).exists()
    assert ran[-1][:3] == ["conda", "list", "--explicit"]
    assert set(_git_status(tmp_project).splitlines()) <= before


def test_ctrl_c_after_the_build_keeps_its_interrupt(tmp_project, monkeypatch,
                                                    capsys, tmp_path_factory):
    """Ctrl-C once the image is built is not turned into the capture's line."""
    _live_conda(monkeypatch, tmp_path_factory.mktemp("host"),
                interrupt="docker image inspect sha256:")
    stub_docker_build(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        _register()

    err = capsys.readouterr().err
    assert "Stopped:" not in err
    assert re.search(r"\bbuild done \(", err), err
    assert not _recorded(tmp_project)
