"""Conda-backend Dockerfile generator (ADR-019 Cycle B).

Pure function: in goes the env name + explicit-list/freeze inputs, out
comes a Dockerfile string. No disk I/O, no subprocess. The CLI handler
in ``wfc/cli.py::_cli_register_env`` is responsible for staging the
explicit list and `pip freeze` capture into the build context and for
writing the rendered Dockerfile to ``.wfc/build/<name>/Dockerfile``.

Recipe (ADR-019 §dockerfile-generation, amended 2026-05-17):

  # syntax=docker/dockerfile:1.4
  FROM <MICROMAMBA_BASE>                          # digest-pinned (decision #11)
  COPY explicit-list.txt /opt/
  RUN --mount=type=cache,target=/opt/conda/pkgs \\
      micromamba install -y -n base -f /opt/explicit-list.txt
  COPY pip-freeze.txt /opt/
  RUN --mount=type=cache,target=/root/.cache/pip \\
      pip install --no-deps -r /opt/pip-freeze.txt
  RUN chmod -R a+rX <env_dir>                     # pair w/ --user (decision #9)

The conda install runs FIRST so the explicit list establishes the
dep graph; the freeze runs `--no-deps` so the resolver isn't allowed to
override what the explicit list pinned.

The micromamba and pip install RUNs are deliberately separate (the
chained `&&` form would defeat the layer cache); the COPY pair is split
so a pip-freeze-only change reuses the micromamba layer. Cache mounts
persist conda packages (``/opt/conda/pkgs``) and pip wheels across
builds (BuildKit-only — Cycle C sets ``DOCKER_BUILDKIT=1``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .bases import MICROMAMBA_BASE


# Where the micromamba base image keeps its ``base`` env. Verified
# empirically against mambaorg/micromamba (2026-07-18): ``micromamba env
# list`` reports base at ``/opt/conda`` (== MAMBA_ROOT_PREFIX) and NO
# ``/opt/conda/envs`` directory exists — installs via ``-n base`` land
# directly under ``/opt/conda``. Both the chmod stage below and the
# dispatch-time interpreter default
# (:func:`wfc.envs.default_python_for_backend`) derive from these.
CONDA_ENV_DIR = "/opt/conda"
CONDA_ENV_PYTHON = f"{CONDA_ENV_DIR}/bin/python"


def generate(
    env_name: str,
    explicit_list_path: Union[str, Path],
    pip_freeze_content: str,
    base_image: Optional[str] = None,
) -> str:
    """Render a conda/micromamba-backend Dockerfile.

    Args:
        env_name: Name of the conda env to materialize. The micromamba
            base image's ``base`` env is reused (single-env image), so the
            *env_name* does not affect any in-image path — it exists for
            signature parity with the other backend generators.
        explicit_list_path: Path to the host explicit-list file (used
            here only as documentation; the CLI handler stages it into
            the build context).
        pip_freeze_content: Verbatim ``pip freeze`` output captured at
            register-env time. Installed with ``--no-deps`` so the
            conda-resolved dep graph stays authoritative.
        base_image: Optional override for the micromamba base image.
            When ``None``, :data:`wfc.dockerfiles.bases.MICROMAMBA_BASE`
            is used.

    Returns:
        Dockerfile text as a single string ending with a trailing newline.
    """
    # micromamba's base env lives at /opt/conda itself in the mambaorg image
    # (installs go to `-n base`; there is no /opt/conda/envs/<name> tree).
    # chmod the real env tree so --user-mismatched runtimes can read it.
    # The old target (/opt/conda/envs/<env_name>) never exists in the built
    # image, which made the chmod RUN — and thus every conda build — fail.
    env_dir = CONDA_ENV_DIR
    base = base_image if base_image is not None else MICROMAMBA_BASE

    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        "COPY explicit-list.txt /opt/",
        "",
        (
            "RUN --mount=type=cache,target=/opt/conda/pkgs "
            "micromamba install -y -n base -f /opt/explicit-list.txt"
        ),
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
