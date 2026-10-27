"""The parameter check: a node's params against its method version's contract.

One value rule for a declared parameter, shared by the server's validation
entry point and (through the agreement table the canvas reads too) the
canvas editor:

- ``any`` is unchecked;
- ``str``, ``bool``, ``list`` and ``dict`` take exactly that JSON kind;
- ``int`` takes an integer or a whole-number float (``1.0``, ``-3.0``) of
  magnitude at most 2^53-1 (``MAX_SAFE_INTEGER``, the largest integer
  JavaScript holds exactly), never a boolean (``bool`` is not ``int``), a
  fractional or non-finite float, or a larger value;
- ``float`` takes any number, whole numbers included, never a boolean;
- a ``null`` value stands for "no value": accepted for an optional
  parameter, refused for a required one, as is an empty string;
- ``min`` / ``max`` bound a number, ``enum`` lists the values allowed; both
  apply to the normalized value.

One normalization gives an accepted value its declared type's form, so one
value has one JSON spelling wherever it is hashed or run: an ``int``
parameter's ``1.0`` becomes ``1`` and a ``float`` parameter's ``1`` becomes
``1.0``; every other value is kept as it is.

A parameter the document leaves out runs at its declared default. A
parameter declares a default when its spec carries a non-null ``default``
(``default: null`` declares none). ``fill_declared_defaults`` adds each
declared default for a key the params leave out and never touches a key
that is present, whatever its value (``{}``, ``[]``, ``""``, ``null``), so a
step that leaves a parameter out and one that writes its default are the
same work. An absent required parameter with a usable default (non-null, and
not ``""``, since a required value cannot be empty) needs no value. A
declared default that breaks its own type or constraints is refused when a
version is registered (``declared_default_refusals``). The module imports
nothing from ``wfc`` beyond the vocabulary, and the checks take the contract
as an argument.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from typing import Any

from axiom_annotations import task

from .vocabulary import PARAM_TYPES

#: The largest magnitude an ``int`` parameter takes: 2^53-1, JavaScript's
#: ``Number.MAX_SAFE_INTEGER``, so the canvas and the server read every
#: accepted value alike.
MAX_SAFE_INTEGER = 2**53 - 1

_KIND_NAMES = {
    bool: "bool", int: "int", float: "float", str: "str",
    list: "list", dict: "dict", type(None): "null",
}


def _kind(value: Any) -> str:
    """The JSON kind of a value, in the parameter vocabulary's spelling."""
    return _KIND_NAMES.get(type(value), type(value).__name__)


def _shown(value: Any) -> str:
    """A value as the refusal shows it: its JSON text, cut to 60 characters."""
    try:
        text = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _is_whole_number(value: Any) -> bool:
    """Whether a value is an integer or a finite whole-number float (no bool)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value) and value.is_integer()


def _is_number(value: Any) -> bool:
    """Whether a value is a JSON number (an int or float, never a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_equal(a: Any, b: Any) -> bool:
    """Whether two values are equal as JSON values (enum membership).

    Both must be the same JSON kind: a bool never equals a number. Numbers
    compare by value (``1`` equals ``1.0``), strings exactly, ``null`` only
    ``null``; lists element by element in order, dicts by equal key sets
    and equal values per key (key order ignored), with this same rule.

    Args:
        a: One value.
        b: The other.

    Returns:
        ``True`` when the two are the same JSON value.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) or _is_number(b):
        return _is_number(a) and _is_number(b) and a == b
    if isinstance(a, list) or isinstance(b, list):
        return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(_json_equal(x, y) for x, y in zip(a, b)))
    if isinstance(a, dict) or isinstance(b, dict):
        return (isinstance(a, dict) and isinstance(b, dict)
                and a.keys() == b.keys()
                and all(_json_equal(a[k], b[k]) for k in a))
    if a is None or b is None:
        return a is None and b is None
    return isinstance(a, str) and isinstance(b, str) and a == b


def _type_matches(ptype: str, value: Any) -> bool:
    """Whether a value is of a declared parameter type (``any`` always is)."""
    if ptype == "any":
        return True
    if ptype == "int":
        return _is_whole_number(value) and abs(value) <= MAX_SAFE_INTEGER
    if ptype == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ptype == "bool":
        return isinstance(value, bool)
    if ptype == "str":
        return isinstance(value, str)
    if ptype == "list":
        return isinstance(value, list)
    if ptype == "dict":
        return isinstance(value, dict)
    return True


def declared_type(ptype: Any) -> str:
    """Return the type a declared param is held to.

    Args:
        ptype: The ``type`` a params-schema entry carries; ``None`` or a
            spelling outside ``PARAM_TYPES`` is treated as ``any``.

    Returns:
        One of ``PARAM_TYPES``.
    """
    return ptype if ptype in PARAM_TYPES else "any"


def declared_param_types(params_schema: Any) -> dict[str, str]:
    """Return each declared param's type, by name, from a version's params schema.

    An entry written as a bare type (``name: int``) reads as that type, as
    ``param_refusals`` reads it.

    Args:
        params_schema: A version's ``{name: {type, required, default, ...}}``,
            or ``None``; anything that is not a mapping declares nothing.

    Returns:
        ``{name: type}`` with each type one of ``PARAM_TYPES``; a param the
        schema does not declare is absent.
    """
    if not isinstance(params_schema, Mapping):
        return {}
    types: dict[str, str] = {}
    for name, spec in params_schema.items():
        spec = spec if isinstance(spec, Mapping) else {"type": spec}
        types[str(name)] = declared_type(spec.get("type"))
    return types


def check_param_value(ptype: str | None, constraints: Mapping[str, Any] | None,
                      value: Any, *, required: bool = False) -> str | None:
    """Check one parameter value against its declared type and constraints.

    Args:
        ptype: The declared type (one of ``PARAM_TYPES``); ``None`` or an
            unknown spelling is treated as ``any``.
        constraints: The declared ``constraints`` (``min``, ``max``,
            ``enum``), or ``None``.
        value: The value as the document carries it, after substitution.
        required: Whether the parameter is declared required.

    Returns:
        ``None`` when the value is accepted, else the reason it is refused
        (the expected type or constraint and the value), for the caller to
        prefix with the node and parameter.
    """
    ptype = declared_type(ptype)
    if value is None or value == "":
        if required:
            return f"is required and cannot be empty (got {_shown(value)})"
        if value is None:
            return None
    if not _type_matches(ptype, value):
        if ptype == "int" and _is_whole_number(value):
            return (f"expects int of magnitude at most {MAX_SAFE_INTEGER}, "
                    f"got {_shown(value)}")
        return f"expects {ptype}, got {_shown(value)} ({_kind(value)})"
    value = _normalized(ptype, value)
    limits = constraints or {}
    enum = limits.get("enum")
    if enum and not any(_json_equal(value, option) for option in enum):
        options = ", ".join(_shown(o) for o in enum)
        return f"must be one of {options}, got {_shown(value)}"
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if number and limits.get("min") is not None and value < limits["min"]:
        return f"must be >= {limits['min']}, got {_shown(value)}"
    if number and limits.get("max") is not None and value > limits["max"]:
        return f"must be <= {limits['max']}, got {_shown(value)}"
    return None


def _normalized(ptype: str, value: Any) -> Any:
    """A value of a declared type in that type's form (``int`` / ``float``)."""
    if ptype == "int" and isinstance(value, float):
        return int(value)
    if ptype == "float" and isinstance(value, int) and not isinstance(value, bool):
        try:
            return float(value)
        except OverflowError:
            return value
    return value


def normalize_param_value(ptype: str | None, value: Any,
                          constraints: Mapping[str, Any] | None = None) -> Any:
    """Give an accepted parameter value its declared type's canonical form.

    An ``int`` parameter's whole-number float becomes an ``int`` (``1.0`` ->
    ``1``); a ``float`` parameter's integer becomes a ``float`` (``1`` ->
    ``1.0``). Every other type, ``any``, ``null``, and any value the value
    rule refuses come back unchanged; values nested in a ``list`` or
    ``dict`` are not touched. Normalizing a normalized value changes
    nothing.

    Args:
        ptype: The declared type (one of ``PARAM_TYPES``); ``None`` or an
            unknown spelling is treated as ``any``.
        value: The value as written.
        constraints: The declared ``constraints``; a value outside them is
            refused by the rule and so kept as written.

    Returns:
        The canonical value, or ``value`` itself when there is nothing to
        change or the rule refuses it.
    """
    ptype = declared_type(ptype)
    if ptype not in ("int", "float") or value is None:
        return value
    limits = constraints if isinstance(constraints, Mapping) else None
    try:
        if check_param_value(ptype, limits, value) is not None:
            return value
    except TypeError:
        return value  # constraints that cannot be compared: keep as written
    return _normalized(ptype, value)


def _spec_mapping(spec: Any) -> Mapping[str, Any]:
    """A params-schema entry as a mapping (a bare type reads as ``{type}``)."""
    return spec if isinstance(spec, Mapping) else {"type": spec}


def _declared_default(spec: Mapping[str, Any]) -> tuple[bool, Any]:
    """Whether a param spec declares a default, and the default.

    A ``default`` key whose value is ``None`` declares no default.

    Args:
        spec: One params-schema entry, as a mapping.

    Returns:
        ``(True, default)`` when the spec declares a non-null default, else
        ``(False, None)``.
    """
    default = spec.get("default")
    return (default is not None, default)


def _default_reason(spec: Mapping[str, Any]) -> str | None:
    """Why a param spec's declared default fails its own type or constraints.

    The required-emptiness rule is not applied: a required parameter's
    ``""`` default is no usable default, which is a different verdict.

    Args:
        spec: One params-schema entry, as a mapping, that declares a
            non-null default.

    Returns:
        ``None`` when the default is accepted, else the reason it is refused.
    """
    _, default = _declared_default(spec)
    try:
        return check_param_value(spec.get("type"), spec.get("constraints"), default)
    except TypeError:
        return (f"cannot be checked against its constraints "
                f"(got {_shown(default)})")


def _usable_default(spec: Mapping[str, Any]) -> bool:
    """Whether a param spec's default can run for a node that leaves it out.

    Usable means declared (non-null) and, for a required parameter, not
    ``""``.

    Args:
        spec: One params-schema entry, as a mapping.

    Returns:
        ``True`` when the default may be filled.
    """
    declared, default = _declared_default(spec)
    if not declared:
        return False
    return not (spec.get("required") is True and default == "")


def fill_declared_defaults(params: Mapping[str, Any] | None,
                           params_schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Add each declared default for a parameter the params leave out.

    A parameter declares a default when its spec carries a non-null
    ``default``. A key that is present is kept as written, whatever its
    value (``{}``, ``[]``, ``""``, ``null``). A required parameter's ``""``
    default is not filled; ``param_refusals`` refuses the node instead.
    A filled default is a copy in its declared type's form, so the result
    never shares a list or dict with the contract. Filling a filled dict
    changes nothing. Only top-level keys are filled.

    Args:
        params: A node's params or a ``param_sets`` variant's, or ``None``
            for none.
        params_schema: The version's ``{name: {type, required, default,
            constraints, ...}}``, or ``None``.

    Returns:
        A new dict: the params as written plus each filled default.
    """
    filled = dict(params or {})
    if not isinstance(params_schema, Mapping):
        return filled
    for name, spec in params_schema.items():
        spec = _spec_mapping(spec)
        if name in filled or not _usable_default(spec):
            continue
        filled[name] = normalize_param_value(
            spec.get("type"), copy.deepcopy(spec["default"]),
            spec.get("constraints"))
    return filled


def normalized_params(params: Any, schema: Mapping[str, Any]) -> Any:
    """One params dict with each declared value in its declared type's form.

    Args:
        params: A params dict; anything that is not a mapping is returned as
            it is (the validator refuses it).
        schema: The version's declared params.

    Returns:
        A new dict with each declared value normalized; a key the schema does
        not declare is kept as written (the validator refuses it).
    """
    if not isinstance(params, Mapping):
        return params
    out = {}
    for name, value in params.items():
        spec = schema.get(name)
        if spec is None:
            out[name] = value
            continue
        spec = _spec_mapping(spec)
        out[name] = normalize_param_value(spec.get("type"), value,
                                          spec.get("constraints"))
    return out


def filled_params(params: Any, schema: Mapping[str, Any]) -> Any:
    """One params dict plus each declared default it leaves out.

    Args:
        params: A params dict or ``None``; anything else is returned as it is
            (the validator refuses it).
        schema: The version's declared params.

    Returns:
        ``fill_declared_defaults``' result, or ``params`` when malformed.
    """
    if params is not None and not isinstance(params, Mapping):
        return params
    return fill_declared_defaults(params, schema)


def normalize_and_fill_params(params: Mapping[str, Any] | None,
                              params_schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Give params their declared types' form, then fill each left-out default.

    The rule the document pass applies node by node
    (``normalize_document_params`` then ``fill_document_defaults``), for one
    params dict: run it after ``param_refusals`` accepts the params.

    Args:
        params: The params as given, or ``None`` for none.
        params_schema: The version's ``{name: {type, required, default,
            constraints, ...}}``, or ``None``.

    Returns:
        A new dict: each declared value normalized, plus each usable declared
        default the params leave out. Running it on its own result changes
        nothing.
    """
    schema = params_schema if isinstance(params_schema, Mapping) else {}
    normalized = normalized_params(dict(params or {}), schema)
    return filled_params(normalized, schema)


def declared_default_refusals(params_schema: Mapping[str, Any] | None) -> list[str]:
    """List each declared default that fails its own type or constraints.

    A default runs whenever a node leaves its parameter out, so one that
    breaks its declared ``type``, ``enum``, ``min`` or ``max`` is refused
    before a version holding it is registered. ``default: null`` declares no
    default and is not checked; nor is a required parameter's ``""``
    default, which is accepted here and simply cannot stand in for a value.

    Args:
        params_schema: A ``method.yaml``'s parsed ``params`` block, or
            ``None``; anything that is not a mapping declares nothing.

    Returns:
        One line per bad default, naming the parameter and the reason, in
        declaration order; empty when every declared default is accepted.
    """
    if not isinstance(params_schema, Mapping):
        return []
    refusals: list[str] = []
    for name, spec in params_schema.items():
        spec = _spec_mapping(spec)
        if not _declared_default(spec)[0]:
            continue
        reason = _default_reason(spec)
        if reason is not None:
            refusals.append(f"parameter '{name}' declares a default that {reason}")
    return refusals


@task(purpose="Refuse a method node's params, or one of its param_sets "
              "variants, that its version's contract does not accept: "
              "undeclared, wrong type, outside min/max/enum, or a required "
              "parameter left out with no usable default; each refusal "
              "names node, variant (if any), parameter and version",
      inputs="The node id, its module.method, the version label, its params "
             "or a variant's params (after substitution), that version's "
             "params schema and the variant's name",
      outputs="One refusal message per offending parameter (empty when valid)")
def param_refusals(node_id: str, method_ref: str, version_label: str,
                   params: Mapping[str, Any] | None,
                   params_schema: Mapping[str, Any] | None,
                   variant: str | None = None) -> list[str]:
    """Check a method node's params against its version's declared params.

    A ``param_sets`` variant is a whole params dict that runs in place of
    the node's params, so it gets the same check; its refusals name the
    variant too.

    The params are checked as written. A parameter they leave out runs at
    its declared default, so it is refused only when it is required and has
    no usable default (none, ``null``, or ``""``); otherwise it is accepted.

    Args:
        node_id: The node's document id.
        method_ref: ``module.method``.
        version_label: The resolved version's label, e.g. ``"v2"``.
        params: The node's params (or the variant's), after variable
            substitution.
        params_schema: The version's ``{name: {type, required, default,
            constraints, ...}}``.
        variant: The variant's name when ``params`` is a ``param_sets``
            variant; ``None`` for the node's own params.

    Returns:
        The refusals, each naming the node, the variant (when given), the
        parameter and the version; empty when every parameter is accepted.
        The params are not changed (``fill_declared_defaults`` fills them).
    """
    params = dict(params or {})
    schema = dict(params_schema or {})
    named = f" variant '{variant}'" if variant is not None else ""
    where = f"Node '{node_id}'{named} ({method_ref} {version_label})"
    refusals: list[str] = []
    for name in params:
        if name not in schema:
            refusals.append(f"{where}: parameter '{name}' is not declared by "
                            f"{version_label}; remove it")
    for name, spec in schema.items():
        spec = _spec_mapping(spec)
        required = spec.get("required") is True
        if name not in params:
            if required and not _usable_default(spec):
                refusals.append(f"{where}: required parameter '{name}' "
                                f"({spec.get('type') or 'any'}) is missing")
            continue
        reason = check_param_value(spec.get("type"), spec.get("constraints"),
                                   params[name], required=required)
        if reason is not None:
            refusals.append(f"{where}: parameter '{name}' {reason}")
    return refusals
