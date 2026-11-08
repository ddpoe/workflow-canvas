"""A crashed pipeline and a dead ``wfc try`` count as ended for ``wfc prune-envs``.

A pipeline whose record names an owner and an engine that are both gone has
crashed: its rows still saying ``running`` no longer keep its containers off
the list, and the plan names ``wfc cancel <id>`` to close its record
(decisions::d-27). A running row's heartbeat that moves between the plan and
the apply makes it alive again, and the apply leaves its container alone. A
draft step whose recorded process is gone has ended: its try's container is
listed and stopped; a live try's is not (decisions::d-28).

State is built through production paths: the running row is the harness's
cut-off claim (Execution's own claim writer), the record is
``write_pipeline_record`` / ``record_engine`` naming processes that ran and
exited, the draft step's entry is ``record_draft_step``'s. Execution's
liveness is the production ``run_liveness``. Docker and the process table
are the registered fakes at Environments' edges.

Does not prove: anything about a real Docker daemon (the Docker-marked
witness in ``tests/integration/test_prune_envs_crashed_docker.py`` does).
"""

from __future__ import annotations

import os
import re

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.fixtures.conftest import ended_process as _ended
from tests.fixtures.draft_project import built_draft_project
from tests.fixtures.fakes import (
    fake_leftover_daemon,
    stub_docker_image_inspect,
    stub_interactive_prompt,
    stub_terminal,
)
from tests.harness import Scenario, node, run_scenario, selector, wire
from wfc import layout
from wfc.cli import cli_main
from wfc.environments import apply_prune, container_labels, plan_prune
from wfc.execution import RunLiveness, check_leftovers, run_liveness
from wfc.execution.draft import DRAFT_STEP_PROCESS_KEY, read_draft_run, record_draft_step
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CLI,
    ProcessIdentity,
    process_identity,
    record_engine,
    write_pipeline_record,
)
from wfc.persistence import Run, get_session

#: A Docker command named in a message (no message may name one).
_DOCKER_COMMAND = re.compile(
    r"\bdocker\s+(ps|kill|run|rm|rmi|stop|container|image|images|build|buildx|"
    r"builder|system|inspect|prune)\b", re.IGNORECASE)

#: The claim heartbeat's interval the tests run with (seconds).
_INTERVAL = 0.05


def _cut_off(root, monkeypatch, pid: str) -> int:
    """A pipeline cut off with S1's a in flight; return its running row's id."""
    run_scenario(Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                          samples=["S1"], pipeline_id=pid),
                 root=root, monkeypatch=monkeypatch, interrupted=("a", "S1", "default"),
                 pipeline_end=False)
    with get_session() as s:
        (row,) = s.exec(select(Run).where(Run.pipeline_id == pid)).all()
    assert row.status == "running"
    return int(row.id)


def _record(root, pid: str, owner: ProcessIdentity, engine_pid: int | None) -> None:
    """Write the pipeline's record naming *owner*, and its engine when given."""
    (layout.pipelines_dir(root) / pid).mkdir(parents=True, exist_ok=True)
    write_pipeline_record(root, pid, ENTRY_POINT_CLI, owner=owner)
    if engine_pid is not None:
        record_engine(root, pid, engine_pid)


def _wait_for_beat(run_id: int, seen, timeout: float = 10.0) -> None:
    """Wait until the run row's heartbeat differs from *seen* (its claim beat once)."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        with get_session() as s:
            if s.get(Run, run_id).heartbeat != seen:
                return
        assert time.monotonic() < deadline, f"run {run_id}'s heartbeat never moved"
        time.sleep(0.01)


def _plan(root):
    """The plan as ``wfc prune-envs`` makes it, over Execution's liveness."""
    liveness = run_liveness(root)
    return plan_prune(root, run_running=liveness, pipeline_ids=liveness.pipeline_ids,
                      crashed_pipelines=liveness.crashed_pipelines,
                      dead_tries=liveness.dead_draft_steps,
                      second_look=liveness.second_look)


def _prune(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    rc = cli_main(["prune-envs", *argv])
    captured = capsys.readouterr()
    return rc, captured.out + captured.err


@workflow(purpose="A pipeline whose record names an owner and an engine that "
                  "are gone has crashed: with no record its running row keeps "
                  "its container off the list; with the record the container "
                  "is listed under a line naming wfc cancel <id>, counted by "
                  "the list-envs footer and the doctor row, and stopped on yes, "
                  "the result repeating the wfc cancel line; no message names "
                  "a docker command")
def test_a_crashed_pipelines_container_is_listed_naming_wfc_cancel_and_stopped(
        tmp_project, monkeypatch, capsys, processes):
    fakes.short_claim_stall_window(monkeypatch, interval=_INTERVAL)
    daemon = fake_leftover_daemon(monkeypatch)
    said: list[str] = []

    口 = Step(step_num=1, name="Cut off, with no record",
             purpose="Its running row and its container; with no record the "
                     "row keeps the container running (the safe side)")
    row = _cut_off(tmp_project, monkeypatch, "p-crash")
    crashed = daemon.start(container_labels(tmp_project, pipeline_id="p-crash",
                                            run_id=str(row)), name="step-a")
    rc, out = _prune(capsys, "--dry-run")
    assert rc == 0 and "step-a" not in out and "Nothing to stop." in out, out

    口 = Step(step_num=2, name="Its record names a gone owner and engine",
             purpose="The plan lists the container under the line saying the wfc "
                     "that ran it is gone and naming wfc cancel p-crash")
    _record(tmp_project, "p-crash", _ended(processes), _ended(processes).pid)
    rc, out = _prune(capsys, "--dry-run")
    said.append(out)
    assert rc == 0, out
    assert ("The wfc that ran pipeline p-crash is gone and its record still says "
            "running; `wfc cancel p-crash` closes its record.") in out
    assert f"step-a (pipeline p-crash, run {row})" in out
    assert daemon.killed == []

    口 = Step(step_num=3, name="The footer and the doctor row count it",
             purpose="One run container to stop, naming wfc prune-envs")
    stub_docker_image_inspect(monkeypatch, RuntimeError("Cannot connect to the Docker daemon"))
    capsys.readouterr()
    assert cli_main(["list-envs"]) == 0
    footer = capsys.readouterr().out.rstrip().splitlines()[-1]
    said.append(footer)
    assert "1 run container to stop" in footer and "`wfc prune-envs`" in footer, footer
    doctor = check_leftovers(tmp_project)
    said.append(doctor.message)
    assert doctor.status == "warn" and "1 run container to stop" in doctor.message, doctor

    口 = Step(step_num=4, name="A yes stops it",
             purpose="The apply's second look sees no heartbeat move; the "
                     "container stops and the result repeats the wfc cancel line")
    stub_terminal(monkeypatch, True)
    stub_interactive_prompt(monkeypatch, "y")
    rc, out = _prune(capsys)
    said.append(out)
    assert rc == 0, out
    assert daemon.killed == [crashed]
    assert f"Stopped run container step-a (pipeline p-crash, run {row})" in out
    assert out.count("`wfc cancel p-crash` closes its record") == 2, out

    text = "\n".join(said)
    assert not _DOCKER_COMMAND.search(text), text


@pytest.mark.parametrize("case", ["owner alive", "engine alive", "owner gone, no engine",
                                  "owner pid reused"])
def test_a_pipeline_is_crashed_only_when_neither_its_owner_nor_its_engine_lives(
        tmp_project, monkeypatch, processes, case):
    """A live owner or a live engine keeps the container; gone, unrecorded or reused do not."""
    daemon = fake_leftover_daemon(monkeypatch)
    row = _cut_off(tmp_project, monkeypatch, "p-x")
    daemon.start(container_labels(tmp_project, pipeline_id="p-x", run_id=str(row)),
                 name="step-a")
    live = processes()
    owner, engine = {
        "owner alive": (process_identity(live.pid), _ended(processes).pid),
        "engine alive": (_ended(processes), live.pid),
        "owner gone, no engine": (_ended(processes), None),
        "owner pid reused": (ProcessIdentity(live.pid, process_identity(live.pid).created - 1000),
                             _ended(processes).pid),
    }[case]
    _record(tmp_project, "p-x", owner, engine)

    plan = _plan(tmp_project)

    listed = [c.name for c in plan.containers]
    assert listed == ([] if case.endswith("alive") else ["step-a"]), plan.lines()
    assert ("wfc cancel p-x" in "\n".join(plan.lines())) is not case.endswith("alive")


@workflow(purpose="The heartbeat guard: a crashed pipeline's container is "
                  "planned, then its running row's heartbeat moves before the "
                  "apply; the apply's second look, taken no sooner than two "
                  "heartbeat intervals after the first, sees it and leaves the "
                  "container running")
def test_a_heartbeat_that_moves_between_the_plan_and_the_apply_leaves_it_alone(
        tmp_project, monkeypatch, processes):
    from wfc.execution.claim import _start_heartbeat, stop_heartbeat

    fakes.short_claim_stall_window(monkeypatch, interval=_INTERVAL)
    daemon = fake_leftover_daemon(monkeypatch)
    row = _cut_off(tmp_project, monkeypatch, "p-beat")
    container = daemon.start(container_labels(tmp_project, pipeline_id="p-beat",
                                              run_id=str(row)), name="step-a")
    _record(tmp_project, "p-beat", _ended(processes), _ended(processes).pid)

    口 = Step(step_num=1, name="Plan at one look",
             purpose="Owner and engine gone, the heartbeat not yet seen moving: listed")
    plan = _plan(tmp_project)
    assert [c.id for c in plan.containers] == [container]

    口 = Step(step_num=2, name="The row's heartbeat moves; apply",
             purpose="The second look sees the heartbeat move: the pipeline is "
                     "alive and its container is left alone")
    with get_session() as s:
        first = s.get(Run, row).heartbeat
    _start_heartbeat(row)
    try:
        _wait_for_beat(row, first)  # moved once before the apply looks again
        result = apply_prune(plan)
    finally:
        stop_heartbeat(row)
    assert daemon.killed == [] and container in daemon.running
    assert result.containers == [] and result.skipped, result.lines()
    assert "wfc cancel" not in "\n".join(result.lines())


def test_the_second_look_waits_two_intervals_and_only_for_a_crashed_container(
        tmp_project, monkeypatch, processes):
    """The apply asks for the second look only with a crashed pipeline's container planned."""
    fakes.short_claim_stall_window(monkeypatch, interval=0.2)
    daemon = fake_leftover_daemon(monkeypatch)
    row = _cut_off(tmp_project, monkeypatch, "p-wait")
    _record(tmp_project, "p-wait", _ended(processes), _ended(processes).pid)
    liveness = run_liveness(tmp_project)
    assert liveness.crashed_pipelines == frozenset({"p-wait"})

    waits: list[float] = []
    second = liveness.second_look(sleep=waits.append)
    (wait,) = waits  # the rest of two 0.2 s intervals since the first look
    assert 0 < wait <= 0.4 + 1e-3  # never more than the two intervals (float slack)
    assert not second("p-wait", str(row))
    assert RunLiveness(tmp_project).second_look(sleep=waits.append) is not None
    assert len(waits) == 1, "no crashed pipeline: nothing is waited for"

    looks: list[str] = []
    ended = daemon.start(container_labels(tmp_project, pipeline_id="p-other", run_id="9"))
    plan = plan_prune(tmp_project, run_running=lambda p, r: False,
                      crashed_pipelines={"p-wait"},
                      second_look=lambda: looks.append("look") or (lambda p, r: False))
    assert apply_prune(plan).containers[0].id == ended and looks == []
    daemon.start(container_labels(tmp_project, pipeline_id="p-wait", run_id=str(row)))
    plan = plan_prune(tmp_project, run_running=lambda p, r: False,
                      crashed_pipelines={"p-wait"},
                      second_look=lambda: looks.append("look") or (lambda p, r: False))
    apply_prune(plan)
    assert looks == ["look"]


def _try_entry(root, module: str, method: str, process: ProcessIdentity | None) -> str:
    """Rewrite the draft's mid step as running in *process*; return its pipeline id."""
    record = read_draft_run(module, method, root)
    step = dict(record["steps"]["mid"]["s1"])
    step.update(status="running", finished_at=None)
    if process is None:
        step.pop(DRAFT_STEP_PROCESS_KEY, None)
    else:
        step[DRAFT_STEP_PROCESS_KEY] = process.to_json()
    record_draft_step(module, method, pipeline_id=record["pipeline_id"],
                      pipeline_name=record["pipeline_name"], step=step, root=root)
    return record["pipeline_id"]


@workflow(purpose="A dead wfc try: a running draft step whose recorded process "
                  "is gone has ended; its container is listed under a line "
                  "naming its draft step and no closing command, counted by "
                  "the doctor row, and stopped; a live try's container, and "
                  "one whose entry records no process, is not listed")
def test_a_dead_trys_container_is_listed_and_stopped_and_a_live_trys_is_not(
        tmp_project, monkeypatch, processes):
    scenario, module, _up, mid_ref = built_draft_project(tmp_project, monkeypatch)
    method = scenario.node_by_id("mid").method_name
    daemon = fake_leftover_daemon(monkeypatch)

    口 = Step(step_num=1, name="The try's process is gone",
             purpose="Listed under the draft step's line, with no wfc cancel; "
                     "the doctor row counts it; the apply stops it")
    pid = _try_entry(tmp_project, module, method, _ended(processes))
    dead = daemon.start(container_labels(tmp_project, pipeline_id=pid, run_id=mid_ref),
                        name="try-mid")
    plan = _plan(tmp_project)
    text = "\n".join(plan.lines())
    assert [c.id for c in plan.containers] == [dead], text
    assert f"The wfc try that ran draft step {mid_ref} is gone" in text
    assert "wfc cancel" not in text and not _DOCKER_COMMAND.search(text)
    row = check_leftovers(tmp_project)
    assert row.status == "warn" and "1 run container to stop" in row.message, row
    result = apply_prune(plan)
    assert daemon.killed == [dead] and [c.id for c in result.containers] == [dead]
    assert "wfc cancel" not in "\n".join(result.lines())

    口 = Step(step_num=2, name="A live try, then one with no recorded process",
             purpose="Neither container is listed: the step is still running")
    for process in (process_identity(os.getpid()), None):
        _try_entry(tmp_project, module, method, process)
        live = daemon.start(container_labels(tmp_project, pipeline_id=pid, run_id=mid_ref))
        assert _plan(tmp_project).containers == ()
        assert live in daemon.running
        daemon.running.pop(live)
