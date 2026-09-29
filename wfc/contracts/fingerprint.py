"""The computation-bearing projection of a parsed ``method.yaml``.

A method's declared contract decides what a run produces and where its
outputs are found, so it belongs to the method's code identity: renaming an
output slot, or changing its ``type``, renames the file the run writes
(``slots.output_slot_filename``), and a cached result computed under the
previous contract is an artifact named for a promise the method no longer
makes.

Not every field in ``method.yaml`` has that property. The rule (user
decision, 2026-09-16) is: **in if it changes a run's outputs, out if it only
describes.** Descriptions, the canvas-facing ``columns`` blocks, the params
schema and the advisory input ``type`` are all out; a description edit or a
YAML reorder must leave every cache key byte-identical, which is the whole
reason this is a projection rather than a hash of the file.

Field by field:

===============================  =====  =======================================
Field                            In?    Why
===============================  =====  =======================================
``outputs`` slot name + ``type``  IN    Names the produced file.
``outputs`` ``columns``,          out   Canvas dropdown / cross-check warnings
``required``, ``description``,          and registration-time save validation;
``contents``                            none of it changes what a run produces.
``inputs`` slot names             IN    The ``slot_paths`` keys the script reads.
``inputs`` ``type``               out   Advisory by construction -- a bad value
                                        warns, it does not refuse.
``params`` schema                 out   Canvas-only; resolved param *values*
                                        are already a cache-key component.
``env``                           out   Already in the key as env_fingerprint.
``executor``                      IN    Decides how the script is invoked.
``script``                        IN    Selects the main script among several
                                        the snapshot's digest covers equally.
``helpers``                       out   Self-covering -- a helpers change moves
                                        the digest through the snapshot set.
``gpus``                          IN    Forwarded as ``--gpus all``; GPU vs CPU
                                        can change numeric output.
``description``                   out   Explicit user decision.
===============================  =====  =======================================

The package invariant holds: this is a pure function over the dict
``parse_method_yaml`` returns. It renders a string, and ``wfc.identity``
takes that string as a value -- Identity imports no ``wfc`` module, so the
parse cannot happen there.
"""

from __future__ import annotations

import json

__all__ = ["render_contract_projection"]


def _slot_type(slot_spec) -> str | None:
    """Read a slot's canonical type from either declaration spelling.

    Args:
        slot_spec: A slot's value in the parsed contract -- either a mapping
            carrying a ``type`` key, or the bare type string the shorthand
            spelling leaves behind.

    Returns:
        The canonical type string, or ``None`` when the slot declares none.
    """
    if isinstance(slot_spec, dict):
        value = slot_spec.get("type")
    else:
        value = slot_spec
    return value if isinstance(value, str) else None


def render_contract_projection(contract: dict | None) -> str | None:
    """Render the computation-bearing fields of a parsed contract as a string.

    The rendering is order-insensitive across every mapping: slot names are
    sorted and the JSON is dumped with ``sort_keys=True``, so reordering
    ``method.yaml``, reflowing it, or adding a comment leaves the rendering --
    and therefore every cache key -- byte-identical. Editing an output slot's
    name or ``type`` moves it.

    Args:
        contract: The dict ``wfc.contracts.parse_method_yaml`` returns, or
            ``None`` when the method directory holds no ``method.yaml``.

    Returns:
        A deterministic string identifying the contract's computation-bearing
        fields, or ``None`` when ``contract`` is ``None``. Callers hand the
        value to ``wfc.identity.build_code_fingerprint``, which refuses the
        ``None`` case rather than fingerprinting a method with no contract.
    """
    if contract is None:
        return None

    inputs = contract.get("inputs") or {}
    outputs = contract.get("outputs") or {}

    projection = {
        # Input slot NAMES only: the keys the script reads its files under.
        # An input's `type` is advisory (a bad value warns), and an advisory
        # annotation must not move a key.
        "inputs": sorted(inputs) if isinstance(inputs, dict) else [],
        # Output slot name -> canonical type: together these name the file
        # the run writes, via `slots.output_slot_filename`.
        "outputs": (
            {name: _slot_type(spec) for name, spec in sorted(outputs.items())}
            if isinstance(outputs, dict) else {}
        ),
        "executor": contract.get("executor"),
        # `script:` selects which of the snapshot's recognised scripts is the
        # main one. The snapshot digest covers them all with equal weight, so
        # without this a `script: a.py` -> `script: b.py` flip runs different
        # code under an unchanged fingerprint.
        "script": contract.get("script"),
        "gpus": bool(contract.get("gpus", False)),
    }
    return json.dumps(projection, sort_keys=True, separators=(",", ":"))
