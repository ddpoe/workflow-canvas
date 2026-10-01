"""Substitution: ``{"$var": name}`` refs in a pipeline document become literals.

One implementation. The canvas authors refs and reads them back; every
submission path calls ``resolve_variables`` before the load, so a ref never
reaches a step's params as a literal dict. Substitution is a caller
obligation, not a load step: the frozen ``pipeline.json`` carries the
substituted form and ``pipeline.editable.json`` the pre-substitution one.
"""

from __future__ import annotations

from typing import Any


class UnknownVariableError(KeyError):
    """Raised when a pipeline references a variable that is not defined.

    The pipeline contains a {$var: name} ref but ``variables`` has no entry
    for ``name``. Caller (canvas server) translates to HTTP 400.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _is_var_ref(v: Any) -> bool:
    """Return True iff ``v`` is a ``{"$var": <str>}`` whole-value ref."""
    return (
        isinstance(v, dict)
        and len(v) == 1
        and "$var" in v
        and isinstance(v["$var"], str)
    )


def _substitute(v: Any, variables: dict[str, Any]) -> Any:
    """Replace a single value if it is a var ref; otherwise return as-is.

    One-pass: if the substituted value itself is a var ref, raise, so a
    chain or cycle of refs can never loop.
    """
    if not _is_var_ref(v):
        return v
    name = v["$var"]
    if name not in variables:
        raise UnknownVariableError(name)
    resolved = variables[name]
    if _is_var_ref(resolved):
        raise ValueError(
            f"Variable '{name}' resolves to another $var ref; nested refs are "
            "not allowed (one-pass substitution)."
        )
    return resolved


def resolve_variables(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Substitute ``{"$var": name}`` refs in a pipeline dict with literals.

    Pure walker over ``nodes[].params`` and ``param_sets[node_id][variant]``.
    Whole-value dict splice — does NOT recurse into nested keys of a
    user-supplied dict literal that happens to contain ``$var`` somewhere
    deeper. Raises :class:`UnknownVariableError` when a referenced name
    is missing from ``pipeline['variables']``.

    Args:
        pipeline: Pipeline JSON dict. May contain a top-level ``variables``
            mapping name -> {type, value} or name -> raw value. Only the
            ``value`` is substituted; ``type`` is metadata.

    Returns:
        A NEW pipeline dict with ``variables`` removed and all refs
        replaced by their resolved literals. Input is not mutated.

    Raises:
        UnknownVariableError: a ref names a variable not in ``variables``.
        ValueError: a variable's value is itself a ``{$var}`` ref.
    """
    out = dict(pipeline)  # shallow copy
    raw_vars = out.pop("variables", None) or {}

    # Normalize variables to {name: value}: accept either {value, type}
    # or a bare value.
    variables: dict[str, Any] = {}
    for name, entry in raw_vars.items():
        if isinstance(entry, dict) and "value" in entry:
            variables[name] = entry["value"]
        else:
            variables[name] = entry

    # Walk node params (top-level params dict per node).
    new_nodes = []
    for node in out.get("nodes", []) or []:
        nd = dict(node)
        params = nd.get("params") or {}
        if isinstance(params, dict):
            new_params = {k: _substitute(v, variables) for k, v in params.items()}
            nd["params"] = new_params
        new_nodes.append(nd)
    out["nodes"] = new_nodes

    # Walk param_sets[node_id][variant_name][param_name].
    ps = out.get("param_sets")
    if isinstance(ps, dict):
        new_ps: dict[str, Any] = {}
        for node_id, variants in ps.items():
            if not isinstance(variants, dict):
                new_ps[node_id] = variants
                continue
            new_variants: dict[str, Any] = {}
            for vname, vparams in variants.items():
                if not isinstance(vparams, dict):
                    new_variants[vname] = vparams
                    continue
                new_variants[vname] = {
                    k: _substitute(v, variables) for k, v in vparams.items()
                }
            new_ps[node_id] = new_variants
        out["param_sets"] = new_ps

    return out
