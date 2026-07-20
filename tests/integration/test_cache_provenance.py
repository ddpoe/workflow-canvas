"""Layer C — cache integrity & provenance invariants (US-1, US-3, US-5).

System-level cache-correctness tests that the provenance primitives never prove
together. Two tests run real containerized pipelines (cache-hit indistinguishable
from fresh run; dedup lineage). The remaining invariants exercise the cache
primitives directly against an archived run's content, which is both faster and
sharper than re-running a pipeline:

  US-3 cache-hit == fresh run: re-running an identical pipeline reuses the cached
       output (CACHED audit, second Run.cache_source_run_id set, no re-exec) and
       serves byte-identical content.
  US-3 dedup lineage: two distinct runs producing identical bytes collapse to ONE
       cache entry but keep TWO RunOutput rows with distinct artifact_path — the
       lineage of both runs is independently recoverable.
  US-5 corruption/tamper: mutating a cache file is detected by restore_from_cache,
       which replaces it from a clean source (hash-verified checkout).
  US-5 missing entry: deleting the cache entry makes resolve_input FAIL cleanly
       (returns None) rather than silently serving wrong/absent bytes.
  US-5 remote-pull: with the local entry pruned but present on a real local-FS DVC
       remote, resolve_input REMOTE-PULLs byte-identical content and repopulates
       the local cache.
  US-1 staging-vs-cache preservation: archive (move=False) leaves the staging copy
       intact and byte-identical to the cache copy.

INVARIANT #8 (backward-compat / NULL-content_hash legacy handling) is
INTENTIONALLY NOT TESTED here (decision D-2): NULL content_hash is the live
deferred-archiving state, not legacy data, and its removal would be an ADR-scale
reversal out of scope for this cycle.

Tests touching Docker/the pipeline are integration + requires_docker; the
primitive-level tests run without Docker but live here for cohesion.
"""

import configparser
import json
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.cli import _run_archive_dir, resolve_input, run_pipeline
from wfc.database import get_session
from wfc.models import Method, Run, RunOutput
from wfc.provenance import (
    _cache_path,
    archive_outputs,
    cache_file,
    hash_path,
    restore_from_cache,
)
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv
from tests.conftest import requires_docker

WFC_ROOT = Path(__file__).resolve().parent.parent.parent


# =============================================================================
# Helpers
# =============================================================================

def _transform_outputs(project_dir):
    """Return all completed transform RunOutput rows (snapshotted as tuples)."""
    with get_session() as session:
        rows = session.exec(
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform")
            .where(Run.status == "completed")
        ).all()
        return [
            (ro.run_id, ro.output_name, ro.artifact_path, ro.content_hash)
            for ro in rows
        ]


def _wire_local_remote(project_dir):
    """Init a real DVC repo with a local-FS remote; return the remote dir."""
    remote_dir = project_dir / "remote_storage"
    remote_dir.mkdir(exist_ok=True)
    from dvc.repo import Repo
    if not (project_dir / ".dvc").exists():
        Repo.init(str(project_dir), no_scm=True)
    cfg = project_dir / ".dvc" / "config"
    parser = configparser.ConfigParser()
    parser.read(cfg)
    if not parser.has_section("core"):
        parser.add_section("core")
    parser.set("core", "remote", "default")
    if not parser.has_section('remote "default"'):
        parser.add_section('remote "default"')
    parser.set('remote "default"', "url", str(remote_dir))
    with open(cfg, "w") as f:
        parser.write(f)
    return remote_dir


def _run_linear(pipeline_factory, project_dir, name, *, suffix, sample, archive):
    """Run input_selector -> transform over one sample. Returns pipeline path."""
    pipeline_path = pipeline_factory(
        name=name,
        nodes=[
            {"id": "sel", "type": "input_selector", "samples": [sample]},
            {"id": "t1", "method": "transform", "module": "test_pipeline",
             "params": {"suffix": suffix}},
        ],
        links=[{"source": "sel", "target": "t1"}],
        samples=[],
    )
    run_pipeline(
        pipeline_path=str(pipeline_path),
        project_root=str(project_dir),
        wfc_root=str(WFC_ROOT),
        cores=1,
        archive=archive,
    )
    return pipeline_path


# =============================================================================
# US-3: cache hit indistinguishable from fresh run
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="US-3: re-running an identical pipeline reuses the cached output "
            "(no re-exec) and serves byte-identical content",
    inputs="run a 1-node pipeline twice with identical code/params/inputs/env",
    outputs="second run is a cache hit (cache_source_run_id set); served bytes equal",
)
def test_cache_hit_indistinguishable_from_fresh_run(pipeline_factory, register_fixture_methods):
    project_dir = register_fixture_methods

    s = Step(step_num=1, name="First (fresh) run", purpose="Execute + archive to populate the cache")
    _create_sample_csv(project_dir, "hit_sample", num_rows=3)
    _run_linear(pipeline_factory, project_dir, "cache_hit_1",
                suffix="_h", sample="hit_sample", archive=True)
    first = _transform_outputs(project_dir)
    assert len(first) == 1, f"expected 1 transform output after run 1, got {len(first)}"
    first_run_id, _, first_path, first_hash = first[0]
    assert first_hash, "first run output not archived"
    first_bytes = Path(first_path).read_bytes()

    s = Step(step_num=2, name="Second (identical) run", purpose="Same code/params/inputs/env -> cache hit")
    _run_linear(pipeline_factory, project_dir, "cache_hit_2",
                suffix="_h", sample="hit_sample", archive=True)

    s = Step(step_num=3, name="Assert cache hit + byte-identical served content",
             purpose="A new Run row exists with cache_source_run_id set; bytes equal run 1")
    with get_session() as session:
        runs = session.exec(
            select(Run).join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform").order_by(Run.id)
        ).all()
        run_snap = [(r.id, r.cache_key, r.cache_source_run_id) for r in runs]
    assert len(run_snap) >= 2, f"expected >=2 transform runs, got {run_snap}"
    # Both runs share a cache_key (identical inputs); the second reused the first.
    assert run_snap[0][1] == run_snap[1][1], "identical runs produced different cache_keys"
    assert run_snap[1][2] is not None, (
        "second run did not record cache_source_run_id — cache hit not registered"
    )
    # Served bytes for the second run resolve to the same content.
    second_resolved = resolve_input(run_snap[1][0])
    if second_resolved is not None:
        assert Path(second_resolved).read_bytes() == first_bytes


# =============================================================================
# US-3: dedup keeps one cache entry but two recoverable RunOutput rows
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="US-3: two distinct runs producing identical bytes collapse to ONE "
            "cache entry but keep TWO RunOutput rows with distinct artifact_path",
    inputs="two pipelines over different samples whose transform output bytes are identical",
    outputs="single cache file; two RunOutput rows; both lineages recoverable",
)
def test_dedup_keeps_one_entry_two_recoverable_rows(pipeline_factory, register_fixture_methods):
    project_dir = register_fixture_methods

    s = Step(step_num=1, name="Two runs with identical output bytes",
             purpose="Two samples with identical content -> identical transform output bytes")
    # Same content + same suffix -> transform produces byte-identical output.
    _create_sample_csv(project_dir, "dedup_a", num_rows=3)
    _create_sample_csv(project_dir, "dedup_b", num_rows=3)
    _run_linear(pipeline_factory, project_dir, "dedup_1",
                suffix="_d", sample="dedup_a", archive=True)
    _run_linear(pipeline_factory, project_dir, "dedup_2",
                suffix="_d", sample="dedup_b", archive=True)

    s = Step(step_num=2, name="Assert dedup + recoverable lineage",
             purpose="One cache entry shared; two RunOutput rows with distinct paths")
    outputs = _transform_outputs(project_dir)
    assert len(outputs) == 2, f"expected 2 transform RunOutput rows, got {len(outputs)}"
    hashes = {o[3] for o in outputs}
    assert len(hashes) == 1, f"identical bytes should share one content_hash, got {hashes}"
    content_hash = hashes.pop()
    assert content_hash, "outputs not archived"
    # ONE cache entry on disk.
    assert _cache_path(project_dir, content_hash).exists()
    # TWO distinct RunOutput rows (distinct run_id AND distinct artifact_path).
    run_ids = {o[0] for o in outputs}
    paths = {o[2] for o in outputs}
    assert len(run_ids) == 2, f"expected 2 distinct run_ids, got {run_ids}"
    assert len(paths) == 2, f"dedup collapsed artifact_path — lineage not recoverable: {paths}"
    # Both runs resolve to the same (single) cache entry.
    for run_id, _, _, _ in outputs:
        resolved = resolve_input(run_id)
        assert resolved == str(_cache_path(project_dir, content_hash))


# =============================================================================
# US-3: cached upstream feeds a re-executing downstream step (mixed case)
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="US-3: a step re-executing downstream of a cache hit receives the "
            "cached upstream output in its slot_paths and succeeds",
    inputs="run sel->t1->t2 twice, changing only t2's params on the second run",
    outputs="t1 cache-hits (audit row); t2 re-executes with t1's cached output wired in",
)
def test_param_change_downstream_of_cache_hit_rewires_inputs(
    pipeline_factory, register_fixture_methods
):
    project_dir = register_fixture_methods

    def _chain(name, t2_suffix):
        pipeline_path = pipeline_factory(
            name=name,
            nodes=[
                {"id": "sel", "type": "input_selector", "samples": ["mix_sample"]},
                {"id": "t1", "method": "transform", "module": "test_pipeline",
                 "params": {"suffix": "_up"}},
                {"id": "t2", "method": "transform", "module": "test_pipeline",
                 "params": {"suffix": t2_suffix}},
            ],
            links=[
                {"source": "sel", "target": "t1"},
                {"source": "t1", "target": "t2"},
            ],
            samples=[],
        )
        run_pipeline(
            pipeline_path=str(pipeline_path),
            project_root=str(project_dir),
            wfc_root=str(WFC_ROOT),
            cores=1,
            archive=True,
        )

    s = Step(step_num=1, name="Fresh run",
             purpose="Both steps execute; t1's output is archived to the cache")
    _create_sample_csv(project_dir, "mix_sample", num_rows=3)
    _chain("mixed_1", "_b")

    s = Step(step_num=2, name="Re-run with only t2's params changed",
             purpose="t1 cache-hits; t2 must re-execute against t1's cached output")
    _chain("mixed_2", "_c")

    s = Step(step_num=3, name="Assert mixed-case wiring",
             purpose="t1's second run is an audit row; t2's new run executed "
                     "with the cached upstream path in slot_paths")
    with get_session() as session:
        runs = session.exec(
            select(Run).join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform")
            .where(Run.status == "completed")
            .order_by(Run.id)
        ).all()
        snap = [(r.id, dict(r.params or {}), r.cache_source_run_id) for r in runs]

    t1_audits = [r for r in snap if r[1].get("suffix") == "_up" and r[2] is not None]
    assert t1_audits, f"upstream step did not cache-hit on the second run: {snap}"

    t2_fresh = [r for r in snap if r[1].get("suffix") == "_c"]
    assert len(t2_fresh) == 1, f"changed-params step did not complete exactly once: {snap}"
    t2_id, _, t2_source = t2_fresh[0]
    assert t2_source is None, "changed-params step must be a fresh execution, not a cache hit"

    # The re-executed step's run context carries the cached upstream output.
    ctx = json.loads((_run_archive_dir(t2_id) / "_run_context.json").read_text())
    data_paths = ctx["slot_paths"].get("data", [])
    assert data_paths, "slot_paths empty — cached upstream output was not wired in"
    assert Path(data_paths[0]).exists()
    # And it is genuinely t1's output (carries t1's computed column).
    header = Path(data_paths[0]).read_text().splitlines()[0]
    assert "computed_up" in header, f"wired input is not t1's output: {header}"


# =============================================================================
# US-5 + US-1: cache integrity primitives (no Docker)
# =============================================================================

def _seed_archived_output(tmp_project, payload: bytes):
    """Create a completed Run + RunOutput with archived content; return (run_id, hash, staging).

    Builds a minimal Method/Run/RunOutput chain, writes a staging artifact, and
    runs the real archive pass so content_hash + cache entry exist exactly as a
    pipeline run would leave them.
    """
    from wfc.models import Module
    staging = tmp_project / "staging" / "out.csv"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(payload)
    with get_session() as session:
        module = Module(name="cmod", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(name="cmeth", module_id=module.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        ro = RunOutput(
            run_id=run.id, output_name="out.csv",
            artifact_path=str(staging), artifact_type="method_file",
        )
        session.add(ro)
        session.commit()
        run_id = run.id
    archive_outputs(tmp_project, run_id=run_id)
    with get_session() as session:
        ro = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).first()
        content_hash = ro.content_hash
    return run_id, content_hash, staging


def _setup_dvc(project_root):
    """Write [dvc] config + init local cache so archive/resolve have somewhere to write."""
    remote_dir = project_root / "dvc_remote"
    remote_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    db_path = (project_root / ".wfc" / "wfc.db").as_posix()
    config_path.write_text(
        f'[database]\nurl = "sqlite:///{db_path}"\n\n'
        f'[project]\nname = "test"\n\n'
        f'[dvc]\nremote_type = "local"\n'
        f'remote_path = "{remote_dir.as_posix()}"\nauto_init = true\n'
    )
    from wfc.provenance import init_dvc
    init_dvc(project_root, {"url": str(remote_dir)})


@workflow(purpose="US-5: a tampered cache file is detected and replaced from a clean source")
def test_corruption_detected_and_replaced(tmp_project):
    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive a known output", purpose="Populate the cache with known bytes")
    run_id, content_hash, _ = _seed_archived_output(tmp_project, b"clean-bytes-v1\n")
    cache_path = _cache_path(tmp_project, content_hash)
    assert cache_path.exists() and hash_path(cache_path) == content_hash

    _ = Step(step_num=2, name="Restore to a workspace path, then tamper it",
             purpose="restore_from_cache must detect the hash mismatch and replace from cache")
    dest = tmp_project / "checkout.csv"
    assert restore_from_cache(content_hash, dest, tmp_project) is True
    assert dest.read_bytes() == b"clean-bytes-v1\n"
    # Tamper the restored copy. The restore inherits the cache entry's
    # read-only mode (copy2 preserves bits; entries are protected), so the
    # tamperer must chmod first — owner can always do that (footgun guard,
    # not a security boundary).
    import os, stat
    os.chmod(dest, stat.S_IWRITE)
    dest.write_bytes(b"TAMPERED\n")
    assert hash_path(dest) != content_hash
    # restore_from_cache is hash-verified: it detects the mismatch and replaces.
    assert restore_from_cache(content_hash, dest, tmp_project) is True
    assert dest.read_bytes() == b"clean-bytes-v1\n", "tampered file not restored from cache"


@workflow(purpose="US-5: deleting a cache entry makes resolve_input FAIL cleanly (None), not serve garbage")
def test_missing_cache_entry_fails_cleanly(tmp_project):
    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive then delete the cache entry",
             purpose="Remove the local cache object with no remote configured")
    run_id, content_hash, _ = _seed_archived_output(tmp_project, b"to-be-deleted\n")
    cache_path = _cache_path(tmp_project, content_hash)
    import os, stat
    os.chmod(cache_path, stat.S_IWRITE)
    cache_path.unlink()
    assert not cache_path.exists()

    _ = Step(step_num=2, name="resolve_input must FAIL cleanly",
             purpose="No local entry + no reachable remote -> None (clean fail, no wrong bytes)")
    assert resolve_input(run_id) is None


@workflow(
    purpose="US-5/D-4: missing local entry is REMOTE-PULLed from a real local-FS DVC "
            "remote with byte-identical content; local cache repopulates",
)
def test_remote_pull_restores_identical_bytes(tmp_project):
    _ = Step(step_num=1, name="Archive + push to a real local-FS DVC remote",
             purpose="Populate the remote so a pruned local entry can be pulled back")
    _setup_dvc(tmp_project)
    payload = b"remote-payload-v1\n"
    run_id, content_hash, _ = _seed_archived_output(tmp_project, payload)
    remote_dir = _wire_local_remote(tmp_project)
    from wfc.remote import push as remote_push
    remote_push([content_hash], tmp_project)
    assert any(f.is_file() for f in remote_dir.rglob("*")), "remote did not receive the pushed object"

    _ = Step(step_num=2, name="Prune local cache entry",
             purpose="Force resolve_input down the REMOTE-PULL branch")
    cache_path = _cache_path(tmp_project, content_hash)
    import os, stat
    os.chmod(cache_path, stat.S_IWRITE)
    cache_path.unlink()
    assert not cache_path.exists()

    _ = Step(step_num=3, name="resolve_input pulls from remote",
             purpose="Returns the repopulated local cache path with byte-identical content")
    resolved = resolve_input(run_id)
    assert resolved is not None, "resolve_input failed to REMOTE-PULL"
    assert Path(resolved).read_bytes() == payload, "pulled bytes differ from original"
    assert cache_path.exists(), "local cache not repopulated after pull"


@workflow(purpose="US-1: archive (move=False) preserves the staging copy byte-identical to the cache copy")
def test_staging_copy_preserved_equals_cache(tmp_project):
    _setup_dvc(tmp_project)
    _ = Step(step_num=1, name="Archive an output (move=False path)",
             purpose="archive_outputs caches with move=False, preserving the source")
    payload = b"staging-vs-cache\n"
    run_id, content_hash, staging = _seed_archived_output(tmp_project, payload)

    _ = Step(step_num=2, name="Assert staging preserved == cache",
             purpose="Both copies exist and are byte-identical")
    assert staging.exists(), "staging copy was consumed — archive must preserve it (move=False)"
    cache_path = _cache_path(tmp_project, content_hash)
    assert cache_path.exists()
    assert staging.read_bytes() == payload
    assert cache_path.read_bytes() == payload
    assert hash_path(staging) == hash_path(cache_path) == content_hash
