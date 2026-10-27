"""Dead or alive, the close-out of a cut-off pipeline, and the cancel through a record.

A pipeline is alive while its record's owner or engine is that same live
process, or while a running row's heartbeat moves; otherwise it is dead once
one stall window has passed. A dead pipeline is closed out: its running rows
fail as interrupted, every other target gets a row, Storage releases what it
held, and its record goes. The one cancel operation sets the record's flag,
kills the engine only on an identity match, and walks only when the owner is
gone.

The run state is produced by the harness's scenario runner (a target stopped
after its claim leaves the claim's own running row). Owners and engines are
real processes: a sleeping child for a live one, a child that was killed for
a gone one. The stall window is shortened through the fake registry.
"""

from __future__ import annotations

import time

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.fixtures.conftest import ended_process as _ended
from tests.fixtures.conftest import mocked_snakemake, wait_until_gone
from tests.harness import Scenario, build_project, node, run_scenario, selector, wire
from wfc import layout
from wfc.execution import (
    INTERRUPTED_RUN_MESSAGE,
    PIPELINE_FAILED,
    HeartbeatSampler,
    ProcessState,
    UnknownPipelineError,
    Verdict,
    assess_pipeline,
    cancel_recorded_pipeline,
    close_out_pipeline,
    judge_liveness,
    kill_process_tree,
    pipeline_status,
    probe_process,
    read_pipeline_record,
)
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CANVAS,
    ENTRY_POINT_CLI,
    ProcessIdentity,
    process_identity,
    record_engine,
    write_pipeline_record,
)
from wfc.persistence import InUseRecord, Run, get_session

A, G, R, U = (ProcessState.ALIVE, ProcessState.GONE, ProcessState.REUSED,
              ProcessState.UNKNOWN)
A_S1 = ("a", "S1", "default")

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731

#: The stall window the tests run with (seconds), and a wait past it.
_WINDOW = 0.3
_PAST_WINDOW = 0.45


def _rows(pid: str) -> list[Run]:
    """Every row of the pipeline, by id."""
    with get_session() as s:
        return list(s.exec(select(Run).where(Run.pipeline_id == pid).order_by(Run.id)).all())


def _by_target(pid: str) -> dict[tuple[str, str], Run]:
    rows = _rows(pid)
    by_target = {(r.node_id, r.sample): r for r in rows}
    assert len(by_target) == len(rows), "one row per target"
    return by_target


def _records() -> list[tuple[str, str | None, int | None]]:
    with get_session() as s:
        return sorted((r.key, r.pipeline_id, r.run_id)
                      for r in s.exec(select(InUseRecord)).all())


def _chain(pid: str, samples=("S1", "S2")) -> Scenario:
    """The chain sel -> a -> b over the samples."""
    return Scenario(nodes=[selector(), node("a", inputs=[wire("sel")]),
                           node("b", inputs=[wire("a")])],
                    samples=list(samples), pipeline_id=pid)


def _cut_off(root, monkeypatch, pid: str, samples=("S1", "S2")):
    """A pipeline cut off with S1's a in flight: its claim's running row, nothing after."""
    obs = run_scenario(_chain(pid, samples), root=root, monkeypatch=monkeypatch,
                       interrupted=A_S1, pipeline_end=False)
    assert [(r.node_id, r.sample, r.status) for r in _rows(pid)] == [("a", "S1", "running")]
    return obs


# --------------------------------------------------------------------------
# The kernel and the probe
# --------------------------------------------------------------------------

#: (owner, engine, heartbeat moved, running rows, window passed) -> verdict.
_TABLE = [
    # Nothing alive: dead once the window passed, undecided before.
    (G, G, False, True, True, Verdict.DEAD),
    (G, G, False, True, False, Verdict.UNDECIDED),
    (G, G, False, False, True, Verdict.DEAD),
    (G, G, False, False, False, Verdict.UNDECIDED),
    # The owner alive keeps it alive: before the window, after it, with no row.
    (A, G, False, True, True, Verdict.ALIVE),
    (A, G, False, False, True, Verdict.ALIVE),
    (A, U, False, False, False, Verdict.ALIVE),
    # The engine alive keeps it alive.
    (G, A, False, True, True, Verdict.ALIVE),
    (G, A, False, False, True, Verdict.ALIVE),
    (U, A, False, False, True, Verdict.ALIVE),
    # A reused pid is another process: gone, on either side.
    (R, G, False, True, True, Verdict.DEAD),
    (G, R, False, True, True, Verdict.DEAD),
    (R, R, False, False, True, Verdict.DEAD),
    (R, A, False, False, True, Verdict.ALIVE),
    # No record: heartbeats alone.
    (U, U, True, True, True, Verdict.ALIVE),
    (U, U, False, True, True, Verdict.DEAD),
    (U, U, False, True, False, Verdict.UNDECIDED),
    # A moving heartbeat keeps it alive, but only on a running row.
    (G, G, True, True, True, Verdict.ALIVE),
    (G, G, True, True, False, Verdict.ALIVE),
    (G, G, True, False, True, Verdict.DEAD),
]


@pytest.mark.parametrize("owner,engine,moved,running,window,expected", _TABLE)
def test_the_dead_or_alive_rule_follows_the_table(owner, engine, moved, running,
                                                  window, expected):
    assert judge_liveness(owner, engine, heartbeat_moved=moved,
                          has_running_rows=running, window_passed=window) is expected


def test_the_probe_tells_a_live_process_from_a_gone_and_a_reused_one(processes):
    live = processes()
    identity = process_identity(live.pid)
    assert probe_process(identity) is ProcessState.ALIVE
    assert probe_process(ProcessIdentity(live.pid, identity.created - 1000)) is ProcessState.REUSED
    assert probe_process(ProcessIdentity(live.pid, None)) is ProcessState.GONE
    assert probe_process(_ended(processes)) is ProcessState.GONE
    assert probe_process(None) is ProcessState.UNKNOWN


def test_a_process_tree_is_killed_only_when_its_identity_matches(processes):
    live = processes()
    identity = process_identity(live.pid)

    assert kill_process_tree(ProcessIdentity(live.pid, identity.created - 1000)) is False
    assert live.poll() is None, "a reused pid is never killed"
    assert kill_process_tree(None) is False

    assert kill_process_tree(identity) is True
    assert live.wait(timeout=10) is not None
    wait_until_gone(live.pid)
    assert kill_process_tree(identity) is False


# --------------------------------------------------------------------------
# Dead or alive over a real pipeline
# --------------------------------------------------------------------------

@workflow(purpose="A pipeline cut off with its owner and engine gone is undecided "
                  "until one stall window has passed with its heartbeats still, "
                  "then dead")
def test_a_cut_off_pipeline_is_dead_once_the_window_passes(tmp_project, monkeypatch,
                                                           processes):
    fakes.short_claim_stall_window(monkeypatch, window=_WINDOW)
    pid = "recover-still"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    write_pipeline_record(obs.project.root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    record_engine(obs.project.root, pid, _ended(processes).pid)

    sampler = HeartbeatSampler()
    assert assess_pipeline(obs.project.root, pid, sampler) is Verdict.UNDECIDED
    time.sleep(_PAST_WINDOW)
    assert assess_pipeline(obs.project.root, pid, sampler) is Verdict.DEAD


@pytest.mark.parametrize("alive_by", ["heartbeat", "engine", "owner"])
@workflow(purpose="A pipeline is never judged dead while its running row's "
                  "heartbeat moves, while its engine is alive with no running row, "
                  "or while its owner is alive with the engine gone (its end steps "
                  "running); its rows are left as they are")
def test_a_pipeline_something_still_runs_is_alive(tmp_project, monkeypatch, processes,
                                                  alive_by):
    from wfc.execution.claim import _start_heartbeat, stop_heartbeat

    fakes.short_claim_stall_window(monkeypatch, interval=0.05, window=_WINDOW)
    pid = f"recover-alive-{alive_by}"
    if alive_by == "heartbeat":
        obs = _cut_off(tmp_project, monkeypatch, pid)
    else:
        # Every target ran: no running row is left, only the processes.
        obs = run_scenario(_chain(pid), root=tmp_project, monkeypatch=monkeypatch,
                           pipeline_end=False)
        assert {r.status for r in _rows(pid)} == {"completed"}
    root = obs.project.root
    owner = process_identity(processes().pid) if alive_by == "owner" else _ended(processes)
    write_pipeline_record(root, pid, ENTRY_POINT_CLI, owner=owner)
    record_engine(root, pid, processes().pid if alive_by == "engine" else _ended(processes).pid)
    before = [(r.id, r.status) for r in _rows(pid)]

    running = [r.id for r in _rows(pid) if r.status == "running"]
    for run_id in running:
        _start_heartbeat(run_id)
    try:
        sampler = HeartbeatSampler()
        assess_pipeline(root, pid, sampler)
        time.sleep(_PAST_WINDOW)
        assert assess_pipeline(root, pid, sampler) is Verdict.ALIVE
    finally:
        for run_id in running:
            stop_heartbeat(run_id)
    assert [(r.id, r.status) for r in _rows(pid)] == before
    assert read_pipeline_record(root, pid) is not None


# --------------------------------------------------------------------------
# The close-out
# --------------------------------------------------------------------------

@workflow(purpose="A pipeline cut off by a restart is closed out: its "
                  "running row fails as interrupted with no traceback, the run "
                  "below it is "
                  "cancelled upstream_failed naming it, every other target it "
                  "never started is cancelled interrupted, one row per expected "
                  "target, the pipeline reads failed and its record is gone; a "
                  "second close-out writes nothing")
def test_a_cut_off_pipeline_is_closed_out(tmp_project, monkeypatch, processes):
    pid = "recover-close-out"
    口 = Step(step_num=1, name="Cut a pipeline off with S1's a in flight",
             purpose="The claim's own running row, its heartbeat still; the "
                     "record names an owner and an engine that are gone")
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    record_engine(root, pid, _ended(processes).pid)
    a_s1 = obs.runs[A_S1].run_id

    口 = Step(step_num=2, name="Close it out",
             purpose="What the recovery does to a pipeline it judged dead")
    done = close_out_pipeline(root, pid)
    assert done.runs_failed == [a_s1] and done.rows_written == 3
    assert done.record_removed and not done.user_cancelled

    口 = Step(step_num=3, name="Every expected target has one row that says why",
             purpose="failed interrupted; upstream_failed naming it below; "
                     "interrupted for the rest")
    rows = _by_target(pid)
    assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
    assert (rows["a", "S1"].status, rows["a", "S1"].error_message) == (
        "failed", INTERRUPTED_RUN_MESSAGE)
    assert rows["a", "S1"].error_traceback is None, (
        "an interrupted step raised nothing: it has no traceback to show")
    assert rows["a", "S1"].finished_at is not None
    assert (rows["b", "S1"].status, rows["b", "S1"].cancel_reason,
            rows["b", "S1"].cancelled_due_to_run_id) == ("cancelled", "upstream_failed", a_s1)
    for target in (("a", "S2"), ("b", "S2")):
        assert (rows[target].status, rows[target].cancel_reason,
                rows[target].cancelled_due_to_run_id) == ("cancelled", "interrupted", None)
    walked = {r.pipeline_doc_hash for t, r in rows.items() if t != ("a", "S1")}
    assert walked == {rows["a", "S1"].pipeline_doc_hash}, (
        "the walk's rows carry the launch's document hash")
    assert pipeline_status([(r.status, r.cancel_reason) for r in rows.values()]) == PIPELINE_FAILED
    assert read_pipeline_record(root, pid) is None

    口 = Step(step_num=4, name="Close it out again",
             purpose="Idempotent: nothing failed, nothing written")
    again = close_out_pipeline(root, pid)
    assert (again.runs_failed, again.rows_written, again.record_removed) == ([], 0, False)
    assert len(_rows(pid)) == 4


def test_a_close_out_takes_the_user_s_reason_from_the_record_s_cancel_flag(
        tmp_project, monkeypatch, processes):
    """A cancel flag set on the record (a `wfc cancel` the owner never acted
    on) makes the close-out's walk say user, though its caller did not."""
    from wfc.execution.pipeline_record import request_cancel

    pid = "recover-close-out-flag"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    assert request_cancel(root, pid).cancel_requested
    a_s1 = obs.runs[A_S1].run_id

    done = close_out_pipeline(root, pid, user_cancelled=False)

    assert done.user_cancelled and done.runs_failed == [a_s1] and done.rows_written == 3
    walked = {t: (r.status, r.cancel_reason) for t, r in _by_target(pid).items()
              if t != ("a", "S1")}
    assert walked == {t: ("cancelled", "user")
                      for t in (("a", "S2"), ("b", "S1"), ("b", "S2"))}
    assert read_pipeline_record(root, pid) is None


@workflow(purpose="Closing out a pipeline releases the sample view only it "
                  "held, its in-use records and the record bound to its killed "
                  "claim; a live pipeline holding one of the same samples and "
                  "another keeps its views and records")
def test_a_close_out_releases_what_only_the_dead_pipeline_held(tmp_project, monkeypatch,
                                                              processes):
    from wfc.storage import restore_sample

    pid, live = "recover-release", "live-pipeline"
    obs = _cut_off(tmp_project, monkeypatch, pid, samples=("S1", "S2", "S3"))
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    for name in ("S1", "S3"):
        restore_sample(name, project_root=root, pipeline_id=pid)
    for name in ("S1", "S2"):
        restore_sample(name, project_root=root, pipeline_id=live)
    a_s1 = obs.runs[A_S1].run_id
    assert any(run_id == a_s1 for _, _, run_id in _records()), (
        "the premise: the killed claim's record is bound to its run")

    close_out_pipeline(root, pid)

    assert not layout.sample_dir(root, "S3").exists(), "only the dead pipeline held S3"
    assert layout.sample_dir(root, "S1").exists(), "the live pipeline still holds S1"
    assert layout.sample_dir(root, "S2").exists()
    assert _records() == [("S1", live, None), ("S2", live, None)]


# --------------------------------------------------------------------------
# The cancel operation
# --------------------------------------------------------------------------

@workflow(purpose="Cancelling a pipeline whose owner is alive (a run_pipeline in "
                  "this process, its engine a real process) sets the flag and "
                  "kills the engine but writes no row; the owner's own end steps "
                  "then walk once with user, and the record goes with them")
def test_a_cancel_with_the_owner_alive_leaves_the_walk_to_the_owner(git_project,
                                                                  monkeypatch, processes):
    from wfc.execution import run_pipeline

    pid = "recover-cancel-owner-alive"
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)
    engine = processes()
    seen = {}

    def cancel_while_the_engine_runs():
        seen["outcome"] = cancel_recorded_pipeline(project.root, pid)
        seen["rows"] = _rows(pid)
        seen["record"] = read_pipeline_record(project.root, pid)

    gen_patch, popen_patch = mocked_snakemake(1)
    proc = popen_patch.kwargs["return_value"]
    proc.pid = engine.pid
    proc.wait.side_effect = lambda *a, **k: (cancel_while_the_engine_runs(), 1)[1]
    fakes.stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch, pytest.raises(RuntimeError):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root), wfc_root=str(project.root),
                     pipeline_id=pid)

    outcome = seen["outcome"]
    assert outcome.owner_alive and outcome.engine_killed and outcome.close_out is None
    assert engine.wait(timeout=10) is not None, "the engine was killed"
    assert seen["rows"] == [], "the canceller does not walk while the owner is alive"
    assert seen["record"].cancel_requested
    rows = _by_target(pid)
    assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
    assert {(r.status, r.cancel_reason) for r in rows.values()} == {("cancelled", "user")}
    assert read_pipeline_record(project.root, pid) is None


@workflow(purpose="Cancelling a pipeline whose owner is gone kills its engine, "
                  "cancels its running row for the user, then walks with user, "
                  "releases and removes the record itself; a second cancel is "
                  "refused and writes nothing")
def test_a_cancel_with_the_owner_gone_closes_the_pipeline_out(tmp_project, monkeypatch,
                                                             processes):
    pid = "recover-cancel-owner-gone"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    engine = processes()
    write_pipeline_record(root, pid, ENTRY_POINT_CLI, owner=_ended(processes))
    record_engine(root, pid, engine.pid)

    outcome = cancel_recorded_pipeline(root, pid)

    assert outcome.engine_killed and not outcome.owner_alive
    assert outcome.runs_cancelled == 1
    assert outcome.close_out is not None and outcome.close_out.rows_written == 3
    assert engine.wait(timeout=10) is not None
    rows = _by_target(pid)
    assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
    assert {(r.status, r.cancel_reason) for r in rows.values()} == {("cancelled", "user")}
    assert read_pipeline_record(root, pid) is None
    assert _records() == [], "the pipeline's records and its claim's are released"

    with pytest.raises(UnknownPipelineError):
        cancel_recorded_pipeline(root, pid)
    assert len(_rows(pid)) == 4
