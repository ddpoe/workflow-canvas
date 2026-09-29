"""The hit rule as a decision table: which completed run a cache key reuses.

``wfc.execution.claim.classify_cache_key`` is the one definition of a hit,
shared by ``pre_run``, the ``check_cache`` verb and the cache-status preview.
Each row below moves one variable off a completed two-output run and names
the verdict and the per-output locations the rule must return:

- ``local`` combines two places on purpose: the DVC cache entry of an
  archived output, and the run-archive path of an output the archive pass
  has not reached yet. Debugging a wrong ``local`` starts here.
- ``remote`` is an archived output that is not local but recorded pushed.
- ``missing`` is an output in neither place. A run with this exact key and
  a missing output is ``outputs_missing`` -- the step re-runs under the same
  key -- and never a hit.

A directory output is local only when its cache entry is complete: the
``.dir`` manifest and every member object (``cache.entry_is_complete``). A
malformed cache entry (a directory tree at an unsuffixed address) is
``missing`` to the rule, so the step re-runs; the reader refuses it.

Every row starts from a run produced by production's five phases
(``completed_run``); outputs are archived by the production archive pass.
Declared fixture deviations: cache entries, member objects and archive
files are removed from disk the way ``dvc gc``, an interrupted pull or a
pruned archive leaves them; ``_lay_tree_at`` lays the pre-cycle directory
shape at an unsuffixed address, which production no longer writes; the
pushed state of a file row is the declared ``flip_push_status`` shortcut,
while a directory row is pushed by the production push worker to the
harness's real local remote.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import Step, workflow

from tests.fixtures.fakes.shortcuts import flip_push_status
from tests.fixtures.routes import completed_run

_METHOD = "hit_rule"
_SAMPLE = "S_hit"
_TREE = {"a.csv": "a\n", "sub/b.csv": "b\n"}


def _complete(project: Path, monkeypatch, shape: str):
    """A completed run: two file outputs, or one directory output ``tree``."""
    if shape == "dir":
        from tests.harness.scenario import Behavior

        return completed_run(project, monkeypatch=monkeypatch, method=_METHOD,
                             sample=_SAMPLE, outputs={"tree": "directory"},
                             output_files={"tree": "tree"},
                             behavior=Behavior(outputs={"tree": dict(_TREE)}))
    return completed_run(project, monkeypatch=monkeypatch, method=_METHOD,
                         sample=_SAMPLE,
                         outputs={"left": ".csv", "right": ".csv"})


def _outputs(run_id: int) -> dict:
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        return {
            row.slot: (row.content_hash, row.artifact_path)
            for row in session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            )
        }


def _run_row(run_id: int):
    from wfc.persistence import Run, get_session

    with get_session() as session:
        return session.get(Run, run_id)


def _module_of(run_id: int) -> str:
    from wfc.persistence import Method, Module, get_session

    with get_session() as session:
        run = _run_row(run_id)
        method = session.get(Method, run.method_id)
        return session.get(Module, method.module_id).name


def _remove(path: Path) -> None:
    """Delete a file or directory, clearing DVC's read-only bit first."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def _prune_cache(project: Path, run_id: int, slot: str) -> None:
    from wfc import layout

    content_hash, _ = _outputs(run_id)[slot]
    assert content_hash, f"output '{slot}' is not archived"
    _remove(layout.dvc_cache_entry(project, content_hash))


def _drop_archive_file(run_id: int, slot: str) -> None:
    _, artifact_path = _outputs(run_id)[slot]
    _remove(Path(artifact_path))


def _archive(project: Path, run_id: int) -> None:
    from wfc.storage.archive import archive_outputs

    archive_outputs(project, run_id=run_id)
    assert all(h for h, _ in _outputs(run_id).values())


def _drop_member(project: Path, run_id: int, slot: str) -> None:
    """Declared deviation: one member object gone, as an interrupted pull leaves."""
    from wfc import layout
    from wfc.storage.cache import manifest_members

    content_hash, _ = _outputs(run_id)[slot]
    assert content_hash.endswith(".dir"), content_hash
    _remove(layout.dvc_cache_entry(project, sorted(manifest_members(project, content_hash))[0]))


def _push(project: Path, run_id: int) -> None:
    """The production push worker sends every pending entry to the remote."""
    from wfc.persistence import PushStatus, RunOutput, get_session
    from wfc.storage.push_worker import _push_worker_tick

    _push_worker_tick(project)
    with get_session() as session:
        rows = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
        assert all(r.push_status == PushStatus.pushed.value for r in rows), rows


def _lay_tree_at(project: Path, run_id: int, slot: str) -> None:
    """Declared deviation: the pre-cycle directory shape at an unsuffixed address."""
    from wfc import layout

    content_hash, _ = _outputs(run_id)[slot]
    address = layout.dvc_cache_entry(project, content_hash)
    _remove(address)
    address.mkdir()
    (address / "a.csv").write_text("old shape\n")


# Each row: (id, run shape, arrange(project, run_id), expected status,
# expected locations, slots whose cache entry is malformed)
_TABLE = [
    ("pre-archive, archive files present", "files",
     lambda p, r: None,
     "local", {"left": "local", "right": "local"}, ()),
    ("pre-archive, one archive file gone", "files",
     lambda p, r: _drop_archive_file(r, "right"),
     "outputs_missing", {"left": "local", "right": "missing"}, ()),
    ("archived, both cache entries present", "files",
     lambda p, r: _archive(p, r),
     "local", {"left": "local", "right": "local"}, ()),
    ("archived, one entry pruned and pushed", "files",
     lambda p, r: (_archive(p, r), flip_push_status(r, "pushed"),
                   _prune_cache(p, r, "right")),
     "remote", {"left": "local", "right": "remote"}, ()),
    ("archived, both entries pruned and pushed", "files",
     lambda p, r: (_archive(p, r), flip_push_status(r, "pushed"),
                   _prune_cache(p, r, "left"), _prune_cache(p, r, "right")),
     "remote", {"left": "remote", "right": "remote"}, ()),
    ("archived, one entry pruned and not pushed", "files",
     lambda p, r: (_archive(p, r), flip_push_status(r, "failed"),
                   _prune_cache(p, r, "right")),
     "outputs_missing", {"left": "local", "right": "missing"}, ()),
    ("archived, a directory tree at one entry's address (malformed)", "files",
     lambda p, r: (_archive(p, r), _lay_tree_at(p, r, "right")),
     "outputs_missing", {"left": "local", "right": "missing"}, ("right",)),
    ("directory, manifest and every member present", "dir",
     lambda p, r: _archive(p, r),
     "local", {"tree": "local"}, ()),
    ("directory, one member missing and not pushed", "dir",
     lambda p, r: (_archive(p, r), _drop_member(p, r, "tree")),
     "outputs_missing", {"tree": "missing"}, ()),
    ("directory, one member missing and pushed", "dir",
     lambda p, r: (_archive(p, r), _push(p, r), _drop_member(p, r, "tree")),
     "remote", {"tree": "remote"}, ()),
]


@pytest.mark.parametrize(
    "shape,arrange,status,locations",
    [pytest.param(sh, a, s, l, id=i) for i, sh, a, s, l, _m in _TABLE],
)
@workflow(purpose="The hit rule places each output of a completed run local "
                  "(DVC cache or run archive), remote (archived, pushed, not "
                  "local) or missing, and the row verdict follows: all local is "
                  "a local hit, any remote and none missing is a remote hit, any "
                  "missing is outputs-missing and never a hit")
def test_hit_rule_decision_table(tmp_project, monkeypatch, shape, arrange,
                                 status, locations):
    from wfc.execution.claim import classify_cache_key, lookup_cache_hit

    口 = Step(step_num=1, name="Complete a run",
             purpose="Production's five phases record two pre-archive file "
                     "outputs, or one directory output, in the run archive")
    driven = _complete(tmp_project, monkeypatch, shape)
    run_id = driven.run_id
    run = _run_row(run_id)
    module = _module_of(run_id)
    assert not any(h for h, _ in _outputs(run_id).values()), (
        "the drive already archived the outputs; the pre-archive rows need "
        "their own arrangement"
    )

    口 = Step(step_num=2, name="Move one variable",
             purpose="Archive, prune a cache entry or a directory member, drop "
                     "an archive file, lay a malformed entry, or push")
    arrange(Path(tmp_project), run_id)

    口 = Step(step_num=3, name="Ask the hit rule",
             purpose="The verdict and every output's location come from one "
                     "probe; only a hit hands back a run to reuse")
    verdict = classify_cache_key(_METHOD, module, run.sample, run.cache_key)
    assert verdict.status == status
    assert verdict.run_id == run_id
    assert {o.slot: o.location for o in verdict.outputs} == locations
    expected_hit = run_id if status in ("local", "remote") else None
    assert lookup_cache_hit(_METHOD, module, run.sample, run.cache_key) \
        == expected_hit


@pytest.mark.parametrize(
    "shape,arrange,status,locations,malformed",
    [pytest.param(sh, a, s, l, m, id=i) for i, sh, a, s, l, m in _TABLE],
)
@workflow(purpose="The consumer's resolver and the hit rule agree on every "
                  "output: a local output resolves to a path that exists (a "
                  "directory's checkout), a remote one is pulled and then read, "
                  "a missing one is refused naming the run, the slot, its "
                  "content hash and the re-run, never a path that does not "
                  "exist, and a malformed cache "
                  "entry is refused naming a re-run")
def test_resolver_agrees_with_the_hit_rule(tmp_project, monkeypatch, capsys,
                                           shape, arrange, status, locations,
                                           malformed):
    from wfc import layout
    from wfc.execution.claim import classify_cache_key
    from wfc.storage import (
        InputUnavailableError, MalformedEntryError, resolve_input,
    )
    from wfc.storage.transport import pull_cache as real_pull_cache
    from tests.fixtures.fakes.seams import stub_transport

    口 = Step(step_num=1, name="Complete a run and move one variable",
             purpose="The same arrangements the decision table reads")
    driven = _complete(tmp_project, monkeypatch, shape)
    run_id = driven.run_id
    run = _run_row(run_id)
    arrange(Path(tmp_project), run_id)
    by_hash = {h: Path(a) for h, a in _outputs(run_id).values() if h}
    pulled: list[str] = []

    def _pull(hashes, project_dir):
        # The DVC remote is the true external edge. A directory row was
        # pushed for real, so its pull is the real one against the harness's
        # local remote. A file row was recorded pushed by the shortcut; the
        # archive pass copies (never moves) an output into the cache, so the
        # run-archive file holds the bytes a pull materializes.
        for h in hashes:
            pulled.append(h)
            if h.endswith(".dir"):
                return real_pull_cache([h], project_dir)
            entry = layout.dvc_cache_entry(Path(project_dir), h)
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_bytes(by_hash[h].read_bytes())
        return True

    stub_transport(monkeypatch, pull_cache=_pull)

    口 = Step(step_num=2, name="Ask the hit rule, then resolve every output",
             purpose="Each output's location against what the consumer's "
                     "resolver does with it")
    verdict = classify_cache_key(_METHOD, _module_of(run_id), run.sample,
                                 run.cache_key)
    assert {o.slot: o.location for o in verdict.outputs} == locations
    for slot, location in locations.items():
        pulled.clear()
        capsys.readouterr()
        if slot in malformed:
            with pytest.raises(MalformedEntryError,
                               match="malformed cache entry") as refusal:
                resolve_input(run_id, slot)
            assert f"Re-run run {run_id}" in str(refusal.value)
            assert location == "missing" and not pulled
            continue
        content_hash, _ = _outputs(run_id)[slot]
        if location == "missing":
            # An output in neither place is refused with its reason, which
            # the consumer records on its run row: the run, the slot, the
            # content hash of an archived row, and the re-run.
            with pytest.raises(InputUnavailableError) as refusal:
                resolve_input(run_id, slot)
            reason = str(refusal.value)
            assert f"run {run_id}" in reason and f"'{slot}'" in reason
            assert f"Re-run run {run_id}" in reason
            if content_hash:
                assert content_hash in reason
            assert "FAIL" in capsys.readouterr().err and not pulled
            continue
        resolved = resolve_input(run_id, slot)
        err = capsys.readouterr().err
        if resolved is not None and content_hash and content_hash.endswith(".dir"):
            assert Path(resolved) == layout.checkout_dir(tmp_project, content_hash)
            assert {p.relative_to(resolved).as_posix(): p.read_text()
                    for p in Path(resolved).rglob("*") if p.is_file()} == _TREE
        if location == "local":
            assert resolved is not None and Path(resolved).exists(), (slot, err)
            assert not pulled
        elif location == "remote":
            assert pulled, f"output '{slot}' is remote but was not pulled"
            assert resolved is not None and Path(resolved).exists(), (slot, err)


@workflow(purpose="Setting DVC up on its own leaves the DVC directory's ignore "
                  "file, so the cache and the credentials never enter git")
def test_init_dvc_writes_the_dvc_ignore_file(tmp_path):
    from wfc.storage import init_dvc

    口 = Step(step_num=1, name="Set DVC up in a bare directory",
             purpose="No wfc init around it")
    init_dvc(tmp_path, {"url": str(tmp_path.parent / f"{tmp_path.name}-remote")})

    口 = Step(step_num=2, name="Read the ignore file",
             purpose="It lists the credentials file, the scratch space and "
                     "the cache")
    lines = (tmp_path / ".dvc" / ".gitignore").read_text(encoding="utf-8").split()
    assert lines == ["/config.local", "/tmp", "/cache"]


@workflow(purpose="A key no completed run of the method in its module carries "
                  "is a miss with no outputs: another key, another module, or "
                  "a run that did not complete")
def test_hit_rule_misses(tmp_project, monkeypatch):
    from wfc.execution.claim import classify_cache_key
    from wfc.persistence import Run, get_session

    口 = Step(step_num=1, name="Complete a run", purpose="The baseline hit")
    driven = completed_run(tmp_project, monkeypatch=monkeypatch, method=_METHOD,
                           sample=_SAMPLE)
    run = _run_row(driven.run_id)
    module = _module_of(driven.run_id)
    assert classify_cache_key(_METHOD, module, run.sample,
                              run.cache_key).status == "local"

    口 = Step(step_num=2, name="Another key, another module",
             purpose="Neither matches the candidate set")
    other_key = classify_cache_key(_METHOD, module, run.sample, "0" * 64)
    assert (other_key.status, other_key.run_id, other_key.outputs) == (
        "miss", None, ())
    assert classify_cache_key(_METHOD, "other_module", run.sample,
                              run.cache_key).status == "miss"

    口 = Step(step_num=3, name="A run that did not complete",
             purpose="Only a completed run is reused")
    with get_session() as session:
        row = session.get(Run, driven.run_id)
        row.status = "failed"
        session.add(row)
        session.commit()
    assert classify_cache_key(_METHOD, module, run.sample,
                              run.cache_key).status == "miss"


@workflow(purpose="Under one cache key, an older completed run whose outputs "
                  "are all readable beats a newer run whose outputs are "
                  "missing: the key is a local hit on the older run")
def test_an_older_readable_run_beats_a_newer_missing_one(tmp_project, monkeypatch):
    import hashlib

    from wfc.execution.claim import classify_cache_key, lookup_cache_hit
    from wfc.storage import cache_file, entry_is_complete

    口 = Step(step_num=1, name="Complete and archive the older run; prune one entry",
             purpose="The older run's 'right' output is neither local nor "
                     "pushed, so the next drive under the same key re-runs")
    older = _complete(tmp_project, monkeypatch, "files")
    _archive(Path(tmp_project), older.run_id)
    right_hash, _ = _outputs(older.run_id)["right"]
    _prune_cache(Path(tmp_project), older.run_id, "right")

    口 = Step(step_num=2, name="Drive the same step again",
             purpose="The older run is outputs-missing, so the claim registers "
                     "and completes a newer run under the same key")
    newer = _complete(tmp_project, monkeypatch, "files")
    newer_row = _run_row(newer.run_id)
    assert newer.run_id != older.run_id
    assert newer_row.cache_source_run_id is None
    assert newer_row.cache_key == _run_row(older.run_id).cache_key

    口 = Step(step_num=3, name="The older run readable again, the newer one not",
             purpose="The production cache writer restores the older run's "
                     "entry from the identical bytes the newer run produced; "
                     "then the newer run's run-archive file is dropped")
    _, newer_right = _outputs(newer.run_id)["right"]
    assert hashlib.md5(Path(newer_right).read_bytes()).hexdigest() == right_hash
    cache_file(newer_right, right_hash, tmp_project, move=False)
    assert entry_is_complete(tmp_project, right_hash)
    _drop_archive_file(newer.run_id, "right")

    口 = Step(step_num=4, name="Ask the hit rule",
             purpose="The newest readable run wins, not the newest run")
    module = _module_of(older.run_id)
    verdict = classify_cache_key(_METHOD, module, newer_row.sample,
                                 newer_row.cache_key)
    assert (verdict.status, verdict.run_id) == ("local", older.run_id)
    assert lookup_cache_hit(_METHOD, module, newer_row.sample,
                            newer_row.cache_key) == older.run_id

