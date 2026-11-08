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
``preflight_sample_content``, before any run row), check the binds a step's
container will see (``refuse_unseen_binds``, the launch bind check: one short
container from the first step's image with a step's binds; a bind that comes
up empty refuses the launch with ``BindsNotVisibleError`` before any run row,
and a check that cannot run lets the launch go on), prepare the launch, start
the push worker, hand the pipeline to Orchestration's engine, and close the
run (the failure sequence raises the engine's message).

``one_method.py`` runs one registered method on one sample, with the params
and the name given: Contracts' one-step document, checked by the validator's
verdict, saved in the project's pipelines folder (named, or carrying the
params' fingerprint; reused when the same file is there, refused when a
different one is), then ``run_pipeline`` over it. ``run_detail.py`` reads one run, recorded or a
draft step: status, metrics, outputs, error, its log paths and stderr's
tail. ``run_counts.py`` counts the runs per method, method version, sample
and env spec, with the newest start time, for every door that lists them
with their run counts.

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
Docker, disk, sample and view probes, each returning a uniform
``CheckResult``, and ``run_all_checks``, which runs the six in display order.
The disk probe warns when the project is on a Windows network drive. The
view probe writes a scratch file into each directory it probes and removes it
again.
``wfc init``, ``wfc doctor`` and the canvas's run gate share them.
``env_image_checks`` runs four more rows, which ``wfc doctor`` prints
after them: the registry, the method versions that cannot run, whether a
step's container sees the project, and what an earlier run or crash left
running (``check_leftovers``, over Execution's own ``run_liveness``, which
``wfc prune-envs`` and ``wfc list-envs`` pass to Environments too). That
liveness counts a crashed pipeline's runs as ended (its record's owner and
engine both gone: ``judge_liveness`` gives no alive, and ``RunLiveness.
second_look`` re-reads its heartbeats before ``wfc prune-envs`` stops
anything), and a running draft step whose recorded process is gone (a dead
``wfc try``); a pipeline with no record keeps its running rows running.
``share_checks`` is ``wfc share``'s read-only checklist of what a
collaborator will be missing.
"""
from .composer import (
    DatabaseUnreachableError,
    LoadedPipeline,
    PipelineDocumentError,
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
from .one_method import (
    OneMethodRunRefusedError,
    SavedOneMethodPipeline,
    params_fingerprint,
    run_one_method,
    save_one_method_pipeline,
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
    RunLiveness,
    check_disk,
    check_docker,
    check_dvc,
    check_git,
    check_leftovers,
    check_registry,
    check_runnable_versions,
    check_samples,
    env_image_checks,
    run_all_checks,
    run_liveness,
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
from .run_counts import RunTallies, RunTally, run_tallies
from .run_detail import RunDetail, RunDetailError, RunDetailOutput, run_detail, run_log
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
    "PipelineDocumentError",
    "PipelineRecord",
    "PipelineRecordError",
    "ProcessIdentity",
    "ProcessState",
    "RunLiveness",
    "UnknownPipelineError",
    "Verdict",
    "assess_pipeline",
    "cancel_recorded_pipeline",
    "cancel_requested",
    "check_disk",
    "check_docker",
    "check_dvc",
    "check_git",
    "check_leftovers",
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
    "run_liveness",
    "run_pipeline",
    "OneMethodRunRefusedError", "SavedOneMethodPipeline", "run_one_method",
    "save_one_method_pipeline", "params_fingerprint",
    "RunDetail", "RunDetailError", "RunDetailOutput", "run_detail", "run_log",
    "RunTallies", "RunTally", "run_tallies",
    "run_step",
    "share_checks",
]
