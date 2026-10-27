"""Environments' registry rules: the base, the names, the probe, image state.

- A GitHub remote in any of its spellings (scp-like SSH, ``ssh://``, HTTPS,
  with or without ``.git``) proposes one base, ``ghcr.io/<owner>/<repo>``
  lowercased, and one source-label URL; any other host proposes none.
- A pushed image is tagged ``fp-<first 12 hex>`` and pulled by
  ``<base>/<env>@<digest>``.
- ``probe_registry`` classifies Docker's answer to one manifest read; every
  failure names the registry, ``docker login <host>`` and ``wfc share``.
- ``image_state`` reads the captures folder and the daemon, never the
  registry.

Images are registered through production ``register`` with the Docker
boundary faked (``tests.fixtures.fakes``); the registry read is faked at
``wfc.environments.docker.read_manifest``.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    refuse_docker,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_read_manifest,
    stub_docker_repo_digest,
)

_OWNERS = ["Lab", "lab", "My-Org", "a1"]
_REPOS = ["Project", "my.repo", "under_score", "r-2"]
_FORMS = [
    "git@github.com:{o}/{r}.git",
    "git@github.com:{o}/{r}",
    "ssh://git@github.com/{o}/{r}.git",
    "ssh://git@github.com:22/{o}/{r}",
    "ssh://git@ssh.github.com:443/{o}/{r}.git",
    "https://github.com/{o}/{r}.git",
    "https://github.com/{o}/{r}",
    "https://github.com/{o}/{r}/",
    "https://user@github.com/{o}/{r}.git",
    "HTTPS://GitHub.com/{o}/{r}",
]


@pytest.mark.parametrize(("owner", "repo"), list(itertools.product(_OWNERS, _REPOS)))
def test_every_spelling_of_a_github_remote_proposes_one_base(owner, repo):
    """SSH, ssh:// and HTTPS, with or without .git, agree on the base and
    the label URL; the base is lowercased."""
    from wfc.environments import github_repository

    found = {github_repository(form.format(o=owner, r=repo)) for form in _FORMS}

    assert len(found) == 1
    (repository,) = found
    assert repository is not None
    assert repository.base == f"ghcr.io/{owner}/{repo}".lower()
    assert repository.base == repository.base.lower()
    assert repository.source_url == f"https://github.com/{owner}/{repo}"


@pytest.mark.parametrize("remote", [
    None,
    "",
    "git@gitlab.com:lab/project.git",
    "https://gitlab.com/lab/project",
    "https://github.example.com/lab/project.git",
    "https://github.com/lab",
    "https://github.com/lab/project/tree/main",
    "file:///srv/git/project.git",
    "/srv/git/project.git",
    "C:/repos/project",
])
def test_a_remote_not_on_github_proposes_nothing(remote):
    from wfc.environments import github_repository

    assert github_repository(remote) is None


def test_the_project_remote_is_read_through_git(tmp_project):
    import subprocess

    from wfc.environments import project_github_repository

    assert project_github_repository(tmp_project) is None
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Lab/Project.git"],
                   cwd=tmp_project, check=True, capture_output=True)

    repository = project_github_repository(tmp_project)

    assert repository is not None and repository.base == "ghcr.io/lab/project"


def test_a_pushed_image_is_named_by_its_fingerprint_and_digest():
    from wfc.environments import push_tag, push_target, registry_host, registry_ref

    fingerprint = "0123456789abcdef0123456789abcdef"

    assert push_tag(fingerprint) == "fp-0123456789ab"
    assert push_target("ghcr.io/lab/project", "tools", fingerprint) == \
        "ghcr.io/lab/project/tools:fp-0123456789ab"
    assert registry_ref("ghcr.io/lab/project/", "tools", FAKE_PUSHED_DIGEST) == \
        f"ghcr.io/lab/project/tools@{FAKE_PUSHED_DIGEST}"
    assert registry_host("ghcr.io/lab/project") == "ghcr.io"
    with pytest.raises(ValueError):
        push_tag("latest")
    with pytest.raises(ValueError):
        registry_ref("ghcr.io/lab/project", "tools", "latest")


@pytest.mark.parametrize(("state", "ok"), [
    ("found", True),
    ("missing", True),
    ("unauthorized", False),
    ("unreachable", False),
    ("error", False),
])
def test_the_registry_probe_names_the_registry_login_and_share(monkeypatch, state, ok):
    """Found or no such manifest: the registry answered and took Docker's
    credentials. Every failure names the registry, `docker login <host>`
    and `wfc share`, and keeps Docker's own words."""
    from wfc.environments import probe_registry
    from wfc.environments.docker import RegistryRead

    asked: list[str] = []
    detail = {"found": "{}", "missing": "manifest unknown",
              "unauthorized": "unauthorized: authentication required",
              "unreachable": "dial tcp: lookup ghcr.io: no such host",
              "error": "docker exploded"}[state]

    def answer(ref):
        asked.append(ref)
        return RegistryRead(ref, state, detail)

    stub_docker_read_manifest(monkeypatch, answer)

    passed, reason = probe_registry("ghcr.io/lab/project")

    assert passed is ok
    assert asked == ["ghcr.io/lab/project/wfc-probe:fp-000000000000"]
    if ok:
        assert reason == ""
    else:
        assert "ghcr.io/lab/project" in reason
        assert "`docker login ghcr.io`" in reason
        assert "`wfc share`" in reason
        assert detail in reason
    if state == "unauthorized":
        assert "logged in" in reason


def test_the_probe_reads_the_ref_it_is_given(monkeypatch):
    from wfc.environments import probe_registry
    from wfc.environments.docker import RegistryRead

    asked: list[str] = []

    def answer(ref):
        asked.append(ref)
        return RegistryRead(ref, "found", "{}")

    stub_docker_read_manifest(monkeypatch, answer)

    assert probe_registry("ghcr.io/lab/project",
                          ref="ghcr.io/lab/project/tools@" + FAKE_PUSHED_DIGEST) == (True, "")
    assert asked == ["ghcr.io/lab/project/tools@" + FAKE_PUSHED_DIGEST]


# -----------------------------------------------------------------------------
# Image state
# -----------------------------------------------------------------------------

_LOCAL_ID = "sha256:" + "c" * 64


def _register_byo(project: Path, monkeypatch, image: str):
    from wfc import environments as envs_mod

    stub_docker_pull(monkeypatch)
    stub_docker_image_inspect(monkeypatch, _LOCAL_ID)
    stub_docker_repo_digest(monkeypatch, lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_image_content(monkeypatch)
    return envs_mod.register(name="tools", backend="byo", source={"image": image},
                             project_dir=project)


@workflow(purpose="Where an image is comes from its captures folder and the "
                  "local daemon only: a local/ image the daemon holds is local, "
                  "one it lacks is missing, one Docker cannot be asked about is "
                  "unknown, a recorded push makes it pushed without asking "
                  "Docker, and a byo registry image is in its own registry; the "
                  "registry is never read")
def test_image_state_reads_the_captures_folder_and_the_daemon(tmp_project, monkeypatch):
    from wfc.environments import image_state
    from wfc.environments.captures import write_pushed
    from wfc.environments.docker import ImageNotFoundError

    record = _register_byo(tmp_project, monkeypatch, "docker://local/tools:1")
    fingerprint = record.env_fingerprint
    stub_docker_read_manifest(monkeypatch, AssertionError("the registry was read"))

    asked: list[str] = []

    def held(ref):
        asked.append(ref)
        return _LOCAL_ID

    stub_docker_image_inspect(monkeypatch, held)
    state = image_state("tools", fingerprint, tmp_project)
    assert state.state == "local" and not state.in_registry
    assert asked == [_LOCAL_ID]  # the daemon ref of a local/ record: its image ID

    stub_docker_image_inspect(monkeypatch, ImageNotFoundError("no such image"))
    assert image_state("tools", fingerprint, tmp_project).state == "missing"

    stub_docker_image_inspect(monkeypatch, RuntimeError("Cannot connect to the "
                                                        "Docker daemon"))
    unknown = image_state("tools", fingerprint, tmp_project)
    assert unknown.state == "unknown" and "Cannot connect" in unknown.detail

    write_pushed(tmp_project, "tools", fingerprint, digest=FAKE_PUSHED_DIGEST,
                 tag="fp-" + fingerprint[:12])
    refuse_docker(monkeypatch, "a pushed image's state asks no Docker")
    pushed = image_state("tools", fingerprint, tmp_project)
    assert pushed.state == "pushed" and pushed.in_registry
    assert pushed.pushed is not None and pushed.pushed.digest == FAKE_PUSHED_DIGEST


def test_a_byo_registry_image_is_in_its_own_registry(tmp_project, monkeypatch):
    from wfc.environments import image_state

    record = _register_byo(tmp_project, monkeypatch, "docker://ghcr.io/lab/tools:1.0")
    refuse_docker(monkeypatch, "a byo registry image's state asks no Docker")

    state = image_state("tools", record.env_fingerprint, tmp_project)

    assert state.state == "byo-registry" and state.in_registry
    assert state.image == record.container


def test_an_image_with_no_captures_folder_is_missing(tmp_project, monkeypatch):
    from wfc.environments import image_state

    refuse_docker(monkeypatch, "no ref to ask Docker about")

    assert image_state("tools", "f" * 32, tmp_project).state == "missing"
