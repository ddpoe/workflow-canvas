"""wfc push-env when a push or the config write fails.

The first failed push stops the run; the pushes before it are recorded and
committed; a first run whose every push fails writes no ``[registry] url``;
the exit code is 1. A tag Docker does not answer names Docker, not
``docker login``. A base that cannot be written is reported, not raised.
Envs are registered through production ``wfc register-env``; Docker is
stubbed at ``wfc.environments.docker``.
"""

from __future__ import annotations

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_docker_image_content, stub_docker_tag, stub_readiness_probes
from tests.test_push_env import (
    FAKE_PUSHED_DIGEST,
    _committed,
    _config,
    _Docker,
    _git,
    _lock,
    _push_env,
    _pushed,
    _record,
    _register_env_cli,
)
from wfc.environments.docker import DockerTimeoutError

BASE = "ghcr.io/lab/tools"


def _setup(project, monkeypatch, tmp_path_factory, names):
    """Register *names* (no base set, a GitHub remote); return their fingerprints."""
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_content(monkeypatch, lambda ref: {"layers": [f"layer-of-{ref}"]})
    docker = _Docker(monkeypatch)
    _git(project, "remote", "add", "origin", "git@github.com:Lab/Tools.git")
    lock = _lock(tmp_path_factory)
    fingerprints = {}
    for name in names:
        stub_docker_image_content(
            monkeypatch, lambda ref, name=name: {"layers": [f"layer-of-{name}"]})
        assert _register_env_cli(name, "--from", str(lock), "--backend", "pixi") == 0
        fingerprints[name] = _record(project, name)["env_fingerprint"]
    return docker, fingerprints


def _failing_push(docker, fails_on: str):
    def push(ref):
        docker.pushes.append(ref)
        if f"/{fails_on}:" in ref:
            raise RuntimeError("denied: permission_denied: write_package")
        return FAKE_PUSHED_DIGEST
    return push


@workflow(purpose="A first push-env --all whose second push fails stops there: "
                  "the third image is never pushed, the first is recorded and "
                  "committed with the base, the failure names docker login "
                  "<host>, and the exit code is 1")
def test_a_failed_push_stops_the_run_and_keeps_the_earlier_pushes(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    from tests.fixtures.fakes import stub_docker_push

    口 = Step(step_num=1, name="Three envs, no base",
             purpose="Each registered on this machine, unpushed")
    docker, fps = _setup(tmp_project, monkeypatch, tmp_path_factory, ["e1", "e2", "e3"])
    stub_docker_push(monkeypatch, _failing_push(docker, "e2"))

    口 = Step(step_num=2, name="push-env --all; e2's push is refused",
             purpose="The run stops at e2")
    capsys.readouterr()
    assert _push_env("--all", "--yes") == 1
    err = capsys.readouterr().err

    口 = Step(step_num=3, name="e1 kept, e3 never attempted",
             purpose="e1's record and the base are committed; e2 and e3 have none")
    assert [ref.split("/")[-1].split(":")[0] for ref in docker.pushes] == ["e1", "e2"]
    assert _pushed(tmp_project, "e1", fps["e1"]).is_file()
    assert not _pushed(tmp_project, "e2", fps["e2"]).exists()
    assert not _pushed(tmp_project, "e3", fps["e3"]).exists()
    assert _committed(tmp_project) == {
        ".wfc/wf-canvas.toml", f".wfc/captures/e1/{fps['e1']}/pushed.json"}
    assert "permission_denied" in err and "`docker login ghcr.io`" in err


def test_a_first_run_whose_pushes_all_fail_writes_no_base(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """The base is written only once a push succeeded."""
    from tests.fixtures.fakes import stub_docker_push

    docker, _ = _setup(tmp_project, monkeypatch, tmp_path_factory, ["e1"])
    stub_docker_push(monkeypatch, _failing_push(docker, "e1"))
    config, head = _config(tmp_project).read_bytes(), _git(tmp_project, "rev-parse", "HEAD")

    assert _push_env("e1", "--yes") == 1
    assert _config(tmp_project).read_bytes() == config
    assert _git(tmp_project, "rev-parse", "HEAD") == head


def test_a_tag_docker_does_not_answer_names_docker_not_a_login(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """The remedy is to check Docker, not `docker login`."""
    _setup(tmp_project, monkeypatch, tmp_path_factory, ["e1"])
    stub_docker_tag(monkeypatch, DockerTimeoutError(
        "docker tag 'x' 'y' did not finish within 15 s: Docker is not answering"))
    capsys.readouterr()

    assert _push_env("e1", "--yes") == 1
    err = capsys.readouterr().err
    assert "Docker did not answer; check that Docker is running" in err
    assert "docker login" not in err


def test_a_base_that_cannot_be_written_is_reported_not_raised(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    """The config holds the registry as an inline table the text edit cannot
    touch: set_registry_url refuses, and push-env reports it, exit 1."""
    _setup(tmp_project, monkeypatch, tmp_path_factory, ["e1"])
    config = _config(tmp_project)
    # A top-level key, before every table: the inline table is [registry].
    config.write_text('registry = {url = ""}\n' + config.read_text(encoding="utf-8"),
                      encoding="utf-8", newline="\n")
    capsys.readouterr()

    assert _push_env("e1", "--yes") == 1
    err = capsys.readouterr().err
    assert "[registry] url" in err and "could not be written" in err
    assert "Traceback" not in err


def test_a_never_pushed_demo_image_names_wfc_demo_to_recreate_it(tmp_path):
    """The demo env is recreated by `wfc demo --force`, not register-env."""
    from wfc.environments import IMAGE_MISSING, PushItem, PushPlan, unpushable_lines
    from wfc.reserved import DEMO_ENV_NAME

    demo = DEMO_ENV_NAME
    plan = PushPlan(project_dir=tmp_path, base=BASE, set_base=False, base_from="",
                    items=(PushItem(demo, "f" * 32, IMAGE_MISSING, current=True),))

    [line] = unpushable_lines(plan)
    assert "`wfc demo --force`" in line
    assert "register-env" not in line


def test_a_pushed_image_with_no_base_names_how_to_set_it(
        tmp_project, monkeypatch, tmp_path_factory):
    """Recorded as pushed but no [registry] url: the refusal names the fix."""
    import pytest

    from wfc.environments import pullable_ref
    from wfc.environments.captures import write_pushed

    _, fingerprints = _setup(tmp_project, monkeypatch, tmp_path_factory, ["e1"])
    write_pushed(tmp_project, "e1", fingerprints["e1"], digest=FAKE_PUSHED_DIGEST,
                 tag=f"fp-{fingerprints['e1'][:12]}")

    with pytest.raises(ValueError) as exc:
        pullable_ref("e1", fingerprints["e1"], _record(tmp_project, "e1")["container"],
                     tmp_project)
    message = str(exc.value)
    assert "no [registry] url" in message
    assert "Pull the project's latest commit" in message
    assert "`wfc push-env e1 --registry <url>`" in message
