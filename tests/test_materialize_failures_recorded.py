"""Materialize reads a sample by its recorded name, and every materialize or
output-collection failure lands on the run row with its message.

Tier 2/3 on the stub rung. Declared fixture deviation: ``_add_sibling``
writes a file beside a restored sample under ``data/samples/<s>/``, which
no production verb writes; it is the state a user's stray file leaves.
``_make_path_absolute`` rewrites one sample row's ``registered_path`` to the
absolute shape rows had before this cycle, which production no longer writes.
``_lose`` deletes bytes a real run or the real archive pass wrote (a run
archive file, a cache object): the state a lost disk or a manual clean-up
leaves, which no production verb produces.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.harness import (
    Behavior,
    Scenario,
    build_project,
    chain,
    drive_target,
    node,
    run_target,
    selector,
    wire,
)

T = ("n1", "s1", "default")
T0 = ("n0", "s1", "default")


def _add_sibling(root: Path, sample: str) -> Path:
    """Declared deviation: a stray file that sorts before the recorded one."""
    from wfc import layout

    sibling = layout.sample_dir(root, sample) / "0_sorts_first.csv"
    sibling.write_text("not the sample\n")
    return sibling


@workflow(purpose="A per-sample reader is handed the file registration recorded "
                  "for its sample even when a sibling in the sample's directory "
                  "sorts before it")
def test_materialize_reads_the_recorded_sample_name(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("recorded_name")
    project = build_project(Scenario(), root=root, monkeypatch=monkeypatch)
    _add_sibling(root, "s1")
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)

    assert obs.exit_code(T) == 0
    paths = [p for ps in obs.phase_args("dispatch", T)["slot_paths"].values() for p in ps]
    assert [Path(p).name for p in paths] == ["s1.csv"]


@workflow(purpose="A collapsed fan-in root is handed, for each bundled sample, "
                  "the file registration recorded, even when a sibling in that "
                  "sample's directory sorts before it")
def test_a_collapsed_fan_in_reads_each_recorded_sample_name(tmp_path_factory,
                                                           monkeypatch):
    root = tmp_path_factory.mktemp("collapsed_recorded_name")
    scenario = Scenario(
        nodes=[selector(fan_mode="in"),
               node("merge", inputs=[wire("sel", target_slot="sources")])],
        samples=["s1", "s2"],
        sample_ready_sentinel=True,
    )
    project = build_project(scenario, root=root, monkeypatch=monkeypatch)
    for s in ("s1", "s2"):
        _add_sibling(root, s)
    obs = drive_target(project, "merge", monkeypatch=monkeypatch)

    bundle = ("merge", "__all__", "default")
    assert obs.exit_code(bundle) == 0
    paths = obs.phase_args("dispatch", bundle)["slot_paths"]["sources"]
    assert [Path(p).name for p in paths] == ["s1.csv", "s2.csv"]


@workflow(purpose="A step whose sample data is missing at materialize ends "
                  "failed with that message on its own run row, not as an "
                  "orphaned run for the pipeline-end walk to flip")
def test_a_materialize_failure_is_recorded_on_the_run_row(tmp_path_factory,
                                                          monkeypatch, capsys):
    口 = Step(step_num=1, name="Run a step whose sample was never restored",
             purpose="The row exists (the claim registered it); the sample's "
                     "directory does not")
    obs = run_target(Scenario(missing_samples=("s1",)), "n1",
                     root=tmp_path_factory.mktemp("missing"),
                     monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The step failed before dispatch",
             purpose="Exit 1, the message printed once, no method launched")
    err = capsys.readouterr().err
    assert obs.exit_code(T) == 1
    assert err.count("sample 's1'") == 1 and "restore_sample" in err
    assert "dispatch" not in obs.phases_ran(T)

    口 = Step(step_num=3, name="The run row carries the message",
             purpose="Failed with the materialize message, not the orphaned-run "
                     "placeholder")
    row = obs.run_row(T)
    assert row["status"] == "failed"
    assert "sample 's1'" in row["error_message"]
    assert "orphaned" not in row["error_message"]


@workflow(purpose="A method that saves an empty directory as an output fails "
                  "its run at collection, naming the slot, and records no "
                  "output row for it")
def test_an_empty_directory_output_is_refused_at_collection(tmp_path_factory,
                                                            monkeypatch):
    scn = Scenario(nodes=[
        selector(),
        node("n1", inputs=[wire("sel")], outputs={"tree": "directory"},
             output_files={"tree": "tree"},
             behavior=Behavior(outputs={"tree": {}})),
    ])
    obs = run_target(scn, "n1", root=tmp_path_factory.mktemp("empty_out"),
                     monkeypatch=monkeypatch)

    assert obs.exit_code(T) == 1
    row = obs.run_row(T)
    assert row["status"] == "failed"
    assert "'tree'" in row["error_message"] and "empty" in row["error_message"]


def _make_path_absolute(root: Path, sample: str) -> None:
    """Declared deviation: the pre-cycle absolute registered_path shape."""
    from sqlmodel import select

    from wfc.persistence import Sample, get_session

    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == sample)).one()
        row.registered_path = str(root / row.registered_path)
        session.add(row)
        session.commit()


@workflow(purpose="A step whose sample row records an absolute restore path "
                  "fails at materialize naming re-registration, by the same "
                  "location rule restore uses")
def test_an_absolute_sample_path_fails_materialize_naming_the_repair(
        tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("absolute_row")
    project = build_project(Scenario(), root=root, monkeypatch=monkeypatch)
    _make_path_absolute(root, "s1")
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)

    assert obs.exit_code(T) == 1
    assert "dispatch" not in obs.phases_ran(T)
    row = obs.run_row(T)
    assert row["status"] == "failed"
    assert "malformed record" in row["error_message"]
    assert "wfc register-sample --name s1" in row["error_message"]


def _lose(path: Path) -> None:
    """Declared deviation: bytes a real run or archive pass wrote go missing."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.unlink()


def _assert_failed_before_dispatch(obs, parent_run_id: int) -> None:
    assert obs.exit_code(T) == 1
    assert "dispatch" not in obs.phases_ran(T)
    row = obs.run_row(T)
    assert row["status"] == "failed"
    assert "'data'" in row["error_message"]
    assert f"parent run {parent_run_id}" in row["error_message"]
    assert "orphaned" not in row["error_message"]


@workflow(purpose="A step whose archived parent output is in neither the local "
                  "cache nor the remote ends failed before dispatch, its own "
                  "row naming the input slot, the parent run, the output's "
                  "content hash and the re-run that repairs it")
def test_an_unresolvable_archived_input_is_recorded_on_the_run_row(
        tmp_path_factory, monkeypatch, capsys):
    from wfc.storage import archive_outputs
    from wfc.storage.cache import _cache_path

    口 = Step(step_num=1, name="Run the parent and archive its output",
             purpose="The real run and the real archive pass record a content "
                     "hash; nothing was pushed, so the remote lacks it")
    root = tmp_path_factory.mktemp("unresolvable_archived")
    project = build_project(Scenario(nodes=chain("n0", "n1")), root=root,
                            monkeypatch=monkeypatch)
    parent = drive_target(project, "n0", monkeypatch=monkeypatch)
    assert parent.exit_code(T0) == 0
    parent_run_id = parent.run_row(T0)["id"]
    [archived] = archive_outputs(root, run_id=parent_run_id)
    assert archived["status"] == "archived"

    口 = Step(step_num=2, name="Lose the cache object",
             purpose="The only local copy the resolver reads for an archived "
                     "row is gone")
    _lose(_cache_path(root, archived["content_hash"]))

    口 = Step(step_num=3, name="The consumer fails before dispatch",
             purpose="Exit 1 with a failed row naming the slot and the parent "
                     "run, not the orphaned-run placeholder; the input "
                     "resolver refuses the output (the rule the hit rule "
                     "reads), and the row carries its reason: the content "
                     "hash, why, and the re-run")
    capsys.readouterr()
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)
    _assert_failed_before_dispatch(obs, parent_run_id)
    message = obs.run_row(T)["error_message"]
    assert archived["content_hash"] in message, message
    assert "never pushed" in message, message
    assert f"Re-run run {parent_run_id}" in message, message
    assert "resolve_input: FAIL" in capsys.readouterr().err


@workflow(purpose="A step whose pre-archive parent output is gone from the run "
                  "archive ends failed before dispatch, its own row naming the "
                  "slot, the parent run, the missing path and the re-run that "
                  "repairs it, rather than launching its method on a missing "
                  "input")
def test_a_missing_pre_archive_input_is_recorded_on_the_run_row(
        tmp_path_factory, monkeypatch, capsys):
    口 = Step(step_num=1, name="Run the parent; lose its run-archive output",
             purpose="The row has no content hash yet, so its run archive is "
                     "the only place its bytes are")
    root = tmp_path_factory.mktemp("missing_pre_archive")
    project = build_project(Scenario(nodes=chain("n0", "n1")), root=root,
                            monkeypatch=monkeypatch)
    parent = drive_target(project, "n0", monkeypatch=monkeypatch)
    assert parent.exit_code(T0) == 0
    parent_run_id = parent.run_row(T0)["id"]
    [out] = parent.output_rows_for(T0)
    assert out["content_hash"] is None
    _lose(Path(out["artifact_path"]))

    口 = Step(step_num=2, name="The consumer fails before dispatch",
             purpose="Exit 1 with a failed row naming the slot and the parent "
                     "run; the input resolver refuses the gone file itself "
                     "(the rule the hit rule reads), and the row carries its "
                     "reason: the path that is not there and the re-run")
    capsys.readouterr()
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)
    _assert_failed_before_dispatch(obs, parent_run_id)
    message = obs.run_row(T)["error_message"]
    assert Path(out["artifact_path"]).name in message, message
    assert f"Re-run run {parent_run_id}" in message, message
    assert "resolve_input: FAIL" in capsys.readouterr().err
