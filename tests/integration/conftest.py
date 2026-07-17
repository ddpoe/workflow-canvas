"""Conftest for Tier 3 integration tests (Docker-dependent).

Provides:
    repo_root: path to the worktree root (contains pyproject.toml, wfc/, etc.)
    minimal_image: session-scoped fixture that builds the minimal wfc image
        from tests/fixtures/Dockerfile.minimal and returns its digest
        (sha256 hex, without the ``sha256:`` prefix). Shared across all
        integration test files in a single session so the image build cost
        is paid exactly once.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


# Tag includes a registry-host segment ("local/") so the digest-pinned ref
# "docker://local/wfc-test-minimal@sha256:<hex>" satisfies the strict shape
# that wfc.envs.validate_container_ref enforces (host + "/" + path + "@sha256").
# docker run still resolves this against the locally-built image because the
# tag is applied to the image when we build it below.
IMAGE_TAG = "local/wfc-test-minimal:latest"
IMAGE_REPO = "local/wfc-test-minimal"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Resolve the repository root (the worktree dir containing pyproject.toml).

    tests/integration/conftest.py lives two directories below the repo root.
    """
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def minimal_image(fixture_container_image: str) -> str:
    """Digest of the minimal wfc image (sha256 hex, no ``sha256:`` prefix).

    Delegates to the single session-scoped builder
    ``tests.conftest.fixture_container_image``. Both fixtures build the same
    ``local/wfc-test-minimal:latest`` tag from ``tests/fixtures/Dockerfile.minimal``;
    sharing one builder avoids a second ``docker build`` that would retag the
    image and leave the first build's captured ``.Id`` digest dangling (Docker
    then can't resolve ``local/wfc-test-minimal@sha256:<stale-id>`` and
    ``docker run`` fails with "Unable to find image" mid-session).
    """
    return fixture_container_image


# Tier-1 image: host wfc + the pure-stdlib wfc-client (ADR-020). Built from
# tests/fixtures/Dockerfile.client. Used by the Tier-1 end-to-end test, where
# a user method does `import wfc_client as wfc`. The Tier-2 parity test
# deliberately uses minimal_image (no wfc-client) instead.
CLIENT_IMAGE_TAG = "local/wfc-test-client:latest"


@pytest.fixture(scope="session")
def client_image(repo_root: Path) -> str:
    """Build the Tier-1 wfc-client image once per session; return its digest.

    Builds ``tests/fixtures/Dockerfile.client`` (host ``wfc`` + the
    standalone ``wfc-client`` package) so a Tier-1 ``@wfc.method`` user
    script can ``import wfc_client as wfc`` inside the container. The digest
    is captured via ``docker image inspect`` so the env manifest can pin an
    immutable ``image@sha256:...`` ref.

    Returns:
        The image digest as a bare sha256 hex string (no ``sha256:`` prefix).
    """
    dockerfile = repo_root / "tests" / "fixtures" / "Dockerfile.client"
    assert dockerfile.exists(), f"Missing Tier-1 client Dockerfile: {dockerfile}"

    build = subprocess.run(
        ["docker", "build", "-t", CLIENT_IMAGE_TAG,
         "-f", str(dockerfile), str(repo_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build of Tier-1 client image failed:\n"
            f"STDOUT:\n{build.stdout}\nSTDERR:\n{build.stderr}"
        )

    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", CLIENT_IMAGE_TAG],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    if inspect.returncode != 0:
        pytest.fail(
            "docker image inspect failed for Tier-1 client image:\n"
            f"STDOUT:\n{inspect.stdout}\nSTDERR:\n{inspect.stderr}"
        )

    image_id = inspect.stdout.strip()
    return image_id[len("sha256:"):] if image_id.startswith("sha256:") else image_id
