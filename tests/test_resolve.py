"""resolve_input three-state resolution and the resolver agreement oracle.

Tier 2: Subsystem tests covering the CACHE / REMOTE-PULL / FAIL contract.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_transport
from tests.fixtures.fakes.shortcuts import flip_push_status
from tests.fixtures.routes import completed_run
from tests.harness.scenario import Behavior
from wfc.layout import run_archive_dir
from wfc.persistence import get_session, Method, Module, Run, RunOutput
from wfc.storage.cache import _cache_path


def _one_output_run(project_dir, monkeypatch, content: bytes):
    """A completed run through the route whose one output ``out`` holds ``content``.

    Returns:
        The driven run; its output row is un-archived, as the record phase
        leaves it.
    """
    return completed_run(
        project_dir, monkeypatch=monkeypatch, method="meth", module="mod",
        sample="s1", outputs={"out": ".txt"}, output_files={"out": "out.txt"},
        behavior=Behavior(outputs={"out": content.decode()}),
    )


def _archive_one(project_dir, run_id: int) -> Path:
    """Run the archive pass over one run and return its one output's cache path."""
    from sqlmodel import select
    from wfc.storage import archive_outputs

    archive_outputs(project_dir, run_id=run_id)
    with get_session() as session:
        (row,) = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
    assert row.content_hash, "the archive pass recorded no content hash"
    return _cache_path(project_dir, row.content_hash)


def _prune_local_cache(project_dir) -> None:
    """Drop the pushed entries from the local cache, as ``wfc cache prune`` does.

    The rows keep their hashes; the bytes are only elsewhere -- the state a
    remote pull is asked about.
    """
    from wfc.storage import prune_dvc_cache

    prune_dvc_cache(project_dir, dry_run=False)


def _lose_cache_entry(path: Path) -> None:
    """Declared deviation: lose a never-pushed entry's local bytes by hand.

    No verb removes an unpushed entry; a disk failure or a deleted cache
    directory does.
    """
    from wfc.storage.cache import _make_writable

    _make_writable(path)
    path.unlink()


def _ensure_method(session, module_name="mod", method_name="meth"):
    mod = Module(name=module_name)
    session.add(mod)
    session.commit()
    session.refresh(mod)
    m = Method(
        module_id=mod.id,
        name=method_name,
        env="container:demo",
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


# =============================================================================
# resolve_input
# =============================================================================

class TestResolveInputThreeState:
    """resolve_input collapses to CACHE / REMOTE-PULL / FAIL."""

    def test_cache_hit_returns_cache_path(self, tmp_project, monkeypatch):
        from wfc.storage import resolve_input

        content = b"run output"
        run_id = _one_output_run(tmp_project, monkeypatch, content).run_id
        cache_path = _archive_one(tmp_project, run_id)
        assert cache_path == _cache_path(tmp_project, hashlib.md5(content).hexdigest())
        assert cache_path.read_bytes() == content

        resolved = resolve_input(run_id)
        assert resolved == str(cache_path)

    def test_remote_pull_succeeds(self, tmp_project, monkeypatch):
        from wfc.storage import resolve_input
        from wfc.storage import transport as _transport

        content = b"pulled bytes"
        run_id = _one_output_run(tmp_project, monkeypatch, content).run_id
        cache_path = _archive_one(tmp_project, run_id)
        flip_push_status(run_id, "pushed")
        _prune_local_cache(tmp_project)

        def fake_pull(hashes, project_dir):
            for h in hashes:
                p = _cache_path(Path(project_dir), h)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)

        stub_transport(monkeypatch, pull_cache=fake_pull)
        assert not cache_path.exists()
        assert resolve_input(run_id) == str(cache_path)
        assert cache_path.read_bytes() == content

    def test_never_pushed_is_refused_with_its_reason(self, tmp_project, monkeypatch):
        """An archived output neither local nor pushed is refused without a
        pull, naming the run, the slot, the content hash and the re-run."""
        from wfc.storage import InputUnavailableError, resolve_input
        from wfc.storage import transport as _transport

        run_id = _one_output_run(tmp_project, monkeypatch, b"never-cached").run_id
        _lose_cache_entry(_archive_one(tmp_project, run_id))

        pulled: list = []
        stub_transport(monkeypatch,
                       pull_cache=lambda hashes, project_dir: pulled.append(hashes))
        with pytest.raises(InputUnavailableError) as refusal:
            resolve_input(run_id)
        reason = str(refusal.value)
        assert f"run {run_id}" in reason and "never pushed" in reason
        assert f"Re-run run {run_id}" in reason
        assert not pulled

    @pytest.mark.parametrize("pushed", [True, False], ids=["pushed", "unpushed"])
    @workflow(
        purpose="an archived output whose cache object was edited is never "
                "served: recorded pushed, it is pulled again and the consumer "
                "gets the genuine bytes; never pushed, the read is refused "
                "naming the run and the change",
        inputs="an archived file output, pushed to the real local remote or "
               "not, whose cache object is rewritten in place",
        outputs="the genuine bytes at the cache path, or InputUnavailableError "
                "(resolve_input) and NotInCacheError (resolve_output)",
    )
    def test_an_edited_cache_object_is_pulled_again_or_refused(
            self, tmp_project, monkeypatch, pushed):
        """Flips adversarial B3c."""
        from wfc.persistence import PushStatus
        from wfc.storage import InputUnavailableError, NotInCacheError, resolve_input
        from wfc.storage import resolve_output
        from wfc.storage.push_worker import _push_worker_tick

        _ = Step(step_num=1, name="Archive one output, and push it for the pushed leg",
                 purpose="The real archive pass and the real push worker tick")
        genuine = b"genuine run output\n"
        run_id = _one_output_run(tmp_project, monkeypatch, genuine).run_id
        obj = _archive_one(tmp_project, run_id)
        if pushed:
            flip_push_status(run_id, PushStatus.pending.value)
            _push_worker_tick(tmp_project)
            with get_session() as session:
                from sqlmodel import select
                row = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()
            assert row.push_status == PushStatus.pushed.value

        _ = Step(step_num=2, name="Edit the cache object in place",
                 purpose="Same address, other bytes")
        os.chmod(obj, 0o644)
        obj.write_bytes(b"TAMPERED run output\n")

        _ = Step(step_num=3, name="Read it",
                 purpose="Pulled again when pushed, refused otherwise")
        if pushed:
            assert resolve_input(run_id) == str(obj)
            assert obj.read_bytes() == genuine
            return
        with pytest.raises(InputUnavailableError) as refusal:
            resolve_input(run_id)
        reason = str(refusal.value)
        assert f"run {run_id}" in reason and "changed in the local cache" in reason
        with pytest.raises(NotInCacheError, match="changed in the local cache"):
            resolve_output(run_id, "out", project_dir=tmp_project)

    def test_audit_row_resolves_via_source_run(self, tmp_project, monkeypatch):
        """A cache-hit audit row (no RunOutput rows of its own) resolves to
        the source run's cached output — the wiring a re-executing step
        downstream of a cache hit depends on."""
        from wfc.storage import resolve_input

        content = b"cached source output"
        source = _one_output_run(tmp_project, monkeypatch, content)
        # The same declaration again: the audit row the claim's cache-hit
        # branch inserts -- completed, cache_source_run_id set, no outputs.
        audit = _one_output_run(tmp_project, monkeypatch, content)
        assert audit.run_row["cache_source_run_id"] == source.run_id
        assert audit.output_rows == []
        cache_path = _archive_one(tmp_project, source.run_id)

        assert resolve_input(audit.run_id) == str(cache_path)


# =============================================================================
# The resolver agreement oracle
# =============================================================================

@workflow(
    purpose="The resolver agreement oracle: before the archive pass the input "
            "resolver hands a run's consumer the run-archive path and the "
            "output resolver refuses the output as not archived; after the "
            "pass both return the same cache path",
    inputs="one completed run whose output sits in its run archive, unhashed",
    outputs="the path each resolver returns before and after archive_outputs",
)
def test_resolvers_agree_across_the_archive_pass(tmp_project, monkeypatch):
    """The two resolvers serve different readers and agree once a row is
    archived."""
    from wfc.storage import NotArchivedError, resolve_input, resolve_output
    from wfc.storage import archive_outputs

    payload = b"agreement bytes"

    口 = Step(step_num=1, name="Record a run output before the archive pass",
             purpose="A completed run's output sits in its run archive with no "
                     "content hash, as the record phase leaves it")
    run = _one_output_run(tmp_project, monkeypatch, payload)
    run_id = run.run_id
    archived_file = run.output_path("out")
    assert archived_file.read_bytes() == payload
    assert run.output_rows[0]["content_hash"] is None

    口 = Step(step_num=2, name="Resolve before the pass",
             purpose="The input resolver returns the run-archive path; the "
                     "output resolver refuses the unarchived output")
    assert resolve_input(run_id, "out") == str(archived_file)
    with pytest.raises(NotArchivedError):
        resolve_output(run_id, "out", pull=False, project_dir=tmp_project)

    口 = Step(step_num=3, name="Run the archive pass",
             purpose="archive_outputs hashes the output and copies it into "
                     "the cache")
    results = archive_outputs(tmp_project, run_id=run_id)
    assert [r["status"] for r in results] == ["archived"]

    口 = Step(step_num=4, name="Resolve after the pass",
             purpose="Both resolvers return the same cache path")
    expected = _cache_path(tmp_project, hashlib.md5(payload).hexdigest())
    input_path = resolve_input(run_id, "out")
    output_path, _row = resolve_output(
        run_id, "out", pull=False, project_dir=tmp_project
    )
    assert Path(input_path).resolve() == output_path.resolve() == expected.resolve()
    assert output_path.read_bytes() == payload


# =============================================================================
# A pre-archive record outside its run's archive is malformed
# =============================================================================

@workflow(
    purpose="A producer's pre-archive output record pointed under the old "
            ".runs/ tree, holding other bytes, is a malformed record: the "
            "consumer that runs next never reads those bytes, the hit rule "
            "places the output missing, and the input resolver refuses it "
            "naming the producer's re-run",
    inputs="a two-node chain whose producer ran; its one record's path "
           "rewritten to <root>/.runs/<run id>/data.csv",
    outputs="the consumer's failed run, the output's location, and the "
            "resolver's refusal",
)
def test_a_pre_archive_record_under_the_old_runs_tree_is_never_read(
    tmp_path, monkeypatch, capfd,
):
    """A recorded path is read only inside its run's archive (request decision 2)."""
    from sqlmodel import select

    from tests.harness import Scenario, build_project, drive_target, node, selector, wire
    from tests.harness.scenario import SELECTOR_ID
    from wfc.storage import resolve_input
    from wfc.storage.resolve import OUTPUT_MISSING, output_location

    口 = Step(step_num=1, name="Run the producer",
             purpose="The head of a two-node chain runs; collect records its one "
                     "output inside its run archive, unhashed")
    project = build_project(
        Scenario(nodes=[
            selector(),
            node("head", inputs=[wire(SELECTOR_ID)], behavior=Behavior(echo_input="data")),
            node("tail", inputs=[wire("head")], behavior=Behavior(echo_input="data")),
        ], samples=["S1"]),
        root=tmp_path / "proj", monkeypatch=monkeypatch,
    )
    head, tail = ("head", "S1", "default"), ("tail", "S1", "default")
    produced = drive_target(project, head, monkeypatch=monkeypatch)
    head_id = produced.runs[head].run_id
    (record,) = produced.output_rows_for(head)
    from wfc.storage.cache import _make_writable
    _make_writable(record["artifact_path"])  # collect left it read-only
    Path(record["artifact_path"]).unlink()

    口 = Step(step_num=2, name="Point the record under the old tree",
             purpose="The one field no production writer sets this way: the "
                     "record's path names a file under <root>/.runs/, which "
                     "holds bytes of its own")
    legacy = project.root / ".runs" / f"{head_id:08d}" / "data.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"id,value\n1,LEGACY\n")
    with get_session() as session:
        row = session.exec(select(RunOutput).where(RunOutput.run_id == head_id)).one()
        row.artifact_path = str(legacy)
        session.add(row)
        session.commit()
        session.refresh(row)
        assert output_location(row, project.root) == OUTPUT_MISSING

    refusal = f"Run {head_id} (head) has a malformed record; re-run it."

    口 = Step(step_num=3, name="Run the consumer",
             purpose="The tail's materialize phase refuses the input, naming the "
                     "producer's re-run in its step log; the method is never "
                     "launched, so the old tree's bytes are never read")
    capfd.readouterr()
    consumed = drive_target(project, tail, monkeypatch=monkeypatch)
    assert consumed.exit_code(tail) != 0
    assert consumed.runs[tail].dispatch_env is None
    assert consumed.output_rows_for(tail) == []
    assert refusal in capfd.readouterr().err

    口 = Step(step_num=4, name="Ask the input resolver",
             purpose="The resolver refuses the malformed record and names the "
                     "producer's re-run")
    assert resolve_input(head_id, "data") is None
    assert refusal in capfd.readouterr().err


# =============================================================================
# resolve_output — audit-row hop
# =============================================================================

class TestResolveOutputAuditHop:
    """resolve_output dereferences cache-hit audit rows to their source run."""

    def _source_and_audit(self, project_dir, monkeypatch):
        """Source run with one archived output (cache populated) plus the
        audit row the same claim's cache-hit branch inserts on a second,
        identical run: completed, cache_source_run_id set, no RunOutput rows."""
        content = b"cached source output"
        source = _one_output_run(project_dir, monkeypatch, content)
        audit = _one_output_run(project_dir, monkeypatch, content)
        assert audit.run_row["cache_source_run_id"] == source.run_id
        assert audit.output_rows == []
        cache_path = _archive_one(project_dir, source.run_id)
        return source.run_id, audit.run_id, cache_path

    def test_audit_row_resolves_source_output(self, tmp_project, monkeypatch):
        from wfc.storage import resolve_output

        source_id, audit_id, cache_path = self._source_and_audit(tmp_project, monkeypatch)

        path, ro = resolve_output(audit_id, "out")
        assert path == cache_path
        assert ro.run_id == source_id

    def test_audit_row_discovery_lists_source_names(self, tmp_project, monkeypatch):
        from wfc.storage import UnknownOutputError, resolve_output

        _, audit_id, _ = self._source_and_audit(tmp_project, monkeypatch)

        with pytest.raises(UnknownOutputError) as excinfo:
            resolve_output(audit_id, None)
        assert excinfo.value.available == ["out"]


# =============================================================================
# output_export_name — what a user's copy of an output is called
# =============================================================================

@pytest.mark.parametrize(
    ("output_name", "artifact_path", "expected"),
    [
        # A plain name gains its artifact's suffix.
        ("masks", "/runs/12/masks.tif", "masks.tif"),
        # A name that already carries the suffix is not doubled — this is
        # what the collect phase writes when a method declares its output
        # by filename.
        ("report.csv", "/runs/12/report.csv", "report.csv"),
        # No name at all: the artifact's own file name.
        ("", "/runs/12/out.parquet", "out.parquet"),
    ],
)
@workflow(
    purpose="An output's export name is its row's name carrying the "
            "artifact's suffix: a plain name gains it, a name already "
            "carrying it is not doubled, and a row with no name falls back "
            "to the artifact's file name — so `wfc export` and the Canvas "
            "artifact browser call the same output the same thing.",
)
def test_output_export_name_table(output_name, artifact_path, expected):
    from wfc.storage import output_export_name

    ro = RunOutput(
        run_id=12,
        output_name=output_name,
        artifact_path=artifact_path,
        artifact_type="method_file",
    )
    assert output_export_name(ro) == expected


# =============================================================================
# exportable_outputs — the strict enumeration behind `wfc export --all`
# =============================================================================

@workflow(
    purpose="The strict enumeration refuses rather than exports part of a "
            "run: a run with no recorded outputs and a run with a malformed "
            "record each raise the typed error `wfc export --all` prints, "
            "before any output is resolved; the malformed one names the run "
            "to re-run.",
)
def test_exportable_outputs_refuses_before_resolving(tmp_project):
    from wfc.storage import (
        MalformedRecordError, UnknownOutputError, exportable_outputs,
    )

    with get_session() as session:
        method = _ensure_method(session)
        empty = Run(method_id=method.id, params={}, sample="s1",
                    status="completed")
        unnamed = Run(method_id=method.id, params={}, sample="s2",
                      status="completed")
        session.add(empty)
        session.add(unnamed)
        session.commit()
        session.refresh(empty)
        session.refresh(unnamed)
        empty_id, unnamed_id = empty.id, unnamed.id
        session.add(RunOutput(
            run_id=unnamed_id,
            output_name="",
            artifact_path=str(run_archive_dir(tmp_project, unnamed_id) / "x.bin"),
            artifact_type="method_file",
        ))
        session.commit()

    with pytest.raises(UnknownOutputError) as excinfo:
        exportable_outputs(empty_id)
    assert f"Run {empty_id} has no recorded outputs." in str(excinfo.value)

    with pytest.raises(MalformedRecordError) as excinfo:
        exportable_outputs(unnamed_id)
    assert f"Run {unnamed_id}" in str(excinfo.value)
    assert "re-run" in str(excinfo.value)
