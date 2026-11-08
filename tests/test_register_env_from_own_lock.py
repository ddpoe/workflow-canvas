"""register-env --from takes an env's own packages.lock back.

An env's captures folder holds ``packages.lock``: the conda explicit list or
the pixi lock, the pip-freeze delimiter, then the pip set. ``wfc register-env
<name> --from <that file> --backend conda|pixi`` stages the part before the
delimiter as the list or lock and the part after as the pip freeze the
recipe's pip layer installs, so the new env's build context and package list
are the first env's. A plain explicit list or ``pixi.lock`` (no delimiter)
stages as it always has: the file whole, an empty pip freeze.

Every registration runs the production CLI path (``cli_main`` ->
``register_env`` -> ``wfc.environments.register``) with Docker faked at
Environments' build boundary (``tests.fixtures.fakes``); the fake build
records the build context it is handed.
"""

from __future__ import annotations

import hashlib
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

# The build-context file each backend stages its list or lock as.
LOCK_FILE = {"conda": "explicit-list.txt", "pixi": "pixi.lock"}
FIRST_SOURCE = {
    "conda": {"explicit_list_content": CONDA_LIST,
              "pip_freeze_content": PIP_SET},
    "pixi": {"pixi_lock_content": PIXI_LOCK, "pixi_toml_content": PIXI_TOML,
             "pip_freeze_content": PIP_SET},
}


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


def _register_from(name: str, path: Path, backend: str) -> int:
    """``wfc register-env <name> --from <path> --backend <backend>``."""
    from wfc.cli import cli_main

    return cli_main(["register-env", name, "--from", str(path),
                     "--backend", backend])


def _record(project: Path, name: str) -> dict:
    return json.loads((project / ".wfc" / "envs.json").read_text(
        encoding="utf-8"))["envs"][name]


def _package_list(project: Path, name: str) -> Path:
    record = _record(project, name)
    return (project / ".wfc" / "captures" / name / record["env_fingerprint"]
            / "packages.lock")


@workflow(purpose="An env's own conda or pixi packages.lock, fed back to "
                  "register-env --from, registers: its pip set is staged as "
                  "the pip freeze the recipe's pip layer installs, the list or "
                  "lock is staged without the delimiter or the pip set, a "
                  "pixi env's pixi.toml beside it is staged too, and the new "
                  "env's package list and source_fingerprint are the first env's")
@pytest.mark.parametrize("backend", ["conda", "pixi"])
def test_an_envs_own_packages_lock_registers_with_its_pip_set(
        tmp_project, monkeypatch, capsys, backend):
    from wfc import environments as envs_mod

    docker = _Docker(monkeypatch)

    口 = Step(step_num=1, name="Register an env with a pip set",
             purpose="Its captures folder holds packages.lock: the list or "
                     "lock, the delimiter, the pip set")
    envs_mod.register(name="first", backend=backend,
                      source=dict(FIRST_SOURCE[backend]),
                      project_dir=tmp_project)
    own_lock = _package_list(tmp_project, "first")
    assert PIP_FREEZE_DELIMITER + PIP_SET in own_lock.read_text(encoding="utf-8")
    first_context = docker.contexts[-1]

    口 = Step(step_num=2, name="Register a second env --from that packages.lock",
             purpose="The production CLI path, file mode")
    rc = _register_from("second", own_lock, backend)
    assert rc == 0, capsys.readouterr().err

    口 = Step(step_num=3, name="The pip set is staged for the pip layer",
             purpose="pip-freeze.txt holds the pip set; the list or lock holds "
                     "neither the delimiter nor the pip set; the context's "
                     "source files are the first env's")
    context = docker.contexts[-1]
    lock_text = context[LOCK_FILE[backend]]
    assert context["pip-freeze.txt"] == PIP_SET
    assert PIP_FREEZE_DELIMITER.strip() not in lock_text
    assert "numpy" not in lock_text
    staged = {k: v for k, v in context.items() if k != "Dockerfile"}
    assert staged == {k: v for k, v in first_context.items()
                      if k != "Dockerfile"}
    if backend == "pixi":
        assert context["pixi.toml"] == PIXI_TOML

    口 = Step(step_num=4, name="The package list and fingerprint follow",
             purpose="The second env's packages.lock is the file it was "
                     "registered from, byte for byte, and its md5 is the "
                     "recorded source_fingerprint")
    second_lock = _package_list(tmp_project, "second")
    assert second_lock.read_bytes() == own_lock.read_bytes()
    assert (_record(tmp_project, "second")["source_fingerprint"]
            == hashlib.md5(own_lock.read_bytes()).hexdigest()
            == _record(tmp_project, "first")["source_fingerprint"])


@workflow(purpose="A plain conda explicit list or pixi.lock, with no "
                  "pip-freeze delimiter, registers --from as it always has: "
                  "the file whole as the list or lock, an empty pip freeze, "
                  "and a package list of the file, the delimiter and nothing "
                  "after it")
@pytest.mark.parametrize(("backend", "filename", "text"), [
    ("conda", "conda-linux-64.lock", CONDA_LIST),
    ("pixi", "pixi.lock", PIXI_LOCK),
])
def test_a_file_with_no_pip_section_stages_as_before(
        tmp_project, monkeypatch, capsys, tmp_path_factory,
        backend, filename, text):
    docker = _Docker(monkeypatch)
    path = tmp_path_factory.mktemp("from") / filename
    path.write_text(text, encoding="utf-8", newline="\n")

    rc = _register_from("tools", path, backend)

    assert rc == 0, capsys.readouterr().err
    context = docker.contexts[-1]
    assert context[LOCK_FILE[backend]] == text
    assert context["pip-freeze.txt"] == ""
    assert "pixi.toml" not in context
    package_list = _package_list(tmp_project, "tools").read_bytes()
    assert package_list == (text + PIP_FREEZE_DELIMITER).encode("utf-8")
    assert (_record(tmp_project, "tools")["source_fingerprint"]
            == hashlib.md5(package_list).hexdigest())
