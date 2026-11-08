"""Orphaned env builds: the build clients still running with no wfc behind them.

An orphaned env build is found from processes, never from Docker's build
history: a live ``docker`` or ``docker-buildx`` client whose command line
builds ``local/<name>:_wfc-build`` from a context under this project's
``.wfc/build/`` and that has no live wfc process among its ancestors
(:func:`orphaned_builds`, over the process table ``_process_table`` reads).
A plugin whose ``docker`` parent is gone counts.

:func:`stop_orphaned_build` ends one. A build whose client processes carry
the mark :func:`wfc.environments.docker.build` gives every client it starts
in a process group of its own (:data:`wfc.environments.docker.BUILD_CLIENT_MARK`,
read from each client's environment) has that group interrupted
(``_interrupt_client``), a bounded wait (:data:`ORPHAN_INTERRUPT_WAIT_S`), and
only then its whole process tree terminated, plugin before client. Any other
build, or one whose environment could not be read, is sent no interrupt: an
interrupt to a pid that leads no group reaches every process on the console
on Windows, and a user's shell group on POSIX. Its process tree is
terminated straight away, plugin before client. Nothing ever kills
``docker.exe`` alone.

This module owns the process side of the leftovers search: the process
table, what counts as a wfc process and a build client, the parent and
ancestor walk, and the interrupt, wait and terminate edges
(:class:`ProcessInfo`, :class:`OrphanedBuild`, :class:`BuildStop`).
:mod:`wfc.environments.leftovers` calls it; it never imports that module.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from axiom_annotations import task

from .. import layout

#: How long an orphaned build's client may take to end on its interrupt
#: before its process tree is terminated. A client that obeys ends in a
#: second or two (measured: 1.5 to 1.9 s).
ORPHAN_INTERRUPT_WAIT_S: Final[float] = 10.0

#: Seconds the interrupt helper process may take to attach and signal.
_HELPER_TIMEOUT_S: Final[float] = 15.0

#: Two reads of one process's creation time may differ by this much; a
#: "parent" created later than this after its child is another process that
#: reused the pid.
_CREATED_TOLERANCE_S: Final[float] = 1.0

#: The tag an env build carries until its image is resolved by digest
#: (``wfc.environments.build``): ``local/<env>:_wfc-build``.
_BUILD_TAG: Final = re.compile(r"^local/(?P<env>[A-Za-z0-9][A-Za-z0-9_.-]*):_wfc-build$")

#: The Docker client and its build plugin, by program name (no ``.exe``).
_DOCKER_CLIENT: Final[str] = "docker"
_BUILDX_PLUGIN: Final[str] = "docker-buildx"

#: The Windows interrupt helper: a short-lived process (never wfc itself)
#: that gives up its own console, attaches to the live client's, ignores
#: the event itself and sends ``CTRL_BREAK_EVENT`` to the client's group.
#: Exit 0 sent; 2 the attach failed; 3 the event could not be sent.
_CTRL_BREAK_HELPER: Final[str] = (
    "import ctypes, sys\n"
    "k32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "group, attach = int(sys.argv[1]), int(sys.argv[2])\n"
    "k32.FreeConsole()\n"
    "if not k32.AttachConsole(attach):\n"
    "    sys.exit(2)\n"
    "k32.SetConsoleCtrlHandler(None, True)\n"
    "sent = k32.GenerateConsoleCtrlEvent(1, group)\n"
    "k32.FreeConsole()\n"
    "sys.exit(0 if sent else 3)\n"
)


@dataclass(frozen=True)
class ProcessInfo:
    """One process as the process table shows it.

    Attributes:
        pid: Its process id.
        ppid: Its parent's process id (the parent may be gone).
        name: Its program name (``docker.exe``).
        cmdline: Its command line; empty when it could not be read.
        created: Its creation time (seconds since the epoch).
        cwd: Its working directory, read only for a Docker client; ``None``
            when not read.
        marked: True when its environment carries the mark
            :func:`wfc.environments.docker.build` gives every client it
            starts in a process group of its own; read only for a Docker
            client. False when it is absent or could not be read.
        pgid: Its process group id, read only for a Docker client on POSIX;
            ``None`` on Windows (which has no process group ids to read) or
            when it could not be read.
    """

    pid: int
    ppid: int
    name: str
    cmdline: tuple[str, ...]
    created: float
    cwd: str | None = None
    marked: bool = False
    pgid: int | None = None


@dataclass(frozen=True)
class OrphanedBuild:
    """An env build whose client is still running with no wfc process behind it.

    Attributes:
        env: The env being built (from the ``local/<env>:_wfc-build`` tag).
        context: The build context, under this project's ``.wfc/build/``.
        group_id: The client's process group: the ``docker`` client's pid,
            which is also its plugin's parent pid and stays the group's id
            after that process is gone. On POSIX, where an orphaned plugin's
            parent becomes init, it is the group id the process table read.
        processes: The build's live client processes, the plugin first,
            then the ``docker`` client when it is still running.
        started: When its oldest client process started.
        marked: True when every one of its client processes carries wfc's
            build-client mark, so *group_id* names a process group wfc
            started: only then is the group sent an interrupt.
    """

    env: str
    context: str
    group_id: int
    processes: tuple[ProcessInfo, ...]
    started: float
    marked: bool = False

    def describe(self, now: float) -> str:
        """The build in one line: its env, how long it has run, its context.

        A build wfc did not start in a group of its own says it will be
        terminated rather than interrupted.
        """
        from .docker import _format_elapsed

        how = ("" if self.marked else
               "; not started by this wfc in a group of its own, so it will be terminated")
        return (f"{self.env} (running for {_format_elapsed(max(0.0, now - self.started))}, "
                f"building {self.context}{how})")

    def holds(self, other: OrphanedBuild) -> bool:
        """Say whether *other* is this build seen again (a process in common)."""
        return any(_same_process(mine, theirs)
                   for mine in self.processes for theirs in other.processes)


@dataclass(frozen=True)
class BuildStop:
    """What stopping one orphaned build did.

    Attributes:
        build: The build.
        stopped: True when none of its client processes is left.
        how: ``interrupted`` (it ended on the interrupt), ``terminated``
            (its process tree was ended: after the wait, or at once for a
            build wfc did not mark), or ``not stopped``.
    """

    build: OrphanedBuild
    stopped: bool
    how: str


# --- the process side: which build clients are orphans ----------------------


def _program(name: str) -> str:
    """A program's name without its directory and a Windows ``.exe``, lowercased."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.removesuffix(".exe")


def _is_wfc_process(info: ProcessInfo) -> bool:
    """Say whether a process is a wfc command (``wfc``, ``python -m wfc``, ...).

    Read from its name and the first words of its command line. A false
    "yes" only keeps a build off the leftovers list.
    """
    if _program(info.name) == "wfc":
        return True
    args = info.cmdline
    for index, arg in enumerate(args[:4]):
        if _program(arg).removesuffix("-script.py").removesuffix(".py") == "wfc":
            return True
        if arg == "-m" and index + 1 < len(args):
            module = args[index + 1]
            if module == "wfc" or module.startswith("wfc."):
                return True
    return False


def _build_target(info: ProcessInfo) -> tuple[str, str] | None:
    """Return ``(env, context)`` when a process is a client building an env's tag.

    A ``docker build`` / ``docker buildx build`` client, or its
    ``docker-buildx buildx build`` plugin, tagging ``local/<env>:_wfc-build``.
    The context is the command line's last word, as wfc passes it.

    Args:
        info: The process.

    Returns:
        The env and the context as written, or ``None``.
    """
    if _program(info.name) not in (_DOCKER_CLIENT, _BUILDX_PLUGIN):
        return None
    args = list(info.cmdline[1:])
    if args[:1] == ["build"]:
        rest = args[1:]
    elif args[:2] == ["buildx", "build"]:
        rest = args[2:]
    else:
        return None
    env = None
    for index, arg in enumerate(rest):
        value = None
        if arg in ("-t", "--tag") and index + 1 < len(rest):
            value = rest[index + 1]
        elif arg.startswith("--tag="):
            value = arg.split("=", 1)[1]
        elif arg.startswith("-t") and len(arg) > 2 and not arg.startswith("--"):
            value = arg[2:].removeprefix("=")
        match = _BUILD_TAG.match(value or "")
        if match is not None:
            env = match.group("env")
    if env is None or not rest:
        return None
    return env, rest[-1]


def _resolved_under(path: str, cwd: str | None, root: Path) -> str | None:
    """Return *path* resolved when it lies under *root*; else ``None``.

    A relative path is read against the process's working directory; one
    whose directory is not known is not under anything.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        if not cwd:
            return None
        candidate = Path(cwd) / candidate
    try:
        resolved = candidate.resolve()
        base = os.path.normcase(str(Path(root).resolve()))
    except (OSError, RuntimeError):
        return None
    folded = os.path.normcase(str(resolved))
    return str(resolved) if folded.startswith(base.rstrip("\\/") + os.sep) else None


def _same_process(a: ProcessInfo, b: ProcessInfo) -> bool:
    """Say whether two reads are one process (same pid, same creation time)."""
    return a.pid == b.pid and abs(a.created - b.created) <= _CREATED_TOLERANCE_S


def _parent(info: ProcessInfo, table: Mapping[int, ProcessInfo]) -> ProcessInfo | None:
    """Return a process's live parent, or ``None`` when it is gone.

    A process holding the parent's pid but created after the child is
    another process that reused the pid: the parent is gone.
    """
    parent = table.get(info.ppid)
    if parent is None or parent.pid == info.pid or info.ppid <= 0:
        return None
    if parent.created > info.created + _CREATED_TOLERANCE_S:
        return None
    return parent


def _has_live_wfc_ancestor(info: ProcessInfo, table: Mapping[int, ProcessInfo]) -> bool:
    """Say whether a live wfc process is among a process's ancestors."""
    seen = {info.pid}
    child = info
    while True:
        parent = _parent(child, table)
        if parent is None or parent.pid in seen:
            return False
        if _is_wfc_process(parent):
            return True
        seen.add(parent.pid)
        child = parent


def orphaned_builds(table: Mapping[int, ProcessInfo],
                    project_dir: str | Path) -> list[OrphanedBuild]:
    """Find this project's env builds whose client runs with no wfc behind it.

    A build client is a live ``docker`` / ``docker-buildx`` process building
    ``local/<env>:_wfc-build`` from a context under this project's
    ``.wfc/build/``. A plugin whose ``docker`` client is alive is one build
    with it; a plugin whose client is gone is a build of its own, its group
    still named by its parent's pid (on POSIX, by the group id the table
    read, as the plugin's parent is then init). A build is an orphan when no live wfc
    process is among its client's ancestors. A build whose context is
    another project's, or anywhere else, is never one.

    Args:
        table: The process table (:func:`_process_table`).
        project_dir: The project root.

    Returns:
        The orphaned builds, oldest first.
    """
    build_root = layout.state_dir(Path(project_dir)) / layout.BUILD_DIR_NAME
    clients: dict[int, tuple[ProcessInfo, str, str]] = {}
    for info in table.values():
        target = _build_target(info)
        if target is None:
            continue
        context = _resolved_under(target[1], info.cwd, build_root)
        if context is not None:
            clients[info.pid] = (info, target[0], context)
    builds = []
    for info, env, context in clients.values():
        parent = _parent(info, table)
        if (_program(info.name) == _BUILDX_PLUGIN and parent is not None
                and parent.pid in clients):
            continue  # held by its live docker client: one build with it
        if _has_live_wfc_ancestor(info, table):
            continue
        plugins = tuple(child for child, _, _ in clients.values()
                        if _program(child.name) == _BUILDX_PLUGIN
                        and child.ppid == info.pid and _parent(child, table) is info)
        if info.pgid is not None:
            group_id = info.pgid
        else:
            group_id = info.pid if _program(info.name) == _DOCKER_CLIENT else info.ppid
        processes = (*plugins, info)
        builds.append(OrphanedBuild(env=env, context=context, group_id=group_id,
                                    processes=processes,
                                    started=min(p.created for p in processes),
                                    marked=all(p.marked for p in processes)))
    return sorted(builds, key=lambda b: (b.started, b.env))


def _client_marked(proc) -> bool:
    """Say whether a process's environment carries wfc's build-client mark.

    Args:
        proc: A psutil process.

    Returns:
        True when :data:`wfc.environments.docker.BUILD_CLIENT_MARK` is
        ``1`` in its environment; False when it is not, or the environment
        cannot be read (access denied, the process gone).
    """
    import psutil

    from .docker import BUILD_CLIENT_MARK

    try:
        return proc.environ().get(BUILD_CLIENT_MARK) == "1"
    except (psutil.Error, OSError):
        return False


def _process_group(pid: int) -> int | None:
    """A process's group id on POSIX; ``None`` on Windows or when it cannot be read."""
    from .docker import _on_windows

    if _on_windows():
        return None
    try:
        return os.getpgid(pid)  # type: ignore[attr-defined, unused-ignore]
    except OSError:
        return None


def _process_table() -> dict[int, ProcessInfo]:
    """Read every live process: the psutil edge of the leftovers search.

    The working directory, the build-client mark and (POSIX) the process
    group id are read only for a Docker client (a relative build context is
    read against the directory; the mark says the client leads a process
    group wfc started). A process
    that cannot be read is left out or shown without what could not be
    read; a mark that cannot be read is absent.

    Returns:
        ``{pid: ProcessInfo}``.
    """
    import psutil

    table: dict[int, ProcessInfo] = {}
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        info = proc.info
        if info.get("pid") is None or info.get("create_time") is None:
            continue
        name = info.get("name") or ""
        cwd = None
        marked = False
        pgid = None
        if _program(name) in (_DOCKER_CLIENT, _BUILDX_PLUGIN):
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = None
            marked = _client_marked(proc)
            pgid = _process_group(info["pid"])
        table[info["pid"]] = ProcessInfo(
            pid=info["pid"], ppid=info.get("ppid") or 0, name=name,
            cmdline=tuple(info.get("cmdline") or ()), created=info["create_time"],
            cwd=cwd, marked=marked, pgid=pgid)
    return table


def _live(procs: Sequence[ProcessInfo]) -> list:
    """The psutil processes of *procs* that are still those processes."""
    import psutil

    live = []
    for info in procs:
        try:
            proc = psutil.Process(info.pid)
            if abs(proc.create_time() - info.created) <= _CREATED_TOLERANCE_S:
                live.append(proc)
        except psutil.Error:
            continue
    return live


def _wait_gone(procs: Sequence[ProcessInfo], timeout: float) -> list[ProcessInfo]:
    """Wait up to *timeout* for processes to exit: the psutil edge.

    Args:
        procs: The processes to wait for.
        timeout: Seconds.

    Returns:
        Those still running when the wait ends.
    """
    import psutil

    live = _live(procs)
    try:
        _, alive = psutil.wait_procs(live, timeout=timeout)
    except psutil.Error:
        alive = live
    still = {p.pid for p in alive}
    return [info for info in procs if info.pid in still]


def _end_tree(procs: Sequence[ProcessInfo]) -> bool:
    """Terminate processes and their descendants, children first: the psutil edge.

    The descendants of each (deepest first), then each in the order given
    (the plugin before its ``docker`` client), through
    :func:`wfc.environments.docker.terminate_process_tree`.

    Args:
        procs: The client processes still running.

    Returns:
        True when every one is gone.
    """
    import psutil

    from .docker import terminate_process_tree

    ordered: list = []
    seen: set[int] = set()
    roots = _live(procs)
    for root in roots:
        try:
            children = root.children(recursive=True)
        except psutil.Error:
            children = []
        for proc in [*reversed(children), root]:
            if proc.pid not in seen:
                seen.add(proc.pid)
                ordered.append(proc)
    return terminate_process_tree(ordered) if ordered else True


def _interrupt_client(group_id: int, attach_pid: int) -> bool:
    """Send an interrupt to an orphaned build client's group: the signal edge.

    Windows: a short-lived helper process (never wfc itself) gives up its
    console, attaches to the live client's (*attach_pid*), ignores the event
    itself and sends ``CTRL_BREAK_EVENT`` to group *group_id*. POSIX (not
    measured): ``SIGINT`` to the client's process group, never to wfc's own.

    Args:
        group_id: The client's process group id (the ``docker`` client's pid).
        attach_pid: A live process of the client (the plugin, or the
            ``docker`` client while it lives).

    Returns:
        True when the interrupt was sent; False when it could not be (the
        attach failed, the helper could not run, the group is wfc's own).
    """
    from .docker import _CREATE_NEW_PROCESS_GROUP, _on_windows, interrupt_process_group

    if not _on_windows():
        try:
            group = os.getpgid(attach_pid)  # type: ignore[attr-defined, unused-ignore]
        except OSError:
            return False
        if group == os.getpgrp():  # type: ignore[attr-defined, unused-ignore]
            return False
        return interrupt_process_group(group)
    try:
        helper = subprocess.run(
            [sys.executable, "-I", "-c", _CTRL_BREAK_HELPER, str(group_id), str(attach_pid)],
            capture_output=True, timeout=_HELPER_TIMEOUT_S, check=False,
            creationflags=_CREATE_NEW_PROCESS_GROUP)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return helper.returncode == 0


@task(purpose="End one orphaned env build. A build whose clients carry wfc's "
              "mark (started by docker.build in a process group of its own): "
              "interrupt that group, wait a bounded time for the client to "
              "exit, and only if it is still running (or the interrupt could "
              "not be sent) terminate its whole process tree. Any other "
              "build: no interrupt, its process tree is terminated at once. "
              "Either tree end takes the plugin before the client",
      inputs="the OrphanedBuild, as seen at the apply's re-check",
      outputs="BuildStop: stopped or not, and how",
      critical="an interrupt goes only to a group wfc marked as its own: one "
               "sent to a pid that leads no group reaches every process on "
               "the console (Windows) or a user's shell group (POSIX). Never "
               "kills docker.exe alone; touches only the build's own processes")
def stop_orphaned_build(build: OrphanedBuild) -> BuildStop:
    """Stop one orphaned build: interrupt a marked client, terminate any other.

    Args:
        build: The build, with its live client processes.

    Returns:
        Whether none of its client processes is left, and how it ended.
    """
    if not build.marked:
        ended = _end_tree(list(build.processes))
        return BuildStop(build, stopped=ended, how="terminated" if ended else "not stopped")
    attach = build.processes[0]
    sent = _interrupt_client(build.group_id, attach.pid)
    left = (_wait_gone(build.processes, ORPHAN_INTERRUPT_WAIT_S) if sent
            else list(build.processes))
    if not left:
        return BuildStop(build, stopped=True, how="interrupted")
    ended = _end_tree(left)
    return BuildStop(build, stopped=ended, how="terminated" if ended else "not stopped")
