"""Every cache entry is pushed on its own: a failed entry fails alone.

The push worker's tick gives each entry its own transfer, outcome and error.
These witnesses run against the real local remote ``wfc init`` configures
(``tests.fixtures.routes.project_archive_dir``); no transport is faked.

Declared fixture deviations (both corrupt the local cache by hand after a
production registration, because production can no longer produce either
state):

- an entry whose cache object is missing (``_drop_cache_object``), the
  real-means failure for the per-entry witness;
- a directory tree laid at an unsuffixed address (``_lay_tree_at``), the
  pre-cycle directory shape a malformed cache entry has.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from wfc import layout
from wfc.persistence import PushStatus, RunOutput, Sample, get_session
from wfc.registration import register_sample
from tests.fixtures.routes import completed_run, project_archive_dir, sample_source_dir


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _register(project: Path, name: str, source: Path) -> Sample:
    register_sample(name=name, source_path=source, project_root=project)
    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


def _force_remove(p: Path) -> None:
    def _onexc(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if p.is_dir():
        for q in p.rglob("*"):
            os.chmod(q, stat.S_IWRITE)
        os.chmod(p, stat.S_IWRITE)
        shutil.rmtree(p, onexc=_onexc)
    elif p.exists():
        os.chmod(p, stat.S_IWRITE)
        p.unlink()


def _drop_cache_object(project: Path, md5: str) -> None:
    """Declared deviation: remove an entry's local object by hand."""
    _force_remove(layout.dvc_cache_entry(project, md5))


def _lay_tree_at(project: Path, md5: str) -> Path:
    """Declared deviation: the pre-cycle directory shape at an unsuffixed address."""
    address = layout.dvc_cache_entry(project, md5)
    _force_remove(address)
    _write(address, {"a.csv": b"old shape\n"})
    return address


def _remote_has(project: Path, md5: str) -> bool:
    return (project_archive_dir(project) / "files" / "md5" / md5[:2] / md5[2:]).is_file()


def _tick(project: Path) -> tuple[int, int]:
    from wfc.storage.push_worker import _push_worker_tick
    return _push_worker_tick(project)


def _sample_rows() -> dict[str, Sample]:
    with get_session() as session:
        return {s.name: s for s in session.exec(select(Sample)).all()}


@workflow(purpose="One push tick with a failing entry among good ones: the good "
                  "rows are pushed and reach the remote; only the failing row is "
                  "failed, with its own entry's error")
def test_a_failed_entry_fails_alone(tmp_project, monkeypatch):
    """P3-3. The failure is produced by real means: the entry's object is gone."""
    _ = Step(step_num=1, name="Register three samples inside a pipeline",
             purpose="WFC_PIPELINE_ID leaves each row pending for the worker")
    monkeypatch.setenv("WFC_PIPELINE_ID", "per-entry")
    src = sample_source_dir(tmp_project)
    rows = {
        name: _register(tmp_project, name, _write(src, {f"{name}.csv": data}) / f"{name}.csv")
        for name, data in (("good_a", b"a\n1\n"), ("good_b", b"b\n2\n"), ("broken", b"c\n3\n"))
    }
    assert {r.push_status for r in rows.values()} == {PushStatus.pending.value}

    _ = Step(step_num=2, name="Lose one entry's local object",
             purpose="The broken entry cannot be pushed by any transport")
    _drop_cache_object(tmp_project, rows["broken"].content_hash)

    _ = Step(step_num=3, name="Tick the worker once", purpose="The real transport")
    pushed, remaining = _tick(tmp_project)

    _ = Step(step_num=4, name="Only the broken row failed",
             purpose="Good rows pushed with no error; the broken row names its own entry")
    after = _sample_rows()
    assert (pushed, remaining) == (2, 1)
    for name in ("good_a", "good_b"):
        assert after[name].push_status == PushStatus.pushed.value, name
        assert after[name].push_error is None
        assert _remote_has(tmp_project, after[name].content_hash)
    broken = after["broken"]
    assert broken.push_status == PushStatus.failed.value
    assert broken.push_attempts == 1
    assert rows["broken"].content_hash in broken.push_error
    assert "not complete in the local cache" in broken.push_error
    assert not _remote_has(tmp_project, broken.content_hash)


@workflow(purpose="A file entry and a directory entry pushed in one tick both reach "
                  "a real local remote: the file object, the directory's manifest "
                  "and every member")
def test_a_file_and_a_directory_entry_both_reach_the_remote(tmp_project, monkeypatch):
    """The old batch's collateral case, now two independent transfers."""
    from wfc.storage.cache import manifest_members

    _ = Step(step_num=1, name="Register a file and a directory sample in a pipeline",
             purpose="Both rows pending for the worker")
    monkeypatch.setenv("WFC_PIPELINE_ID", "file-and-dir")
    src = sample_source_dir(tmp_project)
    file_row = _register(tmp_project, "plain", _write(src, {"plain.csv": b"x\n1\n"}) / "plain.csv")
    dir_row = _register(tmp_project, "tree", _write(src / "tree", {
        "a.csv": b"a\n", "sub/b.csv": b"b\n", ".hidden": b"h\n"}))
    assert dir_row.content_hash.endswith(".dir")

    _ = Step(step_num=2, name="Tick the worker once", purpose="The real transport")
    pushed, remaining = _tick(tmp_project)

    _ = Step(step_num=3, name="Both entries are on the remote",
             purpose="The directory went up as DVC's shape: manifest plus members")
    assert (pushed, remaining) == (2, 0)
    after = _sample_rows()
    assert {after["plain"].push_status, after["tree"].push_status} == {PushStatus.pushed.value}
    assert _remote_has(tmp_project, file_row.content_hash)
    assert _remote_has(tmp_project, dir_row.content_hash)
    members = manifest_members(tmp_project, dir_row.content_hash)
    assert len(members) == 3
    assert all(_remote_has(tmp_project, m) for m in members)


@workflow(purpose="A directory tree at an unsuffixed cache address is refused on "
                  "push and on pull as a malformed cache entry: a sample's refusal names "
                  "re-registration, a run output's names re-running; nothing is "
                  "converted")
def test_a_malformed_entry_is_refused_on_push_and_pull(tmp_project, monkeypatch):
    """P3-4, push and pull legs (the restore leg is in test_directory_cache_entry)."""
    from wfc.storage import archive_outputs
    from wfc.storage.cache import MalformedEntryError
    from wfc.storage.transport import pull_cache

    _ = Step(step_num=1, name="A sample and a run output, each pending",
             purpose="Both rows through production paths")
    monkeypatch.setenv("WFC_PIPELINE_ID", "malformed")
    src = sample_source_dir(tmp_project)
    sample = _register(tmp_project, "old_dir", _write(src, {"old.csv": b"o\n"}) / "old.csv")
    run = completed_run(tmp_project, monkeypatch=monkeypatch, method="m1", module="mod",
                        sample="s", pipeline_id="p1", outputs={"output": ".parquet"})
    archive_outputs(tmp_project, run_id=run.run_id)
    with get_session() as session:
        ro = session.exec(select(RunOutput).where(RunOutput.run_id == run.run_id)).one()
        ro.push_status = PushStatus.pending.value
        session.add(ro)
        session.commit()
        output_hash = ro.content_hash

    _ = Step(step_num=2, name="Both entries take the pre-cycle directory shape",
             purpose="A tree at each unsuffixed address")
    sample_tree = _lay_tree_at(tmp_project, sample.content_hash)
    output_tree = _lay_tree_at(tmp_project, output_hash)

    _ = Step(step_num=3, name="Push refuses each with its kind's repair",
             purpose="The rows fail as malformed cache entries; nothing reaches the remote")
    _pushed, remaining = _tick(tmp_project)  # the run's own sample "s" pushes
    assert remaining == 2
    with get_session() as session:
        s_err = session.get(Sample, sample.id).push_error
        o_err = session.exec(select(RunOutput).where(RunOutput.run_id == run.run_id)).one().push_error
    assert "malformed cache entry" in s_err and "Re-register the sample" in s_err
    assert "malformed cache entry" in o_err and "Re-run" in o_err
    assert not _remote_has(tmp_project, sample.content_hash)

    _ = Step(step_num=4, name="Pull refuses before asking the remote",
             purpose="MalformedEntryError; the tree is left as it was")
    with pytest.raises(MalformedEntryError, match="malformed cache entry"):
        pull_cache([sample.content_hash], tmp_project)
    assert (sample_tree / "a.csv").read_bytes() == b"old shape\n"
    assert (output_tree / "a.csv").read_bytes() == b"old shape\n"
