r"""Conda-backend Dockerfile generator.

Pure function: in goes the env name + explicit-list/freeze inputs, out
comes a Dockerfile string. No disk I/O, no subprocess. The caller
supplies the explicit list and `pip freeze` output, and
:func:`wfc.environments.register` writes them and the rendered
Dockerfile into the build context, ``.wfc/build/<name>/``.

Recipe:

  # syntax=docker/dockerfile:1.4
  FROM <MICROMAMBA_BASE>                          # digest-pinned
  COPY explicit-list.txt /opt/
  RUN --mount=type=cache,target=/opt/conda/pkgs \
      micromamba install -y -n base -f /opt/explicit-list.txt
  COPY pip-freeze.txt /opt/
  ARG MAMBA_DOCKERFILE_ACTIVATE=1                 # base env's pip on PATH
  RUN --mount=type=cache,target=/root/.cache/pip \
      pip install --no-deps -r /opt/pip-freeze.txt
  USER root                                       # /opt/conda is root-owned
  RUN chmod -R a+rX <env_dir>                     # pair w/ --user
  USER $MAMBA_USER                                # restore base default user
  <the shared tail>                               # dockerfiles.tail

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

A conda env captured on a host that is not linux-64 is built once by
:func:`generate_solve` instead: it solves a pinned spec for linux-64 and
writes the image's own explicit list into the image, which registration
reads back and records. The build context is then rewritten to the
explicit-list recipe above, so a build from it never solves again.
:func:`unsolvable_requests` reads a failed solve's report for the pins with
no linux-64 build.
"""

from __future__ import annotations

import re

from ..introspect import PIP_MISSING_SENTINEL
from .bases import MICROMAMBA_BASE
from .tail import finish_recipe

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
    base_image: str | None = None,
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
        *_pip_layers(pip_freeze_content),
        *_permissions_layers(env_dir),
    ]
    return finish_recipe(lines)


#: The build-context file the solve recipe installs from: the pinned spec as
#: environment.yml text (channels and ``name=<version>`` dependencies).
SOLVE_SPEC_FILENAME = "solve-spec.yml"

#: Where the solve recipe writes the image's own linux-64 explicit list, outside
#: the env tree; registration copies it out of a never-started container.
IMAGE_EXPLICIT_LIST_PATH = "/opt/wfc/linux-64-explicit-list.txt"


def generate_solve(
    env_name: str,
    pip_freeze_content: str,
    base_image: str | None = None,
) -> str:
    """Render the Dockerfile that solves a pinned spec for linux-64.

    A conda env from a host that is not linux-64 cannot install its own
    explicit list, so this recipe installs the pinned spec staged as
    :data:`SOLVE_SPEC_FILENAME` with micromamba, which solves it for the
    image's platform from the spec's channels. Right after the solve, before
    any pip layer, a root ``RUN`` writes ``micromamba env export -n base
    --explicit`` (a ``# platform: linux-64`` header, ``@EXPLICIT`` and one
    ``url#md5`` line per package) to :data:`IMAGE_EXPLICIT_LIST_PATH`. The
    pip set follows with ``--no-deps``, then the permissions pass and the
    shared tail, as in :func:`generate`.

    The list read back from that path is what the env records; the build
    context is then rewritten to :func:`generate`'s recipe over that list,
    so a build from it installs exactly it and never solves again.

    Args:
        env_name: The env name; the image reuses the base env, so it moves
            no path (signature parity with :func:`generate`).
        pip_freeze_content: The pip set, ``name==version`` lines. Empty or
            the absent-pip sentinel omits the pip layers.
        base_image: Optional override for the micromamba base image.

    Returns:
        Dockerfile text ending with a trailing newline.
    """
    base = base_image if base_image is not None else MICROMAMBA_BASE
    list_dir = IMAGE_EXPLICIT_LIST_PATH.rsplit("/", 1)[0]
    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        f"COPY {SOLVE_SPEC_FILENAME} /opt/",
        "",
        (
            "RUN --mount=type=cache,target=/opt/conda/pkgs "
            f"micromamba install -y -n base -f /opt/{SOLVE_SPEC_FILENAME}"
        ),
        "",
        # /opt is root-owned; the list is written outside the env tree, as
        # root, before pip can add packages the conda export does not know.
        "USER root",
        "",
        (
            f"RUN mkdir -p {list_dir} && "
            f"micromamba env export -n base --explicit > {IMAGE_EXPLICIT_LIST_PATH}"
        ),
        "",
        "USER $MAMBA_USER",
        "",
        *_pip_layers(pip_freeze_content),
        *_permissions_layers(CONDA_ENV_DIR),
    ]
    return finish_recipe(lines)


#: One unsatisfiable request in micromamba's solver report, e.g.
#: ``└─ vc14_runtime =14.44.35208 * does not exist (perhaps a typo or a
#: missing channel).`` The tree glyph is ``└─`` or ``├─``.
_NO_BUILD_LINE = re.compile(
    r"[└├]─\s*(?:\S+::)?(?P<name>[A-Za-z0-9_][A-Za-z0-9_.\-]*)"
    r"\s+(?P<version>[^\s*]+)?[^\n]*?does not exist"
)


def unsolvable_requests(build_output: str) -> list[str]:
    """Return the pinned requests micromamba reported it has no build for.

    Reads the tail of a failed solve build: each ``<name> =<version> *
    does not exist`` line of micromamba's solver report. For a pinned spec
    solved for linux-64 that means no linux-64 (or noarch) build of that
    version is in the spec's channels.

    Args:
        build_output: The failed ``docker build``'s output, or its error
            message.

    Returns:
        ``name=version`` (or ``name``) per unsatisfiable request, each once,
        in report order; empty when the build failed for another reason.
    """
    found: list[str] = []
    for match in _NO_BUILD_LINE.finditer(build_output):
        version = (match["version"] or "").lstrip("=")
        request = f"{match['name']}={version}" if version else match["name"]
        if request not in found:
            found.append(request)
    return found


def _pip_layers(pip_freeze_content: str) -> list[str]:
    """The pip COPY/RUN pair, or nothing when there is no pip set.

    Args:
        pip_freeze_content: The pip freeze or pip set; empty or the
            absent-pip sentinel means nothing to install.

    Returns:
        The recipe lines, each layer followed by a blank line.
    """
    stripped_freeze = (pip_freeze_content or "").strip()
    if not stripped_freeze or stripped_freeze == PIP_MISSING_SENTINEL.strip():
        return []
    return [
        "COPY pip-freeze.txt /opt/",
        "",
        # The micromamba base activates its base env in a RUN only when this
        # is set; without it the env's pip is not on PATH.
        "ARG MAMBA_DOCKERFILE_ACTIVATE=1",
        "",
        (
            "RUN --mount=type=cache,target=/root/.cache/pip "
            "pip install --no-deps -r /opt/pip-freeze.txt"
        ),
        "",
    ]


def _permissions_layers(env_dir: str) -> list[str]:
    """The root-bracketed ``chmod -R a+rX`` over the env tree.

    The base ships /opt/conda root-owned while builds run as its non-root
    ``$MAMBA_USER``; chmod requires ownership, so the layer runs as root,
    then hands back the base's default user.

    Args:
        env_dir: The env tree to open for reading.

    Returns:
        The recipe lines, ending with a blank line.
    """
    return [
        "USER root",
        "",
        f"RUN chmod -R a+rX {env_dir}",
        "",
        "USER $MAMBA_USER",
        "",
    ]


# Conda platform directories (the ``<channel>/<platform>/<file>`` segment of a
# package URL) whose packages cannot be installed into the linux-64 image.
# Only these names are read from URLs, so a mirror or local channel with an
# unusual path layout is never mistaken for a foreign platform.
_FOREIGN_PLATFORMS = frozenset({
    "win-32", "win-64", "win-arm64",
    "osx-64", "osx-arm64",
    "linux-32", "linux-aarch64", "linux-armv6l", "linux-armv7l",
    "linux-ppc64le", "linux-s390x", "linux-riscv64",
})


def explicit_list_foreign_platforms(explicit_list: str) -> set[str]:
    """Return the platforms other than linux-64 an explicit list was captured for.

    The platform is read from the list's ``# platform:`` header, or, without
    one, from the platform directory in each package URL. Lenient by design:
    a list that names no known foreign platform answers an empty set.

    Args:
        explicit_list: Full text of an explicit list.

    Returns:
        The foreign platforms (``noarch`` is never one); empty for a
        linux-64 list.
    """
    header_platform = None
    url_platforms: set[str] = set()
    for raw in explicit_list.splitlines():
        line = raw.strip()
        if line.startswith("# platform:"):
            header_platform = line.split(":", 1)[1].strip()
        elif "://" in line:
            parts = line.split("#", 1)[0].rstrip("/").split("/")
            if len(parts) >= 2 and parts[-2] in _FOREIGN_PLATFORMS:
                url_platforms.add(parts[-2])

    if header_platform is not None:
        return {header_platform} - {"linux-64", "noarch"}
    return url_platforms


def validate_explicit_list_platform(explicit_list: str, env_name: str) -> None:
    """Fail fast when a conda explicit list was captured on a non-Linux host.

    An explicit list names exact package files, and :func:`generate`
    installs them without solving, so a list captured on Windows or macOS
    unpacks ``win-64`` / ``osx-*`` packages into the Linux image and the
    build fails later (or yields an image that cannot run). The platform is
    read from the list's ``# platform:`` header, or, without one, from the
    platform directory in each package URL.

    Lenient by design when neither names a known foreign platform: the
    build is left to judge.

    Args:
        explicit_list: Full text of the staged explicit list.
        env_name: The env name being registered, used in the instructions.

    Raises:
        ValueError: The list targets a platform other than ``linux-64``,
            with the commands that produce a linux-64 list instead.
    """
    foreign = explicit_list_foreign_platforms(explicit_list)
    if not foreign:
        return

    captured_on = ", ".join(sorted(foreign))
    raise ValueError(
        f"This conda env was captured on {captured_on}, but images run on "
        f"linux-64. A conda capture installs the exact package files it "
        f"lists, and {captured_on} packages cannot run in a Linux image.\n"
        f"Generate a linux-64 package list from the same env and register "
        f"that. conda-lock is a separate tool; preferably install it in a "
        f"new environment, not the one wfc runs in: conda create -n "
        f"conda-lock -c conda-forge conda-lock, then conda activate "
        f"conda-lock and run:\n"
        f"    conda env export -n <your-conda-env> --from-history > environment.yml\n"
        f"    conda-lock -f environment.yml -p linux-64 --kind explicit\n"
        f"    wfc register-env {env_name} --backend conda --from conda-linux-64.lock\n"
        f"Or capture the live env, which solves it for linux-64 from Windows "
        f"and macOS too:\n"
        f"    wfc register-env {env_name} conda:<env>\n"
        f"or capture it on Linux (or WSL), or use a pixi environment, which "
        f"solves for linux-64 from any machine."
    )
