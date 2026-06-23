"""Integration test: user-env image digest invariance (ADR-019 G.2, US-3).

Builds a user-env image twice from the SAME input (same generator-produced
Dockerfile, same base, same pip-freeze) and asserts the two builds resolve to
the same image digest. This proves the acceptance criterion "same input
lockfile/freeze rebuilds to the same image digest" on a real artifact.

The floating ``python:X.Y-slim`` base is ``docker pull``-ed once up front so
both builds start from the identical locally-cached base layer — a tag could
otherwise be re-resolved against the registry mid-test, which is upstream drift,
not a property of the generator. Pinning the cached base isolates the
determinism question to the generated recipe.

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


USER_FREEZE = "wheel==0.43.0\n"

# The inherit backend's floating minor tag. Pulled once up front (see the
# test) so both builds resolve it to the same locally-cached base layer.
BASE_TAG = "python:3.11-slim"


def _build(build_ctx: Path, tag: str) -> str:
    """Render + build the inherit user-env image; return its image ID digest.

    Args:
        build_ctx: Directory used as the docker build context.
        tag: Tag to apply to the built image.

    Returns:
        The image ID (``sha256:<hex>``) as reported by ``docker image inspect``.
    """
    dockerfile_text = inherit.generate(
        env_name="userenv",
        pip_freeze_content=USER_FREEZE,
        base_image=BASE_TAG,
    )
    (build_ctx / "Dockerfile").write_text(dockerfile_text)
    (build_ctx / "pip-freeze.txt").write_text(USER_FREEZE)

    build = subprocess.run(
        ["docker", "build", "--no-cache", "-t", tag, str(build_ctx)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build failed:\n"
            f"STDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
        )

    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect.returncode != 0:
        pytest.fail(
            "docker image inspect failed:\n"
            f"STDOUT:\n{inspect.stdout}\nSTDERR:\n{inspect.stderr}"
        )
    return inspect.stdout.strip()


@workflow(
    purpose="Building a user-env image twice from the same input "
            "(same generator-produced Dockerfile + pinned base + freeze) "
            "yields the same image digest — same input rebuilds reproducibly.",
)
def test_user_env_image_digest_is_invariant(tmp_path: Path):
    # Pull the base once so both builds start from the same cached layer;
    # this removes registry re-resolution as a source of digest drift.
    pull = subprocess.run(
        ["docker", "pull", BASE_TAG],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if pull.returncode != 0:
        pytest.fail(
            f"docker pull {BASE_TAG} failed:\n"
            f"STDOUT:\n{pull.stdout}\nSTDERR:\n{pull.stderr}"
        )

    ctx_a = tmp_path / "a"
    ctx_b = tmp_path / "b"
    ctx_a.mkdir()
    ctx_b.mkdir()

    tag_a = f"local/wfc-userenv-digest-a:{uuid.uuid4().hex[:8]}"
    tag_b = f"local/wfc-userenv-digest-b:{uuid.uuid4().hex[:8]}"
    try:
        digest_a = _build(ctx_a, tag_a)
        digest_b = _build(ctx_b, tag_b)
        assert digest_a == digest_b, (
            "Same input must rebuild to the same image digest, but the two "
            f"builds differed:\n  build A: {digest_a}\n  build B: {digest_b}"
        )
    finally:
        for tag in (tag_a, tag_b):
            subprocess.run(
                ["docker", "image", "rm", "-f", tag],
                capture_output=True,
                text=True,
                timeout=60,
            )
