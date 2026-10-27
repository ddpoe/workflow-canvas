"""An image pushed from a project with a GitHub remote carries the source label.

GHCR connects a pushed package to the repository its image's
``org.opencontainers.image.source`` label names. ``push_image`` gives every
image it pushes from a project whose git remote is on GitHub that label: an
image built without it (a byo ``local/`` image here) is pushed as a labelled
copy whose content renders the same env fingerprint, and a copy that renders
another one is refused before anything is pushed. A remote not on GitHub, or
an image that already carries the label, is pushed as it is.

Projects come from ``tmp_project`` (``wfc init``); the env is registered and
pushed through the CLI (``register-env --backend byo``, ``push-env``), the
git remote set with real ``git remote``. Docker is faked at the
``wfc.environments.docker`` wrappers by a small daemon that holds images by
ID with their content and labels, names that point at IDs, and builds a
context's ``FROM`` image again with the labels it is given.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_image_labels,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_repo_digest,
    stub_docker_tag,
    stub_readiness_probes,
)
from wfc.environments.docker import ImageNotFoundError
from wfc.environments.fingerprint import content_fingerprint
from wfc.environments.registry import SOURCE_LABEL

ENV = "tools"
BYO = "local/tools:1"
SOURCE_ID = "sha256:" + "a" * 64
CONTENT = {"layers": ["sha256:" + "1" * 64], "env": ["PATH=/usr/bin"],
           "user": "", "workdir": "/"}
GITHUB_URL = "https://github.com/Lab/Tools"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _wfc(*argv: str) -> int:
    from wfc.cli import cli_main

    return cli_main(list(argv))


def _digest_of(image_id: str) -> str:
    """The registry digest the fake registry reports for one image ID."""
    return "sha256:" + hashlib.sha256(image_id.encode()).hexdigest()


class _Daemon:
    """Images by ID (content and labels), and the names that point at them.

    ``build`` reads its context's Dockerfile: each line after ``FROM`` adds a
    layer, as Docker's would, and the labels asked for are added to the
    ``FROM`` image's own. *copy_content*, when given, is the content every
    build reports instead (a daemon whose copy is not the source).
    """

    def __init__(self, monkeypatch, *, labels: dict[str, str] | None = None,
                 copy_content: dict | None = None) -> None:
        self.images = {SOURCE_ID: {"content": CONTENT, "labels": dict(labels or {})}}
        self.names = {BYO: SOURCE_ID}
        self.copy_content = copy_content
        self.builds: list[tuple[list[str], dict[str, str]]] = []
        self.pushes: list[tuple[str, str]] = []
        stub_readiness_probes(monkeypatch, docker="ok")
        stub_docker_pull(monkeypatch)
        stub_docker_repo_digest(monkeypatch, lambda ref, repository: _digest_of(
            self.image_id(ref)))
        stub_docker_image_inspect(monkeypatch, self.image_id)
        stub_docker_image_content(monkeypatch,
                                  lambda ref: self.images[self.image_id(ref)]["content"])
        stub_docker_image_labels(monkeypatch, lambda ref: dict(
            self.images[self.image_id(ref)]["labels"]))
        stub_docker_tag(monkeypatch, self._tag)
        stub_docker_build(monkeypatch, self._build)
        stub_docker_push(monkeypatch, self._push)

    def image_id(self, ref: str) -> str:
        if ref in self.images:
            return ref
        if ref in self.names:
            return self.names[ref]
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    def _tag(self, source: str, target: str) -> None:
        self.names[target] = self.image_id(source)

    def _build(self, dockerfile_dir, tag: str, labels=None) -> None:
        lines = (Path(dockerfile_dir) / "Dockerfile").read_text(
            encoding="utf-8").splitlines()
        assert lines and lines[0].startswith("FROM "), lines
        base = self.images[self.image_id(lines[0].split()[1])]
        content = self.copy_content or {
            **base["content"],
            "layers": base["content"]["layers"] + [f"layer:{line}" for line in lines[1:]]}
        image_id = "sha256:" + hashlib.sha256(
            json.dumps([content, base["labels"], labels], sort_keys=True).encode()
        ).hexdigest()
        self.images[image_id] = {"content": content,
                                 "labels": {**base["labels"], **(labels or {})}}
        self.names[tag] = image_id
        self.builds.append((lines, dict(labels or {})))

    def _push(self, ref: str) -> str:
        image_id = self.image_id(ref)
        self.pushes.append((ref, image_id))
        return _digest_of(image_id)


def _register(project: Path) -> str:
    """Register the byo image with no base; return its env fingerprint."""
    assert _wfc("register-env", ENV, "--backend", "byo", "--image",
                f"docker://{BYO}") == 0
    record = json.loads((project / ".wfc" / "envs.json").read_text(
        encoding="utf-8"))["envs"][ENV]
    return record["env_fingerprint"]


def _pushed(project: Path, fingerprint: str) -> Path:
    return project / ".wfc" / "captures" / ENV / fingerprint / "pushed.json"


@pytest.mark.parametrize("labels", [
    {},
    {SOURCE_LABEL: "https://github.com/Other/Repo"},
], ids=["no-label", "another-repository"])
@workflow(purpose="With a GitHub remote, push-env of a byo local/ image that "
                  "carries no source label, or one naming another repository, "
                  "pushes a copy carrying the project repository's label, "
                  "whose content renders the recorded env fingerprint; "
                  "pushed.json holds the copy's digest, and envs.json and the "
                  "captures' image.json are byte for byte what they were")
def test_an_unlabelled_image_is_pushed_as_a_labelled_copy_with_its_fingerprint(
        tmp_project, monkeypatch, labels):
    """An image without the repository's label goes as a labelled copy with the recorded fingerprint."""
    daemon = _Daemon(monkeypatch, labels=labels)

    口 = Step(step_num=1, name="A byo image without the repository's label, a GitHub remote",
             purpose="Registered with no base, so it stays on this machine")
    _git(tmp_project, "remote", "add", "origin", "git@github.com:Lab/Tools.git")
    fingerprint = _register(tmp_project)
    assert fingerprint == content_fingerprint(CONTENT)
    manifest = (tmp_project / ".wfc" / "envs.json").read_bytes()
    image_json = _pushed(tmp_project, fingerprint).with_name("image.json")
    image_record = image_json.read_bytes()

    口 = Step(step_num=2, name="push-env --yes",
             purpose="The base comes from the remote; one image is pushed")
    assert _wfc("push-env", ENV, "--yes") == 0

    口 = Step(step_num=3, name="The pushed image is the labelled copy",
             purpose="Not the registered image ID: the same content, the label added")
    target = f"ghcr.io/lab/tools/{ENV}:fp-{fingerprint[:12]}"
    ((ref, pushed_id),) = daemon.pushes
    assert ref == target
    assert pushed_id != SOURCE_ID
    assert daemon.images[pushed_id]["labels"] == {SOURCE_LABEL: GITHUB_URL}
    assert content_fingerprint(daemon.images[pushed_id]["content"]) == fingerprint

    口 = Step(step_num=4, name="The record names the copy's digest",
             purpose="pushed.json holds what the registry stored; envs.json "
                     "and image.json are unchanged")
    record = json.loads(_pushed(tmp_project, fingerprint).read_text(encoding="utf-8"))
    assert record["digest"] == _digest_of(pushed_id)
    assert (tmp_project / ".wfc" / "envs.json").read_bytes() == manifest
    assert image_json.read_bytes() == image_record


@workflow(purpose="With a GitHub remote, a labelled copy whose content "
                  "renders another env fingerprint is refused naming both "
                  "fingerprints: nothing is pushed and no pushed.json is "
                  "written")
def test_a_labelled_copy_with_other_content_is_refused_and_nothing_is_recorded(
        tmp_project, monkeypatch, capsys):
    """A copy whose content differs is refused before any push or record."""
    other = {**CONTENT, "env": ["PATH=/usr/bin", "EXTRA=1"]}
    daemon = _Daemon(monkeypatch, copy_content=other)

    口 = Step(step_num=1, name="A byo image, no label, a GitHub remote",
             purpose="The daemon's copy of it will report other content")
    _git(tmp_project, "remote", "add", "origin", "git@github.com:Lab/Tools.git")
    fingerprint = _register(tmp_project)
    head = _git(tmp_project, "rev-parse", "HEAD")

    口 = Step(step_num=2, name="push-env --yes is refused",
             purpose="Exit 1; the message names the copy's fingerprint and the recorded one")
    capsys.readouterr()
    assert _wfc("push-env", ENV, "--yes") == 1
    err = capsys.readouterr().err
    assert content_fingerprint(other) in err and fingerprint in err, err

    口 = Step(step_num=3, name="Nothing pushed, nothing recorded",
             purpose="No push, no pushed.json, no commit")
    assert daemon.pushes == []
    assert not _pushed(tmp_project, fingerprint).exists()
    assert _git(tmp_project, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("remote, base, labels", [
    ("https://gitlab.com/Lab/Tools.git", "ghcr.io/lab/tools", {}),
    ("git@github.com:Lab/Tools.git", None, {SOURCE_LABEL: GITHUB_URL}),
], ids=["remote-not-on-github", "already-labelled"])
@workflow(purpose="With a remote not on GitHub, or an image that already "
                  "carries the repository's source label, push-env pushes the "
                  "registered image itself and builds no copy")
def test_an_image_needing_no_label_is_pushed_as_it_is(
        tmp_project, monkeypatch, remote, base, labels):
    """No GitHub remote, or the label already there: the image itself is pushed."""
    daemon = _Daemon(monkeypatch, labels=labels)

    口 = Step(step_num=1, name="A byo image and the remote",
             purpose="Registered with no base")
    _git(tmp_project, "remote", "add", "origin", remote)
    fingerprint = _register(tmp_project)

    口 = Step(step_num=2, name="push-env --yes",
             purpose="A remote not on GitHub proposes no base, so it is given")
    registry = ("--registry", base) if base else ()
    assert _wfc("push-env", ENV, *registry, "--yes") == 0

    口 = Step(step_num=3, name="The registered image is pushed",
             purpose="No build; the pushed ref names the registered image ID")
    assert daemon.builds == []
    ((_ref, pushed_id),) = daemon.pushes
    assert pushed_id == SOURCE_ID
    record = json.loads(_pushed(tmp_project, fingerprint).read_text(encoding="utf-8"))
    assert record["digest"] == _digest_of(SOURCE_ID)
