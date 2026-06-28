"""Docker subprocess boundary for ADR-019 container env backend (Cycle C).

Centralizes every ``docker`` CLI invocation wfc makes during
``wfc register-env`` and the runtime container dispatch (Cycle D). Tests
mock the three functions here rather than ``subprocess.run`` directly,
which keeps the boundary small and the test plan honest.

v1 surface (this cycle):

- :func:`build` — ``docker build -t <tag> <dir>`` with BuildKit enabled.
  ``DOCKER_BUILDKIT=1`` is merged into a copy of ``os.environ`` so the
  caller's PATH / DOCKER_HOST / HOME / proxy vars survive the spawn.
- :func:`image_inspect` — ``docker image inspect <ref> --format '{{.Id}}'``.
  Returns the digest string verbatim, including the ``sha256:`` prefix.
- :func:`pull` — ``docker pull <ref>``. Only used on the BYO branch when
  the image is not already present in the local daemon.

There is no ``push`` function in v1. ADR-019's 2026-05-17 amendment
deferred registry push / pull-from-registry to v1.x; this module will
grow a ``push(ref)`` function then. Do not add it here speculatively.

All functions raise :class:`RuntimeError` on non-zero exit and surface
docker's stderr verbatim in the message — the user sees the real
``unable to resolve image`` / ``COPY failed`` text, not a wrapped
``CalledProcessError``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Union

from axiom_annotations import task


@task(purpose="Run docker build -t <tag> <dir> with BuildKit enabled")
def build(dockerfile_dir: Union[str, Path], tag: str) -> None:
    """Run ``docker build -t <tag> <dockerfile_dir>`` with BuildKit on.

    BuildKit is required by the Cycle B Dockerfile generators (they emit
    ``# syntax=docker/dockerfile:1.4`` and use ``--mount=type=cache``
    instructions). We merge ``DOCKER_BUILDKIT=1`` into a *copy* of the
    process env so the user's PATH, HOME, DOCKER_HOST, HTTP(S)_PROXY, etc.
    survive — passing ``env={'DOCKER_BUILDKIT': '1'}`` alone would strip
    every other variable and break the build on most setups.

    Args:
        dockerfile_dir: Directory containing the ``Dockerfile`` to build.
            Used as docker's build context.
        tag: Image tag to assign to the resulting image (e.g.
            ``"my-env:_wfc-build"``).

    Raises:
        RuntimeError: If docker exits non-zero. The error message
            includes docker's stderr verbatim.
    """
    build_env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    cmd = ["docker", "build", "-t", tag, str(dockerfile_dir)]
    proc = subprocess.run(
        cmd,
        env=build_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker build failed (exit {proc.returncode}):\n{proc.stderr}"
        )


def image_inspect(ref: str) -> str:
    """Return the local image digest for *ref* via ``docker image inspect``.

    The format string ``'{{.Id}}'`` returns the canonical digest string
    including the ``sha256:`` prefix (e.g. ``"sha256:abc123..."``). The
    caller decides whether to strip or attach that prefix when assembling
    a manifest entry.

    Args:
        ref: Image reference understood by the local docker daemon
            (tag, name, or ``<name>@sha256:<digest>``).

    Returns:
        The digest string, including the ``sha256:`` prefix.

    Raises:
        RuntimeError: If docker exits non-zero (typically because the
            image is not present in the local daemon).
    """
    cmd = ["docker", "image", "inspect", ref, "--format", "{{.Id}}"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker image inspect {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    return proc.stdout.strip()


def pull(ref: str) -> None:
    """Run ``docker pull <ref>`` to fetch *ref* into the local daemon.

    Used on the BYO branch when :func:`image_inspect` reports the image
    is not locally resolvable. Generator-backed envs never pull — they
    build the image locally instead.

    Args:
        ref: Image reference to pull. May be a floating-tag ref
            (``reg/img:latest``) or a digest-pinned ref
            (``reg/img@sha256:...``).

    Raises:
        RuntimeError: If docker exits non-zero (network failure,
            unauthorized, unknown image, etc.). The error message
            includes docker's stderr verbatim.
    """
    cmd = ["docker", "pull", ref]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker pull {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
