"""Run-readiness health checks shared by ``wfc init``, ``wfc doctor``, and the canvas.

Three probe functions — :func:`check_git`, :func:`check_dvc`, and
:func:`check_docker` — each return a uniform :class:`CheckResult` with a
``status`` of ``"ok"``, ``"warn"``, or ``"fail"`` plus a plain-language
``message`` and an optional ``fix_hint``.  The probes never raise on a
not-ready environment (a missing binary is a ``fail`` result, not an
exception) and never mutate the project — they only observe.

The organizing principle (cycle decision D-3): git and Docker are BOTH hard
run-requirements that the tooling can detect and surface but cannot install.
They share the same continue-on-fail posture — probe, report, never abort.
DVC differs: it ships as a Poetry dependency, so :func:`check_dvc` only
*warns* on a missing install (D-5), never fails.

:func:`render_health_table` renders a list of results as a compact text table;
both ``wfc init`` and ``wfc doctor`` call it so the readiness definition lives
in exactly one place.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["ok", "warn", "fail"]


@dataclass
class CheckResult:
    """Outcome of a single health check.

    Attributes:
        name: Short label for the checked piece (``"git"``, ``"dvc"``,
            ``"docker"``).
        status: ``"ok"`` (ready), ``"warn"`` (degraded but not a hard gate),
            or ``"fail"`` (a hard run-requirement is not satisfied).
        message: One-line plain-language description of what was found.
        fix_hint: Optional follow-up sentence telling the user how to fix a
            ``warn``/``fail``.  Empty string when there is nothing to fix.
    """

    name: str
    status: Status
    message: str
    fix_hint: str = ""


# =============================================================================
# git
# =============================================================================

def check_git(project_dir: Path | str | None = None) -> CheckResult:
    """Probe whether the project has a git repo with a clean, committed HEAD.

    Mirrors the conditions :func:`wfc.version.get_git_commit` enforces (binary
    present, inside a work tree, has a HEAD commit, tracked tree clean) but as
    a NON-throwing probe: every not-ready state maps to a ``warn``/``fail``
    result rather than an exception.

    Args:
        project_dir: Directory within the git repository.  Defaults to the
            current working directory.

    Returns:
        A :class:`CheckResult` named ``"git"``.
    """
    cwd = str(Path(project_dir).resolve()) if project_dir is not None else None

    if shutil.which("git") is None:
        return CheckResult(
            name="git",
            status="fail",
            message="git is not installed (not found on PATH).",
            fix_hint=_git_install_hint(),
        )

    # Inside a work tree?
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd, capture_output=True, text=True,
    )
    if inside.returncode != 0:
        return CheckResult(
            name="git",
            status="fail",
            message="No git repository here — wfc versions every run by its git commit.",
            fix_hint="Run `wfc init` (it initializes git automatically) or `git init`.",
        )

    # Has a HEAD commit?
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd, capture_output=True, text=True,
    )
    if head.returncode != 0:
        return CheckResult(
            name="git",
            status="fail",
            message="Git repository has no commits yet — runs need a HEAD commit.",
            fix_hint="Make an initial commit: `git add -A && git commit -m 'init'`.",
        )

    # Tracked tree clean?  (untracked files do not block a run, matching
    # get_git_commit's porcelain filtering.)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd, capture_output=True, text=True,
    )
    if status.returncode != 0:
        return CheckResult(
            name="git",
            status="fail",
            message=f"`git status` failed: {status.stderr.strip()}",
            fix_hint="Check the repository is healthy (`git status`).",
        )
    tracked_dirty = [
        line for line in status.stdout.strip().splitlines()
        if line[:2].strip() and not line.startswith("??")
    ]
    if tracked_dirty:
        return CheckResult(
            name="git",
            status="fail",
            message="Working tree has uncommitted changes to tracked files.",
            fix_hint="Commit or stash your changes before running: `git commit -am ...`.",
        )

    return CheckResult(
        name="git",
        status="ok",
        message="git repository present with a clean, committed HEAD.",
    )


def _git_install_hint() -> str:
    """Return an OS-appropriate hint for installing git."""
    system = platform.system()
    if system == "Windows":
        return "Install Git for Windows from https://git-scm.com/download/win."
    if system == "Darwin":
        return "Install git via `xcode-select --install` or `brew install git`."
    return "Install git with your package manager (e.g. `apt install git`)."


# =============================================================================
# DVC
# =============================================================================

def check_dvc(project_dir: Path | str | None = None) -> CheckResult:
    """Probe whether DVC provenance storage is configured and reachable.

    Checks, in order: a ``[dvc] url`` is set in ``wf-canvas.toml``; ``.dvc/config``
    declares a remote; the remote is reachable (local-FS dir exists).  Per D-5
    it also defensively probes that DVC is importable / on PATH — a missing DVC
    surfaces as a ``warn`` (never a ``fail`` and never an install prompt), since
    DVC ships as a Poetry dependency and absence is a self-healable anomaly.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            current working directory.

    Returns:
        A :class:`CheckResult` named ``"dvc"``.  Never ``fail`` on a missing
        DVC install; reserved ``fail`` for genuine misconfiguration.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else Path.cwd()

    # Defensive availability probe (D-5): WARN only, never a gate.
    if not _dvc_available():
        return CheckResult(
            name="dvc",
            status="warn",
            message="DVC is not importable / not on PATH (it ships as a project dependency).",
            fix_hint="Re-install dependencies with `poetry install`.",
        )

    # Is a [dvc] url configured in wf-canvas.toml?
    try:
        from .init import read_config
        config = read_config(proj)
    except FileNotFoundError:
        return CheckResult(
            name="dvc",
            status="fail",
            message="No wfc project here (wf-canvas.toml not found).",
            fix_hint="Run `wfc init` to scaffold the project.",
        )
    dvc_config = config.get("dvc")
    if not dvc_config or not dvc_config.get("url"):
        return CheckResult(
            name="dvc",
            status="fail",
            message="No DVC archive configured ([dvc] url missing in wf-canvas.toml).",
            fix_hint="Re-run `wfc init` to write a default [dvc] archive location.",
        )

    # Is .dvc/config wired with a remote?
    from .remote import has_remote_configured
    if not has_remote_configured(proj):
        return CheckResult(
            name="dvc",
            status="fail",
            message="DVC archive declared but .dvc/config has no remote wired.",
            fix_hint="Re-run `wfc init` to initialize DVC.",
        )

    # Is the remote reachable?
    from .provenance import check_remote_reachable
    reachable, reason = check_remote_reachable(proj)
    if not reachable:
        return CheckResult(
            name="dvc",
            status="fail",
            message=f"DVC archive not reachable: {reason}",
            fix_hint="Check the archive path exists, or re-run `wfc init`.",
        )

    # Deep-validate: make DVC itself parse .dvc/config and resolve the
    # default remote.  The cheap checks above only INI-parse the file, so
    # a URL DVC's schema rejects (the historic file://C:/... form) passed
    # them and only failed at first push.  This is doctor — the one-time
    # ~4s DVC import cost is acceptable here.
    try:
        from dvc.repo import Repo
        with Repo(str(proj)) as repo:
            repo.cloud.get_remote_odb()
    except Exception as exc:
        return CheckResult(
            name="dvc",
            status="fail",
            message=f"DVC rejected the archive configuration: {exc}",
            fix_hint="Re-run `wfc init` to rewrite the archive location, or "
                     "fix the remote URL in .dvc/config and wf-canvas.toml.",
        )

    return CheckResult(
        name="dvc",
        status="ok",
        message=f"DVC archive configured and reachable ({dvc_config.get('url')}).",
    )


def _dvc_available() -> bool:
    """Return True if DVC is importable or the ``dvc`` binary is on PATH."""
    try:
        import dvc  # noqa: F401
        return True
    except Exception:
        return shutil.which("dvc") is not None


# =============================================================================
# Docker
# =============================================================================

def check_docker() -> CheckResult:
    """Probe whether the Docker daemon is installed and running.

    Execution is container-only (ADR-019 Cycle H), so a missing binary or a
    stopped daemon is a hard ``fail`` — there is no host fallback.  Verifies
    both that ``docker`` is on PATH and that ``docker info`` succeeds (which
    requires a live daemon).

    Returns:
        A :class:`CheckResult` named ``"docker"``.
    """
    if shutil.which("docker") is None:
        return CheckResult(
            name="docker",
            status="fail",
            message="Docker is not installed (not found on PATH).",
            fix_hint=_docker_install_hint(),
        )

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return CheckResult(
            name="docker",
            status="fail",
            message=f"Could not query the Docker daemon: {exc}",
            fix_hint=_docker_start_hint(),
        )

    if result.returncode != 0:
        return CheckResult(
            name="docker",
            status="fail",
            message="Docker is installed but the daemon is not running.",
            fix_hint=_docker_start_hint(),
        )

    return CheckResult(
        name="docker",
        status="ok",
        message="Docker daemon is running.",
    )


def _docker_install_hint() -> str:
    """Return an OS-appropriate hint for installing Docker."""
    system = platform.system()
    if system == "Windows":
        return "Install Docker Desktop from https://www.docker.com/products/docker-desktop/."
    if system == "Darwin":
        return "Install Docker Desktop for Mac from https://www.docker.com/products/docker-desktop/."
    return "Install Docker Engine (https://docs.docker.com/engine/install/)."


def _docker_start_hint() -> str:
    """Return an OS-appropriate hint for starting a stopped Docker daemon."""
    system = platform.system()
    if system == "Windows":
        return "Start Docker Desktop and wait for it to report 'running', then try again."
    if system == "Darwin":
        return "Start Docker Desktop and wait for it to report 'running', then try again."
    return "Start the Docker daemon (e.g. `sudo systemctl start docker`), then try again."


# =============================================================================
# Renderer
# =============================================================================

_STATUS_LABEL = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}


def render_health_table(results: list[CheckResult]) -> str:
    """Render a list of check results as a compact text health table.

    Used by both ``wfc init`` (closing summary) and ``wfc doctor`` so the
    rendering lives in one place.  Each row shows ``[STATUS] name — message``
    and, for non-ok rows, a fix-hint line.

    Args:
        results: The check results to render, in display order.

    Returns:
        A multi-line string (no trailing newline) ready to print.
    """
    lines = ["Run-readiness:"]
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        label = _STATUS_LABEL.get(r.status, r.status.upper())
        lines.append(f"  [{label:>4}] {r.name.ljust(width)}  {r.message}")
        if r.status != "ok" and r.fix_hint:
            lines.append(f"         {' ' * width}  -> {r.fix_hint}")
    return "\n".join(lines)


def run_all_checks(project_dir: Path | str | None = None) -> list[CheckResult]:
    """Run git, DVC, and Docker checks and return their results in order.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            current working directory.

    Returns:
        ``[check_git(...), check_dvc(...), check_docker()]``.
    """
    return [
        check_git(project_dir),
        check_dvc(project_dir),
        check_docker(),
    ]
