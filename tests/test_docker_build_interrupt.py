"""The docker build runs in its own process group and passes Ctrl-C on.

A killed ``docker build`` client orphans its build: on Windows killing
``docker.exe`` leaves its ``docker-buildx.exe`` plugin holding it. So the
build is started in its own process group, a Ctrl-C is passed on to that
group as an interrupt, and only a client that ignores it for a bounded wait
has its whole tree terminated. Every output line reaches the caller's sink
as it is read.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

from tests.fixtures.fakes.docker_build import CLIENT_PID, fake_interruptible_build
from wfc.environments import docker as docker_runner
from wfc.environments.docker import (
    BUILD_CLIENT_MARK,
    BUILD_INTERRUPT_WAIT_S,
    BuildInterruptedError,
)

_CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", 1)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)


def _kinds(events: list) -> list[str]:
    return [what for what, _ in events]


def test_on_windows_the_client_starts_in_a_new_process_group(monkeypatch, tmp_path):
    """The console's Ctrl-C reaches wfc, not the client."""
    script = fake_interruptible_build(monkeypatch, windows=True)

    docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert script.spawn_kwargs["creationflags"] & _NEW_GROUP
    assert "start_new_session" not in script.spawn_kwargs


def test_the_client_is_marked_in_its_environment_and_its_argv_is_unchanged(
    monkeypatch, tmp_path,
):
    """The mark prune-envs reads rides in the environment, nowhere else."""
    monkeypatch.setenv("WFC_TEST_SENTINEL", "kept")
    script = fake_interruptible_build(monkeypatch)

    docker_runner.build(tmp_path, "local/env:_wfc-build", {"b": "2", "a": "1"})

    env = script.spawn_kwargs["env"]
    assert env[BUILD_CLIENT_MARK] == "1" and env["DOCKER_BUILDKIT"] == "1"
    assert env["WFC_TEST_SENTINEL"] == "kept"
    assert script.cmd == ["docker", "build", "--provenance=false", "-t",
                          "local/env:_wfc-build", "--label", "a=1", "--label", "b=2",
                          str(tmp_path)]
    assert not any(BUILD_CLIENT_MARK in arg for arg in script.cmd)


def test_off_windows_the_client_starts_in_its_own_session(monkeypatch, tmp_path):
    """The terminal's SIGINT reaches wfc, not the client."""
    script = fake_interruptible_build(monkeypatch, windows=False)

    docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert script.spawn_kwargs["start_new_session"] is True
    assert "creationflags" not in script.spawn_kwargs


@pytest.mark.parametrize(("windows", "sent"), [(True, _CTRL_BREAK),
                                               (False, signal.SIGINT)])
def test_ctrl_c_is_passed_on_and_a_client_that_ends_is_never_terminated(
    monkeypatch, tmp_path, windows, sent,
):
    """The interrupt goes to the client's group; the client ends on it."""
    script = fake_interruptible_build(monkeypatch, interrupt=True, obeys=True,
                                      windows=windows)

    with pytest.raises(BuildInterruptedError) as caught:
        docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert caught.value.client_ended is True
    assert script.events[0] == ("interrupt", (CLIENT_PID, sent))
    assert "terminate" not in _kinds(script.events)
    assert "kill" not in _kinds(script.events)


def test_a_client_that_ignores_the_interrupt_has_its_whole_tree_ended_after_the_wait(
    monkeypatch, tmp_path,
):
    """Interrupt first, then the bounded wait, then the plugin and docker.exe."""
    script = fake_interruptible_build(monkeypatch, interrupt=True, obeys=False)

    with pytest.raises(BuildInterruptedError) as caught:
        docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert caught.value.client_ended is False
    kinds = _kinds(script.events)
    assert kinds[:2] == ["interrupt", "wait"]
    assert script.events[1] == ("wait", BUILD_INTERRUPT_WAIT_S)
    ended = [name for what, name in script.events if what in ("terminate", "kill")]
    assert "docker-buildx.exe" in ended, "the plugin holds the build: it must end"
    assert ended.index("docker-buildx.exe") < ended.index("docker.exe")
    assert not script.plugin.alive and not script.client.alive


def test_a_second_ctrl_c_during_the_wait_ends_the_tree_at_once(monkeypatch, tmp_path):
    """A second Ctrl-C cuts the wait short; the interrupt still went first."""
    script = fake_interruptible_build(monkeypatch, interrupt=True, obeys=False,
                                      second_interrupt=True)

    with pytest.raises(BuildInterruptedError) as caught:
        docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert caught.value.client_ended is False
    assert _kinds(script.events)[:3] == ["interrupt", "wait", "terminate"]
    assert not script.plugin.alive


def test_an_interrupt_that_cannot_be_sent_falls_back_to_the_tree(monkeypatch, tmp_path):
    """No console to signal: nothing to wait for, the tree is ended."""
    script = fake_interruptible_build(monkeypatch, interrupt=True, obeys=True,
                                      console=False)

    with pytest.raises(BuildInterruptedError) as caught:
        docker_runner.build(tmp_path, "local/env:_wfc-build")

    assert caught.value.client_ended is False
    assert _kinds(script.events)[0] == "interrupt"
    assert "wait" not in _kinds(script.events)
    assert not script.plugin.alive and not script.client.alive


def test_the_interrupt_is_a_keyboard_interrupt_for_callers_that_do_not_know_it():
    """A caller that does not catch it ends as on any Ctrl-C."""
    assert issubclass(BuildInterruptedError, KeyboardInterrupt)


def test_every_output_line_reaches_the_sink_and_the_failure_keeps_the_tail(
    monkeypatch, tmp_path,
):
    """The sink sees every line in order; the error still carries the tail."""
    output = "".join(f"#5 line {i}\n" for i in range(60)) + "COPY failed: no file\n"
    fake_interruptible_build(monkeypatch, output=output, returncode=1)
    seen: list[str] = []

    with pytest.raises(RuntimeError, match="COPY failed") as caught:
        docker_runner.build(tmp_path, "local/env:_wfc-build", sink=seen.append)

    assert seen == output.splitlines()
    message = str(caught.value) + "\n"
    assert "#5 line 21\n" in message, "the last 40 lines are kept"
    assert "#5 line 20\n" not in message


def test_a_sink_that_fails_does_not_stall_the_build_and_is_raised_after(
    monkeypatch, tmp_path,
):
    """A failing sink is dropped, the pipe keeps draining, its error surfaces."""
    output = "".join(f"line {i}\n" for i in range(5))
    fake_interruptible_build(monkeypatch, output=output, returncode=0)
    seen: list[str] = []

    def sink(line: str) -> None:
        seen.append(line)
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        docker_runner.build(tmp_path, "local/env:_wfc-build", sink=sink)

    assert seen == ["line 0"]
