"""The canvas server's recovery, and the routes reaching a recorded pipeline.

A pipeline this server does not run in memory -- one a restart cut off, or a
``wfc run-pipeline`` pipeline -- is reached through its pipeline record: the
status route answers for it, the cancel route stops it through Execution's
one cancel operation, and the recovery the lifespan starts closes it out once
it is dead. An id with neither an in-memory job nor a record is still 404.

The run state is produced by the harness's scenario runner (a target stopped
after its claim leaves the claim's own running row, its heartbeat still).
Owners and engines are real processes: a sleeping child for a live one, a
child that was killed for a gone one. The stall window is shortened through
the fake registry. Requests go through the app's test client; ``with`` runs
the lifespan, so the recovery runs as the server runs it.
"""

from __future__ import annotations

import dataclasses
import time
import uuid

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.fixtures.conftest import ended_process as _ended
from tests.fixtures.routes import canvas_client
from tests.harness import Phase, Scenario, drive_target, node, run_scenario, selector, wire
from wfc import layout
from wfc.canvas import state
from wfc.canvas.recovery import PipelineRecovery
from wfc.execution import INTERRUPTED_RUN_MESSAGE, is_pipeline_id, read_pipeline_record
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CANVAS,
    ENTRY_POINT_CLI,
    process_identity,
    record_engine,
    write_pipeline_record,
)
from wfc.persistence import Run, get_session

A_S1 = ("a", "S1", "default")

#: The stall window the lifespan's recovery runs with in these tests (seconds).
_WINDOW = 1.0


def _rows(pid: str) -> list[Run]:
    with get_session() as s:
        return list(s.exec(select(Run).where(Run.pipeline_id == pid).order_by(Run.id)).all())


def _by_target(pid: str) -> dict[tuple[str, str], Run]:
    rows = _rows(pid)
    by_target = {(r.node_id, r.sample): r for r in rows}
    assert len(by_target) == len(rows), "one row per target"
    return by_target


def _cut_off(root, monkeypatch, pid: str, samples=("S1", "S2")):
    """sel -> a -> b over the samples, cut off with S1's a in flight."""
    obs = run_scenario(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")]),
                        node("b", inputs=[wire("a")])],
                 samples=list(samples), pipeline_id=pid),
        root=root, monkeypatch=monkeypatch, interrupted=A_S1, pipeline_end=False)
    assert [(r.node_id, r.sample, r.status) for r in _rows(pid)] == [("a", "S1", "running")]
    return obs


def _wait_for(condition, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


@workflow(purpose="Through the app, a pipeline with a record and no "
                  "in-memory job (its owner gone, its engine alive) is reported "
                  "by the status route and stopped by the cancel route: the "
                  "engine is killed, every expected target has one user row, "
                  "the record is removed, and the status route then reads it "
                  "cancelled from its rows")
def test_a_recorded_pipeline_is_watched_and_stopped_through_the_app(
        tmp_project, monkeypatch, processes):
    pid = "reach-cancel"
    口 = Step(step_num=1, name="A pipeline a restart left running",
             purpose="S1's a in flight; the record names a gone owner and a "
                     "live engine; the server has no job for it")
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    engine = processes()
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    record_engine(root, pid, engine.pid)

    with canvas_client(root, monkeypatch) as client:
        口 = Step(step_num=2, name="The status route reports it",
                 purpose="Its nodes from the frozen document; a running, b "
                         "pending; the record stands for a live thread")
        resp = client.get(f"/api/workflow/status/{pid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["overall_status"], body["thread_alive"]) == ("running", True)
        assert body["node_states"]["a"]["status"] == "running"
        assert body["node_states"]["b"]["status"] == "pending"

        口 = Step(step_num=3, name="The cancel route stops it",
                 purpose="Execution's cancel operation: the engine killed on "
                         "its identity, the owner gone so it closes out with user")
        resp = client.post(f"/api/workflow/cancel/{pid}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "cancelled", "job_id": pid, "noop": False}
        assert engine.wait(timeout=10) is not None, "the engine was killed"

        口 = Step(step_num=4, name="Every target has a row, the record is gone",
                 purpose="One row per expected target, all cancelled for the user")
        rows = _by_target(pid)
        assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
        assert {(r.status, r.cancel_reason) for r in rows.values()} == {("cancelled", "user")}
        assert read_pipeline_record(root, pid) is None

        口 = Step(step_num=5, name="The status route still answers, from the rows",
                 purpose="A tab watching it sees the ending")
        body = client.get(f"/api/workflow/status/{pid}").json()
        assert (body["overall_status"], body["thread_alive"]) == ("cancelled", False)
        assert {ns["status"] for ns in body["node_states"].values()} == {"cancelled"}


@workflow(purpose="The status route answers for a live CLI pipeline between "
                  "steps: its owner alive, a step running and the next pending, "
                  "never settled as completed")
def test_the_status_route_answers_for_a_live_cli_pipeline(tmp_project, monkeypatch,
                                                         processes):
    pid = "reach-cli"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    write_pipeline_record(obs.project.root, pid, ENTRY_POINT_CLI,
                          owner=process_identity(processes().pid))

    with canvas_client(obs.project.root, monkeypatch) as client:
        body = client.get(f"/api/workflow/status/{pid}").json()

    assert (body["overall_status"], body["thread_alive"], body["error"]) == (
        "running", True, None)
    assert {n: ns["status"] for n, ns in body["node_states"].items()} == {
        "a": "running", "b": "pending"}


def test_the_recovery_never_closes_out_a_live_cli_pipeline(tmp_project, monkeypatch,
                                                           processes):
    """A ``wfc run-pipeline`` pipeline whose engine is gone between steps and
    whose running row's heartbeat is still, but whose owner (another live
    process) still runs it, is judged alive look after look: its rows and its
    record are left as they are."""
    pid = "recover-live-cli"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CLI,
                          owner=process_identity(processes().pid))
    record_engine(root, pid, _ended(processes).pid)
    before = [(r.id, r.status, r.finished_at) for r in _rows(pid)]

    recovery = PipelineRecovery(root, owned=lambda: (), window=0.3)
    recovery.baseline()
    for _ in range(3):
        time.sleep(0.45)
        assert recovery.look() == []

    assert recovery.closed == []
    assert [(r.id, r.status, r.finished_at) for r in _rows(pid)] == before
    record = read_pipeline_record(root, pid)
    assert record is not None and not record.cancel_requested


@workflow(purpose="The lifespan's recovery: the server serves at once while a "
                  "dead recorded pipeline waits out the stall window, then "
                  "closes it out; a pipeline in the server's job table and one "
                  "whose record this process owns are never closed out")
def test_the_recovery_closes_out_the_dead_and_never_an_owned_pipeline(
        tmp_project, monkeypatch, processes):
    dead, in_memory, recorded_here = "recover-dead", "recover-in-memory", "recover-mine"
    口 = Step(step_num=1, name="A dead pipeline and two owned ones",
             purpose="The dead one's owner and engine are gone; the in-memory "
                     "one has running rows and no record; the other's record "
                     "names this process. Every heartbeat is still")
    obs = _cut_off(tmp_project, monkeypatch, dead, samples=("S1", "S2", "S3"))
    root = obs.project.root
    write_pipeline_record(root, dead, ENTRY_POINT_CANVAS, owner=_ended(processes))
    record_engine(root, dead, _ended(processes).pid)
    for pid, sample in ((in_memory, "S2"), (recorded_here, "S3")):
        drive_target(dataclasses.replace(obs.project, pipeline_id=pid),
                     ("a", sample, "default"), monkeypatch=monkeypatch,
                     through=Phase.CLAIM)
    write_pipeline_record(root, recorded_here, ENTRY_POINT_CANVAS)
    fakes.short_claim_stall_window(monkeypatch, window=_WINDOW)

    client = canvas_client(root, monkeypatch)
    state._active_jobs[in_memory] = {"thread": None, "pipeline_id": in_memory,
                                     "step_map": {}, "log_dir": "", "error": None,
                                     "proc": None, "cancel_requested": False}
    with client:
        口 = Step(step_num=2, name="A request is served at once",
                 purpose="Startup does not wait: the dead pipeline still reads "
                         "running, its record still there")
        body = client.get(f"/api/workflow/status/{dead}").json()
        assert body["node_states"]["a"]["status"] == "running"
        assert read_pipeline_record(root, dead) is not None

        口 = Step(step_num=3, name="After the window it is closed out",
                 purpose="Its running row failed as interrupted, every target "
                         "has a row, its record is gone")
        assert _wait_for(lambda: read_pipeline_record(root, dead) is None)
        rows = _by_target(dead)
        assert len(rows) == 6
        assert (rows["a", "S1"].status, rows["a", "S1"].error_message) == (
            "failed", INTERRUPTED_RUN_MESSAGE)

        口 = Step(step_num=4, name="The owned pipelines are left alone",
                 purpose="Two more windows pass; their running rows and the "
                         "record stay")
        time.sleep(2 * _WINDOW + 0.5)
        for pid in (in_memory, recorded_here):
            assert [r.status for r in _rows(pid)] == ["running"], pid
        assert read_pipeline_record(root, recorded_here) is not None


@pytest.mark.parametrize("kill", ["works", "denied"])
@workflow(purpose="The cancel route stops a recorded pipeline it has never read "
                  "the status of: 200, every expected target cancelled for the "
                  "user, the record gone. A kill that meets a psutil error other "
                  "than a vanished process is contained: the engine reads not "
                  "stopped (noop) and the rows still end cancelled")
def test_the_cancel_route_stops_a_pipeline_first_reached_by_the_cancel(
        tmp_project, monkeypatch, processes, kill):
    pid = f"reach-cancel-first-{kill}"
    口 = Step(step_num=1, name="A pipeline a restart left running",
             purpose="Its owner gone, its engine alive; no status read yet")
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    engine = processes()
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    record_engine(root, pid, engine.pid)
    if kill == "denied":
        fakes.deny_process_kill(monkeypatch)

    with canvas_client(root, monkeypatch) as client:
        口 = Step(step_num=2, name="The cancel route is the first request",
                 purpose="It reaches the pipeline through its record")
        resp = client.post(f"/api/workflow/cancel/{pid}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["noop"] is (kill == "denied")

        口 = Step(step_num=3, name="Every target ends cancelled for the user",
                 purpose="The kill's error never stops the row flip and the close-out")
        rows = _by_target(pid)
        assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
        assert {(r.status, r.cancel_reason) for r in rows.values()} == {("cancelled", "user")}
        assert read_pipeline_record(root, pid) is None
        body = client.get(f"/api/workflow/status/{pid}").json()
        assert body["overall_status"] == "cancelled"
    if kill == "works":
        assert engine.wait(timeout=10) is not None, "the engine was killed"
    else:
        assert engine.poll() is None, "a denied kill leaves the engine running"


@workflow(purpose="One look of the recovery judges every candidate even when one "
                  "cannot be judged: a pipeline whose record file is unreadable "
                  "is skipped and kept, and a dead neighbour is still closed out")
def test_one_unreadable_record_never_stops_the_recovery_s_look(
        tmp_project, monkeypatch, processes):
    bad, dead = "look-a-unreadable", "look-b-dead"
    口 = Step(step_num=1, name="A dead pipeline, and one whose record is not a record",
             purpose="The unreadable one sorts first, with a running row")
    obs = _cut_off(tmp_project, monkeypatch, dead)
    root = obs.project.root
    write_pipeline_record(root, dead, ENTRY_POINT_CLI, owner=_ended(processes))
    drive_target(dataclasses.replace(obs.project, pipeline_id=bad),
                 ("a", "S2", "default"), monkeypatch=monkeypatch, through=Phase.CLAIM)
    bad_record = layout.pipeline_record_path(root, bad)
    bad_record.parent.mkdir(parents=True, exist_ok=True)
    bad_record.write_text("not a record", encoding="utf-8")

    口 = Step(step_num=2, name="One look with no stall window",
             purpose="Every candidate past its window at the first look")
    recovery = PipelineRecovery(root, owned=lambda: (), window=0.0)
    recovery.baseline()
    closed = recovery.look()

    口 = Step(step_num=3, name="The dead one is closed out, the other kept",
             purpose="The unreadable one is judged again at the next look")
    assert closed == [dead]
    assert read_pipeline_record(root, dead) is None
    assert [r.status for r in _rows(bad)] == ["running"]


def test_an_id_that_is_not_a_pipeline_id_never_forms_a_record_path(
        tmp_project, monkeypatch, processes):
    pid = "reach-valid"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CLI,
                          owner=process_identity(processes().pid))
    # The same record under a directory whose name is not a pipeline id.
    hidden = layout.pipeline_record_path(root, ".hidden")
    hidden.parent.mkdir(parents=True)
    hidden.write_text(layout.pipeline_record_path(root, pid).read_text(encoding="utf-8"),
                      encoding="utf-8")

    with canvas_client(root, monkeypatch) as client:
        assert client.get(f"/api/workflow/status/{pid}").status_code == 200
        assert client.get("/api/workflow/status/.hidden").status_code == 404
        assert client.post("/api/workflow/cancel/.hidden").status_code == 404
    assert read_pipeline_record(root, ".hidden") is None
    assert [is_pipeline_id(text) for text in (
        str(uuid.uuid4()), "run-2", "a.b_c", "..", ".hidden", "a/b", "a\\b", "")] == [
        True, True, True, False, False, False, False, False]


def test_an_id_with_no_job_and_no_record_is_unknown(tmp_project, monkeypatch):
    unknown = str(uuid.uuid4())
    with canvas_client(tmp_project, monkeypatch) as client:
        assert client.get(f"/api/workflow/status/{unknown}").status_code == 404
        assert client.post(f"/api/workflow/cancel/{unknown}").status_code == 404
