"""History's pipelines summary and the run payload's cancel reasons.

``GET /api/wfc/pipelines`` gives each pipeline its status, by Execution's
one pipeline status rule, and the number of runs its frozen document
expects; the canvas status route takes its terminal word from the same rule.
``GET /api/wfc/runs`` carries each cancelled run's reason and, for an upstream
failure, the failed run's method and sample beside its id.

The rows are produced by running scenarios on the harness's stub rung and
ending them through ``run_pipeline`` with a mocked engine, as in
``tests/test_pipeline_run_records.py``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import bind_provider, stub_docker_image_inspect
from tests.fixtures.routes import canvas_client
from tests.harness import (
    Phase,
    Scenario,
    drive_target,
    exits,
    node,
    run_scenario,
    selector,
    wire,
)

SAMPLES = ["S1", "S2"]
A_S1 = ("a", "S1", "default")
A_S2 = ("a", "S2", "default")
STEP_MAP = {"a": "a", "b": "b", "c": "c"}

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731


def _scenario(pid: str, *, fail: bool, fail_all: bool = False) -> Scenario:
    """sel -> a -> b, and sel -> c beside them, over two samples.

    ``fail`` fails a on S1; ``fail_all`` fails a on both samples.
    """
    behaviors = ({"S1": exits(1), "S2": exits(1)} if fail_all
                 else {"S1": exits(1)} if fail else {})
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")], behavior_by_sample=behaviors),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    )


def _end_pipeline(project, pid: str, monkeypatch, *, exit_code: int,
                  cancelled: bool = False) -> None:
    """End the pipeline through ``run_pipeline`` with a mocked engine."""
    from wfc.execution import run_pipeline

    gen_patch, popen_patch = mocked_snakemake(exit_code)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    kwargs = {"is_cancelled": (lambda: True)} if cancelled else {}
    with gen_patch, popen_patch:
        try:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(project.root),
                         wfc_root=str(project.root), pipeline_id=pid, **kwargs)
        except RuntimeError:
            assert exit_code != 0


def _get(root: Path, monkeypatch, path: str):
    """GET ``path`` from a canvas served on ``root``."""
    bind_provider(monkeypatch, None)
    with canvas_client(root, monkeypatch) as client:
        resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _two_pipelines(root: Path, monkeypatch):
    """A failed pipeline with a frozen document, and a stopped one without.

    ``p-failed`` fails S1's A while S2's A is in flight, so b is cancelled
    upstream_failed and the never-scheduled c not_run. ``p-stopped`` is one
    step claimed under another pipeline id, with no frozen document, then
    stopped by the user.
    """
    from wfc.execution.lifecycle import cancel_pipeline

    obs = run_scenario(_scenario("p-failed", fail=True), root=root,
                       monkeypatch=monkeypatch, interrupted=A_S2,
                       pipeline_end=False)
    _end_pipeline(obs.project, "p-failed", monkeypatch, exit_code=1)
    stopped = dataclasses.replace(obs.project, pipeline_id="p-stopped")
    drive_target(stopped, ("c", "S1", "default"), monkeypatch=monkeypatch,
                 through=Phase.CLAIM)
    cancel_pipeline("p-stopped", str(root))
    return obs


@workflow(purpose="The pipelines summary gives each pipeline its status by "
                  "the pipeline status rule and the runs its frozen document "
                  "expects, null for a pipeline with no frozen document, and "
                  "the document's node order")
def test_the_pipelines_summary_gives_each_pipeline_its_status_and_expected_count(
    git_project, monkeypatch,
):
    口 = Step(step_num=1, name="Leave a failed and a stopped pipeline",
             purpose="Rows production wrote, under two pipeline ids")
    _two_pipelines(git_project, monkeypatch)

    口 = Step(step_num=2, name="Read the summary",
             purpose="One entry per pipeline")
    summaries = {s["pipelineId"]: s for s in
                 _get(git_project, monkeypatch, "/api/wfc/pipelines")}
    assert summaries["p-failed"] == {
        "pipelineId": "p-failed", "status": "failed", "expected": 6,
        "nodeOrder": ["a", "b", "c"],
    }
    assert summaries["p-stopped"] == {
        "pipelineId": "p-stopped", "status": "cancelled", "expected": None,
        "nodeOrder": None,
    }


@pytest.mark.parametrize("ending", ["success", "failure", "user_cancel",
                                    "failure_beside_success"])
@workflow(purpose="A finished pipeline's status reads the same from the canvas "
                  "status route and from History's pipelines summary, whatever "
                  "way it ended, including a node that failed on every sample "
                  "beside a branch that succeeded (mixed, which a ranking "
                  "where one failed node fails the pipeline would read failed)")
def test_a_finished_pipeline_reads_the_same_on_the_canvas_and_in_history(
    git_project, monkeypatch, ending,
):
    from wfc.canvas.run_state import CANVAS_OVERALL_STATUS, aggregate_pipeline_status
    from wfc.execution.lifecycle import cancel_pipeline

    pid = f"p-{ending}"
    interrupted = {"failure": A_S2, "user_cancel": A_S1}.get(ending)
    obs = run_scenario(_scenario(pid, fail=ending == "failure",
                                 fail_all=ending == "failure_beside_success"),
                       root=git_project, monkeypatch=monkeypatch,
                       interrupted=interrupted, pipeline_end=False)
    if ending == "user_cancel":
        cancel_pipeline(pid, str(git_project))
    _end_pipeline(obs.project, pid, monkeypatch,
                  exit_code=0 if ending == "success" else 1,
                  cancelled=ending == "user_cancel")

    summary = next(s for s in _get(git_project, monkeypatch, "/api/wfc/pipelines")
                   if s["pipelineId"] == pid)
    canvas = aggregate_pipeline_status(
        pid, STEP_MAP, thread_alive=False,
        error=None if ending == "success" else "engine failed", log_dir=None,
        cancel_requested=ending == "user_cancel")
    assert summary["status"] == {"success": "succeeded", "failure": "failed",
                                 "user_cancel": "cancelled",
                                 "failure_beside_success": "mixed"}[ending]
    assert CANVAS_OVERALL_STATUS[summary["status"]] == canvas.overall_status


@workflow(purpose="A pipeline that ended with fewer rows than its frozen "
                  "document expects (one recorded before every run got a row) "
                  "counts no pending runs and takes its status from its rows "
                  "while it has no pipeline record; with a record its missing "
                  "runs count as pending and it reads running")
def test_pending_runs_count_only_for_a_pipeline_with_a_record(git_project, monkeypatch):
    from wfc.execution.pipeline_record import ENTRY_POINT_CLI, write_pipeline_record

    pid = "p-missing-rows"
    口 = Step(step_num=1, name="Leave an ended pipeline missing a row",
             purpose="a fails on S1, so b on S1 never runs; the run stops "
                     "before the pipeline-end walk, as a pipeline from before "
                     "every expected run got a row did")
    run_scenario(_scenario(pid, fail=True), root=git_project,
                 monkeypatch=monkeypatch, pipeline_end=False)
    from wfc.persistence import Run, get_session
    from sqlmodel import select
    with get_session() as s:
        rows = s.exec(select(Run).where(Run.pipeline_id == pid)).all()
    assert len(rows) == 5 and all(r.status != "running" for r in rows), (
        "the premise: five ended rows of six expected")

    口 = Step(step_num=2, name="Without a record: no pending runs",
             purpose="The status comes from the rows alone: a failure beside "
                     "completed runs reads mixed")
    summary = next(s for s in _get(git_project, monkeypatch, "/api/wfc/pipelines")
                   if s["pipelineId"] == pid)
    assert (summary["status"], summary["expected"]) == ("mixed", 6)

    口 = Step(step_num=3, name="With a record: the missing run is pending",
             purpose="The record is read per request; the pipeline reads running")
    write_pipeline_record(git_project, pid, ENTRY_POINT_CLI)
    summary = next(s for s in _get(git_project, monkeypatch, "/api/wfc/pipelines")
                   if s["pipelineId"] == pid)
    assert (summary["status"], summary["expected"]) == ("running", 6)


@workflow(purpose="The runs payload carries each cancelled run's reason, its "
                  "node, and for an upstream failure the failed run's id, "
                  "method and sample")
def test_the_runs_payload_says_why_each_run_was_cancelled(git_project, monkeypatch):
    obs = _two_pipelines(git_project, monkeypatch)
    a_s1 = str(obs.runs[A_S1].run_id)

    runs = _get(git_project, monkeypatch, "/api/wfc/runs")
    by_key = {(r["pipelineId"], r["nodeId"], r["dataSource"]): r for r in runs}

    upstream = by_key[("p-failed", "b", "S1")]
    assert (upstream["status"], upstream["cancelReason"]) == ("cancelled",
                                                              "upstream_failed")
    assert (upstream["cancelledDueToRunId"], upstream["cancelledDueToMethod"],
            upstream["cancelledDueToSample"]) == (a_s1, "a", "S1")

    not_run = by_key[("p-failed", "c", "S2")]
    assert not_run["cancelReason"] == "not_run"
    assert (not_run["cancelledDueToRunId"], not_run["cancelledDueToMethod"],
            not_run["cancelledDueToSample"]) == (None, None, None)

    stopped = by_key[("p-stopped", "c", "S1")]
    assert stopped["cancelReason"] == "user"

    failed = by_key[("p-failed", "a", "S1")]
    assert failed["cancelReason"] is None
