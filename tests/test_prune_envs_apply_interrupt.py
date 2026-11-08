"""Ctrl-C while ``wfc prune-envs`` stops what it listed.

The user confirmed (``--yes``) and the apply is part way through: the second
run's stop is where Ctrl-C lands. The command ends with one plain line and
exit 130, never a traceback; the line says the prune was interrupted, how
many leftovers are still there (counted again) and that ``wfc prune-envs``
finishes it. A second Ctrl-C during that count ends with the line and no
count. Docker and the process table are the registered fakes at
Environments' edges; Execution's liveness is the production one (no record
and no rows: both runs have ended).
"""

from __future__ import annotations

import re

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import fake_build_processes, fake_leftover_daemon
from wfc.cli import cli_main
from wfc.environments import container_labels

#: A Docker command named in a message (case 10 forbids it).
_DOCKER_COMMAND = re.compile(
    r"\bdocker\s+(ps|kill|run|rm|rmi|stop|container|image|images|build|buildx|"
    r"builder|system|inspect|prune)\b", re.IGNORECASE)


def _two_ended_runs_and_a_build(root, monkeypatch):
    """Two ended runs' containers and an orphaned build, the apply's three stops."""
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    for run_id in ("7", "8"):
        daemon.start(container_labels(root, pipeline_id="p-ended", run_id=run_id),
                     name=f"step-{run_id}")
    procs.build(root / ".wfc" / "build" / "alpha", "alpha")
    return daemon, procs


def _ctrl_c_at_the_second_stop(daemon, *, again: bool) -> None:
    """Raise ``KeyboardInterrupt`` at the second kill; with *again*, at every call after it."""
    kills = []

    def on_call(args):
        if again and len(kills) >= 2:
            raise KeyboardInterrupt
        if args[0] == "kill":
            kills.append(args)
            if len(kills) == 2:
                raise KeyboardInterrupt

    daemon.on_call = on_call


def _prune_yes(capsys) -> tuple[int, str, str]:
    """``wfc prune-envs --yes``; a Ctrl-C that escapes the CLI fails the test."""
    capsys.readouterr()
    try:
        rc = cli_main(["prune-envs", "--yes"])
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C during the apply escaped wfc prune-envs as a traceback")
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@workflow(purpose="Ctrl-C at the second stop of wfc prune-envs --yes ends with "
                  "one plain line and exit 130, no traceback: the line says the "
                  "prune was interrupted, counts what is still there (the "
                  "second run's container and the build) and names wfc "
                  "prune-envs to finish; no message names a docker command")
def test_ctrl_c_during_the_apply_says_what_is_still_left(tmp_project, monkeypatch, capsys):
    """One line, exit 130, the recount of what the interrupted apply left."""
    daemon, procs = _two_ended_runs_and_a_build(tmp_project, monkeypatch)

    口 = Step(step_num=1, name="Ctrl-C at the second stop",
             purpose="The first run's container is stopped, then Ctrl-C lands "
                     "on the second's; the build is never reached")
    _ctrl_c_at_the_second_stop(daemon, again=False)
    rc, out, err = _prune_yes(capsys)
    assert rc == 130, out + err
    assert len(daemon.killed) == 1 and len(daemon.running) == 1
    assert procs.events == []

    口 = Step(step_num=2, name="The one line",
             purpose="One plain line: interrupted, what is still there, wfc "
                     "prune-envs to finish; no traceback, no docker command")
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, err
    line = lines[0]
    assert "interrupted" in line.lower()
    assert "1 run container to stop" in line and "1 env build to stop" in line, line
    assert "`wfc prune-envs`" in line
    assert "Traceback" not in out + err
    assert not _DOCKER_COMMAND.search(out + err), out + err


def test_a_second_ctrl_c_during_the_recount_ends_without_a_count(
        tmp_project, monkeypatch, capsys):
    """Ctrl-C again while the leftovers are counted: the line, no count, exit 130."""
    daemon, _ = _two_ended_runs_and_a_build(tmp_project, monkeypatch)
    _ctrl_c_at_the_second_stop(daemon, again=True)
    rc, out, err = _prune_yes(capsys)
    assert rc == 130, out + err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, err
    assert "interrupted" in lines[0].lower() and "`wfc prune-envs`" in lines[0]
    assert "to stop" not in lines[0], lines[0]
    assert not _DOCKER_COMMAND.search(out + err), out + err
