r"""Pixi-backend Dockerfile generator.

Pure function: in goes the env name + lock/freeze inputs, out comes a
Dockerfile string. No disk I/O, no subprocess. The caller supplies the
pixi.lock and `pip freeze` output, and :func:`wfc.environments.register`
writes them and the rendered Dockerfile into the build context,
``.wfc/build/<name>/``.

Recipe:

  # syntax=docker/dockerfile:1.4
  FROM <PIXI_BASE>                                # digest-pinned
  WORKDIR /opt                                    # pixi reads pixi.toml from cwd
  COPY pixi.toml pixi.lock /opt/
  RUN --mount=type=cache,target=/root/.cache/rattler \
      pixi install --locked --environment <env>   # tool-pinned env materialization
  COPY pip-freeze.txt /opt/
  RUN --mount=type=cache,target=/root/.cache/pip \
      /opt/.pixi/envs/<env>/bin/python -m pip install --no-deps -r /opt/pip-freeze.txt
  RUN chmod -R a+rX /opt/.pixi/envs/<env>         # pair w/ --user

The `--no-deps` pip layer runs AFTER `pixi install --locked` so the
locked env establishes the dep graph; the freeze layer reconstructs the
exact wheel set the user had at register-env time without re-solving.

The COPY pair is split so the slow `pixi install --locked` layer reuses
Docker's layer cache when only pip-freeze.txt has changed; the cache
mounts persist pixi's rattler downloads and pip's wheel cache across
builds (BuildKit-only — :func:`wfc.environments.docker.build` sets
``DOCKER_BUILDKIT=1``).
"""

from __future__ import annotations

from .bases import PIXI_BASE, PIXI_LOCK_MAX_VERSION


def validate_lock_for_env(lock_text: str, env_name: str) -> None:
    """Fail fast when a staged pixi.lock cannot build *env_name*.

    Three checks, each raising ``ValueError`` at register time instead of
    letting ``docker build`` surface a cryptic in-container pixi error:

    * **Lock format version** — the lock is written by the user's local
      pixi, which may be newer than the pinned in-container build tool
      (:data:`wfc.environments.dockerfiles.bases.PIXI_BASE`). A top-level ``version``
      above :data:`wfc.environments.dockerfiles.bases.PIXI_LOCK_MAX_VERSION` is
      rejected with both versions named.
    * **Environment membership** — :func:`generate` passes *env_name*
      verbatim to ``pixi install --environment``, so the lock's
      ``environments`` block must define it. Rejected naming the keys
      the lock actually has (most pixi projects define exactly one,
      ``default``).
    * **linux-64 packages** — the image is linux-64, so the environment's
      ``packages`` block must have a ``linux-64`` entry. A lock made on
      Windows or macOS lists only that host's platform unless linux-64 was
      added to the workspace; rejected naming the platforms it has and the
      commands that add linux-64.

    Lenient by design when the lock does not parse as a YAML mapping or
    lacks the ``version`` key / ``environments`` block / ``packages``
    entries — those shapes are left for the build to surface; this guard
    only covers the known-confusing failures.

    Args:
        lock_text: Full text of the staged ``pixi.lock``.
        env_name: The env name the generator will materialize.

    Raises:
        ValueError: Lock format version too new, *env_name* missing
            from the lock's ``environments`` block, or no linux-64
            packages for it.
    """
    import yaml

    try:
        lock = yaml.safe_load(lock_text)
    except yaml.YAMLError:
        return
    if not isinstance(lock, dict):
        return

    version = lock.get("version")
    if isinstance(version, int) and version > PIXI_LOCK_MAX_VERSION:
        base_tag = PIXI_BASE.split("@")[0]
        raise ValueError(
            f"pixi.lock is lock-format version {version}, newer than the "
            f"in-container build pixi ({base_tag}) can read (max supported "
            f"lock version: {PIXI_LOCK_MAX_VERSION}). The lock was written "
            f"by a newer local pixi than this wfc release bundles. "
            f"Regenerate the lock with a matching pixi version, or upgrade "
            f"wfc."
        )

    environments = lock.get("environments")
    if (
        isinstance(environments, dict)
        and environments
        and env_name not in environments
    ):
        available = ", ".join(sorted(environments))
        raise ValueError(
            f"pixi.lock has no environment named {env_name!r} — it has: "
            f"{available}. The name passed to `wfc register-env` is "
            f"installed verbatim via `pixi install --environment "
            f"{env_name}` inside the image build, so it must match an "
            f"environment defined in the lock (usually `default`)."
        )

    env_block = environments.get(env_name) if isinstance(environments, dict) else None
    packages = env_block.get("packages") if isinstance(env_block, dict) else None
    if isinstance(packages, dict) and packages and "linux-64" not in packages:
        platforms = ", ".join(sorted(packages))
        raise ValueError(
            f"pixi.lock has no linux-64 packages for environment "
            f"{env_name!r} (it has: {platforms}). Images run on linux-64. "
            f"Add the platform and re-lock, then register again:\n"
            f"    pixi workspace platform add linux-64\n"
            f"    pixi install"
        )


def env_dir_path(env_name: str) -> str:
    """Return the container path of the materialized pixi env tree.

    ``pixi install --locked --environment <env_name>`` runs with
    ``WORKDIR /opt`` (where the generator COPYs pixi.toml + pixi.lock), so
    pixi materializes the env under the project-relative ``.pixi/envs/``
    tree: ``/opt/.pixi/envs/<env_name>``. Verified empirically against the
    prefix-dev pixi base image (2026-07-21). This is the single source of
    truth for that layout — the generator's pip stage, the chmod stage,
    and the dispatch-time interpreter default
    (:func:`wfc.environments.default_python_for_backend`) all derive from it.

    Args:
        env_name: Name of the pixi environment.

    Returns:
        Absolute container path of the env directory.
    """
    return f"/opt/.pixi/envs/{env_name}"


def env_python_path(env_name: str) -> str:
    """Return the container path of the pixi env's Python interpreter.

    Args:
        env_name: Name of the pixi environment.

    Returns:
        Absolute container path of the env's ``python`` binary.
    """
    return f"{env_dir_path(env_name)}/bin/python"


def generate(
    env_name: str,
    pip_freeze_content: str,
    base_image: str | None = None,
) -> str:
    """Render a pixi-backend Dockerfile.

    Args:
        env_name: Name of the pixi environment to materialize via
            ``pixi install --locked --environment <env_name>``. Becomes
            part of the install command verbatim.
        pip_freeze_content: Verbatim ``pip freeze`` output captured at
            register-env time. Travels as a build context file and is
            installed with ``--no-deps`` so the locked env's dep graph
            is preserved.
        base_image: Optional override for the pixi base image. When
            ``None``, :data:`wfc.environments.dockerfiles.bases.PIXI_BASE` is used.

    Returns:
        Dockerfile text as a single string ending with a trailing newline.
    """
    # pixi installs envs under <cwd>/.pixi/envs/<env_name>; with WORKDIR
    # /opt that is /opt/.pixi/envs/<env_name>. chmod that subtree so a
    # --user-mismatched runtime can read every file (pair with the
    # `--user <uid>:<gid>` flag dispatch passes to docker run).
    env_dir = env_dir_path(env_name)
    base = base_image if base_image is not None else PIXI_BASE
    # The python inside the pixi env is what runs the --no-deps freeze.
    env_python = env_python_path(env_name)

    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        # pixi resolves pixi.toml from the working directory; the base
        # image sets no WORKDIR, so without this the install runs at /.
        "WORKDIR /opt",
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
