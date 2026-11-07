"""The options family: declaration shape and resolution.

- ``options`` key on an output slot (``fixed``, ``from_params``, ``patterns``)
- Declaration shape: ``validate_options_block()`` (called by the parser)
- Resolution: ``resolve_options()``

An output's options are suggestions: they name the values a downstream
parameter declared ``options_from`` that output's input can pick from, and
feed the canvas's options picker. Nothing checks data or wiring against
them -- not at load, lock or run, and the system never opens a user's data
file to audit it against what was declared.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping

# =============================================================================
# Declaration shape
# =============================================================================

#: The form an ``options`` block takes, for the refusal messages.
OPTIONS_BLOCK_FORM = (
    "An `options` block is a mapping with any of `fixed` (a list of names), "
    "`from_params` (a list of entries, each with `params`, a list of "
    "parameter names, and `pattern`, a format string) and `patterns` "
    "(a list of glob patterns)"
)


def _is_str_list(value) -> bool:
    """True when *value* is a list whose every item is a string."""
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_options_block(slot_name: str, block, source) -> None:
    """Refuse a malformed ``options`` block on an output slot declaration.

    A well-formed block passes through untouched. A block that is not a
    mapping, a ``fixed`` that is not a list of strings, a ``from_params``
    entry that lacks its parameter list or its pattern, or a ``patterns``
    that is a bare string is refused naming the slot -- each of these
    parses clean and then resolves to nothing, or fails, at first use.

    Args:
        slot_name: The output slot carrying the block.
        block: The ``options`` value as read from ``method.yaml``.
        source: Path of the declaration file (for the message).

    Raises:
        ValueError: If the block is malformed.
    """
    def refuse(what: str, given) -> ValueError:
        return ValueError(
            f"{source}: slot '{slot_name}' {what}, got "
            f"{type(given).__name__}. {OPTIONS_BLOCK_FORM}."
        )

    if not isinstance(block, dict):
        raise refuse("options block must be a mapping", block)

    if "fixed" in block and not _is_str_list(block["fixed"]):
        raise refuse("options `fixed` must be a list of names", block["fixed"])

    if "from_params" in block:
        entries = block["from_params"]
        if not isinstance(entries, list):
            raise refuse("options `from_params` must be a list of entries",
                         entries)
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise refuse(f"options `from_params[{i}]` must be a mapping",
                             entry)
            if not _is_str_list(entry.get("params")):
                raise refuse(
                    f"options `from_params[{i}]` must declare `params` as a "
                    f"list of parameter names", entry.get("params"))
            if not isinstance(entry.get("pattern"), str):
                raise refuse(
                    f"options `from_params[{i}]` must declare `pattern` as a "
                    f"format string", entry.get("pattern"))

    if "patterns" in block and not _is_str_list(block["patterns"]):
        raise refuse("options `patterns` must be a list of glob patterns",
                     block["patterns"])


def _mapping(value) -> Mapping:
    """*value* when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def retired_form_refusals(contract: Mapping | None) -> list[str]:
    """List each retired options form, or bad ``options_from``, a contract declares.

    Registration refuses a version declaring any of these; the parser stays
    lenient so registered versions that hold them stay readable. Each line
    names the slot or parameter and what replaces the form: ``columns`` on
    an output (now ``options``), ``columns`` or ``options`` on an input
    (removed: the upstream's output declares ``options``), a parameter's
    ``column_of_input`` (now ``options_from``) or ``new_column`` (removed),
    ``strict`` inside an output's ``options`` (now ``fixed``), an
    ``options_from`` that is neither an input name nor a list of input
    names, and an ``options_from`` naming an input the method does not
    declare.

    Args:
        contract: A parsed ``method.yaml`` (``inputs``, ``outputs``,
            ``params``), or ``None``.

    Returns:
        One line per finding, in declaration order (outputs, inputs, then
        parameters); empty when the contract declares none.
    """
    contract = _mapping(contract)
    inputs = _mapping(contract.get("inputs"))
    outputs = _mapping(contract.get("outputs"))
    params = _mapping(contract.get("params"))
    refusals: list[str] = []

    for slot, spec in outputs.items():
        spec = _mapping(spec)
        if "columns" in spec:
            refusals.append(f"output slot '{slot}' declares `columns`, which "
                            f"is now `options:` beneath the output")
        if "strict" in _mapping(spec.get("options")):
            refusals.append(f"output slot '{slot}' declares `strict` in its "
                            f"`options`, which is now `fixed`")

    for slot, spec in inputs.items():
        for key in ("columns", "options"):
            if key in _mapping(spec):
                refusals.append(
                    f"input slot '{slot}' declares `{key}`, which is removed: "
                    f"declare `options:` on the upstream method's output, and "
                    f"`options_from: {slot}` on the parameter that picks from it")

    for name, spec in params.items():
        spec = _mapping(spec)
        if "column_of_input" in spec:
            refusals.append(f"parameter '{name}' declares `column_of_input`, "
                            f"which is now `options_from`")
        if "new_column" in spec:
            refusals.append(
                f"parameter '{name}' declares `new_column`, which is removed: "
                f"a name built from a parameter goes in the output's "
                f"`options: from_params`")
        if "options_from" not in spec:
            continue
        named = spec["options_from"]
        if isinstance(named, str):
            named = [named]
        if not _is_str_list(named):
            refusals.append(
                f"parameter '{name}' `options_from` must be an input slot name "
                f"or a list of input slot names, got "
                f"{type(spec['options_from']).__name__}")
            continue
        for slot in named:
            if slot not in inputs:
                refusals.append(
                    f"parameter '{name}' `options_from` names input '{slot}', "
                    f"which the method does not declare (inputs: "
                    f"{', '.join(inputs) or 'none'})")
    return refusals


# =============================================================================
# Resolution
# =============================================================================

def _expand_entry(entry: Mapping, params: Mapping) -> list[str]:
    """Expand one ``from_params`` entry over one param set.

    Each listed parameter's value is one choice, or one choice per item
    when it is a list; the entry's pattern is formatted with every
    combination, in the parameters' order. An entry with a parameter the
    set leaves unset (absent or ``None``) adds nothing.

    Args:
        entry: A ``from_params`` entry (``params``, ``pattern``).
        params: One param set.

    Returns:
        The names the entry builds from this set, in expansion order.
    """
    pattern = entry.get("pattern", "{}")
    choices: list[list[str]] = []
    for pname in entry.get("params", []):
        value = params.get(pname)
        if value is None:
            return []
        choices.append([str(v) for v in value] if isinstance(value, list)
                       else [str(value)])
    return [pattern.format(*combo) for combo in itertools.product(*choices)]


def resolve_options(options: Mapping | None,
                    param_sets: Iterable[Mapping] | Mapping) -> dict:
    """Resolve an output's ``options`` block over one or more param sets.

    The answer keeps declaration order. ``fixed`` lists the fixed names as
    declared. ``built`` holds one group per ``from_params`` entry that
    builds a name: the entry's ``params`` and the names its pattern
    expands to over every param set (the cartesian product of the listed
    parameters' values, a list value contributing each item), in
    first-seen order across the sets. ``patterns`` are the glob patterns
    as written, never expanded. A name appears once: the first place it
    occurs wins (fixed, then the entries in order), and patterns are
    de-duplicated as strings. ``all`` is the fixed names then the built
    ones, in that order.

    Args:
        options: The ``options`` dict from an output slot, or ``None``.
        param_sets: The upstream's param sets (its base values and each
            variant's or override's), each already holding its declared
            defaults. A single mapping is read as one set.

    Returns:
        ``{"fixed": [...], "built": [{"params": [...], "names": [...]}],
        "patterns": [...], "all": [...]}``.
    """
    if isinstance(param_sets, Mapping):
        param_sets = [param_sets]
    param_sets = list(param_sets)
    options = options or {}

    seen: set[str] = set()

    def first_seen(names: Iterable[str]) -> list[str]:
        kept = []
        for name in names:
            if name not in seen:
                seen.add(name)
                kept.append(name)
        return kept

    fixed = first_seen(options.get("fixed") or [])

    built = []
    for entry in options.get("from_params") or []:
        names = first_seen(name for params in param_sets
                           for name in _expand_entry(entry, params))
        if names:
            built.append({"params": list(entry.get("params", [])),
                          "names": names})

    patterns = list(dict.fromkeys(options.get("patterns") or []))

    return {
        "fixed": fixed,
        "built": built,
        "patterns": patterns,
        "all": fixed + [name for group in built for name in group["names"]],
    }
