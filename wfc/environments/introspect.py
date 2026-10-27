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

A conda env captured on a host that is not linux-64 (Windows, macOS) cannot
install its own explicit list into the image, so it is solved for linux-64
instead, from what the user asked for:

  conda_env_export_from_history(env_path) -> str
      ``conda env export --from-history``: the requested packages and the
      channels.
  conda_list_json(env_path) -> list[dict]
      ``conda list --json``: every installed package, with its version and
      channel (``pypi`` for a pip-installed one).
  conda_version() -> str
      The host's conda version.
  solve_capture_source(...) -> dict
      The register source payload of a solved env (:class:`SolveSpec`, the
      pip set and the host's capture), built from those answers.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import layout
from .host import IMAGE_CONDA_PLATFORM

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


# =============================================================================
# Solve capture: a conda env from a host that is not linux-64
# =============================================================================

#: The register source key whose presence marks a conda payload to be solved
#: for linux-64; its value is the :class:`SolveSpec`.
SOLVE_SOURCE_KEY = "conda_solve"
#: The register source key holding the host's own capture, by file name inside
#: the captures folder's ``host/`` directory.
HOST_CAPTURE_SOURCE_KEY = "host_capture"

#: The channel ``conda list --json`` names for a pip-installed package.
_PYPI_CHANNEL = "pypi"

# A --from-history request: an optional `channel::` prefix, then the name;
# whatever follows (`=3.11`, `>=1.10`, `[version=...]`) is the constraint.
_REQUEST = re.compile(r"^(?:(?P<channel>.+?)::)?(?P<name>[A-Za-z0-9_][A-Za-z0-9_.\-]*)")


#: Seconds each conda call of a solve capture may take before it is stopped.
CONDA_TIMEOUT_S = 300.0


def _run_conda(args: list[str], what: str) -> str:
    """Run ``conda <args>`` and return its stdout.

    Args:
        args: The arguments after ``conda``.
        what: The capture being made, for the error messages.

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero, or does not finish within
            :data:`CONDA_TIMEOUT_S`.
    """
    try:
        result = subprocess.run(
            ["conda", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONDA_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"conda executable not found on PATH — cannot read {what}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"`conda {' '.join(args)}` did not finish within "
            f"{CONDA_TIMEOUT_S:g} s; cannot read {what}"
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"conda {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def conda_env_export_from_history(env_path: str | Path) -> str:
    """Return ``conda env export --from-history`` for the env at *env_path*.

    The packages the user asked for, with the constraints they gave, and the
    env's channels. On Windows the text may carry a ``prefix:`` line.

    Args:
        env_path: The conda env prefix.

    Returns:
        The export's environment.yml text.

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero.
    """
    return _run_conda(
        ["env", "export", "--from-history", "--prefix", str(env_path)],
        f"the requested packages of the env at {env_path}")


def conda_list_json(env_path: str | Path) -> list[dict]:
    """Return ``conda list --json`` for the env at *env_path*.

    Args:
        env_path: The conda env prefix.

    Returns:
        One dict per installed package, with at least ``name``, ``version``
        and ``channel`` (``pypi`` for a pip-installed package).

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero.
        ValueError: If the output is not a JSON list.
    """
    text = _run_conda(["list", "--json", "--prefix", str(env_path)],
                      f"the packages of the env at {env_path}")
    packages = json.loads(text)
    if not isinstance(packages, list):
        raise ValueError(f"conda list --json for {env_path} is not a list")
    return packages


def conda_version() -> str:
    """Return the host's conda version, e.g. ``"24.11.3"``.

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero.
    """
    out = _run_conda(["--version"], "the conda version").strip()
    return out.removeprefix("conda").strip()


@dataclass(frozen=True)
class SolveSpec:
    """The spec a solved conda env is installed from.

    Attributes:
        channels: The channels, in order, as the host's export names them
            (a ``defaults`` channel is kept).
        specs: Each requested package as ``[channel::]name=<version>``, the
            version the host has installed, with no build string.
        platform: The platform it is solved for.
    """

    channels: tuple[str, ...]
    specs: tuple[str, ...]
    platform: str = IMAGE_CONDA_PLATFORM

    def to_yaml(self) -> str:
        """Return the spec as environment.yml text (channels, dependencies), LF."""
        lines = ["channels:", *(f"  - {c}" for c in self.channels),
                 "dependencies:", *(f"  - {s}" for s in self.specs)]
        return "\n".join(lines) + "\n"


def _conda_packages(installed: list[dict]) -> list[dict]:
    """The conda-installed (not pip-installed) entries of a ``conda list --json``."""
    return [p for p in installed if p.get("channel") != _PYPI_CHANNEL]


def _channel_of(package: dict) -> str:
    """The channel name to solve from for an installed conda package."""
    channel = str(package.get("channel") or "")
    # Anaconda's own channels (pkgs/main, pkgs/r, pkgs/msys2) are `defaults`.
    return "defaults" if channel.startswith("pkgs/") else channel


def pinned_spec(from_history: str, installed: list[dict]) -> SolveSpec:
    """Pin each requested package to the version the host has installed.

    Args:
        from_history: ``conda env export --from-history`` text (CRLF and a
            ``prefix:`` line are fine).
        installed: ``conda list --json`` for the same env.

    Returns:
        The :class:`SolveSpec`: the export's channels (or, when it names
        none, the channels the requested packages came from) and one
        ``[channel::]name=<installed version>`` per request, in the export's
        order.

    Raises:
        ValueError: The export cannot be read, or a requested package is not
            installed; the message names it.
    """
    import yaml

    doc = yaml.safe_load(from_history.replace("\r\n", "\n")) or {}
    if not isinstance(doc, dict):
        raise ValueError("conda env export --from-history did not return a "
                         "YAML mapping")
    by_name = {str(p.get("name", "")).lower(): p for p in _conda_packages(installed)}

    specs: list[str] = []
    requested: list[dict] = []
    seen: set[str] = set()
    for entry in doc.get("dependencies") or []:
        if not isinstance(entry, str):
            continue  # a pip: section; --from-history writes none
        match = _REQUEST.match(entry.strip())
        if match is None:
            raise ValueError(f"cannot read the requested package {entry!r}")
        name = match["name"].lower()
        if name in seen:
            continue
        seen.add(name)
        package = by_name.get(name)
        if package is None:
            raise ValueError(
                f"The env's history requests {entry!r}, but no {name!r} "
                f"package is installed in it, so there is no version to pin. "
                f"Install it again, or remove it from the env, then register."
            )
        prefix = f"{match['channel']}::" if match["channel"] else ""
        specs.append(f"{prefix}{name}={package['version']}")
        requested.append(package)

    channels = [str(c) for c in doc.get("channels") or []]
    if not channels:
        channels = list(dict.fromkeys(_channel_of(p) for p in requested
                                      if _channel_of(p)))
    return SolveSpec(channels=tuple(channels), specs=tuple(specs))


def pypi_pip_set(installed: list[dict]) -> str:
    """Return the pip set: the pip-installed packages, pinned, one per line.

    Only the ``pypi``-channel entries of ``conda list --json``; packages
    conda installed (which ``pip freeze`` shows as ``@ file:///`` lines)
    come from the solve instead.

    Args:
        installed: ``conda list --json`` for the env.

    Returns:
        ``name==version`` lines sorted by name, LF, or ``""`` when nothing
        was pip-installed.
    """
    pins = sorted(f"{p['name']}=={p['version']}" for p in installed
                  if p.get("channel") == _PYPI_CHANNEL)
    return "".join(f"{pin}\n" for pin in pins)


def solve_capture_source(*, explicit_list: str, from_history: str,
                         installed: list[dict], pip_freeze: str,
                         host_platform: str, conda_version: str,
                         captured_at: str) -> dict:
    """Return the register source payload of a conda env solved for linux-64.

    Args:
        explicit_list: The host's ``conda list --explicit --md5``.
        from_history: The host's ``conda env export --from-history``.
        installed: The host's ``conda list --json``.
        pip_freeze: The host's full ``pip freeze``, or
            :data:`PIP_MISSING_SENTINEL` for an env without python.
        host_platform: The host's conda platform, e.g. ``"win-64"``.
        conda_version: The host's conda version.
        captured_at: The capture time, ISO 8601 with a UTC offset.

    Returns:
        ``{SOLVE_SOURCE_KEY: SolveSpec, "pip_freeze_content": <pip set>,
        HOST_CAPTURE_SOURCE_KEY: {<host/ file name>: <text>}}``. There is no
        ``explicit_list_content``: the image's own linux-64 list is read back
        after the build. The host's texts are kept as given.

    Raises:
        ValueError: As :func:`pinned_spec` raises.
    """
    spec = pinned_spec(from_history, installed)
    pip_set = (PIP_MISSING_SENTINEL if pip_freeze == PIP_MISSING_SENTINEL
               else pypi_pip_set(installed))
    host_info = json.dumps({"platform": host_platform,
                            "conda_version": conda_version,
                            "captured_at": captured_at},
                           indent=2, sort_keys=True) + "\n"
    return {
        SOLVE_SOURCE_KEY: spec,
        "pip_freeze_content": pip_set,
        HOST_CAPTURE_SOURCE_KEY: {
            layout.CAPTURE_HOST_EXPLICIT_LIST_FILENAME: explicit_list,
            layout.CAPTURE_HOST_FROM_HISTORY_FILENAME: from_history,
            layout.CAPTURE_HOST_PINNED_SPEC_FILENAME: spec.to_yaml(),
            layout.CAPTURE_HOST_PIP_FREEZE_FILENAME: pip_freeze,
            layout.CAPTURE_HOST_INFO_FILENAME: host_info,
        },
    }
