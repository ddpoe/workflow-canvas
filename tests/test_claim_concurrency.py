"""One run per cache key computes at a time, and a claim's rows land together.

Two processes claim one method, sample and cache key on one SQLite
database: one run computes, the other waits and becomes its cache hit, or
runs after it failed. A claim left running by a killed process is closed as
failed once its heartbeat stays still for the stall window, and the waiting
claim runs; the closed claim's own record phase, should it still run, leaves
the run failed. A failure after the first row of the claim, on the miss and
the hit path, leaves no row.

The method and sample are built by production (``completed_run``); every
claim is production ``pre_run`` in a child process holding its claim the way
``run_step`` does. Test-side seams in the child: the heartbeat interval and
stall window are set short (``fakes.short_claim_stall_window``);
``wait_on_running_claim`` is wrapped to drop a file signal before it
delegates, so the winner holds until its peer is waiting; and, for the
failure after the claim's first row, ``fakes.fault_after_claim_input_rows``,
which the test names and the child installs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures import fakes
from tests.fixtures.routes import completed_run

REPO = Path(__file__).resolve().parent.parent
CHILD_TIMEOUT = 240

CHILD = r'''
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
mode, name, signals = sys.argv[2], sys.argv[3], Path(sys.argv[4])
module, method, sample = sys.argv[5], sys.argv[6], sys.argv[7]
installs = [n for n in sys.argv[8].split(",") if n]

import pytest
import wfc.execution.claim as claim
from tests.fixtures import fakes
from wfc.execution.record import complete_run, run_record

mp = pytest.MonkeyPatch()
fakes.short_claim_stall_window(mp, interval=0.05, window=1.0)

def signal(n):
    (signals / n).write_text("")

def wait_for(n, limit=120):
    deadline = time.monotonic() + limit
    while not (signals / n).exists():
        if time.monotonic() > deadline:
            raise SystemExit(f"{name}: no {n} within {limit} s")
        time.sleep(0.02)

def claim_it(leg):
    return claim.pre_run(method_name=method, module_name=module, sample=sample,
                         params={"leg": leg}, selector_slot="data",
                         git_commit="c" * 40, hold_claim=True)

waited = []
real_wait = claim.wait_on_running_claim
def signalling_wait(run_id, label):
    waited.append(run_id)
    signal("waiting")
    return real_wait(run_id, label)
claim.wait_on_running_claim = signalling_wait

if mode in ("completed", "failed"):
    signal(f"{name}-ready")
    wait_for("A-ready")
    wait_for("B-ready")
    flag, run_id = claim_it(mode)
    if flag == "NEW" and not waited:
        # The winner holds, its heartbeat advancing, for three stall
        # windows after its peer started waiting, then ends.
        wait_for("waiting")
        time.sleep(claim.STALL_WINDOW_SECONDS * 3)
        claim.stop_heartbeat(run_id)
        complete_run(run_id, status=mode,
                     error_message=None if mode == "completed" else "injected failure")
    elif flag == "NEW":
        claim.stop_heartbeat(run_id)
        complete_run(run_id)
    print("RESULT", flag, run_id, waited, flush=True)
elif mode == "killed":
    flag, run_id = claim_it("stall")
    time.sleep(0.3)
    print("RESULT", flag, run_id, waited, flush=True)
    os._exit(0)
elif mode == "taker":
    flag, run_id = claim_it("stall")
    claim.stop_heartbeat(run_id)
    print("RESULT", flag, run_id, waited, flush=True)
elif mode == "lost":
    # The claim's heartbeat stands still, as a stalled process's does, until
    # another claim took it over; then its own record phase runs.
    flag, run_id = claim_it("lost")
    claim.stop_heartbeat(run_id)
    signal("lost-claimed")
    wait_for("taken")
    outcomes = signals / "outcomes"
    outcomes.mkdir(exist_ok=True)
    rc = run_record(ending="completed", run_id=run_id, node_id=method,
                    sample=sample, variant="default", pipeline_id="lost-claim",
                    outcomes_dir=outcomes)
    print("RC", rc, flush=True)
    print("RESULT", flag, run_id, waited, flush=True)
elif mode == "lost-taker":
    wait_for("lost-claimed")
    flag, run_id = claim_it("lost")
    claim.stop_heartbeat(run_id)
    signal("taken")
    print("RESULT", flag, run_id, waited, flush=True)
elif mode == "fault":
    flag, run_id = claim_it("hit")
    claim.stop_heartbeat(run_id)
    complete_run(run_id)
    for install in installs:
        getattr(fakes, install)(mp)
    for leg in ("hit", "miss"):
        try:
            claim_it(leg)
        except RuntimeError as exc:
            print("REFUSED", leg, exc, flush=True)
    print("RESULT", flag, run_id, waited, flush=True)
'''


@pytest.fixture
def race_method(tmp_project, monkeypatch):
    """A registered method and sample, built and run once by production."""
    from tests.harness.scenario import DEFAULT_MODULE
    # The claims below pass ``leg``; the method declares it, as a claim's
    # parameter check requires.
    first = completed_run(tmp_project, monkeypatch=monkeypatch, method="race",
                          params={"leg": "setup"})
    return tmp_project, DEFAULT_MODULE, "race", first.run_row["sample"]


def _spawn(tmp_path: Path, race, mode: str, name: str,
           installs: tuple = ()) -> subprocess.Popen:
    """Start one child claim; *installs* are registry fakes it installs."""
    root, module, method, sample = race
    signals = tmp_path / "signals"
    signals.mkdir(exist_ok=True)
    child = tmp_path / "child_claim.py"
    child.write_text(CHILD, newline="\n")
    return subprocess.Popen(
        [sys.executable, str(child), str(REPO), mode, name, str(signals),
         module, method, sample, ",".join(f.__name__ for f in installs)],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _finish(procs: dict[str, subprocess.Popen],
            errors: dict[str, str] | None = None) -> dict[str, str]:
    """Each child's stdout (its stderr into *errors*); a child still alive at
    the timeout is killed."""
    outputs: dict[str, str] = {}
    try:
        for name, proc in procs.items():
            out, err = proc.communicate(timeout=CHILD_TIMEOUT)
            assert proc.returncode == 0, f"{name}: {err}"
            outputs[name] = out
            if errors is not None:
                errors[name] = err
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)
    return outputs


def _result(out: str) -> tuple[str, int, list]:
    line = next(line for line in out.splitlines() if line.startswith("RESULT"))
    _, flag, run_id, waited = line.split(" ", 3)
    return flag, int(run_id), eval(waited)  # noqa: S307 - the child's own list


def _runs(leg: str) -> list:
    from sqlmodel import select

    from wfc.persistence import Run, get_session
    with get_session() as session:
        return [r for r in session.exec(select(Run).order_by(Run.id)).all()
                if r.params == {"leg": leg}]


@pytest.mark.parametrize("winner_ends", ["completed", "failed"])
@workflow(purpose="Two processes claim one method, sample and cache key "
                  "on one SQLite database; one run computes while the other "
                  "waits on it, printing the run it waits on, past three stall "
                  "windows because the winner's heartbeat advances. A winner "
                  "that completes becomes the other's cache hit (an audit row "
                  "of it); one that fails lets the other run")
def test_two_claims_of_one_key_compute_once(race_method, tmp_path, winner_ends):
    口 = Step(step_num=1, name="Claim the key in two processes at once",
             purpose="Both pass a file barrier, then call production pre_run "
                     "holding the claim; the winner holds until the loser waits")
    procs = {name: _spawn(tmp_path, race_method, winner_ends, name)
             for name in ("A", "B")}
    outputs = _finish(procs)
    results = {name: _result(out) for name, out in outputs.items()}
    (winner_name, (_, winner_id, _)), = [
        (n, r) for n, r in results.items() if not r[2]]
    (loser_name, (loser_flag, loser_id, loser_waited)), = [
        (n, r) for n, r in results.items() if r[2]]

    口 = Step(step_num=2, name="One run computed; the other waited on it",
             purpose="The loser waited on the winner alone and printed one "
                     "line naming it; the winner was never taken over")
    assert loser_waited == [winner_id]
    waiting_lines = [line for line in outputs[loser_name].splitlines()
                     if line.startswith("Waiting on run")]
    assert waiting_lines and all(f"run {winner_id}," in line for line in waiting_lines)
    assert "stopped responding" not in outputs[loser_name]
    rows = {r.id: r for r in _runs(winner_ends)}
    assert rows[winner_id].status == winner_ends
    assert rows[winner_id].error_message in (None, "injected failure")

    口 = Step(step_num=3, name="The loser became the hit, or ran",
             purpose="Completed winner: the loser's row is a cache-hit audit "
                     "row of it and the key has one computing row. Failed "
                     "winner: the loser ran and completed")
    if winner_ends == "completed":
        assert loser_flag == "CACHED"
        assert rows[loser_id].cache_source_run_id == winner_id
        assert [r.id for r in rows.values() if r.cache_source_run_id is None] == [winner_id]
    else:
        assert loser_flag == "NEW"
        assert rows[loser_id].status == "completed"
        assert rows[loser_id].cache_source_run_id is None


@workflow(purpose="A claim left running by a killed process (its "
                  "heartbeat advanced, then stopped) does not block its key: a "
                  "later claim waits one stall window, closes it as failed with "
                  "an error saying its claim stopped responding, and runs")
def test_a_killed_claim_is_taken_over_after_the_stall_window(race_method, tmp_path):
    口 = Step(step_num=1, name="Claim, beat, and die",
             purpose="A child claims the key, lets its heartbeat advance, then "
                     "exits without ending its run (no record, no fail_pipeline)")
    killed = _finish({"killed": _spawn(tmp_path, race_method, "killed", "killed")})
    _, killed_id, _ = _result(killed["killed"])
    (row,) = _runs("stall")
    assert row.status == "running" and row.heartbeat > 0

    口 = Step(step_num=2, name="A second claim of the key takes over",
             purpose="It waits on the killed claim, sees its heartbeat stay "
                     "still for the stall window, closes it by compare-and-set "
                     "and claims the key itself")
    taker = _finish({"taker": _spawn(tmp_path, race_method, "taker", "taker")})
    flag, taker_id, waited = _result(taker["taker"])
    assert (flag, waited) == ("NEW", [killed_id])
    rows = {r.id: r for r in _runs("stall")}
    assert rows[killed_id].status == "failed"
    assert "Its claim stopped responding" in rows[killed_id].error_message
    assert rows[taker_id].status == "running" and taker_id != killed_id


@workflow(purpose="A claim another process took over (its heartbeat stood "
                  "still for the stall window) does not end completed when its "
                  "own record phase runs: the run stays failed with the "
                  "take-over's error, the record phase names the lost claim "
                  "and ends the step failed")
def test_a_claim_taken_over_is_not_completed_by_its_own_record(race_method, tmp_path):
    口 = Step(step_num=1, name="Stall a claim; take it over in another process",
             purpose="The stalled claim's process stays alive; the taker waits "
                     "one stall window, closes it as failed and claims the key")
    procs = {"lost": _spawn(tmp_path, race_method, "lost", "lost"),
             "taker": _spawn(tmp_path, race_method, "lost-taker", "taker")}
    errors: dict[str, str] = {}
    outputs = _finish(procs, errors)
    _, lost_id, _ = _result(outputs["lost"])
    flag, taker_id, waited = _result(outputs["taker"])
    assert (flag, waited) == ("NEW", [lost_id])

    口 = Step(step_num=2, name="The lost claim's record phase writes nothing",
             purpose="Its run stays the take-over's failed row; the record "
                     "phase names the lost claim and returns a failure")
    rows = {r.id: r for r in _runs("lost")}
    assert rows[lost_id].status == "failed"
    assert "Its claim stopped responding" in rows[lost_id].error_message
    assert rows[taker_id].status == "running"
    assert "RC 1" in outputs["lost"].splitlines()
    assert f"run {lost_id} is no longer running" in errors["lost"]


@workflow(purpose="The claim's legs: a failure after the claim's run "
                  "row and input rows were written, on the hit path (the audit "
                  "row) and the miss path (the running row), leaves no row of "
                  "either kind")
def test_a_claim_that_fails_after_its_first_rows_leaves_none(race_method, tmp_path):
    from sqlmodel import select

    from wfc.persistence import Run, RunInput, get_session

    out = _finish({"fault": _spawn(tmp_path, race_method, "fault", "fault",
                                   installs=(fakes.fault_after_claim_input_rows,))})["fault"]
    _, completed_id, _ = _result(out)
    assert "REFUSED hit" in out and "REFUSED miss" in out

    assert [r.id for r in _runs("hit")] == [completed_id]
    assert _runs("miss") == []
    with get_session() as session:
        run_ids = set(session.exec(select(Run.id)).all())
        inputs = session.exec(select(RunInput)).all()
    assert {i.run_id for i in inputs} <= run_ids
    assert [i.sample_name for i in inputs if i.run_id == completed_id] == [
        race_method[3]]
