"""Inherit-backend Dockerfile generator (ADR-019 Cycle B).

Pure function: in goes the env name + freeze, out comes a Dockerfile
string. No disk I/O, no subprocess.

Per ADR-019 decision #11 (amended 2026-05-16), the inherit backend uses
a **floating ``python:X.Y-slim`` minor tag** computed inline from
``sys.version_info.major.minor`` at register-env time. This module does
**NOT** call into :mod:`wfc.dockerfiles.bases` — the asymmetric pinning
design is that ``PIXI_BASE`` / ``MICROMAMBA_BASE`` are digest-pinned
because the tools run at build time, while inherit's base is passive
(interpreter + pip running ``pip install --no-deps`` against a fully
pinned freeze). Per-env reproducibility is anchored by the post-build
image digest stored in ``.wfc/envs.json``.

Recipe (ADR-019 §dockerfile-generation, amended 2026-05-17):

  # syntax=docker/dockerfile:1.4
  FROM python:X.Y-slim                            # floating minor tag
  COPY pip-freeze.txt /opt/
  RUN --mount=type=cache,target=/root/.cache/pip \\
      pip install --no-deps -r /opt/pip-freeze.txt
  RUN chmod -R a+rX <env_dir>

No `--no-cache-dir`: the cache mount keeps pip's cache on the host (out
of the final image) without disabling pip's caching machinery. The cache
persists across builds — including builds of *different* envs — so
shared wheels install from BuildKit's host-side store on rebuild
(BuildKit-only — Cycle C sets ``DOCKER_BUILDKIT=1``).
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple, Union


def _resolve_minor(
    version: Optional[Union[str, Tuple[int, ...]]],
) -> str:
    """Return ``X.Y`` for the inherit-base tag.

    Args:
        version: When ``None``, the host's ``sys.version_info`` is used.
            When a tuple, the first two elements are taken as
            ``(major, minor)``. When a string like ``"3.12.7"`` or
            ``"3.12"``, the first two dot-separated components are used.

    Returns:
        The ``"<major>.<minor>"`` string suitable for interpolation into
        ``python:<X.Y>-slim``.
    """
    if version is None:
        info = sys.version_info
        return f"{info.major}.{info.minor}"
    if isinstance(version, tuple):
        return f"{version[0]}.{version[1]}"
    # String path: take first two dot-separated components.
    parts = str(version).split(".")
    return f"{parts[0]}.{parts[1]}"


def generate(
    env_name: str,
    pip_freeze_content: str,
    version: Optional[Union[str, Tuple[int, ...]]] = None,
    base_image: Optional[str] = None,
) -> str:
    """Render an inherit-backend Dockerfile.

    Args:
        env_name: Logical env name; used in the chmod path so the
            resulting layout matches the pixi/conda recipes.
        pip_freeze_content: Verbatim ``pip freeze`` output captured at
            register-env time. Installed with ``--no-deps`` so the
            freeze itself is authoritative.
        version: Optional Python version override. ``None`` (default)
            uses the host's ``sys.version_info``. A tuple
            ``(major, minor, ...)`` or string ``"3.12.7"`` / ``"3.12"``
            picks an explicit minor.
        base_image: Optional override for the base image. When ``None``
            (the normal case), ``python:X.Y-slim`` is used with X.Y
            derived from *version* (or the host interpreter).

    Returns:
        Dockerfile text as a single string ending with a trailing newline.
    """
    if base_image is not None:
        base = base_image
    else:
        minor = _resolve_minor(version)
        base = f"python:{minor}-slim"

    # Match the layout of pixi/conda recipes so the runtime's --user fix
    # has a stable read target.
    env_dir = f"/opt/envs/{env_name}"

    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        "COPY pip-freeze.txt /opt/",
        "",
        (
            "RUN --mount=type=cache,target=/root/.cache/pip "
            "pip install --no-deps -r /opt/pip-freeze.txt"
        ),
        "",
        f"RUN chmod -R a+rX {env_dir}",
        "",
    ]
    return "\n".join(lines)
