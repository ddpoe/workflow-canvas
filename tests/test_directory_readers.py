"""Readers of a directory entry treat it as local only when it is whole.

Declared fixture deviations:

- ``_drop_member`` removes one member object by hand after a production
  registration, which is the state an interrupted pull leaves and which
  production cannot produce on demand;
- ``_drop_output_rows`` deletes a run's ``RunOutput`` rows, because no
  production verb removes a run's references and the prune reads exactly
  those rows.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from axiom_annotations import Step, workflow
from sqlmodel import select

from wfc import layout
from wfc.persistence import RunOutput, Sample, get_session
from wfc.registration import register_sample
from wfc.registration.sample_health import classify_sample
from wfc.storage.cache import entry_is_complete, manifest_members
from tests.fixtures.routes import sample_source_dir


def _dir_source(root: Path) -> Path:
    for rel, data in {"a.csv": b"a\n", "sub/b.csv": b"b\n"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _drop_member(project: Path, dir_hash: str) -> None:
    """Declared deviation: one member object removed, as an interrupted pull leaves."""
    member = sorted(manifest_members(project, dir_hash))[0]
    obj = layout.dvc_cache_entry(project, member)
    os.chmod(obj, stat.S_IWRITE)
    obj.unlink()


@workflow(purpose="A directory sample whose manifest is local but one member is "
                  "missing is not cached: its health drops from local-only to "
                  "unreachable instead of reading as a usable local copy")
def test_a_partial_directory_entry_is_not_cached(tmp_project, monkeypatch):
    """D-2 item 1 (Architect row: health of a partial entry)."""
    _ = Step(step_num=1, name="Register a directory sample, unpushed",
             purpose="Inside a pipeline, so the row stays pending (local only)")
    monkeypatch.setenv("WFC_PIPELINE_ID", "partial")
    register_sample(name="tree", source_path=_dir_source(sample_source_dir(tmp_project) / "tree"),
                    project_root=tmp_project)
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "tree")).one()
    assert classify_sample(row, tmp_project).state == "local_only"

    _ = Step(step_num=2, name="Lose one member object",
             purpose="The manifest stays; the entry is now partial")
    _drop_member(tmp_project, row.content_hash)
    assert layout.dvc_cache_entry(tmp_project, row.content_hash).is_file()

    _ = Step(step_num=3, name="The sample is not cached",
             purpose="Not pushed and not whole locally: unreachable")
    assert not entry_is_complete(tmp_project, row.content_hash)
    assert classify_sample(row, tmp_project).state == "unreachable"


# ---------------------------------------------------------------------------
# Directory outputs: resolver pull and prune member expansion
# ---------------------------------------------------------------------------

def _dir_output_run(project: Path, monkeypatch, method: str, files: dict[str, str]):
    """A completed run whose one directory output ``tree`` holds ``files``, archived."""
    from wfc.storage import archive_outputs
    from tests.fixtures.routes import completed_run
    from tests.harness.scenario import Behavior

    run = completed_run(
        project, monkeypatch=monkeypatch, method=method, module="mod", sample="s",
        outputs={"tree": "directory"}, output_files={"tree": "tree"},
        behavior=Behavior(outputs={"tree": files}),
    )
    archive_outputs(project, run_id=run.run_id)
    with get_session() as session:
        (row,) = session.exec(select(RunOutput).where(RunOutput.run_id == run.run_id)).all()
    assert row.content_hash and row.content_hash.endswith(".dir"), row.content_hash
    return run, row


def _drop_output_rows(run_id: int) -> None:
    """Declared deviation: no production verb deletes a run's output rows.

    Removing them is what "this directory is no longer referenced" means to
    the prune's reference query.
    """
    with get_session() as session:
        for row in session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all():
            session.delete(row)
        session.commit()


@workflow(purpose="A directory output whose local entry has lost a member is "
                  "pulled back whole by the output resolver, which then serves "
                  "its checkout, rather than serving the partial entry")
def test_a_resolver_pulls_a_partial_directory_entry(tmp_project, monkeypatch):
    """D-2 item 1 (Architect row: health of a partial entry), resolver leg."""
    from wfc.persistence import PushStatus
    from wfc.storage import resolve_output
    from wfc.storage.push_worker import _push_worker_tick

    _ = Step(step_num=1, name="Archive a directory output and push it",
             purpose="The push worker sends the manifest and every member to "
                     "the real local remote and records the row pushed, the "
                     "record a resolver pulls on")
    run, row = _dir_output_run(tmp_project, monkeypatch, "m1",
                               {"a.csv": "a\n", "b.csv": "b\n"})
    _push_worker_tick(tmp_project)
    with get_session() as session:
        assert session.get(RunOutput, row.id).push_status == PushStatus.pushed.value

    _ = Step(step_num=2, name="Lose one member object locally",
             purpose="The manifest stays; the entry is partial")
    _drop_member(tmp_project, row.content_hash)
    assert not entry_is_complete(tmp_project, row.content_hash)

    _ = Step(step_num=3, name="Resolve the output with pull allowed",
             purpose="The resolver pulls the missing member, then serves the checkout")
    path, _ro = resolve_output(run.run_id, "tree", pull=True, project_dir=tmp_project)
    assert entry_is_complete(tmp_project, row.content_hash)
    assert path == layout.checkout_dir(tmp_project, row.content_hash)
    assert {p.name: p.read_text() for p in path.iterdir()} == {"a.csv": "a\n", "b.csv": "b\n"}


@workflow(purpose="Two directory outputs share one file: the cache holds its object "
                  "once, and pruning one directory's references keeps the shared "
                  "object and every member of the live directory")
def test_prune_keeps_the_members_of_a_live_directory(tmp_project, monkeypatch):
    """P3-12, prune half (the stored-once half is in test_directory_cache_entry)."""
    from wfc.storage import local_path, prune_dvc_cache

    _ = Step(step_num=1, name="Two runs archive directories sharing one file",
             purpose="Each directory also has a member of its own")
    run_a, row_a = _dir_output_run(tmp_project, monkeypatch, "m1",
                                   {"shared.csv": "same\n", "only_a.csv": "a\n"})
    _run_b, row_b = _dir_output_run(tmp_project, monkeypatch, "m2",
                                    {"shared.csv": "same\n", "only_b.csv": "b\n"})
    members_a = manifest_members(tmp_project, row_a.content_hash)
    members_b = manifest_members(tmp_project, row_b.content_hash)
    shared = members_a & members_b
    assert len(shared) == 1
    checkout_a = local_path(tmp_project, row_a.content_hash)
    assert checkout_a is not None and checkout_a.is_dir()

    _ = Step(step_num=2, name="Drop directory A's references and prune",
             purpose="force bypasses the unpushed guard, so only references keep entries")
    _drop_output_rows(run_a.run_id)
    prune_dvc_cache(tmp_project, dry_run=False, force=True)

    _ = Step(step_num=3, name="The live directory is whole; A is gone",
             purpose="Shared object kept; A's manifest, own member and checkout removed")
    assert entry_is_complete(tmp_project, row_b.content_hash)
    (shared_md5,) = shared
    assert layout.dvc_cache_entry(tmp_project, shared_md5).is_file()
    assert not layout.dvc_cache_entry(tmp_project, row_a.content_hash).exists()
    for own_a in members_a - shared:
        assert not layout.dvc_cache_entry(tmp_project, own_a).exists()
    assert not checkout_a.exists()
    assert not layout.checkout_stamp(tmp_project, row_a.content_hash).exists()
