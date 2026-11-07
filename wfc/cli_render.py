"""Rendering for the CLI's reading verbs: plain tables and ``--json`` objects.

Only :mod:`wfc.cli` imports this module. A verb body parses its arguments,
calls one owner function, renders what it returns here, and exits.

``--json`` prints one JSON object on stdout and nothing else there: a list
is ``{"ok": true, "items": [...]}``, a single answer its own object with
``ok``. A failure prints its ``ERROR:`` line on stderr, as the plain form
does, and ``{"ok": false, "error": "<the same words>"}`` on stdout; the
exit code is the plain form's.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# =============================================================================
# JSON
# =============================================================================


def _plain(value: Any) -> Any:
    """A value as plain JSON: dataclasses as dicts, paths as strings, tuples as lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def print_json(obj: dict[str, Any]) -> None:
    """Print one JSON object on stdout.

    Args:
        obj: The object; dataclasses and paths in it are made plain.
    """
    print(json.dumps(_plain(obj), indent=2, default=str))


def print_items(items: Iterable[Any]) -> None:
    """Print a list as ``{"ok": true, "items": [...]}``.

    Args:
        items: The items (dicts or dataclasses).
    """
    print_json({"ok": True, "items": list(items)})


def fail(message: str, *, as_json: bool) -> int:
    """Report a failure: ``ERROR:`` on stderr, and under ``--json`` the object too.

    Args:
        message: What went wrong.
        as_json: Whether ``--json`` was given.

    Returns:
        1, the exit code.
    """
    print(f"ERROR: {message}", file=sys.stderr)
    if as_json:
        print_json({"ok": False, "error": message})
    return 1


# =============================================================================
# Tables
# =============================================================================


def table_lines(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    """A table padded to its widest cell; an empty cell prints as ``-``.

    Args:
        columns: The header.
        rows: The rows, one value per column.

    Returns:
        The lines, header first, trailing spaces stripped.
    """
    cells = [tuple(columns)] + [
        tuple("-" if value in (None, "") else str(value) for value in row)
        for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
            .rstrip() for row in cells]


def print_table(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Print :func:`table_lines`.

    Args:
        columns: The header.
        rows: The rows.
    """
    print("\n".join(table_lines(columns, rows)))


# =============================================================================
# The Registry's listings
# =============================================================================

METHOD_COLUMNS = ("METHOD", "NEWEST", "VERSIONS", "ENV", "SOURCE")
MODULE_COLUMNS = ("MODULE", "METHODS", "CONTRACTS", "COLOR", "DESCRIPTION")
SAMPLE_COLUMNS = ("SAMPLE", "TYPE", "SIZE", "REGISTERED", "SOURCE")


def method_row(method: Any) -> tuple:
    """One ``list-methods`` row of a ``ListedMethod``."""
    newest = f"v{method.versions[-1].label}" if method.versions else None
    return (f"{method.module}.{method.name}", newest, len(method.versions),
            method.env, method.source)


def module_row(module: Any) -> tuple:
    """One ``list-modules`` row of a ``ListedModule``."""
    return (module.name, module.methods, len(module.contracts), module.color,
            module.description)


def sample_row(sample: Any) -> tuple:
    """One ``list-samples`` row of a ``ListedSample``."""
    return (sample.name, sample.file_type, sample.size, sample.registered_at,
            sample.source)


# =============================================================================
# doctor
# =============================================================================


def check_item(result: Any) -> dict[str, Any]:
    """One readiness check as a JSON item.

    Args:
        result: A ``CheckResult``.

    Returns:
        Its name, status, message and fix hint.
    """
    return {"name": result.name, "status": result.status,
            "message": result.message, "fix_hint": result.fix_hint or None}


# =============================================================================
# share and try
# =============================================================================

#: What ``wfc share`` says sharing through git does not carry, and how runs
#: are handed over instead.
SHARE_NOTES = (
    "Sharing through git carries the project's code, registered methods and "
    "env records; the registry carries their images.",
    "Runs, samples and outputs stay in this project's local wfc.db: a "
    "collaborator registers the samples again from the data "
    "(`wfc register-sample`).",
    "To hand over runs with their outputs, use `wfc export-run` (add "
    "--with-image for an image that is not pushed).",
)


def try_exports(ref: str, outputs: Iterable[str]) -> list[dict[str, str]]:
    """The export command of each output of a try's draft step.

    Args:
        ref: The draft step's draft string.
        outputs: The method's declared output slots.

    Returns:
        One ``{"slot", "export"}`` per output; the destination is left for
        the user to name.
    """
    return [{"slot": slot,
             "export": f"wfc export --run {ref} --slot {slot} --dest <dir>"}
            for slot in outputs]


# =============================================================================
# show-run
# =============================================================================


def show_run_lines(detail: Any) -> list[str]:
    """What ``wfc show-run`` prints for one run's detail.

    Args:
        detail: A ``RunDetail``.

    Returns:
        The status line, the run's fields, metrics, outputs, error, the two
        log paths and the stderr tail.
    """
    kind = "Draft step" if detail.draft else "Run"
    lines = [f"{kind} {detail.run}: {detail.status}"]
    for label, value in (("method", detail.method), ("sample", detail.sample),
                         ("pipeline", detail.pipeline_id), ("node", detail.node_id)):
        lines.append(f"  {label:<9}{value if value not in (None, '') else '-'}")
    if detail.cache_source_run_id is not None:
        lines.append(f"  cache hit of run {detail.cache_source_run_id}")
    if detail.metrics:
        lines.append("Metrics:")
        lines.extend(f"  {key}: {value}" for key, value in detail.metrics.items())
    else:
        lines.append("Metrics: none")
    if detail.outputs_error:
        lines.append(f"Outputs: cannot be listed: {detail.outputs_error}")
    elif detail.outputs:
        lines.append("Outputs:")
        lines.extend(f"  {line}" for line in table_lines(
            ("SLOT", "STATE", "PATH"),
            ((o.slot, o.state, o.path) for o in detail.outputs)))
    else:
        lines.append("Outputs: none")
    if detail.error:
        lines.append(f"Error: {detail.error}")
    lines.append(f"stdout log: {detail.stdout_log}")
    lines.append(f"stderr log: {detail.stderr_log}")
    if detail.stderr_tail:
        lines.append(f"Last {len(detail.stderr_tail)} lines of stderr:")
        lines.extend(f"  {line}" for line in detail.stderr_tail)
    else:
        lines.append("stderr: empty or no log")
    return lines


# =============================================================================
# run-pipeline / check-pipeline
# =============================================================================


def document_refusal_lines(exc: Exception) -> list[str]:
    """The stderr lines of a refused pipeline document: one ``ERROR:`` line.

    The validator's refusal and the draft preflight's (each carries
    ``refusals``) is the line ``ERROR: the pipeline document is refused:``
    with each refusal indented under it (a draft refusal's line names its
    node and the check); any other refusal is ``ERROR:`` and its message, a
    continuation line indented.

    Args:
        exc: A ``PipelineDocumentError``.

    Returns:
        The lines, the first the only one starting ``ERROR:``.
    """
    refusals = getattr(exc, "refusals", None)
    if refusals:
        return ["ERROR: the pipeline document is refused:",
                *(f"  {getattr(refusal, 'message', refusal)}" for refusal in refusals)]
    first, *rest = str(exc).splitlines() or [""]
    return [f"ERROR: {first}", *(f"  {line}" for line in rest)]


def verdict_lines(verdict: dict[str, Any]) -> list[str]:
    """``check-pipeline``'s stderr lines: warnings, then errors.

    A ``WARNING:`` line per warning, an ``ERROR:`` line per error (draft
    refusals among them).

    Args:
        verdict: ``{valid, errors, warnings, draftRefusals}``.

    Returns:
        The warning lines, then the error lines.
    """
    return ([f"WARNING: {w}" for w in verdict.get("warnings", [])]
            + [f"ERROR: {e}" for e in verdict.get("errors", [])])
