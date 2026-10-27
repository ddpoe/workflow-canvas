"""Env names Docker cannot push are refused at push time.

The env-name grammar is unchanged: an env named ``MyTools`` registers and
runs on this machine. A registry repository name is narrower (lower case,
Docker's path-component grammar), so such an env is never pushed:
register-env with a base refuses it before Docker is asked; push-env lists
it as cannot push, with the name's reason taking precedence over the state's
own, and pushes the rest; doctor, share, the method pre-check and the
canvas chip say it cannot be pushed instead of "run push-env".

Projects come from the shared ``tmp_project`` fixture (``wfc init``); envs
are registered through production ``wfc register-env``, the base set
through production ``set_registry_url``. Docker is faked at the
``wfc.environments.docker`` wrappers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    refuse_docker,
    stub_docker_image_content,
    stub_docker_read_manifest,
    stub_gh,
    stub_readiness_probes,
)
from tests.test_push_env import (
    _Docker,
    _git,
    _pushed,
    _record,
    _register_env_cli,
)
BASE = "ghcr.io/lab/tools"


def _setup(project: Path, monkeypatch, names: list[str]):
    """Register *names* as byo ``local/`` images (no base, a GitHub remote).

    The env name may hold upper case; the image's own name cannot (Docker
    refuses it), so each is ``local/<name lowered>:1``.

    Returns:
        The faked daemon and each env's fingerprint.
    """
    stub_readiness_probes(monkeypatch, docker="ok")
    # Content named by the image ID, the shape the fixture env's record is
    # made from, so the labelled copy a push makes of any image (fixture-env
    # included) renders that image's recorded fingerprint.
    stub_docker_image_content(monkeypatch, lambda ref: {
        "layers": [ref], "env": None, "user": None, "workdir": None})
    docker = _Docker(monkeypatch)
    _git(project, "remote", "add", "origin", "git@github.com:Lab/Tools.git")
    fingerprints = {}
    for name in names:
        assert _register_env_cli(name, "--backend", "byo", "--image",
                                 f"docker://local/{name.lower()}:1") == 0
        fingerprints[name] = _record(project, name)["env_fingerprint"]
    return docker, fingerprints

#: Docker's repository path-component grammar, as distribution/reference
#: spells it: ``[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*``.
PUSHABLE = ["tools", "my_env", "a__b", "x--y", "wfc-demo-env", "image-io",
            "fixture-env", "e1", "env.v2"]
UNPUSHABLE = ["MyTools", "_x", "x-", "a___b", "a_-b", "__demo__env"]


def _set_base(project: Path) -> None:
    from wfc.persistence import set_registry_url

    config = set_registry_url(project, BASE)
    _git(project, "add", "--", config.relative_to(project).as_posix())
    _git(project, "commit", "-q", "-m", "set the registry base")


def _tree(root: Path) -> dict[str, bytes]:
    state = root / ".wfc"
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(state.rglob("*")) if p.is_file() and p.name != "wfc.db"}


@pytest.mark.parametrize("name", PUSHABLE)
def test_a_name_docker_accepts_has_no_problem(name):
    from wfc.environments import push_name_problem

    assert push_name_problem(name, registered=True) is None


@pytest.mark.parametrize("name", UNPUSHABLE)
def test_a_name_docker_refuses_names_the_rule_and_both_fixes(name):
    from wfc.environments import push_name_problem

    reason = push_name_problem(name, registered=True)
    assert reason is not None
    assert f"env {name!r} cannot be pushed" in reason
    assert "lower-case letters and digits" in reason
    assert "`wfc register-env <new-name> ...`" in reason
    assert "`env: <new-name>`" in reason and "`wfc register-method`" in reason
    assert f"`wfc delete-env {name}`" in reason
    assert "--no-push" not in reason


def test_the_fixes_follow_where_the_name_is_read():
    """register-env adds --no-push; delete-env only for a registered env."""
    from wfc.environments import push_name_problem

    registering = push_name_problem("MyTools", registering=True)
    assert "--no-push" in registering and "wfc delete-env" not in registering
    assert "wfc delete-env" not in push_name_problem("MyTools")


@workflow(purpose="register-env with a registry base refuses a byo local/ image "
                  "named MyTools before Docker is asked and writes nothing; "
                  "with --no-push it registers, saying push-env cannot push "
                  "it under this name; a byo registry image named MyTools "
                  "registers, since it is never pushed")
def test_register_env_refuses_an_unpushable_name_before_docker(
        tmp_project, monkeypatch, capsys):
    stub_readiness_probes(monkeypatch, docker="ok")
    _set_base(tmp_project)

    口 = Step(step_num=1, name="A local byo image, base set",
             purpose="Refused before any Docker call; nothing written")
    refuse_docker(monkeypatch, "register-env must refuse before Docker")
    before = _tree(tmp_project)
    head = _git(tmp_project, "rev-parse", "HEAD")
    capsys.readouterr()
    rc = _register_env_cli("MyTools", "--backend", "byo",
                           "--image", "docker://local/mytools:1")
    err = capsys.readouterr().err
    assert rc != 0
    assert "env 'MyTools' cannot be pushed" in err
    assert "--no-push" in err
    assert _tree(tmp_project) == before
    assert _git(tmp_project, "rev-parse", "HEAD") == head

    口 = Step(step_num=2, name="The same with --no-push",
             purpose="Registered; the line says push-env cannot push it")
    docker = _Docker(monkeypatch)
    stub_docker_image_content(monkeypatch, lambda ref: {"layers": [f"layer-of-{ref}"]})
    rc = _register_env_cli("MyTools", "--backend", "byo",
                           "--image", "docker://local/mytools:1", "--no-push")
    out = capsys.readouterr().out
    assert rc == 0
    assert "MyTools" in json.loads(
        (tmp_project / ".wfc" / "envs.json").read_text(encoding="utf-8"))["envs"]
    assert "`wfc push-env MyTools` pushes it" not in out
    assert "push-env cannot push it under this name" in out
    assert docker.tags == [] and docker.pushes == []

    口 = Step(step_num=3, name="A byo registry image named MyTools",
             purpose="Never pushed, so never refused")
    rc = _register_env_cli("MyTools", "--backend", "byo",
                           "--image", "docker://ghcr.io/lab/mytools:1", "--force")
    assert rc == 0, capsys.readouterr().err
    assert docker.pushes == []


@workflow(purpose="push-env --all with envs tools and MyTools, both on this "
                  "machine: tools is pushed, MyTools is listed as cannot push "
                  "with the name's reason and never tagged, exit 1; doctor's "
                  "registry row and share's images row name MyTools with the "
                  "reason, not push-env")
def test_push_env_all_pushes_the_rest_and_names_the_unpushable_env(
        tmp_project, monkeypatch, capsys, tmp_path_factory):
    from wfc.execution.readiness import check_registry, share_checks

    口 = Step(step_num=1, name="Two local envs, no base, a GitHub remote",
             purpose="tools and MyTools registered, unpushed")
    docker, fps = _setup(tmp_project, monkeypatch, ["tools", "MyTools"])

    口 = Step(step_num=2, name="push-env --all",
             purpose="tools pushed; MyTools listed with the reason; exit 1")
    capsys.readouterr()
    assert _push_env_all() == 1
    captured = capsys.readouterr()
    assert _pushed(tmp_project, "tools", fps["tools"]).is_file()
    assert not _pushed(tmp_project, "MyTools", fps["MyTools"]).exists()
    assert not any("MyTools" in source or "MyTools" in target
                   for source, target in docker.tags)
    assert "cannot push: MyTools:fp-" in captured.out
    assert "env 'MyTools' cannot be pushed" in captured.err

    口 = Step(step_num=3, name="doctor and share",
             purpose="Both warn naming MyTools and the reason, not push-env")
    stub_docker_read_manifest(monkeypatch, "found")
    stub_gh(monkeypatch)
    doctor = check_registry(tmp_project)
    assert doctor.status == "warn", doctor
    assert "MyTools:fp-" in doctor.message
    assert "env 'MyTools' cannot be pushed" in doctor.fix_hint
    assert "push-env" not in doctor.fix_hint
    images = {row.name: row for row in share_checks(tmp_project)}["images"]
    assert images.status == "warn", images
    assert "MyTools:fp-" in images.message
    assert "versions row" not in images.message
    assert "env 'MyTools' cannot be pushed" in images.fix_hint
    assert "push-env" not in images.fix_hint
    assert _record(tmp_project, "MyTools")["env_fingerprint"] == fps["MyTools"]


def _push_env_all() -> int:
    from wfc.cli import cli_main

    return cli_main(["push-env", "--all", "--yes"])


@workflow(purpose="The canvas chip for an env named MyTools whose image is on "
                  "this machine is cannot_push carrying the name's reason "
                  "whether or not a base is set, and does not say run push-env")
def test_the_chip_says_the_name_cannot_be_pushed(
        tmp_project, monkeypatch, tmp_path_factory):
    from wfc.environments import registry_chip

    _, fps = _setup(tmp_project, monkeypatch, ["MyTools"])
    older = fps["MyTools"]
    # A second image: the first stays on this machine as an older one.
    stub_docker_image_content(monkeypatch, lambda ref: {"layers": ["layer-two"]})
    assert _register_env_cli("MyTools", "--backend", "byo", "--image",
                             "docker://local/mytools:2", "--force") == 0
    record = _record(tmp_project, "MyTools")
    fingerprint, container = record["env_fingerprint"], record["container"]
    assert fingerprint != older

    口 = Step(step_num=1, name="No base",
             purpose="The chip is cannot_push with the reason")
    chip = registry_chip("MyTools", tmp_project, container=container,
                         fingerprint=fingerprint, versions=[("m/a v1", older)])
    assert chip.state == "cannot_push"
    assert chip.reason and "env 'MyTools' cannot be pushed" in chip.reason

    口 = Step(step_num=2, name="Base set",
             purpose="Still cannot_push, no older image counted as not pushed")
    _set_base(tmp_project)
    chip = registry_chip("MyTools", tmp_project, container=container,
                         fingerprint=fingerprint, versions=[("m/a v1", older)])
    assert chip.state == "cannot_push"
    assert chip.older_not_pushed == 0
