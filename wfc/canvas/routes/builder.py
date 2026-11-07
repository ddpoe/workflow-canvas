"""Builder routes: the module palette, validation, and an output slot's options."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import col, select

from ...persistence import MethodContract, Module, Run, get_session
from ..models import PaletteModule, PipelineInput, ValidateResponse
from ..state import _require_provider

router = APIRouter()


def _version_contract(module_name: str, method_name: str,
                      version: str | None = None) -> MethodContract | None:
    """A method version's contract (the newest when *version* is ``None``).

    Args:
        module_name: The method's module.
        method_name: The method's name.
        version: A version's content identity, or ``None`` for the newest.

    Returns:
        The contract, or ``None`` for a method with no version list.

    Raises:
        HTTPException: 404 when no version of the method carries *version*.
    """
    from ...registration import (
        MalformedMethodRecordError,
        UnknownVersionError,
        method_contract,
    )

    try:
        return method_contract(module_name, method_name, version)
    except UnknownVersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MalformedMethodRecordError:
        return None


def _contract_fields(mc: MethodContract | None) -> dict[str, Any]:
    """The palette's contract fields for one version (empty ones without a contract)."""
    return {
        "inputs":        mc.input_slots   if mc else {},
        "outputs":       mc.output_slots  if mc else {},
        "params_schema": mc.params_schema if mc else {},
        "executor":      mc.executor      if mc else "python",
    }


def _palette_versions(module_name: str, method_name: str) -> list[dict[str, Any]]:
    """Every version of a method, in label order, each with its contract.

    Each entry names the version by its content identity (what a pin holds)
    and its label (for display), with its registration time, env and whether
    it is the newest. A method with no version list has none.
    """
    from ...registration import MalformedMethodRecordError, version_contracts

    try:
        pairs = version_contracts(module_name, method_name)
    except MalformedMethodRecordError:
        return []
    newest = len(pairs) - 1
    return [
        {
            "label":         synced.entry.label,
            "identity":      synced.entry.identity,
            "registered_at": synced.entry.registered_at,
            "env":           synced.entry.env_name,
            "newest":        index == newest,
            **_contract_fields(contract),
        }
        for index, (synced, contract) in enumerate(pairs)
    ]


# =============================================================================
# Modules endpoint -- live DB query, no provider required
# =============================================================================


@router.get("/api/modules", response_model=dict[str, PaletteModule])
def get_modules():
    """Return modules as a nested dict keyed by module/method name for the builder UI.

    Shape: {moduleName: {description, color, methods: {methodName: {inputs,
    outputs, params_schema, executor, env, description, versions}}, drafts:
    {methodName: {kind, folder, ...contract, env, valid, reasons}}}}. The
    method's own contract fields and env are its newest version's;
    ``versions`` lists every version (label, identity, registered_at, env,
    newest) with its contract. ``color`` is the module's stored colour and
    ``drafts`` the module's drafts (the palette's Drafts group). The
    builder's module palette reads this shape.
    """
    from ...registration import list_drafts
    from .. import draft_views
    from ..state import _server_project_root
    from .registry import _module_colors

    root = _server_project_root().resolve()
    try:
        pointers = list_drafts(root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    with get_session() as session:
        modules = session.exec(select(Module).order_by(col(Module.id))).all()
        # The colour every view shows: Registration's store (a module with
        # none stored gets its default first, as the Registry's list does).
        colors = _module_colors([mod.name for mod in modules])
        result: dict[str, Any] = {}
        for mod in modules:
            methods_dict: dict[str, Any] = {}
            for meth in mod.methods:
                versions = _palette_versions(mod.name, meth.name)
                newest = versions[-1] if versions else None
                fields = ({k: newest[k] for k in
                           ("inputs", "outputs", "params_schema", "executor")}
                          if newest else _contract_fields(None))
                methods_dict[meth.name] = {
                    **fields,
                    "description":   f"{mod.name} — {meth.name}",
                    "env":           newest["env"] if newest else meth.env,
                    "versions":      versions,
                }
            result[mod.name] = {
                "description": mod.description or mod.name,
                "methods": methods_dict,
                "color": colors.get(mod.name),
                "drafts": {},
            }
    for pointer in pointers:
        if pointer.module in result:
            result[pointer.module]["drafts"][pointer.method] = \
                draft_views.palette_draft(pointer, root)
    return result


# =============================================================================
# Workflow builder endpoints
# =============================================================================


@router.post("/api/workflow/validate", response_model=ValidateResponse)
def validate_workflow(pipeline: PipelineInput):
    """Validate a pipeline graph against registered methods in the DB.

    The thin wrapper over Execution's ``document_verdict``, the verdict
    ``wfc check-pipeline`` gives the same document: Graph's structural
    check over the substituted document, the validator's refusals and
    ``draftRefusals``, each draft node that cannot run (no draft or its
    folder gone, a failing Registration check, a draft run in progress).
    """
    from ...execution.validation import document_verdict
    from ..state import _server_project_root

    return document_verdict(pipeline.model_dump(), _server_project_root())


# ---------------------------------------------------------------------------
# An output slot's options, for the Inspector's options picker
# ---------------------------------------------------------------------------


class OutputOptionsRequest(BaseModel):
    """What the options picker asks of an upstream output slot.

    A method upstream names its pinned ``version`` (absent: the newest) and
    its ``param_sets``: the node's base values and each variant's and
    per-sample override's, each as the node holds them. A Run Reference
    upstream names its ``run_id`` instead; the run names its own method,
    version and params, and the other fields are not read.

    Attributes:
        slot: The upstream's output slot.
        version: The upstream node's pinned version (its content identity).
        param_sets: The upstream's param sets; none is one empty set.
        run_id: The referenced run's id.
    """

    slot: str
    version: str | None = None
    param_sets: list[dict[str, Any]] = Field(default_factory=list)
    run_id: int | str | None = None


def _run_upstream(run_id: int | str) -> tuple[str, str, MethodContract | None, dict]:
    """The method, contract and params a referenced run ran with.

    The contract is the one the run's own version was registered with; a
    run with no version (an old or refused row) reads the newest version's.

    Args:
        run_id: The run's id, as the request carries it.

    Returns:
        ``(module, method, contract, params)``.

    Raises:
        HTTPException: 400 for an id that is not an integer, 404 for no
            such run.
    """
    _require_provider()
    try:
        rid = int(run_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id}") from exc
    with get_session() as session:
        run = session.get(Run, rid)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        module_name, method_name = run.method.module.name, run.method.name
        params = dict(run.params or {})
        contract = None
        if run.version_id is not None:
            contract = session.exec(select(MethodContract).where(
                MethodContract.version_id == run.version_id)).first()
            if contract is not None:
                session.expunge(contract)
    if contract is None:
        contract = _version_contract(module_name, method_name)
    return module_name, method_name, contract, params


@router.post("/api/contracts/{method_full}/output_options")
def resolve_output_options(method_full: str, request: OutputOptionsRequest):
    """Resolve the options an upstream method's output slot offers.

    The Inspector's options picker calls this for a parameter declared with
    ``options_from: <input>``, once per upstream wired to that input. Each
    param set is filled with the version's declared defaults and the slot's
    ``options`` block is expanded over them with
    ``wfc.contracts.resolve_options``. A Run Reference upstream resolves
    through its run: the run's method and version, and one param set, the
    params it ran with. *method_full* is then not read (the canvas sends a
    placeholder).

    Args:
        method_full: ``module.method`` (e.g. ``features.regionprops``).
        request: The slot, and either the version and param sets or a run id.

    Returns:
        ``{fixed, built: [{params, names}], patterns, all}`` in declaration
        order, each name once; ``patterns`` are returned literally (matching
        one needs the data, which is not read here). A Run Reference's
        answer also names the run's ``module`` and ``method``.

    Raises:
        HTTPException: 400 for a method name with no dot or a run id that is
            not an integer; 404 for an unknown run, or, without a run id, an
            unknown module, method or version.
    """
    from ...contracts import fill_declared_defaults, resolve_options

    if request.run_id is not None:
        module_name, method_name, contract, run_params = _run_upstream(request.run_id)
        param_sets = [run_params]
    else:
        if "." not in method_full:
            raise HTTPException(status_code=400, detail="method must be 'module.method'")
        module_name, method_name = method_full.split(".", 1)
        with get_session() as session:
            mod = session.exec(select(Module).where(Module.name == module_name)).first()
            if not mod:
                raise HTTPException(status_code=404,
                                    detail=f"Module {module_name} not found")
            if not any(m.name == method_name for m in mod.methods):
                raise HTTPException(status_code=404,
                                    detail=f"Method {method_full} not found")
        contract = _version_contract(module_name, method_name, request.version)
        param_sets = request.param_sets or [{}]

    slot_spec = ((contract.output_slots if contract else None) or {}).get(request.slot)
    options = slot_spec.get("options") if isinstance(slot_spec, dict) else None
    schema = contract.params_schema if contract else None
    resolved = resolve_options(
        options if isinstance(options, dict) else None,
        [fill_declared_defaults(params, schema) for params in param_sets])
    if request.run_id is not None:
        # The canvas knows a Run Reference only by its run id; the picker's
        # header names the run's method in its module's colour.
        return {**resolved, "module": module_name, "method": method_name}
    return resolved
