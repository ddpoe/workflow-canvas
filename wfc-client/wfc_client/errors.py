"""Exceptions for wfc-client.

Pure stdlib; no wfc / pandas / sqlmodel imports.
"""

from __future__ import annotations


class ContractViolation(RuntimeError):
    """Raised when a method's outputs/metrics don't satisfy its contracts.

    Provides an actionable error message showing what was missing,
    what was produced, and where contracts are defined. The message
    shape is preserved from the original in-tree ``wfc.method`` decorator
    so error-message assertions remain stable across the extraction.
    """

    def __init__(
        self,
        method: str,
        module: "str | None" = None,
        missing_outputs: "list[str] | None" = None,
        missing_metrics: "list[str] | None" = None,
        available_outputs: "list[str] | None" = None,
        available_metrics: "list[str] | None" = None,
        extra_outputs: "list[str] | None" = None,
        # ADR-005: content-level column validation fields
        missing_columns: "list[str] | None" = None,
        available_columns: "list[str] | None" = None,
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
        lines.append("  Declare each output with ctx.save_artifact(name, path) in your @wfc.method function.")
        lines.append("  Log scalars with ctx.log_metric(name, value).")

        super().__init__("\n".join(lines))
