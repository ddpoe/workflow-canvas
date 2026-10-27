"""``wfc cache prune`` removes the extracted code copies no running run uses.

A run executes its method version's copy under ``.wfc/runs/code/<tree>/``.
The copy is derived from git, so the prune removes it (and its stamp) when no
``running`` run's version names its tree, and a later run extracts it again.
The project, its registration and every run are production's (the harness).
"""

from __future__ import annotations

import importlib

import pytest
from axiom_annotations import Step, workflow

from tests.harness import Phase, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.persistence import Run, get_session


def _project(tmp_project, monkeypatch):
    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1", "s2"], pipeline_id="prune-code",
                        name="prune_code")
    return build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)


def _copy_and_stamp(project):
    from wfc.registration import version_entry

    spec = project.scenario.method_nodes()[0]
    entry = version_entry(spec.module, spec.method_name, root=project.root)
    return (layout.code_copy_dir(project.root, entry.tree_hash),
            layout.code_copy_stamp(project.root, entry.tree_hash))


@workflow(purpose="wfc cache prune removes an extracted code copy no running "
                  "run uses, with its stamp; the next run of the method "
                  "extracts the copy again and completes")
def test_cache_prune_removes_an_unused_copy_and_the_next_run_extracts_it_again(
        tmp_project, monkeypatch, capsys):
    from wfc.storage import cache_prune

    口 = Step(step_num=1, name="Run once",
             purpose="The run extracts the version's copy and stamps it")
    project = _project(tmp_project, monkeypatch)
    first = drive_target(project, ("a", "s1", "default"), monkeypatch=monkeypatch)
    assert first.runs[("a", "s1", "default")].rc == 0
    copy, stamp = _copy_and_stamp(project)
    assert copy.is_dir() and stamp.is_file()

    口 = Step(step_num=2, name="Prune",
             purpose="No run is running: the copy and its stamp are removed and "
                     "listed")
    capsys.readouterr()
    assert cache_prune(force=True) == 0
    out = capsys.readouterr().out
    assert "Unused code copies to remove: 1" in out
    assert str(copy) in out
    assert not copy.exists() and not stamp.exists()

    口 = Step(step_num=3, name="Run a new target",
             purpose="The run extracts the copy again, stamps it, and completes")
    again = drive_target(project, ("a", "s2", "default"), monkeypatch=monkeypatch)
    assert again.runs[("a", "s2", "default")].rc == 0
    with get_session() as session:
        row = session.get(Run, again.run_id_sidecars[("a", "s2", "default")])
        assert row.status == "completed"
    assert copy.is_dir() and stamp.is_file()


@workflow(purpose="A copy whose tree a running run's version names is kept by "
                  "the prune; a stamp left without its copy is removed")
def test_prune_keeps_the_copy_a_running_run_uses(tmp_project, monkeypatch):
    from tests.fixtures.fakes import interpose_phases
    from tests.harness.drivers import _StopPhase
    from tests.harness.observe import TargetRun
    from wfc.storage import prune_code_copies

    project = _project(tmp_project, monkeypatch)
    drive_target(project, ("a", "s1", "default"), monkeypatch=monkeypatch)
    copy, stamp = _copy_and_stamp(project)
    orphan = layout.code_copy_stamp(project.root, "0" * 40)
    orphan.write_text("stale\n", encoding="utf-8")

    # A claim of s2 leaves its run row 'running' (stopped after Claim).
    run_step_mod = importlib.import_module("wfc.execution.run_step")
    record = TargetRun(target=("a", "s2", "default"))
    with interpose_phases(run_step_mod, record, Phase.CLAIM, monkeypatch):
        with pytest.raises(_StopPhase):
            run_step_mod.run_step(node_id="a", sample="s2",
                                  pipeline_id=project.scenario.pipeline_id,
                                  pipeline_json=str(project.pipeline_json))
    with get_session() as session:
        assert session.get(Run, record.run_id).status == "running"

    assert prune_code_copies(project.root) == []
    assert copy.is_dir() and stamp.is_file()
    assert not orphan.exists()


def test_unreachable_remote_guard_still_removes_unused_copies(
        tmp_project, monkeypatch, capsys):
    """The guard holds back archives and cache entries, never code copies.

    With the archive gone (the remote reads as unreachable) and no
    ``--force``, the prune exits 1 and leaves an orphan run archive in
    place, yet removes the unused copy and its stamp without a prompt.
    """
    import shutil

    from tests.fixtures.conftest import project_archive_dir
    from wfc.storage import cache_prune
    from wfc.storage.cache import _make_writable

    project = _project(tmp_project, monkeypatch)
    drive_target(project, ("a", "s1", "default"), monkeypatch=monkeypatch)
    copy, stamp = _copy_and_stamp(project)
    orphan = layout.artifact_store(project.root) / "00099999"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "output.csv").write_text("orphan data", encoding="utf-8")
    archive = project_archive_dir(project.root)
    if archive.exists():
        _make_writable(archive)
        shutil.rmtree(archive)
    # The guard path asks nothing. pytest's captured stdin raises on a read,
    # and a prompt that read nothing would abort and keep the copy; input()
    # also prints its prompt, which the captured output must not hold.
    capsys.readouterr()
    assert cache_prune() == 1

    captured = capsys.readouterr()
    assert "unreachable" in (captured.out + captured.err).lower()
    assert "Proceed?" not in captured.out
    assert not copy.exists() and not stamp.exists()
    assert orphan.is_dir()
