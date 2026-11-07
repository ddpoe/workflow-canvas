"""Catalog case 9 with a real engine: ``wfc run-method <module>.<method> --sample``.

The one-method run saves its one-step document and runs it as any pipeline,
so the run is recorded and cached the way every pipeline's is: the first call
records a completed run of the method on the sample with the params given,
the second call reuses the saved file untouched and is a cache hit of the
first run.

Integration + requires_docker, on the shared fixture methods
(``register_fixture_methods``); nothing is stubbed.
"""
from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import create_sample_csv
from wfc import layout
from wfc.cli import cli_main
from wfc.execution import params_fingerprint
from wfc.persistence import Method, Run, get_session


def _transform_runs() -> list[tuple[int, str, str | None, int | None, dict | None]]:
    """Every transform run as ``(id, status, cache_key, cache_source_run_id, params)``."""
    with get_session() as session:
        rows = session.exec(
            select(Run).join(Method, Run.method_id == Method.id)
            .where(Method.name == "transform").order_by(Run.id)
        ).all()
        return [(r.id, r.status, r.cache_key, r.cache_source_run_id, r.params)
                for r in rows]


@pytest.mark.integration
@requires_docker
@workflow(purpose="Case 9 with Docker: wfc run-method --sample --params saves the "
                  "one-step document under the params' fingerprint and records a "
                  "completed run with those params; the second call reuses the "
                  "saved file untouched and is a cache hit of the first run")
def test_run_method_records_a_run_with_its_params_and_hits_the_cache_the_second_time(
        register_fixture_methods):
    project_dir = register_fixture_methods
    create_sample_csv(project_dir, "one_sample", num_rows=3)
    params = {"suffix": "_x"}
    saved = layout.one_method_pipeline_path(project_dir, "test_pipeline", "transform",
                                            "one_sample", params_fingerprint(params))
    argv = ["run-method", "test_pipeline.transform", "--sample", "one_sample",
            "--params", '{"suffix": "_x"}']

    口 = Step(step_num=1, name="Run the method on the sample with params",
             purpose="Exit 0; the document is saved under the fingerprint; one "
                     "completed transform run carrying the params, not a cache hit")
    assert cli_main(argv) == 0
    assert saved.is_file()
    first = _transform_runs()
    assert [(status, source) for _id, status, _key, source, _p in first] == [
        ("completed", None)], first
    assert first[0][4]["suffix"] == "_x", first

    口 = Step(step_num=2, name="Run it again",
             purpose="Exit 0; the saved file untouched; a second run row that "
                     "is a cache hit of the first, under the same key")
    before = saved.stat().st_mtime_ns
    assert cli_main(argv) == 0
    assert saved.stat().st_mtime_ns == before
    runs = _transform_runs()
    assert len(runs) == 2, runs
    (first_id, _s, first_key, _src, _p), (_id, status, key, source, _p2) = runs
    assert status == "completed" and key == first_key
    assert source == first_id, "the second call must be a cache hit of the first"
