"""Builder routes: the module palette, validation, and output-column resolution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ...persistence import MethodContract, Module, Run, get_session
from ..models import PipelineInput
from ..state import _require_provider

router = APIRouter()


# =============================================================================
# Modules endpoint -- live DB query, no provider required
# =============================================================================


@router.get("/api/modules")
def get_modules():
    """Return modules as a nested dict keyed by module/method name for the builder UI.

    Shape: {moduleName: {description, methods: {methodName: {inputs, outputs, ...}}}}
    The builder's module palette reads this shape.
    """
    with get_session() as session:
        modules = session.exec(select(Module)).all()
        result: dict[str, Any] = {}
        for mod in modules:
            methods_dict: dict[str, Any] = {}
            for meth in mod.methods:
                mc: MethodContract | None = meth.contract
                methods_dict[meth.name] = {
                    "inputs":        mc.input_slots   if mc else {},
                    "outputs":       mc.output_slots  if mc else {},
                    "version":       "1.0.0",
                    "description":   f"{mod.name} — {meth.name}",
                    "params_schema": mc.params_schema if mc else {},
                    "env":           meth.env,
                    "executor":      mc.executor      if mc else "python",
                }
            result[mod.name] = {
                "description": mod.description or mod.name,
                "methods": methods_dict,
            }
    return result


# =============================================================================
# Workflow builder endpoints
# =============================================================================


@router.post("/api/workflow/validate")
def validate_workflow(pipeline: PipelineInput):
    """Validate a pipeline graph against registered methods in the DB.

    The thin wrapper over the Graph unit's structural core: fetch the
    contract map through Registration and hand the request document to
    ``wfc.graph.validate_structure``, which answers ``{valid, errors,
    warnings}``.
    """
    from ...graph import validate_structure
    from ...registration import load_contract_map

    with get_session() as session:
        contract_map = load_contract_map(session)

    return validate_structure(pipeline.model_dump(), contract_map)


# ---------------------------------------------------------------------------
# column_of_input output column resolution
# ---------------------------------------------------------------------------


@router.get("/api/contracts/{method_full}/output_columns")
def get_output_columns(method_full: str, slot: str, params: str | None = None,
                       run_id: str | None = None):
    """Resolve declared output columns for a method's slot.

    The canvas inspector calls this for params declared with
    ``column_of_input: <slot>`` to populate a dropdown of candidate column
    names. Reuses ``wfc.contracts.resolve_columns`` against the upstream
    method's contract.

    Args:
        method_full: ``module.method`` (e.g. ``data_preprocessing.regionprops``).
        slot: Output slot name to resolve columns for.
        params: JSON-encoded dict of upstream node's current canvas params
            (used by ``from_params`` expansion). Optional; missing/empty
            falls back to ``{}``.
        run_id: When the upstream is a ``run_reference`` node, pass the
            referenced run id; the endpoint then uses Run.params from the
            DB as the resolution context.

    Returns:
        ``{strict, from_params, patterns, all}`` where ``all`` is the
        union of resolved column names produced by ``resolve_columns``.
        ``patterns`` are returned literally (resolution requires a CSV;
        not done here per the no-introspection constraint).
    """
    import json as _json

    from ...contracts import resolve_columns

    parsed_params: dict[str, Any] = {}
    if run_id:
        # Look up Run.params in the wfc DB for run_reference upstream.
        _require_provider()
        with get_session() as session:
            try:
                rid = int(run_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id}") from exc
            run = session.get(Run, rid)
            if not run:
                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
            parsed_params = run.params or {}
            method_full = f"{run.module}.{run.method}"
    elif params:
        try:
            parsed_params = _json.loads(params)
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}") from exc

    if "." not in method_full:
        raise HTTPException(status_code=400, detail="method must be 'module.method'")
    module_name, method_name = method_full.split(".", 1)

    with get_session() as session:
        modules_db = {m.name: m for m in session.exec(select(Module)).all()}
        mod = modules_db.get(module_name)
        if not mod:
            raise HTTPException(status_code=404, detail=f"Module {module_name} not found")
        meth = next((m for m in mod.methods if m.name == method_name), None)
        if not meth:
            raise HTTPException(status_code=404, detail=f"Method {method_full} not found")
        mc = meth.contract
        output_slots = mc.output_slots if mc else {}
        slot_spec = output_slots.get(slot, {})
        if not isinstance(slot_spec, dict):
            slot_spec = {}
        cols_spec = slot_spec.get("columns") or {}

    strict = list(cols_spec.get("strict", []) or [])
    from_params_specs = list(cols_spec.get("from_params", []) or [])
    patterns = list(cols_spec.get("patterns", []) or [])
    all_cols = sorted(resolve_columns(cols_spec, parsed_params))

    return {
        "strict": strict,
        "from_params": from_params_specs,
        "patterns": patterns,
        "all": all_cols,
    }
