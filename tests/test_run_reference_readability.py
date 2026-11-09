"""A reference reads the run it names wherever wfc keeps that run's outputs.

Witnesses for two cases of the reference resolver
(``wfc.storage.resolve_run_reference_outputs``) and Graph's reference-link
rule (``wfc.graph.reference_link_error``):

* **A reference to a cache-hit run** reads the reused run's outputs: the
  audit row a cache hit writes owns no output records, so the resolver
  follows it to the run whose outputs it reused, and the consumer reads
  those bytes.
* **A reference whose output wfc can no longer read** -- not in the cache,
  not in the run folder, not on the remote -- is refused before anything
  runs, in one sentence, by check-pipeline, by ``run_pipeline`` before any
  run row, and by the claim's backstop. Only ``missing`` is refused: an
  output recorded pushed is pulled at run time, and a run with one missing
  output still serves a link to its readable one.

State is built through production paths: runs by the harness's five-phase
drive, stored runs by Storage's archive pass, cache hits by the claim, a
pushed output by the production push worker. Declared deviation: a cache
entry is removed from disk the way ``dvc gc`` leaves it.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.harness import Scenario, build_project, node, reference, run_target, selector, wire

SRC = ("src", "s1", "default")
SINK = ("sink", "s1", "default")


def _producer(outputs: dict[str, str] | None = None) -> Scenario:
    """The referenced run's own pipeline: a selector feeding ``src``."""
    return Scenario(
        nodes=[selector(), node("src", inputs=[wire("sel")],
                                outputs=outputs or {"data": ".csv"})],
        pipeline_id="producer", name="producer")


def _consumer(run_id: int, *, source_slot: str | None = None,
              outputs: dict[str, str] | None = None) -> Scenario:
    """A second pipeline whose ``sink`` reads run ``run_id`` through ``ref``."""
    return Scenario(
        nodes=[selector(), node("src", inputs=[wire("sel")],
                                outputs=outputs or {"data": ".csv"}),
               reference("ref", run_id=str(run_id)),
               node("sink", inputs=[wire("ref", source_slot=source_slot)])],
        pipeline_id="consumer", name="consumer")


def _records(run_id: int) -> dict[str, tuple[str | None, str]]:
    """``{slot: (content_hash, artifact_path)}`` of a run's own output records."""
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        return {r.slot: (r.content_hash, r.artifact_path) for r in session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id))}


def _archive(root: Path, run_id: int) -> None:
    """Storage's archive pass stores the run's outputs; the run folder empties."""
    from wfc.storage import archive_outputs

    archive_outputs(root, run_id=run_id)
    for content_hash, path in _records(run_id).values():
        assert content_hash and not Path(path).exists()


def _push(root: Path, run_id: int) -> None:
    """The production push worker sends the run's entries to the remote."""
    from wfc.persistence import PushStatus
    from wfc.storage.push_worker import _push_worker_tick

    _push_worker_tick(root)
    assert all(_push_status(run_id, slot) == PushStatus.pushed.value
               for slot in _records(run_id))


def _push_status(run_id: int, slot: str) -> str | None:
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        return session.exec(select(RunOutput.push_status).where(
            RunOutput.run_id == run_id, RunOutput.slot == slot)).one()


def _prune(root: Path, run_id: int, slot: str) -> None:
    """Declared deviation: the output's cache entry is gone, as ``dvc gc`` leaves it."""
    from wfc import layout

    entry = layout.dvc_cache_entry(root, _records(run_id)[slot][0])
    os.chmod(entry, stat.S_IREAD | stat.S_IWRITE)
    entry.unlink()


def _run_count() -> int:
    from wfc.persistence import Run, get_session

    with get_session() as session:
        return len(session.exec(select(Run)).all())


def _unreadable(run_id: int, slot: str) -> str:
    """The one sentence a link to an unreadable output is refused with."""
    return (f"Reference 'ref' reads output '{slot}' of run {run_id} (src), "
            f"which wfc can no longer read from the cache, the run folder or "
            f"the remote; re-run that step and select the new run.")


# =============================================================================
# A reference to a cache-hit run
# =============================================================================

@workflow(purpose="A reference naming a cache-hit run reads the reused run's "
                  "outputs: the load binds the reused run's recorded path, "
                  "the reference read serves its stored bytes, and the "
                  "consumer runs on them")
def test_a_reference_to_a_cache_hit_run_reads_the_reused_runs_outputs(
        git_project, monkeypatch):
    from wfc.execution import load_pipeline_from_path
    from wfc.storage.resolve import reference_read_path

    口 = Step(step_num=1, name="Run src, store it, then hit the cache",
             purpose="The first drive writes the outputs and the archive pass "
                     "stores them; the second drive of the same step is a "
                     "cache hit, whose audit row owns no output records")
    producer = _producer()
    source = run_target(producer, "src", root=git_project,
                        monkeypatch=monkeypatch).runs[SRC].run_id
    written = Path(_records(source)["data"][1]).read_bytes()
    _archive(git_project, source)
    hit = run_target(producer, "src", root=git_project, monkeypatch=monkeypatch)
    audit = hit.runs[SRC].run_id
    assert hit.runs[SRC].ending == "cached"
    assert hit.run_row(SRC)["cache_source_run_id"] == source
    assert _records(audit) == {}

    口 = Step(step_num=2, name="Load a pipeline whose reference names the hit",
             purpose="The composer's load binds the reused run's recorded "
                     "path onto the consumer's slot")
    project = build_project(_consumer(audit), root=git_project,
                            monkeypatch=monkeypatch)
    sink = next(s for s in load_pipeline_from_path(project.pipeline_json).steps
                if s.node_id == "sink")
    assert sink.run_ref_inputs == {"data": [_records(source)["data"][1]]}

    口 = Step(step_num=3, name="The reference read serves the stored bytes",
             purpose="The path names the reused run's output, read from the "
                     "cache now that the run folder is empty")
    read = Path(reference_read_path(sink.run_ref_inputs["data"][0]))
    assert read.is_relative_to(git_project / ".dvc" / "cache")
    assert read.read_bytes() == written

    口 = Step(step_num=4, name="The consumer runs on them",
             purpose="Claim, materialize, dispatch, collect and record: the "
                     "slot the method reads holds the reused run's bytes")
    obs = run_target(_consumer(audit), "sink", root=git_project,
                     monkeypatch=monkeypatch)
    assert obs.exit_code(SINK) == 0
    slot_paths = obs.phase_args("dispatch", SINK)["slot_paths"]
    assert [Path(p).read_bytes() for p in slot_paths["data"]] == [written]
    assert obs.run_row(SINK)["status"] == "completed"


# =============================================================================
# A reference whose output wfc can no longer read
# =============================================================================

@workflow(purpose="A reference whose output is in neither the cache, the run "
                  "folder nor the remote is refused in one sentence naming "
                  "the reference, the output and the run, by check-pipeline, "
                  "by run_pipeline before any run row is written, and by the "
                  "claim's backstop")
def test_a_reference_to_an_output_wfc_cannot_read_is_refused_before_anything_runs(
        git_project, monkeypatch, capsys):
    from wfc.execution.claim import run_claim
    from wfc.execution.pipeline import run_pipeline
    from wfc.execution.validation import PipelineDocumentError, check_pipeline

    from tests.fixtures.fakes.engine import fake_engine_process

    口 = Step(step_num=1, name="Store a run, then lose its output everywhere",
             purpose="The archive pass empties the run folder; the cache "
                     "entry is then gone and the output was never pushed")
    source = run_target(_producer(), "src", root=git_project,
                        monkeypatch=monkeypatch).runs[SRC].run_id
    _archive(git_project, source)
    _prune(git_project, source, "data")
    project = build_project(_consumer(source), root=git_project,
                            monkeypatch=monkeypatch)
    rows_before = _run_count()

    口 = Step(step_num=2, name="check-pipeline refuses it",
             purpose="The verdict is not valid and carries the sentence")
    verdict = check_pipeline(project.pipeline_json, project.root)
    assert verdict["valid"] is False
    assert _unreadable(source, "data") in verdict["errors"]

    口 = Step(step_num=3, name="run_pipeline refuses it before any run row",
             purpose="The composer's load is run_pipeline's first step; the "
                     "engine is never launched")
    launched: list = []
    with fake_engine_process(only_snakemake=True,
                             launch=lambda argv, **_: launched.append(argv)):
        with pytest.raises(PipelineDocumentError) as refused:
            run_pipeline(str(project.pipeline_json),
                         project_root=str(project.root))
    assert str(refused.value) == _unreadable(source, "data")
    assert launched == []
    assert _run_count() == rows_before

    口 = Step(step_num=4, name="The claim's backstop refuses it too",
             purpose="A direct claim, with no load before it, asks the same "
                     "rule and writes no row")
    capsys.readouterr()
    result = run_claim(node_id="sink", sample="s1", variant="default",
                       method_name=None, module_name=None,
                       params=None, parent_run_ids=None,
                       pipeline_id=project.pipeline_id,
                       pipeline_json=str(project.pipeline_json), git_commit=None)
    assert result == {"ok": False, "rc": 1}
    assert _unreadable(source, "data") in capsys.readouterr().err
    assert _run_count() == rows_before


@workflow(purpose="Only an output wfc can read from nowhere is refused: an "
                  "output recorded pushed but gone locally loads (it is "
                  "pulled at run time), and a run with one missing output "
                  "still serves a link to its readable one while a link to "
                  "the missing one is refused")
def test_only_the_missing_output_a_link_uses_is_refused(git_project, monkeypatch):
    from wfc.execution import load_pipeline_from_path
    from wfc.execution.validation import PipelineDocumentError

    two = {"left": ".csv", "right": ".csv"}

    口 = Step(step_num=1, name="Store a two-output run, push it, lose one locally",
             purpose="The left output is recorded pushed and absent from the "
                     "cache: remote, not missing")
    source = run_target(_producer(two), "src", root=git_project,
                        monkeypatch=monkeypatch).runs[SRC].run_id
    _archive(git_project, source)
    _push(git_project, source)
    _prune(git_project, source, "left")

    口 = Step(step_num=2, name="A link to the remote output loads",
             purpose="Nothing is pulled at load; the run-time read pulls it")
    project = build_project(_consumer(source, source_slot="left", outputs=two),
                            root=git_project, monkeypatch=monkeypatch)
    sink = next(s for s in load_pipeline_from_path(project.pipeline_json).steps
                if s.node_id == "sink")
    assert sink.run_ref_inputs == {"data": [_records(source)["left"][1]]}

    口 = Step(step_num=3, name="Make one output missing",
             purpose="A second run of the same shape, stored and never "
                     "pushed, loses its right output's cache entry")
    other = Scenario(nodes=[selector(), node("src", inputs=[wire("sel")],
                                             outputs=two, params={"k": 2})],
                     pipeline_id="producer", name="producer")
    second = run_target(other, "src", root=git_project,
                        monkeypatch=monkeypatch).runs[SRC].run_id
    _archive(git_project, second)
    _prune(git_project, second, "right")

    口 = Step(step_num=4, name="The readable output still serves its link",
             purpose="Only the output a link uses is checked")
    project = build_project(_consumer(second, source_slot="left", outputs=two),
                            root=git_project, monkeypatch=monkeypatch)
    sink = next(s for s in load_pipeline_from_path(project.pipeline_json).steps
                if s.node_id == "sink")
    assert sink.run_ref_inputs == {"data": [_records(second)["left"][1]]}

    口 = Step(step_num=5, name="The missing output's link is refused",
             purpose="The same run, the link naming the output wfc cannot read")
    project = build_project(_consumer(second, source_slot="right", outputs=two),
                            root=git_project, monkeypatch=monkeypatch)
    with pytest.raises(PipelineDocumentError) as refused:
        load_pipeline_from_path(project.pipeline_json)
    assert str(refused.value) == _unreadable(second, "right")
