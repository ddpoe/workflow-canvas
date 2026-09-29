"""The run-readiness health table.

:func:`render_health_table` renders a list of readiness results as a compact
text table.  Both ``wfc init`` (closing summary) and ``wfc doctor`` call it,
so the presentation of readiness lives in exactly one place.

The probes themselves — :class:`~wfc.execution.CheckResult`,
:func:`~wfc.execution.check_git`, :func:`~wfc.execution.check_dvc`,
:func:`~wfc.execution.check_docker` and
:func:`~wfc.execution.run_all_checks` — belong to Execution and live in
:mod:`wfc.execution.readiness`.
"""

from __future__ import annotations

from .execution import CheckResult

_STATUS_LABEL = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}


def render_health_table(results: list[CheckResult]) -> str:
    """Render a list of check results as a compact text health table.

    Used by both ``wfc init`` (closing summary) and ``wfc doctor`` so the
    rendering lives in one place.  Each row shows ``[STATUS] name — message``
    and, for non-ok rows, a fix-hint line.

    Args:
        results: The check results to render, in display order.

    Returns:
        A multi-line string (no trailing newline) ready to print.
    """
    lines = ["Run-readiness:"]
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        label = _STATUS_LABEL.get(r.status, r.status.upper())
        lines.append(f"  [{label:>4}] {r.name.ljust(width)}  {r.message}")
        if r.status != "ok" and r.fix_hint:
            lines.append(f"         {' ' * width}  -> {r.fix_hint}")
    return "\n".join(lines)
