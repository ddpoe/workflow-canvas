"""The pipeline record ``run_pipeline`` keeps while it runs a pipeline.

``run_pipeline`` writes the record once the launch has fixed the pipeline id,
adds the engine's identity when the engine starts, lets a cancel flag set on
the record reach its endings, and removes the record after its end steps,
whatever way the pipeline ends. A record it cannot write refuses the
pipeline before the engine starts.

The run state is produced by the harness's scenario runner; Snakemake itself
is mocked, as in ``tests/test_pipeline_run_records.py``.
"""

from __future__ import annotations

import json
import os

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import stub_docker_image_inspect
from tests.harness import Scenario, build_project, exits, node, selector, wire
from wfc import layout
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CANVAS,
    ENTRY_POINT_CLI,
    PipelineRecordError,
    cancel_requested,
    frozen_step_map,
    list_pipeline_records,
    process_identity,
    read_pipeline_record,
    record_engine,
    remove_pipeline_record,
    request_cancel,
    write_pipeline_record,
)
from wfc.persistence import Run, get_session

SAMPLES = ["S1", "S2"]

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731


def _rows(pid: str) -> list[Run]:
    """Every row of the pipeline, by id."""
    with get_session() as s:
        return list(s.exec(
            select(Run).where(Run.pipeline_id == pid).order_by(Run.id)
        ).all())


def _chain(pid: str, *, fail: bool = False) -> Scenario:
    """sel -> a -> b over two samples; ``fail`` fails a on S1."""
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire("sel")],
                    behavior_by_sample={"S1": exits(1)} if fail else {}),
               node("b", inputs=[wire("a")])],
        samples=list(SAMPLES), pipeline_id=pid,
    )


def _run(project, pid: str, monkeypatch, *, exit_code: int, during_engine=None,
         engine_pid: int | None = None, **kwargs) -> None:
    """Run ``run_pipeline`` with a mocked engine.

    Args:
        project: The built scenario project.
        pid: The pipeline id.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        exit_code: What the engine exits with; non-zero expects the raise.
        during_engine: Called while the engine runs, before it exits.
        engine_pid: The pid the engine process reports.
        **kwargs: Passed on to ``run_pipeline``.
    """
    from wfc.execution import run_pipeline

    gen_patch, popen_patch = mocked_snakemake(exit_code)
    proc = popen_patch.kwargs["return_value"]
    if engine_pid is not None:
        proc.pid = engine_pid
    if during_engine is not None:
        proc.wait.side_effect = lambda *a, **k: (during_engine(), exit_code)[1]
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch:
        call = lambda: run_pipeline(  # noqa: E731
            pipeline_path=str(project.pipeline_json), project_root=str(project.root),
            wfc_root=str(project.root), pipeline_id=pid, **kwargs)
        if exit_code == 0:
            call()
        else:
            with pytest.raises(RuntimeError):
                call()


@pytest.mark.parametrize("ending", ["success", "failure", "user_cancel"])
@workflow(purpose="While run_pipeline runs a pipeline its record names the "
                  "pipeline, the entry point, this process as the owner and the "
                  "engine by pid and creation time; once its end steps are done "
                  "the record is gone, after a success, a failure and a user "
                  "cancel alike")
def test_run_pipeline_keeps_a_record_while_it_runs_and_none_after(
    git_project, monkeypatch, ending,
):
    pid = f"pipe-record-{ending}"
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)
    # A real live process other than this one stands in for the engine, so
    # the identity carries a creation time and differs from the owner's.
    engine_pid = os.getppid()
    seen = []

    def _read_record():
        seen.append(read_pipeline_record(project.root, pid))

    kwargs = {"entry_point": ENTRY_POINT_CANVAS} if ending == "success" else {}
    if ending == "user_cancel":
        kwargs["is_cancelled"] = lambda: True
    _run(project, pid, monkeypatch, exit_code=0 if ending == "success" else 1,
         during_engine=_read_record, engine_pid=engine_pid, **kwargs)

    record = seen[0]
    assert record is not None
    assert (record.pipeline_id, record.entry_point, record.cancel_requested) == (
        pid, ENTRY_POINT_CANVAS if ending == "success" else ENTRY_POINT_CLI, False)
    assert record.owner == process_identity(os.getpid())
    assert record.engine == process_identity(engine_pid)
    assert record.engine.created is not None and record.owner.created is not None
    assert read_pipeline_record(project.root, pid) is None
    assert not layout.pipeline_record_path(project.root, pid).exists()


@workflow(purpose="A cancel flag set on the pipeline's record by another "
                  "process while the engine runs reaches run_pipeline's ending "
                  "as a user cancel: every target the pipeline never ran ends "
                  "cancelled for the user, not for the engine's failure")
def test_a_cancel_flag_on_the_record_reaches_the_ending(git_project, monkeypatch):
    pid = "pipe-record-flag"
    口 = Step(step_num=1, name="Build the project; nothing has run",
             purpose="Two samples through a -> b")
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Set the flag while the engine runs, which then exits 1",
             purpose="The canceller writes the record only; the caller's own "
                     "probe never answers")
    _run(project, pid, monkeypatch, exit_code=1,
         during_engine=lambda: request_cancel(project.root, pid))

    口 = Step(step_num=3, name="Every target reads stopped by the user",
             purpose="Without the flag the same ending reads engine_failed")
    rows = _rows(pid)
    assert sorted((r.node_id, r.sample) for r in rows) == [
        ("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")]
    assert {(r.status, r.cancel_reason) for r in rows} == {("cancelled", "user")}
    assert read_pipeline_record(project.root, pid) is None


@workflow(purpose="A pipeline whose record cannot be written is refused before "
                  "the engine starts, with a message naming the record, and no "
                  "run row is written: a pipeline never runs unrecorded")
def test_a_record_that_cannot_be_written_refuses_the_pipeline(git_project, monkeypatch):
    from wfc.execution import run_pipeline

    pid = "pipe-record-unwritable"
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)
    # A directory where the record file goes: no write can replace it.
    layout.pipeline_record_path(project.root, pid).mkdir(parents=True)

    gen_patch, popen_patch = mocked_snakemake(0)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch as popen, pytest.raises(PipelineRecordError) as raised:
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id=pid)

    assert "pipeline record" in str(raised.value)
    assert "not started" in str(raised.value)
    assert not popen.called
    assert _rows(pid) == []


@workflow(purpose="A Ctrl-C while the engine runs cuts run_pipeline's end steps "
                  "short and leaves the pipeline record in place, naming this "
                  "process as the owner, so the recovery can judge the pipeline "
                  "once the process is gone; a close-out of it then writes one "
                  "row per expected target and removes the record")
def test_a_ctrl_c_leaves_the_record_for_the_close_out(git_project, monkeypatch):
    from wfc.execution import close_out_pipeline, run_pipeline

    pid = "pipe-record-ctrl-c"
    口 = Step(step_num=1, name="Build the project; nothing has run",
             purpose="Two samples through a -> b")
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Ctrl-C while waiting on the engine",
             purpose="The engine's wait raises KeyboardInterrupt, which "
                     "run_pipeline lets through")
    gen_patch, popen_patch = mocked_snakemake(1)
    popen_patch.kwargs["return_value"].wait.side_effect = KeyboardInterrupt
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch, pytest.raises(KeyboardInterrupt):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id=pid)

    口 = Step(step_num=3, name="The record is still there",
             purpose="Without it the pipeline would never be closed out")
    record = read_pipeline_record(project.root, pid)
    assert record is not None
    assert record.owner == process_identity(os.getpid())

    口 = Step(step_num=4, name="The close-out writes every expected target",
             purpose="Nothing ran, so every target reads interrupted; the "
                     "record goes")
    close_out_pipeline(project.root, pid)
    rows = _rows(pid)
    assert sorted((r.node_id, r.sample) for r in rows) == [
        ("a", "S1"), ("a", "S2"), ("b", "S1"), ("b", "S2")]
    assert {(r.status, r.cancel_reason) for r in rows} == {("cancelled", "interrupted")}
    assert read_pipeline_record(project.root, pid) is None


def test_the_record_round_trips_its_engine_and_its_flag(tmp_path):
    root = tmp_path
    layout.pipeline_run_dir(root, "p1").mkdir(parents=True)
    assert read_pipeline_record(root, "p1") is None
    assert not cancel_requested(root, "p1")

    written = write_pipeline_record(root, "p1", ENTRY_POINT_CLI)
    assert written.owner == process_identity(os.getpid())
    assert read_pipeline_record(root, "p1") == written

    with_engine = record_engine(root, "p1", os.getppid())
    assert with_engine.engine == process_identity(os.getppid())
    flagged = request_cancel(root, "p1")
    assert flagged.engine == with_engine.engine and flagged.cancel_requested
    assert cancel_requested(root, "p1")
    assert list_pipeline_records(root) == [flagged]

    assert remove_pipeline_record(root, "p1") is True
    assert remove_pipeline_record(root, "p1") is False
    assert record_engine(root, "p1", os.getpid()) is None
    assert request_cancel(root, "p1") is None
    assert list_pipeline_records(root) == []


def test_a_file_that_is_not_a_record_is_refused_and_skipped(tmp_path, capsys):
    root = tmp_path
    layout.pipeline_run_dir(root, "bad").mkdir(parents=True)
    layout.pipeline_record_path(root, "bad").write_text("{not json", encoding="utf-8")
    layout.pipeline_run_dir(root, "good").mkdir(parents=True)
    good = write_pipeline_record(root, "good", ENTRY_POINT_CANVAS)

    with pytest.raises(PipelineRecordError):
        read_pipeline_record(root, "bad")
    assert not cancel_requested(root, "bad")
    assert list_pipeline_records(root) == [good]
    assert "bad" in capsys.readouterr().err


def test_a_gone_process_has_no_creation_time():
    import psutil

    gone = max(psutil.pids()) + 100_000
    assert process_identity(gone).created is None


def test_the_step_map_comes_from_the_frozen_document(tmp_path):
    root = tmp_path
    assert frozen_step_map(root, "p1") is None
    layout.pipeline_run_dir(root, "p1").mkdir(parents=True)
    layout.pipeline_doc_path(root, "p1").write_text(json.dumps({"nodes": [
        {"id": "sel", "type": "input_selector"},
        {"id": "n1", "type": "method", "method": "align"},
    ]}), encoding="utf-8")
    assert frozen_step_map(root, "p1") == {"sel": "", "n1": "align"}
