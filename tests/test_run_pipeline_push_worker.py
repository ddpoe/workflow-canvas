"""No push worker outlives ``run_pipeline``, however the pipeline ends.

``run_pipeline`` starts the push worker before it hands the pipeline to the
engine. The endings drain it; an engine that raises -- an error, or Ctrl-C --
reaches neither ending, and a cancel's drain does not wait, so ``run_pipeline``
stops the worker itself on the way out. A worker left running ticks on the
process engine forever, and once that engine is rebound to another project it
writes push state into the wrong database.

The project is built by the harness (``init_project`` gives it a remote, so
the worker really starts); Snakemake itself is mocked, as in
``tests/test_pipeline_record.py``.
"""

from __future__ import annotations

import threading

import pytest
from axiom_annotations import workflow

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import stub_docker_image_inspect
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.execution.lifecycle import PUSH_WORKER_THREAD_NAME

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731

#: How each pipeline ends: what the engine's wait does, whether the caller
#: cancels, and what ``run_pipeline`` raises.
_ENDINGS = {
    "engine-error": (RuntimeError("the engine's wait failed"), False, RuntimeError),
    "ctrl-c": (KeyboardInterrupt(), False, KeyboardInterrupt),
    "cancel": (1, True, RuntimeError),
}


def _live_push_workers() -> list[threading.Thread]:
    """Every push-worker thread still running in this process."""
    return [t for t in threading.enumerate()
            if t.name == PUSH_WORKER_THREAD_NAME and t.is_alive()]


@pytest.mark.parametrize("ending", sorted(_ENDINGS))
@workflow(purpose="However run_pipeline ends -- an engine that raises an error "
                  "or Ctrl-C, reaching no ending, or a cancel, whose drain "
                  "does not wait -- it stops the push worker it started "
                  "before it raises")
def test_no_push_worker_outlives_run_pipeline(git_project, monkeypatch, ending):
    from wfc.execution import run_pipeline

    on_wait, cancelled, raised = _ENDINGS[ending]
    pid = f"push-worker-{ending}"
    project = build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id=pid),
        root=git_project, monkeypatch=monkeypatch,
    )
    assert _live_push_workers() == []

    seen_running: list[int] = []

    def _engine_wait(*_args, **_kwargs):
        # The worker is running while the engine is: the test cannot pass by
        # never starting it.
        seen_running.append(len(_live_push_workers()))
        if isinstance(on_wait, BaseException):
            raise on_wait
        return on_wait

    gen_patch, popen_patch = mocked_snakemake(1)
    popen_patch.kwargs["return_value"].wait.side_effect = _engine_wait
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch, pytest.raises(raised):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id=pid,
                     is_cancelled=(lambda: True) if cancelled else None)

    assert seen_running == [1]
    assert _live_push_workers() == [], (
        f"run_pipeline ended ({ending}) and left its push worker running")
