"""Dockerfile-generation package.

Per-backend ``generate(...)`` functions live in
:mod:`wfc.environments.dockerfiles.pixi`,
:mod:`wfc.environments.dockerfiles.conda` and
:mod:`wfc.environments.dockerfiles.byo`;
:mod:`wfc.environments.dockerfiles.bases` pins their base images by digest.
The :func:`generate_for_backend` dispatch helper routes a (backend,
**kwargs) pair to the right module, so :func:`wfc.environments.register`
and ``wfc register-env --dry-run`` stay backend-agnostic. The image is
built locally and never pushed.
"""

from __future__ import annotations

from typing import Optional

from . import byo, conda, pixi


def generate_for_backend(backend: str, **kwargs) -> Optional[str]:
    """Dispatch to the per-backend ``generate(...)`` function.

    Args:
        backend: One of ``"pixi"``, ``"conda"``, ``"byo"``.
        **kwargs: Forwarded verbatim to the chosen module's ``generate``.

    Returns:
        The Dockerfile text, or ``None`` for the BYO backend (which has
        no Dockerfile — the upstream image is used as-is).

    Raises:
        ValueError: If *backend* is not a known backend name.
    """
    if backend == "pixi":
        return pixi.generate(**kwargs)
    if backend == "conda":
        return conda.generate(**kwargs)
    if backend == "byo":
        return byo.generate(**kwargs)
    raise ValueError(
        f"Unknown backend {backend!r}. "
        f"Supported: 'pixi', 'conda', 'byo'."
    )
