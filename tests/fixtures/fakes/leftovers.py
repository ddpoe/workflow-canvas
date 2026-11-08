"""Leftovers boundaries: the Docker listings and the process table prune-envs reads.

``wfc.environments.leftovers`` finds what an earlier crash left through two
edges. The Docker one, ``wfc.environments.containers._docker`` (leftovers
calls it through that module), answers the running containers' listing and
inspect, the kill, the env images' listing and the build cache's size:
:func:`fake_leftover_daemon` stands a model daemon there (a
:class:`ContainerDaemon` that also holds each container's variables and
name, the images and the cache size). ``tests/conftest.py`` arms an empty one
in every test not marked ``integration``.

The process one, in ``wfc.environments.build_clients``, is the table psutil
reads and the three things the apply does to a build's client: interrupt its
group, wait for it, terminate its tree (``_process_table``,
``_interrupt_client``, ``_wait_gone``, ``_end_tree``).
:func:`fake_build_processes` stands a model process table there, with wfc
processes, ``docker.exe`` clients and their ``docker-buildx.exe`` plugins; one
ordered event list records every interrupt, wait and terminate. A client is
marked (its environment shows wfc's build-client mark) when it is started
through :meth:`BuildProcesses.build`, as wfc's ``docker.build`` starts one,
unless the test says otherwise. conftest arms
an empty one suite-wide too, so no unit test reads the real process table or
signals a real process. :func:`stub_posix_process_groups` is the POSIX half of
the interrupt edge: the process groups ``os.getpgid`` / ``os.killpg`` see.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ._entry import fake
from .containers import ContainerDaemon

#: A ``{{.Label "<name>"}}`` column of a ``ps --format`` template.
_LABEL_COLUMN = re.compile(r'\{\{\.Label "([^"]+)"\}\}')


class LeftoverDaemon(ContainerDaemon):
    """A model daemon for the leftovers search: containers, images, the cache.

    Attributes:
        variables: ``{container id: {name: value}}``, each container's variables.
        names: ``{container id: name}``.
        images: ``(repository, image id)`` rows of the local images.
        cache_size: The build cache's size as Docker prints it (``"1.5GB"``).
    """

    def __init__(self) -> None:
        super().__init__()
        self.variables: dict[str, dict[str, str]] = {}
        self.names: dict[str, str] = {}
        self.images: list[tuple[str, str]] = []
        self.cache_size = "0B"
        #: ``{container id: (labels, state)}`` of the containers not running
        #: (``created`` or ``exited``); a plain ``rm`` removes one.
        self.stopped: dict[str, tuple[dict[str, str], str]] = {}
        self.removed: list[str] = []

    def start(self, labels: dict[str, str] | None = None, *,
              variables: dict[str, str] | None = None, name: str = "") -> str:
        """Start a container with *labels* and *variables*; return its id."""
        cid = super().start(labels or {})
        self.variables[cid] = dict(variables or {})
        self.names[cid] = name or f"box-{len(self.names) + 1}"
        return cid

    def create(self, labels: dict[str, str] | None = None, *, name: str = "",
               state: str = "created") -> str:
        """Add a container that is not running (*state* ``created`` or ``exited``)."""
        cid = self.start(labels, name=name)
        self.stopped[cid] = (self.running.pop(cid), state)
        return cid

    def start_created(self, cid: str) -> None:
        """Start a container that was not running (it is running from now on)."""
        self.running[cid] = self.stopped.pop(cid)[0]

    def _row(self, template: str, cid: str) -> str:
        """One container's line of a ``ps --format`` listing: id, names, state, labels."""
        labels, state = self.stopped.get(cid) or (self.running.get(cid) or {}, "running")
        row = (template.replace("{{.ID}}", cid).replace("{{.Names}}", self.names.get(cid, ""))
               .replace("{{.State}}", state))
        return _LABEL_COLUMN.sub(lambda m: labels.get(m.group(1), ""), row)

    def _remove(self, args: list[str]) -> subprocess.CompletedProcess:
        """A plain ``rm``: removes a container not running; refuses a running one."""
        if "--force" in args or "-f" in args:
            raise AssertionError(f"the leftovers search forced a remove: {args}")
        for cid in args[1:]:
            if cid in self.running:
                return subprocess.CompletedProcess(
                    ["docker", *args], 1, "",
                    f'Error response from daemon: cannot remove container "/{cid}": '
                    f"container is running: stop the container before removing "
                    f"or force remove\n")
            if cid not in self.stopped:
                return subprocess.CompletedProcess(
                    ["docker", *args], 1, "",
                    f"Error response from daemon: No such container: {cid}\n")
            del self.stopped[cid]
            self.removed.append(cid)
        return subprocess.CompletedProcess(["docker", *args], 0, "", "")

    def answer(self, args: Sequence[str], *, what: str) -> subprocess.CompletedProcess:
        """Answer one Docker call: the listing and kill as the base, plus the rest.

        A ``ps`` with ``--format`` lists the same containers as the base, one
        line each, with the template's ``{{.ID}}``, ``{{.Names}}`` and
        ``{{.Label "<name>"}}`` columns filled in (a missing label is empty,
        as Docker prints it).
        """
        args = list(args)
        if args[0] == "ps" and "--format" in args:
            listed = super().answer(args, what=what)
            if listed.returncode != 0:
                return listed
            template = args[args.index("--format") + 1]
            ids = listed.stdout.split()
            if "--all" in args:
                ids += list(self.stopped)
            out = "".join(self._row(template, cid) + "\n" for cid in ids)
            return subprocess.CompletedProcess(["docker", *args], 0, out, "")
        if args[0] in ("ps", "kill"):
            return super().answer(args, what=what)
        self.calls.append(args)
        if self.on_call is not None:
            self.on_call(args)
        if isinstance(self.failure, BaseException):
            raise self.failure
        if isinstance(self.failure, str):
            return subprocess.CompletedProcess(["docker", *args], 1, "", self.failure)
        if args[0] == "rm":
            return self._remove(args)
        if args[0] == "inspect":
            found = [{"Id": cid, "Name": "/" + self.names.get(cid, ""),
                      "Config": {"Labels": self.running[cid] or None,
                                 "Env": [f"{k}={v}" for k, v in
                                         self.variables.get(cid, {}).items()]},
                      "State": {"Running": True}}
                     for cid in args[1:] if cid in self.running]
            return subprocess.CompletedProcess(["docker", *args], 0, json.dumps(found), "")
        if args[0] == "images":
            out = "".join(f"{repo}\t{image}\n" for repo, image in self.images
                          if repo.startswith("local/"))
            return subprocess.CompletedProcess(["docker", *args], 0, out, "")
        if args[:2] == ["system", "df"]:
            rows = [{"Type": "Images", "Size": "9.9GB"},
                    {"Type": "Build Cache", "Size": self.cache_size}]
            return subprocess.CompletedProcess(
                ["docker", *args], 0, "".join(json.dumps(r) + "\n" for r in rows), "")
        raise AssertionError(f"the leftovers search made an unexpected call: {args}")


@fake(
    boundary="wfc.environments.containers._docker -- the Docker calls of the "
             "leftovers search: the containers' listing (running, and with "
             "--all those not running) and inspect, the kill, the plain "
             "remove, the env images' listing and the build cache's size",
    preserves="every call made, recorded; a model of running containers with "
              "their labels, variables and names (a listing filters on every "
              "label and prints a --format template's id, names, state and "
              "label columns, a kill removes); containers created or exited, "
              "listed only with --all, which a plain rm removes and a running "
              "one refuses with Docker's message; the images' repositories and "
              "ids; the cache size as Docker prints it; the plan's and apply's "
              "own classification, re-check, wording and every caller's handling",
    not_proven="that Docker's ps --format label and state columns, inspect, "
               "images listing or system df print these shapes, that a kill "
               "ends a real container, or that a real plain remove refuses a "
               "running one with that message (the removal of a real created "
               "container: tests/integration/test_prune_envs_stopped_docker.py)",
    backed_by="pm_mvp::tests.integration.test_prune_envs_docker::"
              "test_apply_stops_an_ended_runs_container_and_leaves_a_running_one",
)
def fake_leftover_daemon(monkeypatch) -> LeftoverDaemon:
    """Replace the leftovers search's Docker edge with a model daemon.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The daemon: start containers, add images, set the cache size or a
        ``failure``, read its calls and kills.
    """
    from wfc.environments import containers

    daemon = LeftoverDaemon()
    monkeypatch.setattr(containers, "_docker", daemon.answer)
    return daemon


class BuildProcesses:
    """A model process table: wfc commands, docker clients and their plugins.

    Attributes:
        table: ``{pid: ProcessInfo}`` of every process ever started here.
        alive: The pids still running.
        groups: ``{pid: process group id}``.
        ignoring: Group ids whose processes ignore an interrupt.
        unattachable: Pids a console cannot be attached to.
        events: Ordered ``("interrupt", group, attach pid)``,
            ``("wait", timeout)`` and ``("terminate", (name, ...))`` tuples.
    """

    def __init__(self) -> None:
        self.table: dict = {}
        self.alive: set[int] = set()
        self.groups: dict[int, int] = {}
        self.ignoring: set[int] = set()
        self.unattachable: set[int] = set()
        self.events: list[tuple] = []
        self._next_pid = 5000
        self._clock = 1_700_000_000.0

    def spawn(self, name: str, cmdline: Sequence[str], *, parent=None,
              cwd: str | None = None, group: int | None = None,
              marked: bool = False):
        """Start a process; return its ``ProcessInfo``.

        Args:
            name: Its program name.
            cmdline: Its command line.
            parent: Its parent's ``ProcessInfo``, or ``None`` for one whose
                parent is not in the table.
            cwd: Its working directory.
            group: Its process group id; its own pid when not given.
            marked: Whether its environment shows wfc's build-client mark;
                False also stands for an environment that could not be read.
        """
        from wfc.environments.build_clients import ProcessInfo

        self._next_pid += 7
        self._clock += 5.0
        pid = self._next_pid
        info = ProcessInfo(pid=pid, ppid=parent.pid if parent is not None else 4,
                           name=name, cmdline=tuple(cmdline), created=self._clock,
                           cwd=cwd, marked=marked)
        self.table[pid] = info
        self.alive.add(pid)
        self.groups[pid] = group if group is not None else pid
        return info

    def wfc(self, *args: str):
        """Start a wfc command (``wfc.exe`` run by the venv's Python)."""
        return self.spawn("python.exe", ["C:\\venv\\Scripts\\python.exe",
                                         "C:\\venv\\Scripts\\wfc.exe", *args])

    def build(self, context: str | Path, env: str, *, parent=None, plugin: bool = True,
              marked: bool = True):
        """Start a ``docker build`` of *env* from *context*, as wfc starts one.

        Args:
            context: The build context.
            env: The env built (its tag is ``local/<env>:_wfc-build``).
            parent: The process that started the client.
            plugin: Whether the client has its ``docker-buildx.exe`` child.
            marked: Whether the client (and the plugin, which inherits its
                environment) carries wfc's mark, as every client wfc's
                ``docker.build`` starts does; False for one started otherwise.

        Returns:
            ``(docker client, plugin or None)``.
        """
        tag = f"local/{env}:_wfc-build"
        client = self.spawn("docker.exe", ["docker", "build", "--provenance=false",
                                           "-t", tag, str(context)], parent=parent,
                            marked=marked)
        child = None
        if plugin:
            child = self.spawn(
                "docker-buildx.exe",
                ["C:\\Program Files\\Docker\\cli-plugins\\docker-buildx.exe",
                 "buildx", "build", "--provenance=false", "-t", tag, str(context)],
                parent=client, group=client.pid, marked=marked)
        return client, child

    def kill(self, info) -> None:
        """End a process from outside wfc (a crash, a task manager)."""
        self.alive.discard(info.pid)

    def is_alive(self, info) -> bool:
        """Say whether a process is still running."""
        return info.pid in self.alive

    def _table(self) -> dict:
        return {pid: info for pid, info in self.table.items() if pid in self.alive}

    def _interrupt(self, group_id: int, attach_pid: int) -> bool:
        self.events.append(("interrupt", group_id, attach_pid))
        if attach_pid not in self.alive or attach_pid in self.unattachable:
            return False
        if group_id not in self.ignoring:
            for pid, group in self.groups.items():
                if group == group_id:
                    self.alive.discard(pid)
        return True

    def _wait_gone(self, procs, timeout: float) -> list:
        self.events.append(("wait", timeout))
        return [p for p in procs if p.pid in self.alive]

    def _end_tree(self, procs) -> bool:
        self.events.append(("terminate", tuple(self.table[p.pid].name for p in procs)))
        for proc in procs:
            self.alive.discard(proc.pid)
        return True


@fake(
    boundary="the process side of wfc.environments.build_clients -- "
             "_process_table (psutil's process listing), _interrupt_client "
             "(the Windows CTRL_BREAK helper / POSIX killpg), _wait_gone and "
             "_end_tree (psutil's wait and terminate)",
    preserves="a process table with pids, parents, names, command lines, "
              "creation times, working directories and whether each client's "
              "environment shows wfc's build-client mark; process groups; which "
              "groups obey an interrupt and which pids can be attached to; one "
              "ordered list of every interrupt, wait and terminate the apply "
              "sends; the orphan search, the re-check and the stop's sequencing "
              "stay real",
    not_proven="that psutil reads these fields from real processes, that the "
               "helper's CTRL_BREAK reaches a real client, or that BuildKit "
               "cancels the build when its client ends",
    backed_by="pm_mvp::tests.integration.test_prune_envs_docker::"
              "test_an_orphaned_build_whose_client_was_killed_is_ended_by_the_apply",
)
def fake_build_processes(monkeypatch) -> BuildProcesses:
    """Replace the leftovers search's process edge with a model process table.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.

    Returns:
        The model: start wfc commands and builds in it, kill processes, read
        its events.
    """
    from wfc.environments import build_clients

    model = BuildProcesses()
    monkeypatch.setattr(build_clients, "_process_table", model._table)
    monkeypatch.setattr(build_clients, "_interrupt_client", model._interrupt)
    monkeypatch.setattr(build_clients, "_wait_gone", model._wait_gone)
    monkeypatch.setattr(build_clients, "_end_tree", model._end_tree)
    return model


@fake(
    boundary="the POSIX half of wfc.environments.build_clients._interrupt_client "
             "-- wfc.environments.docker._on_windows, os.getpgid, os.getpgrp "
             "and os.killpg",
    preserves="each pid's process group and wfc's own; every killpg sent, "
              "recorded with its signal",
    not_proven="that a real docker client on Linux or macOS ends its build on "
               "SIGINT to its group (not measured)",
    backed_by="undrivable: the suite runs its Docker-marked tests on Windows "
              "only; POSIX is the unmeasured default (d-8)",
)
def stub_posix_process_groups(monkeypatch, groups: dict[int, int], *,
                              own_group: int) -> list[tuple[int, int]]:
    """Make the interrupt edge see a POSIX host with these process groups.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        groups: ``{pid: process group id}``.
        own_group: wfc's own process group id.

    Returns:
        Every ``(group, signal)`` ``os.killpg`` was sent, in order.
    """
    from wfc.environments import docker

    sent: list[tuple[int, int]] = []

    def getpgid(pid: int) -> int:
        if pid not in groups:
            raise ProcessLookupError(pid)
        return groups[pid]

    monkeypatch.setattr(docker, "_on_windows", lambda: False)
    monkeypatch.setattr(os, "getpgid", getpgid, raising=False)
    monkeypatch.setattr(os, "getpgrp", lambda: own_group, raising=False)
    monkeypatch.setattr(os, "killpg", lambda gid, sig: sent.append((gid, sig)),
                        raising=False)
    return sent
