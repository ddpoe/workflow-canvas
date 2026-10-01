"""Host env resolvers for live capture: the interpreter, or the env directory, an env spec names.

``resolve_python_for_env`` — the python binary for a pixi env spec.
``resolve_conda_env_dir`` — a conda env's prefix, without requiring a python.

``wfc register-env <name> <spec>`` resolves the live host env through these
before it captures the env's package list.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# =============================================================================
# Environment resolution
# =============================================================================

def _find_python_in_env(env_dir: Path) -> Path:
    """Locate the python executable inside a conda/pixi environment directory.

    Handles both Unix (``bin/python``) and Windows (``python.exe`` at env
    root, or ``Scripts/python.exe``) layouts.

    Returns:
        Absolute ``Path`` to the python executable.

    Raises:
        ValueError: If no python binary is found.
    """
    import sys as _sys

    if _sys.platform == "win32":
        candidates = [env_dir / "python.exe", env_dir / "Scripts" / "python.exe"]
    else:
        candidates = [env_dir / "bin" / "python"]

    for c in candidates:
        if c.exists():
            return c.resolve()

    searched = ", ".join(str(c) for c in candidates)
    raise ValueError(
        f"Python executable not found. Searched: {searched}. "
        f"The environment directory {env_dir} exists but the python "
        f"binary is missing."
    )


def _default_root() -> Path:
    """Return the resolved project root, for a caller that named none."""
    from ..persistence import project_root
    return project_root()


def _local_pixi_env_dir(env: str, project_dir: Path | None = None) -> Path | None:
    """Return ``<project_dir>/.pixi/envs/<env>`` when it exists, else ``None``.

    Pixi's per-project local layout puts envs at ``<project>/.pixi/envs/<env>``
    with no project-name prefix in the path. Used as a fallback after the
    configured ``[pixi].root`` glob fails, so locally-installed envs
    work even when the user hasn't set a global pixi root.
    """
    base = Path(project_dir) if project_dir is not None else _default_root()
    candidate = base / ".pixi" / "envs" / env
    return candidate if candidate.exists() else None


def _resolve_pixi_standalone(
    name: str,
    pixi_root: str | Path | None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a standalone pixi project env.

    Resolution cascade:

    1. Configured ``[pixi].root`` glob: ``{pixi_root}/{name}-*/envs/default``.
    2. Local fallback: ``<project_dir>/.pixi/envs/default`` (when the env
       was installed via ``pixi install`` inside the project itself).
    """
    pixi_root_path: Path | None = Path(pixi_root) if pixi_root else None
    pattern = f"{name}-*/envs/default"
    matches = sorted(pixi_root_path.glob(pattern)) if pixi_root_path else []

    if len(matches) == 1:
        return _find_python_in_env(matches[0])
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise ValueError(
            f"Multiple pixi environments match '{name}':\n  {listing}\n"
            f"Remove duplicates so only one {name}-* directory exists "
            f"under {pixi_root_path}."
        )

    # Zero matches in pixi_root — try the local project fallback.
    local = _local_pixi_env_dir("default", project_dir)
    if local is not None:
        return _find_python_in_env(local)

    searched: list[str] = []
    if pixi_root_path is not None:
        searched.append(str(pixi_root_path / pattern))
    searched.append(str((project_dir or _default_root()) / ".pixi" / "envs" / "default"))
    raise ValueError(
        f"No pixi environment found for '{name}'.\n"
        f"Searched:\n  " + "\n  ".join(searched) + "\n"
        "Run `pixi install` in the environment directory first, "
        "or set [pixi] root in .wfc/wf-canvas.toml."
    )


def _resolve_pixi_project_env(
    project: str,
    env: str,
    pixi_root: str | Path | None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a pixi project + env.

    Resolution cascade:

    1. Configured ``[pixi].root`` glob: ``{pixi_root}/{project}-*/envs/{env}``.
    2. Local fallback: ``<project_dir>/.pixi/envs/{env}`` (when the pixi
       project lives inside the wfc project itself).
    """
    pixi_root_path: Path | None = Path(pixi_root) if pixi_root else None
    pattern = f"{project}-*/envs/{env}"
    matches = sorted(pixi_root_path.glob(pattern)) if pixi_root_path else []

    if len(matches) == 1:
        return _find_python_in_env(matches[0])
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise ValueError(
            f"Multiple pixi environments match '{project}:{env}':\n  {listing}\n"
            f"Remove duplicates so only one {project}-* directory exists "
            f"under {pixi_root_path}."
        )

    # Zero matches in pixi_root — try the local project fallback.
    local = _local_pixi_env_dir(env, project_dir)
    if local is not None:
        return _find_python_in_env(local)

    searched: list[str] = []
    if pixi_root_path is not None:
        searched.append(str(pixi_root_path / pattern))
    searched.append(str((project_dir or _default_root()) / ".pixi" / "envs" / env))
    raise ValueError(
        f"No pixi environment found for project '{project}', env '{env}'.\n"
        f"Searched:\n  " + "\n  ".join(searched) + "\n"
        "Run `pixi install` in the project directory first, "
        "or set [pixi] root in .wfc/wf-canvas.toml."
    )


def resolve_conda_env_dir(name: str, conda_root: str | Path | None = None) -> Path:
    """Resolve a conda environment *directory* by name (no python required).

    Checks ``{conda_root}/envs/{name}``.  If ``conda_root`` is not
    configured, auto-detects via ``conda info --base``. Unlike
    :func:`resolve_python_for_env`, this does not require a python binary
    inside the env — needed for live-capture of python-free envs (e.g. an
    R-only conda-forge env).

    Args:
        name: Conda environment name.
        conda_root: Conda base directory; auto-detected when ``None``.

    Returns:
        Absolute path to the environment directory.

    Raises:
        ValueError: If the environment directory does not exist (or conda
            root cannot be detected).
    """
    if not conda_root:
        conda_root = _detect_conda_root()
    conda_root = Path(conda_root)
    env_dir = conda_root / "envs" / name

    if not env_dir.exists():
        raise ValueError(
            f"No conda environment '{name}' found at {env_dir}. "
            f"Create it with `conda create -n {name} python` first."
        )
    return env_dir


def _detect_conda_root() -> str:
    """Auto-detect conda base directory via ``conda info --base``."""
    try:
        result = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise ValueError(
        "Cannot auto-detect conda root (conda not found or not on PATH). "
        "Set [conda] root in .wfc/wf-canvas.toml."
    )


def resolve_python_for_env(
    env_spec: str,
    pixi_root: str | Path | None = None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve a python executable from a typed pixi env specifier.

    Supported prefixes::

        pixi:<project>:<env>   pixi project with explicit env name
        pixi:<name>            standalone pixi project (default env)

    Any other spec raises ``ValueError`` with guidance. A conda env is
    resolved by its prefix instead (:func:`resolve_conda_env_dir`), because
    a python-free conda env has no interpreter to find.

    Resolution cascades from the configured ``[pixi].root`` glob to a
    local ``<project_dir>/.pixi/envs/<env>`` fallback so locally-installed
    envs work even without a configured global pixi root.

    Args:
        env_spec: Typed env string (e.g. ``"pixi:image-io"``).
        pixi_root: Absolute path to pixi environment root.
        project_dir: wfc project root, used to find a local
            ``.pixi/envs/<env>`` fallback when ``pixi_root`` is unset or
            empty. Defaults to cwd.

    Returns:
        Absolute ``Path`` to the python executable.

    Raises:
        ValueError: If the spec is not a pixi spec, or the env cannot be
            found.
    """
    parts = env_spec.split(":")
    if parts[0] == "pixi" and len(parts) == 3:
        # pixi:<project>:<env>
        return _resolve_pixi_project_env(
            parts[1], parts[2], pixi_root, project_dir=project_dir
        )
    elif parts[0] == "pixi" and len(parts) == 2:
        # pixi:<name>  (standalone, default env)
        return _resolve_pixi_standalone(
            parts[1], pixi_root, project_dir=project_dir
        )
    else:
        raise ValueError(
            f"Unknown env spec '{env_spec}'. Use a typed pixi prefix: "
            f"'pixi:<project>:<env>' or 'pixi:<name>'."
        )
