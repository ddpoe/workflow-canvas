"""wfc doctor's bind row: does a step's container see the project?

``wfc doctor`` runs the launch's bind check itself, in the image of the first
env in the manifest whose image is on this machine, and shows the verdict as
the ``binds`` row under "Env images:": visible is ``ok``; a bind that came up
empty is ``fail`` (doctor exits 1), naming the bind and the fix; no env image
here, Docker not answering, or a check that could not run is a row that says
it was not checked and never fails. The row is doctor's only: it is not in
``run_all_checks``, which ``wfc init``'s summary and the canvas gate share.

Projects come from the scenario harness (``build_project``: one env, method
versions registered through production). Docker is faked at its edge: the
daemon's image inspect (``stub_docker_image_inspect``) and the check's
container run (``stub_bind_probe``; the suite's default answers visible).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_bind_probe,
    stub_docker_image_inspect,
    stub_readiness_probes,
)
from tests.fixtures.routes import init_test_project
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments.docker import ImageNotFoundError

ENV = "image-io"
HELD = "sha256:" + "c" * 64


def _project(git_project, monkeypatch):
    """One env and one method whose v1 records the env's current image."""
    return build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id="pipe-doctor-binds", env_name=ENV,
                 env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)


def _doctor(monkeypatch, capsys, root: Path, *flags: str) -> tuple[int, str]:
    """Run ``wfc doctor`` in *root* with the host-tool probes ok."""
    from wfc.cli import cli_main

    stub_readiness_probes(monkeypatch, git="ok", dvc="ok", docker="ok",
                          samples="ok")
    monkeypatch.chdir(root)
    capsys.readouterr()
    rc = cli_main(["doctor", *flags])
    return rc, capsys.readouterr().out


def _row(out: str, name: str) -> str:
    """The row named *name* and its fix-hint line, as doctor printed them."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines)
              if line.startswith("  [") and line[9:].split()[0] == name)
    rest = lines[at + 1:at + 2]
    hint = rest if rest and "->" in rest[0] else []
    return "\n".join([lines[at], *hint])


def _never_started(argv, *, timeout):
    raise AssertionError(f"no container may be started ({argv})")


def _current_image_ref(root: Path) -> str:
    """The daemon ref of the env's current image, as a step would be handed it."""
    from wfc.environments import daemon_ref, list_envs

    record = dict(list_envs(root))[ENV]
    return daemon_ref(record.container)


@workflow(purpose="wfc doctor's binds row runs the launch's container check in "
                  "the env's image: a visible project is ok and doctor exits "
                  "0; an empty project bind is a FAIL row naming the bind, its "
                  "host path, a local disk, the archive and macOS file sharing, "
                  "and doctor exits 1 (its JSON not ok); a check that cannot "
                  "run is a WARN row saying it was not checked, exit 0")
def test_doctor_binds_row_maps_the_check_verdicts(git_project, monkeypatch,
                                                  capsys):
    """The three verdicts of the check, as doctor's row and exit code."""
    project = _project(git_project, monkeypatch)
    root = project.root
    stub_docker_image_inspect(monkeypatch, HELD)

    口 = Step(step_num=1, name="The container sees the project",
             purpose="The check answers visible: the row is ok, exit 0")
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "binds")
    assert row.startswith("  [  OK] binds"), row
    assert ENV in row
    assert out.index("Env images:") < out.index("  [  OK] binds")

    口 = Step(step_num=2, name="The project bind comes up empty",
             purpose="A FAIL row naming /work, the project's host path and the "
                     "fix; doctor exits 1 and its JSON says not ok")
    stub_bind_probe(monkeypatch, "empty")
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 1, out
    row = _row(out, "binds")
    assert row.startswith("  [FAIL] binds"), row
    assert f"{root.as_posix()} at /work" in row
    assert "local disk" in row and "wfc init --archive" in row
    assert "macOS" in row and "file sharing" in row
    assert "`docker" not in row
    rc, out = _doctor(monkeypatch, capsys, root, "--json")
    assert rc == 1
    report = json.loads(out)
    assert report["ok"] is False
    binds = next(item for item in report["env_images"] if item["name"] == "binds")
    assert binds["status"] == "fail"
    assert all(item["name"] != "binds" for item in report["checks"])

    口 = Step(step_num=3, name="The check cannot run",
             purpose="Docker refuses the container: a WARN row saying it was "
                     "not checked, exit 0; the check ran in the env's current "
                     "image")
    started: list[list[str]] = []

    def refused(argv, *, timeout):
        started.append(list(argv))
        return subprocess.CompletedProcess(
            argv, 125, stdout="", stderr="docker: exec: \"/bin/sh\": not found\n")

    stub_bind_probe(monkeypatch, refused)
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "binds")
    assert row.startswith("  [WARN] binds") and "Not checked" in row, row
    assert len(started) == 1
    assert _current_image_ref(root) in started[0]


@workflow(purpose="With no env image on this machine, or Docker not "
                  "answering, wfc doctor's binds row says it was not checked "
                  "and starts no container; doctor still exits 0")
def test_doctor_binds_row_not_checked_without_an_image(git_project, monkeypatch,
                                                       capsys):
    """No image here, then Docker down: not checked, never fail."""
    project = _project(git_project, monkeypatch)
    root = project.root
    stub_bind_probe(monkeypatch, _never_started)

    口 = Step(step_num=1, name="The env's image is not on this machine",
             purpose="WARN, not checked, no container started, exit 0")
    stub_docker_image_inspect(monkeypatch, ImageNotFoundError("Error: No such image"))
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "binds")
    assert row.startswith("  [WARN] binds") and "Not checked" in row, row
    assert "on this machine" in row

    口 = Step(step_num=2, name="Docker does not answer",
             purpose="WARN naming Docker, not checked, exit 0")
    stub_docker_image_inspect(monkeypatch, RuntimeError(
        "Cannot connect to the Docker daemon"))
    rc, out = _doctor(monkeypatch, capsys, root)
    assert rc == 0, out
    row = _row(out, "binds")
    assert row.startswith("  [WARN] binds") and "Docker did not answer" in row, row


def test_binds_row_with_no_env_or_no_project_starts_nothing(
        git_project, tmp_path_factory, monkeypatch):
    """No env registered: ok, not checked. Outside a project: the no-project warn."""
    from wfc.execution.readiness import check_container_binds
    from wfc.persistence import reset_engine

    stub_bind_probe(monkeypatch, _never_started)
    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    reset_engine()
    init_test_project(git_project)
    row = check_container_binds(git_project)
    reset_engine()
    assert row.status == "ok" and row.message.startswith("Not checked"), row
    outside = tmp_path_factory.mktemp("elsewhere")
    row = check_container_binds(outside)
    assert row.status == "warn" and "No wfc project here" in row.message, row
