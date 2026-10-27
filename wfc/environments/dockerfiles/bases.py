"""Base-image references for container builds.

Pinning:

- **Every base is digest-pinned** (``<host>/<path>@sha256:<hex>``): pixi's,
  micromamba's, the four Python bases uv projects build on, and the uv
  binary. The build-time tools (``pixi install --locked``, ``micromamba
  install -f``, ``uv pip install``) run inside these images, so the tool
  version is load-bearing for build determinism. Two users on the same wfc
  version building the same env must invoke the same tool binaries.

Bumping the pinned digests is a manual maintainer step on each wfc release —
no Renovate config, no ``wfc refresh-bases`` command. Hand-edit the constant
below; make sure the new digest actually exists in the registry before
committing.

Per-env overrides remain for every backend via
``wfc register-env <name> --base-image "...@sha256:..."``.
"""

from __future__ import annotations

from typing import Final

# -----------------------------------------------------------------------------
# Pixi base — used when method.yaml declares pixi.toml + pixi.lock
# -----------------------------------------------------------------------------
# ghcr.io/prefix-dev/pixi:0.66.0, digest verified against ghcr.io on
# 2026-07-21. The tag is kept alongside the digest for readability; the
# digest is what docker resolves. The in-container pixi version must be
# able to read the lock files current pixi releases produce — bump both
# together.
PIXI_BASE: Final[str] = (
    "ghcr.io/prefix-dev/pixi:0.66.0"
    "@sha256:e8857b9ba407f92958feb0098c816cae713264251d43bb42217bcedfc8a719fd"
)

# Highest pixi.lock format version the pinned pixi above can read. Lock
# files are written by the USER'S local pixi, which may be newer than the
# in-container build tool; register-env checks staged locks against this
# constant so a too-new lock fails upfront instead of as a cryptic pixi
# error inside `docker build`. Verified against pixi 0.66.0 (writes
# `version: 6`) on 2026-07-22 — bump together with PIXI_BASE whenever the
# pinned pixi starts reading a newer lock format.
PIXI_LOCK_MAX_VERSION: Final[int] = 6


# -----------------------------------------------------------------------------
# Micromamba base — used when method.yaml declares environment.yml + lock
# -----------------------------------------------------------------------------
# docker.io/mambaorg/micromamba:latest, digest verified against docker.io
# on 2026-07-21.
MICROMAMBA_BASE: Final[str] = (
    "docker.io/mambaorg/micromamba"
    "@sha256:fb18405d6004af757a38ec498a078240b4fd5549146990a484c28bb7e78aace4"
)

# -----------------------------------------------------------------------------
# Python bases and the uv binary — used by the uv backend
# -----------------------------------------------------------------------------
# docker.io/library/python:3.X-slim-bookworm, one per Python minor a uv
# project can pin, index digests read with `docker buildx imagetools inspect`
# on 2026-10-14. The image's interpreter is the base's; the minor's tag is
# kept alongside the digest for readability. Debian bookworm's glibc (2.36)
# bounds the manylinux wheels the uv lock guard accepts
# (wfc.environments.uv) — bump both together if the Debian release moves.
PYTHON_BASES: Final[dict[str, str]] = {
    "3.10": (
        "docker.io/library/python:3.10-slim-bookworm"
        "@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370"
    ),
    "3.11": (
        "docker.io/library/python:3.11-slim-bookworm"
        "@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89"
    ),
    "3.12": (
        "docker.io/library/python:3.12-slim-bookworm"
        "@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
    ),
    "3.13": (
        "docker.io/library/python:3.13-slim-bookworm"
        "@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641"
    ),
}

# ghcr.io/astral-sh/uv:0.11.16, index digest read on 2026-10-14. The uv
# recipe copies its binary into the python base to create the venv and
# install the exported requirements.
UV_IMAGE: Final[str] = (
    "ghcr.io/astral-sh/uv:0.11.16"
    "@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d"
)

# Which backend builds on which base is the backend table's
# (``wfc.environments.backends.BACKENDS``, each row's ``pinned_bases``).
