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
    on, or one still waiting for the close-out lock) makes the close-out end
    the pipeline for the user, though its caller did not ask: the running row
    is cancelled for the user, not failed as interrupted, and the walk says
    user."""
    from wfc.execution.pipeline_record import request_cancel

    pid = "recover-close-out-flag"
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CANVAS, owner=_ended(processes))
    assert request_cancel(root, pid).cancel_requested

    done = close_out_pipeline(root, pid, user_cancelled=False)

    assert done.user_cancelled and done.rows_written == 3
    assert (done.runs_failed, done.runs_cancelled) == ([], 1)
    assert {t: (r.status, r.cancel_reason) for t, r in _by_target(pid).items()} == {
        t: ("cancelled", "user")
        for t in (("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2"))}
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
                     project_root=str(project.root),
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
    late = close_out_pipeline(root, pid)
    assert (late.closed, late.rows_written, late.record_removed) == (False, 0, False), (
        "a close-out after the cancel closed it finds nothing to close")
    assert len(_rows(pid)) == 4


# --------------------------------------------------------------------------
# Two processes, one pipeline: the close-out lock file
# --------------------------------------------------------------------------

#: One closer in its own process: ``wfc cancel <pid> --yes`` through the CLI,
#: or the recovery's close-out. It counts the release of in-use records and
#: the record removals it makes, writes ``ready`` just before it starts, and
#: writes what it did as JSON.
_CLOSER = """
import json, sys
from pathlib import Path
role, root, pid, out, ready = sys.argv[1:6]
from wfc.execution import recovery
counts = {"releases": 0, "removals": 0}
real_release, real_remove = recovery.release_finished_runs, recovery.remove_pipeline_record
def release(*a, **k):
    counts["releases"] += 1
    return real_release(*a, **k)
def remove(*a, **k):
    gone = real_remove(*a, **k)
    counts["removals"] += int(gone)
    return gone
recovery.release_finished_runs, recovery.remove_pipeline_record = release, remove
Path(ready).write_text("ready", encoding="utf-8")
if role == "cancel":
    from wfc.cli import cli_main
    result = {"code": cli_main(["cancel", pid, "--yes"])}
else:
    done = recovery.close_out_pipeline(root, pid)
    result = {"closed": done.closed, "rows_written": done.rows_written}
result.update(counts)
Path(out).write_text(json.dumps(result), encoding="utf-8")
"""


def _start_closer(role: str, root, pid: str, scratch):
    """Start one closer process; returns (process, its result file, its ready file)."""
    import subprocess
    import sys

    out, ready = scratch / f"{role}.json", scratch / f"{role}.ready"
    proc = subprocess.Popen([sys.executable, "-c", _CLOSER, role, str(root), pid,
                             str(out), str(ready)], cwd=str(root),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, out, ready


def _finish(proc, out) -> tuple[dict, str]:
    """Wait for a closer; returns what it did and its stdout."""
    import json

    stdout, stderr = proc.communicate(timeout=120)
    assert proc.returncode == 0, f"the closer failed:\n{stdout}\n{stderr}"
    return json.loads(out.read_text(encoding="utf-8")), stdout


def _wait_for(condition, what: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def _dead_cli_pipeline(tmp_project, monkeypatch, processes, pid: str):
    """A `wfc run-pipeline` pipeline cut off with S1's a in flight, its owner and engine gone."""
    obs = _cut_off(tmp_project, monkeypatch, pid)
    root = obs.project.root
    write_pipeline_record(root, pid, ENTRY_POINT_CLI, owner=_ended(processes))
    record_engine(root, pid, _ended(processes).pid)
    return root


def _flagged(root, pid: str) -> bool:
    record = read_pipeline_record(root, pid)
    return bool(record is not None and record.cancel_requested)


@workflow(purpose="`wfc cancel` and the recovery's close-out of one dead "
                  "`wfc run-pipeline` pipeline, started at once in two processes, "
                  "close it once: one release of the in-use records, one record "
                  "removal, one row per target, and every row says the user "
                  "cancelled it, whichever process takes the lock first")
def test_a_cancel_and_a_close_out_in_two_processes_close_the_pipeline_once(
        tmp_project, monkeypatch, processes, tmp_path_factory):
    from wfc.execution.recovery import close_out_lock

    pid = "recover-two-closers"
    scratch = tmp_path_factory.mktemp("closers")
    口 = Step(step_num=1, name="Cut a `wfc run-pipeline` pipeline off",
             purpose="Its running row left behind, its owner and engine gone")
    root = _dead_cli_pipeline(tmp_project, monkeypatch, processes, pid)

    口 = Step(step_num=2, name="Start both closers while the lock is held",
             purpose="The test holds the close-out lock until the cancel has set "
                     "its flag and the close-out is about to ask for the lock, "
                     "so both wait on it together; then lets go")
    with close_out_lock(root, pid):
        cancel, cancel_out, _ = _start_closer("cancel", root, pid, scratch)
        close, close_out, close_ready = _start_closer("close-out", root, pid, scratch)
        _wait_for(lambda: _flagged(root, pid), "the cancel's flag")
        _wait_for(close_ready.exists, "the close-out to start")

    口 = Step(step_num=3, name="Both finish; the pipeline was closed once",
             purpose="One release, one record removal; the one that came second "
                     "found nothing left to close")
    cancelled, _ = _finish(cancel, cancel_out)
    closed, _ = _finish(close, close_out)
    assert cancelled["code"] == 0
    assert cancelled["releases"] + closed["releases"] == 1, (cancelled, closed)
    assert cancelled["removals"] + closed["removals"] == 1, (cancelled, closed)

    口 = Step(step_num=4, name="Every row says the user cancelled it",
             purpose="The running row and the three targets the walk wrote")
    rows = _by_target(pid)
    assert set(rows) == {("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")}
    assert {(r.status, r.cancel_reason) for r in rows.values()} == {("cancelled", "user")}
    assert read_pipeline_record(root, pid) is None
    assert _records() == []


def test_a_cancel_that_waits_on_a_close_out_gets_its_reason_and_says_it_ended(
        tmp_project, monkeypatch, processes, tmp_path_factory):
    """The close-out takes the lock first: it reads the flag the waiting cancel
    set, so it ends the pipeline for the user; the cancel then finds it ended
    and says so, releasing and removing nothing."""
    from wfc.execution.recovery import close_out_lock

    pid = "recover-close-out-first"
    scratch = tmp_path_factory.mktemp("closers")
    root = _dead_cli_pipeline(tmp_project, monkeypatch, processes, pid)

    with close_out_lock(root, pid):
        cancel, cancel_out, _ = _start_closer("cancel", root, pid, scratch)
        _wait_for(lambda: _flagged(root, pid), "the cancel's flag")
        done = close_out_pipeline(root, pid)

    assert done.user_cancelled and done.runs_cancelled == 1 and done.rows_written == 3
    cancelled, stdout = _finish(cancel, cancel_out)
    assert (cancelled["code"], cancelled["releases"], cancelled["removals"]) == (0, 0, 0)
    assert f"Pipeline {pid} ended before the cancel reached it" in stdout
    assert {(r.status, r.cancel_reason) for r in _by_target(pid).values()} == {
        ("cancelled", "user")}


def test_a_lock_file_left_by_a_gone_process_is_taken_over(tmp_path, processes):
    import json
    import os

    from wfc.execution.pipeline_record import hold_lock_file

    lock = tmp_path / "close-out.lock"
    lock.write_text(json.dumps(_ended(processes).to_json()), encoding="utf-8")

    with hold_lock_file(lock):
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert not lock.exists()


def test_a_release_refused_while_a_waiter_reads_the_lock_still_removes_it(
        tmp_path, monkeypatch):
    """Windows refuses to remove a file a waiter has open (WinError 32); the
    release retries, so the lock never stays behind naming a holder that left."""
    from wfc.execution.pipeline_record import hold_lock_file

    # The refusal is Windows's, so the run is too: elsewhere a refused
    # removal is a real denial and is raised at once.
    monkeypatch.setattr("wfc.storage.replace.HOLDS_REFUSE", True)
    lock = tmp_path / "close-out.lock"
    refused = fakes.refuse_path_unlink_once(monkeypatch, lock)
    with hold_lock_file(lock):
        pass
    assert refused == [lock]
    assert not lock.exists()


def test_a_stale_lock_is_removed_by_one_waiter_and_never_once_retaken(
        tmp_path, processes):
    """Two waiters can both read a dead holder; only one at a time decides and
    removes, deciding again first, so a lock retaken in between is left."""
    import json
    import os

    from wfc.execution.pipeline_record import _take_over_stale_lock, _this_process

    lock = tmp_path / "close-out.lock"
    guard = tmp_path / "close-out.lock.takeover"
    dead = json.dumps(_ended(processes).to_json())

    # A second waiter arriving after the first retook the lock: it is left.
    live = json.dumps(_this_process().to_json())
    lock.write_text(live, encoding="utf-8")
    assert _take_over_stale_lock(lock) is True
    assert lock.read_text(encoding="utf-8") == live and not guard.exists()

    # Another waiter is deciding right now: this one leaves the lock to it.
    lock.write_text(dead, encoding="utf-8")
    guard.write_text("", encoding="utf-8")
    assert _take_over_stale_lock(lock) is False
    assert lock.exists()

    # The one deciding waiter removes the dead holder's lock, then its guard.
    os.remove(guard)
    assert _take_over_stale_lock(lock) is True
    assert not lock.exists() and not guard.exists()


def test_a_waiter_behind_another_waiters_takeover_sleeps_until_it_leaves(
        tmp_path, processes, monkeypatch):
    """A waiter behind another waiter's takeover polls instead of spinning.

    A dead holder's lock and another waiter inside its takeover: this waiter
    sleeps a poll between tries, and enters once that waiter's takeover file
    is gone.
    """
    import json
    import threading

    from wfc.execution.pipeline_record import _LOCK_POLL_SECONDS, hold_lock_file

    lock = tmp_path / "close-out.lock"
    guard = tmp_path / "close-out.lock.takeover"
    lock.write_text(json.dumps(_ended(processes).to_json()), encoding="utf-8")
    guard.write_text("", encoding="utf-8")
    polls = fakes.takeover_left_after_polls(monkeypatch, guard, after=3)

    entered = threading.Event()

    def wait_for_the_lock() -> None:
        with hold_lock_file(lock):
            entered.set()

    waiter = threading.Thread(target=wait_for_the_lock, daemon=True)
    waiter.start()
    try:
        assert entered.wait(timeout=5), \
            "the waiter never entered: it spun without polling, so the takeover stayed"
    finally:
        guard.unlink(missing_ok=True)
        waiter.join(timeout=60)
    assert polls[:3] == [_LOCK_POLL_SECONDS] * 3
    assert not lock.exists() and not guard.exists()


def test_a_stale_takeover_is_claimed_before_removal_and_a_fresh_one_is_kept(
        tmp_path, monkeypatch):
    """A stale takeover file is claimed by rename before it is removed.

    A takeover file left by a waiter that stopped inside it is removed by the
    waiter that claims it; when the file it claimed turns out fresh (another
    waiter's, made after it judged the old one stale), it is put back.
    """
    import json
    import os

    from wfc.execution.pipeline_record import (
        _UNNAMED_LOCK_STALE_SECONDS,
        _take_over_stale_lock,
        _this_process,
    )

    lock = tmp_path / "close-out.lock"
    guard = tmp_path / "close-out.lock.takeover"
    lock.write_text(json.dumps(_this_process().to_json()), encoding="utf-8")
    long_ago = time.time() - 2 * _UNNAMED_LOCK_STALE_SECONDS

    guard.write_text("stale", encoding="utf-8")
    os.utime(guard, (long_ago, long_ago))
    assert _take_over_stale_lock(lock) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["close-out.lock"]

    guard.write_text("stale", encoding="utf-8")
    os.utime(guard, (long_ago, long_ago))
    claims = fakes.takeover_renewed_before_claim(monkeypatch, guard, fresh="fresh")
    assert _take_over_stale_lock(lock) is False
    assert claims == [guard]
    assert guard.read_text(encoding="utf-8") == "fresh"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "close-out.lock", "close-out.lock.takeover"]


def test_a_close_out_whose_run_dir_another_holder_removed_makes_it_again(
        tmp_project, monkeypatch):
    """A holder in another process removes the run dir it made when it leaves;
    a waiter whose lock file's folder went that way makes it again."""
    from wfc.execution import recovery

    root, pid = tmp_project, "pipe-gone-dir"
    run_dir = layout.pipeline_run_dir(root, pid)
    lock = layout.pipeline_close_out_lock_path(root, pid)
    holds = fakes.run_dir_removed_at_first_lock(monkeypatch, run_dir)
    entered = []
    with recovery.close_out_lock(root, pid):
        entered.append(run_dir.is_dir())
    assert holds == [lock, lock] and entered == [True]
    assert not run_dir.exists()
