"""BYO (bring-your-own) backend Dockerfile generator (ADR-019 Cycle B).

BYO envs reference an externally-built image (e.g. a vendor-published
container like ``ghcr.io/mouseland/cellpose:v3.0.7``). wfc does not
generate a Dockerfile in this path — there is nothing to build.
Digest resolution (`docker pull && docker inspect`) is the actual
work for the BYO branch and lives in Cycle C.
"""

from __future__ import annotations

from typing import Optional


def generate(*args, **kwargs) -> Optional[str]:
    """Return ``None`` — BYO has no Dockerfile to generate.

    The CLI handler treats a ``None`` return as a signal to print the
    "no Dockerfile for BYO" notice and exit 0 under ``--dry-run``;
    Cycle C will replace this stub with a digest-resolution path that
    pulls the image and inspects its manifest.

    Args:
        *args: Accepts and ignores the kwargs the other generators use
            (env_name, pip_freeze_content, image, ...) so the dispatch
            call site doesn't have to special-case the signature.
        **kwargs: Same.

    Returns:
        Always ``None``.
    """
    return None
