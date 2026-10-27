"""The container-side interpreter a method script runs under.

``register`` records each image's interpreter in its captures folder's
``image.json``: the ``--python`` override, else the backend's default
(:func:`default_python_for_backend`). Dispatch runs a method version's script
with the interpreter of the image that version recorded
(:func:`version_interpreter`), asked by ``(env, env_fingerprint)``; the env's
current record is never read for it. The value is a container path; nothing
translates it.
"""

from __future__ import annotations

from pathlib import Path


def default_python_for_backend(backend: str | None, env_name: str) -> str:
    """Return the per-backend default container interpreter path.

    The defaults mirror what each backend's image recipe actually
    materializes:

    - ``pixi``: the env python the pixi generator's own pip stage uses
      (``/opt/.pixi/envs/<env_name>/bin/python``).
    - ``conda``: the micromamba base-env python (``/opt/conda/bin/python``,
      verified empirically against the mambaorg base image).
    - ``byo`` and anything unknown: bare ``"python"`` (resolved by the
      image's PATH).

    Each value is the backend table's ``default_python`` column
    (:data:`wfc.environments.backends.BACKENDS`).

    Args:
        backend: Backend name from the env record, or ``None`` when no
            record is available.
        env_name: Env name (needed for the pixi env path).

    Returns:
        Container-side interpreter path or bare ``"python"``.
    """
    from .backends import BACKENDS

    row = BACKENDS.get(backend) if backend is not None else None
    if row is None:
        return "python"
    return row.default_python(env_name)


def version_interpreter(env: str, fingerprint: str | None,
                        project_dir: Path | str) -> str:
    """Return the interpreter of *env*'s image at *fingerprint*.

    This is what a method version that recorded that image runs its script
    under, whatever the env's current record says: an older version keeps
    its own image's interpreter after the env is re-registered under
    another backend.

    Args:
        env: The env name the version records.
        fingerprint: The ``env_fingerprint`` the version records.
        project_dir: The project root.

    Returns:
        The container-side interpreter path ``image.json`` records.

    Raises:
        CaptureMissingError: The image has no captures folder (the version
            was registered before wfc recorded captures, or the folder was
            removed), or its ``image.json`` cannot be read.
    """
    from .captures import image_info

    return image_info(env, fingerprint, Path(project_dir)).interpreter
