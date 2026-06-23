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
def minimal_image(repo_root: Path) -> str:
    """Build the minimal wfc image once per session; return its digest.

    Digest is captured via ``docker image inspect`` so the env manifest
    can reference an immutable ``image@sha256:...`` ref (matches the
    contract enforced by ``capture_env_content``).
    """
    dockerfile = repo_root / "tests" / "fixtures" / "Dockerfile.minimal"
    assert dockerfile.exists(), f"Missing fixture Dockerfile: {dockerfile}"

    build = subprocess.run(
        [
            "docker", "build",
            "-t", IMAGE_TAG,
            "-f", str(dockerfile),
            str(repo_root),
        ],
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
        [
            "docker", "image", "inspect",
            "--format", "{{.Id}}",
            IMAGE_TAG,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect.returncode != 0:
        pytest.fail(
            "docker image inspect failed:\n"
            f"STDOUT:\n{inspect.stdout}\nSTDERR:\n{inspect.stderr}"
        )

    # Image ID is "sha256:<digest>" — strip the prefix for our ref shape.
    image_id = inspect.stdout.strip()
    if image_id.startswith("sha256:"):
        digest = image_id[len("sha256:"):]
    else:
        digest = image_id

    return digest
