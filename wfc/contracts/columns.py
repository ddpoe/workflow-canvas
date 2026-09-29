"""The column family: declaration shape, resolution and cross-check.

- ``columns`` key on input/output CSV/Parquet slots (strict, from_params, patterns)
- Declaration shape: ``validate_columns_block()`` (called by the parser)
- Column resolution: ``resolve_columns()``
- Static cross-step check: ``cross_check_columns()``

A column declaration documents a slot, feeds the canvas column dropdown and
the load-time declaration cross-check. The system does not open a user's
data file to audit it against what was declared.
"""

from __future__ import annotations

import itertools
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Declaration shape
# =============================================================================

#: The form a ``columns`` block takes, for the refusal messages.
COLUMNS_BLOCK_FORM = (
    "A `columns` block is a mapping with any of `strict` (a list of column "
    "names), `from_params` (a list of entries, each with `params`, a list "
    "of parameter names, and `pattern`, a format string) and `patterns` "
    "(a list of glob patterns)"
)


def _is_str_list(value) -> bool:
    """True when *value* is a list whose every item is a string."""
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_columns_block(slot_name: str, block, source) -> None:
    """Refuse a malformed ``columns`` block on a slot declaration.

    A well-formed block passes through untouched. A block that is not a
    mapping, a ``strict`` that is not a list of strings, a ``from_params``
    entry that lacks its parameter list or its pattern, or a ``patterns``
    that is a bare string is refused naming the slot -- each of these
    parses clean and then resolves to nothing, or fails, at first use.

    Args:
        slot_name: The input or output slot carrying the block.
        block: The ``columns`` value as read from ``method.yaml``.
        source: Path of the declaration file (for the message).

    Raises:
        ValueError: If the block is malformed.
    """
    def refuse(what: str, given) -> ValueError:
        return ValueError(
            f"{source}: slot '{slot_name}' {what}, got "
            f"{type(given).__name__}. {COLUMNS_BLOCK_FORM}."
        )

    if not isinstance(block, dict):
        raise refuse("columns block must be a mapping", block)

    if "strict" in block and not _is_str_list(block["strict"]):
        raise refuse("columns `strict` must be a list of column names",
                     block["strict"])

    if "from_params" in block:
        entries = block["from_params"]
        if not isinstance(entries, list):
            raise refuse("columns `from_params` must be a list of entries",
                         entries)
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise refuse(f"columns `from_params[{i}]` must be a mapping",
                             entry)
            if not _is_str_list(entry.get("params")):
                raise refuse(
                    f"columns `from_params[{i}]` must declare `params` as a "
                    f"list of parameter names", entry.get("params"))
            if not isinstance(entry.get("pattern"), str):
                raise refuse(
                    f"columns `from_params[{i}]` must declare `pattern` as a "
                    f"format string", entry.get("pattern"))

    if "patterns" in block and not _is_str_list(block["patterns"]):
        raise refuse("columns `patterns` must be a list of glob patterns",
                     block["patterns"])


# =============================================================================
# Column resolution engine
# =============================================================================

def resolve_columns(column_spec: dict | None, params: dict) -> set[str]:
    """Expand a column spec into a set of required column names.

    Supports three resolution strategies that can be combined:
      - ``strict``: literal column names
      - ``from_params``: column names derived from runtime parameter values
        via cartesian product expansion
      - ``patterns``: a declaration only; glob patterns are never expanded
        into names and are never matched against a data file

    Args:
        column_spec: The ``columns`` dict from a slot definition.  May contain
            ``strict``, ``from_params``, and/or ``patterns`` keys.
        params: The run's parameter dict for resolving ``from_params``.

    Returns:
        Set of required column names (from strict + expanded from_params).
        Patterns are NOT included; the system does not open a user's data
        file to audit its columns against the declaration.
    """
    if not column_spec:
        return set()

    columns: set[str] = set()

    # --- strict: literal column names ---
    strict = column_spec.get("strict", [])
    columns.update(strict)

    # --- from_params: param-driven expansion ---
    for entry in column_spec.get("from_params", []):
        param_names = entry.get("params", [])
        pattern = entry.get("pattern", "{}")

        # Collect param values; skip if any param is missing
        param_values: list[list[str]] = []
        skip = False
        for pname in param_names:
            if pname not in params:
                skip = True
                break
            val = params[pname]
            if isinstance(val, list):
                param_values.append([str(v) for v in val])
            else:
                param_values.append([str(val)])

        if skip:
            continue

        # Cartesian product expansion
        for combo in itertools.product(*param_values):
            columns.add(pattern.format(*combo))

    return columns


def cross_check_columns(
    upstream_output_spec: dict | None,
    downstream_input_spec: dict | None,
) -> list[str]:
    """Statically check column compatibility between connected steps.

    Only checks ``strict`` columns -- ``from_params`` columns are deferred
    to runtime because the param values may not be known at pipeline-load
    time.  ``patterns`` are also skipped (they require actual data).

    Args:
        upstream_output_spec: The ``columns`` dict from the upstream output slot.
        downstream_input_spec: The ``columns`` dict from the downstream input slot.

    Returns:
        List of warning messages.  Empty list if compatible.
    """
    if not upstream_output_spec or not downstream_input_spec:
        return []

    upstream_strict = set(upstream_output_spec.get("strict", []))
    downstream_strict = set(downstream_input_spec.get("strict", []))

    if not upstream_strict or not downstream_strict:
        return []

    missing = sorted(downstream_strict - upstream_strict)
    if missing:
        return [
            f"Downstream requires strict columns {missing} "
            f"not declared in upstream output (has {sorted(upstream_strict)})"
        ]

    return []
