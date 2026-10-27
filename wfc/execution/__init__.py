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

``pipeline_record.py`` is the pipeline record ``run_pipeline`` keeps in the
pipeline run dir while it runs a pipeline: the entry point, the owning and
engine processes (each a pid and its creation time) and the cancel flag. It
also reads a frozen document's step map.

``recovery.py`` answers whether a recorded pipeline is still being run (its
owner, its engine, its heartbeats: ``judge_liveness``), closes out one that
is not (``close_out_pipeline``: running rows failed as interrupted, a row for
every target, Storage's release, the record removed), and cancels one
through its record (``cancel_recorded_pipeline``).

``readiness.py`` answers whether a run can start at all: the git, DVC,
Docker, sample and view probes, each returning a uniform ``CheckResult``, and
``run_all_checks``, which runs the five in display order. The view probe
writes a scratch file into each directory it probes and removes it again.
``wfc init``, ``wfc doctor`` and the canvas's run gate share them.
``env_image_checks`` runs two more rows, the registry and the method
versions that cannot run, which ``wfc doctor`` prints after them.
``share_checks`` is ``wfc share``'s read-only checklist of what a
collaborator will be missing.
"""
from .composer import (
    DatabaseUnreachableError,
    LoadedPipeline,
    load_pipeline_from_document,
    load_pipeline_from_path,
)
from .lifecycle import (
    PIPELINE_CANCELLED,
    PIPELINE_FAILED,
    PIPELINE_MIXED,
    PIPELINE_RUNNING,
    PIPELINE_SUCCEEDED,
    ExpectedTarget,
    ExpectedTargets,
    expected_targets,
    pipeline_status,
)
from .pipeline import run_pipeline
from .pipeline_record import (
    ENTRY_POINT_CANVAS,
    ENTRY_POINT_CLI,
    PipelineRecord,
    PipelineRecordError,
    ProcessIdentity,
    cancel_requested,
    frozen_step_map,
    is_pipeline_id,
    list_pipeline_records,
    process_identity,
    read_pipeline_record,
    request_cancel,
)
from .readiness import (
    CheckResult,
    check_docker,
    check_dvc,
    check_git,
    check_registry,
    check_runnable_versions,
    check_samples,
    env_image_checks,
    run_all_checks,
    share_checks,
)
from .recovery import (
    INTERRUPTED_RUN_MESSAGE,
    CancelOutcome,
    CloseOut,
    HeartbeatSampler,
    ProcessState,
    UnknownPipelineError,
    Verdict,
    assess_pipeline,
    cancel_recorded_pipeline,
    close_out_pipeline,
    judge_liveness,
    kill_process_tree,
    probe_process,
)
from .run_step import run_step

__all__ = [
    "ENTRY_POINT_CANVAS",
    "ENTRY_POINT_CLI",
    "INTERRUPTED_RUN_MESSAGE",
    "PIPELINE_CANCELLED",
    "PIPELINE_FAILED",
    "PIPELINE_MIXED",
    "PIPELINE_RUNNING",
    "PIPELINE_SUCCEEDED",
    "CancelOutcome",
    "CheckResult",
    "CloseOut",
    "DatabaseUnreachableError",
    "ExpectedTarget",
    "ExpectedTargets",
    "HeartbeatSampler",
    "LoadedPipeline",
    "PipelineRecord",
    "PipelineRecordError",
    "ProcessIdentity",
    "ProcessState",
    "UnknownPipelineError",
    "Verdict",
    "assess_pipeline",
    "cancel_recorded_pipeline",
    "cancel_requested",
    "check_docker",
    "check_dvc",
    "check_git",
    "check_registry",
    "check_runnable_versions",
    "check_samples",
    "close_out_pipeline",
    "env_image_checks",
    "expected_targets",
    "frozen_step_map",
    "is_pipeline_id",
    "judge_liveness",
    "kill_process_tree",
    "list_pipeline_records",
    "load_pipeline_from_document",
    "load_pipeline_from_path",
    "pipeline_status",
    "probe_process",
    "process_identity",
    "read_pipeline_record",
    "request_cancel",
    "run_all_checks",
    "run_pipeline",
    "run_step",
    "share_checks",
]
