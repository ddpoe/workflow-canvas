"""register-env --from <file> --dry-run renders what the registration would build.

``wfc register-env <name> --from <file> --backend conda|pixi --dry-run`` writes
``.wfc/build/<name>/Dockerfile`` and stops before Docker. The file is read as
the real registration reads it: an env's own ``packages.lock`` is split on the
pip-freeze delimiter, so its pip set shows as the recipe's pip layer, and a
plain explicit list or ``pixi.lock`` (no delimiter) renders with no pip layer.
The dry run writes nothing else: no record, no captures, no log.

Every call runs the production CLI path (``cli_main`` -> ``register_env``)
with Docker faked at Environments' build boundary (``tests.fixtures.fakes``);
the fake build records the build context it is handed, which is the recipe
the real registration staged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_readiness_probes,
)
from wfc.environments.packages import PIP_FREEZE_DELIMITER

CONDA_LIST = ("@EXPLICIT\n"
              "https://conda.anaconda.org/conda-forge/linux-64/"
              "python-3.11.0-h.conda#0a\n")
PIXI_LOCK = """\
version: 5
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda
  name: python
  version: 3.11.0
"""
PIXI_TOML = '[project]\nname = "tools"\n'
PIP_SET = "numpy==1.24.0\nrequests==2.31.0\n"
FIRST_SOURCE = {
    "conda": {"explicit_list_content": CONDA_LIST,
              "pip_freeze_content": PIP_SET},
    "pixi": {"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
             "pip_freeze_content": PIP_SET},
}
# What a recipe with a pip layer copies in; a recipe without one never names it.
PIP_LAYER = "COPY pip-freeze.txt"


class _Docker:
    """Docker faked at the build boundary; each build's context is recorded."""

    def __init__(self, monkeypatch):
        self.contexts: list[dict[str, str]] = []

        def build(build_dir, *args, **kwargs):
            self.contexts.append({p.name: p.read_text(encoding="utf-8")
                                  for p in Path(build_dir).iterdir()
                                  if p.is_file()})

        stub_docker_build(monkeypatch, build)
        stub_docker_image_content(monkeypatch)
        stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
        stub_readiness_probes(monkeypatch, docker="ok")


def _register_from(name: str, path: Path, backend: str, *extra: str) -> int:
    """``wfc register-env <name> --from <path> --backend <backend> [extra]``."""
    from wfc.cli import cli_main

    return cli_main(["register-env", name, "--from", str(path),
                     "--backend", backend, *extra])


def _own_lock(project: Path, name: str) -> Path:
    """The ``packages.lock`` in *name*'s captures folder."""
    record = json.loads((project / ".wfc" / "envs.json").read_text(
        encoding="utf-8"))["envs"][name]
    return (project / ".wfc" / "captures" / name / record["env_fingerprint"]
            / "packages.lock")


def _tree(project: Path) -> dict[str, bytes]:
    """Every file under *project* outside ``.git``, with its bytes."""
    return {p.relative_to(project).as_posix(): p.read_bytes()
            for p in project.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(project).parts}


def _dry_run(project: Path, name: str, path: Path, backend: str,
             docker: _Docker, capsys) -> str:
    """Run the dry run; check it wrote only its Dockerfile and ran no build.

    Returns:
        The Dockerfile the dry run wrote.
    """
    before = _tree(project)
    builds = len(docker.contexts)

    rc = _register_from(name, path, backend, "--dry-run")

    out, err = capsys.readouterr()
    assert rc == 0, err
    dockerfile = project / ".wfc" / "build" / name / "Dockerfile"
    assert out.strip() == str(dockerfile.resolve())
    assert len(docker.contexts) == builds
    after = _tree(project)
    added = {k for k in after if k not in before}
    assert added == {f".wfc/build/{name}/Dockerfile"}
    assert {k: v for k, v in after.items() if k in before} == before
    return dockerfile.read_text(encoding="utf-8")


@workflow(purpose="register-env --dry-run of an env's own conda or pixi "
                  "packages.lock renders the recipe's pip layer for its pip "
                  "set, writes only the Dockerfile (no record, captures or "
                  "log, no build), and the Dockerfile is the one the real "
                  "registration from the same file stages")
@pytest.mark.parametrize("backend", ["conda", "pixi"])
def test_dry_run_of_an_envs_own_packages_lock_renders_its_pip_layer(
        tmp_project, monkeypatch, capsys, backend):
    from wfc import environments as envs_mod

    docker = _Docker(monkeypatch)

    口 = Step(step_num=1, name="Register an env with a pip set",
             purpose="Its captures folder holds packages.lock: the list or "
                     "lock, the delimiter, the pip set")
    envs_mod.register(name="first", backend=backend,
                      source=dict(FIRST_SOURCE[backend]),
                      project_dir=tmp_project)
    own_lock = _own_lock(tmp_project, "first")
    assert PIP_FREEZE_DELIMITER + PIP_SET in own_lock.read_text(encoding="utf-8")
    capsys.readouterr()  # the first registration's report

    口 = Step(step_num=2, name="Dry-run a second env --from that packages.lock",
             purpose="The Dockerfile has the pip layer; only it is written and "
                     "no build runs")
    dockerfile = _dry_run(tmp_project, "second", own_lock, backend, docker,
                          capsys)
    assert PIP_LAYER in dockerfile

    口 = Step(step_num=3, name="The real registration stages the same recipe",
             purpose="Registering from the same file builds from the "
                     "Dockerfile the dry run printed")
    assert _register_from("second", own_lock, backend) == 0, \
        capsys.readouterr().err
    assert docker.contexts[-1]["Dockerfile"] == dockerfile


@workflow(purpose="register-env --dry-run of a plain conda explicit list or "
                  "pixi.lock (no pip-freeze delimiter) renders the recipe "
                  "with no pip layer, as the generator renders it with an "
                  "empty pip freeze, writes only the Dockerfile, and agrees "
                  "with the real registration's staged recipe")
@pytest.mark.parametrize(("backend", "filename", "text"), [
    ("conda", "conda-linux-64.lock", CONDA_LIST),
    ("pixi", "pixi.lock", PIXI_LOCK),
])
def test_dry_run_of_a_file_with_no_pip_section_renders_as_before(
        tmp_project, monkeypatch, capsys, tmp_path_factory,
        backend, filename, text):
    from wfc.environments.dockerfiles import generate_for_backend

    docker = _Docker(monkeypatch)
    path = tmp_path_factory.mktemp("from") / filename
    path.write_text(text, encoding="utf-8", newline="\n")

    dockerfile = _dry_run(tmp_project, "tools", path, backend, docker, capsys)

    assert PIP_LAYER not in dockerfile
    assert dockerfile == generate_for_backend(
        backend, env_name="tools", pip_freeze_content="")
    assert _register_from("tools", path, backend) == 0, capsys.readouterr().err
    assert docker.contexts[-1]["Dockerfile"] == dockerfile
