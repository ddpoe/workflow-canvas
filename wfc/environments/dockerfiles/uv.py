r"""uv-backend Dockerfile generator.

Pure function: in goes the env name and the project's Python minor, out
comes a Dockerfile string. No disk I/O, no subprocess. The caller exports
the uv project's requirements on the host
(:func:`wfc.environments.uv.capture_project`), and
:func:`wfc.environments.register` writes them as ``requirements.txt`` and
the rendered Dockerfile into the build context, ``.wfc/build/<name>/``.

Recipe:

  # syntax=docker/dockerfile:1.4
  FROM <PYTHON_BASES[minor]>                      # digest-pinned python:3.X-slim-bookworm
  COPY --from=<UV_IMAGE> /uv /usr/local/bin/uv    # digest-pinned uv binary
  COPY requirements.txt /opt/
  RUN uv venv --python /usr/local/bin/python3 /opt/venv
  RUN --mount=type=cache,target=/root/.cache/uv \
      uv pip install --python /opt/venv/bin/python \
      --require-hashes --no-deps -r /opt/requirements.txt
  RUN chmod -R a+rX /opt/venv                     # pair w/ --user
  <the shared tail>                               # dockerfiles.tail

The venv is made from the base's own interpreter, so the image's Python is
the minor ``.python-version`` pins. ``uv export`` already resolved every
package for the lock, so the install takes the list as it is
(``--no-deps``) and checks every download against its hash
(``--require-hashes``). The method scripts run under :data:`VENV_PYTHON`.
"""

from __future__ import annotations

from .bases import PYTHON_BASES, UV_IMAGE
from .tail import finish_recipe

#: The venv the recipe creates and installs into.
VENV_DIR = "/opt/venv"
#: The venv's interpreter: the one a uv env's methods run under.
VENV_PYTHON = f"{VENV_DIR}/bin/python"
#: The build-context file the recipe installs from: ``uv export``'s output.
REQUIREMENTS_FILENAME = "requirements.txt"
#: The base image's interpreter the venv is created from.
_BASE_PYTHON = "/usr/local/bin/python3"


def venv_python(env_name: str) -> str:
    """Return the interpreter a uv env's methods run under.

    Args:
        env_name: The env name; the venv path does not depend on it.

    Returns:
        :data:`VENV_PYTHON`.
    """
    return VENV_PYTHON


def base_for_minor(python_minor: str) -> str:
    """Return the digest-pinned Python base for a minor.

    Args:
        python_minor: The minor the project pins, e.g. ``"3.12"``.

    Returns:
        The ``python:<minor>-slim-bookworm`` reference from
        :data:`wfc.environments.dockerfiles.bases.PYTHON_BASES`.

    Raises:
        ValueError: The minor has no pinned base.
    """
    base = PYTHON_BASES.get(python_minor)
    if base is None:
        supported = ", ".join(PYTHON_BASES)
        raise ValueError(
            f"Python {python_minor} has no wfc base image (supported: "
            f"{supported}).")
    return base


def generate(
    env_name: str,
    pip_freeze_content: str = "",
    base_image: str | None = None,
    *,
    python_minor: str,
) -> str:
    """Render a uv-backend Dockerfile.

    Args:
        env_name: The env name; the venv path does not depend on it
            (signature parity with the other backend generators).
        pip_freeze_content: Unused: a uv env's pip section is always empty,
            everything is in ``requirements.txt`` (signature parity).
        base_image: Optional override for the Python base image. When
            ``None``, the minor's base from
            :data:`wfc.environments.dockerfiles.bases.PYTHON_BASES` is used.
        python_minor: The Python minor the project pins in
            ``.python-version``, e.g. ``"3.12"``.

    Returns:
        Dockerfile text as a single string ending with a trailing newline.

    Raises:
        ValueError: *python_minor* has no pinned base and no *base_image*
            was given.
    """
    base = base_image if base_image is not None else base_for_minor(python_minor)
    lines = [
        "# syntax=docker/dockerfile:1.4",
        f"FROM {base}",
        "",
        f"COPY --from={UV_IMAGE} /uv /usr/local/bin/uv",
        "",
        f"COPY {REQUIREMENTS_FILENAME} /opt/",
        "",
        f"RUN uv venv --python {_BASE_PYTHON} {VENV_DIR}",
        "",
        (
            "RUN --mount=type=cache,target=/root/.cache/uv "
            f"UV_LINK_MODE=copy uv pip install --python {VENV_PYTHON} "
            f"--require-hashes --no-deps -r /opt/{REQUIREMENTS_FILENAME}"
        ),
        "",
        f"RUN chmod -R a+rX {VENV_DIR}",
        "",
    ]
    return finish_recipe(lines)
