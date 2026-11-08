"""Only a build client wfc marked is sent an interrupt; any other is terminated.

``wfc.environments.docker.build`` starts every client in a process group of
its own and marks it with a variable in its environment. An interrupt sent
to a pid that leads no process group would reach every process on the
console (Windows) or a user's shell group (POSIX), so prune-envs' apply
sends one only to an orphaned build whose every client process carries the
mark. Any other orphan, or one whose environment could not be read, has its
process tree terminated straight away, plugin before ``docker.exe``.

The process table is the registered fake at Environments' psutil edge; its
event list records every interrupt (the helper process on Windows, the
``killpg`` on POSIX, through ``_interrupt_client``), wait and terminate. The
mark's reading is checked against real child processes.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psutil
from axiom_annotations import workflow

from tests.fixtures.fakes import fake_build_processes, fake_leftover_daemon
from wfc.environments import apply_prune, plan_prune
from wfc.environments.build_clients import ORPHAN_INTERRUPT_WAIT_S, _client_marked
from wfc.environments.docker import BUILD_CLIENT_MARK


def _never_running(pipeline_id, run_id):
    return False


def _build_dir(project, env):
    return project / ".wfc" / "build" / env


@workflow(purpose="An orphaned build whose client wfc did not mark is "
                  "terminated straight away, plugin before docker.exe, with "
                  "no interrupt sent and no wait; a marked one beside it is "
                  "still interrupted first")
def test_an_unmarked_orphan_is_terminated_plugin_first_with_no_interrupt(
        tmp_path, monkeypatch):
    """The apply sends an interrupt to the marked build only."""
    project = tmp_path / "proj"
    fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    procs.build(_build_dir(project, "old"), "old", marked=False)
    new_client, new_plugin = procs.build(_build_dir(project, "new"), "new", marked=True)

    plan = plan_prune(project, run_running=_never_running)

    assert [(b.env, b.marked) for b in plan.builds] == [("old", False), ("new", True)]
    text = "\n".join(plan.lines())
    assert "old (running for" in text and "will be terminated" in text, text
    result = apply_prune(plan)

    assert [(s.build.env, s.stopped, s.how) for s in result.builds] == [
        ("old", True, "terminated"), ("new", True, "interrupted")]
    assert procs.events == [
        ("terminate", ("docker-buildx.exe", "docker.exe")),
        ("interrupt", new_client.pid, new_plugin.pid), ("wait", ORPHAN_INTERRUPT_WAIT_S)]
    assert not procs.alive
    assert "Stopped env build old (terminated)" in result.lines()


@workflow(purpose="An orphaned plugin whose docker.exe is gone counts as "
                  "unmarked when its own environment could not be read, even "
                  "though its group id names wfc's client: it is terminated, "
                  "never interrupted")
def test_a_plugin_whose_environment_could_not_be_read_counts_as_unmarked(
        tmp_path, monkeypatch):
    """Unreadable is unmarked: the safe side."""
    project = tmp_path / "proj"
    fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    client = procs.spawn("docker.exe", ["docker", "build", "--provenance=false", "-t",
                                        "local/a:_wfc-build", str(_build_dir(project, "a"))],
                         marked=True)
    procs.spawn("docker-buildx.exe",
                ["docker-buildx.exe", "buildx", "build", "-t", "local/a:_wfc-build",
                 str(_build_dir(project, "a"))],
                parent=client, group=client.pid, marked=False)  # environ unreadable
    procs.kill(client)

    result = apply_prune(plan_prune(project, run_running=_never_running))

    assert [(s.build.env, s.how) for s in result.builds] == [("a", "terminated")]
    assert procs.events == [("terminate", ("docker-buildx.exe",))]


class _Unreadable:
    """A process whose environment the OS refuses to show."""

    pid = 4

    def environ(self):
        raise psutil.AccessDenied(self.pid)


def test_the_mark_is_read_from_a_clients_environment_and_unreadable_is_unmarked():
    """Tier 1: marked, unmarked, access denied, gone."""
    script = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    marked = subprocess.Popen(script, env={**_base_env(), BUILD_CLIENT_MARK: "1"})
    plain = subprocess.Popen(script, env=_base_env())
    try:
        assert _client_marked(psutil.Process(marked.pid)) is True
        assert _client_marked(psutil.Process(plain.pid)) is False
        gone = psutil.Process(plain.pid)
    finally:
        marked.kill()
        plain.kill()
        marked.wait(timeout=10)
        plain.wait(timeout=10)
    assert _client_marked(gone) is False
    assert _client_marked(_Unreadable()) is False


def _base_env() -> dict[str, str]:
    """The variables a child needs to start, without the mark."""
    return {k: v for k, v in os.environ.items() if k != BUILD_CLIENT_MARK}
