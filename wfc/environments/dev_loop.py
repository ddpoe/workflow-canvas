"""Interactive dev-loop commands for container envs.

Three convenience verbs (``wfc jupyter``, ``wfc shell``, ``wfc exec``) launch
an ephemeral container of the env's digest-pinned image with the same
bind-mount layout ``wfc run-step`` uses. This gives method developers
production-parity at dev time without forcing them to hand-type the full
``docker run`` invocation.

Every entry point is one ``docker run`` invocation that exits with the
container's exit code. No session bookkeeping, no pip-freeze diff, no
wrapper script. The ephemeral discipline -- "the container is spawned
fresh per invocation; in-session changes (including pip install) do not
persist into pipeline runs" -- is communicated through CLI ``--help``
text, not enforced at runtime.

Engine selection: project-level ``[executor] type`` in
``.wfc/wf-canvas.toml`` (default ``local``). ``slurm`` triggers a clean
"cluster Apptainer dispatch (executor=slurm) is not supported" error: the
dev loop does not run under cluster Apptainer.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from .. import layout

# Port that uniFLOW (a Konica Minolta print server) squats on Dante's local
# Windows machine -- it 302-redirects to :8443 and breaks anything that
# auto-picks 8000 as a free port. Hard-skip even if the bind probe says it
# is free, so a different process briefly holding 8000 during ``wfc jupyter``
# can never cause the redirect to land in a notebook URL.
_PORT_HARD_SKIP = {8000}


class _DevLoopError(Exception):
    """Internal error carrying an exit code and a stderr-formatted message.

    Each public entry point catches this and prints the message to stderr,
    then returns ``exit_code``. The CLI dispatch layer surfaces that as
    the process exit code.
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


# ============================================================================
# Internal: env / runtime resolution
# ============================================================================

def _read_project_executor(project_dir: Path) -> str:
    """Read the project-level executor from ``.wfc/wf-canvas.toml``.

    Dev-loop commands have no method context, so executor is read at the
    project level rather than from ``method.yaml``. Only ``"local"`` and
    ``"slurm"`` are meaningful, and ``"slurm"`` triggers the carve-out
    error. Anything else is treated as ``"local"``.

    Returns:
        The executor type string. Defaults to ``"local"`` when the
        config file is missing, the ``[executor]`` section is missing,
        or ``type`` is unset.
    """
    config_path = layout.marker_path(project_dir)
    if not config_path.exists():
        return "local"
    try:
        with open(config_path, "rb") as f:
            parsed = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return "local"
    executor_section = parsed.get("executor", {}) or {}
    return executor_section.get("type", "local") or "local"


def _resolve_env_and_runtime(env_name: str) -> tuple[str, Path, Path]:
    """Resolve the container image ref, project root, and DVC cache dir.

    Centralises the boilerplate every dev-loop entry point shares:
    project-root walk, executor guard, env-manifest lookup, container-ref
    validation, DVC cache resolution.

    Args:
        env_name: Name of the env (key in ``.wfc/envs.json::envs``).

    Returns:
        ``(image_ref, project_root, dvc_cache_dir)`` -- image_ref is the
        daemon ref :func:`~wfc.environments.runnable.ensure_runnable`
        returns, the form ``build_docker_command`` hands Docker; a missing
        local image has been rebuilt first.

    Raises:
        _DevLoopError: When the project root is missing, the executor is
            ``slurm``, the env is not registered, the env is not a
            container env (empty ``container`` field), or its missing
            image is refused or fails to rebuild.
    """
    # The canonical resolver (imported here so dev-loop stays light at
    # import time): WFC_PROJECT_ROOT validated against the marker, else the
    # upward walk, else its error — converted to this module's exit shape.
    from ..persistence import project_root as _resolve_project_root
    try:
        project_root = _resolve_project_root()
    except RuntimeError as exc:
        raise _DevLoopError(f"ERROR: No wfc project found: {exc}") from exc

    executor = _read_project_executor(project_root)
    if executor == "slurm":
        raise _DevLoopError(
            "ERROR: cluster Apptainer dispatch (executor=slurm) is not "
            "supported, so dev-loop commands (wfc jupyter/shell/exec) "
            "cannot run under it."
        )

    # Lazy import keeps the dev loop cheap to import.
    from . import manifest as envs_mod

    try:
        record = envs_mod.get(env_name, project_root)
    except ValueError as exc:
        raise _DevLoopError(f"ERROR: {exc}") from exc

    if record is None:
        raise _DevLoopError(
            f"ERROR: env {env_name!r} not found in .wfc/envs.json. "
            f"Run `wfc list-envs` to see registered envs."
        )

    container_ref = getattr(record, "container", "") or ""
    if not container_ref:
        raise _DevLoopError(
            f"ERROR: env {env_name!r} has no container image registered. "
            f"Dev-loop commands require a container env."
        )

    # What Docker is handed: the record's daemon ref, once the daemon is
    # known to hold the image (a missing local env is rebuilt from its
    # staged build context, or refused naming the command that recreates it).
    from .runnable import ensure_runnable
    try:
        container_ref = ensure_runnable(env_name, record, project_root)
    except RuntimeError as exc:  # a refusal, or a failed rebuild
        raise _DevLoopError(f"ERROR: {exc}") from exc

    dvc_cache_dir = layout.dvc_cache_dir(project_root)
    return container_ref, project_root, dvc_cache_dir


# ============================================================================
# Port autopick (jupyter only)
# ============================================================================

def _autopick_port(start_port: int = 8888, max_port: int = 8999) -> int:
    """Return the first free TCP port in ``[start_port, max_port]``.

    Walks upward through the range, attempting a ``bind`` on
    ``127.0.0.1:<port>``. Returns the first port that binds cleanly.
    **Hard-skips port 8000 unconditionally** even when ``bind`` reports
    it as free -- on Dante's machine uniFLOW (momsmartclnt.exe) owns 8000
    and 302-redirects to :8443, which would corrupt any Jupyter URL the
    user opened.

    Args:
        start_port: Inclusive lower bound (default 8888).
        max_port: Inclusive upper bound (default 8999).

    Returns:
        A port number in ``[start_port, max_port]`` that is currently
        free and not on the hard-skip list.

    Raises:
        _DevLoopError: When every port in the range is occupied (or on
            the skip list).
    """
    for port in range(start_port, max_port + 1):
        if port in _PORT_HARD_SKIP:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            sock.close()
        return port
    raise _DevLoopError(
        f"ERROR: no free port in range {start_port}-{max_port} "
        f"for Jupyter Lab (port 8000 is always skipped). "
        f"Pass --port to override."
    )


# ============================================================================
# Docker argv splicing
# ============================================================================

def _splice_run_flags(argv: list[str], extra_flags: Sequence[str]) -> list[str]:
    """Inject ``extra_flags`` into a ``docker run ...`` argv after ``--rm``.

    ``build_docker_command`` returns argv shaped::

        ["docker", "run", "--rm", "--user", "...", ...mounts..., <image>, ...inner]

    Dev-loop commands need to add per-subcommand flags (``-it``, ``-i``,
    ``-p host:container``) *between* ``--rm`` and the image ref so they
    apply to the container and not to the inner command. We splice right
    after ``--rm`` (index 3) to keep this position stable regardless of
    future mount additions.

    Args:
        argv: Output of ``build_docker_command``.
        extra_flags: Flags to inject (e.g. ``["-it"]`` or
            ``["-it", "-p", "9999:8888"]``).

    Returns:
        New argv list with ``extra_flags`` spliced in.
    """
    if len(argv) < 3 or argv[:3] != ["docker", "run", "--rm"]:
        # Defensive: if the helper's output shape ever changes, fail
        # loudly rather than silently misplacing the flags.
        raise _DevLoopError(
            "ERROR: unexpected docker argv shape from "
            "wfc.environments.argv.build_docker_command; "
            "expected ['docker', 'run', '--rm', ...]"
        )
    return argv[:3] + list(extra_flags) + argv[3:]


def _build_docker_argv(
    image_ref: str,
    project_root: Path,
    dvc_cache_dir: Path,
    inner_argv: Sequence[str],
    extra_flags: Sequence[str],
) -> list[str]:
    """Compose the full ``docker run ...`` argv for a dev-loop command.

    Reuses :func:`wfc.environments.argv.build_docker_command` for the base
    shape (so ``--user``, bind-mounts, and ``-w /work`` stay in sync with
    ``wfc run-step``), then splices ``extra_flags`` into the per-subcommand
    position. UID/GID use the same ``getattr(os, 'getuid', lambda: 0)()``
    pattern as :mod:`wfc.execution.dispatch` so Windows/macOS callers
    don't crash.
    """
    from .argv import build_docker_command

    uid = getattr(os, "getuid", lambda: 0)()
    gid = getattr(os, "getgid", lambda: 0)()
    base = build_docker_command(
        image_ref,
        project_root,
        dvc_cache_dir,
        list(inner_argv),
        uid=uid,
        gid=gid,
    )
    return _splice_run_flags(base, extra_flags)


# ============================================================================
# Public entry points
# ============================================================================

def shell(env_name: str) -> int:
    """Drop into an interactive shell inside an ephemeral container.

    Uses ``sh -c 'exec bash 2>/dev/null || exec sh'`` so slim images
    without bash still get a usable shell (the inner ``sh`` from the
    fallback). Container is bind-mounted the same way ``wfc run-step``
    does it.

    Args:
        env_name: Name of the env to launch.

    Returns:
        The container's exit code, or 1 on a resolution / docker error.
    """
    try:
        image_ref, project_root, dvc_cache_dir = _resolve_env_and_runtime(env_name)
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    inner = ["sh", "-c", "exec bash 2>/dev/null || exec sh"]
    try:
        argv = _build_docker_argv(
            image_ref, project_root, dvc_cache_dir, inner, ["-it"]
        )
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    return _run_subprocess(argv)


def exec_(env_name: str, cmd_argv: Sequence[str]) -> int:
    """Run an arbitrary command inside an ephemeral container.

    Uses ``-i`` (no ``-t``) so the command works correctly when
    redirected (e.g. ``wfc exec foo cat > out.txt``). The command is
    passed verbatim as the container's argv.

    Args:
        env_name: Name of the env to launch.
        cmd_argv: The command to run inside the container, as a sequence
            of arguments. Empty list is treated as a usage error.

    Returns:
        The container's exit code, or 1 on a resolution / docker error.
    """
    if not cmd_argv:
        print(
            "ERROR: wfc exec requires a command. "
            "Usage: wfc exec <env> <cmd> [args...]",
            file=sys.stderr,
        )
        return 1

    try:
        image_ref, project_root, dvc_cache_dir = _resolve_env_and_runtime(env_name)
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    try:
        argv = _build_docker_argv(
            image_ref, project_root, dvc_cache_dir, list(cmd_argv), ["-i"]
        )
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    return _run_subprocess(argv)


def jupyter(env_name: str, port: int | None = None) -> int:
    """Launch Jupyter Lab inside an ephemeral container of the env's image.

    The Jupyter server inside the container always binds 8888 (the inner
    port); the host port is either an explicit ``--port`` value or the
    autopick result (8888 if free, otherwise the next free port in the
    bounded range, with 8000 hard-skipped). The URL with the
    auto-generated token is printed by Jupyter itself on startup.

    Args:
        env_name: Name of the env to launch.
        port: Optional explicit host port. When ``None``, autopick walks
            8888-8999. Explicit overrides are NOT autopicked (the user
            asked for that port; we respect it).

    Returns:
        The container's exit code, or 1 on a resolution / docker error.
    """
    try:
        image_ref, project_root, dvc_cache_dir = _resolve_env_and_runtime(env_name)
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    try:
        resolved_port = port if port is not None else _autopick_port()
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    inner = [
        "jupyter", "lab",
        "--ip", "0.0.0.0",
        "--no-browser",
        "--allow-root",
        "--port", "8888",
    ]
    extra = ["-it", "-p", f"{resolved_port}:8888"]
    try:
        argv = _build_docker_argv(
            image_ref, project_root, dvc_cache_dir, inner, extra
        )
    except _DevLoopError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    print(
        f"Jupyter Lab launching on host port {resolved_port} "
        f"(container port 8888). Open the http://127.0.0.1:{resolved_port}/?token=... "
        f"URL printed below.",
        file=sys.stderr,
    )
    return _run_subprocess(argv)


# ============================================================================
# Subprocess shim (separate function for ease of test patching)
# ============================================================================

def _run_subprocess(argv: list[str]) -> int:
    """Invoke ``docker`` and return its exit code.

    Split out so tests can patch a single seam without touching the
    higher-level argv-construction logic.
    """
    try:
        result = subprocess.run(argv, check=False)
    except FileNotFoundError:
        print(
            "ERROR: docker not found on PATH. "
            "Install Docker Desktop (Windows/macOS) or docker-ce (Linux).",
            file=sys.stderr,
        )
        return 1
    return result.returncode
