"""Subsystem tests: the demo env, the demo stamp, and --force over an older demo.

The demo env is ``wfc-demo-env``. The demo registers its env under a name Docker can push and never pushes it
itself, base or not; ``wfc push-env wfc-demo-env`` pushes it. ``wfc demo``
writes and commits ``.wfc/demo.json``; a demo with no stamp, or another one,
is refused with the older-wfc advice, and ``wfc doctor`` warns about it.
``--force`` over an older demo removes every env the demo module's methods
name, so nothing names the old ``__demo__env`` afterwards.

Projects are built through production paths (``wfc init``, ``wfc demo``,
the registration functions with the demo's opt-in); Docker is faked at the
``wfc.environments.docker`` wrappers and the demo's own probe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    fake_subprocess_run,
    redirect_home,
    refusing_process,
    stub_demo_docker_probe,
    stub_docker_image_content,
    stub_readiness_probes,
    stub_server_bind,
)
from tests.test_demo_preflight import _tree_snapshot
from tests.test_env_push_name_rule import BASE, _set_base
from tests.test_push_env import _Docker
from wfc.reserved import DEMO_ENV_NAME

OLD_DEMO_ENV = "__demo__env"
OLDER_WFC = "this demo project was made by an older wfc"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _project(tmp_path: Path, monkeypatch):
    """An initialised project with every Docker entry point faked."""
    from wfc.init import init_project

    redirect_home(monkeypatch, tmp_path / "home")
    target = tmp_path / "proj"
    init_project(target, assume_yes=True)
    # A docker command that slips past the wrappers fails the test.
    fake_subprocess_run(monkeypatch, refusing_process(
        "wfc demo must not reach docker"), only="docker")
    stub_demo_docker_probe(monkeypatch, "ok")
    monkeypatch.setenv("WFC_DEMO_IMAGE", "local/wfc-demo-env:test")
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_content(monkeypatch)
    docker = _Docker(monkeypatch)
    stub_server_bind(monkeypatch, started=[], where="demo")
    return target, docker


def _demo(target: Path, *flags: str) -> int:
    from wfc.cli import cli_main

    return cli_main(["demo", "--dir", str(target), "--no-open", *flags])


def _envs(target: Path) -> dict:
    return json.loads((target / ".wfc" / "envs.json").read_text("utf-8"))["envs"]


def _stamp(target: Path) -> Path:
    return target / ".wfc" / "demo.json"


@workflow(purpose="wfc demo with a registry base set registers wfc-demo-env "
                  "without tagging or pushing it and names wfc push-env "
                  "wfc-demo-env; that command then pushes "
                  "<base>/wfc-demo-env:fp-...")
def test_the_demo_never_pushes_and_push_env_pushes_its_env(
        tmp_path, monkeypatch, capsys):
    from wfc.cli import cli_main

    target, docker = _project(tmp_path, monkeypatch)
    _set_base(target)
    capsys.readouterr()

    口 = Step(step_num=1, name="wfc demo, base set",
             purpose="Registered with no tag and no push")
    assert _demo(target) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert DEMO_ENV_NAME in _envs(target)
    assert docker.tags == [] and docker.pushes == []
    assert f"wfc push-env {DEMO_ENV_NAME}" in out

    口 = Step(step_num=2, name="wfc push-env wfc-demo-env",
             purpose="Pushes the demo image under the base")
    monkeypatch.chdir(target)
    rc = cli_main(["push-env", DEMO_ENV_NAME, "--yes"])
    assert rc == 0, capsys.readouterr().err
    fingerprint = _envs(target)[DEMO_ENV_NAME]["env_fingerprint"]
    assert docker.pushes == [f"{BASE}/{DEMO_ENV_NAME}:fp-{fingerprint[:12]}"]


def _old_demo_project(tmp_path: Path, monkeypatch) -> Path:
    """A demo project as an older wfc left it: env __demo__env, no stamp.

    Built through production paths: the env registered with the demo's
    opt-in, the demo module, and a demo method whose method.yaml names
    ``__demo__env`` (the bundled preprocess asset with its env line
    renamed, as the older wfc shipped it).
    """
    from wfc import environments as envs_mod
    from wfc.demo.scaffold import ASSETS_DIR, DEMO_MODULE
    from wfc.persistence import use_project
    from wfc.registration import register_method, register_module

    target, _ = _project(tmp_path, monkeypatch)
    method_dir = tmp_path / "old-assets" / "preprocess"
    shutil.copytree(ASSETS_DIR / "methods" / "preprocess", method_dir)
    yaml = method_dir / "method.yaml"
    yaml.write_text(yaml.read_text("utf-8").replace(
        f"env: {DEMO_ENV_NAME}", f"env: {OLD_DEMO_ENV}"), "utf-8")
    monkeypatch.chdir(target)
    with use_project(target):
        envs_mod.register(name=OLD_DEMO_ENV, backend="byo",
                          source={"image": "docker://local/wfc-demo-env:old"},
                          project_dir=target, allow_reserved=True)
        register_module(name=DEMO_MODULE, contracts=[], allow_reserved=True)
        register_method(method_dir=method_dir, module_name=DEMO_MODULE,
                        method_name=f"{DEMO_MODULE}preprocess",
                        script_name="preprocess.py",
                        allow_reserved=True, assume_yes=True)
    assert OLD_DEMO_ENV in _envs(target)
    assert (target / ".wfc" / "captures" / OLD_DEMO_ENV).is_dir()
    assert not _stamp(target).exists()
    return target


@workflow(purpose="wfc demo --force over a demo an older wfc made (env "
                  "__demo__env, a method recording it, no stamp) leaves no "
                  "__demo__env in envs.json, captures or any version list; "
                  "wfc-demo-env is registered, the methods record it, the "
                  "stamp is current and the tree is clean")
def test_demo_force_over_an_older_demo_leaves_nothing_naming_the_old_env(
        tmp_path, monkeypatch, capsys):
    from wfc.demo.scaffold import DEMO_MODULE, DEMO_SCHEMA
    from wfc.registration import read_version_list

    target = _old_demo_project(tmp_path, monkeypatch)
    capsys.readouterr()

    assert _demo(target, "--force") == 0, capsys.readouterr().err

    envs = _envs(target)
    assert OLD_DEMO_ENV not in envs and DEMO_ENV_NAME in envs
    assert not (target / ".wfc" / "captures" / OLD_DEMO_ENV).exists()
    assert not (target / ".wfc" / "build" / OLD_DEMO_ENV).exists()
    lists = sorted((target / ".wfc" / "registry" / "methods" / DEMO_MODULE)
                   .glob("*.versions.json"))
    assert len(lists) == 5
    for path in lists:
        method = path.name[: -len(".versions.json")]
        entries = read_version_list(target, DEMO_MODULE, method)
        assert [e.env_name for e in entries] == [DEMO_ENV_NAME]
    registry = target / ".wfc" / "registry"
    assert not any(OLD_DEMO_ENV in p.read_text("utf-8", errors="replace")
                   for p in registry.rglob("*") if p.is_file())
    assert json.loads(_stamp(target).read_text("utf-8")) == {
        "demo_schema": DEMO_SCHEMA}
    assert _git(target, "status", "--porcelain") == ""


@workflow(purpose="wfc demo writes and commits the demo schema stamp; plain "
                  "wfc demo over a current stamp is refused naming --force "
                  "and --remove, over a missing or older stamp with the "
                  "older-wfc advice, each changing nothing; the doctor row "
                  "appears only for the stale demo")
def test_the_demo_stamp_is_written_checked_and_reported(
        tmp_path, monkeypatch, capsys):
    from wfc.demo.scaffold import DEMO_SCHEMA, demo_schema_check

    target, _ = _project(tmp_path, monkeypatch)
    assert demo_schema_check(target) is None  # no demo: no row

    口 = Step(step_num=1, name="wfc demo",
             purpose="The stamp is written and committed; the tree is clean")
    assert _demo(target) == 0, capsys.readouterr().err
    assert json.loads(_stamp(target).read_text("utf-8")) == {
        "demo_schema": DEMO_SCHEMA}
    assert _git(target, "ls-files", "--", ".wfc/demo.json") == ".wfc/demo.json"
    assert _git(target, "status", "--porcelain") == ""
    assert demo_schema_check(target) is None

    口 = Step(step_num=2, name="wfc demo again, current stamp",
             purpose="Refused with the --force rebuild wording; nothing changes")
    before = _tree_snapshot(target)
    capsys.readouterr()
    assert _demo(target) == 1
    err = capsys.readouterr().err
    assert "`wfc demo --force` removes the demo and builds it again" in err
    assert "`wfc demo --remove`" in err and OLDER_WFC not in err
    assert _tree_snapshot(target) == before

    口 = Step(step_num=3, name="An older stamp, then none",
             purpose="Refused with the older-wfc advice; nothing changes; "
                     "the doctor row warns naming wfc demo --force")
    # Declared fixture deviation: no production path writes an older
    # stamp or deletes one alone, so the file is rewritten, then removed.
    _stamp(target).write_text('{"demo_schema": 1}\n', encoding="utf-8")
    for _ in ("older", "missing"):
        before = _tree_snapshot(target)
        capsys.readouterr()
        assert _demo(target) == 1
        err = capsys.readouterr().err
        assert f"{OLDER_WFC}; run `wfc demo --force` to rebuild it" in err
        assert _tree_snapshot(target) == before
        row = demo_schema_check(target)
        assert row is not None and row.status == "warn"
        assert "wfc demo --force" in row.fix_hint
        _stamp(target).unlink(missing_ok=True)
