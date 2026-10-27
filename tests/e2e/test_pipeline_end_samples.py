"""E2E: the end of a pipeline removes the samples it restored; the next one restores them.

A pipeline's restore rule makes each sample it reads a view of its cache
entry under ``.wfc/workspace/samples/<name>/`` with a readiness sentinel.
Whether the pipeline succeeds or fails, its end removes those views and
sentinels unless another pipeline holds them; the cache entry keeps the
bytes, and the next pipeline makes the view again.

Tier 3: @workflow + Step() markers.
"""

import os
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import create_sample_csv as _create_sample_csv
from wfc import layout
from wfc.execution import run_pipeline

WFC_ROOT = Path(__file__).resolve().parent.parent.parent

# Real pipelines through the container dispatch path (register_fixture_methods
# builds a real image): deselected from the default suite, skipped without Docker.
pytestmark = [pytest.mark.integration, requires_docker]


def _assert_sample_view_gone(project_dir: Path, name: str, content_hash: str) -> None:
    assert not layout.sample_dir(project_dir, name).exists(), (
        f"the end of the pipeline removes the restored sample '{name}'")
    assert not layout.sample_ready_sentinel(project_dir, name).exists(), (
        f"the end of the pipeline removes the readiness sentinel of '{name}'")
    assert layout.dvc_cache_entry(project_dir, content_hash).is_file(), (
        "the cache entry keeps the sample's bytes")


def _transform_pipeline(pipeline_factory, name: str, suffix: str, *, crash: bool = False):
    nodes = [
        {"id": "selector_1", "type": "input_selector", "samples": ["sample_a"]},
        {"id": "transform_1", "method": "transform", "module": "test_pipeline",
         "params": {"suffix": suffix}},
    ]
    links = [{"source": "selector_1", "target": "transform_1"}]
    if crash:
        nodes.append({"id": "faulty_1", "method": "faulty", "module": "test_pipeline",
                      "params": {"failure_mode": "crash"}})
        links.append({"source": "transform_1", "target": "faulty_1"})
    return pipeline_factory(name=name, nodes=nodes, links=links, samples=[])


def _run(pipeline_path: Path, project_dir: Path) -> None:
    run_pipeline(pipeline_path=str(pipeline_path), project_root=str(project_dir),
                 wfc_root=str(WFC_ROOT), cores=1)


@workflow(
    purpose="End one pipeline by success and one by failure, then run a third: "
            "the restored sample and its readiness sentinel are gone after each "
            "of the first two, and the third restores the sample as a view of "
            "its cache entry and runs over it",
)
def test_each_pipeline_end_removes_its_restored_samples_and_the_next_restores_them(
        pipeline_factory, register_fixture_methods):
    from wfc.persistence import InUseKind, Method, Run, Sample, get_session
    from wfc.storage import end_pipeline_samples, links
    from wfc.storage.in_use import held_keys, hold_for_pipeline

    project_dir = register_fixture_methods

    口 = Step(step_num=1, name="Register a sample",
             purpose="Registration caches the sample; nothing is restored yet")
    _create_sample_csv(project_dir, "sample_a", num_rows=3)
    with get_session() as session:
        sample = session.exec(select(Sample).where(Sample.name == "sample_a")).one()
    content_hash = sample.content_hash
    obj = layout.dvc_cache_entry(project_dir, content_hash)
    assert not layout.sample_dir(project_dir, "sample_a").exists()

    口 = Step(step_num=2, name="A pipeline that succeeds",
             purpose="Its end, after the archive pass, removes the sample it "
                     "restored and the sentinel")
    _run(_transform_pipeline(pipeline_factory, "ends-ok", "_ok"), project_dir)
    _assert_sample_view_gone(project_dir, "sample_a", content_hash)

    口 = Step(step_num=3, name="A pipeline that fails",
             purpose="Its end removes the sample too")
    with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
        _run(_transform_pipeline(pipeline_factory, "ends-failed", "_failed", crash=True),
             project_dir)
    _assert_sample_view_gone(project_dir, "sample_a", content_hash)
    assert held_keys(InUseKind.sample_view) == set(), "both pipelines released their records"

    口 = Step(step_num=4, name="A third pipeline restores the sample",
             purpose="A reader outside the pipeline holds the sample so its view "
                     "outlives the third pipeline's end and can be looked at: it "
                     "is a read-only view of the cache entry, a hardlink where "
                     "the filesystem links, and the third pipeline's step ran "
                     "over it")
    hold_for_pipeline(InUseKind.sample_view, "sample_a", "observer")
    _run(_transform_pipeline(pipeline_factory, "third", "_third"), project_dir)
    view = layout.restored_sample_path(project_dir, "sample_a", sample.registered_path)
    assert layout.sample_ready_sentinel(project_dir, "sample_a").exists()
    assert view.read_bytes() == obj.read_bytes()
    assert not os.access(view, os.W_OK), "the restored sample is read-only"
    # The view's own folder is read-only; the tier is probed beside it.
    beside = next(d for d in view.parents if os.access(d, os.W_OK))
    if links.probe_pair(obj.parent, beside).tier == links.TIER_HARDLINK:
        assert os.path.samefile(view, obj), "the restore costs no copy: a hardlink of the entry"
    with get_session() as session:
        third = session.exec(
            select(Run).join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform", Run.status == "completed")
            .where(Run.cache_source_run_id.is_(None))).all()
    assert len(third) == 3, (
        "the transform of each pipeline computed over the sample: the "
        "succeeded one, the failed one (before its downstream crash) and the third")

    口 = Step(step_num=5, name="The last holder ends",
             purpose="The view goes with its last holder")
    assert end_pipeline_samples(project_dir, "observer") == ["sample_a"]
    _assert_sample_view_gone(project_dir, "sample_a", content_hash)
