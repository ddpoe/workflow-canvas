"""ensure_runnable: a runnable daemon ref for an env, rebuilding a missing one.

Envs are registered through production ``register`` with the Docker boundary
stubbed. The daemon's contents are simulated at ``image_inspect``: a probe of
the recorded image ID misses when the image is "gone", and the rebuild's own
inspect of the build tag returns the new image ID. ``pull`` is armed to fail
the test everywhere: wfc never pulls a ``local/`` image.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    canned_process,
    fake_subprocess_run,
    refuse_docker,
    stub_docker_build,
    stub_docker_image_inspect,
    stub_docker_pull,
)
from tests.fixtures.routes import init_test_project
from wfc.environments.docker import ImageNotFoundError

OLD_DIGEST = "a" * 64
NEW_DIGEST = "b" * 64
ENV = "image-io"
BUILD_TAG = f"local/{ENV}:_wfc-build"

PIXI_LOCK = "version: 6\nenvironments:\n  image-io:\n    packages: {}\n"
PIXI_TOML = ('[project]\nname = "image-io"\n'
             'channels = ["conda-forge"]\nplatforms = ["linux-64"]\n')
PIP_FREEZE = "numpy==1.26.4\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _register_pixi(project: Path, monkeypatch, **kwargs):
    """Register the pixi env through production, its image built as OLD_DIGEST."""
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    return envs_mod.register(
        name=ENV, backend="pixi",
        source={"pixi_lock_content": PIXI_LOCK,
                "pixi_toml_content": PIXI_TOML,
                "pip_freeze_content": PIP_FREEZE},
        project_dir=project, **kwargs,
    )


def _image_gone(monkeypatch, *, builds: list, pulls: list) -> None:
    """The daemon lost the registered image; a build produces NEW_DIGEST."""
    def inspect(ref):
        if ref == BUILD_TAG and builds:
            return f"sha256:{NEW_DIGEST}"
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_build(monkeypatch,
                      lambda build_dir, tag: builds.append((Path(build_dir), tag)))
    stub_docker_pull(monkeypatch, lambda ref: pulls.append(ref))


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


@workflow(purpose="A registered pixi env whose image the Docker daemon no "
                  "longer holds is rebuilt by ensure_runnable from its staged "
                  "build context: the record takes the new digest with its "
                  "python kept, .wfc/envs.json alone is committed by pathspec, "
                  "the new ref is printed, and nothing is pulled")
def test_missing_local_env_is_rebuilt_from_its_build_context(
    git_wfc_project, monkeypatch, capsys,
):
    from wfc.environments import ensure_runnable, get

    project = git_wfc_project
    口 = Step(step_num=1, name="Register a pixi env",
             purpose="register-env builds the image, stages the build "
                     "context and commits the record")
    registered = _register_pixi(project, monkeypatch,
                                python_override="/opt/env/bin/python")
    assert _git(project, "status", "--porcelain").strip() == ""

    口 = Step(step_num=2, name="Lose the image and stage an unrelated file",
             purpose="The daemon no longer holds the image (a fresh daemon, "
                     "a prune); the user has their own change staged")
    builds: list = []
    pulls: list = []
    _image_gone(monkeypatch, builds=builds, pulls=pulls)
    (project / "notes.txt").write_text("mine\n", encoding="utf-8")
    _git(project, "add", "notes.txt")
    head_before = _git(project, "rev-parse", "HEAD").strip()
    capsys.readouterr()

    口 = Step(step_num=3, name="Ask for a runnable ref",
             purpose="ensure_runnable probes, finds the image missing and "
                     "rebuilds it from .wfc/build/<name>/")
    ref = ensure_runnable(ENV, get(ENV, project), project)

    口 = Step(step_num=4, name="Check the rebuild",
             purpose="New ref returned and recorded, python and "
                     "source_fingerprint kept, one pathspec commit, the "
                     "user's staged file untouched, no pull")
    assert ref == f"sha256:{NEW_DIGEST}"
    assert builds == [(project / ".wfc" / "build" / ENV, BUILD_TAG)]
    assert pulls == []

    rebuilt = get(ENV, project)
    assert rebuilt.container == f"docker://local/{ENV}@sha256:{NEW_DIGEST}"
    assert rebuilt.python == "/opt/env/bin/python"
    assert rebuilt.source_fingerprint == registered.source_fingerprint
    assert rebuilt.env_fingerprint != registered.env_fingerprint
    assert rebuilt.built_from_lock == registered.built_from_lock

    assert _git(project, "rev-parse", "HEAD~1").strip() == head_before
    committed = _git(project, "show", "--name-only", "--pretty=format:", "HEAD")
    assert committed.split() == [".wfc/envs.json"]
    assert _git(project, "status", "--porcelain").splitlines() == ["A  notes.txt"]

    assert rebuilt.container in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["missing", "mismatched"])
@workflow(purpose="A missing pixi image whose build context is gone, or no "
                  "longer reproduces the record's source_fingerprint (a failed "
                  "register-env --force overwrote it), is refused naming "
                  "`wfc register-env <name> --force`; nothing is built or "
                  "pulled and the record is unchanged")
def test_missing_env_with_unusable_context_is_refused(
    tmp_path, monkeypatch, damage,
):
    from wfc.environments import EnvNotRunnableError, ensure_runnable, get

    (tmp_path / ".wfc").mkdir()
    _register_pixi(tmp_path, monkeypatch)
    build_dir = tmp_path / ".wfc" / "build" / ENV
    if damage == "missing":
        for f in build_dir.iterdir():
            f.unlink()
        build_dir.rmdir()
    else:
        (build_dir / "pip-freeze.txt").write_text("numpy==2.0.0\n",
                                                   encoding="utf-8")
    before = _manifest(tmp_path)

    builds: list = []
    pulls: list = []
    _image_gone(monkeypatch, builds=builds, pulls=pulls)
    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_runnable(ENV, get(ENV, tmp_path), tmp_path)

    message = str(exc.value)
    assert ENV in message
    assert f"wfc register-env {ENV} --force" in message
    assert builds == [] and pulls == []
    assert _manifest(tmp_path) == before


@pytest.mark.parametrize(("name", "command"), [
    ("__demo__env", "wfc demo --force"),
    ("my-local", "wfc register-env my-local --force"),
])
@workflow(purpose="A byo env on a local/ image the daemon lacks is refused, "
                  "never pulled or built: the reserved demo env names "
                  "`wfc demo --force`, any other names "
                  "`wfc register-env <name> --force`")
def test_missing_local_byo_image_is_refused(tmp_path, monkeypatch, name, command):
    from wfc import environments as envs_mod
    from wfc.environments import EnvNotRunnableError, ensure_runnable, get

    (tmp_path / ".wfc").mkdir()
    stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
    envs_mod.register(name=name, backend="byo",
                      source={"image": "docker://local/wfc-demo-env:latest"},
                      project_dir=tmp_path,
                      allow_reserved=name.startswith("__demo__"))

    builds: list = []
    pulls: list = []
    _image_gone(monkeypatch, builds=builds, pulls=pulls)
    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_runnable(name, get(name, tmp_path), tmp_path)

    assert name in str(exc.value)
    assert command in str(exc.value)
    assert builds == [] and pulls == []


@pytest.mark.parametrize("backend", ["pixi", "byo"])
@workflow(purpose="When Docker cannot be asked about a local/ image (the "
                  "daemon is not running), ensure_runnable refuses naming "
                  "Docker: it does not rebuild, pull, or advise recreating "
                  "the env, whose image may well still be there")
def test_unreachable_docker_is_not_a_missing_image(tmp_path, monkeypatch,
                                                    backend):
    from wfc import environments as envs_mod
    from wfc.environments import EnvNotRunnableError, ensure_runnable, get

    (tmp_path / ".wfc").mkdir()
    if backend == "pixi":
        _register_pixi(tmp_path, monkeypatch)
        name = ENV
    else:
        name = "__demo__env"
        stub_docker_image_inspect(monkeypatch, f"sha256:{OLD_DIGEST}")
        envs_mod.register(name=name, backend="byo",
                          source={"image": "docker://local/wfc-demo-env:latest"},
                          project_dir=tmp_path, allow_reserved=True)
    before = _manifest(tmp_path)

    daemon_down = ("failed to connect to the docker API at "
                   "unix:///var/run/docker.sock; is the docker daemon running?")
    stub_docker_image_inspect(monkeypatch, RuntimeError(daemon_down))
    stub_docker_build(monkeypatch, AssertionError("rebuilt while Docker was down"))
    stub_docker_pull(monkeypatch, AssertionError("pulled while Docker was down"))

    with pytest.raises(EnvNotRunnableError) as exc:
        ensure_runnable(name, get(name, tmp_path), tmp_path)

    message = str(exc.value)
    assert name in message
    assert "Docker is running" in message
    assert daemon_down in message
    assert "--force" not in message
    assert _manifest(tmp_path) == before


def test_present_local_image_is_returned_without_a_build(tmp_path, monkeypatch):
    from wfc.environments import ensure_runnable, get

    (tmp_path / ".wfc").mkdir()
    _register_pixi(tmp_path, monkeypatch)
    probes: list = []

    def inspect(ref):
        probes.append(ref)
        return f"sha256:{OLD_DIGEST}"

    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_build(monkeypatch, AssertionError("present image rebuilt"))
    stub_docker_pull(monkeypatch, AssertionError("local image pulled"))

    assert ensure_runnable(ENV, get(ENV, tmp_path), tmp_path) \
        == f"sha256:{OLD_DIGEST}"
    assert probes == [f"sha256:{OLD_DIGEST}"]


def test_registry_image_passes_through_without_touching_docker(
    tmp_path, monkeypatch,
):
    from tests.fixtures.routes.registration import write_env_record
    from wfc.environments import ensure_runnable, get

    write_env_record(tmp_path, "reg", image="ghcr.io/org/tool",
                     digest=OLD_DIGEST)
    refuse_docker(monkeypatch)

    assert ensure_runnable("reg", get("reg", tmp_path), tmp_path) \
        == f"ghcr.io/org/tool@sha256:{OLD_DIGEST}"


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
