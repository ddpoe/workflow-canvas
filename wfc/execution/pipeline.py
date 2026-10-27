"""``run_pipeline``: the one entry point that runs a pipeline document.

Substitute the document's variables and load it through the composer; refuse
the whole pipeline, in one message and before any run row, if a named
sample's content is in neither the local cache nor the archive; make every
image the nodes run runnable (an image the Docker daemon lacks is pulled
once by its recorded registry digest and checked, or the pipeline is
refused, again before any run row);
prepare the launch (the pipeline id, the log directories, the frozen
documents); start the push worker; hand the loaded pipeline to the engine
(Orchestration's ``invoke_engine``); then close the run: on a non-zero exit
the failure sequence raises the engine's message, otherwise the success
sequence writes the cancelled rows, archives and drains.

The ``run-pipeline`` verb, the canvas server and the test harness all call
this one function. It returns nothing on success and raises
``RuntimeError`` on failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from axiom_annotations import AutoStep, Step, workflow

from ..graph import UnknownVariableError

# Re-exported for init's ignore list (init may not import the engine layer).
from ..orchestration import ENGINE_STATE_DIR_NAME as ENGINE_STATE_DIR_NAME

# From their defining modules, so the AutoStep edges resolve.
from ..orchestration.engine import invoke_engine
from ..persistence import get_session
from ..persistence import project_root as get_project_root
from ..registration.sample_health import preflight_sample_content
from .composer import load_pipeline_from_document
from .lifecycle import (
    PushWorker,
    finish_failed,
    finish_succeeded,
    pipeline_summary,
    prepare_launch,
    start_push_worker,
    stop_push_worker,
)
from .node_env import preflight_pipeline_envs
from .pipeline_record import (
    ENTRY_POINT_CLI,
    PipelineRecordError,
    cancel_requested,
    record_engine,
    remove_pipeline_record,
    write_pipeline_record,
)
from .prepare import (
    FilledDefaults,
    filled_defaults,
    prepare_document,
    unpinned_resolutions,
)

#: The longest a value is shown in the declared-defaults line before it is cut.
_DEFAULT_VALUE_WIDTH = 60


def _defaults_line(entry: FilledDefaults) -> str:
    """The run-pipeline line naming the declared defaults one node or variant runs.

    Args:
        entry: One node's, or one variant's, filled defaults.

    Returns:
        ``[run_pipeline] node '<id>'[ variant '<v>'] (<module>/<method>) runs
        declared defaults: name=<value>, ...``, each value as compact JSON
        cut at 60 characters with an ellipsis.
    """
    shown = []
    for name, value in entry.values.items():
        text = json.dumps(value, separators=(",", ":"))
        if len(text) > _DEFAULT_VALUE_WIDTH:
            text = text[:_DEFAULT_VALUE_WIDTH] + "\u2026"
        shown.append(f"{name}={text}")
    variant = f" variant '{entry.variant}'" if entry.variant is not None else ""
    return (f"[run_pipeline] node '{entry.node_id}'{variant} "
            f"({entry.module}/{entry.method}) runs declared defaults: "
            + ", ".join(shown))


@workflow(
    purpose="Run a pipeline document end to end: substitute and load, refuse "
            "samples whose content is unreachable, make every env runnable, "
            "prepare the launch, start "
            "the push worker, hand the pipeline to the engine, and close the "
            "run",
    inputs="Pipeline JSON path, project root, cores, the launch switches, the "
           "canvas's callbacks",
    outputs="Completed pipeline; all run rows written to DB. Raises on failure",
)
def run_pipeline(
    pipeline_path: str,
    project_root: str | None = None,
    wfc_root: str | None = None,
    cores: int = 4,
    snakefile_path: str | None = None,
    pipeline_id: str | None = None,
    capture_output: bool = False,
    archive: bool = True,
    keep_going: bool = False,
    process_registry=None,
    is_cancelled=None,
    archive_progress_fn=None,
    entry_point: str = ENTRY_POINT_CLI,
) -> None:
    """Run a pipeline JSON: load it, run the engine over it, close the run.

    This is the single entry-point the GUI (or any caller) uses to execute a
    pipeline.  It handles Snakefile generation and the Snakemake invocation
    internally -- callers never need to touch snakemake directly.

    After successful pipeline completion, an archive pass hashes and caches
    all un-archived outputs (deferred archiving).  Controlled by the
    ``archive`` parameter (default: True).

    Args:
        pipeline_path: Path to the pipeline JSON file.
        project_root: Root of the wfc project (git repo with method commits).
            Defaults to the current working directory.
        wfc_root: Unused.  Kept so the signature and the ``--wfc-root`` flag
            stay as they are; nothing reads it.  The generated file finds
            ``wfc`` through the interpreter that runs Snakemake.
        cores: Number of Snakemake cores (default: 4).
        snakefile_path: Where to write the generated Snakefile.  Defaults to
            the pipeline's log directory
            (``<project_root>/.wfc/runs/pipelines/<pipeline_id>/Snakefile``).
        pipeline_id: Optional caller-provided pipeline ID.  When provided,
            this ID is used instead of generating a new UUID.  This ensures
            the canvas server and run_pipeline share the same ID for status
            tracking.  When omitted (CLI usage), a new UUID is generated.
        capture_output: When True, redirect stdout/stderr to log files in the
            pipeline log directory (used by the canvas server for log capture).
            When False (default, CLI usage), leave stdout/stderr connected to
            the terminal.
        archive: When True (default), run the deferred archive pass after
            successful pipeline completion.  When False, leave outputs
            un-archived (content_hash=NULL).
        keep_going: When True, pass ``--keep-going`` to Snakemake so a
            failure in one job doesn't cancel jobs that have no dependency
            on the failed one.  Useful for fan-out pipelines where each
            sample is independent: one bad sample still lets the others
            complete.  Default False (Snakemake's default fail-fast).
        process_registry: Optional callback handed the live engine process
            (the canvas server's cancel handle).
        is_cancelled: Optional probe the endings consult; when it answers
            True the cancel handler owns row state.
        archive_progress_fn: Optional callback forwarded to
            ``archive_outputs`` during the end-of-run archive pass, called
            per file with (run_id, output_name, status) -- see
            ``wfc.storage.archive_outputs``.  Lets an in-process caller
            (the canvas server) observe auto-archive progress.  When None
            (default, CLI usage), progress is only printed.
        entry_point: What started the pipeline, kept in its pipeline
            record: ``cli`` (default; the ``run-pipeline`` verb and any
            headless caller) or ``canvas``.

    While it runs the pipeline, from the launch until its end steps are
    done, the pipeline has a record in its run dir naming this process as
    its owner, the engine once it starts, and a cancel flag another process
    may set; the flag counts as a cancel exactly as ``is_cancelled``
    answering True does. The record is removed on every ending except an
    interrupt (``KeyboardInterrupt``, ``SystemExit``): that leaves it in
    place, so once this process is gone the recovery judges the pipeline
    dead and its close-out writes the rows the end steps did not.

    The push worker started for the pipeline is not left running on any
    ending: one no ending drained (an engine or an archive pass that raised)
    is drained on the way out, and every one gets a few seconds to finish
    its last tick.

    Returns:
        Nothing.  Returning is the statement that every scheduled job ran
        to completion.

    Raises:
        RuntimeError: The engine exited non-zero.  The message names the
            engine, the exit code and the log directory, and carries the
            captured stderr when output was captured.
        PipelineRecordError: The pipeline record could not be written.
            Raised after ``prepare_launch`` and before the engine starts, so
            no run row was written.  (A ``RuntimeError``.)
        ValueError: The document uses a variable its ``variables`` block
            does not declare.
        UnreachableSampleError: One or more of the pipeline's samples name
            content that is in neither the local cache nor the archive.
            Raised before ``prepare_launch``, so no run row was written;
            the message names every offender and its re-registration
            command.  (A ``ValueError``.)
        EnvNotRunnableError: An image the nodes run is not on this machine
            and cannot be pulled and checked (never pushed, the pull failed,
            or the pulled content renders another env fingerprint), or
            Docker does not answer.  Raised before ``prepare_launch``, so no
            run row was written; the message names the version, the env and
            the commands that recreate it.  (A ``RuntimeError``.)
        DatabaseUnreachableError: The composer could not reach the project
            database; nothing was launched.
    """
    _project_root = str(Path(project_root).resolve() if project_root else get_project_root())

    口 = Step(step_num=1, name="Prepare, then load",
             purpose="Prepare the document as the canvas does -- resolve its "
                     "{$var} refs to literals, validate it, enrich a sparse "
                     "document against the registered contracts and pin every "
                     "method node to the version it runs, filling every "
                     "declared default it leaves out -- and print one line "
                     "per node the document left unpinned, naming the version "
                     "it resolved, and one per node or param_sets variant that "
                     "runs declared defaults, naming each and its value; first "
                     "(substitution is the caller's obligation, not a load "
                     "step: an unknown name fails here, before anything is "
                     "scheduled), then load the substituted document through "
                     "Execution's composer (references, contract map and "
                     "sample hashes fetched in one session, Graph load pure); "
                     "an unreachable database fails here, before the launch")
    _document = json.loads(Path(pipeline_path).read_text(encoding="utf-8"))
    try:
        _substituted = prepare_document(_document)
    except UnknownVariableError as _unknown:
        raise ValueError(
            f"Unknown pipeline variable: '{_unknown.name}' -- the document's "
            f"'variables' block declares no such name"
        ) from _unknown
    # The version each node the document left unpinned resolved to; the
    # canvas's frozen copy is pinned throughout, so its path prints none.
    for _resolved in unpinned_resolutions(_document, _substituted):
        print(f"[run_pipeline] node '{_resolved.node_id}' "
              f"({_resolved.module}/{_resolved.method}) is not pinned: "
              f"runs v{_resolved.label}, the newest version")
    # The declared defaults preparation filled, where they run; an already
    # filled document (the canvas's frozen copy) prints none.
    for _filled in filled_defaults(_document, _substituted):
        print(_defaults_line(_filled))
    loaded = load_pipeline_from_document(_substituted)

    口 = Step(step_num=2, name="Refuse samples whose content is unreachable",
             purpose="Place every named sample in the local-cache x "
                     "push_status table (no network I/O) and refuse the whole "
                     "pipeline in one message, naming every offender and its "
                     "re-registration command, when any sample's content is "
                     "in neither the local cache nor the archive",
             inputs="The loaded pipeline's sample names",
             outputs="Nothing, or UnreachableSampleError",
             critical="runs before prepare_launch, so the refusal lands "
                      "before any run row is written; a local miss on a "
                      "pushed row is the documented cold start and is "
                      "silent, because the emitted restore rule pulls it")
    with get_session() as _preflight_session:
        preflight_sample_content(
            loaded.pipeline.samples, _preflight_session, _project_root,
        )

    口 = Step(step_num=3, name="Make every env runnable",
             purpose="Ask Environments' run-time rule once per distinct "
                     "image the pipeline's nodes run (each node's method "
                     "version's recorded image): present, else pulled once "
                     "by its recorded digest and its content checked, else "
                     "the pipeline is refused naming the command that "
                     "recreates the env and wfc register-method",
             inputs="The loaded pipeline's steps' modules and methods",
             outputs="Nothing, or EnvNotRunnableError",
             critical="runs before prepare_launch, so a refusal lands "
                      "before any run row is written and the pull happens "
                      "once, before the engine starts; nothing is built, and "
                      "the nodes' own run-step never pulls")
    from ..graph import document_node as _document_node
    preflight_pipeline_envs(
        [(step.module_name, step.method_name,
          (_document_node(_substituted, step.node_id) or {}).get("version") or None)
         for step in loaded.pipeline.steps],
        _project_root,
    )

    口 = AutoStep(step_num=4, name="Prepare the launch")
    _launch = prepare_launch(
        Path(_project_root), Path(pipeline_path), _document, _substituted, pipeline_id,
    )

    口 = Step(step_num=5, name="Record the pipeline",
             purpose="Write the pipeline record into the pipeline run dir: the "
                     "pipeline id, the entry point, this process as the owner "
                     "(pid and creation time), no engine yet, no cancel. When "
                     "the engine process starts, its pid and creation time go "
                     "into the record before the caller's registry gets it; "
                     "the endings read a cancel from the caller's probe or the "
                     "record's flag; the record is removed after the end "
                     "steps, on every ending but an interrupt (Ctrl-C, "
                     "SystemExit), which leaves it for the recovery to close "
                     "the pipeline out",
             outputs="The record, or PipelineRecordError",
             critical="A record that cannot be written refuses the pipeline "
                      "here, before the push worker and the engine start and "
                      "before any run row: a pipeline never runs unrecorded")
    try:
        write_pipeline_record(_project_root, _launch.pipeline_id, entry_point)
    except PipelineRecordError as _record_exc:
        raise PipelineRecordError(
            f"{_record_exc}; the pipeline was not started") from _record_exc

    def _on_engine_started(proc) -> None:
        # Name the engine in the record first, so a canceller in another
        # process can find it, then hand it to the caller's registry.
        try:
            record_engine(_project_root, _launch.pipeline_id, int(proc.pid))
        except Exception as _engine_exc:
            print(f"[run_pipeline] could not record the engine process in the "
                  f"pipeline record: {_engine_exc}", file=sys.stderr)
        if process_registry is not None:
            process_registry(proc)

    def _cancelled() -> bool:
        # The caller's probe, or the record's flag a canceller set.
        if is_cancelled is not None and is_cancelled():
            return True
        return cancel_requested(_project_root, _launch.pipeline_id)

    _interrupted = False
    _push: PushWorker | None = None
    try:
        口 = AutoStep(step_num=6, name="Start the push worker")
        _push = start_push_worker(Path(_project_root))

        口 = AutoStep(step_num=7, name="Hand the pipeline to the engine")
        outcome = invoke_engine(
            loaded.pipeline, _project_root,
            pipeline_id=_launch.pipeline_id, log_dir=_launch.log_dir,
            frozen_doc=_launch.frozen_doc, sample_hashes=loaded.sample_hashes,
            cores=cores, snakefile_path=snakefile_path, capture_output=capture_output,
            keep_going=keep_going, process_registry=_on_engine_started,
        )

        口 = Step(step_num=8, name="Close the run",
                 purpose="On a non-zero exit the failure sequence raises the "
                         "engine's message (once, with the outcome); otherwise the "
                         "success sequence writes the cancelled rows (both stamp "
                         "the launch's document hash on them), runs the "
                         "archive pass and drains the push worker. Both remove "
                         "the samples the pipeline restored, with their readiness "
                         "sentinels, unless another pipeline holds them (after "
                         "the archive pass on success); best-effort, a failure "
                         "is printed. Both read a cancel from the caller's probe "
                         "or from the record's flag",
                 critical="finish_failed never returns, so finish_succeeded is "
                          "the success path only")
        try:
            if outcome.exit_code != 0:
                finish_failed(_launch.pipeline_id, _project_root, outcome, _push,
                              _cancelled, pipeline_doc_hash=_launch.pipeline_doc_hash)
            finish_succeeded(
                _launch.pipeline_id, _project_root, _push, _cancelled,
                archive=archive, archive_progress_fn=archive_progress_fn,
                pipeline_doc_hash=_launch.pipeline_doc_hash,
            )
        finally:
            口 = Step(step_num=9, name="Print the pipeline summary",
                     purpose="Count the pipeline's run rows and print the summary, "
                             "on both paths, after the pipeline-end walk has "
                             "written its failed and cancelled rows -- the same "
                             "rows the canvas status route reads",
                     critical="Best-effort: a summary that cannot be read never "
                              "replaces the pipeline's own failure")
            try:
                pipeline_summary(_launch.pipeline_id)
            except Exception as _summary_exc:
                print(f"[run_pipeline] pipeline summary failed: {_summary_exc}")
    except BaseException as _ending:
        # An interrupt (Ctrl-C, SystemExit) cuts the end steps short: the
        # record stays, so once this process is gone the recovery judges
        # the pipeline dead and its close-out writes the missing rows.
        _interrupted = not isinstance(_ending, Exception)
        raise
    finally:
        if not _interrupted:
            try:
                口 = AutoStep(step_num=10, name="Remove the pipeline record, "
                                               "unless an interrupt cut the end "
                                               "steps short")
                remove_pipeline_record(_project_root, _launch.pipeline_id)
            except Exception as _remove_exc:
                print(f"[run_pipeline] could not remove the pipeline record: "
                      f"{_remove_exc}", file=sys.stderr)
        if _push is not None:
            口 = Step(step_num=11, name="Stop the push worker the endings did not",
                     purpose="On every ending, stop a push worker still "
                             "running: an engine or an archive pass that "
                             "raised reached no drain, so drain it here (not "
                             "waiting on the push tail after an interrupt), "
                             "then give the thread a few seconds to finish its "
                             "last tick, a cancel's included",
                     critical="A worker left running ticks on the process "
                              "engine forever and writes push state into "
                              "whatever database that engine is rebound to; "
                              "best-effort, a failure is printed")
            try:
                stop_push_worker(_push, cancel=_interrupted)
            except Exception as _stop_exc:
                print(f"[run_pipeline] could not stop the push worker: "
                      f"{_stop_exc}", file=sys.stderr)
