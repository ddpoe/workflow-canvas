"""``wfc cancel <pipeline id>`` names what it stops, asks, then cancels.

Every invocation runs the real CLI entry point (``cli_main``, through
``run_cli``) over a recorded running pipeline: the harness's scenario runner
leaves the chain sel -> a -> b cut off with S1's ``a`` in flight (its claim's
running row), and the pipeline's record names an owner and an engine that
are real processes. pytest's stdin is not a terminal; the answer typed at a
terminal goes through the ``stub_terminal`` / ``stub_interactive_prompt``
fakes.
"""

from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import ended_process
from tests.fixtures.fakes import stub_interactive_prompt, stub_terminal
from tests.fixtures.routes.clients import run_cli
from tests.harness import Scenario, node, run_scenario, selector, wire
from wfc.execution import read_pipeline_record
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CLI,
    process_identity,
    record_engine,
    write_pipeline_record,
)
from wfc.persistence import Run, get_session

A_S1 = ("a", "S1", "default")
TARGETS = {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}


def _rows(pid: str) -> list[tuple[str, str, str, str | None]]:
    """Every row of the pipeline as (node, sample, status, cancel reason), by id."""
    with get_session() as s:
        return [(r.node_id, r.sample, r.status, r.cancel_reason)
                for r in s.exec(select(Run).where(Run.pipeline_id == pid)
                                .order_by(Run.id)).all()]


def _recorded_running(root_fixture, monkeypatch, processes, pid: str, *,
                      owner_alive: bool = False):
    """A pipeline cut off with S1's a running, recorded with a live engine.

    Returns:
        (project root, the engine process, the owner process or None).
    """
    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire("sel")]),
                               node("b", inputs=[wire("a")])],
                        samples=["S1", "S2"], pipeline_id=pid)
    obs = run_scenario(scenario, root=root_fixture, monkeypatch=monkeypatch,
                       interrupted=A_S1, pipeline_end=False)
    root = obs.project.root
    assert _rows(pid) == [("a", "S1", "running", None)]
    owner = processes() if owner_alive else None
    write_pipeline_record(root, pid, ENTRY_POINT_CLI,
                          owner=(process_identity(owner.pid) if owner is not None
                                 else ended_process(processes)))
    engine = processes()
    record_engine(root, pid, engine.pid)
    return root, engine, owner


@workflow(purpose="wfc cancel --yes on a recorded running pipeline whose owner "
                  "is gone names the pipeline, its running step and the engine, "
                  "stops the engine, and leaves every target with a cancelled-by-"
                  "the-user record; it says the verb wrote the remaining records")
def test_cancel_with_yes_cancels_and_every_target_ends_with_a_record(
        tmp_project, monkeypatch, processes):
    pid = "cli-cancel-yes"
    root, engine, _ = _recorded_running(tmp_project, monkeypatch, processes, pid)

    口 = Step(step_num=1, name="Cancel with --yes",
             purpose="The plan prints, nothing is asked, the cancel runs")
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "n", prompts=prompts)
    result = run_cli("cancel", pid, "--yes")
    assert result.returncode == 0, result.stderr
    assert prompts == []
    assert f"wfc cancel stops pipeline {pid} (started by wfc run-pipeline)." in result.stdout
    assert "a on S1" in result.stdout
    assert f"Engine: process {engine.pid}" in result.stdout

    口 = Step(step_num=2, name="Every target ends with a record",
             purpose="The engine is stopped, the rows are cancelled for the user, "
                     "the record goes, and the output says who wrote what")
    assert engine.wait(timeout=10) is not None, "the engine was stopped"
    rows = _rows(pid)
    assert {(n, s) for n, s, _, _ in rows} == TARGETS and len(rows) == 4
    assert {(status, reason) for _, _, status, reason in rows} == {("cancelled", "user")}
    assert read_pipeline_record(root, pid) is None
    assert f"Cancelled pipeline {pid}: 1 running step(s) cancelled." in result.stdout
    assert "The engine was stopped." in result.stdout
    assert "Its owner is gone, so wfc cancel recorded the remaining steps: " \
           "3 record(s) written." in result.stdout


@workflow(purpose="wfc cancel on a pipeline whose owner is still running "
                  "stops the engine and cancels the running step, and says the "
                  "owner records the remaining steps as it ends")
def test_cancel_with_the_owner_running_leaves_the_rest_to_the_owner(
        tmp_project, monkeypatch, processes):
    pid = "cli-cancel-owner-alive"
    root, engine, _ = _recorded_running(tmp_project, monkeypatch, processes, pid,
                                        owner_alive=True)

    result = run_cli("cancel", pid, "--yes")

    assert result.returncode == 0, result.stderr
    assert engine.wait(timeout=10) is not None
    assert _rows(pid) == [("a", "S1", "cancelled", "user")]
    assert read_pipeline_record(root, pid).cancel_requested
    assert "Its owner is still running: it records the remaining steps " \
           "as cancelled as it ends." in result.stdout


@workflow(purpose="Without --yes, wfc cancel changes nothing unless the user "
                  "agrees: with no terminal it is refused naming --yes, and "
                  "answering no at Proceed? [y/N] leaves the rows, the record and "
                  "the engine as they were")
def test_answering_no_leaves_everything_as_it_was(tmp_project, monkeypatch, processes):
    pid = "cli-cancel-no"
    root, engine, _ = _recorded_running(tmp_project, monkeypatch, processes, pid)
    before = (_rows(pid), read_pipeline_record(root, pid))

    口 = Step(step_num=1, name="No terminal, no --yes",
             purpose="Refused after the plan prints, naming --yes")
    result = run_cli("cancel", pid)
    assert result.returncode == 1
    assert "a on S1" in result.stdout
    assert "--yes" in result.stderr and "Nothing was cancelled" in result.stderr
    assert (_rows(pid), read_pipeline_record(root, pid)) == before

    口 = Step(step_num=2, name="Answer no at the terminal",
             purpose="The terminal and the answer are the only substitutions")
    stub_terminal(monkeypatch, True)
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "n", prompts=prompts)
    result = run_cli("cancel", pid)
    assert result.returncode == 1
    assert prompts == ["Proceed? [y/N]: "]
    assert "Nothing was cancelled." in result.stdout

    口 = Step(step_num=3, name="Nothing changed",
             purpose="The rows, the record (no cancel flag) and the engine are as "
                     "they were")
    assert (_rows(pid), read_pipeline_record(root, pid)) == before
    assert not before[1].cancel_requested
    assert engine.poll() is None, "the engine is still running"


def test_the_plan_says_an_engine_that_already_ended_is_not_stopped(
        tmp_project, monkeypatch, processes):
    """The plan names the engine as one to stop only while its recorded
    process is alive; an engine that ended reads as already gone."""
    pid = "cli-cancel-engine-gone"
    _, engine, _ = _recorded_running(tmp_project, monkeypatch, processes, pid)
    engine.kill()
    engine.wait(timeout=10)

    result = run_cli("cancel", pid)

    assert result.returncode == 1
    assert f"Engine: process {engine.pid}" in result.stdout
    assert "already gone; nothing is stopped" in result.stdout
    assert "will be stopped" not in result.stdout


@pytest.mark.parametrize("pipeline_id", ["no-such-pipeline", "../outside"],
                         ids=["unknown", "not-a-pipeline-id"])
@workflow(purpose="wfc cancel on an id with no record and no running row, or "
                  "on a string that is not a pipeline id, is refused with a message "
                  "naming the id; nothing is asked")
def test_an_unknown_pipeline_is_refused(tmp_project, monkeypatch, pipeline_id):
    from tests.harness import build_project

    build_project(Scenario(nodes=[selector()], samples=["S1"]), root=tmp_project,
                  monkeypatch=monkeypatch)
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "y", prompts=prompts)

    result = run_cli("cancel", pipeline_id, "--yes")

    assert result.returncode == 1
    assert f"pipeline {pipeline_id} has no record and no running step" in result.stderr
    assert prompts == []
    assert "Cancelled" not in result.stdout
