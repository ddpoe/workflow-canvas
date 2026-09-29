"""Conda-backend Dockerfile generator.

Pure function: in goes the env name + explicit-list/freeze inputs, out
comes a Dockerfile string. No disk I/O, no subprocess. The caller
supplies the explicit list and `pip freeze` output, and
:func:`wfc.environments.register` writes them and the rendered
Dockerfile into the build context, ``.wfc/build/<name>/``.

Recipe:

  # syntax=docker/dockerfile:1.4
  FROM <MICROMAMBA_BASE>                          # digest-pinned
  COPY explicit-list.txt /opt/
  RUN --mount=type=cache,target=/opt/conda/pkgs \\
      micromamba install -y -n base -f /opt/explicit-list.txt
  COPY pip-freeze.txt /opt/
  RUN --mount=type=cache,target=/root/.cache/pip \\
      pip install --no-deps -r /opt/pip-freeze.txt
  USER root                                       # /opt/conda is root-owned
  RUN chmod -R a+rX <env_dir>                     # pair w/ --user
  USER $MAMBA_USER                                # restore base default user

The conda install runs FIRST so the explicit list establishes the
dep graph; the freeze runs `--no-deps` so the resolver isn't allowed to
override what the explicit list pinned.

The chmod stage is bracketed by USER directives: the micromamba base
runs builds as its non-root default user (``$MAMBA_USER``, an ENV the
base defines), but ships ``/opt/conda`` and ``/opt/conda/conda-meta``
root-owned — writable, yet ``chmod`` requires *ownership*, so the
unbracketed layer exits 1 and fails every conda build. Root runs the
chmod; the bracket then restores the base's default user so the image's
runtime user contract is unchanged.

The micromamba and pip install RUNs are deliberately separate (the
chained `&&` form would defeat the layer cache); the COPY pair is split
so a pip-freeze-only change reuses the micromamba layer. Cache mounts
persist conda packages (``/opt/conda/pkgs``) and pip wheels across
builds (BuildKit-only — :func:`wfc.environments.docker.build` sets
``DOCKER_BUILDKIT=1``).
"""

from __future__ import annotations

from typing import Optional

from ..introspect import PIP_MISSING_SENTINEL
from .bases import MICROMAMBA_BASE


# Where the micromamba base image keeps its ``base`` env. Verified
# empirically against mambaorg/micromamba (2026-07-18): ``micromamba env
# list`` reports base at ``/opt/conda`` (== MAMBA_ROOT_PREFIX) and NO
# ``/opt/conda/envs`` directory exists — installs via ``-n base`` land
# directly under ``/opt/conda``. Both the chmod stage below and the
# dispatch-time interpreter default
# (:func:`wfc.environments.default_python_for_backend`) derive from these.
CONDA_ENV_DIR = "/opt/conda"
CONDA_ENV_PYTHON = f"{CONDA_ENV_DIR}/bin/python"


def generate(
    env_name: str,
    pip_freeze_content: str,
    base_image: Optional[str] = None,
) -> str:
    """Render a conda/micromamba-backend Dockerfile.

    Args:
        env_name: Name of the conda env to materialize. The micromamba
            base image's ``base`` env is reused (single-env image), so the
            *env_name* does not affect any in-image path — it exists for
            signature parity with the other backend generators.
        pip_freeze_content: Verbatim ``pip freeze`` output captured at
            register-env time. Installed with ``--no-deps`` so the
            conda-resolved dep graph stays authoritative. When empty (file
            mode, nothing pip-installed) or the absent-pip sentinel
            (:data:`wfc.environments.introspect.PIP_MISSING_SENTINEL` — e.g. an
            R-only conda-forge env with no python/pip), the pip COPY/RUN
            layers are omitted entirely so the image builds without pip.
        base_image: Optional override for the micromamba base image.
            When ``None``, :data:`wfc.environments.dockerfiles.bases.MICROMAMBA_BASE`
            is used.

    Returns:
        Dockerfile text as a single string ending with a trailing newline.
    """
    # micromamba's base env lives at /opt/conda itself in the mambaorg image
    # (installs go to `-n base`; there is no /opt/conda/envs/<name> tree).
    # chmod the real env tree so --user-mismatched runtimes can read it.
    env_dir = CONDA_ENV_DIR
    base = base_image if base_image is not None else MICROMAMBA_BASE

    stripped_freeze = (pip_freeze_content or "").strip()
    needs_pip_layers = bool(stripped_freeze) and (
        stripped_freeze != PIP_MISSING_SENTINEL.strip()
    )

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
    ]
    if needs_pip_layers:
        lines += [
            "COPY pip-freeze.txt /opt/",
            "",
            (
                "RUN --mount=type=cache,target=/root/.cache/pip "
                "pip install --no-deps -r /opt/pip-freeze.txt"
            ),
            "",
        ]
    lines += [
        # The base ships /opt/conda root-owned while builds run as its
        # non-root $MAMBA_USER; chmod requires ownership, so the layer
        # must run as root, then hand back the base's default user.
        "USER root",
        "",
        f"RUN chmod -R a+rX {env_dir}",
        "",
        "USER $MAMBA_USER",
        "",
    ]
    return "\n".join(lines)
