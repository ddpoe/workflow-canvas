"""
@wfc_method decorator and dispatcher for wfc-managed method scripts.

Eliminates boilerplate in method scripts by handling:
  - RunContext setup (env vars, input loading, output saving)
  - Return value classification (contract outputs vs free-form)
  - Metric classification (contract metrics vs informational)
  - Contract validation with actionable error messages

Usage in a method script::

    from wfc.method import wfc_method, wfc_method_main

    @wfc_method
    def ploidy_filter(inputs, params):
        df = pd.read_csv(inputs["data"][0])
        # ... science ...
        return df, {"n_cells_before": n_before, "n_cells_after": n_after}

    if __name__ == "__main__":
        wfc_method_main()
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# Registry of @wfc_method-decorated functions
# =============================================================================

_registry: list[Callable] = []


# =============================================================================
# ContractViolation exception
# =============================================================================

class ContractViolation(RuntimeError):
    """Raised when a @wfc_method function's return values don't satisfy module contracts.

    Provides an actionable error message showing what was missing,
    what was returned, and where contracts are defined.
    """

    def __init__(
        self,
        method: str,
        module: str | None = None,
        missing_outputs: list[str] | None = None,
        missing_metrics: list[str] | None = None,
        available_outputs: list[str] | None = None,
        available_metrics: list[str] | None = None,
        extra_outputs: list[str] | None = None,
        # ADR-005: content-level column validation fields
        missing_columns: list[str] | None = None,
        available_columns: list[str] | None = None,
        slot_name: str = "",
    ):
        self.method = method
        self.module = module
        self.missing_outputs = missing_outputs or []
        self.missing_metrics = missing_metrics or []
        self.available_outputs = available_outputs or []
        self.available_metrics = available_metrics or []
        self.extra_outputs = extra_outputs or []
        self.missing_columns = missing_columns or []
        self.available_columns = available_columns or []
        self.slot_name = slot_name

        lines = [f"ContractViolation: Method '{method}'"]
        if module:
            lines[0] += f" (module '{module}')"

        if self.missing_columns:
            lines.append("")
            slot_label = f" (slot '{slot_name}')" if slot_name else ""
            lines.append(f"  Missing required input columns{slot_label}:")
            for name in self.missing_columns:
                lines.append(f"    - {name}")
            if self.available_columns:
                lines.append(f"  Available columns: {self.available_columns}")

        if self.missing_outputs:
            lines.append("")
            lines.append("  Missing required outputs:")
            for name in self.missing_outputs:
                lines.append(f"    - {name}")

        if self.missing_metrics:
            lines.append("")
            lines.append("  Missing required metrics:")
            for name in self.missing_metrics:
                lines.append(f"    - {name}")

        if self.extra_outputs:
            lines.append("")
            lines.append("  Undeclared outputs returned (not in contract):")
            for name in self.extra_outputs:
                lines.append(f"    - {name}")

        if self.available_outputs or self.available_metrics:
            lines.append("")
            lines.append("  You returned:")
            if self.available_outputs:
                lines.append(f"    outputs: {self.available_outputs}")
            if self.available_metrics:
                lines.append(f"    metrics: {self.available_metrics}")

        lines.append("")
        lines.append("  Module contracts are defined by register_module(contracts=[...]).")
        lines.append("  Return matching keys from your @wfc_method function.")
        lines.append("  Use ctx.save() directly for truly free-form side-files.")

        super().__init__("\n".join(lines))


# =============================================================================
# @wfc_method decorator
# =============================================================================

def wfc_method(func: Callable) -> Callable:
    """Mark a function as a wfc-managed method entry point.

    The decorated function should accept ``(inputs, params)`` where
    ``inputs`` is ``dict[str, list[Path]] | None`` — slot names mapped
    to lists of input file paths.  Methods own their I/O: read files
    using whatever library is appropriate for the slot type.

    Return one of:

    - ``DataFrame`` — saved as the primary output
    - ``(DataFrame, metrics_dict)`` — saved as primary output + metrics
    - ``(outputs_dict, metrics_dict)`` — named outputs + metrics

    The decorator is a no-op at import time (just registers the function).
    The actual dispatch happens when ``wfc_method_main()`` is called.

    Args:
        func: The method function to decorate.

    Returns:
        The original function, unchanged, with ``_wfc_method = True`` set.
    """
    func._wfc_method = True  # type: ignore[attr-defined]
    _registry.append(func)
    return func


# =============================================================================
# Dispatcher — called from __main__
# =============================================================================

def wfc_method_main() -> None:
    """Run the @wfc_method-decorated function using RunContext.

    Reads PM_* environment variables, loads input data, calls the
    registered function, classifies outputs/metrics against module
    contracts, saves results, and writes ``_run_results.json``.

    This is the single entry point for ``if __name__ == "__main__"``.

    Raises:
        RuntimeError: If no @wfc_method function is registered.
        ContractViolation: If required contract outputs/metrics are missing.
    """
    if not _registry:
        raise RuntimeError(
            "wfc_method_main() called but no @wfc_method function was found. "
            "Ensure your function is decorated with @wfc_method before the "
            "if __name__ == '__main__' block."
        )

    func = _registry[-1]  # last registered = the user's function
    _execute_wfc_method(func)


def _execute_wfc_method(func: Callable) -> None:
    """Core dispatcher: load input → call func → classify returns → save → finalize.

    Args:
        func: The @wfc_method-decorated function.
    """
    from wfc.wfc_context import RunContext

    ctx = RunContext()

    # --- Load input --------------------------------------------------------
    # load_input() returns dict[str, list[Path]] or None.  Methods own
    # their I/O — they read files using whatever library fits the slot type.
    input_data = ctx.load_input()

    # --- ADR-005: Input column validation (hard gate) ----------------------
    method_name = ctx._context.get("method_name", "")
    input_slots, output_slots = _get_method_contract_slots(method_name)
    multi_paths_json = os.environ.get("WFC_INPUT_PATHS")
    if input_slots and multi_paths_json:
        from wfc.contracts import validate_input_columns
        slot_paths_for_val = json.loads(multi_paths_json)
        for slot, paths in slot_paths_for_val.items():
            slot_def = input_slots.get(slot, {})
            column_spec = slot_def.get("columns")
            if column_spec:
                slot_type = slot_def.get("type", "csv")
                if slot_type in ("csv", "parquet"):
                    for p in paths:
                        validate_input_columns(
                            Path(p), column_spec, ctx.params,
                            slot_name=slot, method_name=method_name,
                        )

    # --- Call the user function --------------------------------------------
    result = func(input_data, ctx.params)

    # --- Parse return value ------------------------------------------------
    outputs, metrics = _parse_return(result)

    # --- Look up contracts for this method's module ------------------------
    method_name = ctx._context.get("method_name", "")
    output_contracts, metric_contracts, module_name = _get_module_contracts(method_name)

    # If DB lookup failed (isolated env without sqlmodel), synthesize output
    # contracts from the slot_outputs map embedded in _run_context.json.  This
    # guarantees the correct file extension (e.g. ".csv") even when the DB is
    # not importable, so Snakemake's expected output filenames are honoured.
    if not output_contracts:
        slot_outputs = ctx._context.get("slot_outputs", {})
        for slot_name, filename in slot_outputs.items():
            ext = os.path.splitext(filename)[1]  # e.g. ".csv" from "predictions.csv"
            output_contracts[slot_name] = {"value_type": ext, "required": True}

    # --- Save outputs — classified by contract match -----------------------
    for name, data in outputs.items():
        if name not in output_contracts:
            raise ContractViolation(
                method=method_name,
                module=module_name,
                missing_outputs=[],
                missing_metrics=[],
                available_outputs=list(outputs.keys()),
                extra_outputs=[name],
            )
        # Contract-tracked: use save_module (validates ext)
        contract = output_contracts[name]
        ext = contract.get("value_type") or RunContext._infer_ext(data)
        ctx.save_module(name, data, ext=ext)

    # --- Log all metrics ---------------------------------------------------
    ctx.log_metrics(metrics)

    # --- ADR-005: Output column validation (soft warning) ------------------
    if output_slots:
        from wfc.contracts import validate_output_columns, validate_directory_contents
        for slot_name_val, slot_def in output_slots.items():
            column_spec = slot_def.get("columns")
            contents_spec = slot_def.get("contents")
            slot_type = slot_def.get("type", "csv")

            if slot_type in ("csv", "parquet") and column_spec:
                # Find the saved output file path
                output_path = ctx._module_outputs.get(slot_name_val)
                if output_path:
                    validate_output_columns(
                        ctx.run_dir / output_path, column_spec, ctx.params,
                        slot_name=slot_name_val, method_name=method_name,
                    )

            if slot_type == "directory" and contents_spec:
                output_path = ctx._module_outputs.get(slot_name_val)
                if output_path:
                    validate_directory_contents(
                        ctx.run_dir / output_path, contents_spec, ctx.params,
                        method_name=method_name,
                    )

    # --- Validate: all REQUIRED contracts satisfied? -----------------------
    missing_outputs = [
        name for name, c in output_contracts.items()
        if c.get("required", True) and name not in outputs
    ]
    missing_metrics = [
        name for name, c in metric_contracts.items()
        if c.get("required", True) and name not in metrics
    ]

    if missing_outputs or missing_metrics:
        raise ContractViolation(
            method=method_name,
            module=module_name,
            missing_outputs=missing_outputs,
            missing_metrics=missing_metrics,
            available_outputs=list(outputs.keys()),
            available_metrics=list(metrics.keys()),
        )

    # --- Write _run_results.json -------------------------------------------
    ctx.finalize()


# =============================================================================
# Return value parser
# =============================================================================

def _parse_return(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize the return value of a @wfc_method function.

    Accepted patterns:
      - ``DataFrame`` → ``{"output": df}, {}``
      - ``(DataFrame, dict)`` → ``{"output": df}, metrics``
      - ``(dict, dict)`` → ``outputs, metrics``
      - ``dict`` (single dict, no metrics) → ``outputs, {}``

    Args:
        result: Raw return value from the user function.

    Returns:
        ``(outputs_dict, metrics_dict)``
    """
    # Single DataFrame → primary output, no metrics
    if isinstance(result, pd.DataFrame):
        return {"output": result}, {}

    # Tuple: (something, metrics_dict)
    if isinstance(result, tuple):
        if len(result) != 2:
            raise TypeError(
                f"@wfc_method function returned a tuple of length {len(result)}. "
                f"Expected (outputs, metrics) — a 2-tuple."
            )
        outputs_raw, metrics = result

        if not isinstance(metrics, dict):
            raise TypeError(
                f"@wfc_method function returned metrics of type {type(metrics).__name__}. "
                f"Expected a dict."
            )

        # Normalize: bare DataFrame → {"output": df}
        if isinstance(outputs_raw, pd.DataFrame):
            return {"output": outputs_raw}, metrics

        if isinstance(outputs_raw, dict):
            return outputs_raw, metrics

        raise TypeError(
            f"@wfc_method function returned outputs of type {type(outputs_raw).__name__}. "
            f"Expected a DataFrame or dict of named outputs."
        )

    # Single dict (no metrics)
    if isinstance(result, dict):
        return result, {}

    # None → empty
    if result is None:
        return {}, {}

    raise TypeError(
        f"@wfc_method function returned {type(result).__name__}. "
        f"Expected: DataFrame, (DataFrame, dict), (dict, dict), or dict."
    )


# =============================================================================
# Contract lookup
# =============================================================================

def _get_module_contracts(
    method_name: str,
) -> tuple[dict[str, dict], dict[str, dict], str | None]:
    """Look up module contracts for a method from the database.

    Args:
        method_name: The method name to look up.

    Returns:
        ``(output_contracts, metric_contracts, module_name)``
        where each contract dict is ``{name: {value_type, required}}``.
        Returns empty dicts if the method or module has no contracts.
    """
    if not method_name:
        return {}, {}, None

    try:
        from wfc.database import get_session
        from wfc.models import Method, ModuleContract
        from sqlmodel import select
    except Exception:
        # If DB is not available (standalone execution), skip contracts
        return {}, {}, None

    output_contracts: dict[str, dict] = {}
    metric_contracts: dict[str, dict] = {}
    module_name: str | None = None

    try:
        from wfc.models import Module

        with get_session() as session:
            method = session.exec(
                select(Method).where(Method.name == method_name)
            ).first()
            if method is None:
                return {}, {}, None

            # Get module name
            module = session.exec(
                select(Module).where(Module.id == method.module_id)
            ).first()
            if module:
                module_name = module.name

            contracts = session.exec(
                select(ModuleContract).where(
                    ModuleContract.module_id == method.module_id
                )
            ).all()

            for c in contracts:
                entry = {
                    "value_type": c.value_type,
                    "required": c.required,
                }
                if c.contract_type == "output":
                    output_contracts[c.name] = entry
                elif c.contract_type == "metric":
                    metric_contracts[c.name] = entry
    except Exception:
        # Graceful degradation: if DB lookup fails, skip contract validation
        pass

    return output_contracts, metric_contracts, module_name


def _get_method_contract_slots(
    method_name: str,
) -> tuple[dict | None, dict | None]:
    """Look up MethodContract input/output slots for content-level validation.

    Args:
        method_name: The method name to look up.

    Returns:
        ``(input_slots, output_slots)`` dicts from MethodContract,
        or ``(None, None)`` if unavailable.
    """
    if not method_name:
        return None, None

    try:
        from wfc.database import get_session
        from wfc.models import Method, MethodContract
        from sqlmodel import select
    except Exception:
        return None, None

    try:
        with get_session() as session:
            method = session.exec(
                select(Method).where(Method.name == method_name)
            ).first()
            if method is None:
                return None, None

            mc = session.exec(
                select(MethodContract).where(
                    MethodContract.method_id == method.id
                )
            ).first()
            if mc is None:
                return None, None

            return mc.input_slots, mc.output_slots
    except Exception:
        return None, None
