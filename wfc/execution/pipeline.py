"""``run_pipeline``: the one entry point that runs a pipeline document.

Substitute the document's variables and load it through the composer; refuse
the whole pipeline, in one message and before any run row, if a named
sample's content is in neither the local cache nor the archive; make every
env the nodes run in runnable (a locally built image the Docker daemon lost
is rebuilt once, or the pipeline is refused, again before any run row);
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
    finish_failed,
    finish_succeeded,
    pipeline_summary,
    prepare_launch,
    start_push_worker,
)
from .node_env import preflight_pipeline_envs
from .prepare import prepare_document


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
            (``<project_root>/.runs/pipelines/<pipeline_id>/Snakefile``).
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

    Returns:
        Nothing.  Returning is the statement that every scheduled job ran
        to completion.

    Raises:
        RuntimeError: The engine exited non-zero.  The message names the
            engine, the exit code and the log directory, and carries the
            captured stderr when output was captured.
        ValueError: The document uses a variable its ``variables`` block
            does not declare.
        UnreachableSampleError: One or more of the pipeline's samples name
            content that is in neither the local cache nor the archive.
            Raised before ``prepare_launch``, so no run row was written;
            the message names every offender and its re-registration
            command.  (A ``ValueError``.)
        EnvNotRunnableError: An env the nodes run in has a locally built
            image the Docker daemon lacks, and it cannot be rebuilt.  Raised
            before ``prepare_launch``, so no run row was written; the
            message names the env and the command that recreates it.  (A
            ``RuntimeError``; a rebuild whose ``docker build`` fails raises
            a plain ``RuntimeError`` at the same point.)
        DatabaseUnreachableError: The composer could not reach the project
            database; nothing was launched.
    """
    _project_root = str(Path(project_root).resolve() if project_root else get_project_root())

    口 = Step(step_num=1, name="Prepare, then load",
             purpose="Prepare the document as the canvas does -- resolve its "
                     "{$var} refs to literals and enrich a sparse document "
                     "against the registered contracts -- first "
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
             purpose="Ask Environments' ensure_runnable once per distinct "
                     "container env the pipeline's nodes run in (the rule "
                     "dispatch uses, over the substituted document every "
                     "node's run-step reads), so a locally built image the "
                     "Docker daemon lost is rebuilt once, or the pipeline is "
                     "refused naming the command that recreates it",
             inputs="The substituted document and the loaded pipeline's "
                    "node ids",
             outputs="Nothing, or EnvNotRunnableError",
             critical="runs before prepare_launch, so a rebuild's manifest "
                      "commit lands before the launch records the run's "
                      "commit and before any node claims (the claim reads "
                      "the rebuilt env's fingerprint), and a refusal lands "
                      "before any run row is written; the nodes' own "
                      "run-step never rebuilds")
    preflight_pipeline_envs(
        _substituted, [step.node_id for step in loaded.pipeline.steps],
        _project_root,
    )

    口 = AutoStep(step_num=4, name="Prepare the launch")
    _launch = prepare_launch(
        Path(_project_root), Path(pipeline_path), _document, _substituted, pipeline_id,
    )

    口 = AutoStep(step_num=5, name="Start the push worker")
    _push = start_push_worker(Path(_project_root))

    口 = AutoStep(step_num=6, name="Hand the pipeline to the engine")
    outcome = invoke_engine(
        loaded.pipeline, _project_root,
        pipeline_id=_launch.pipeline_id, log_dir=_launch.log_dir,
        frozen_doc=_launch.frozen_doc, sample_hashes=loaded.sample_hashes,
        cores=cores, snakefile_path=snakefile_path, capture_output=capture_output,
        keep_going=keep_going, process_registry=process_registry,
    )

    口 = Step(step_num=7, name="Close the run",
             purpose="On a non-zero exit the failure sequence raises the "
                     "engine's message (once, with the outcome); otherwise the "
                     "success sequence writes the cancelled rows, runs the "
                     "archive pass and drains the push worker",
             critical="finish_failed never returns, so finish_succeeded is "
                      "the success path only")
    try:
        if outcome.exit_code != 0:
            finish_failed(_launch.pipeline_id, _project_root, outcome, _push, is_cancelled)
        finish_succeeded(
            _launch.pipeline_id, _project_root, _push, is_cancelled,
            archive=archive, archive_progress_fn=archive_progress_fn,
        )
    finally:
        口 = Step(step_num=8, name="Print the pipeline summary",
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
