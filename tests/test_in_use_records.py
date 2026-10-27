"""In-use records keep what a holder uses; collect and record land whole.

A claim records its code copy as in use before it first looks at the copy,
the claim transaction binds the record to the run, and the record phase
releases it in the same transaction as the status flip and the push
enqueue. Prune removes a copy only when no holder has it. Collect writes
every output row in one transaction: a failed write fails the run naming
the slot, and no row of the run survives. The project, its registration and
every run are production's (the harness).
"""

from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.harness import Behavior, Phase, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.persistence import InUseKind, InUseRecord, Run, RunOutput, get_session

S1 = ("a", "s1", "default")
S2 = ("a", "s2", "default")


def _project(tmp_project, monkeypatch, *, outputs=None, behavior=None):
    kwargs = {}
    if outputs is not None:
        kwargs = {"outputs": outputs, "behavior": behavior}
    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)], **kwargs)],
                        samples=["s1", "s2"], pipeline_id="in-use", name="in_use")
    return build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)


def _copy(project):
    from wfc.registration import version_entry

    spec = project.scenario.method_nodes()[0]
    entry = version_entry(spec.module, spec.method_name, root=project.root)
    return layout.code_copy_dir(project.root, entry.tree_hash)


def _records() -> list[InUseRecord]:
    with get_session() as session:
        return list(session.exec(select(InUseRecord)).all())


@workflow(purpose="A prune between a claim's in-use record and its run "
                  "row keeps the code copy, the run executes it, and once the "
                  "run's record phase released the record the copy is pruned")
def test_a_prune_during_a_claim_keeps_the_copy_the_claim_runs(tmp_project, monkeypatch):
    口 = Step(step_num=1, name="Run once",
             purpose="The run extracts the copy; its record phase releases the "
                     "run's in-use record")
    project = _project(tmp_project, monkeypatch)
    first = drive_target(project, S1, monkeypatch=monkeypatch)
    assert first.runs[S1].rc == 0
    copy = _copy(project)
    assert copy.is_dir()
    assert _records() == []

    口 = Step(step_num=2, name="Prune inside the next claim",
             purpose="The prune runs after the claim's last look at its copy "
                     "and before its run row: the claim's record keeps the copy")
    pruned = fakes.prune_during_claim(monkeypatch)
    again = drive_target(project, S2, monkeypatch=monkeypatch)
    assert pruned == [[]]
    assert copy.is_dir()
    assert again.runs[S2].rc == 0
    with get_session() as session:
        row = session.get(Run, again.run_id_sidecars[S2])
        assert row.status == "completed"
    assert _records() == []

    口 = Step(step_num=3, name="Prune after the run",
             purpose="No holder is left, so the copy goes")
    from wfc.storage import prune_code_copies

    assert prune_code_copies(project.root) == [copy]
    assert not copy.exists()


@workflow(purpose="A claim served from the cache runs no code: its hit transaction "
                  "releases the code-copy record the claim held, so no record "
                  "is left for its token")
def test_a_cache_hit_claim_leaves_no_in_use_record(tmp_project, monkeypatch):
    口 = Step(step_num=1, name="Run once",
             purpose="The run's record phase releases its record")
    project = _project(tmp_project, monkeypatch)
    first = drive_target(project, S1, monkeypatch=monkeypatch)
    assert first.runs[S1].rc == 0
    assert _records() == []

    口 = Step(step_num=2, name="Claim the same step again",
             purpose="A cache hit: the audit row names the first run, and the "
                     "record the claim held before it composed its key is gone")
    again = drive_target(project, S1, monkeypatch=monkeypatch)
    assert again.exit_code(S1) == 0
    assert again.run_row(S1)["cache_source_run_id"] == first.runs[S1].run_id
    assert _records() == []


@workflow(purpose="A claim refused after it held its code copy (pre_run refuses "
                  "a dirty working tree) releases the record, so no record is "
                  "left for its token and no run row is written")
def test_a_refused_claim_leaves_no_in_use_record(tmp_project, monkeypatch):
    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1"], pipeline_id="in-use", name="in_use",
                        dirty_repo=True)
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    obs = drive_target(project, S1, monkeypatch=monkeypatch)
    assert obs.exit_code(S1) != 0
    assert obs.phases_ran(S1) == ["claim"]
    assert obs.run_rows == []
    assert _records() == []


@workflow(purpose="Collect: a failed write of one output row fails the "
                  "run with the unrecorded-output ending naming the slot, and "
                  "none of the run's output rows is written")
def test_a_failed_output_row_write_fails_the_run_and_writes_no_row(tmp_project, monkeypatch):
    project = _project(tmp_project, monkeypatch, outputs={"x": "file", "y": "file"},
                       behavior=Behavior(outputs={"x": "id\n1\n", "y": "id\n2\n"}))
    failed = fakes.fault_at_output_row_write(monkeypatch, RuntimeError("database went away"))
    obs = drive_target(project, S1, monkeypatch=monkeypatch)
    assert len(failed) == 1
    assert obs.exit_code(S1) != 0
    row = obs.run_row(S1)
    assert row["status"] == "failed"
    assert f"slot '{failed[0]}'" in row["error_message"]
    assert "could not record" in row["error_message"]
    assert obs.output_rows_for(S1) == []


@workflow(purpose="Record: a failure inside the record transaction keeps "
                  "none of it -- the run stays running, its outputs unqueued and "
                  "its in-use record held -- and the record lands whole once the "
                  "write succeeds")
def test_a_failed_record_write_lands_none_of_the_record(tmp_project, monkeypatch):
    from wfc.execution.record import run_record
    from wfc.storage import first_push_status

    口 = Step(step_num=1, name="Run through collect",
             purpose="The run row is running, its output row written, its "
                     "code-copy record bound to it")
    project = _project(tmp_project, monkeypatch)
    obs = drive_target(project, S1, monkeypatch=monkeypatch, through=Phase.COLLECT)
    run_id = obs.runs[S1].run_id
    assert [r.run_id for r in _records()] == [run_id]
    with get_session() as session:
        outputs = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        paths = [o.artifact_path for o in outputs]
        unqueued = {o.push_status for o in outputs}
    assert paths

    def record():
        return run_record(ending="completed", run_id=run_id, node_id="a", sample="s1",
                          variant="default", pipeline_id=project.scenario.pipeline_id,
                          outcomes_dir=layout.pipeline_outcomes_dir(
                              project.root, project.scenario.pipeline_id),
                          output_files=paths, metrics={"m": 1})

    口 = Step(step_num=2, name="Fail the record transaction's last write",
             purpose="The flip and the enqueue before it are rolled back")
    with monkeypatch.context() as mp:
        fakes.fault_at_run_release(mp, RuntimeError("release failed"))
        with pytest.raises(RuntimeError, match="release failed"):
            record()
    with get_session() as session:
        assert session.get(Run, run_id).status == "running"
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        assert {o.push_status for o in rows} == unqueued
    assert [r.run_id for r in _records()] == [run_id]

    口 = Step(step_num=3, name="Record again",
             purpose="The flip, the enqueue and the release land together")
    assert record() == 0
    with get_session() as session:
        assert session.get(Run, run_id).status == "completed"
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        assert {o.push_status for o in rows} == {first_push_status(project.root).value}
    assert _records() == []


def test_remove_if_unheld_removes_only_what_no_other_holder_has(tmp_project, monkeypatch):
    """The caller's own record goes first; another holder keeps the path; a raise keeps all."""
    from wfc.storage import held_keys, hold_for_pipeline, remove_if_unheld

    _project(tmp_project, monkeypatch)
    kind = InUseKind.sample_view
    removed: list[str] = []
    hold_for_pipeline(kind, "S", "p1")
    hold_for_pipeline(kind, "S", "p2")
    assert not remove_if_unheld(kind, "S", lambda: removed.append("S"), pipeline_id="p1")
    assert removed == [] and held_keys(kind) == {"S"}
    assert remove_if_unheld(kind, "S", lambda: removed.append("S"), pipeline_id="p2")
    assert removed == ["S"] and held_keys(kind) == set()

    def refuse():
        raise OSError("in use by the OS")

    hold_for_pipeline(kind, "T", "p3")
    with pytest.raises(OSError):
        remove_if_unheld(kind, "T", refuse, pipeline_id="p3")
    assert held_keys(kind) == {"T"}
