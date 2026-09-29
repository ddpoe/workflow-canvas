"""Run-readiness probes shared by ``wfc init``, ``wfc doctor``, and the canvas.

Four probe functions — :func:`check_git`, :func:`check_dvc`,
:func:`check_docker` and :func:`check_samples` — each return a uniform
:class:`CheckResult` with a ``status`` of ``"ok"``, ``"warn"``, or ``"fail"``
plus a plain-language ``message`` and an optional ``fix_hint``.  The probes
never raise on a not-ready environment (a missing binary is a ``fail``
result, not an exception) and never mutate the project — they only observe.

The organizing principle: git and Docker are BOTH hard
run-requirements that the tooling can detect and surface but cannot install.
They share the same continue-on-fail posture — probe, report, never abort.
DVC differs: it ships as a Poetry dependency, so :func:`check_dvc` only
*warns* on a missing install, never fails.  :func:`check_samples` is about
the project's data rather than its tooling: it runs Registration's
reachability table over every ``Sample`` row and is the one surface that
shows a sample's health at all.

:func:`run_all_checks` runs the four in display order; every caller renders
the results through :func:`wfc.preflight.render_health_table`, so the
readiness definition lives in exactly one place.
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


def default_project_dir() -> Path:
    """Return the project a readiness check probes when none is named.

    The resolved project root (``WFC_PROJECT_ROOT``, else the nearest
    directory at or above the working directory holding
    ``.wfc/wf-canvas.toml``), so ``wfc doctor`` run from a subdirectory
    checks the project it is in. Outside any project there is no root to
    resolve; the working directory is returned, and the checks report that
    it is not a project.

    Returns:
        The directory to probe.
    """
    from ..persistence import project_root
    try:
        return project_root()
    except RuntimeError:
        return Path.cwd()

def check_git(project_dir: Path | str | None = None) -> CheckResult:
    """Probe whether the project has a git repo with a clean, committed HEAD.

    Mirrors the conditions :func:`wfc.version.get_git_commit` enforces (binary
    present, inside a work tree, has a HEAD commit, tracked tree clean) but as
    a NON-throwing probe: every not-ready state maps to a ``warn``/``fail``
    result rather than an exception.

    Args:
        project_dir: Directory within the git repository.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

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
    declares a remote; the remote is reachable (local-FS dir exists).  It
    also defensively probes that DVC is importable / on PATH — a missing DVC
    surfaces as a ``warn`` (never a ``fail`` and never an install prompt), since
    DVC ships as a Poetry dependency and absence is a self-healable anomaly.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        A :class:`CheckResult` named ``"dvc"``.  Never ``fail`` on a missing
        DVC install; reserved ``fail`` for genuine misconfiguration.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()

    # Defensive availability probe: WARN only, never a gate.
    if not _dvc_available():
        return CheckResult(
            name="dvc",
            status="warn",
            message="DVC is not importable / not on PATH (it ships as a project dependency).",
            fix_hint="Re-install dependencies with `poetry install`.",
        )

    # Is a [dvc] url configured in wf-canvas.toml?
    try:
        from ..persistence import read_config
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
    from ..storage import has_remote_configured
    if not has_remote_configured(proj):
        return CheckResult(
            name="dvc",
            status="fail",
            message="DVC archive declared but .dvc/config has no remote wired.",
            fix_hint="Re-run `wfc init` to initialize DVC.",
        )

    # Is the remote reachable?
    from ..storage import check_remote_reachable
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
    # a URL DVC's schema rejects (e.g. a file://C:/... form) passes them
    # and only fails at first push.  This is doctor — the one-time
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
# Samples
# =============================================================================

#: How many offenders a sample fix-hint names before it says "and N more".
_HINT_NAMES = 3


def _name_list(entries) -> str:
    """Render up to :data:`_HINT_NAMES` sample names, then an overflow count.

    Args:
        entries: The ``SampleHealth`` entries to name.

    Returns:
        A comma-joined name list, e.g. ``"a, b, c and 2 more"``.
    """
    names = [e.name for e in entries]
    shown = ", ".join(names[:_HINT_NAMES])
    extra = len(names) - _HINT_NAMES
    return f"{shown} and {extra} more" if extra > 0 else shown


def check_samples(project_dir: Path | str | None = None) -> CheckResult:
    """Report whether every registered sample's content is still reachable.

    Runs :func:`wfc.registration.classify_sample`'s table -- local cache
    presence x ``push_status``, no network I/O -- over every ``Sample`` row
    and reports the four counts.  It is the only surface that shows a
    sample's health: registration prints one stderr warning when a push
    fails and nothing mentions the row again, so the single most
    consequential state (*this project is not reproducible on another
    machine*) was recorded and never shown.

    ``fail`` on any unreachable sample, ``warn`` on any local-only one,
    ``ok`` otherwise.  A local miss on a pushed row -- the documented cold
    start -- is counted and is neither a warn nor a fail, because the next
    run's restore pulls it.

    The rows and the files are read from the SAME project.  Every other
    input this check has -- the config probe, the cache-presence test each
    ``classify_sample`` verdict turns on -- is path-resolved against
    ``project_dir``, so reading rows from the process-bound engine instead
    would pair one project's registry with another project's files.  That
    is not hypothetical: ``wfc init`` builds the new project's database
    through a one-off engine and disposes of it, leaving the process bound
    wherever it already was, so ``wfc init --dir <elsewhere>`` run from
    outside a project reported on a database that was not the one it had
    just created.  :func:`~wfc.persistence.use_project` binds the two
    together for the read.

    An explicit ``DATABASE_URL`` still wins: it is the operator saying
    which database to read, and a check is not the place to overrule it.
    In that case the process engine is used as-is, which is what every
    other read in the process does under the same override.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        A :class:`CheckResult` named ``"samples"``.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()

    # Same project probe as check_dvc, so the two agree about where a
    # project is: outside one there is no registry to read and nothing this
    # check could say.
    try:
        from ..persistence import read_config
        read_config(proj)
    except FileNotFoundError:
        return CheckResult(
            name="samples",
            status="fail",
            message="No wfc project here (wf-canvas.toml not found).",
            fix_hint="Run `wfc init` to scaffold the project.",
        )

    import os
    from contextlib import nullcontext

    from .. import layout
    from ..persistence import get_session, use_project
    from ..persistence.engine import OVERRIDE_ENV_VAR
    from ..registration.sample_health import load_sample_health

    # An explicit DATABASE_URL is the operator naming the database; leave
    # the process engine alone.  Otherwise bind rows and files together.
    override = os.environ.get(OVERRIDE_ENV_VAR)
    if not override and not layout.db_path(proj).exists():
        # use_project would bootstrap the schema, so a health check would
        # CREATE the database it came to report on.  Say what is true
        # instead.
        return CheckResult(
            name="samples",
            status="warn",
            message=f"No sample registry: {layout.db_path(proj)} does not exist.",
            fix_hint="Run `wfc init` here to create the project's database.",
        )

    bind = nullcontext() if override else use_project(proj)
    try:
        with bind:
            with get_session() as session:
                health = load_sample_health(session, proj)
    except Exception as exc:
        return CheckResult(
            name="samples",
            status="warn",
            message=f"Could not read the sample registry: {exc}",
            fix_hint=(
                f"The message above is what the read of "
                f"{layout.db_path(proj)} raised; it names the failure. The "
                f"other checks are unaffected."
            ),
        )

    if not health:
        return CheckResult(
            name="samples",
            status="ok",
            message="No samples registered.",
        )

    healthy = [e for e in health if e.state == "healthy"]
    cold = [e for e in health if e.state == "cold"]
    local_only = [e for e in health if e.state == "local_only"]
    unreachable = [e for e in health if e.state == "unreachable"]

    message = (
        f"{len(health)} registered: {len(healthy)} healthy, "
        f"{len(cold)} cached elsewhere only (will pull on the next run), "
        f"{len(local_only)} local-only (not reproducible elsewhere), "
        f"{len(unreachable)} unreachable."
    )

    if unreachable:
        return CheckResult(
            name="samples",
            status="fail",
            message=message,
            fix_hint=(
                f"Content is in neither the local cache nor the archive for "
                f"{_name_list(unreachable)} — re-register from source: "
                f"`wfc register-sample --name {unreachable[0].name} "
                f"--source <path>`."
            ),
        )

    if local_only:
        return CheckResult(
            name="samples",
            status="warn",
            message=message,
            fix_hint=(
                f"{_name_list(local_only)} exist only on this machine — "
                f"re-register to push to the archive: "
                f"`wfc register-sample --name {local_only[0].name} "
                f"--source <path>`."
            ),
        )

    return CheckResult(name="samples", status="ok", message=message)


# =============================================================================
# Docker
# =============================================================================

def check_docker() -> CheckResult:
    """Probe whether the Docker daemon is installed and running.

    Execution is container-only, so a missing binary or a
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


def run_all_checks(project_dir: Path | str | None = None) -> list[CheckResult]:
    """Run the git, DVC, Docker and sample checks and return them in order.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        ``[check_git(...), check_dvc(...), check_docker(), check_samples(...)]``.
    """
    return [
        check_git(project_dir),
        check_dvc(project_dir),
        check_docker(),
        check_samples(project_dir),
    ]
