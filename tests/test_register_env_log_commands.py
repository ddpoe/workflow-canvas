"""register-env's log holds every Docker and git command wfc ran, as it ran.

A conda env on a Windows host is registered through the production verb
(``wfc.environments.verbs.register_env``) with a registry base set, so the
registration runs the solve build, the copy-out, the explicit-list build, a
push and the commit. Docker is faked at its subprocess edges
(``subprocess.run`` for every docker call but the build, ``subprocess.Popen``
for the build); git runs for real, its ``Popen`` spied, so each command the
log should hold is the one handed to the process.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
from pathlib import Path, PurePosixPath

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_conda_list_explicit,
    fake_conda_solve_capture,
    fake_pip_freeze,
    fake_subprocess_run,
    real_subprocess_run,
    record_spawns,
    stub_host_conda_platform,
)
from wfc import layout
from wfc.environments import progress

ENV = "imaging"
BASE = "ghcr.io/lab/tools-project"
IMAGE_ID = "sha256:" + "c" * 64
PUSHED_DIGEST = "sha256:" + "d" * 64
SOURCE_LABEL = "org.opencontainers.image.source"
LINUX_LIST = ("@EXPLICIT\n"
              "https://conda.anaconda.org/conda-forge/linux-64/"
              "python-3.11.0-h.conda#0a\n")
FIXTURES = Path(__file__).parent / "fixtures" / "conda_solve"

class _Edges:
    """The Docker daemon at its subprocess edges, and git's spawns, recorded."""

    def __init__(self, monkeypatch, conda_root: Path) -> None:
        self._runs: list[list[str]] = []
        self._conda_root = conda_root
        fake_subprocess_run(monkeypatch, self._run)
        self._spawns = record_spawns(monkeypatch)

    @property
    def docker(self) -> list[list[str]]:
        return [*self._runs, *self._spawns.docker_builds]

    @property
    def git(self) -> list[list[str]]:
        return self._spawns.git

    def _run(self, cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        if argv[:3] == ["conda", "info", "--base"]:
            return canned_process(stdout=f"{self._conda_root}\n")(cmd)
        if argv[:1] != ["docker"]:
            return real_subprocess_run(cmd, *args, **kwargs)
        self._runs.append(argv)
        return self._answer(argv)

    def _answer(self, argv: list[str]) -> subprocess.CompletedProcess:
        ok = canned_process()
        if argv[1:3] == ["image", "inspect"]:
            fmt = argv[argv.index("--format") + 1]
            if fmt == "{{.Id}}":
                return canned_process(stdout=IMAGE_ID + "\n")(argv)
            if fmt == "{{json .}}":
                return canned_process(stdout=json.dumps({
                    "RootFS": {"Layers": ["sha256:" + "1" * 64]},
                    "Config": {"Env": ["PATH=/usr/bin"], "User": "",
                               "WorkingDir": "/", "Labels": {}}}))(argv)
            return canned_process(stdout="[]")(argv)
        if argv[1] == "cp":
            source = PurePosixPath(argv[2].split(":", 1)[1])
            (Path(argv[3]) / source.name).write_text(LINUX_LIST, encoding="utf-8",
                                                     newline="\n")
            return ok(argv)
        if argv[1] in ("create", "rm", "tag"):
            return ok(argv)
        if argv[1] == "push":
            return canned_process(
                stdout=f"{argv[2]}: digest: {PUSHED_DIGEST} size: 1\n")(argv)
        if argv[1:3] == ["manifest", "inspect"]:
            return canned_process(returncode=1, stderr="manifest unknown")(argv)
        raise AssertionError(f"unexpected docker call {argv}")


def _conda_env(tmp_path: Path) -> Path:
    """A conda env under a conda root, its interpreter in both layouts."""
    conda_root = tmp_path / "conda"
    env_dir = conda_root / "envs" / ENV
    (env_dir / "conda-meta").mkdir(parents=True)
    (env_dir / "bin").mkdir()
    (env_dir / "python.exe").write_bytes(b"")
    (env_dir / "bin" / "python").write_bytes(b"")
    return conda_root


def _set_base(project: Path) -> None:
    """Set ``[registry] url`` and a GitHub remote; commit the config."""
    from wfc.persistence import set_registry_url

    config = set_registry_url(project, BASE)
    for args in (["remote", "add", "origin",
                  "https://github.com/lab/tools-project.git"],
                 ["add", "--", config.relative_to(project).as_posix()],
                 ["commit", "-q", "-m", "set the registry base"]):
        subprocess.run(["git", *args], cwd=project, check=True,
                       capture_output=True)


def _logged(argv: list[str]) -> str:
    return "$ " + shlex.join(argv)


@workflow(purpose="register-env's log holds every command wfc ran exactly as "
                  "it was handed to the process: the solve build and the "
                  "explicit-list build with --provenance=false, each image "
                  "inspect with its --format, the copy-out's create, cp and "
                  "rm, the push's tag and push, and the commit's git calls; "
                  "the build's environment is not logged and stderr names no "
                  "docker command")
def test_the_log_holds_each_command_as_it_was_run(tmp_project, monkeypatch,
                                                  capsys, tmp_path_factory):
    from wfc.environments import verbs

    口 = Step(step_num=1, name="A Windows conda env and a registry base",
             purpose="The host solves for linux-64; the base makes the "
                     "registration push")
    conda_root = _conda_env(tmp_path_factory.mktemp("host"))
    stub_host_conda_platform(monkeypatch, "win-64")
    fake_conda_list_explicit(monkeypatch,
                             (FIXTURES / "win-64-explicit-list.txt").read_text(
                                 encoding="utf-8"))
    fake_conda_solve_capture(
        monkeypatch,
        from_history=(FIXTURES / "from-history.yml").read_text(encoding="utf-8"),
        list_json=json.loads((FIXTURES / "conda-list.json").read_text(
            encoding="utf-8")))
    fake_pip_freeze(monkeypatch, "")
    _set_base(tmp_project)
    edges = _Edges(monkeypatch, conda_root)

    口 = Step(step_num=2, name="Register the env", purpose="The production verb")
    rc = verbs.register_env(ENV, spec=f"conda:{ENV}", docker_gate=lambda: None)
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    口 = Step(step_num=3, name="Every Docker command is in the log as it ran",
             purpose="Both builds, the inspects, the copy-out, the tag and the push")
    (log,) = sorted(layout.register_env_log_dir(tmp_project).glob(f"{ENV}-*.log"))
    text = log.read_text(encoding="utf-8")
    for argv in edges.docker:
        assert _logged(argv) in text, argv
    builds = [a for a in edges.docker if a[:2] == ["docker", "build"]]
    assert builds and all("--provenance=false" in a for a in builds)
    assert f"{SOURCE_LABEL}=https://github.com/lab/tools-project" in builds[0]
    verbs_seen = [a[1] if a[1] not in ("image",) else
                  f"inspect {a[a.index('--format') + 1]}" for a in edges.docker]
    for expected in ("create", "cp", "rm", "tag", "push", "inspect {{.Id}}",
                     "inspect {{json .}}"):
        assert expected in verbs_seen, (expected, verbs_seen)
    assert "DOCKER_BUILDKIT" not in text

    口 = Step(step_num=4, name="The git commands of the registration are in the log",
             purpose="Every git command wfc spawned, the commit's among them")
    assert any("commit" in argv for argv in edges.git), edges.git
    for argv in edges.git:
        assert _logged(argv) in text, argv

    口 = Step(step_num=5, name="stderr is unchanged",
             purpose="The log is a file: no line on stderr names a docker command")
    assert "docker " not in captured.err


def test_no_command_is_logged_outside_the_registration_or_off_its_thread(
        tmp_path, monkeypatch):
    from wfc import version
    from wfc.environments import docker

    fake_subprocess_run(monkeypatch, canned_process(), only="docker")

    with progress.registration_log(tmp_path, "x") as reporter:
        log = reporter.log_path
        worker = threading.Thread(target=lambda: (docker.tag("off", "thread"),
                                                  version.git_toplevel(tmp_path)))
        worker.start()
        worker.join()
        docker.tag("on", "thread")
    docker.tag("after", "close")

    text = log.read_text(encoding="utf-8")
    assert "$ docker tag on thread" in text
    assert "off" not in text and "after" not in text
    assert "git" not in text
