"""Integration test: a generated user-env image has no wfc (ADR-019 G.2, US-3).

Builds a real user-env image from a generator-produced Dockerfile and proves
the no-wfc-leakage guarantee on the actual artifact (not on a Dockerfile
string): ``python -c "import wfc"`` inside the built image must exit non-zero
(ModuleNotFoundError), because ADR-020 makes the Tier 2 env-var + file contract
canonical and user-env images never pre-install wfc.

This is the live-artifact complement to the pure-function string assertions in
``tests/test_user_env_dockerfile_contract.py``.

Selection: marked ``integration`` (deselected by the default
``-m "not slow and not integration"`` addopts) AND guarded by ``requires_docker``
so it skips cleanly where no Docker daemon is reachable. Run explicitly with
``pytest -m integration tests/integration/``.

Annotation Tier 2 (``@workflow(purpose=...)``) per ``pm_mvp::docs.test-policy``.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from dflow.core.decorators import workflow

from wfc.dockerfiles import inherit
from tests.conftest import requires_docker


# Deselected by default (integration) + skipped where Docker is absent.
pytestmark = [pytest.mark.integration, requires_docker]


# A user-env freeze that pulls NOTHING wfc-related — just the stdlib interpreter
# plus a single tiny, fast-to-install dep so the build exercises the
# `pip install --no-deps` layer the generator emits.
USER_FREEZE = "wheel==0.43.0\n"


def _build_user_env_image(build_ctx: Path, tag: str) -> None:
    """Render the inherit user-env Dockerfile into *build_ctx* and build it.

    Args:
        build_ctx: A directory used as the docker build context. The
            generator-produced Dockerfile and its ``pip-freeze.txt`` are
            written here.
        tag: The image tag to apply to the built image.
    """
    dockerfile_text = inherit.generate(
        env_name="userenv",
        pip_freeze_content=USER_FREEZE,
    )
    (build_ctx / "Dockerfile").write_text(dockerfile_text)
    (build_ctx / "pip-freeze.txt").write_text(USER_FREEZE)

    build = subprocess.run(
        ["docker", "build", "-t", tag, str(build_ctx)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build of user-env image failed:\n"
            f"STDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
        )


@workflow(
    purpose="A user-env image built from a generator-produced Dockerfile "
            "contains no wfc: `python -c \"import wfc\"` exits non-zero inside "
            "the built image (ADR-020 no-wfc-in-image guarantee, proven on a "
            "real artifact).",
)
def test_user_env_image_has_no_wfc(tmp_path: Path):
    tag = f"local/wfc-userenv-nowfc-test:{uuid.uuid4().hex[:8]}"
    try:
        _build_user_env_image(tmp_path, tag)

        run = subprocess.run(
            ["docker", "run", "--rm", tag, "python", "-c", "import wfc"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert run.returncode != 0, (
            "`import wfc` should FAIL inside a user-env image (no wfc "
            f"pre-installed), but it succeeded.\nSTDOUT:\n{run.stdout}\n"
            f"STDERR:\n{run.stderr}"
        )
        assert "ModuleNotFoundError" in run.stderr or "No module named" in run.stderr, (
            "Expected a ModuleNotFoundError for `wfc`; got:\n"
            f"STDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag],
            capture_output=True,
            text=True,
            timeout=60,
        )
