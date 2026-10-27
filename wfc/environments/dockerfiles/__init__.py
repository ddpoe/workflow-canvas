"""Dockerfile-generation package.

Per-backend ``generate(...)`` functions live in
:mod:`wfc.environments.dockerfiles.pixi`,
:mod:`wfc.environments.dockerfiles.conda` and
:mod:`wfc.environments.dockerfiles.byo`;
:mod:`wfc.environments.dockerfiles.bases` pins their base images by digest,
and every generated recipe ends with :mod:`wfc.environments.dockerfiles.tail`.
The :func:`generate_for_backend` dispatch helper routes a (backend,
**kwargs) pair to the generator the backend table
(:mod:`wfc.environments.backends`) names, so
:func:`wfc.environments.register` and ``wfc register-env --dry-run`` stay
backend-agnostic. The image is built locally; ``wfc register-env`` then
pushes it when the project's ``[registry] url`` is set and ``--no-push``
is not given.
"""

from __future__ import annotations


def generate_for_backend(backend: str, **kwargs) -> str | None:
    """Dispatch to the backend's ``generate(...)`` function.

    Args:
        backend: A backend name from :data:`wfc.environments.backends.BACKENDS`.
        **kwargs: Forwarded verbatim to the backend's generator.

    Returns:
        The Dockerfile text, or ``None`` for a backend that builds no image
        (byo, whose upstream image is used as-is).

    Raises:
        ValueError: If *backend* is not in the backend table.
    """
    from ..backends import backend_row

    return backend_row(backend).generate(**kwargs)
