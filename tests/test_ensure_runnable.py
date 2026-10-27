"""The run-time rule: an image on this machine runs; else pulled and checked; else refused.

Envs are registered through production ``register`` (and pushed through
production ``push_image``) with the Docker boundary stubbed. The daemon's
contents are simulated at ``image_inspect`` and ``image_content``; ``pull``
records. ``build`` is armed to fail the test everywhere: nothing is ever
built at run time.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    canned_process,
    fake_subprocess_run,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_repo_digest,
    stub_docker_tag,
)
from tests.fixtures.routes import init_test_project
from wfc.environments.docker import ImageNotFoundError
from wfc.reserved import DEMO_ENV_NAME

OLD_DIGEST = "a" * 64
PULLED_ID = "f" * 64
ENV = "image-io"
BASE = "ghcr.io/owner/repo"
REGISTRY_REF = f"{BASE}/{ENV}@{FAKE_PUSHED_DIGEST}"

PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')
PIP_FREEZE = "numpy==1.26.4\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _content_of(ref: str) -> dict:
    """The content the daemon reports for *ref*: a layer named after it."""
    return {"layers": [f"sha256:layer-of-{ref}"], "env": ["PATH=/opt/env/bin"],
            "user": "", "workdir": "/"}


REGISTERED_CONTENT = _content_of(f"sha256:{OLD_DIGEST}")


def _register_pixi(project: Path, monkeypatch, *, push: bool):
    """Register the pixi env through production, built as OLD_DIGEST.

    With *push*, the project's base is set and the image is pushed through
    production ``push_image`` (``pushed.json`` written).
    """
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_content(monkeypatch, _content_of)
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    record = envs_mod.register(
        name=ENV, backend="pixi",
        source={"pixi_lock_content": PIXI_LOCK,
                "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": PIP_FREEZE},
        project_dir=project, push=False,
    )
    if push:
        from wfc.persistence import set_registry_url

        set_registry_url(project, BASE)
        stub_docker_tag(monkeypatch, None)
        stub_docker_push(monkeypatch, None)
        envs_mod.push_image(project, ENV, record.env_fingerprint,
                            record.container, BASE)
    return record


def _image_gone(monkeypatch, *, pulls: list, pulled_content=None,
                pull_error: Exception | None = None,
                content_reads: list | None = None) -> set:
    """The daemon lost the registered image; a pull of a ref makes it held.

    A pulled image answers inspect with PULLED_ID, a different ID than the
    record's (the other image store's), and *pulled_content* as its content
    (default: the registered image's content). Each ref whose content is
    read is appended to *content_reads*.
    """
    held: set = set()

    def inspect(ref):
        if ref in held:
            return f"sha256:{PULLED_ID}"
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    def pull(ref):
        pulls.append(ref)
        if pull_error is not None:
            raise pull_error
        held.add(ref)

    def content(ref):
        if content_reads is not None:
            content_reads.append(ref)
        if ref in held:
            return pulled_content or REGISTERED_CONTENT
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_image_content(monkeypatch, content)
    stub_docker_pull(monkeypatch, pull)
    stub_docker_build(monkeypatch, AssertionError("built at run time"))
    return held


def _manifest(project: Path) -> dict:
    return json.loads((project / ".wfc" / "envs.json").read_text(encoding="utf-8"))


@pytest.fixture
def git_wfc_project(git_project, monkeypatch):
    """A project ``wfc init`` produced inside a git repository, tree clean."""
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    reset_engine()
    yield git_project
    reset_engine()


@workflow(purpose="An image the daemon lacks whose captures folder "
                  "records a push is pulled by <base>/<env>@<digest>, its "
                  "content checked against the recorded env fingerprint, and "
                  "run by the registry ref; nothing is built, the record and "
                  "HEAD are unchanged, and a second ask pulls nothing")
def test_missing_pushed_image_is_pulled_checked_and_run_by_its_registry_ref(
    git_wfc_project, monkeypatch,
):
    from wfc.environments import ensure_version_runnable, get

    project = git_wfc_project
    口 = Step(step_num=1, name="Register and push",
             purpose="register-env records the image; push_image writes "
                     "pushed.json under the project's base")
    record = _register_pixi(project, monkeypatch, push=True)
    before, head = _manifest(project), _git(project, "rev-parse", "HEAD")

    口 = Step(step_num=2, name="Lose the image and ask for it",
             purpose="The daemon no longer holds the image; the rule pulls")
    pulls: list = []
    reads: list = []
    _image_gone(monkeypatch, pulls=pulls, content_reads=reads)
    ref = ensure_version_runnable(ENV, record.env_fingerprint, project)

    口 = Step(step_num=3, name="Check the pull",
             purpose="One pull of the digest-pinned registry ref, whose "
                     "content is read after the pull and fingerprinted, then "
                     "returned; nothing recorded or committed")
    assert ref == REGISTRY_REF
    assert pulls == [REGISTRY_REF]
    assert reads == [REGISTRY_REF]
    assert _manifest(project) == before
    assert _git(project, "rev-parse", "HEAD") == head
    assert get(ENV, project).container == record.container

    # Pulled already: the content is checked again, nothing is pulled.
    assert ensure_version_runnable(ENV, record.env_fingerprint, project) == REGISTRY_REF
    assert pulls == [REGISTRY_REF]


@workflow(purpose="Locked decision 11: a method version that records an older "
                  "image than the env's current one runs that older image: "
                  "with the daemon holding neither, the older image's own "
                  "push is pulled by <base>/<env>@<its digest> and checked "
                  "against the older fingerprint; the current image is not "
                  "pulled")
def test_an_older_versions_pushed_image_is_pulled_not_the_current_one(
    git_wfc_project, monkeypatch,
):
    from wfc import environments as envs_mod
    from wfc.environments import ensure_version_runnable

    project = git_wfc_project
    口 = Step(step_num=1, name="Register and push the older image",
             purpose="The first image is pushed; pushed.json records its digest")
    older = _register_pixi(project, monkeypatch, push=True)

    口 = Step(step_num=2, name="Register a newer image, not pushed",
             purpose="The env's current record moves to another fingerprint")
    newer_id = "9" * 64
    stub_docker_image_inspect(monkeypatch, f"sha256:{newer_id}")
    stub_docker_image_content(monkeypatch, _content_of)
    current = envs_mod.register(
        name=ENV, backend="pixi", force=True, push=False, project_dir=project,
        source={"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": "numpy==2.0.0\n"})
    assert current.env_fingerprint != older.env_fingerprint

    口 = Step(step_num=3, name="Ask for the older version's image",
             purpose="Neither image is on this machine: the older push is "
                     "pulled and its content checked against the older "
                     "fingerprint")
    pulls: list = []
    reads: list = []
    _image_gone(monkeypatch, pulls=pulls, content_reads=reads)
    ref = ensure_version_runnable(ENV, older.env_fingerprint, project)
    assert ref == pulls[0] == reads[0] == REGISTRY_REF
    assert len(pulls) == 1
    assert envs_mod.get(ENV, project).env_fingerprint == current.env_fingerprint


@pytest.mark.parametrize("case", ["never-pushed", "pull-fails", "content-differs"])
@workflow(purpose="An image the daemon lacks is refused when it "
                  "was never pushed, when its pull fails (Docker's cause "
                  "named), or when the pulled content renders another env "
                  "fingerprint; each refusal names `wfc register-env <env> "
                  "--force` then `wfc register-method` and that the steps run "
                  "again, and nothing is built or recorded")
def test_a_missing_image_that_cannot_be_pulled_and_checked_is_refused(
    git_wfc_project, monkeypatch, case,
):
    from wfc.environments import EnvNotRunnableError, ensure_version_runnable

    project = git_wfc_project
    record = _register_pixi(project, monkeypatch, push=case != "never-pushed")
    before = _manifest(project)
    pulls: list = []
    cause = "unauthorized: authentication required"
    _image_gone(
        monkeypatch, pulls=pulls,
        pull_error=RuntimeError(cause) if case == "pull-fails" else None,
        pulled_content=(_content_of("something-else")
                        if case == "content-differs" else None))

    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_version_runnable(ENV, record.env_fingerprint, project)

    message = str(exc.value)
    assert f"wfc register-env {ENV} --force" in message
    assert message.index("--force") < message.index("wfc register-method")
    assert "run again rather than reuse cached results" in message
    assert _manifest(project) == before
    if case == "never-pushed":
        assert pulls == []
        assert "never pushed" in message
        assert f"wfc push-env {ENV}" in message
    else:
        assert pulls == [REGISTRY_REF]
        # The image already has pushed.json: push-env would be a no-op.
        assert "push-env" not in message
    if case == "pull-fails":
        assert cause in message
        assert "`docker login ghcr.io`" in message
        assert "Otherwise recreate it with" in message
    if case == "content-differs":
        assert record.env_fingerprint in message


@pytest.mark.parametrize(("name", "command"), [
    (DEMO_ENV_NAME, "wfc demo --force"),
    ("my-local", "wfc register-env my-local --force"),
])
def test_missing_unpushed_local_byo_image_is_refused(git_wfc_project, monkeypatch,
                                                     name, command):
    """A byo env on a local/ image the daemon lacks, never pushed, is refused
    naming the reserved demo env's `wfc demo --force` or `wfc register-env
    <name> --force`; nothing is pulled or built."""
    from wfc import environments as envs_mod
    from wfc.environments import EnvNotRunnableError, ensure_runnable, get

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    envs_mod.register(name=name, backend="byo",
                      source={"image": "docker://local/wfc-demo-env:latest"},
                      project_dir=git_wfc_project,
                      allow_reserved=name == DEMO_ENV_NAME)
    pulls: list = []
    _image_gone(monkeypatch, pulls=pulls)
    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_runnable(name, get(name, git_wfc_project), git_wfc_project)

    assert name in str(exc.value)
    assert command in str(exc.value)
    assert pulls == []


def test_missing_byo_registry_image_is_pulled_by_its_recorded_ref(
    git_wfc_project, monkeypatch,
):
    """A byo image recorded by its own registry's digest, absent from
    the daemon, is pulled by that recorded ref and checked; never refused
    for want of a push."""
    from wfc import environments as envs_mod
    from wfc.environments import ensure_runnable, get

    ref = f"ghcr.io/org/tool@sha256:{OLD_DIGEST}"
    stub_docker_image_content(monkeypatch, REGISTERED_CONTENT)
    stub_docker_image_inspect(monkeypatch, f"sha256:{PULLED_ID}")
    stub_docker_repo_digest(monkeypatch, f"sha256:{OLD_DIGEST}")
    envs_mod.register(name="reg", backend="byo", source={"image": f"docker://{ref}"},
                      project_dir=git_wfc_project, push=False)
    pulls: list = []
    _image_gone(monkeypatch, pulls=pulls)

    assert ensure_runnable("reg", get("reg", git_wfc_project), git_wfc_project) == ref
    assert pulls == [ref]


@workflow(purpose="After a pull, the daemon answers a different image "
                  "ID than the record names (the other image store): the "
                  "dispatch half of the rule hands Docker the registry ref "
                  "the pre-flight returned, never a recomputed ID, without "
                  "pulling, and the docker run argv carries --pull never")
def test_dispatch_runs_the_registry_ref_never_a_recomputed_id(
    git_wfc_project, monkeypatch,
):
    from wfc.environments import (
        build_docker_command,
        ensure_version_runnable,
        version_run_ref,
    )

    project = git_wfc_project
    record = _register_pixi(project, monkeypatch, push=True)
    pulls: list = []
    _image_gone(monkeypatch, pulls=pulls)
    preflight = ensure_version_runnable(ENV, record.env_fingerprint, project)

    ref = version_run_ref(ENV, record.env_fingerprint, project)
    argv = build_docker_command(ref, project, project / ".dvc" / "cache",
                                ["python", "m.py"], uid=0, gid=0)

    assert ref == preflight == REGISTRY_REF
    assert pulls == [REGISTRY_REF]
    assert argv[:5] == ["docker", "run", "--rm", "--pull", "never"]
    assert REGISTRY_REF in argv
    assert not any(PULLED_ID in a for a in argv)


@pytest.mark.parametrize("backend", ["pixi", "byo"])
@workflow(purpose="When Docker cannot be asked about an image (the daemon is "
                  "not running), the rule refuses naming Docker: it does not "
                  "pull, build, or advise recreating the env, whose image "
                  "may well still be there")
def test_unreachable_docker_is_not_a_missing_image(git_wfc_project, monkeypatch,
                                                    backend):
    from wfc import environments as envs_mod
    from wfc.environments import EnvNotRunnableError, ensure_runnable, get

    if backend == "pixi":
        _register_pixi(git_wfc_project, monkeypatch, push=False)
        name = ENV
    else:
        name = DEMO_ENV_NAME
        stub_docker_image_content(monkeypatch)
        stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
        envs_mod.register(name=name, backend="byo",
                          source={"image": "docker://local/wfc-demo-env:latest"},
                          project_dir=git_wfc_project, allow_reserved=True)
    before = _manifest(git_wfc_project)

    daemon_down = ("failed to connect to the docker API at "
                   "unix:///var/run/docker.sock; is the docker daemon running?")
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, RuntimeError(daemon_down))
    stub_docker_build(monkeypatch, AssertionError("built while Docker was down"))
    stub_docker_pull(monkeypatch, AssertionError("pulled while Docker was down"))

    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_runnable(name, get(name, git_wfc_project), git_wfc_project)

    message = str(exc.value)
    assert name in message
    assert "Docker is running" in message
    assert daemon_down in message
    assert "--force" not in message
    assert _manifest(git_wfc_project) == before


def test_present_local_image_is_returned_without_a_pull(git_wfc_project, monkeypatch):
    from wfc.environments import ensure_runnable, get

    _register_pixi(git_wfc_project, monkeypatch, push=True)
    probes: list = []

    def inspect(ref):
        probes.append(ref)
        return f"sha256:{OLD_DIGEST}"

    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_build(monkeypatch, AssertionError("present image built"))
    stub_docker_pull(monkeypatch, AssertionError("present image pulled"))

    assert ensure_runnable(ENV, get(ENV, git_wfc_project), git_wfc_project) \
        == f"sha256:{OLD_DIGEST}"
    assert probes == [f"sha256:{OLD_DIGEST}"]


@pytest.mark.parametrize(("stderr", "missing"), [
    # The daemon answered and lacks the image: classic, then containerd.
    ("Error: No such image: sha256:" + OLD_DIGEST, True),
    ("Error response from daemon: No such object: sha256:" + OLD_DIGEST, True),
    # The daemon did not answer, or refused: not a missing image.
    ("failed to connect to the docker API at npipe:////./pipe/docker_engine; "
     "check if the daemon is running", False),
    ("permission denied while trying to connect to the Docker daemon socket",
     False),
])
def test_image_inspect_tells_a_missing_image_from_an_unreachable_docker(
    monkeypatch, stderr, missing,
):
    from wfc.environments import docker as docker_runner

    fake_subprocess_run(monkeypatch,
                        canned_process(returncode=1, stdout="", stderr=stderr),
                        only="docker")

    with pytest.raises(RuntimeError) as exc:
        docker_runner.image_inspect(f"sha256:{OLD_DIGEST}")
    assert isinstance(exc.value, docker_runner.ImageNotFoundError) is missing
    assert stderr in str(exc.value)
