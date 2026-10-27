"""register-env pushes the image to the project's registry before it commits.

With ``[registry] url`` set, a registration tags the image it built (or a byo
``local/`` image) ``<base>/<env>:fp-<first 12 hex of the env fingerprint>``,
pushes it, and records the digest the registry reported in the image's
captures folder as ``pushed.json``, committed with the record. A failed push
records nothing and commits nothing. ``--no-push``, or no base, keeps the
image on this machine and says so. A byo registry image is never copied.
Builds in a project whose git remote is on GitHub carry the repository's
source label, which moves no fingerprint.

Projects come from the shared ``tmp_project`` fixture (``wfc init``); the
base is set through production ``set_registry_url`` and the git remote with
real ``git remote``. Docker is faked at the ``wfc.environments.docker``
wrappers (``tests.fixtures.fakes``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_repo_digest,
    stub_docker_tag,
    stub_readiness_probes,
)

BASE = "ghcr.io/lab/tools-project"
IMAGE_ID = "sha256:" + "c" * 64
SOURCE_LABEL = "org.opencontainers.image.source"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root* by relative path, with its bytes."""
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _set_base(project: Path) -> None:
    """Set ``[registry] url`` through production and commit the config."""
    from wfc.persistence import set_registry_url

    config = set_registry_url(project, BASE)
    _git(project, "add", "--", config.relative_to(project).as_posix())
    _git(project, "commit", "-q", "-m", "set the registry base")


class _Docker:
    """Every Docker call a registration makes, faked and recorded in order."""

    def __init__(self, monkeypatch, project: Path, *, layer: str = "1",
                 push=None) -> None:
        self.project = project
        self.calls: list[tuple] = []
        self.heads_at_push: list[str] = []
        self.labels: list[dict | None] = []
        self._push_answer = push
        stub_docker_build(monkeypatch, self._build)
        stub_docker_pull(monkeypatch)
        stub_docker_image_inspect(monkeypatch, IMAGE_ID)
        stub_docker_repo_digest(monkeypatch,
                                lambda ref, repository: "sha256:" + "a" * 64)
        stub_docker_image_content(monkeypatch, {
            "layers": ["sha256:" + layer * 64], "env": ["PATH=/usr/bin"],
            "user": "", "workdir": "/"})
        stub_docker_tag(monkeypatch, self._tag)
        stub_docker_push(monkeypatch, self._push)

    def _build(self, dockerfile_dir, tag, **kwargs):
        self.calls.append(("build", tag))
        self.labels.append(kwargs.get("labels"))

    def _tag(self, source, target):
        self.calls.append(("tag", source, target))

    def _push(self, ref):
        self.calls.append(("push", ref))
        self.heads_at_push.append(_git(self.project, "rev-parse", "HEAD"))
        if isinstance(self._push_answer, BaseException):
            raise self._push_answer
        return FAKE_PUSHED_DIGEST

    def pushes(self) -> list[str]:
        return [call[1] for call in self.calls if call[0] == "push"]


def _lock(tmp_path_factory) -> Path:
    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    return lock


def _cli(*argv: str) -> int:
    from wfc.cli import cli_main

    return cli_main(["register-env", *argv])


def _record(project: Path, env: str) -> dict:
    return json.loads((project / ".wfc" / "envs.json").read_text(
        encoding="utf-8"))["envs"][env]


def _pushed(project: Path, env: str, fingerprint: str) -> Path:
    return project / ".wfc" / "captures" / env / fingerprint / "pushed.json"


@workflow(purpose="With a registry base set, register-env pushes the built "
                  "image before it commits and commits pushed.json with the "
                  "record; a second registration whose push fails records "
                  "nothing, leaves no captures folder and no commit, and names "
                  "the registry, `docker login <host>` and --no-push")
def test_register_pushes_before_it_commits_and_a_failed_push_records_nothing(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")

    口 = Step(step_num=1, name="Set the project's registry base",
             purpose="[registry] url through set_registry_url, committed")
    _set_base(tmp_project)
    head = _git(tmp_project, "rev-parse", "HEAD")

    口 = Step(step_num=2, name="Register a pixi env",
             purpose="The image is built, tagged and pushed while HEAD is still "
                     "the commit before the registration")
    docker = _Docker(monkeypatch, tmp_project)
    assert _cli("tools", "--from", str(lock), "--backend", "pixi") == 0
    assert [call[0] for call in docker.calls] == ["build", "tag", "push"]
    assert docker.heads_at_push == [head]

    口 = Step(step_num=3, name="The commit records where the image was pushed",
             purpose="pushed.json holds the registry's digest and the fp tag, "
                     "and is in the register commit")
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    pushed = json.loads(_pushed(tmp_project, "tools", fingerprint).read_text(
        encoding="utf-8"))
    assert pushed["digest"] == FAKE_PUSHED_DIGEST
    assert pushed["tag"] == f"fp-{fingerprint[:12]}"
    committed = set(_git(tmp_project, "show", "--name-only", "--format=",
                         "HEAD").split())
    assert f".wfc/captures/tools/{fingerprint}/pushed.json" in committed
    assert f"pushed to {BASE}" in capsys.readouterr().out

    口 = Step(step_num=4, name="A second registration's push fails",
             purpose="Docker refuses the push; nothing is recorded or committed")
    manifest = tmp_project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(),
              _tree_bytes(tmp_project / ".wfc" / "captures"),
              _git(tmp_project, "rev-parse", "HEAD"))
    _Docker(monkeypatch, tmp_project, layer="2",
            push=RuntimeError("docker push failed: denied: permission_denied"))
    assert _cli("other", "--from", str(lock), "--backend", "pixi") == 1
    err = capsys.readouterr().err
    assert BASE in err and "--no-push" in err and "denied" in err
    assert "Run `docker login ghcr.io` and register again" in err
    assert (manifest.read_bytes(),
            _tree_bytes(tmp_project / ".wfc" / "captures"),
            _git(tmp_project, "rev-parse", "HEAD")) == before
    assert not (tmp_project / ".wfc" / "captures" / "other").exists()


@workflow(purpose="With no registry base set, or with --no-push, register-env "
                  "pushes nothing, writes no pushed.json, and says the image "
                  "stays on this machine")
def test_no_base_or_no_push_keeps_the_image_on_this_machine(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")

    no_base = _Docker(monkeypatch, tmp_project)
    assert _cli("tools", "--from", str(lock), "--backend", "pixi") == 0
    out = capsys.readouterr().out
    assert "stays on this machine" in out and "wfc push-env tools" in out

    _set_base(tmp_project)
    no_push = _Docker(monkeypatch, tmp_project, layer="2")
    assert _cli("other", "--from", str(lock), "--backend", "pixi",
                "--no-push") == 0
    out = capsys.readouterr().out
    assert "stays on this machine" in out and BASE in out

    for docker in (no_base, no_push):
        assert [call[0] for call in docker.calls] == ["build"]
    for env in ("tools", "other"):
        fingerprint = _record(tmp_project, env)["env_fingerprint"]
        assert not _pushed(tmp_project, env, fingerprint).exists()


@workflow(purpose="Every image register-env pushes, built or a byo local/ "
                  "image, is tagged from its image ID as <base>/<env>:fp-<first "
                  "12 hex of its env fingerprint>, and that tag is what is pushed")
@pytest.mark.parametrize(("backend", "argv"), [
    ("pixi", ("--backend", "pixi")),
    ("byo", ("--backend", "byo", "--image", "docker://local/lab-tool:1.0")),
])
def test_every_pushed_image_carries_its_fingerprint_tag(
        tmp_project, monkeypatch, tmp_path_factory, backend, argv):
    stub_readiness_probes(monkeypatch, docker="ok")
    _set_base(tmp_project)
    docker = _Docker(monkeypatch, tmp_project)
    extra = (("--from", str(_lock(tmp_path_factory)))
             if backend == "pixi" else ())

    assert _cli("tools", *extra, *argv) == 0

    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    target = f"{BASE}/tools:fp-{fingerprint[:12]}"
    assert re.fullmatch(r"fp-[0-9a-f]{12}", target.rsplit(":", 1)[1])
    tags = [call for call in docker.calls if call[0] == "tag"]
    assert tags == [("tag", IMAGE_ID, target)]
    assert docker.pushes() == [target]


@workflow(purpose="A byo image already in a registry is not copied to the "
                  "project's registry: no tag, no push, no pushed.json, and "
                  "the output says it stays in its own registry")
def test_a_byo_registry_image_is_not_pushed(tmp_project, monkeypatch, capsys):
    stub_readiness_probes(monkeypatch, docker="ok")
    _set_base(tmp_project)
    docker = _Docker(monkeypatch, tmp_project)

    assert _cli("tools", "--backend", "byo",
                "--image", "docker://ghcr.io/other/tool:1.0") == 0

    assert [call[0] for call in docker.calls
            if call[0] in ("tag", "push")] == []
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    assert not _pushed(tmp_project, "tools", fingerprint).exists()
    assert "its own registry" in capsys.readouterr().out


@workflow(purpose="Builds in a project whose git remote is on GitHub carry the "
                  "repository's source label, the same URL for an SSH and an "
                  "HTTPS remote; with a remote not on GitHub, or no remote, "
                  "there is no label; the env fingerprint and the rendered "
                  "Dockerfile are the same in all four")
def test_the_source_label_follows_the_github_remote_and_moves_no_fingerprint(
        tmp_project, monkeypatch, tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")
    docker = _Docker(monkeypatch, tmp_project)
    dockerfile = tmp_project / ".wfc" / "build" / "tools" / "Dockerfile"
    seen = []

    remotes = [("add", "git@github.com:Lab/Tools.git"),
               ("set-url", "https://github.com/Lab/Tools"),
               ("set-url", "https://gitlab.com/Lab/Tools.git"),
               ("remove", None)]
    for verb, url in remotes:
        _git(tmp_project, "remote", verb, "origin", *([url] if url else []))
        force = ("--force",) if seen else ()
        assert _cli("tools", "--from", str(lock), "--backend", "pixi",
                    "--no-push", *force) == 0
        seen.append((_record(tmp_project, "tools")["env_fingerprint"],
                     dockerfile.read_bytes()))

    url = "https://github.com/Lab/Tools"
    assert docker.labels == [{SOURCE_LABEL: url}, {SOURCE_LABEL: url}, None, None]
    assert seen[0] == seen[1] == seen[2] == seen[3]


@workflow(purpose="A failed push over an image whose captures folder was "
                  "already there keeps the folder byte for byte; a later push "
                  "adds only pushed.json to it, in the register commit; and "
                  "re-registering an image that holds pushed.json pushes "
                  "nothing")
def test_a_push_into_an_existing_folder_adds_only_the_push_record(
        tmp_project, monkeypatch, tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")
    register = ("tools", "--from", str(lock), "--backend", "pixi")

    _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--no-push") == 0
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    folder = tmp_project / ".wfc" / "captures" / "tools" / fingerprint
    kept = _tree_bytes(folder)
    manifest = tmp_project / ".wfc" / "envs.json"
    _set_base(tmp_project)
    before = (manifest.read_bytes(), _git(tmp_project, "rev-parse", "HEAD"))

    _Docker(monkeypatch, tmp_project, push=RuntimeError("unauthorized"))
    assert _cli(*register, "--force") == 1
    assert _tree_bytes(folder) == kept
    assert (manifest.read_bytes(), _git(tmp_project, "rev-parse", "HEAD")) == before

    _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--force") == 0
    after = _tree_bytes(folder)
    assert set(after) == {*kept, "pushed.json"}
    assert {k: v for k, v in after.items() if k != "pushed.json"} == kept
    committed = set(_git(tmp_project, "show", "--name-only", "--format=",
                         "HEAD").split())
    assert f".wfc/captures/tools/{fingerprint}/pushed.json" in committed

    docker = _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--force") == 0
    assert docker.pushes() == []
    assert _tree_bytes(folder) == after


def test_a_push_record_that_cannot_be_read_undoes_the_record_step(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """A broken pushed.json refuses the push and leaves envs.json/image.json."""
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")
    register = ("tools", "--from", str(lock), "--backend", "pixi")
    _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--no-push") == 0
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    _set_base(tmp_project)
    broken = _pushed(tmp_project, "tools", fingerprint)
    broken.write_text("{not json", encoding="utf-8", newline="\n")
    folder = broken.parent
    manifest = tmp_project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(folder),
              _git(tmp_project, "rev-parse", "HEAD"))
    capsys.readouterr()

    docker = _Docker(monkeypatch, tmp_project, layer="1")
    assert _cli(*register, "--force") == 1

    assert (manifest.read_bytes(), _tree_bytes(folder),
            _git(tmp_project, "rev-parse", "HEAD")) == before
    assert docker.pushes() == []
    assert "push record cannot be read" in capsys.readouterr().err


def test_a_refused_commit_removes_the_push_record_it_added(
        tmp_project, monkeypatch, tmp_path_factory):
    lock = _lock(tmp_path_factory)
    stub_readiness_probes(monkeypatch, docker="ok")
    register = ("tools", "--from", str(lock), "--backend", "pixi")
    _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--no-push") == 0
    fingerprint = _record(tmp_project, "tools")["env_fingerprint"]
    folder = tmp_project / ".wfc" / "captures" / "tools" / fingerprint
    kept = _tree_bytes(folder)
    _set_base(tmp_project)
    hook = tmp_project / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)

    _Docker(monkeypatch, tmp_project)
    assert _cli(*register, "--force") == 1

    assert _tree_bytes(folder) == kept
