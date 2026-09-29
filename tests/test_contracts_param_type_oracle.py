"""Contracts unit: the param-type vocabulary agreement oracle.

A method parameter's declared ``type`` is read by two sides. The parser
(``wfc.contracts.parse_method_yaml``) decides what a declaration may say;
the canvas renders a closed set -- ``str``, ``int``, ``float``, ``bool``,
``list``, ``dict`` -- and degrades anything else to an editor for an unknown
type (``wfc/canvas/static/src/lib/shared/types.ts::ContractType``,
``paramTypes.ts::classifyParamType``). The document closes the vocabulary
to that set plus ``any``, the explicit opt-out meaning the value is not
checked against a type.

The matrix has three halves:

- **verbatim** -- every canonical name parses and survives unchanged;
- **normalised** -- every accepted spelling (the alias table in
  ``wfc.contracts.vocabulary``, matched case-insensitively) comes back as
  its canonical name, so the stored contract and everything downstream --
  the editor, ``WFC_PARAMS`` -- only ever see canonical names;
- **refused** -- anything else is refused at parse, naming the parameter.

The expected column is written out here rather than imported from the
package: it pins the vocabulary as decided, and the parser must agree.

Requirement: ``docs/system/contracts.json``, section ``testing.requirements``
("Agreement oracle: one param-type vocabulary").
"""
from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.contracts import parse_method_yaml

#: The closed vocabulary: the six types the canvas renders, plus the
#: document's explicit opt-out.
PARAM_TYPE_VOCABULARY = ("str", "int", "float", "bool", "list", "dict", "any")

#: Accepted spellings and the canonical name each resolves to. Every alias
#: in the table, plus a capitalised variant of every canonical name (the
#: lookup is case-insensitive, so ``Any`` and ``DICT`` are covered without
#: being listed in the table).
NORMALISED = {
    "string": "str",
    "text": "str",
    "integer": "int",
    "number": "float",
    "double": "float",
    "boolean": "bool",
    "dictionary": "dict",
    "map": "dict",
    "mapping": "dict",
    "object": "dict",
    "Str": "str",
    "Int": "int",
    "Float": "float",
    "Bool": "bool",
    "List": "list",
    "DICT": "dict",
    "Any": "any",
}

#: Spellings outside the vocabulary and the alias table. ``array`` is not
#: an accepted spelling of ``list``.
REFUSED = ("strng", "array", "flaot")


def _parse_param_type(tmp_path: Path, type_value: str) -> dict:
    """Parse a real ``method.yaml`` declaring one param of the given type."""
    method_dir = tmp_path / "typed_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        "env: oracle-env\n"
        "outputs:\n  out:\n    type: .csv\n"
        "params:\n"
        "  knob:\n"
        f"    type: {type_value}\n"
        "    default: 1\n"
    )
    contract = parse_method_yaml(method_dir)
    assert contract is not None
    return contract


CELLS = [
    pytest.param(t, t, id=f"verbatim-{t}") for t in PARAM_TYPE_VOCABULARY
] + [
    pytest.param(given, canonical, id=f"normalised-{given}")
    for given, canonical in NORMALISED.items()
] + [
    pytest.param(t, None, id=f"refused-{t}") for t in REFUSED
]


@pytest.mark.parametrize("type_value,expected", CELLS)
@workflow(purpose="The set of param types the parser accepts is exactly the "
                  "closed vocabulary the parameter editor renders plus the "
                  "explicit opt-out: canonical names survive verbatim, "
                  "accepted spellings come back as the canonical name, "
                  "anything else is refused at parse naming the parameter",
          inputs="One declared param type and the canonical name it should "
                 "resolve to (None when it must be refused)",
          outputs="Agreement, or the parser accepting a type the editor "
                  "cannot render or storing a non-canonical spelling")
def test_param_type_vocabulary_is_closed(tmp_path, type_value, expected):
    """One declared type, against the vocabulary both sides must share."""
    if expected is not None:
        contract = _parse_param_type(tmp_path, type_value)
        assert contract["params"]["knob"]["type"] == expected
    else:
        with pytest.raises(ValueError, match="knob"):
            _parse_param_type(tmp_path, type_value)
