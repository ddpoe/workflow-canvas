"""BYO (bring-your-own) backend Dockerfile generator.

BYO envs reference an externally-built image (e.g. a vendor-published
container like ``ghcr.io/mouseland/cellpose:v3.0.7``). wfc does not
generate a Dockerfile in this path — there is nothing to build.
:func:`wfc.environments.register` resolves the image's digest instead:
``docker image inspect``, pulling the image first when it is not local.
"""

from __future__ import annotations


def generate(*args, **kwargs) -> str | None:
    """Return ``None`` — BYO has no Dockerfile to generate.

    ``wfc register-env --dry-run`` treats a ``None`` return as a signal
    to print the "no Dockerfile for BYO" notice and exit 0.

    Args:
        *args: Accepts and ignores the kwargs the other generators use
            (env_name, pip_freeze_content, image, ...) so the dispatch
            call site doesn't have to special-case the signature.
        **kwargs: Same.

    Returns:
        Always ``None``.
    """
    return None
