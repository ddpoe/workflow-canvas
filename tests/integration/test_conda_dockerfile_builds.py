"""Real-build verification of the conda/micromamba Dockerfile path.

The string-level generator tests (tests/test_conda_dockerfile_guard.py)
assert Dockerfile SHAPE only; nothing there proves the generated file
actually builds against the real pinned micromamba base. This module
registers a python-free (R-only) conda env through the production
``wfc.environments.register`` path with a real ``docker build`` — exercising the
micromamba explicit-list layer, the pip-layer skip for a pip-less env,
and the permissions pass over the real ``/opt/conda`` tree — then runs
the recorded interpreter inside the image by the exact ref shape
run-step dispatches.

Base-digest liveness for BOTH backends is already asserted by
``test_pixi_dockerfile_builds.py::test_pinned_base_digests_exist_in_registries``
(it iterates ``BASES_BY_BACKEND``) — not duplicated here.

``integration``-marked (deselected by default), skips cleanly without a
reachable Docker daemon, and ``slow`` — the first run solves and
downloads a real (minimal) R env; BuildKit-cached afterwards.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from axiom_annotations import workflow

from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


@pytest.mark.slow
@workflow(
    purpose="The generated conda Dockerfile builds end-to-end against the "
            "real pinned micromamba base from a captured explicit list of a "
            "python-free (R-only) env — proving the explicit-list install "
            "layer, the pip-layer skip (an image with no pip cannot build "
            "pip layers), and the permissions pass over the real /opt/conda "
            "tree — and the recorded container ref plus interpreter run "
            "under docker in exactly the form run-step dispatches."
)
def test_generated_conda_dockerfile_builds_r_env_no_pip(tmp_path):
    from wfc import environments as envs_mod
    from wfc.environments.dockerfiles.bases import MICROMAMBA_BASE
    from wfc.environments.introspect import PIP_MISSING_SENTINEL

    (tmp_path / ".wfc").mkdir()

    # Capture a real explicit list INSIDE the pinned base — no host conda
    # needed. The base image's `base` env starts EMPTY; the install step
    # before the export is what gives the export something to emit.
    capture = subprocess.run(
        [
            "docker", "run", "--rm", MICROMAMBA_BASE,
            "bash", "-c",
            "micromamba install -y -n base -c conda-forge r-base >/dev/null"
            " && micromamba env export -n base --explicit --md5",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert capture.returncode == 0, capture.stderr
    explicit_list = capture.stdout
    assert "@EXPLICIT" in explicit_list, explicit_list

    # Register through the production path — one real docker build, no
    # mocks. PIP_MISSING_SENTINEL is exactly what live capture stages for
    # a python-free env; were pip layers generated anyway, this image
    # could not build (no pip exists inside it).
    record = envs_mod.register(
        name="r-smoke",
        backend="conda",
        source={
            "explicit_list_content": explicit_list,
            "pip_freeze_content": PIP_MISSING_SENTINEL,
        },
        python_override="/opt/conda/bin/Rscript",
        project_dir=tmp_path,
    )

    assert re.fullmatch(
        r"docker://local/r-smoke@sha256:[0-9a-f]{64}", record.container
    ), record.container
    assert record.python == "/opt/conda/bin/Rscript"

    # Run the recorded interpreter by the exact ref run-step passes to
    # docker. register records the built image's {{.Id}}, and
    # `local/r-smoke@sha256:<Id>` resolves only under Docker's containerd
    # image store — on a classic-graphdriver daemon this run fails even
    # though the build succeeded. That failure reproduces a REAL run-step
    # failure (run-step dispatches the identical ref), so treat it as
    # signal, not flakiness.
    ref = record.container.removeprefix("docker://")
    run_proc = subprocess.run(
        ["docker", "run", "--rm", ref, record.python, "--version"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run_proc.returncode == 0, run_proc.stderr
    # The env's r-base version floats at solve time and Rscript's banner
    # changed across R releases: newer R prints "Rscript (R) version X",
    # older prints "R scripting front-end version X". Either proves the
    # recorded path is a real Rscript.
    banner = run_proc.stdout + run_proc.stderr
    assert (
        "Rscript (R) version" in banner or "R scripting front-end" in banner
    ), (
        run_proc.stdout,
        run_proc.stderr,
    )
