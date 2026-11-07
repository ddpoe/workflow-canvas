"""``wfc try``'s launch: one draft step run as a one-node pipeline, recording nothing.

A try is a pipeline named ``try-<method>``: Contracts' one-step document
for the draft node (with an input selector for its sample, feeding the one
input no parent entry feeds, when one is left). Its
documents are frozen through the launch's own freeze, its ``pipeline_runs``
row is written through the launch writer, and the draft step runs directly,
with no engine. Like every draft step it writes no run row, no Blob row and
no cache entry, which is why the try does not go through ``prepare_launch``
(that keeps the frozen document in the cache).

However the try ends -- completed, failed, refused, interrupted -- its
running draft steps are ended before its outcome is stored, so nothing is
left in progress. A try that ran no step (refused before it) removes its
row, as a refused launch does.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, workflow

from ..contracts import DRAFT_PIN, SampleInputError, one_step_document
from ..graph import validate_structure
from ..persistence import CANCEL_REASON_USER, require_project_root
from .draft import (
    end_running_draft_steps,
    pipeline_draft_steps,
    preflight_drafts,
    run_draft_step,
)
from .lifecycle import freeze_launch_documents
from .parents import parse_parent_entries
from .pipeline_outcome import (
    discard_pipeline_launch,
    record_pipeline_launch,
    store_pipeline_outcome,
)
from .prepare import enrich_document, read_contract_map

#: What a try (or a wired session) refused for several candidate inputs says
#: to do: the sample feeds one input, parent entries feed the rest.
FEED_THE_OTHERS = ("Feed all but one of them with --parent-run-id "
                   "(input:output:run); the sample feeds the one left.")


class TryRefusedError(ValueError):
    """The try's document cannot be built; nothing was written."""


@dataclass(frozen=True)
class TryRequest:
    """What one try runs.

    Attributes:
        module: The draft's module.
        method: The method (the folder's name), also the node id.
        folder: The draft folder.
        params: The step's parameter values.
        sample: The sample the step runs as (and reads, into the one input
            no parent entry feeds, when one is left).
        parent_entries: Parent entries as typed (``input:output:run``).
    """

    module: str
    method: str
    folder: Path
    params: dict[str, Any]
    sample: str
    parent_entries: list[str]


@dataclass(frozen=True)
class TryOutcome:
    """How a try ended.

    Attributes:
        pipeline_id: The try's pipeline id.
        returncode: 0 when the step completed, 1 otherwise.
        refusal: Why the try was refused before its step, when it was.
    """

    pipeline_id: str
    returncode: int
    refusal: str | None = None


def try_document(request: TryRequest,
                 contract_map: Mapping[str, Any]) -> dict[str, Any]:
    """The one-node pipeline document a try runs, as the user would submit it.

    Built by Contracts' one-step document: the draft node (``version:
    "draft"`` and its folder), and an input selector for the sample feeding
    the one input no parent entry feeds, when one is left; a document that
    holds the selector then passes Graph's structural checks.

    Args:
        request: The try.
        contract_map: The registered contract map (holding the draft).

    Returns:
        The sparse document, named ``try-<method>``.

    Raises:
        ValueError: A parent entry is malformed, the sample could feed more
            than one input (``SampleInputError``, naming them), or the draft
            cannot run (named).
    """
    fed = try_fed_outside(request)
    document = one_step_document(
        f"{request.module}.{request.method}", request.sample, contract_map,
        version=DRAFT_PIN, folder=str(request.folder), params=request.params,
        fed=fed[request.method], name=f"try-{request.method}")
    if _feeds_the_sample(document):
        # Graph's structural checks, the parent entries' inputs counted as
        # connected; the method node alone (every input fed by a parent
        # entry, outside the document) is not a pipeline root to check.
        verdict = validate_structure(document, dict(contract_map), fed_outside=fed)
        if verdict["errors"]:
            raise ValueError("; ".join(verdict["errors"]))
    return document


def try_fed_outside(request: TryRequest) -> dict[str, list[str]]:
    """The inputs a try's parent entries feed, outside its document.

    Args:
        request: The try.

    Returns:
        ``{node id: [input slot, ...]}`` for the try's one node (its id is
        the method).

    Raises:
        ValueError: A parent entry is malformed.
    """
    return {request.method: [entry.input_slot for entry in parse_parent_entries(
        request.parent_entries, step=request.method, allow_drafts=True)]}


def _feeds_the_sample(document: Mapping[str, Any]) -> bool:
    """Whether a try's document feeds its sample to an input (holds the selector link)."""
    return bool(document.get("links"))


@workflow(purpose="Run wfc try's draft step as a one-node pipeline: build and "
                  "enrich its document, refuse it through the draft preflight, freeze both forms through the launch's "
                  "freeze, write its pipeline_runs row, restore the sample, run "
                  "the draft step; then end any running draft step and store "
                  "the outcome, or remove the row when no step ran",
          inputs="the try (module, method, folder, params, sample, parent "
                 "entries), the project root",
          outputs="TryOutcome: the pipeline id, the return code, a refusal",
          critical="writes no run row, Blob row or cache entry; whatever ends "
                   "the try, no draft step of it is left running and its row "
                   "is settled")
def run_try(request: TryRequest, root: Path, *,
            on_start: Callable[[], None] | None = None) -> TryOutcome:
    """Run one try end to end.

    Args:
        request: The try.
        root: The project root: the process's own (any spelling that
            resolves to it).
        on_start: Called once the draft step starts (its record written),
            never for a try refused before it.

    Returns:
        How the try ended.

    Raises:
        TryRefusedError: The document cannot be built or enriched, or the
            draft preflight refuses it (nothing written; the previous
            draft run stays).
        ProjectMismatchError: ``root`` is not the process's project root;
            nothing was written.
        KeyboardInterrupt: The try was interrupted; its draft step was ended
            as cancelled and its outcome stored first.
    """
    root = require_project_root(root)

    口 = Step(step_num=1, name="Build the document",
             purpose="The sparse one-node document through Contracts' one-step "
                     "document (the sample feeds the one input left to feed), "
                     "then its enriched form through Execution's enrichment; a "
                     "refusal writes nothing")
    try:
        contract_map = read_contract_map()
        sparse = try_document(request, contract_map)
        enriched = enrich_document(sparse, contract_map)
    except SampleInputError as exc:
        raise TryRefusedError(f"{exc} {FEED_THE_OTHERS}") from exc
    except ValueError as exc:
        raise TryRefusedError(str(exc)) from exc

    try:
        口 = AutoStep(step_num=2, name="The draft preflight")
        preflight_drafts(sparse, root, fed_outside=try_fed_outside(request))
    except ValueError as exc:
        raise TryRefusedError(str(exc)) from exc

    口 = AutoStep(step_num=3, name="Freeze the documents")
    frozen = freeze_launch_documents(root, sparse, enriched)
    pipeline_id = frozen.pipeline_id

    口 = AutoStep(step_num=4, name="Write the launch row")
    record_pipeline_launch(pipeline_id, sparse)

    ending = {"status": "failed", "message": "wfc try ended before its step did",
              "cancel_reason": None}
    try:
        口 = Step(step_num=5, name="Restore the sample",
                 purpose="Only when the sample feeds an input; a sample that "
                         "cannot be read refuses the try before its step")
        if _feeds_the_sample(sparse):
            from ..storage import restore_sample
            try:
                restore_sample(request.sample, project_root=root)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - named for the user
                return TryOutcome(
                    pipeline_id=pipeline_id, returncode=1,
                    refusal=f"sample '{request.sample}' cannot be read: {exc}")

        口 = AutoStep(step_num=6, name="Run the draft step")
        rc = run_draft_step(request.method, request.sample, "default",
                            pipeline_id=pipeline_id, document=enriched,
                            parent_run_ids=request.parent_entries,
                            pipeline_name=enriched.get("name"),
                            on_start=on_start)
        return TryOutcome(pipeline_id=pipeline_id, returncode=rc)
    except KeyboardInterrupt:
        ending = {"status": "cancelled", "message": "Cancelled by user",
                  "cancel_reason": CANCEL_REASON_USER}
        raise
    finally:
        口 = AutoStep(step_num=7, name="End the try's running draft steps")
        end_running_draft_steps(pipeline_id, root=root, **ending)

        口 = Step(step_num=8, name="Settle the pipeline row",
                 purpose="Store the outcome through the status helper after the "
                         "last step write; a try that ran no step removes its "
                         "row, as a refused launch does")
        if pipeline_draft_steps(pipeline_id, root):
            store_pipeline_outcome(root, pipeline_id)
        else:
            discard_pipeline_launch(pipeline_id)
