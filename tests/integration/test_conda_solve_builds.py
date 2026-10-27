"""Real-build verification of a conda env solved for linux-64.

A conda env captured live on Windows or macOS is registered from a spec
pinned to the host's versions. This module hands ``register`` that payload
(what live capture produces; the capture half is covered by
``tests/test_conda_solve_capture.py``) and builds it for real against the
pinned micromamba base: the solve, the read-back of the image's own
explicit list through the hardened copy-out, the record, and the staged
``.wfc/build/`` context.

``integration``-marked (deselected by default), skips cleanly without a
reachable Docker daemon, and ``slow``: the first run solves and downloads a
small python env from conda-forge.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from axiom_annotations import Step, workflow

from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

#: One package line of an explicit list: a linux-64 or noarch URL with an md5.
_PACKAGE_LINE = re.compile(r"^https://\S+/(linux-64|noarch)/\S+#[0-9a-f]{32}$")


def _package_lines(explicit_list: str) -> list[str]:
    """The URL lines of an explicit list, sorted."""
    return sorted(ln.strip() for ln in explicit_list.splitlines()
                  if "://" in ln)


@pytest.mark.slow
@workflow(
    purpose="A conda env solved for linux-64 from a small pinned spec builds "
            "for real; the recorded package list is the image's own linux-64 "
            "explicit list with md5s plus the pip set, its md5 is "
            "source_fingerprint, the captures folder holds the explicit-list "
            "recipe, image.json's solved_for and host/, the warning names "
            "where to look, and .wfc/build/ holds the explicit-list recipe"
)
def test_a_solved_conda_env_records_the_images_list(tmp_project, capsys):
    from wfc import environments as envs_mod
    from wfc import layout
    from wfc.environments.dockerfiles.conda import generate
    from wfc.environments.introspect import (HOST_CAPTURE_SOURCE_KEY,
                                             SOLVE_SOURCE_KEY, SolveSpec)
    from wfc.environments.packages import PIP_FREEZE_DELIMITER

    pip_set = "tomli==2.0.1\n"
    host_files = {
        layout.CAPTURE_HOST_EXPLICIT_LIST_FILENAME:
            "# platform: win-64\r\n@EXPLICIT\r\n",
        layout.CAPTURE_HOST_INFO_FILENAME:
            json.dumps({"platform": "win-64"}) + "\n",
    }

    口 = Step(step_num=1, name="Register the solve payload",
             purpose="Build the solve recipe for a pinned python and six, "
                     "with a pip set, from conda-forge")
    record = envs_mod.register(
        name="solved",
        backend="conda",
        source={
            SOLVE_SOURCE_KEY: SolveSpec(channels=("conda-forge",),
                                        specs=("python=3.12.11", "six=1.17.0")),
            "pip_freeze_content": pip_set,
            HOST_CAPTURE_SOURCE_KEY: host_files,
        },
        project_dir=tmp_project,
    )

    口 = Step(step_num=2, name="The record is the image's linux-64 list",
             purpose="packages.lock is the read-back list, the delimiter and "
                     "the pip set, and its md5 is source_fingerprint")
    folder = layout.env_capture_dir(tmp_project, "solved", record.env_fingerprint)
    lock = (folder / layout.CAPTURE_PACKAGES_FILENAME).read_bytes()
    assert hashlib.md5(lock).hexdigest() == record.source_fingerprint
    explicit_list, pip_part = lock.decode("utf-8").split(PIP_FREEZE_DELIMITER)
    assert pip_part == pip_set
    assert "# platform: linux-64" in explicit_list
    assert "@EXPLICIT" in explicit_list
    packages = _package_lines(explicit_list)
    assert any("/python-3.12.11-" in p for p in packages)
    assert any("/six-1.17.0-" in p for p in packages)
    assert all(_PACKAGE_LINE.match(p) for p in packages), packages

    image = json.loads((folder / layout.CAPTURE_IMAGE_FILENAME).read_text("utf-8"))
    assert image["solved_for"] == "linux-64"
    host = folder / layout.CAPTURE_HOST_DIR_NAME
    assert {p.name: p.read_bytes().decode("utf-8") for p in host.iterdir()} == host_files

    warning = capsys.readouterr().err
    assert "solved for linux-64" in warning
    assert f"{record.env_fingerprint}/{layout.CAPTURE_PACKAGES_FILENAME}" in warning

    口 = Step(step_num=3, name="The build context is the explicit-list recipe",
             purpose="The captures Dockerfile and .wfc/build/<env>/ are the "
                     "explicit-list recipe")
    recipe = generate("solved", pip_set)
    assert (folder / layout.CAPTURE_DOCKERFILE_FILENAME).read_text("utf-8") == recipe
    build_dir = layout.env_build_dir(tmp_project, "solved")
    assert build_dir.joinpath("Dockerfile").read_text("utf-8") == recipe
