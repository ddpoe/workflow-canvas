"""Run-readiness probes shared by ``wfc init``, ``wfc doctor``, and the canvas.

Five probe functions — :func:`check_git`, :func:`check_dvc`,
:func:`check_docker`, :func:`check_samples` and :func:`check_views` — each
return a uniform :class:`CheckResult` with a ``status`` of ``"ok"``,
``"warn"``, or ``"fail"`` plus a plain-language ``message`` and an optional
``fix_hint``.  The probes never raise on a not-ready environment (a missing
binary is a ``fail`` result, not an exception).  They write no row, config
or tracked file: :func:`check_views` writes a scratch file into each
directory it probes and removes it again, :func:`check_samples` reads the
database only when it already exists (it never creates one), and git and
DVC keep whatever housekeeping files they write when queried.

The organizing principle: git and Docker are BOTH hard
run-requirements that the tooling can detect and surface but cannot install.
They share the same continue-on-fail posture — probe, report, never abort.
DVC differs: it ships as a Poetry dependency, so :func:`check_dvc` only
*warns* on a missing install, never fails.  :func:`check_samples` is about
the project's data rather than its tooling: it runs Registration's
reachability table over every ``Sample`` row and is the one surface that
shows a sample's health at all.

:func:`run_all_checks` runs the five in display order; every caller renders
the results through :func:`wfc.preflight.render_health_table`, so the
readiness definition lives in exactly one place.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..environments import GhLogin

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
    from ..version import GitStatusError, git_installed, has_head_commit, in_work_tree, tracked_changes

    cwd = Path(project_dir).resolve() if project_dir is not None else Path.cwd()

    if not git_installed():
        return CheckResult(
            name="git",
            status="fail",
            message="git is not installed (not found on PATH).",
            fix_hint=_git_install_hint(),
        )

    # Inside a work tree?
    if not in_work_tree(cwd):
        return CheckResult(
            name="git",
            status="fail",
            message="No git repository here — wfc versions every run by its git commit.",
            fix_hint="Run `wfc init` (it initializes git automatically) or `git init`.",
        )

    # Has a HEAD commit?
    if not has_head_commit(cwd):
        return CheckResult(
            name="git",
            status="fail",
            message="Git repository has no commits yet — runs need a HEAD commit.",
            fix_hint="Make an initial commit: `git add -A && git commit -m 'init'`.",
        )

    # Tracked tree clean?  (untracked files do not block a run, matching
    # get_git_commit's porcelain filtering.)
    try:
        tracked_dirty = tracked_changes(cwd)
    except GitStatusError as exc:
        return CheckResult(
            name="git",
            status="fail",
            message=f"`git status` failed: {exc.stderr}",
            fix_hint="Check the repository is healthy (`git status`).",
        )
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


# =============================================================================
# Env images: the registry, and the versions that cannot run
# =============================================================================
#
# Two rows ``wfc doctor`` prints after :func:`run_all_checks`; ``wfc share``
# prints the second and splits the first into its own rows (see
# :func:`share_checks`). They are not in :func:`run_all_checks`:
# the registry row asks the network, and ``wfc init``'s summary and the
# canvas's run gate share that function. Both rows only warn.

def _no_project(name: str) -> CheckResult:
    """The warn both env-image rows give outside a wfc project."""
    return CheckResult(
        name=name,
        status="warn",
        message="No wfc project here (wf-canvas.toml not found).",
        fix_hint="Run `wfc init` to scaffold the project.",
    )


def _versions_by_env(proj: Path) -> dict[str, list[tuple[str, str]]]:
    """Return ``env -> [(module/method vN, env_fingerprint)]`` for every env.

    The envs are Environments' (the manifest and the captures folders); the
    versions recording each are Registration's, read from the committed
    version lists, as ``wfc push-env --all`` reads them.

    Args:
        proj: The project root.

    Returns:
        The versions recording each env.

    Raises:
        ValueError: The env manifest or a version list cannot be read.
    """
    from ..environments import pushable_envs
    from ..registration import version_fingerprints_recording_env

    return {name: version_fingerprints_recording_env(name, proj)
            for name in pushable_envs(proj)}


def check_registry(project_dir: Path | str | None = None) -> CheckResult:
    """Report whether the project's env images can be pulled from its registry.

    With no ``[registry] url`` the images stay on this machine and the row
    is ``ok``. With a base, the registry is asked once through the Docker
    CLI (Environments' ``probe_registry``, by a pushed image's own ref when
    there is one) and every env image on this machine that is not recorded
    as pushed is named. Either problem is a ``warn`` naming its fix:
    ``docker login <host>``, ``wfc push-env --all``, and ``wfc share`` for
    the rest of the project's sharing setup. Never ``fail``.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        A :class:`CheckResult` named ``"registry"``.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()
    from ..environments import registry_base

    try:
        base = registry_base(proj)
    except FileNotFoundError:
        return _no_project(name="registry")
    except ValueError as exc:
        return CheckResult(name="registry", status="warn",
                           message=f"Could not read the project's config: {exc}",
                           fix_hint="Fix .wfc/wf-canvas.toml, then run `wfc doctor` again.")
    if not base:
        return CheckResult(
            name="registry", status="ok",
            message="No registry set: env images stay on this machine.")

    from ..environments import (
        IMAGE_LOCAL,
        IMAGE_PUSHED,
        CaptureError,
        plan_push,
        probe_registry,
        pullable_ref,
        registry_host,
        unpushable_lines,
    )

    share = "`wfc share` checks the rest of the project's sharing setup."
    try:
        plan = plan_push(proj, None, recorded=_versions_by_env(proj))
        ref = next((pullable_ref(item.env, item.fingerprint, item.image or "", proj)
                    for item in plan.items if item.state == IMAGE_PUSHED), None)
    except (ValueError, CaptureError) as exc:
        return CheckResult(name="registry", status="warn",
                           message=f"Could not list the env images: {exc}",
                           fix_hint=share)
    try:
        answered, reason = probe_registry(base, ref=ref)
    except (RuntimeError, OSError) as exc:
        answered, reason = False, (f"the registry {base} could not be asked: {exc}")
    if not answered:
        return CheckResult(
            name="registry", status="warn", message=reason,
            fix_hint=f"Run `docker login {registry_host(base)}`, then "
                     f"`wfc push-env --all` to push what is still local. {share}")
    # Images on this machine push-env cannot push: a name no registry
    # accepts, or no captures folder to record the push in.
    stuck = [item for item in plan.unreachable if item.state == IMAGE_LOCAL]
    if plan.to_push or stuck:
        parts, fixes = [], []
        if plan.to_push:
            labels = ", ".join(item.label for item in plan.to_push)
            parts.append(f"{len(plan.to_push)} env image(s) on this machine are "
                         f"not in the registry {base}: {labels}.")
            fixes.append("Run `wfc push-env --all` to push them.")
        if stuck:
            parts.append(f"{len(stuck)} env image(s) on this machine cannot be "
                         f"pushed: " + ", ".join(item.label for item in stuck) + ".")
            fixes.extend(line.removeprefix("ERROR: ")
                         for line in unpushable_lines(plan, stuck))
        return CheckResult(name="registry", status="warn", message=" ".join(parts),
                           fix_hint=" ".join([*fixes, share]))
    return CheckResult(
        name="registry", status="ok",
        message=f"The registry {base} answers, and every env image on this "
                f"machine is pushed.")


def check_runnable_versions(project_dir: Path | str | None = None) -> CheckResult:
    """List the method versions whose image is neither here nor in the registry.

    For every image a method version records, Environments says where it
    is: recorded as pushed (``pushed.json``) or a byo registry image counts
    as in the registry; otherwise the local daemon is asked. No registry is
    contacted. A version whose image is in neither place cannot run on this
    machine or any other; the row names each such version with the command
    that recreates its env and ``wfc register-method``. With Docker not
    answering the row says it cannot tell; a version whose image record
    cannot be read is named as such. Only ``ok`` or ``warn``.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        A :class:`CheckResult` named ``"versions"``.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()
    from ..persistence import read_config

    try:
        read_config(proj)
    except FileNotFoundError:
        return _no_project("versions")
    except ValueError:
        pass  # the env and version reads below say what they can

    from ..environments import (
        IMAGE_MISSING,
        IMAGE_UNKNOWN,
        CaptureError,
        image_state,
        list_envs,
        recreate_command,
    )

    try:
        records = dict(list_envs(proj))
        recorded = _versions_by_env(proj)
    except ValueError as exc:
        return CheckResult(name="versions", status="warn",
                           message=f"Could not read the method versions: {exc}",
                           fix_hint="The message above names the file that "
                                    "could not be read.")

    gone: dict[str, list[str]] = {}
    unknown: dict[str, list[str]] = {}
    unreadable: dict[str, list[str]] = {}
    unreadable_why: list[str] = []
    total = 0
    for env, entries in recorded.items():
        by_image: dict[str, list[str]] = {}
        for label, fingerprint in entries:
            if fingerprint:
                by_image.setdefault(fingerprint, []).append(label)
        record = records.get(env)
        for fingerprint, labels in by_image.items():
            total += len(labels)
            current = record is not None and fingerprint == record.env_fingerprint
            try:
                state = image_state(env, fingerprint, proj,
                                    container=(record.container
                                               if record is not None and current
                                               else None))
            except CaptureError as exc:
                unreadable.setdefault(env, []).extend(labels)
                unreadable_why.append(str(exc))
                continue
            if state.state == IMAGE_MISSING:
                gone.setdefault(env, []).extend(labels)
            elif state.state == IMAGE_UNKNOWN:
                unknown.setdefault(env, []).extend(labels)

    if not gone and not unknown and not unreadable:
        if not total:
            return CheckResult(name="versions", status="ok",
                               message="No method version records an env image.")
        return CheckResult(
            name="versions", status="ok",
            message=f"{total} method version(s): every image is on this "
                    f"machine or in the registry.")

    parts: list[str] = []
    hints: list[str] = []
    if gone:
        names = ", ".join(label for labels in gone.values() for label in labels)
        parts.append(f"{sum(map(len, gone.values()))} method version(s) can't run "
                     f"here or anywhere else, their images neither on this "
                     f"machine nor in the registry: {names}.")
        for env, labels in gone.items():
            hints.append(f"for {', '.join(labels)}: recreate the image with "
                         f"`{recreate_command(env)}`, then register those "
                         f"methods again with `wfc register-method`")
    if unknown:
        names = ", ".join(label for labels in unknown.values() for label in labels)
        parts.append(f"Docker did not answer, so wfc cannot tell whether the "
                     f"images of {names} are on this machine.")
        hints.append("start Docker and run `wfc doctor` again to tell")
    if unreadable:
        names = ", ".join(label for labels in unreadable.values() for label in labels)
        parts.append(f"The image records of {names} cannot be read, so wfc "
                     f"cannot tell where their images are: "
                     f"{'; '.join(unreadable_why)}.")
        hints.append("fix or remove the broken record the message names, then "
                     "run `wfc doctor` again")
    hint = "; ".join(hints)
    return CheckResult(name="versions", status="warn", message=" ".join(parts),
                       fix_hint=hint[:1].upper() + hint[1:] + ".")


def env_image_checks(project_dir: Path | str | None = None) -> list[CheckResult]:
    """Run the registry and runnable-versions rows, in display order.

    Not part of :func:`run_all_checks`: ``wfc doctor`` prints these after it.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        ``[check_registry(...), check_runnable_versions(...)]``.
    """
    return [check_registry(project_dir), check_runnable_versions(project_dir)]


# =============================================================================
# wfc share: what a collaborator will be missing
# =============================================================================
#
# ``wfc share``'s checklist: each row asks the unit that owns its answer
# (the git remote: ``wfc.version``; the base: the project's config; the
# login probe, the images and the package connections: Environments; the
# versions that cannot run: :func:`check_runnable_versions`). The rows only
# warn and nothing is written: no commit, no push, no ``docker login``, and
# ``gh auth token`` is printed for the user to run, never run.

class _ShareFacts:
    """What the share rows read once and share between them."""

    def __init__(self, proj: Path, base: str | None) -> None:
        from ..environments import project_github_repository

        self.proj = proj
        self.base = base
        self.repository = project_github_repository(proj)
        # The base `wfc push-env --all` pushes to: the plan's base once
        # _share_plan has made one (the set base, else its proposal).
        self.target = base
        self._gh: GhLogin | None = None

    @property
    def gh(self) -> GhLogin:
        """The ``gh`` login, asked once and only when a row needs it."""
        if self._gh is None:
            from ..environments import gh_login
            self._gh = gh_login()
        return self._gh

    @property
    def on_ghcr(self) -> bool:
        """Whether the images go (or would go) to GHCR."""
        from ..environments import package_name
        return bool(self.target) and package_name(self.target or "", "x") is not None


def _share_remote_row(facts: _ShareFacts) -> CheckResult:
    """The git remote a collaborator clones from."""
    from ..version import git_remote_url

    remote = git_remote_url(facts.proj)
    if remote is None:
        return CheckResult(
            name="remote", status="warn",
            message="The project has no git remote: a collaborator has "
                    "nothing to clone.",
            fix_hint=f"Create a private GitHub repository and push to it: "
                     f"`gh repo create {facts.proj.name} --private --source . "
                     f"--push`.")
    where = (facts.repository.source_url if facts.repository is not None
             else remote)
    return CheckResult(name="remote", status="ok",
                       message=f"git remote: {where}.")


def _share_login_fix(facts: _ShareFacts, host: str) -> str:
    """The login line the user runs: through ``gh`` for GHCR, else ``docker login``."""
    from ..environments.registry import GHCR_HOST

    if host.lower() != GHCR_HOST:
        return f"Run `docker login {host}`, then `wfc share` again."
    user = facts.gh.user if facts.gh.ready else "<your GitHub user>"
    first = "" if facts.gh.ready else "`gh auth login --scopes write:packages`, then "
    return (f"Log Docker in to {host} with your GitHub login: {first}"
            f"`gh auth token | docker login {host} -u {user} --password-stdin`, "
            f"then `wfc share` again.")


def _share_login_row(facts: _ShareFacts, ref: str | None) -> CheckResult:
    """Whether Docker is logged in to the registry (one probe)."""
    from ..environments import probe_registry, registry_host

    if not facts.target:
        why = ("the git remote is not on GitHub" if facts.repository is None
               else "the env images could not be listed to propose one (the "
                    "images row says why)")
        return CheckResult(
            name="login", status="warn",
            message=f"No registry to ask yet: no base is set and {why}.",
            fix_hint="Set a registry base first (the base row), then run "
                     "`wfc share` again.")
    host = registry_host(facts.target)
    try:
        answered, reason = probe_registry(facts.target, ref=ref)
    except (RuntimeError, OSError) as exc:
        answered, reason = False, f"the registry {facts.target} could not be asked: {exc}"
    if answered:
        return CheckResult(name="login", status="ok",
                           message=f"Docker is logged in to {host}: the "
                                   f"registry {facts.target} answers.")
    return CheckResult(name="login", status="warn", message=reason,
                       fix_hint=_share_login_fix(facts, host))


def _share_base_row(facts: _ShareFacts) -> CheckResult:
    """Whether ``[registry] url`` is set."""
    if facts.base:
        return CheckResult(name="base", status="ok",
                           message=f"Registry base: {facts.base}.")
    message = ("No registry base is set ([registry] url): env images stay on "
               "this machine.")
    if facts.repository is not None:
        return CheckResult(
            name="base", status="warn", message=message,
            fix_hint=f"Run `wfc push-env --all`: it proposes "
                     f"{facts.repository.base} from the git remote, sets it "
                     f"and pushes the images.")
    return CheckResult(
        name="base", status="warn", message=message,
        fix_hint="Run `wfc push-env --all --registry <url>` (the git remote "
                 "is not on GitHub, so no base can be proposed).")


def _share_plan(facts: _ShareFacts):
    """Plan ``wfc push-env --all`` without applying it, or say why it cannot be planned.

    The plan's base (the one set, else the git remote's proposal) becomes
    ``facts.target``, so every row names the base push-env would use. With
    no base and no GitHub remote there is nothing to plan.
    """
    from ..environments import PushRefused, plan_push

    if not facts.base and facts.repository is None:
        return None, None
    try:
        plan = plan_push(facts.proj, None, recorded=_versions_by_env(facts.proj))
    except (PushRefused, ValueError) as exc:
        return None, str(exc)
    facts.target = plan.base
    return plan, None


def _share_images_row(facts: _ShareFacts, plan, error: str | None) -> CheckResult:
    """Whether every env image is in the registry (recorded pushes; no registry call)."""
    from ..environments import IMAGE_BYO_REGISTRY, IMAGE_PUSHED, unpushable_lines
    from ..environments.argv import strip_docker_scheme

    if error is not None:
        return CheckResult(name="images", status="warn",
                           message=f"Could not list the env images: {error}",
                           fix_hint="The message above names what could not be "
                                    "read; fix it, then run `wfc share` again.")
    if plan is None:
        return CheckResult(
            name="images", status="warn",
            message="No registry base is set, so no env image is pushed.",
            fix_hint="Run `wfc push-env --all --registry <url>` to set the "
                     "base and push them.")
    pushed = [item for item in plan.items if item.state == IMAGE_PUSHED]
    byo = [item for item in plan.items if item.state == IMAGE_BYO_REGISTRY]
    parts = []
    if pushed:
        parts.append(f"{len(pushed)} pushed to {plan.base}.")
    if byo:
        parts.append(f"{len(byo)} byo image(s) pullable from their own "
                     f"registry: " + ", ".join(
                         f"{item.label} ({strip_docker_scheme(item.image or '')})"
                         for item in byo) + ".")
    # A name no registry accepts: the image runs here, so no version breaks.
    misnamed = [item for item in plan.unreachable if item.name_problem]
    gone = [item for item in plan.unreachable if not item.name_problem]
    if gone:
        parts.append(f"{len(gone)} cannot be pushed from this machine: "
                     + ", ".join(item.label for item in gone)
                     + " (the versions row names what they break).")
    if misnamed:
        parts.append(f"{len(misnamed)} cannot be pushed under its env's name: "
                     + ", ".join(item.label for item in misnamed) + ".")
    if plan.to_push:
        labels = ", ".join(item.label for item in plan.to_push)
        where = plan.base if facts.base else f"a registry (none is set; {plan.base} proposed)"
        return CheckResult(
            name="images", status="warn",
            message=" ".join([f"{len(plan.to_push)} env image(s) on this "
                              f"machine are not in {where}: {labels}.", *parts]),
            fix_hint=" ".join(["Run `wfc push-env --all` to push them.",
                               *(line.removeprefix("ERROR: ")
                                 for line in unpushable_lines(plan, misnamed))]))
    if plan.unreachable:
        return CheckResult(
            name="images", status="warn", message=" ".join(parts),
            fix_hint=" ".join(line.removeprefix("ERROR: ")
                              for line in _unpushable(plan)))
    if not plan.items:
        return CheckResult(name="images", status="ok",
                           message="No env image is recorded.")
    return CheckResult(name="images", status="ok",
                       message="Every env image is in a registry: " + " ".join(parts))


def _unpushable(plan) -> list[str]:
    from ..environments import unpushable_lines
    return unpushable_lines(plan)


def _share_packages_row(facts: _ShareFacts, plan) -> CheckResult:
    """Whether every pushed image's GHCR package is connected to the repository (``gh api``)."""
    from ..environments import (
        CONNECT_REPOSITORY_STEPS,
        IMAGE_PUSHED,
        PACKAGE_CONNECTED,
        PACKAGE_NOT_FOUND,
        PACKAGE_OTHER_REPOSITORY,
        PACKAGE_UNCONNECTED,
        PACKAGE_UNKNOWN,
        PackageConnection,
        gh_scopes,
        package_connection,
        package_name,
        package_page_url,
    )

    if not facts.on_ghcr:
        where = (f"the registry {facts.target} is not GHCR" if facts.target
                 else "no registry base is set")
        return CheckResult(name="packages", status="ok",
                           message=f"No GitHub package to connect: {where}.")
    envs = sorted({item.env for item in (plan.items if plan else ())
                   if item.state == IMAGE_PUSHED})
    if not envs:
        return CheckResult(name="packages", status="ok",
                           message="No image is pushed yet, so no GitHub "
                                   "package to connect.")
    target = facts.target or ""
    names = [name for env in envs
             if (name := package_name(target, env)) is not None]
    expected = (f"{facts.repository.owner}/{facts.repository.repo}"
                if facts.repository is not None else None)
    repo_words = (f"the repository {facts.repository.source_url}"
                  if facts.repository is not None else "the project's repository")
    # The Connect Repository pop-up lists every repository the user has, by
    # owner/repo: name the one to choose.
    connect = (f"{CONNECT_REPOSITORY_STEPS} at the bottom of the page, and "
               f"choose {expected} in the list" if expected is not None
               else f"{CONNECT_REPOSITORY_STEPS} at the bottom of the page")
    if not facts.gh.ready:
        why = ("`gh` is not installed" if not facts.gh.installed
               else "`gh` is not logged in")
        links = ", ".join(package_page_url(owner, pkg) for owner, pkg in names)
        return CheckResult(
            name="packages", status="warn",
            message=f"wfc cannot tell whether the {len(names)} pushed "
                    f"package(s) are connected to {repo_words}: {why}.",
            fix_hint=f"Open each package's page and connect it to "
                     f"{repo_words} with {connect}: {links}. Or install `gh`, "
                     f"run `gh auth login`, and run `wfc share` again to check.")
    bad: list[tuple[PackageConnection, str]] = []
    for owner, pkg in names:
        conn = package_connection(owner, pkg, expected)
        if conn.state == PACKAGE_CONNECTED:
            continue
        if conn.state == PACKAGE_UNCONNECTED:
            why = "not connected to a repository"
        elif conn.state == PACKAGE_OTHER_REPOSITORY:
            why = f"connected to {conn.repository}, not {expected}"
        elif conn.state == PACKAGE_NOT_FOUND:
            why = "gh cannot see it (not pushed there, or no access)"
        else:
            why = f"gh did not answer ({conn.detail})"
        bad.append((conn, why))
    if not bad:
        return CheckResult(name="packages", status="ok",
                           message=f"{len(names)} pushed package(s), each "
                                   f"connected to {repo_words}.")
    unread = [conn for conn, _ in bad
              if conn.state in (PACKAGE_NOT_FOUND, PACKAGE_UNKNOWN)]
    if unread:
        # GitHub answers a login without a packages scope with 404 (or 403):
        # name that cause, not a package a collaborator cannot pull.
        scopes = gh_scopes()
        if scopes.lacks_packages:
            held = ", ".join(sorted(scopes.scopes or ()))
            return CheckResult(
                name="packages", status="warn",
                message=f"wfc cannot read the {len(unread)} pushed package(s) "
                        f"on GitHub: gh's login ({facts.gh.user}) has no "
                        f"packages scope (it holds {held}), so it cannot tell "
                        f"whether they are connected to {repo_words}: "
                        + ", ".join(f"{c.owner}/{c.package}" for c in unread) + ".",
                fix_hint="Add the packages scope to gh's login: `gh auth "
                         "refresh --hostname github.com --scopes "
                         "write:packages` (or `--scopes read:packages` if you "
                         "only pull), then `wfc share` again.")
    return CheckResult(
        name="packages", status="warn",
        message=f"{len(bad)} pushed package(s) not connected to {repo_words}: "
                + "; ".join(f"{conn.owner}/{conn.package}: {why}" for conn, why in bad)
                + ". A collaborator cannot pull them.",
        fix_hint=f"Connect each to {repo_words} on its package page, with "
                 f"{connect}: "
                 + ", ".join(conn.page_url for conn, _ in bad) + ".")


def share_checks(project_dir: Path | str | None = None) -> list[CheckResult]:
    """Run ``wfc share``'s checklist: what a collaborator will be missing.

    Rows, in order: ``remote`` (a git remote to clone), ``login`` (Docker
    logged in to the registry: one probe), ``base`` (``[registry] url``
    set), ``images`` (every recorded image pushed, from the captures folders
    and the daemon only; byo registry images listed as pullable from their
    own registry), ``packages`` (each pushed GHCR package connected to the
    repository, through ``gh api``; without ``gh`` the row says it cannot
    tell and prints each package's page link; when ``gh`` cannot read a
    package and its login holds no packages scope, the row names the
    ``gh auth refresh`` line that adds it) and ``versions``
    (:func:`check_runnable_versions`). Every non-ok row is a ``warn`` naming
    its fix. Nothing is written, committed or pushed.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        The rows; a single ``share`` warn outside a wfc project.
    """
    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()
    from ..environments import registry_base

    try:
        base = registry_base(proj)
    except FileNotFoundError:
        return [_no_project("share")]
    except ValueError as exc:
        return [CheckResult(name="share", status="warn",
                            message=f"Could not read the project's config: {exc}",
                            fix_hint="Fix .wfc/wf-canvas.toml, then run `wfc share` again.")]
    facts = _ShareFacts(proj, base)
    plan, error = _share_plan(facts)
    ref = None
    if plan is not None and facts.base:
        from ..environments import IMAGE_PUSHED, CaptureError, pullable_ref
        try:
            ref = next((pullable_ref(item.env, item.fingerprint, item.image or "", proj)
                        for item in plan.items if item.state == IMAGE_PUSHED), None)
        except (ValueError, CaptureError):
            ref = None
    return [
        _share_remote_row(facts),
        _share_login_row(facts, ref),
        _share_base_row(facts),
        _share_images_row(facts, plan, error),
        _share_packages_row(facts, plan),
        check_runnable_versions(proj),
    ]


# =============================================================================
# views
# =============================================================================

def check_views(project_dir: Path | str | None = None) -> CheckResult:
    """Report whether views of cache entries are links or fall back to copies.

    Storage's probe tries each view tier (reflink, hardlink, copy) with a
    scratch file from the cache into the sample workspace and the
    checkouts directory, and removes it again. A copy is a ``warn``, never
    a ``fail``: runs work, but each local sample or result is kept twice.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        A :class:`CheckResult` named ``"views"``.
    """
    from ..storage import probe_view_tiers

    proj = Path(project_dir).resolve() if project_dir is not None else default_project_dir()
    try:
        probes = probe_view_tiers(proj)
    except OSError as exc:
        return CheckResult(
            name="views",
            status="warn",
            message=f"Could not probe how views of cache entries are made: {exc}",
            fix_hint="Check that the project directory is writable.",
        )
    copies = [p for p in probes if p.tier == "copy"]
    if not copies:
        tiers = ", ".join(f"{p.target}: {p.tier}" for p in probes)
        return CheckResult(name="views", status="ok",
                           message=f"Restored samples and checkouts are links to the "
                                   f"cache ({tiers}).")
    where = "; ".join(f"{p.target} ({p.directory}): {p.reason}" for p in copies)
    return CheckResult(
        name="views",
        status="warn",
        message=(f"Views fall back to copies, so each local item takes twice its "
                 f"size: the cache cannot be linked into {where}."),
        fix_hint="Keep .wfc/ and .dvc/cache/ on one filesystem that supports "
                 "hard links or reflinks.",
    )


def run_all_checks(project_dir: Path | str | None = None) -> list[CheckResult]:
    """Run the git, DVC, Docker, sample and view checks and return them in order.

    Args:
        project_dir: Root directory of the wfc project.  Defaults to the
            resolved project root (see :func:`default_project_dir`).

    Returns:
        ``[check_git(...), check_dvc(...), check_docker(), check_samples(...),
        check_views(...)]``.
    """
    return [
        check_git(project_dir),
        check_dvc(project_dir),
        check_docker(),
        check_samples(project_dir),
        check_views(project_dir),
    ]
