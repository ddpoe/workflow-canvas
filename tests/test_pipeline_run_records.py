"""Every run a pipeline expected ends with a record, and the pipeline's status.

The pipeline-end walk writes one ``cancelled`` row for every expected target
that has none, after a failure and after a user cancel alike, and each
cancelled row records why (``cancel_reason``). ``pipeline_status`` is the one
rule a pipeline's status comes from: History's summary and the canvas status
route read it alike after every ending, and no pipeline record outlives one.

The run state the walk reconciles is produced by running the scenario on the
harness's stub rung; Snakemake itself is mocked, as in
``tests/test_cancelled_rows_run_pipeline.py``.
"""

from __future__ import annotations

import json
import threading

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import ended_process, mocked_snakemake
from tests.fixtures.fakes import bind_provider, stub_docker_image_inspect
from tests.fixtures.routes import canvas_client
from tests.harness import (Scenario, build_project, exits, node, run_scenario,
                           selector, wire)
from wfc.persistence import Run, get_session

SAMPLES = ["S1", "S2"]

A_S1 = ("a", "S1", "default")
A_S2 = ("a", "S2", "default")

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731


def _rows(pid: str) -> list[Run]:
    """Every row of the pipeline, by id."""
    with get_session() as s:
        return list(s.exec(
            select(Run).where(Run.pipeline_id == pid).order_by(Run.id)
        ).all())


def _run_pipeline(project, pid: str, monkeypatch, *, exit_code: int,
                  cancelled: bool = False) -> None:
    """Run ``run_pipeline`` over the scenario's state with a mocked engine."""
    from wfc.execution import run_pipeline

    gen_patch, popen_patch = mocked_snakemake(exit_code)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    kwargs = {"is_cancelled": (lambda: True)} if cancelled else {}
    with gen_patch, popen_patch:
        if exit_code == 0:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(project.root),
                         wfc_root=str(project.root), pipeline_id=pid, **kwargs)
        else:
            with pytest.raises(RuntimeError):
                run_pipeline(pipeline_path=str(project.pipeline_json),
                             project_root=str(project.root),
                             wfc_root=str(project.root), pipeline_id=pid,
                             **kwargs)


@workflow(purpose="A user cancel with one step running and the rest not "
                  "started: the running row and every unstarted target end "
                  "cancelled with reason user, a finish time and no cause")
def test_a_user_cancel_gives_every_unstarted_target_a_user_row(git_project,
                                                              monkeypatch):
    from wfc.execution.lifecycle import cancel_pipeline

    pid = "pipe-user-cancel"
    口 = Step(step_num=1, name="Run until the cancel catches S1's A in flight",
             purpose="S1's A holds the claim's own running row; nothing after "
                     "it was scheduled")
    obs = run_scenario(Scenario(
        nodes=[selector(), node("a", inputs=[wire("sel")]),
               node("b", inputs=[wire("a")])],
        samples=list(SAMPLES), pipeline_id=pid,
    ), root=git_project, monkeypatch=monkeypatch, interrupted=A_S1,
        pipeline_end=False)
    assert obs.run_row(A_S1)["status"] == "running"
    assert len(_rows(pid)) == 1

    口 = Step(step_num=2, name="Cancel, then end the pipeline as the canvas does",
             purpose="The cancel route flips the running row; the run thread's "
                     "failure ending runs the walk with the probe answering true")
    cancel_pipeline(pid, str(obs.project.root))
    _run_pipeline(obs.project, pid, monkeypatch, exit_code=1, cancelled=True)

    口 = Step(step_num=3, name="Every target has one user row",
             purpose="The flipped row and the walk's rows all read stopped by "
                     "the user, finished, with no cause")
    rows = _rows(pid)
    assert sorted((r.node_id, r.sample) for r in rows) == [
        ("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")]
    for r in rows:
        assert r.status == "cancelled", (r.node_id, r.sample)
        assert r.cancel_reason == "user", (r.node_id, r.sample)
        assert r.finished_at is not None, (r.node_id, r.sample)
        assert r.cancelled_due_to_run_id is None, (r.node_id, r.sample)


@workflow(purpose="A failure with an independent branch the engine never "
                  "scheduled: the failure's descendants are cancelled "
                  "upstream_failed under their nearest failed ancestor, and "
                  "the independent branch is cancelled not_run with no cause")
def test_a_failure_cancels_descendants_and_an_unscheduled_branch_not_run(
    git_project, monkeypatch,
):
    pid = "pipe-failure-branch"
    口 = Step(step_num=1, name="Run until the abort catches S2's A in flight",
             purpose="S1's A fails; the fail-fast abort leaves S2's A running "
                     "and schedules neither b nor the independent c")
    obs = run_scenario(Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")],
                    behavior_by_sample={"S1": exits(1)}),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    ), root=git_project, monkeypatch=monkeypatch, interrupted=A_S2,
        pipeline_end=False)
    a_s1 = obs.runs[A_S1].run_id
    a_s2 = obs.runs[A_S2].run_id
    assert obs.run_row(A_S1)["status"] == "failed"
    assert all(obs.runs[t].skipped for t in obs.targets if t[0] == "c"), (
        "the premise: the independent branch was never scheduled")

    口 = Step(step_num=2, name="End the pipeline with the engine's failure",
             purpose="fail_pipeline flips S2's A to failed, then the walk runs")
    _run_pipeline(obs.project, pid, monkeypatch, exit_code=1)

    口 = Step(step_num=3, name="Each cancelled row says why",
             purpose="b under its failed A, c with no failed ancestor")
    cancelled = {(r.node_id, r.sample): r for r in _rows(pid)
                 if r.status == "cancelled"}
    assert set(cancelled) == {("b", "S1"), ("b", "S2"), ("c", "S1"), ("c", "S2")}
    assert cancelled[("b", "S1")].cancel_reason == "upstream_failed"
    assert cancelled[("b", "S1")].cancelled_due_to_run_id == a_s1
    assert cancelled[("b", "S2")].cancel_reason == "upstream_failed"
    assert cancelled[("b", "S2")].cancelled_due_to_run_id == a_s2
    for sample in SAMPLES:
        row = cancelled[("c", sample)]
        assert row.cancel_reason == "not_run"
        assert row.cancelled_due_to_run_id is None
    assert all(r.finished_at is not None for r in cancelled.values())


def _engine_fails_before_any_claim(project, pid: str, monkeypatch, *,
                                   during_engine=None) -> str:
    """Run ``run_pipeline`` with an engine that exits 1; return its message.

    Args:
        project: The built scenario project.
        pid: The pipeline id.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        during_engine: Called while the fake engine runs, before it exits.

    Returns:
        The message ``run_pipeline`` raised.
    """
    from wfc.execution import run_pipeline

    gen_patch, popen_patch = mocked_snakemake(1)
    if during_engine is not None:
        proc = popen_patch.kwargs["return_value"]
        proc.wait.side_effect = lambda *a, **k: (during_engine(), 1)[1]
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch, pytest.raises(RuntimeError) as raised:
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id=pid)
    return str(raised.value)


@workflow(purpose="An engine that exits non-zero before any step is claimed, "
                  "with no cancel: every expected target ends cancelled for the "
                  "engine-side reason engine_failed, carrying the engine's "
                  "message, and the pipeline reads failed")
def test_an_engine_that_failed_before_any_step_reads_failed(git_project,
                                                           monkeypatch):
    from wfc.execution import pipeline_status

    pid = "pipe-engine-failed"
    口 = Step(step_num=1, name="Build the project; nothing has run",
             purpose="Two samples through a chain a -> b and an independent c")
    project = build_project(Scenario(
        nodes=[selector(), node("a", inputs=[wire("sel")]),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    ), root=git_project, monkeypatch=monkeypatch)
    assert _rows(pid) == []

    口 = Step(step_num=2, name="The engine exits 1 before any claim",
             purpose="run_pipeline's failure ending runs with no failed row "
                     "and no user cancel")
    message = _engine_fails_before_any_claim(project, pid, monkeypatch)

    口 = Step(step_num=3, name="Every target is cancelled engine_failed",
             purpose="One row per expected target, each with the engine's "
                     "message and no cause, and the pipeline reads failed")
    rows = _rows(pid)
    assert sorted((r.node_id, r.sample) for r in rows) == [
        ("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2"),
        ("c", "S1"), ("c", "S2")]
    for r in rows:
        assert (r.status, r.cancel_reason) == ("cancelled", "engine_failed"), (
            r.node_id, r.sample)
        assert r.error_message == message, (r.node_id, r.sample)
        assert r.cancelled_due_to_run_id is None, (r.node_id, r.sample)
        assert r.finished_at is not None, (r.node_id, r.sample)
    assert pipeline_status([(r.status, r.cancel_reason) for r in rows], 0) \
        == "failed"


@workflow(purpose="An engine failure whose only cause is a claim refusal is a "
                  "step failure, not an engine failure: the refused target's "
                  "failed row explains it, so its descendants are "
                  "upstream_failed and the independent targets not_run")
def test_a_claim_refusal_keeps_the_engine_failure_a_step_failure(git_project,
                                                                monkeypatch):
    from wfc.execution.record import write_claim_refusal

    pid = "pipe-refused-then-engine-failed"
    口 = Step(step_num=1, name="Build the project; nothing has run",
             purpose="a -> b, and an independent c, over two samples")
    project = build_project(Scenario(
        nodes=[selector(), node("a", inputs=[wire("sel")]),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    ), root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The claim refuses S1's a while the engine runs",
             purpose="The refusal leaves only its sidecar; the engine then "
                     "exits 1")
    _engine_fails_before_any_claim(
        project, pid, monkeypatch,
        during_engine=lambda: write_claim_refusal(pid, "a", "S1", "default",
                                                  "refused: no input"))

    口 = Step(step_num=3, name="Reasons follow the refusal",
             purpose="S1's a failed with the refusal; S1's b upstream_failed "
                     "under it; every other target not_run")
    rows = {(r.node_id, r.sample): r for r in _rows(pid)}
    assert rows[("a", "S1")].status == "failed"
    assert rows[("a", "S1")].error_message == "refused: no input"
    assert rows[("b", "S1")].cancel_reason == "upstream_failed"
    assert rows[("b", "S1")].cancelled_due_to_run_id == rows[("a", "S1")].id
    for key in [("a", "S2"), ("b", "S2"), ("c", "S1"), ("c", "S2")]:
        assert (rows[key].status, rows[key].cancel_reason) == (
            "cancelled", "not_run"), key


def _expected_keys(project, pid: str) -> list[tuple[str, str, str]]:
    """The expected targets' keys, from the enumeration the walk uses."""
    from wfc.execution.lifecycle import expected_targets

    targets = expected_targets(pid, str(project.root))
    assert targets is not None
    return sorted(t.key for t in targets.targets)


def _run_as_the_canvas(project, pid: str, monkeypatch, *, exit_code: int,
                       engine_pid: int | None = None,
                       during_engine=None) -> None:
    """Run the pipeline as the canvas's run route does, with a mocked engine.

    The job entry ``submit_pipeline`` installs (each node of the submitted
    document mapped to its method, no error, no process, no cancel), then
    ``run_in_background``
    on its own thread, which records a failure on the entry for the status
    route to read.

    Args:
        project: The built scenario project.
        pid: The pipeline id (the job id).
        monkeypatch: An active ``pytest.MonkeyPatch``.
        exit_code: The exit status the fake engine reports.
        engine_pid: A real process to stand for the engine in the pipeline
            record, so a cancel kills it on its identity.
        during_engine: Called while the fake engine runs, before it exits.
    """
    from wfc.canvas.state import _active_jobs
    from wfc.canvas.submission import run_in_background

    document = json.loads(project.pipeline_json.read_text(encoding="utf-8"))
    step_map = {n["id"]: n.get("method") or "" for n in document["nodes"]}
    gen_patch, popen_patch = mocked_snakemake(exit_code)
    proc = popen_patch.kwargs["return_value"]
    if engine_pid is not None:
        proc.pid = engine_pid
    if during_engine is not None:
        proc.wait.side_effect = lambda *a, **k: (during_engine(), exit_code)[1]
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    thread = threading.Thread(target=run_in_background, daemon=True, kwargs={
        "pipeline_path": project.pipeline_json,
        "project_root": str(project.root), "pipeline_id": pid,
        "keep_going": False})
    _active_jobs[pid] = {"thread": thread, "pipeline_id": pid,
                         "step_map": step_map,
                         "log_dir": None, "error": None, "proc": None,
                         "cancel_requested": False}
    with gen_patch, popen_patch:
        thread.start()
        thread.join(timeout=60)
    assert not thread.is_alive(), "the run thread ended"


#: The five ways a pipeline ends, and the status History reads for each.
_ENDINGS = {
    "success": "succeeded",
    "failure": "failed",
    "user_cancel": "cancelled",
    "close_out": "failed",
    "engine_failed": "failed",
}


@pytest.mark.parametrize("ending", list(_ENDINGS))
@workflow(purpose="Whatever way a pipeline ends -- success, a failure, a user "
                  "cancel through the cancel route, a close-out after a "
                  "restart, or an engine that failed before any step -- its "
                  "rows match its expected targets exactly, one row each; "
                  "History's summary and the canvas status route read the "
                  "same status; and no pipeline record is left")
def test_rows_match_the_expected_targets_after_every_ending(git_project,
                                                           monkeypatch,
                                                           processes, ending):
    from wfc.canvas.run_state import CANVAS_OVERALL_STATUS
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.execution import close_out_pipeline, read_pipeline_record
    from wfc.execution.pipeline_record import (ENTRY_POINT_CANVAS,
                                               record_engine,
                                               write_pipeline_record)

    pid = f"pipe-ending-{ending}"
    口 = Step(step_num=1, name="Bring the pipeline to where its ending starts",
             purpose="sel -> a -> b and sel -> c over two samples. Every target "
                     "run, S1's a failed with S2's a in flight, S1's a in "
                     "flight, or (engine failure) nothing run")
    behaviors = {"failure": {"S1": exits(1)}}.get(ending, {})
    scenario = Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")], behavior_by_sample=behaviors),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    )
    if ending == "engine_failed":
        project = build_project(scenario, root=git_project,
                                monkeypatch=monkeypatch)
    else:
        interrupted = {"failure": A_S2, "user_cancel": A_S1,
                       "close_out": A_S1}.get(ending)
        project = run_scenario(scenario, root=git_project,
                               monkeypatch=monkeypatch, interrupted=interrupted,
                               pipeline_end=False).project
    root = project.root
    client = canvas_client(root, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(root)))

    def history_status() -> str:
        summaries = client.get("/api/wfc/pipelines").json()
        return next(s["status"] for s in summaries if s["pipelineId"] == pid)

    def route_status() -> str:
        resp = client.get(f"/api/workflow/status/{pid}")
        assert resp.status_code == 200, resp.text
        return resp.json()["overall_status"]

    口 = Step(step_num=2, name="End it",
             purpose="Success, failure and engine failure end on the canvas's "
                     "run thread; the user cancel is the cancel route while the "
                     "engine runs; the close-out is what the recovery does to a "
                     "pipeline a restart left with its owner and engine gone, "
                     "after a watching tab reached it through its record")
    if ending == "close_out":
        write_pipeline_record(root, pid, ENTRY_POINT_CANVAS,
                              owner=ended_process(processes))
        record_engine(root, pid, ended_process(processes).pid)
        assert (history_status(), route_status()) == ("running", "running")
        close_out_pipeline(root, pid)
    elif ending == "user_cancel":
        engine = processes()
        stopped = {}
        _run_as_the_canvas(
            project, pid, monkeypatch, exit_code=1, engine_pid=engine.pid,
            during_engine=lambda: stopped.setdefault(
                "resp", client.post(f"/api/workflow/cancel/{pid}")))
        assert stopped["resp"].json() == {"status": "cancelled",
                                          "job_id": pid, "noop": False}
        assert engine.wait(timeout=10) is not None, "the engine was killed"
    else:
        _run_as_the_canvas(project, pid, monkeypatch,
                           exit_code=0 if ending == "success" else 1)

    口 = Step(step_num=3, name="One row per expected target",
             purpose="The rows' keys are the expected targets' keys, each once")
    rows = _rows(pid)
    keys = sorted((r.node_id, r.sample or "",
                   json.dumps(r.params or {}, sort_keys=True, default=str))
                  for r in rows)
    assert keys == _expected_keys(project, pid)
    assert len(keys) == 6

    口 = Step(step_num=4, name="History and the canvas read the same status",
             purpose="History's summary and the status route, each in its own "
                     "words, give the ending's status")
    assert history_status() == _ENDINGS[ending]
    assert route_status() == CANVAS_OVERALL_STATUS[_ENDINGS[ending]]

    口 = Step(step_num=5, name="No record is left",
             purpose="Every ending removes the pipeline record")
    assert read_pipeline_record(root, pid) is None


#: Short names for the rows the status table is written in: (status, reason).
_DONE = ("completed", None)
_RUNNING = ("running", None)
_FAILED = ("failed", None)
_USER = ("cancelled", "user")
_UPSTREAM = ("cancelled", "upstream_failed")
_NOT_RUN = ("cancelled", "not_run")
_NO_REASON = ("cancelled", None)
_INTERRUPTED = ("cancelled", "interrupted")
_ENGINE_FAILED = ("cancelled", "engine_failed")


@pytest.mark.parametrize(("rows", "pending", "expected"), [
    # running: a row that has not ended, or a pending target, decides alone.
    ([_DONE, _RUNNING], 0, "running"),
    ([_DONE], 2, "running"),
    ([_INTERRUPTED, _RUNNING], 0, "running"),
    ([_FAILED], 1, "running"),
    # succeeded: every row completed.
    ([_DONE, _DONE], 0, "succeeded"),
    # failed: nothing completed, and a row failed ...
    ([_FAILED], 0, "failed"),
    ([_FAILED, _USER, _FAILED], 0, "failed"),
    # ... or was cancelled for an engine-side reason.
    ([_INTERRUPTED], 0, "failed"),
    ([_ENGINE_FAILED], 0, "failed"),
    ([_USER, _INTERRUPTED], 0, "failed"),
    ([_NOT_RUN, _ENGINE_FAILED], 0, "failed"),
    # cancelled: every row cancelled, none for an engine-side reason.
    ([_USER], 0, "cancelled"),
    ([_USER, _NOT_RUN], 0, "cancelled"),
    ([_UPSTREAM], 0, "cancelled"),
    ([_NO_REASON, _NO_REASON], 0, "cancelled"),
    # mixed: something completed, and something failed or was cancelled,
    # whatever the reason.
    ([_DONE, _FAILED], 0, "mixed"),
    ([_DONE, _USER], 0, "mixed"),
    ([_DONE, _INTERRUPTED], 0, "mixed"),
    ([_DONE, _ENGINE_FAILED, _USER], 0, "mixed"),
    ([_DONE, _FAILED, _NOT_RUN], 0, "mixed"),
])
def test_pipeline_status_follows_the_table(rows, pending, expected):
    """Each row of the pipeline status table, over plain (status, reason)
    values: an engine-side reason (``interrupted``, ``engine_failed``) reads
    as a failure when nothing completed, and as a cancel otherwise."""
    from wfc.execution import pipeline_status

    assert pipeline_status(rows, pending) == expected
