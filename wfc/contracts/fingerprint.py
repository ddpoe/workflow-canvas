"""The contract projection: what of a parsed ``method.yaml`` a version is.

A method version is its fingerprinted content, and its contract is everything
the canvas, the validator and the runner read from it: the slots a run reads
and writes (with their types, required-ness and declared columns), the
parameters a node may set (with their types, defaults and constraints), and
how the script is invoked. So the projection is the whole parsed contract,
minus what only describes it or is keyed elsewhere. Stating the exclusions
rather than the inclusions means a contract field added later is in by
default.

Left out:

===============================  ==============================================
Field                            Why it is out
===============================  ==============================================
a slot's ``description``         Prose; changes nothing the canvas, the
a parameter's ``description``    validator or a run reads. (A top-level
                                 ``description`` is not in the parsed contract.)
mapping key order, YAML layout,  Formatting. Mapping keys are sorted; a slot's
comments                         shorthand ``name: .csv`` reads as
                                 ``name: {type: .csv}`` and an empty slot or
                                 section as ``{}``. List order is kept: a
                                 ``columns.strict``
                                 list or an ``enum`` is content.
``env``                          The version's env axis: already in the content
                                 identity through the env name and fingerprint.
``helpers``                      Self-covering: the declared files are in the
                                 snapshot the fingerprint hashes.
===============================  ==============================================

Everything else is in: each input slot's and output slot's declaration
(name, ``type``, ``required``, ``columns``, ``multiple``, ``contents`` and any
other key), each parameter's declaration (name, ``type``, ``required``,
``default``, ``constraints``, ``column_of_input``, ``new_column`` and any
other key), ``executor``, ``script`` and ``gpus``. No default is filled in:
an absent ``required`` and ``required: true`` stay distinct, because the
claim and the validator read them differently.

A ``method.yaml`` edit to anything but a description, the layout, ``env`` or
``helpers`` therefore moves the code fingerprint, and registration mints the
next version, whose contract carries the edit.

The package invariant holds: this is a pure function over the dict
``parse_method_yaml`` returns. It renders a string, and ``wfc.identity``
takes that string as a value -- Identity imports no ``wfc`` module, so the
parse cannot happen there.
"""

from __future__ import annotations

import json

__all__ = ["render_contract_projection"]

#: Top-level contract keys keyed or covered elsewhere (see the module table).
_OUT_OF_PROJECTION = frozenset({"env", "helpers"})

#: The declaration sections whose entries are name -> declaration mappings.
_DECLARATION_SECTIONS = ("inputs", "outputs", "params")


def _declaration(spec):
    """Project one slot's or parameter's declaration.

    Args:
        spec: The declaration's value in the parsed contract: a mapping, the
            bare type string a slot's shorthand spelling leaves behind, or
            ``None`` for an empty declaration.

    Returns:
        The declaration as a mapping without its ``description``; a bare
        string becomes ``{"type": spec}`` and ``None`` becomes ``{}``, so
        the two spellings of one declaration project alike. Any other value
        is returned as it is.
    """
    if spec is None:
        return {}
    if isinstance(spec, str):
        return {"type": spec}
    if isinstance(spec, dict):
        return {key: value for key, value in spec.items() if key != "description"}
    return spec


def render_contract_projection(contract: dict | None) -> str | None:
    """Render a parsed contract, minus what only describes it, as a string.

    The rendering is deterministic and order-insensitive across every
    mapping (``sort_keys=True``), so reordering ``method.yaml``, reflowing
    it, adding a comment or rewording a description leaves it -- and the
    code fingerprint -- byte-identical. Any other edit moves it.

    Args:
        contract: The dict ``wfc.contracts.parse_method_yaml`` returns, or
            ``None`` when the method directory holds no ``method.yaml``.

    Returns:
        A deterministic string identifying the contract, or ``None`` when
        ``contract`` is ``None``. Callers hand the value to
        ``wfc.identity.build_code_fingerprint``, which refuses the ``None``
        case rather than fingerprinting a method with no contract.
    """
    if contract is None:
        return None

    projection = {}
    for key, value in contract.items():
        if key in _OUT_OF_PROJECTION:
            continue
        if key in _DECLARATION_SECTIONS:
            # An empty `inputs:` reads as None; it declares what `{}` does.
            if value is None:
                value = {}
            elif isinstance(value, dict):
                value = {name: _declaration(spec) for name, spec in value.items()}
        projection[key] = value
    # `default=str` keeps a YAML value JSON has no form for (a date default)
    # from refusing registration; it renders as its text.
    return json.dumps(projection, sort_keys=True, separators=(",", ":"),
                      default=str)
