"""Helpers for capturing a live host env's package list.

Live capture (``wfc register-env <name> <spec>``) stages their output into
the image build context:

  conda_list_explicit(env_path) -> str
      Shell out to ``conda list --explicit --md5 --prefix <env_path>`` and
      return stdout.  Install-based (actuality).

  pip_freeze_best_effort(env_python) -> str
      Shell out to ``<env_python> -m pip freeze --disable-pip-version-check``
      and return stdout, or :data:`PIP_MISSING_SENTINEL` when the env has no
      pip.  Any other failure raises.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# =============================================================================
# conda list --explicit
# =============================================================================

def conda_list_explicit(env_path: str | Path) -> str:
    """Return the output of ``conda list --explicit --md5 --prefix <env_path>``.

    Install-based (actuality) fingerprint.  The ``--md5`` flag includes the
    content MD5 of each installed package, yielding a strong identity string
    that does not depend on lock files.

    Args:
        env_path: Absolute path to a conda environment prefix
            (the directory containing ``conda-meta/``).

    Returns:
        Raw stdout of ``conda list --explicit --md5``.

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero.
    """
    env_path = Path(env_path)
    try:
        result = subprocess.run(
            ["conda", "list", "--explicit", "--md5", "--prefix", str(env_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            "conda executable not found on PATH — cannot fingerprint "
            f"env at {env_path}"
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"conda list --explicit --md5 --prefix {env_path} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# =============================================================================
# pip freeze
# =============================================================================

# Sentinel emitted in place of `pip freeze` output when pip is provably
# absent from the target interpreter (pixi/conda envs assembled entirely
# from conda-forge, say). The sentinel must be distinct from any real pip
# freeze output and from an empty-pip-freeze-with-pip-present result so
# the env_fingerprint still encodes the fact that pip was missing.
PIP_MISSING_SENTINEL = "pip-freeze: unavailable (pip not installed)\n"


def pip_freeze_best_effort(env_python: str | Path) -> str:
    """Return ``<env_python> -m pip freeze`` output, tolerating a missing pip.

    Returns :data:`PIP_MISSING_SENTINEL` when `<env_python> -m pip` fails
    specifically because pip is not installed in the target env. All
    other failures (nonzero exit with a different error, missing
    interpreter, etc.) still raise — those are not "pip is absent" and
    silently swallowing them would hide real env problems.

    Intended for pixi/conda backends where the lock (pixi.lock) or
    explicit install list (conda list --explicit) is already the
    authoritative fingerprint and pip freeze only catches pip-installed
    drift on top.
    """
    env_python = Path(env_python)
    try:
        result = subprocess.run(
            [str(env_python), "-m", "pip", "freeze", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Python interpreter not found at {env_python} — cannot run pip freeze"
        ) from e

    if result.returncode == 0:
        return result.stdout

    # `python -m pip` with pip absent prints `No module named pip` to
    # stderr and exits nonzero. That signature is specific enough to
    # distinguish missing-pip from "pip crashed for some other reason";
    # the latter must still raise.
    if "No module named pip" in (result.stderr or ""):
        return PIP_MISSING_SENTINEL

    raise RuntimeError(
        f"'{env_python} -m pip freeze' failed "
        f"(exit {result.returncode}): {result.stderr.strip()}"
    )
