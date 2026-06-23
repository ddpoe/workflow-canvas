"""Pixi-backend Dockerfile generator (ADR-019 Cycle B).

Pure function: in goes the env name + lock/freeze inputs, out comes a
Dockerfile string. No disk I/O, no subprocess. The CLI handler in
``wfc/cli.py::_cli_register_env`` is responsible for reading the host
pixi.lock and `pip freeze` output and for writing the rendered
Dockerfile to ``.wfc/build/<name>/Dockerfile``.

Recipe (ADR-019 §dockerfile-generation, amended 2026-05-17):

  # syntax=docker/dockerfile:1.4
  FROM <PIXI_BASE>                                # digest-pinned (decision #11)
  COPY pixi.toml pixi.lock /opt/
  RUN --mount=type=cache,target=/root/.cache/rattler \\
      pixi install --locked --environment <env>   # tool-pinned env materialization
  COPY pip-freeze.txt /opt/
  RUN --mount=type=cache,target=/root/.cache/pip \\
      <env-python> -m pip install --no-deps -r /opt/pip-freeze.txt
  RUN chmod -R a+rX <env_dir>                     # pair w/ --user (decision #9)

The `--no-deps` pip layer runs AFTER `pixi install --locked` so the
locked env establishes the dep graph; the freeze layer reconstructs the
exact wheel set the user had at register-env time without re-solving.

The COPY pair is split so the slow `pixi install --locked` layer reuses
Docker's layer cache when only pip-freeze.txt has changed; the cache
mounts persist pixi's rattler downloads and pip's wheel cache across
builds (BuildKit-only — Cycle C sets ``DOCKER_BUILDKIT=1``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .bases import PIXI_BASE


def generate(
    env_name: str,
    pixi_lock_path: Union[str, Path],
    pip_freeze_content: str,
    base_image: Optional[str] = None,
) -> str:
    """Render a pixi-backend Dockerfile.

    Args:
        env_name: Name of the pixi environment to materialize via
            ``pixi install --locked --environment <env_name>``. Becomes
            part of the install command verbatim.
        pixi_lock_path: Path to the host ``pixi.lock`` (used here only
            for source-of-truth documentation; the CLI handler is the one
            that stages the file into the build context).
        pip_freeze_content: Verbatim ``pip freeze`` output captured at
            register-env time. Travels as a build context file and is
            installed with ``--no-deps`` so the locked env's dep graph
            is preserved.
        base_image: Optional override for the pixi base image. When
            ``None``, :data:`wfc.dockerfiles.bases.PIXI_BASE` is used.

    Returns:
        Dockerfile text as a single string ending with a trailing newline.
    """
    # pixi installs envs under /<env_name>/envs/default by default; chmod
    # that subtree so a --user-mismatched runtime can read every file
    # (pair with ADR-019 decision #9's `--user $(id -u):$(id -g)` flag).
    env_dir = f"/{env_name}/envs/default"
    base = base_image if base_image is not None else PIXI_BASE
    # The python inside the pixi env is what runs the --no-deps freeze.
    env_python = f"{env_dir}/bin/python"

    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        "COPY pixi.toml pixi.lock /opt/",
        "",
        (
            "RUN --mount=type=cache,target=/root/.cache/rattler "
            f"pixi install --locked --environment {env_name}"
        ),
        "",
        "COPY pip-freeze.txt /opt/",
        "",
        (
            "RUN --mount=type=cache,target=/root/.cache/pip "
            f"{env_python} -m pip install --no-deps -r /opt/pip-freeze.txt"
        ),
        "",
        f"RUN chmod -R a+rX {env_dir}",
        "",
    ]
    return "\n".join(lines)
