"""The container-side interpreter a method script runs under.

Dispatch runs a method script inside the env's image with the interpreter
:func:`resolve_env_python` returns: the record's ``python`` field, else the
backend's default (:func:`default_python_for_backend`), else bare
``python``. ``register`` records the backend default when it builds an env.
The value is a container path; nothing translates it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import EnvRecord


def default_python_for_backend(backend: str | None, env_name: str) -> str:
    """Return the per-backend default container interpreter path.

    The defaults mirror what each backend's image recipe actually
    materializes, so envs registered before the per-env ``python`` field
    existed resolve to identical values by construction:

    - ``pixi``: the env python the pixi generator's own pip stage uses
      (``/opt/.pixi/envs/<env_name>/bin/python``).
    - ``conda``: the micromamba base-env python (``/opt/conda/bin/python``,
      verified empirically against the mambaorg base image).
    - ``byo`` and anything unknown: bare ``"python"`` (resolved by the
      image's PATH).

    Args:
        backend: Backend name from the env record (``"pixi"``, ``"conda"``,
            ``"byo"``), or ``None`` when no record is available.
        env_name: Env name (needed for the pixi env path).

    Returns:
        Container-side interpreter path or bare ``"python"``.
    """
    if backend == "pixi":
        from .dockerfiles.pixi import env_python_path
        return env_python_path(env_name)
    if backend == "conda":
        from .dockerfiles.conda import CONDA_ENV_PYTHON
        return CONDA_ENV_PYTHON
    return "python"


def resolve_env_python(env_name: str, record: EnvRecord | None) -> str:
    """Resolve the interpreter a container should run a method script with.

    Pure three-step fallback:

    1. The record's ``python`` field, when present (recorded at
       registration; wins unconditionally).
    2. The per-backend default computed from the record's backend
       (:func:`default_python_for_backend`) — covers records written
       before the field existed.
    3. Bare ``"python"`` when there is no record at all (unknown env or
       the per-method direct-image escape hatch).

    The returned value is a CONTAINER path — callers must never run it
    through host→container path translation.

    Args:
        env_name: Env name (manifest key).
        record: The env's manifest record, or ``None``.

    Returns:
        Container-side interpreter path or bare ``"python"``.
    """
    if record is not None and getattr(record, "python", None):
        return record.python  # type: ignore[return-value]
    backend = record.backend if record is not None else None
    return default_python_for_backend(backend, env_name)
