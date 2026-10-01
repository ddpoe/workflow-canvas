"""Base-image references for container builds.

Pinning:

- **Pixi and conda bases are digest-pinned** (``<host>/<path>@sha256:<hex>``).
  The build-time tools (``pixi install --locked``, ``micromamba install -f``)
  run inside these base images, so the tool version is load-bearing for
  build determinism. Two users on the same wfc version building the same
  env must invoke the same tool binaries.

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
# Backend → base lookup (used by the Dockerfile generator dispatch)
# -----------------------------------------------------------------------------

BASES_BY_BACKEND: Final[dict[str, str]] = {
    "pixi": PIXI_BASE,
    "conda": MICROMAMBA_BASE,
}
