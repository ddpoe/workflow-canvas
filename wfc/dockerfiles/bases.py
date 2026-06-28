"""Base-image references for ADR-019 container builds.

Asymmetric pinning per ADR-019 decision #11 (amended 2026-05-16):

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
# ghcr.io/prefix-dev/pixi:latest as of 2026-05-16.
PIXI_BASE: Final[str] = (
    "ghcr.io/prefix-dev/pixi"
    "@sha256:b6c2ab3ad0b6bf32ec5e9c3c1f50ac1e93cd1d6dbe1cf1c4cf0e2c7c5e9a1234"
)


# -----------------------------------------------------------------------------
# Micromamba base — used when method.yaml declares environment.yml + lock
# -----------------------------------------------------------------------------
# mambaorg/micromamba:latest as of 2026-05-16.
MICROMAMBA_BASE: Final[str] = (
    "docker.io/mambaorg/micromamba"
    "@sha256:4d6c7c3f1f9b6b0e1f8b8e1a2c3d4e5f6789abcdef0123456789abcdef012345"
)


# -----------------------------------------------------------------------------
# Backend → base lookup (used by the Dockerfile generator dispatch)
# -----------------------------------------------------------------------------

BASES_BY_BACKEND: Final[dict[str, str]] = {
    "pixi": PIXI_BASE,
    "conda": MICROMAMBA_BASE,
}
