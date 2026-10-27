"""Real-build verification of the uv backend.

A tiny uv project is locked by the host's uv, registered through
``wfc register-env --backend uv`` (the host checks, ``uv export``, a real
``docker build`` from the pinned python base), and its package imports
under the recorded interpreter inside the built image. The pinned bases
themselves are checked by
``test_pixi_dockerfile_builds.py::test_pinned_base_digests_exist_in_registries``,
which iterates the backend table.

``integration``-marked (deselected by default); skips without a reachable
Docker daemon or without uv on PATH (CI has none).
"""

from __future__ import annotations

import subprocess

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker, requires_uv

pytestmark = [pytest.mark.integration, requires_docker, requires_uv]

_PYPROJECT = """\
[project]
name = "uvsmoke"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["six==1.16.0"]

[dependency-groups]
docs = ["idna==3.7"]
"""


@pytest.mark.slow
@workflow(purpose="A uv project registered with --backend uv builds from its "
                  "Python minor's pinned base, and its packages, including a "
                  "chosen group's, import under /opt/venv/bin/python in the "
                  "image")
def test_a_uv_env_builds_and_its_packages_import_in_the_image(
        tmp_project, tmp_path):
    from wfc.environments import daemon_ref, manifest, verbs

    口 = Step(step_num=1, name="Lock a tiny uv project on the host",
             purpose="pyproject with one dependency and one group, Python "
                     "pinned to 3.12, locked by the host's uv")
    project = tmp_path / "uvsmoke"
    project.mkdir()
    (project / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (project / ".python-version").write_text("3.12\n", encoding="utf-8")
    lock = subprocess.run(["uv", "lock"], cwd=project, capture_output=True,
                          text=True, timeout=600)
    assert lock.returncode == 0, lock.stderr

    口 = Step(step_num=2, name="Register it with --backend uv",
             purpose="The host checks and the export, then a real docker build")
    # requires_docker has probed the daemon; the gate lets the build run.
    rc = verbs.register_env("uvsmoke", backend="uv", from_path=str(project),
                            groups=["docs"], docker_gate=lambda: None)
    assert rc == 0
    record = manifest.get("uvsmoke", tmp_project)
    assert record.python == "/opt/venv/bin/python"

    口 = Step(step_num=3, name="Import the packages in the image",
             purpose="The recorded interpreter imports the dependency and the "
                     "group's package, and is CPython 3.12")
    run = subprocess.run(
        ["docker", "run", "--rm", "--pull=never", daemon_ref(record.container),
         record.python, "-c",
         "import sys, six, idna; print(sys.version_info[:2], six.__version__, "
         "idna.__version__)"],
        capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    assert "(3, 12) 1.16.0 3.7" in run.stdout, run.stdout
