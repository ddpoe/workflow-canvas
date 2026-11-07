"""Document validation: the one verdict every door gives a pipeline document.

The canvas's validate route, its submission, ``wfc run-pipeline`` and the
runs preview all ask this module, after variable substitution, whether a
document may run. The verdict is the list of refusals, each naming the node
and the field:

- Graph's key check: a top-level or node key wfc does not read (a node's
  ``script`` included) is refused by name;
- Contracts' parameter check: each method node's params against its
  resolved version's contract (its pin, or the newest version when it has
  none), asked of Registration -- an undeclared parameter, a missing
  required one, a wrong type, a value outside ``min``/``max``/``enum``.
  Each of the node's variants under the document's ``param_sets`` runs in
  place of its params, so each gets the same check against the same
  version, its refusals naming the variant; a variant that is not a
  mapping of parameters is refused naming node and variant.
  A pin that names no version of the method is refused naming both.
- a node pinned to ``draft`` runs its method's draft: it is refused when
  the draft cannot run now (Registration's ``draft_run_refusal``: no
  draft, another folder, its folder gone) or its ``method.yaml`` cannot be
  read, and otherwise its params and variants are checked against the
  params its ``method.yaml`` declares now (no Update needed first). An
  unpinned node on a DRAFT method (a draft and no registered version) is
  refused in Contracts' words (``version_refusal``).

A node whose method is not registered or has no version list is left to
the structure check and to enrichment, which refuse it by name. A version
with no contract declares no params, so its node's params and variants are
not checked. Nothing is filled in: a parameter the document leaves out
stays out.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, task

from ..contracts import DRAFT_PIN, param_refusals, version_refusal
from ..graph import document_key_refusals
from ..registration import (
    MalformedMethodRecordError,
    chosen_version,
    draft_contract_now,
    draft_run_refusal,
    read_draft,
    version_contracts,
)
from .composer import PipelineDocumentError as PipelineDocumentError
from .composer import _composer_session

_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


class PipelineRefusedError(PipelineDocumentError):
    """A pipeline document the validator refuses; nothing was prepared or run.

    One kind of ``PipelineDocumentError`` (and so a ``ValueError``), carrying
    every refusal rather than the first.

    Attributes:
        refusals: Every refusal, each naming the node (or the document) and
            the field.
    """

    def __init__(self, refusals: list[str]):
        """Build the message: one refusal per line.

        Args:
            refusals: The validator's refusals (non-empty).
        """
        self.refusals = list(refusals)
        super().__init__("\n".join(self.refusals))


def _method_nodes(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The document's method nodes that name a module and a method."""
    return [
        n for n in document.get("nodes") or []
        if isinstance(n, Mapping)
        and (n.get("type") or "method") not in _SYSTEM_NODE_TYPES
        and n.get("module") and n.get("method")
    ]


@task(purpose="Give a substituted pipeline document its one validation verdict: "
              "Graph's unknown-key refusals, then each method node's params "
              "and each of its param_sets variants against its resolved "
              "version's contract through Contracts",
      inputs="A pipeline document after variable substitution",
      outputs="Every refusal, naming node, field and version (empty when the "
              "document may run)",
      critical="The same function at every door: canvas validate, canvas "
               "submit, run-pipeline and the runs preview")
def validation_refusals(document: Mapping[str, Any]) -> list[str]:
    """List every refusal of a substituted pipeline document.

    Args:
        document: The document after ``{$var}`` substitution.

    Returns:
        The refusals: unknown keys first, then, per method node in document
        order, its params' refusals followed by each of its ``param_sets``
        variants' refusals in document order. Empty when the document may
        run.

    Raises:
        DatabaseUnreachableError: A method node's contract is needed and the
            project database cannot be reached.
    """
    口 = Step(step_num=1, name="Refuse unknown keys",
             purpose="Graph names every top-level and node key wfc does not read")
    refusals = document_key_refusals(document)

    口 = Step(step_num=2, name="Check each method node's params and variants against its version",
             purpose="Resolve the node's pin (or the newest version) from "
                     "Registration's versions and contracts, then hand its "
                     "params schema to Contracts' parameter check for the "
                     "node's params and once per param_sets variant",
             critical="A method with no versions is skipped here: the structure "
                      "check and enrichment refuse it by name. A version with "
                      "no contract declares no params and is not checked")
    nodes = _method_nodes(document)
    if not nodes:
        return refusals
    param_sets = document.get("param_sets")
    param_sets = param_sets if isinstance(param_sets, Mapping) else {}
    with _composer_session():
        pass  # the probe: an unreachable database is named before any read
    versions_of: dict[tuple[str, str], list | None] = {}
    for node in nodes:
        module, method = str(node["module"]), str(node["method"])
        if node.get("version") == DRAFT_PIN:
            refusals.extend(_draft_node_refusals(node, module, method,
                                                 param_sets.get(str(node.get("id")))))
            continue
        if (module, method) not in versions_of:
            try:
                versions_of[(module, method)] = version_contracts(module, method)
            except MalformedMethodRecordError:
                versions_of[(module, method)] = None
        versions = versions_of[(module, method)]
        if not versions:
            if not node.get("version") and _has_draft(module, method):
                # A DRAFT method: an unpinned node is never run as the draft.
                refusals.append(version_refusal(node, {"draft_only": True}))
            continue
        pin = node.get("version")
        chosen = chosen_version(versions, pin, entry_of=lambda vc: vc[0].entry)
        if chosen is None:
            labels = ", ".join(f"v{v.entry.label}" for v, _ in versions)
            refusals.append(
                f"Node '{node.get('id')}' ({module}.{method}): pinned version "
                f"{pin} is not a version of the method (its versions: "
                f"{labels})")
            continue
        version, contract = chosen
        if contract is None:
            continue  # a version with no contract declares no params
        node_id, label = str(node.get("id")), f"v{version.entry.label}"
        refusals.extend(param_refusals(
            node_id, f"{module}.{method}", label,
            node.get("params"), contract.params_schema))
        refusals.extend(_variant_refusals(
            node_id, f"{module}.{method}", label,
            param_sets.get(node_id), contract.params_schema))
    return refusals


def _has_draft(module: str, method: str) -> bool:
    """Whether the module's method has a draft (a pointer wfc cannot read counts)."""
    try:
        return read_draft(module, method) is not None
    except ValueError:
        return True


def _draft_node_refusals(node: Mapping[str, Any], module: str, method: str,
                         variants: Any) -> list[str]:
    """Refuse a node pinned to ``draft`` that cannot run, else check it against the draft.

    A draft that cannot run now (none, another folder, its folder gone) or
    whose ``method.yaml`` cannot be read is one refusal naming the node;
    otherwise the node's params and each of its variants are checked
    against the params its ``method.yaml`` declares now (read as the draft
    re-read reads it; nothing is written, and no Update is needed first).

    Args:
        node: The method node pinned to ``draft``.
        module: Its module.
        method: Its method.
        variants: The document's ``param_sets`` entry for the node, if any.

    Returns:
        The refusals; empty when the node may run its draft.
    """
    node_id, method_ref = str(node.get("id")), f"{module}.{method}"
    why = draft_run_refusal(module, method, node.get("folder"))
    if why is not None:
        return [f"Node '{node_id}' ({method_ref}): {why}"]
    pointer = read_draft(module, method)
    assert pointer is not None  # draft_run_refusal refuses no draft
    contract, error = draft_contract_now(pointer)
    if contract is None:
        return [f"Node '{node_id}' ({method_ref}): the draft's method.yaml "
                f"cannot be read: {error}"]
    return draft_param_refusals(node, variants, contract)


def draft_param_refusals(node: Mapping[str, Any], variants: Any,
                         contract: Mapping[str, Any]) -> list[str]:
    """Check a draft node's params and ``param_sets`` variants against a draft contract.

    Nothing is dropped or adjusted: a param the contract no longer declares,
    or a value of the wrong type, is refused naming the node and the field.

    Args:
        node: The method node pinned to ``draft``.
        variants: The document's ``param_sets`` entry for the node, if any.
        contract: The draft's contract read now (``parse_method_yaml``'s form).

    Returns:
        The refusals, the node's params first, then each variant's.
    """
    node_id = str(node.get("id"))
    method_ref = f"{node.get('module')}.{node.get('method')}"
    schema = contract.get("params")
    return (param_refusals(node_id, method_ref, "DRAFT", node.get("params"), schema)
            + _variant_refusals(node_id, method_ref, "DRAFT", variants, schema))


def _variant_refusals(node_id: str, method_ref: str, version_label: str,
                      variants: Any, params_schema: Mapping[str, Any] | None,
                      ) -> list[str]:
    """Check each of a node's ``param_sets`` variants like its params.

    Args:
        node_id: The node's document id.
        method_ref: ``module.method``.
        version_label: The resolved version's label, e.g. ``"v2"``.
        variants: The document's ``param_sets[node_id]``: variant name to
            params, or ``None`` when the node has none.
        params_schema: The version's declared params.

    Returns:
        The refusals, variants in document order; a variant that is not a
        mapping of parameters is refused naming node and variant.
    """
    if variants is None:
        return []
    if not isinstance(variants, Mapping):
        return [f"Node '{node_id}': its param_sets entry must map variant "
                f"names to params, got {type(variants).__name__}"]
    refusals: list[str] = []
    for name, values in variants.items():
        if not isinstance(values, Mapping):
            refusals.append(f"Node '{node_id}' variant '{name}': must be a "
                            f"mapping of parameters, got "
                            f"{type(values).__name__}")
            continue
        refusals.extend(param_refusals(node_id, method_ref, version_label,
                                       values, params_schema, variant=str(name)))
    return refusals


@task(purpose="Raise the validator's refusals for a substituted pipeline "
              "document, so nothing is prepared or run from a refused one",
      inputs="A pipeline document after variable substitution",
      outputs="None; PipelineRefusedError carrying every refusal when the "
              "document has any")
def refuse_invalid(document: Mapping[str, Any]) -> None:
    """Raise the validator's refusals for a substituted document, if any.

    Args:
        document: The document after ``{$var}`` substitution.

    Raises:
        PipelineRefusedError: The document has at least one refusal.
        DatabaseUnreachableError: The database cannot be reached.
    """
    refusals = validation_refusals(document)
    if refusals:
        raise PipelineRefusedError(refusals)


def _draft_nodes(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The document's nodes pinned to their method's draft."""
    return [n for n in document.get("nodes") or []
            if isinstance(n, Mapping) and n.get("version") == DRAFT_PIN
            and n.get("module") and n.get("method")]


@task(purpose="Name each draft node of a document that cannot run, without "
              "writing anything: Registration's no-draft, wrong-folder and "
              "folder-gone refusals and any ✗ of the draft's Registration check "
              "on the read-only re-read are draft_invalid; a draft run still in "
              "progress in another pipeline is draft_in_use",
      inputs="A pipeline document (substituted or not) and the project root",
      outputs="One refusal per refusing draft node, in document order, each "
              "with its node, draft, kind and message",
      critical="Writes nothing: the env image is not made runnable and the "
               "pointer is not stored; the launch's preflight does both")
def draft_refusals(document: Mapping[str, Any], root: Path | str) -> list[dict[str, Any]]:
    """Each draft node of a document that cannot run, as ``DraftRefusal``.

    ``draft_invalid`` when the draft cannot be read for a run (Registration's
    ``draft_run_refusal``: no draft, another folder, its folder gone; the
    reason is its message) or its Registration check, made on the read-only
    re-read of its folder now, fails (each ✗ line a reason);
    ``draft_in_use`` when Execution's ``draft_run_in_progress`` refuses it.

    Args:
        document: The pipeline document (variables substituted or not).
        root: The project root.

    Returns:
        One refusal per refusing draft node, in document order.
    """
    from ..registration import (
        DraftNotFoundError,
        DraftRefusedError,
        draft_run_refusal,
        reread_draft,
    )
    from .draft import draft_run_in_progress, read_draft_run

    root = Path(root)
    refusals: list[dict[str, Any]] = []
    for node in _draft_nodes(document):
        module, method = str(node["module"]), str(node["method"])
        node_id, qualified = str(node.get("id")), f"{module}.{method}"
        base = {"nodeId": node_id, "draft": qualified}
        口 = Step(step_num=1.1, name="Can the draft be read for a run",
                 purpose="Registration's no-draft, wrong-folder and folder-gone "
                         "refusals, then its read-only re-read's check")
        why = draft_run_refusal(module, method, node.get("folder"), root)
        reasons: list[str] = [why] if why is not None else []
        if not reasons:
            try:
                reasons = reread_draft(module, method, root, write=False).check.failures
            except (DraftNotFoundError, DraftRefusedError, ValueError) as exc:
                reasons = [str(exc)]
        if reasons:
            detail = (reasons[0] if why is not None else
                      f"the draft's Registration check fails: {'; '.join(reasons)}")
            refusals.append({**base, "kind": "draft_invalid", "reasons": reasons,
                             "message": f"Node '{node_id}' ({qualified}): {detail}"})
            continue
        口 = Step(step_num=1.2, name="Is its draft run still in progress",
                 purpose="Another pipeline's draft run of it still running "
                         "refuses, naming that pipeline")
        busy = draft_run_in_progress(module, method, root)
        if busy is not None:
            record = read_draft_run(module, method, root) or {}
            refusals.append({
                **base, "kind": "draft_in_use",
                "pipelineId": str(record.get("pipeline_id") or "") or None,
                "pipelineName": record.get("pipeline_name"),
                "message": f"Node '{node_id}' ({qualified}): {busy}"})
    return refusals


@task(purpose="Give a pipeline document its whole verdict without running or "
              "writing anything: Graph's structural check over the substituted "
              "document, the validator's refusals and the draft refusals, the "
              "verdict canvas Validate and check-pipeline both give",
      inputs="A pipeline document as submitted, and the project root",
      outputs="{valid, errors, warnings, draftRefusals}",
      critical="Each draft refusal's message is also an error, once")
def document_verdict(document: Mapping[str, Any],
                     root: Path | str | None = None) -> dict[str, Any]:
    """The verdict on a pipeline document: may it run, and if not, why not.

    Args:
        document: The document as submitted (its ``{$var}`` refs intact).
        root: The project root; the resolved one when omitted.

    Returns:
        ``{"valid", "errors", "warnings", "draftRefusals"}``: ``errors`` is
        Graph's structural errors, then the validator's refusals, then each
        draft refusal's message not already among them; ``valid`` is true
        when there are none. A variable the document does not declare, or
        variables that fail to resolve, is the one error.

    Raises:
        ProjectMismatchError: ``root`` is not the process's project root.
    """
    from ..graph import validate_structure
    from ..persistence import require_project_root
    from .prepare import read_contract_map, substitute_variables

    root = require_project_root(root)

    口 = Step(step_num=1, name="Substitute variables",
             purpose="An undeclared or unresolvable variable is the verdict's "
                     "one error, in the run path's words")
    try:
        substituted = substitute_variables(document)
    except PipelineDocumentError as exc:
        return {"valid": False, "warnings": [], "draftRefusals": [],
                "errors": [str(exc)]}

    口 = Step(step_num=2, name="Structure, refusals and drafts",
             purpose="Graph's validate_structure over the contract map, the "
                     "validator's refusals, the draft refusals")
    result = validate_structure(substituted, read_contract_map())
    errors = list(result.get("errors", [])) + validation_refusals(substituted)
    refusals = draft_refusals(substituted, root.resolve())
    errors += [r["message"] for r in refusals if r["message"] not in errors]
    return {**result, "errors": errors, "valid": not errors,
            "warnings": list(result.get("warnings", [])),
            "draftRefusals": refusals}


@task(purpose="Check a pipeline document the way a run would take it, "
              "without launching: the verdict, then, when it passes, the "
              "load's legality stage (prepare and load through the composer), "
              "whose refusal joins the verdict's errors in the load's words",
      inputs="A pipeline document as submitted, and the project root",
      outputs="{valid, errors, warnings, draftRefusals}",
      critical="No launch, no Docker, nothing written; a document it passes "
               "is one run_pipeline's preparation and load accept")
def check_document(document: Mapping[str, Any],
                   root: Path | str | None = None) -> dict[str, Any]:
    """The check-pipeline verdict on a document: the verdict plus the load.

    Args:
        document: The document as submitted (its ``{$var}`` refs intact).
        root: The project root; the resolved one when omitted.

    Returns:
        ``document_verdict``'s answer when it refuses the document;
        otherwise the same answer with each refusal of the preparation or
        the load (``PipelineDocumentError``) added to ``errors`` and
        ``valid`` false when there is one.

    Raises:
        DatabaseUnreachableError: The database cannot be reached.
        ProjectMismatchError: ``root`` is not the process's project root.
    """
    from ..persistence import require_project_root
    from .composer import load_pipeline_from_document
    from .prepare import prepare_document

    root = require_project_root(root)

    口 = AutoStep(step_num=1, name="The verdict")
    verdict = document_verdict(document, root)
    if not verdict["valid"]:
        return verdict

    口 = Step(step_num=2, name="Prepare and load",
             purpose="The run path's preparation and the composer's load, "
                     "with no launch; a refusal is the verdict's error in "
                     "the load's words")
    try:
        load_pipeline_from_document(prepare_document(document))
    except PipelineDocumentError as exc:
        refused = getattr(exc, "refusals", None) or [str(exc)]
        errors = verdict["errors"] + [e for e in refused if e not in verdict["errors"]]
        return {**verdict, "valid": False, "errors": errors}
    return verdict


@task(purpose="Check a pipeline file for check-pipeline: read it as "
              "run-pipeline reads it, then check_document",
      inputs="The pipeline file's path and the project root",
      outputs="{valid, errors, warnings, draftRefusals}, or "
              "PipelineDocumentError for a file that cannot be read")
def check_pipeline(pipeline_path: str | Path,
                   root: Path | str | None = None) -> dict[str, Any]:
    """The check-pipeline verdict on a pipeline file.

    Args:
        pipeline_path: The pipeline JSON file.
        root: The project root; the resolved one when omitted.

    Returns:
        ``check_document``'s answer for the document the file holds.

    Raises:
        PipelineDocumentError: The file is missing or is not a JSON object,
            in run-pipeline's words.
        DatabaseUnreachableError: The database cannot be reached.
        ProjectMismatchError: ``root`` is not the process's project root.
    """
    from ..persistence import require_project_root
    from .prepare import read_pipeline_document

    root = require_project_root(root)

    口 = AutoStep(step_num=1, name="Read the file")
    document = read_pipeline_document(pipeline_path)

    口 = AutoStep(step_num=2, name="Check the document")
    verdict = check_document(document, root)
    return verdict
