"""register-env says what it is doing, keeps a log, and ends plainly on Ctrl-C.

Every registration runs the production verb
(``wfc.environments.verbs.register_env`` -> ``wfc.environments.register``)
from a live conda env, off a terminal (pytest's captured stderr). The
capture is faked at its subprocess boundary (``conda info --base`` and
``conda list --explicit``; git runs for real), the image inspects at
``subprocess.run`` too, and the build at ``wfc.environments.docker.build``,
whose faked output reaches the sink the registration hands it.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_subprocess_run,
    real_subprocess_run,
    short_register_env_heartbeat,
    stub_docker_build,
    stub_host_conda_platform,
)
from wfc import layout
from wfc.environments import progress

ENV = "tools"
CONDA_LIST = ("@EXPLICIT\n"
              "https://conda.anaconda.org/conda-forge/linux-64/"
              "python-3.11.0-h.conda#0a\n")
# The faked build's output: more lines than the 40 an error keeps.
BUILD_LINES = [f"#{i} step output line {i}" for i in range(1, 61)]
# What the faked daemon answers `docker image inspect --format <f>` with.
INSPECT = {"{{.Id}}": "sha256:" + "c" * 64 + "\n",
           "{{json .}}": json.dumps({"RootFS": {"Layers": ["sha256:" + "1" * 64]},
                                     "Config": {"Env": ["PATH=/usr/bin"],
                                                "User": "", "WorkingDir": "/"}})}


def _live_conda(monkeypatch, tmp_path: Path) -> Path:
    """A conda env on the host, its two capture calls answered by a fake."""
    conda_root = tmp_path / "conda"
    (conda_root / "envs" / ENV / "conda-meta").mkdir(parents=True)

    def host(cmd, *args, **kwargs):
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
    return conda_root


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


def _progress_lines(err: str) -> list[str]:
    return [line for line in err.splitlines()
            if line.startswith(progress.LINE_PREFIX)]


@workflow(purpose="register-env off a terminal prints a start and an end line "
                  "with its time for each phase, a heartbeat while the build "
                  "runs long, and keeps a log of those lines, each command wfc "
                  "ran and the build's whole output, whose path it prints at "
                  "the end; the registration records as it always has")
def test_phases_heartbeat_and_log_off_a_terminal(tmp_project, monkeypatch,
                                                 capsys, tmp_path_factory):
    口 = Step(step_num=1, name="A live conda env and a build that runs a while",
             purpose="The heartbeat interval is shortened; the faked build "
                     "streams its output to the sink, then runs past it")
    _live_conda(monkeypatch, tmp_path_factory.mktemp("host"))
    short_register_env_heartbeat(monkeypatch, 0.05)

    def build(build_dir, tag, labels=None, *, sink=None):
        for line in BUILD_LINES:
            sink(line)
        time.sleep(0.5)

    stub_docker_build(monkeypatch, build)
    before = set(_git_status(tmp_project).splitlines())

    口 = Step(step_num=2, name="Register the env", purpose="The production verb")
    rc = _register()
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    口 = Step(step_num=3, name="Each phase has a start and an end line with its time",
             purpose="capture and its host calls, stage, build, resolve, "
                     "record, commit; no push (no registry base)")
    lines = _progress_lines(captured.err)
    for name in ("capture", "conda info --base", "conda list --explicit",
                 "stage", "build", "resolve", "record", "commit"):
        assert any(re.search(rf"\b{re.escape(name)} started$", ln) for ln in lines), name
        assert any(re.search(rf"\b{re.escape(name)} done \(\d+\.\ds\)$", ln)
                   for ln in lines), name
    assert not any(" push " in ln for ln in lines)
    beats = [ln for ln in lines if "build still running" in ln]
    assert beats, lines
    build_done = next(i for i, ln in enumerate(lines)
                      if re.search(r"\bbuild done", ln))
    assert all(lines.index(b) < build_done for b in beats)

    口 = Step(step_num=4, name="The log holds the lines, the commands and the output",
             purpose="One log under .wfc/logs/register-env/, its path the "
                     "last line on stderr; nothing on stderr names a docker command")
    (log,) = _logs(tmp_project)
    text = log.read_text(encoding="utf-8")
    for line in lines:
        assert line.removeprefix(progress.LINE_PREFIX) in text
    assert "$ conda list --explicit --md5 --prefix" in text
    assert "$ conda info --base" in text
    tag = f"local/{ENV}:_wfc-build"
    assert f"$ docker image inspect {tag} --format '{{{{.Id}}}}'" in text
    assert "$ docker image inspect sha256:" in text and "--format '{{json .}}'" in text
    assert re.search(r"^\S+ \$ git -c core\.longpaths=true .*\bcommit\b", text,
                     re.MULTILINE), text
    for line in BUILD_LINES:
        assert f"| {line}" in text
    assert captured.err.rstrip().splitlines()[-1] == f"Log: {log}"
    assert "docker " not in captured.err

    口 = Step(step_num=5, name="The registration records as before",
             purpose="The record, its captures folder and the commit")
    record = json.loads(layout.env_manifest_path(tmp_project).read_text(
        encoding="utf-8"))["envs"][ENV]
    assert (layout.env_capture_dir(tmp_project, ENV, record["env_fingerprint"])
            / layout.CAPTURE_PACKAGES_FILENAME).exists()
    assert record["container"] in captured.out.splitlines()
    assert set(_git_status(tmp_project).splitlines()) <= before


@workflow(purpose="A failed build's error names the env, the exit status and "
                  "the log, without naming a docker command, and the log holds "
                  "the build's whole output, not only the error's last lines")
def test_a_failed_build_names_the_log_which_holds_the_whole_output(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    _live_conda(monkeypatch, tmp_path_factory.mktemp("host"))

    def build(build_dir, tag, labels=None, *, sink=None):
        for line in BUILD_LINES:
            sink(line)
        tail = "\n".join(BUILD_LINES[-40:])
        raise RuntimeError(f"docker build failed (exit 1):\n{tail}")

    stub_docker_build(monkeypatch, build)

    rc = _register()

    err = capsys.readouterr().err
    assert rc == 1
    (log,) = _logs(tmp_project)
    assert f"Log: {log}" in err
    assert f"The image build for env {ENV!r} failed (exit 1)" in err
    assert BUILD_LINES[-1] in err and BUILD_LINES[0] not in err
    assert "docker " not in err
    assert any(re.search(r"\bbuild failed \(\d+\.\ds\)$", ln)
               for ln in _progress_lines(err))
    text = log.read_text(encoding="utf-8")
    assert all(f"| {line}" in text for line in BUILD_LINES)
    assert not layout.env_manifest_path(tmp_project).exists() or ENV not in (
        json.loads(layout.env_manifest_path(tmp_project).read_text(
            encoding="utf-8")).get("envs", {}))


@workflow(purpose="Ctrl-C during the build ends register-env with one plain "
                  "line naming the log, no traceback, exit 130, no record and "
                  "no captures folder")
def test_ctrl_c_during_the_build_ends_with_one_line_and_records_nothing(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    from wfc.environments.docker import BuildInterruptedError

    _live_conda(monkeypatch, tmp_path_factory.mktemp("host"))
    stub_docker_build(monkeypatch, BuildInterruptedError(client_ended=True))

    rc = _register()

    err = capsys.readouterr().err
    assert rc == 130
    (log,) = _logs(tmp_project)
    others = [ln for ln in err.splitlines()
              if not ln.startswith(progress.LINE_PREFIX)]
    assert others == ["Stopped: the image build was stopped and nothing was "
                      f"recorded. (log: {log})"]
    assert "Traceback" not in err
    assert any(re.search(r"\bbuild stopped \(", ln) for ln in _progress_lines(err))
    manifest = layout.env_manifest_path(tmp_project)
    assert not manifest.exists() or ENV not in json.loads(
        manifest.read_text(encoding="utf-8")).get("envs", {})
    assert not layout.env_captures_dir(tmp_project, ENV).exists()


def test_dry_run_opens_no_log(tmp_project, capsys):
    from wfc.environments import verbs

    rc = verbs.register_env(ENV, backend="conda", from_path="x.lock",
                            dry_run=True, docker_gate=lambda: None)

    assert rc == 0, capsys.readouterr().err
    assert not layout.register_env_log_dir(tmp_project).exists()


def test_the_log_path_is_the_env_and_the_utc_time():
    root = Path("proj")
    when = datetime(2026, 11, 7, 15, 30, 12, tzinfo=UTC)

    path = layout.register_env_log_path(root, "tools", when)

    assert path == root / ".wfc" / "logs" / "register-env" / "tools-20261107T153012Z.log"
    assert layout.logs_relpath() == ".wfc/logs"


def test_the_log_folder_is_ignored_in_a_project_made_before_it(tmp_project):
    gitignore = tmp_project / ".gitignore"
    kept = [ln for ln in gitignore.read_text(encoding="utf-8").splitlines()
            if "logs" not in ln]
    gitignore.write_text("\n".join(kept) + "\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "an older .gitignore"],
                   cwd=tmp_project, check=True, capture_output=True)
    before = _git_status(tmp_project)

    with progress.registration_log(tmp_project, ENV) as reporter:
        reporter.note("a line")

    assert reporter.log_path is not None and reporter.log_path.exists()
    assert _git_status(tmp_project) == before


@pytest.mark.parametrize(("seconds", "shown"), [
    (4.25, "4.2s"), (187, "3m 07s"), (3725, "1h 02m 05s")])
def test_phase_times_read_as_seconds_minutes_and_hours(seconds, shown):
    assert progress.format_elapsed(seconds) == shown
