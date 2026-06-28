"""Dockerfile-generation package (ADR-019).

Per-backend ``generate(...)`` functions live in :mod:`wfc.dockerfiles.pixi`,
:mod:`wfc.dockerfiles.conda`, and :mod:`wfc.dockerfiles.byo`. The
:func:`generate_for_backend` dispatch
helper routes a (backend, **kwargs) pair to the right module so the CLI
handler can stay backend-agnostic.

Cycle A populated :mod:`wfc.dockerfiles.bases` with pinned-digest base
references for pixi and conda. Cycle B adds the four per-backend
generators and the dispatch helper. Cycle C will wire the dispatch into
the full ``docker build && docker push`` path.
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
