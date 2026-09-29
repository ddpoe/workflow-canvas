"""Submitting a pipeline document: the ``submit-pipeline`` workflow and its background run.

``submit_pipeline`` takes the canvas's document and the served project and
returns the pipeline id and the step map. It refuses an empty document, gates
on run readiness, substitutes the document's variables, enriches it, writes
the frozen document and the editable form, then registers the job and starts
``run_in_background`` on a thread. The run route is the HTTP wrapper around it.

``run_pipeline_fn`` and ``fail_pipeline_fn`` are the test seams the background
run reads at call time; both import from Execution lazily.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from .models import PipelineInput
from .state import _active_jobs, _make_archive_progress_writer


def _import_run_pipeline():
    """Lazy import of run_pipeline from the Execution package.

    Module-level reference so tests can mock ``wfc.canvas.submission.run_pipeline_fn``
    and have the mock remain active during background thread execution.
    """
    from ..execution import run_pipeline
    return run_pipeline


def _import_fail_pipeline():
    """Lazy import of fail_pipeline from the Execution lifecycle."""
    from ..execution.lifecycle import fail_pipeline
    return fail_pipeline


# Callable references -- overridden in tests via mock
run_pipeline_fn = _import_run_pipeline
fail_pipeline_fn = _import_fail_pipeline


class SubmissionRefused(Exception):
    """The document was refused before anything was written or started.

    The message is the refusal's sentence: an empty document, or a variable
    the document cannot resolve.
    """


class RunNotReady(SubmissionRefused):
    """The run-readiness gate refused the submission.

    Attributes:
        payload: The kind-tagged ``{kind, message, hint}`` payload the canvas
            renders for pre-run errors.
    """

    def __init__(self, payload: Dict[str, Any]) -> None:
        super().__init__(payload["message"])
        self.payload = payload


@dataclass(frozen=True)
class SubmittedPipeline:
    """A submitted pipeline: its id and its step map.

    Attributes:
        pipeline_id: The pipeline id, which is also the job id.
        step_map: Each canvas node id mapped to its method name.
    """

    pipeline_id: str
    step_map: Dict[str, str]


def _enrich_pipeline(pipeline: "PipelineInput") -> Dict[str, Any]:
    """Enrich a PipelineJSON payload with script paths and slot_outputs from the DB.

    The canvas sends minimal node data (id, method, module, params).
    Registration reads the contract map for every registered method and the
    Contracts unit's pure ``enrich_pipeline`` derives each node's script
    path, slot filenames and slot types from it, so ``load_pipeline()`` has
    everything it needs.

    Args:
        pipeline: The PipelineInput model from the canvas frontend.

    Returns:
        A dict with ``nodes``, ``links``, and ``samples`` keys matching
        the PipelineJSON format that ``load_pipeline()`` expects.
    """
    from ..execution.prepare import enrich_document

    return enrich_document(pipeline.model_dump())


def _classify_pipeline_error(exc: Exception) -> Dict[str, Any]:
    """Map a pre-run exception to a structured payload for the canvas UI.

    The UI shows the raw message by default (it's already human-readable —
    ``DirtyRepositoryError`` / ``resolve_env_fingerprint`` (not registered) /
    ``capture_env_content`` (unsupported spec) build full sentences).
    ``kind`` lets the frontend pick an icon/color or inline action (e.g. a
    "Commit changes" button for dirty_repo).  ``hint`` is an optional
    follow-up sentence separated from the main message.
    """
    from ..version import DirtyRepositoryError

    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, DirtyRepositoryError):
        return {
            "kind": "dirty_repo",
            "message": message,
            "hint": "Commit or stash your changes, then click Run again.",
        }
    # Module/method lookup miss from pre_run (wfc/execution/claim.py).
    if isinstance(exc, ValueError) and "not found" in message.lower():
        return {"kind": "not_found", "message": message}
    return {"kind": "unknown", "message": message}


def _readiness_payload(check) -> Dict[str, Any]:
    """Build a kind-tagged payload from a not-ready readiness CheckResult.

    Maps a failing ``check_docker`` / ``check_git`` result to the same
    ``{kind, message, hint}`` shape the frontend renders for pre-run
    errors. The kinds are ``not_runnable_docker`` and ``not_runnable_git``.

    Args:
        check: A :class:`wfc.execution.CheckResult` with a non-``ok`` status.

    Returns:
        ``{"kind": ..., "message": ..., "hint": ...}``.
    """
    return {
        "kind": f"not_runnable_{check.name}",
        "message": check.message,
        "hint": check.fix_hint,
    }


@task(
    purpose="Run a submitted pipeline on its thread: run it; close a failed run "
            "unless a cancel was requested; record the classified error on the "
            "job entry",
    inputs="The frozen document's path, the project root, the pipeline id, the "
           "keep-going switch; the job entry submit_pipeline installed",
    outputs="Nothing. On failure the pipeline's rows are closed (unless "
            "cancelled) and the entry's error holds the classified payload",
)
def run_in_background(
    pipeline_path: Path, project_root: str, pipeline_id: str, keep_going: bool,
) -> None:
    """Execute a submitted pipeline in a background thread.

    Args:
        pipeline_path: The frozen ``pipeline.json`` the run reads.
        project_root: The served project's root.
        pipeline_id: The pipeline id, which keys the job entry.
        keep_going: Whether one failing job leaves independent jobs running.
    """
    def _on_process_started(proc):
        # Hot-write the live Popen handle into the active-jobs registry so
        # the cancel endpoint can locate the subprocess and terminate its
        # process tree.
        try:
            _active_jobs[pipeline_id]["proc"] = proc
        except Exception:
            pass  # Race with cancel-after-completion; ignore.

    def _is_cancelled():
        return bool(_active_jobs.get(pipeline_id, {}).get("cancel_requested"))

    口 = Step(step_num=1, name="Run the pipeline",
             purpose="Call Execution's run_pipeline through the seam with output "
                     "captured, the process registry and cancel probe over the "
                     "job entry, and the archive progress writer")
    try:
        _rp = run_pipeline_fn()
        _rp(
            pipeline_path=str(pipeline_path),
            project_root=project_root,
            pipeline_id=pipeline_id,
            capture_output=True,
            keep_going=keep_going,
            process_registry=_on_process_started,
            is_cancelled=_is_cancelled,
            archive_progress_fn=_make_archive_progress_writer(),
        )
    except Exception as exc:
        口 = Step(step_num=2, name="Close a failed run unless a cancel was requested",
                 purpose="Flip the pipeline's rows to failed through the seam, "
                         "best-effort; when the cancel route already asked for a "
                         "cancel, leave the rows to cancel_pipeline")
        # If the cancel endpoint already requested cancellation, suppress
        # the orphan-failure flip — cancel_pipeline owns the row state.
        if not _active_jobs.get(pipeline_id, {}).get("cancel_requested"):
            try:
                _fp = fail_pipeline_fn()
                _fp(pipeline_id)
            except Exception:
                pass  # Best-effort cleanup

        口 = Step(step_num=3, name="Record the classified error",
                 purpose="Write the kind-tagged payload of the exception onto the "
                         "job entry, which the status route reports",
                 critical="An entry removed while the run ended is tolerated "
                          "(KeyError only)")
        try:
            _active_jobs[pipeline_id]["error"] = _classify_pipeline_error(exc)
        except KeyError:
            pass  # The entry was removed while the run ended; nothing reads it.


def submitted_document(pipeline: PipelineInput) -> Dict[str, Any]:
    """The posted pipeline as a document, in the form it was submitted.

    Variables and ``{$var}`` refs stay intact. The run route keeps this form
    for History's reopen and prepares it; the cache-status route prepares
    the same form, so both routes hand preparation one document.

    Args:
        pipeline: The posted pipeline.

    Returns:
        The document dict.
    """
    document: Dict[str, Any] = {
        "name": pipeline.name,
        "nodes": [n.model_dump(exclude_none=True) for n in pipeline.nodes],
        "links": [l.model_dump(exclude_none=True) for l in pipeline.links],
        "samples": pipeline.samples,
    }
    if pipeline.param_sets:
        document["param_sets"] = pipeline.param_sets
    if pipeline.explicit_combos:
        document["explicit_combos"] = pipeline.explicit_combos
    if pipeline.variables:
        document["variables"] = pipeline.variables
    return document


@workflow(
    purpose="Submit a pipeline document: refuse an empty one, gate on run "
            "readiness, substitute its variables, enrich it, write the frozen "
            "document and the editable form, then register the job and start "
            "the background run",
    inputs="The canvas's pipeline document and the served project's root",
    outputs="The pipeline id and the step map. Raises SubmissionRefused (or "
            "RunNotReady) before anything is written or started",
)
def submit_pipeline(pipeline: PipelineInput, project_root: Path) -> SubmittedPipeline:
    """Submit a pipeline document for a background run.

    Multiple pipelines may run concurrently; each gets its own
    pipeline_id-scoped run directory and job entry.

    Args:
        pipeline: The canvas's pipeline document. Its node params and param
            sets are replaced in place by their substituted values.
        project_root: The served project's root. Git readiness is probed
            against it, not the process cwd.

    Returns:
        The pipeline id and the step map (node id to method name).

    Raises:
        RunNotReady: Docker or git is not ready; carries the kind-tagged payload.
        SubmissionRefused: The document has no nodes, or names a variable it
            does not declare, or its variables fail to resolve.
    """
    口 = Step(step_num=1, name="Refuse an empty document",
             purpose="A document with no nodes is refused before any probe, "
                     "write or thread")
    if not pipeline.nodes:
        raise SubmissionRefused("Pipeline has no nodes")

    口 = Step(step_num=2, name="Gate on run readiness",
             purpose="Probe Docker, then git against the served project (not the "
                     "process cwd), through the readiness checks `wfc doctor` "
                     "shares; a failing probe refuses with its kind-tagged payload "
                     "so no orphan run starts",
             critical="The probes are imported at call time, so a patch on "
                      "wfc.execution.readiness reaches them")
    root = str(project_root)
    from ..execution.readiness import check_docker, check_git
    docker_check = check_docker()
    if docker_check.status == "fail":
        raise RunNotReady(_readiness_payload(docker_check))
    git_check = check_git(root)
    if git_check.status == "fail":
        raise RunNotReady(_readiness_payload(git_check))

    口 = Step(step_num=3, name="Keep the submitted form",
             purpose="The document as submitted, variables and {$var} refs "
                     "intact, is what History's reopen rehydrates")
    pre_sub_dict = submitted_document(pipeline)

    口 = AutoStep(step_num=4, name="Prepare the document")
    from ..execution.prepare import prepare_document
    from ..graph import UnknownVariableError
    try:
        pipeline_json = prepare_document(pre_sub_dict)
    except UnknownVariableError as exc:
        raise SubmissionRefused(f"Unknown pipeline variable: '{exc.name}'")
    except ValueError as exc:
        raise SubmissionRefused(str(exc))

    口 = Step(step_num=5, name="Write the frozen document and the editable form",
             purpose="Mint the pipeline id, create its run directory through "
                     "Layout, write the enriched pipeline.json the run reads, and "
                     "write the submitted form as pipeline.editable.json for "
                     "History's reopen (best-effort)")
    pipeline_id = str(uuid.uuid4())

    pipeline_dir = layout.pipeline_run_dir(Path(project_root), pipeline_id)
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    pipeline_path = pipeline_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps(pipeline_json, indent=2), encoding="utf-8")

    # Persist the pre-substitution form so History "Open in canvas" can
    # rehydrate the Pipeline Variables panel and per-row binding chips.
    # Writes the user-submitted shape (with `variables` block + `{$var}`
    # refs intact). A run that lacks this sidecar falls back to
    # pipeline.json.
    editable_path = pipeline_dir / "pipeline.editable.json"
    try:
        editable_path.write_text(
            json.dumps(pre_sub_dict, indent=2, default=str), encoding="utf-8"
        )
    except OSError:
        pass  # Sidecar is best-effort; submission still proceeds.

    口 = Step(step_num=6, name="Register the job and start the run",
             purpose="Build the step map, install the job entry (thread, step "
                     "map, log directory, no error, no process, no cancel), then "
                     "start the background run",
             critical="The entry is installed before the thread starts: the "
                      "run's callbacks, its failure branch, and the status and "
                      "cancel routes read it")
    # Build step mapping: node_id -> method_name (for frontend status tracking)
    step_map = {n.id: n.method for n in pipeline.nodes}

    thread = threading.Thread(
        target=run_in_background,
        kwargs={
            "pipeline_path": pipeline_path,
            "project_root": root,
            "pipeline_id": pipeline_id,
            "keep_going": pipeline.keep_going,
        },
        daemon=True,
    )
    _active_jobs[pipeline_id] = {
        "thread": thread,
        "pipeline_id": pipeline_id,
        "step_map": step_map,
        "log_dir": str(pipeline_dir),
        "error": None,
        "proc": None,
        "cancel_requested": False,
    }
    thread.start()

    return SubmittedPipeline(pipeline_id=pipeline_id, step_map=step_map)
