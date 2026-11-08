"""The docker build client as a process tree: spawn, interrupt, terminate.

``wfc.environments.docker.build`` starts ``docker build`` in its own process
group and, on Ctrl-C, interrupts that group, waits a bounded time, and only
then terminates whatever of the client's tree is left (on Windows
``docker.exe`` and its ``docker-buildx.exe`` plugin child, the real BuildKit
client). This fake stands in for all of it: the ``Popen`` the build spawns,
the group interrupt (``os.kill`` with ``CTRL_BREAK_EVENT`` on Windows,
``os.killpg`` with ``SIGINT`` elsewhere), and the process table ``psutil``
reads and signals. One ordered event list records every interrupt, wait,
terminate and kill, so a test can say what happened in which order.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from ._entry import fake

#: The fake client's pid, its group id, and its plugin child's pid.
CLIENT_PID = 4100
PLUGIN_PID = 4101


@dataclass
class _FakeProc:
    """One process of the fake client tree, as ``psutil.Process`` shows it."""

    pid: int
    _name: str
    events: list
    alive: bool = True
    children_: list = field(default_factory=list)

    def name(self) -> str:
        return self._name

    def children(self, recursive: bool = False) -> list:
        return [c for c in self.children_ if c.alive]

    def is_running(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.events.append(("terminate", self._name))
        self.alive = False

    def kill(self) -> None:
        self.events.append(("kill", self._name))
        self.alive = False


@dataclass
class BuildScript:
    """What the fake build did, and what it was asked to do.

    Attributes:
        events: Ordered ``(what, detail)`` tuples: ``("interrupt", (gid,
            signal))``, ``("wait", timeout)``, ``("terminate", name)``,
            ``("kill", name)``.
        cmd: The argv the build spawned.
        spawn_kwargs: Every keyword the ``Popen`` call passed.
        client: The fake ``docker.exe``.
        plugin: The fake ``docker-buildx.exe``.
    """

    events: list
    cmd: list | None = None
    spawn_kwargs: dict = field(default_factory=dict)
    client: _FakeProc | None = None
    plugin: _FakeProc | None = None


class _FakeBuildPopen:
    """The ``docker build`` client: canned output, then the scripted ending."""

    def __init__(self, script: BuildScript, output: str, returncode: int, *,
                 interrupt: bool, obeys: bool, second_interrupt: bool):
        self._script = script
        self._returncode = returncode
        self._interrupt = interrupt
        self._obeys = obeys
        self._second_interrupt = second_interrupt
        self.pid = CLIENT_PID
        self.returncode = None if interrupt else returncode
        self.stdout = iter(output.splitlines(keepends=True))

    def poll(self):
        if self._interrupt:
            self._interrupt = False
            raise KeyboardInterrupt
        if not self._script.client.alive and self.returncode is None:
            self.returncode = 130 if self._obeys else -15
        return self.returncode

    def wait(self, timeout=None):
        self._script.events.append(("wait", timeout))
        if self._second_interrupt:
            self._second_interrupt = False
            raise KeyboardInterrupt
        if self._script.client.alive:
            raise subprocess.TimeoutExpired("docker build", timeout)
        return self.poll()

    def deliver_interrupt(self) -> None:
        """The client and its plugin end on the interrupt, when they obey it."""
        if self._obeys:
            self._script.plugin.alive = False
            self._script.client.alive = False


@fake(
    boundary="the docker build client process tree inside "
             "wfc.environments.docker.build -- subprocess.Popen, the group "
             "interrupt (os.kill CTRL_BREAK_EVENT on Windows, os.killpg SIGINT "
             "elsewhere), psutil's view of docker.exe and its docker-buildx.exe "
             "plugin child, and wfc.environments.docker._on_windows",
    preserves="the argv, env and spawn keywords the build passes (all "
              "recorded), the streamed output, the exit status, and one "
              "ordered list of every interrupt, wait, terminate and kill the "
              "build sends; a client that obeys the interrupt ends with its "
              "plugin, one that ignores it lives until terminated",
    not_proven="that a real client ends on CTRL_BREAK or SIGINT, or that "
               "BuildKit cancels the build when it does (the Docker-marked "
               "witness drives a real sleeping build for that)",
    backed_by="pm_mvp::tests.integration.test_docker_build_interrupt_docker::"
              "test_ctrl_c_ends_a_real_build_within_seconds",
)
def fake_interruptible_build(monkeypatch, *, output: str = "",
                             returncode: int = 0, interrupt: bool = False,
                             obeys: bool = True, second_interrupt: bool = False,
                             windows: bool = True,
                             console: bool = True) -> BuildScript:
    """Replace the docker build client with a scripted process tree.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        output: The build's streamed output.
        returncode: The exit status when no interrupt is scripted.
        interrupt: Raise ``KeyboardInterrupt`` in the build's first poll, as
            a Ctrl-C arriving while the build runs.
        obeys: Whether the client and its plugin end on the interrupt.
        second_interrupt: Raise ``KeyboardInterrupt`` again in the bounded
            wait, as a second Ctrl-C.
        windows: Whether the build sees a Windows host.
        console: Whether the group interrupt can be sent; ``False`` makes
            it fail as ``GenerateConsoleCtrlEvent`` does with no console.

    Returns:
        The :class:`BuildScript` the build fills in.
    """
    import psutil

    from wfc.environments import docker

    script = BuildScript(events=[])
    plugin = _FakeProc(PLUGIN_PID, "docker-buildx.exe", script.events)
    client = _FakeProc(CLIENT_PID, "docker.exe", script.events,
                       children_=[plugin])
    script.client, script.plugin = client, plugin
    procs = {CLIENT_PID: client, PLUGIN_PID: plugin}
    holder: dict = {}

    def spawn(cmd, **kwargs):
        script.cmd = cmd
        script.spawn_kwargs = kwargs
        holder["popen"] = _FakeBuildPopen(
            script, output, returncode, interrupt=interrupt, obeys=obeys,
            second_interrupt=second_interrupt)
        return holder["popen"]

    def interrupt_group(gid, sig):
        script.events.append(("interrupt", (gid, sig)))
        if not console:
            raise OSError("no console to send the interrupt through")
        if gid != CLIENT_PID:
            raise ProcessLookupError(gid)
        holder["popen"].deliver_interrupt()

    def process(pid):
        proc = procs.get(pid)
        if proc is None or not proc.alive:
            raise psutil.NoSuchProcess(pid)
        return proc

    def wait_procs(plist, timeout=None):
        gone = [p for p in plist if not p.alive]
        return gone, [p for p in plist if p.alive]

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(docker, "_on_windows", lambda: windows)
    if windows:
        monkeypatch.setattr(os, "kill", interrupt_group)
    else:
        monkeypatch.setattr(os, "killpg", interrupt_group, raising=False)
    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    return script
