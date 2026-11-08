"""The count after a Ctrl-C in ``wfc prune-envs``'s apply says what is really left.

The user confirmed (``--yes``) and Ctrl-C lands on the second stop; the
command counts again what is still there (stop-only) and ends with one line.
That count says plainly when Docker could not be reached for it, and counts
by the liveness the apply used: a crashed pipeline the apply's second look
found alive again is not counted. Docker and the process table are the
registered fakes at Environments' edges; Execution's liveness and its second
look are the production ones, the crashed pipeline's state built through
production paths (the harness's cut-off claim, ``write_pipeline_record``).
"""

from __future__ import annotations

from tests.fixtures import fakes
from tests.fixtures.conftest import ended_process as _ended
from tests.fixtures.fakes import fake_leftover_daemon
from tests.test_prune_envs_apply_interrupt import (
    _DOCKER_COMMAND,
    _ctrl_c_at_the_second_stop,
    _prune_yes,
    _two_ended_runs_and_a_build,
)
from tests.test_prune_envs_crashed_runs import _cut_off, _record
from wfc.environments import container_labels

#: The claim heartbeat's interval for the recount (seconds): the second look
#: waits two of them, which a loaded CI runner needs to see a real beat.
_RECOUNT_INTERVAL = 0.5


def _the_line(err: str) -> str:
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, err
    return lines[0]


def test_docker_unreachable_at_the_recount_is_said_plainly(tmp_project, monkeypatch, capsys):
    """Docker stops answering after the Ctrl-C: the line says it could not be reached.

    The build is still counted, and no docker command is named.
    """
    daemon, _ = _two_ended_runs_and_a_build(tmp_project, monkeypatch)
    kills = []

    def on_call(args):
        if args[0] == "kill":
            kills.append(args)
            if len(kills) == 2:
                daemon.failure = ("Cannot connect to the Docker daemon at "
                                  "npipe:////./pipe/docker_engine.")
                raise KeyboardInterrupt

    daemon.on_call = on_call
    rc, out, err = _prune_yes(capsys)

    assert rc == 130, out + err
    line = _the_line(err)
    assert line == ("Stopped: the prune was interrupted; Docker could not be "
                    "reached, so its containers were not counted; still there: "
                    "1 env build to stop. Run `wfc prune-envs` to finish.")
    assert not _DOCKER_COMMAND.search(out + err), out + err


def test_the_recount_uses_the_second_looks_liveness(tmp_project, monkeypatch, capsys,
                                                    processes):
    """A crashed pipeline the second look found alive is not counted as left."""
    from wfc.execution.claim import _start_heartbeat, stop_heartbeat

    # The command plans and applies in one go, so the heartbeat must move
    # inside the second look's two-interval wait on its own: the interval is
    # long enough for the heartbeat thread to beat there on a loaded host.
    fakes.short_claim_stall_window(monkeypatch, interval=_RECOUNT_INTERVAL,
                                   window=10 * _RECOUNT_INTERVAL)
    daemon = fake_leftover_daemon(monkeypatch)
    row = _cut_off(tmp_project, monkeypatch, "p-beat")
    beat = daemon.start(container_labels(tmp_project, pipeline_id="p-beat",
                                         run_id=str(row)), name="step-beat")
    _record(tmp_project, "p-beat", _ended(processes), _ended(processes).pid)
    for run_id in ("r7", "r8"):
        daemon.start(container_labels(tmp_project, pipeline_id="p-ended", run_id=run_id),
                     name=f"step-{run_id}")
    _ctrl_c_at_the_second_stop(daemon, again=False)

    _start_heartbeat(row)  # beating: the second look finds p-beat alive
    try:
        rc, out, err = _prune_yes(capsys)
    finally:
        stop_heartbeat(row)

    assert rc == 130, out + err
    assert "step-beat (pipeline p-beat" in out, out  # planned at the first look
    assert beat in daemon.running and beat not in daemon.killed
    line = _the_line(err)
    assert "still there: 1 run container to stop" in line, line
