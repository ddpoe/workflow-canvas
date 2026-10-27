"""wfc doctor's env-image rows: the registry, and the versions that cannot run.

After the four run-readiness rows, ``wfc doctor`` prints two more under their
own heading: ``registry`` (no base is fine; with a base, the registry must
answer Docker's credentials and every image on this machine must be pushed)
and ``versions`` (every method version whose image is neither on this
machine nor recorded as pushed, each with the commands that recreate it).
Both only warn, so doctor's exit rule is unchanged, and neither joins
``run_all_checks``, which ``wfc init``'s summary and the canvas's run gate
share.

Projects come from the scenario harness (``build_project``: one env, method
versions registered through production); the base is set through production
``set_registry_url`` and pushes go through ``wfc push-env``. Docker and the
registry are faked at the ``wfc.environments.docker`` wrappers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_docker_image_inspect,
    stub_docker_push,
    stub_docker_read_manifest,
    stub_docker_tag,
    stub_readiness_probes,
)
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

ENV = "image-io"
BASE = "ghcr.io/lab/tools-project"
HELD = "sha256:" + "c" * 64


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _project(git_project, monkeypatch):
    """One env and one method whose v1 records the env's current image."""
    return build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id="pipe-doctor-rows", env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)


def _set_base(root: Path) -> None:
    from wfc.persistence import set_registry_url

    set_registry_url(root, BASE)
    _git(root, "add", "--", ".wfc/wf-canvas.toml")
    _git(root, "commit", "-q", "-m", "set the registry base")


def _doctor(monkeypatch, capsys, root: Path) -> tuple[int, str]:
    from wfc.cli import cli_main

    stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok",
                          samples="ok")
    monkeypatch.chdir(root)
    capsys.readouterr()
    rc = cli_main(["doctor"])
    return rc, capsys.readouterr().out


def _row(out: str, name: str) -> str:
    """The row named *name* and its fix-hint line, as doctor printed them."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("  [") and line[9:].split()[0] == name)
    rest = lines[at + 1:at + 2]
    hint = rest if rest and "->" in rest[0] else []
    return "\n".join([lines[at], *hint])


def _never_the_registry(ref):
    raise AssertionError(f"the registry must not be asked ({ref})")


@workflow(purpose="A method version whose image is neither on this machine "
                  "nor pushed is listed by wfc doctor's versions row with the "
                  "command that recreates the image and wfc register-method; "
                  "the row warns and doctor still exits 0; with Docker not "
                  "answering the row says it cannot tell, and with the image "
                  "back on the machine it is ok")
def test_doctor_lists_the_versions_that_cannot_run(git_project, monkeypatch,
                                                   capsys):
    """Doctor half: the versions row names what cannot run."""
    project = _project(git_project, monkeypatch)
    root = project.root
    stub_docker_read_manifest(monkeypatch, _never_the_registry)

    口 = Step(step_num=1, name="The image is gone from this machine",
             purpose="Never pushed and the daemon no longer holds it")
    stub_docker_image_inspect(monkeypatch,
                              ImageNotFoundError("Error: No such image"))
    rc, out = _doctor(monkeypatch, capsys, root)

    口 = Step(step_num=2, name="doctor lists the version and its remedy",
             purpose="A WARN versions row naming the version, the recreate "
                     "command and wfc register-method; exit 0 unchanged")
    assert rc == 0, out
    row = _row(out, "versions")
    assert row.startswith("  [WARN] versions"), row
    assert "/a v1" in row
    assert f"wfc register-env {ENV} --force" in row
    assert "wfc register-method" in row
    assert _row(out, "registry").startswith("  [  OK] registry"), out
    assert out.index("Run-readiness:") < out.index("  [WARN] versions")

    口 = Step(step_num=3, name="Docker does not answer",
             purpose="The row warns that it cannot tell, and never fails")
    stub_docker_image_inspect(monkeypatch, RuntimeError(
        "Cannot connect to the Docker daemon"))
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "versions")
    assert row.startswith("  [WARN] versions") and "cannot tell" in row, row

    口 = Step(step_num=4, name="The image is back on this machine",
             purpose="Every version can run here: the row is ok")
    stub_docker_image_inspect(monkeypatch, HELD)
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0
    assert _row(out, "versions").startswith("  [  OK] versions"), out


@workflow(purpose="wfc doctor's registry row: ok with no base and the "
                  "registry never asked; with a base, a registry refusing "
                  "Docker's credentials warns naming docker login <host> and "
                  "wfc share; a local unpushed image warns naming it and wfc "
                  "push-env --all; once everything is pushed the pushed "
                  "image's own ref is the probe and the row is ok")
def test_doctor_registry_row_states(git_project, monkeypatch, capsys):
    """The registry row through no base, no login, unpushed, all pushed."""
    from wfc.execution.readiness import check_registry

    project = _project(git_project, monkeypatch)
    root = project.root
    stub_docker_image_inspect(monkeypatch, HELD)

    口 = Step(step_num=1, name="No registry base",
             purpose="ok; nothing asks the registry")
    stub_docker_read_manifest(monkeypatch, _never_the_registry)
    result = check_registry(root)
    assert result.status == "ok", result
    assert "No registry" in result.message

    口 = Step(step_num=2, name="Base set, Docker not logged in",
             purpose="warn naming docker login <host> and wfc share")
    _set_base(root)
    stub_docker_read_manifest(monkeypatch, "unauthorized")
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "registry")
    assert row.startswith("  [WARN] registry"), row
    assert "docker login ghcr.io" in row and "wfc share" in row

    口 = Step(step_num=3, name="Registry answers, an image is not pushed",
             purpose="warn naming the image's label and wfc push-env --all")
    stub_docker_read_manifest(monkeypatch, "found")
    result = check_registry(root)
    assert result.status == "warn", result
    assert f"{ENV}:fp-" in result.message
    assert "wfc push-env --all" in result.fix_hint
    assert "wfc share" in result.fix_hint

    口 = Step(step_num=4, name="Everything pushed",
             purpose="push-env --all records the push; the probe asks for "
                     "the pushed image's digest ref and the row is ok")
    from wfc.cli import cli_main

    stub_docker_tag(monkeypatch)
    stub_docker_push(monkeypatch)
    assert cli_main(["push-env", "--all", "--yes"]) == 0
    asked: list[str] = []

    def found(ref):
        from wfc.environments.docker import RegistryRead

        asked.append(ref)
        return RegistryRead(ref, "found", "{}")

    stub_docker_read_manifest(monkeypatch, found)
    result = check_registry(root)
    assert result.status == "ok", result
    assert len(asked) == 1 and asked[0].startswith(f"{BASE}/{ENV}@sha256:")


def test_doctor_rows_outside_a_project_ask_nothing(tmp_path, monkeypatch):
    """Outside a project both rows warn without asking Docker or a registry."""
    from tests.fixtures.fakes import refuse_docker
    from wfc.execution.readiness import check_registry, check_runnable_versions

    refuse_docker(monkeypatch)
    for row in (check_registry(tmp_path), check_runnable_versions(tmp_path)):
        assert row.status == "warn", row
        assert "No wfc project here" in row.message


def test_run_all_checks_keeps_its_five_rows(monkeypatch, tmp_path):
    """`wfc init`'s summary and the canvas's run gate keep their five probe
    rows; the env-image rows are not among them."""
    from wfc.execution import run_all_checks

    stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok",
                          samples="ok")
    assert [r.name for r in run_all_checks(tmp_path)] == [
        "git", "dvc", "docker", "samples", "views"]
