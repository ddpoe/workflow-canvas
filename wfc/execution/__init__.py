"""Execution — the Tier 1 run lifecycle.

Five phase modules carry one step's run: claim (``claim.py``) decides
whether work happens and registers the run, materialize (``materialize.py``)
resolves declared inputs to local paths, dispatch (``dispatch.py``) runs the
method in its container, collect (``collect.py``) turns the method's exit
into outputs and metrics, and record (``record.py``) writes the rows,
sentinel and sidecars every ending funnels through. ``run_step.py``
orchestrates the phase sequence.

``lifecycle.py`` owns the pipeline around the steps: launch bookkeeping and
the legacy-workspace sweep, the push-worker start and drain, the failure and
success sequences, cancellation, the cancelled-row pass, and the pipeline
summary.

``composer.py`` is the composer: a pipeline document to a ``PipelineDef``
with its reads done in one session (the referenced runs' outputs and
sample, the contract map, the samples' content hashes), failing before the
launch when the database is unreachable (``DatabaseUnreachableError``).

``pipeline.py`` is ``run_pipeline``, the one entry point that runs a
pipeline document: substitute and load through the composer, refuse the
pipeline when a named sample's content is unreachable (Registration's
``preflight_sample_content``, before any run row), prepare the launch, start
the push worker, hand the pipeline to Orchestration's engine, and close the
run (the failure sequence raises the engine's message).

``readiness.py`` answers whether a run can start at all: the git, DVC,
Docker and sample probes, each returning a uniform ``CheckResult``, and
``run_all_checks``, which runs the four in display order. ``wfc init``,
``wfc doctor`` and the canvas's run gate share them.
"""
from .composer import (
    DatabaseUnreachableError,
    LoadedPipeline,
    load_pipeline_from_document,
    load_pipeline_from_path,
)
from .pipeline import run_pipeline
from .readiness import (
    CheckResult,
    check_docker,
    check_dvc,
    check_git,
    check_samples,
    run_all_checks,
)
from .run_step import run_step

__all__ = [
    "CheckResult",
    "DatabaseUnreachableError",
    "LoadedPipeline",
    "check_docker",
    "check_dvc",
    "check_git",
    "check_samples",
    "load_pipeline_from_document",
    "load_pipeline_from_path",
    "run_all_checks",
    "run_pipeline",
    "run_step",
]
