"""The database half of ``wfc prune-envs``' liveness: a running row keeps its container.

A container whose labels name a run that is still running is never listed
(US-3): a ``runs`` row still ``running`` with no pipeline record, a running
row whose pipeline record names a live owner, a draft step still ``running``
in a live process, and one whose entry records no process (written before
draft steps recorded one). Once Execution closes the row out (failed, or
cancelled for the user) the same container, its labels unchanged, is
listed. Records that cannot be read count every run as running
(decisions::d-24, d-27, d-28, d-37).

State is built through production paths: the running row is the harness's
cut-off claim (Execution's own claim writer), the record is
``write_pipeline_record``'s naming this process, the draft step's entry is
``record_draft_step``'s, and the close-outs are Execution's own
``close_out_pipeline`` and ``end_running_draft_steps``. Execution's liveness
is the production ``run_liveness``, reached through ``wfc prune-envs
--dry-run``. Docker is the registered fake at Environments' edge.

Does not prove: a crashed pipeline or a dead ``wfc try`` (task 19's
``tests/test_prune_envs_crashed_runs.py``), anything about a real Docker
daemon.
"""

from __future__ import annotations

import os

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.draft_project import built_draft_project
from tests.fixtures.fakes import fake_leftover_daemon
from tests.harness import Scenario, node, run_scenario, selector, wire
from wfc import layout
from wfc.cli import cli_main
from wfc.environments import container_labels
from wfc.execution.draft import (
    DRAFT_STEP_PROCESS_KEY,
    end_running_draft_steps,
    read_draft_run,
    record_draft_step,
)
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CLI,
    process_identity,
    write_pipeline_record,
)
from wfc.execution.recovery import close_out_pipeline
from wfc.persistence import Run, get_session

#: The line ``wfc prune-envs`` prints when the runs could not be read.
_UNREADABLE = "so every run counts as still running and no run container is listed"


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


def _row_status(run_id: int) -> str:
    with get_session() as s:
        return s.get(Run, run_id).status


def _dry_run(capsys) -> str:
    """``wfc prune-envs --dry-run``'s output (it exits 0)."""
    capsys.readouterr()
    rc = cli_main(["prune-envs", "--dry-run"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert rc == 0, out
    return out


@workflow(purpose="A runs row still running keeps its container off the "
                  "prune-envs list, with no pipeline record and with a record "
                  "naming a live owner; once Execution closes the row out "
                  "(failed with no record, cancelled for the user with one) "
                  "the same container, its labels unchanged, is listed")
@pytest.mark.parametrize("case", ["no record", "live owner"])
def test_a_running_row_keeps_its_container_off_the_list_until_it_is_closed_out(
        tmp_project, monkeypatch, capsys, case):
    daemon = fake_leftover_daemon(monkeypatch)
    pid = f"p-{case.replace(' ', '-')}"

    口 = Step(step_num=1, name="A running row, its container",
             purpose="The row is running; with a live owner the pipeline's "
                     "record names this process")
    row = _cut_off(tmp_project, monkeypatch, pid)
    if case == "live owner":
        (layout.pipelines_dir(tmp_project) / pid).mkdir(parents=True, exist_ok=True)
        write_pipeline_record(tmp_project, pid, ENTRY_POINT_CLI)  # owned by this process
    container = daemon.start(container_labels(tmp_project, pipeline_id=pid,
                                              run_id=str(row)), name="step-a")

    口 = Step(step_num=2, name="Never listed while it runs",
             purpose="The dry run lists nothing to stop")
    out = _dry_run(capsys)
    assert "step-a" not in out and "Nothing to stop." in out, out

    口 = Step(step_num=3, name="Execution closes the row out",
             purpose="Failed as interrupted with no record; cancelled for the "
                     "user with one (the record is removed)")
    close_out_pipeline(tmp_project, pid, user_cancelled=case == "live owner")
    assert _row_status(row) == ("cancelled" if case == "live owner" else "failed")

    口 = Step(step_num=4, name="The same container is listed",
             purpose="Its labels name an ended run; the dry run lists it and stops nothing")
    out = _dry_run(capsys)
    assert f"step-a (pipeline {pid}, run {row})" in out, out
    assert daemon.killed == [] and container in daemon.running


def _draft_entry(root, module: str, method: str, *, process) -> str:
    """Rewrite the draft's mid step as running in *process* (``None``: none recorded).

    Returns:
        The draft run's pipeline id.
    """
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


@workflow(purpose="A draft step still running keeps its try's container off "
                  "the prune-envs list, whether its entry records a live "
                  "process (this one) or no process at all (an entry from "
                  "before draft steps recorded one); once Execution ends the "
                  "step (failed, or cancelled) the same container is listed")
@pytest.mark.parametrize("case,ending", [("live process", "failed"),
                                         ("no recorded process", "cancelled")])
def test_a_running_draft_step_keeps_its_container_off_the_list_until_it_ends(
        tmp_project, monkeypatch, capsys, case, ending):
    scenario, module, _up, mid_ref = built_draft_project(tmp_project, monkeypatch)
    method = scenario.node_by_id("mid").method_name
    daemon = fake_leftover_daemon(monkeypatch)

    口 = Step(step_num=1, name="A running draft step, its container",
             purpose="The entry is running, recording this process or none")
    process = process_identity(os.getpid()) if case == "live process" else None
    pid = _draft_entry(tmp_project, module, method, process=process)
    container = daemon.start(container_labels(tmp_project, pipeline_id=pid,
                                              run_id=mid_ref), name="try-mid")

    口 = Step(step_num=2, name="Never listed while it runs",
             purpose="The dry run lists nothing to stop")
    out = _dry_run(capsys)
    assert "try-mid" not in out and "Nothing to stop." in out, out

    口 = Step(step_num=3, name="Execution ends the step",
             purpose="The pipeline's running draft steps end as its rows would")
    assert end_running_draft_steps(pid, status=ending, message="ended",
                                   cancel_reason="user" if ending == "cancelled" else None,
                                   root=tmp_project) == 1
    assert read_draft_run(module, method, tmp_project)["steps"]["mid"]["s1"]["status"] == ending

    口 = Step(step_num=4, name="The same container is listed",
             purpose="Its labels name an ended draft step; nothing is stopped")
    out = _dry_run(capsys)
    assert "try-mid" in out and "Nothing to stop." not in out, out
    assert daemon.killed == [] and container in daemon.running


def test_records_that_cannot_be_read_count_every_run_as_running(
        tmp_project, monkeypatch, capsys):
    """An unreadable draft store lists no run container, saying why; readable again, it is listed."""
    daemon = fake_leftover_daemon(monkeypatch)
    daemon.start(container_labels(tmp_project, pipeline_id="p-ended", run_id="7"),
                 name="step-7")
    out = _dry_run(capsys)
    assert "step-7 (pipeline p-ended, run 7)" in out and _UNREADABLE not in out, out

    pointer = layout.draft_pointer_path(tmp_project, "broken", "method")
    pointer.parent.mkdir(parents=True)
    pointer.write_text("not a draft pointer", encoding="utf-8")
    out = _dry_run(capsys)
    assert "step-7" not in out and _UNREADABLE in out, out

    pointer.unlink()
    out = _dry_run(capsys)
    assert "step-7 (pipeline p-ended, run 7)" in out, out
    assert daemon.killed == []


def test_a_pipeline_record_that_cannot_be_read_keeps_that_pipelines_containers(
        tmp_project, monkeypatch, capsys):
    """A record wfc cannot read counts its pipeline's runs as running; another pipeline's are listed."""
    daemon = fake_leftover_daemon(monkeypatch)
    for pid, rid in (("p-garbled", "3"), ("p-ended", "7")):
        daemon.start(container_labels(tmp_project, pipeline_id=pid, run_id=rid),
                     name=f"step-{rid}")
    record = layout.pipelines_dir(tmp_project) / "p-garbled" / layout.PIPELINE_RECORD_FILENAME
    record.parent.mkdir(parents=True)
    record.write_text("{ not a record", encoding="utf-8")

    out = _dry_run(capsys)

    assert "step-3" not in out, out
    assert "step-7 (pipeline p-ended, run 7)" in out, out
