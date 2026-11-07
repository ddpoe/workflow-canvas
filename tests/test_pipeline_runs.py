"""The ``pipeline_runs`` table: a pipeline's stored status and end.

The launch writes a pipeline's row as running; Execution's one status helper
stores its status and end through ``pipeline_status`` after every path that
changes its outcome; the canvas server's start gives every pipeline from
before the table its row and stores every row's outcome; History reads the
stored status.

The no-drift witness drives a pipeline through each outcome path this release
has (success, failure, cancel, restart recovery) and checks, after each, that
the stored status and end equal ``pipeline_status`` recomputed from the rows.
The rows come from running scenarios on the harness's stub rung and ending
them through ``run_pipeline`` with a mocked engine, as in
``tests/test_pipeline_run_records.py``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import (
    bind_provider,
    fake_engine_process,
    stub_docker_image_inspect,
)
from tests.fixtures.routes import canvas_client
from tests.fixtures.schema_snapshots import SEED_ROWS, build_release_database, seed_sql
from tests.harness import Scenario, exits, node, run_scenario, selector, wire
from wfc import layout
from wfc.persistence import PipelineRun, Run, get_session, reset_engine

SAMPLES = ["S1", "S2"]
A_S1 = ("a", "S1", "default")
A_S2 = ("a", "S2", "default")

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731


def _rows(pid: str) -> list[Run]:
    """Every run row of the pipeline, by id."""
    with get_session() as s:
        return list(s.exec(
            select(Run).where(Run.pipeline_id == pid).order_by(Run.id)).all())


def _stored(pid: str) -> tuple[str, datetime | None] | None:
    """The pipeline's stored ``(status, ended_at)``, or None without a row."""
    with get_session() as s:
        row = s.get(PipelineRun, pid)
        return None if row is None else (row.status, row.ended_at)


def _recomputed(root: Path, pid: str) -> tuple[str, datetime | None]:
    """The pipeline's status and end, worked out again from its rows.

    The status by ``pipeline_status`` over every row, with the expected
    targets that have no row pending while the pipeline has a record; the
    end is the latest finish time once it is not running.
    """
    from wfc.execution import expected_targets, pipeline_status, read_pipeline_record

    rows = _rows(pid)
    expected = expected_targets(pid, str(root))
    pending = (max(len(expected.targets) - len(rows), 0)
               if expected is not None and read_pipeline_record(root, pid)
               else 0)
    status = pipeline_status(((r.status, r.cancel_reason) for r in rows), pending)
    if status == "running":
        return status, None
    return status, max((r.finished_at for r in rows if r.finished_at is not None),
                       default=None)


def _assert_no_drift(root: Path, pid: str) -> str:
    """The stored status and end equal the recomputed ones; returns the status."""
    stored = _stored(pid)
    assert stored is not None, f"{pid} has no pipeline_runs row"
    assert stored == _recomputed(root, pid)
    return stored[0]


def _scenario(pid: str, *, fail: bool = False) -> Scenario:
    """sel -> a -> b, and sel -> c beside them, over two samples; a fails on S1."""
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")],
                    behavior_by_sample={"S1": exits(1)} if fail else {}),
               node("b", inputs=[wire("a")]),
               node("c", inputs=[wire("sel")])],
        samples=list(SAMPLES), pipeline_id=pid,
    )


def _end_pipeline(project, pid: str, monkeypatch, *, exit_code: int,
                  cancelled: bool = False) -> None:
    """End the pipeline through ``run_pipeline`` with a mocked engine."""
    from wfc.execution import run_pipeline

    gen_patch, _ = mocked_snakemake(exit_code)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    kwargs = {"is_cancelled": (lambda: True)} if cancelled else {}
    # Only the engine is faked: a draft's preflight runs the Registration
    # check, which calls git.
    with gen_patch, fake_engine_process(returncode=exit_code, only_snakemake=True):
        try:
            run_pipeline(pipeline_path=str(project.pipeline_json),
                         project_root=str(project.root),
                         pipeline_id=pid, **kwargs)
        except RuntimeError:
            assert exit_code != 0


# ---- the launch ---------------------------------------------------------------

def test_the_launch_writes_the_pipeline_row_as_running(tmp_project):
    """``prepare_launch`` writes the row: running, the document's name, its
    start, no end; a blank name is stored as none."""
    from wfc.execution.lifecycle import prepare_launch

    source = tmp_project / "doc.json"
    named = {"name": "Nightly QC", "nodes": [], "edges": []}
    source.write_text(json.dumps(named), encoding="utf-8")
    prepare_launch(tmp_project, source, named, named, "p-named")
    with get_session() as s:
        row = s.get(PipelineRun, "p-named")
        assert (row.status, row.name, row.ended_at) == ("running", "Nightly QC", None)
        assert row.started_at is not None

    blank = {"name": "  ", "nodes": [], "edges": []}
    prepare_launch(tmp_project, source, blank, blank, "p-blank")
    with get_session() as s:
        assert s.get(PipelineRun, "p-blank").name is None


# ---- the no-drift witness -----------------------------------------------------

#: The status each ending leaves.
_ENDINGS = {
    "success": "succeeded",
    "failure": "failed",
    "user_cancel": "cancelled",
    "close_out": "failed",
}


@pytest.mark.parametrize("ending", list(_ENDINGS))
@workflow(purpose="After every path that changes a pipeline's outcome -- the "
                  "success and failure endings, a user cancel of its running "
                  "rows and the cancel's own ending, the restart close-out -- "
                  "the stored status and end equal pipeline_status recomputed "
                  "from the rows (no drift), and the launch's running row "
                  "reads running until then")
def test_the_stored_status_never_drifts_from_the_rows(git_project, monkeypatch,
                                                     ending):
    from wfc.execution import close_out_pipeline
    from wfc.execution.lifecycle import cancel_pipeline, fail_pipeline

    pid = f"p-drift-{ending}"
    interrupted = {"failure": A_S2, "user_cancel": A_S1, "close_out": A_S1}.get(ending)
    obs = run_scenario(_scenario(pid, fail=ending == "failure"), root=git_project,
                       monkeypatch=monkeypatch, interrupted=interrupted,
                       pipeline_end=False)
    assert _stored(pid)[0] == "running", "the launch's row reads running"

    if ending == "success":
        _end_pipeline(obs.project, pid, monkeypatch, exit_code=0)
    elif ending == "failure":
        fail_pipeline(pid)
        _assert_no_drift(git_project, pid)
        _end_pipeline(obs.project, pid, monkeypatch, exit_code=1)
    elif ending == "user_cancel":
        cancel_pipeline(pid, str(git_project))
        _assert_no_drift(git_project, pid)
        _end_pipeline(obs.project, pid, monkeypatch, exit_code=1, cancelled=True)
    else:
        close_out_pipeline(git_project, pid)

    assert _assert_no_drift(git_project, pid) == _ENDINGS[ending]
    assert _stored(pid)[1] == max(r.finished_at for r in _rows(pid)), (
        "the end is the rows' latest finish")


# ---- the no-drift witness, a pipeline with draft steps -------------------------

def _recomputed_with_drafts(root: Path, pid: str) -> tuple[str, datetime | None]:
    """The status and end worked out again from the rows and the draft steps left.

    Each draft step the pipeline still has in its draft's ``draft-run.json``
    counts as a run (its status, cancel reason and finish, as naive UTC like
    the rows'); an ended pipeline has no record, so nothing is pending.
    """
    from datetime import UTC

    from wfc.execution import read_pipeline_record
    from wfc.execution.draft import pipeline_draft_steps
    from wfc.execution.pipeline_outcome import pipeline_outcome

    assert read_pipeline_record(root, pid) is None, "the pipeline has ended"
    runs = [(r.status, r.cancel_reason, r.finished_at) for r in _rows(pid)]
    for _draft, step in pipeline_draft_steps(pid, root):
        finished = step.get("finished_at")
        moment = datetime.fromisoformat(finished) if finished else None
        if moment is not None and moment.tzinfo is not None:
            moment = moment.astimezone(UTC).replace(tzinfo=None)
        runs.append((step["status"], step.get("cancel_reason"), moment))
    return pipeline_outcome(runs)


def _assert_no_drift_with_drafts(root: Path, pid: str) -> str:
    stored = _stored(pid)
    assert stored is not None, f"{pid} has no pipeline_runs row"
    assert stored == _recomputed_with_drafts(root, pid)
    return stored[0]


def _draft_chain(pid: str, *, recorded_step: bool = True) -> Scenario:
    """sel -> up -> mid (draft) -> low; without up when nothing is recorded."""
    head = [node("up", inputs=[wire("sel")], outputs={"table": ".csv"})] \
        if recorded_step else []
    mid_input = wire("up", source_slot="table") if recorded_step else wire("sel")
    return Scenario(
        nodes=[selector(), *head,
               node("mid", inputs=[mid_input], outputs={"table": ".csv"}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["S1"], pipeline_id=pid, drafts=("mid",))


#: Each way a draft run is cleared, and the reason the pipeline then reads.
_CLEARINGS = ("replaced", "outputs_deleted", "removed", "registered")


@pytest.mark.parametrize("clearing", [*_CLEARINGS, "nothing_recorded"])
@workflow(purpose="A pipeline with draft steps: after its ending and after each "
                  "way its draft run is cleared (replaced by another pipeline's, "
                  "Delete outputs, Remove draft, Register), the stored status and "
                  "end equal pipeline_status recomputed from its rows and the "
                  "draft steps it has left (no drift); a pipeline that recorded "
                  "nothing leaves pipeline_runs when its draft run is cleared")
def test_the_stored_status_never_drifts_through_a_draft_run_clearing(
        git_project, monkeypatch, clearing):
    import dataclasses

    from tests.fixtures.fakes.docker import stub_docker_image_content
    from tests.harness.project import STUB_DIGEST
    from wfc.execution.draft_clearing import (
        delete_draft_outputs_and_clear,
        plan_draft_delete,
        register_draft_and_clear,
        remove_draft_and_clear,
    )

    pid = f"p-drift-draft-{clearing}"
    # The draft runs on its env's current image: the daemon reports it present.
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    scenario = _draft_chain(pid, recorded_step=clearing != "nothing_recorded")
    mid = scenario.node_by_id("mid")
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch,
                       pipeline_end=False)
    _end_pipeline(obs.project, pid, monkeypatch, exit_code=0)
    assert _assert_no_drift_with_drafts(git_project, pid) == "succeeded"
    with get_session() as s:
        assert s.get(PipelineRun, pid).draft_step_count == 2

    if clearing == "replaced":
        run_scenario(dataclasses.replace(scenario, pipeline_id=f"{pid}-b"),
                     root=git_project, monkeypatch=monkeypatch)
    elif clearing == "outputs_deleted":
        planned = plan_draft_delete(mid.module, mid.method_name, outputs_only=True,
                                    root=git_project).token
        delete_draft_outputs_and_clear(mid.module, mid.method_name, git_project,
                                       planned=planned)
    elif clearing == "registered":
        register_draft_and_clear(mid.module, mid.method_name, assume_yes=True,
                                 root=git_project)
    else:
        planned = plan_draft_delete(mid.module, mid.method_name,
                                    root=git_project).token
        remove_draft_and_clear(mid.module, mid.method_name, git_project,
                               planned=planned)

    if clearing == "nothing_recorded":
        assert _stored(pid) is None, "a pipeline that recorded nothing leaves"
        return
    assert _assert_no_drift_with_drafts(git_project, pid) == "succeeded"
    with get_session() as s:
        row = s.get(PipelineRun, pid)
        assert row.cleared_reason and row.cleared_at is not None
    assert _stored(pid)[1] == max(r.finished_at for r in _rows(pid)), (
        "what is left is up's run")


# ---- case 18, the pipeline half -----------------------------------------------

#: Finish times given to the snapshot's runs, so each past pipeline has an end.
_FINISHED = {1: "2026-09-01 10:05:00.000000", 2: "2026-09-01 10:09:00.000000",
             3: "2026-09-02 11:00:00.000000", 4: "2026-09-03 12:00:00.000000",
             5: "2026-09-04 09:30:00.000000", 6: "2026-09-04 09:31:00.000000"}


def _previous_release_project(root: Path) -> None:
    """Replace the project's database with the previous release's, seeded.

    LEGACY-SHAPE FIXTURE (the testing SOP's schema-snapshot exception): the
    release snapshot's DDL and rows, with finish times on the ended runs, as
    a user's database from before ``pipeline_runs`` holds them.
    """
    reset_engine()
    db_path = layout.db_path(root)
    for leftover in (db_path, db_path.with_name(db_path.name + "-wal"),
                     db_path.with_name(db_path.name + "-shm")):
        leftover.unlink(missing_ok=True)
    finishes = "\n".join(f"UPDATE runs SET finished_at = '{when}' WHERE id = {rid};"
                         for rid, when in _FINISHED.items())
    build_release_database(db_path, seed_sql() + "\n" + finishes)


def _seeded_pipelines() -> dict[str, list[tuple[str, str | None]]]:
    """Each seeded pipeline's runs as ``(status, cancel_reason)``."""
    columns, rows = SEED_ROWS["runs"]
    pid_at, status_at = columns.index("pipeline_id"), columns.index("status")
    by_pipeline: dict[str, list[tuple[str, str | None]]] = {}
    for row in rows:
        by_pipeline.setdefault(row[pid_at], []).append((row[status_at], None))
    return by_pipeline


def _table(db_path: Path) -> dict[str, tuple]:
    """The ``pipeline_runs`` rows as ``{pipeline_id: (status, ended_at)}``."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {pid: (status, ended) for pid, status, ended in conn.execute(
            "SELECT pipeline_id, status, ended_at FROM pipeline_runs")}
    finally:
        conn.close()


def _serve(root: Path, monkeypatch) -> tuple[dict[str, str], set[str]]:
    """Start the canvas on ``root``; History's statuses and listed pipelines."""
    bind_provider(monkeypatch, None)
    with canvas_client(root, monkeypatch) as client:
        summaries = client.get("/api/wfc/pipelines")
        runs = client.get("/api/wfc/runs")
    assert summaries.status_code == 200, summaries.text
    assert runs.status_code == 200, runs.text
    return ({s["pipelineId"]: s["status"] for s in summaries.json()},
            {r["pipelineId"] for r in runs.json() if r["pipelineId"]})


@workflow(purpose="A previous release's database, opened by this release's "
                  "canvas server: every past pipeline gets one pipeline_runs "
                  "row carrying its status and end, History lists the same "
                  "pipelines with the statuses the pipeline status rule gives "
                  "their rows, and a second start changes nothing")
def test_past_pipelines_get_their_rows_with_status_and_end(tmp_project, monkeypatch):
    from wfc.execution import pipeline_status

    口 = Step(step_num=1, name="Open a previous release's database",
             purpose="Seven past pipelines (completed, failed, retried, a "
                     "cache-hit audit row, two left running) and no "
                     "pipeline_runs table")
    _previous_release_project(tmp_project)
    seeded = _seeded_pipelines()
    before = {pid: pipeline_status(runs) for pid, runs in seeded.items()}

    口 = Step(step_num=2, name="Start the canvas server",
             purpose="The bootstrap creates the table; the start-up pass gives "
                     "every past pipeline its row and stores its outcome before "
                     "History reads it")
    statuses, listed = _serve(tmp_project, monkeypatch)

    口 = Step(step_num=3, name="One row per past pipeline, with status and end",
             purpose="The ended pipelines' end is their latest run's finish; "
                     "the running ones have none")
    db_path = layout.db_path(tmp_project)
    table = _table(db_path)
    assert set(table) == set(seeded)
    columns, rows = SEED_ROWS["runs"]
    pid_at = columns.index("pipeline_id")
    for pid in seeded:
        finishes = [_FINISHED[row[0]] for row in rows
                    if row[pid_at] == pid and row[0] in _FINISHED]
        ended = max(finishes) if before[pid] != "running" else None
        assert table[pid] == (before[pid], ended), pid

    口 = Step(step_num=4, name="History lists the same pipelines and statuses",
             purpose="The pipelines the runs name, each with the status its "
                     "rows give")
    assert listed == set(seeded)
    assert statuses == before

    口 = Step(step_num=5, name="A second start changes nothing",
             purpose="The pass is idempotent: no row added, the same outcomes")
    again, _ = _serve(tmp_project, monkeypatch)
    assert again == before
    assert _table(db_path) == table
