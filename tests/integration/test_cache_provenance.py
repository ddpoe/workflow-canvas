"""Cache integrity & provenance invariants (containerized).

System-level cache-correctness tests that run real containerized pipelines:
cache-hit indistinguishable from a fresh run, dedup lineage, and a param-change
step re-wiring its inputs downstream of a cache hit.

The Docker-free primitive-level invariants (corruption detect-and-replace,
missing-entry clean failure, remote-pull restore, staging-vs-cache preservation)
live in tests/test_cache_provenance_primitives.py.

NULL content_hash is not treated as legacy data here: it is the live
deferred-archiving state of an output not yet archived, covered by
tests/test_deferred_archiving.py.

All tests here are integration + requires_docker.
"""

import json
from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.execution import run_pipeline
from wfc.storage import resolve_input
from wfc.persistence import project_root as get_project_root
from wfc.layout import run_archive_dir
from wfc.persistence import get_session, Method, Run, RunOutput
from wfc.storage.cache import (
    _cache_path,
    cache_file,
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
# Cache hit indistinguishable from fresh run
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="Re-running an identical pipeline reuses the cached output "
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
# Dedup keeps one cache entry but two recoverable RunOutput rows
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="Two distinct runs producing identical bytes collapse to ONE "
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
# Cached upstream feeds a re-executing downstream step (mixed case)
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(
    purpose="A step re-executing downstream of a cache hit receives the "
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
    ctx = json.loads((run_archive_dir(get_project_root(), t2_id) / "_run_context.json").read_text())
    data_paths = ctx["slot_paths"].get("data", [])
    assert data_paths, "slot_paths empty — cached upstream output was not wired in"
    assert Path(data_paths[0]).exists()
    # And it is genuinely t1's output (carries t1's computed column).
    header = Path(data_paths[0]).read_text().splitlines()[0]
    assert "computed_up" in header, f"wired input is not t1's output: {header}"
