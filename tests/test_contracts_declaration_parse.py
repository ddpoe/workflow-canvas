"""Contracts unit: the declaration parse families.

The method-declaration family (``parse_method_yaml``), parametrized
over real ``method.yaml`` files under ``tmp_path``: no file means nothing
(registration then proceeds without slot rows); invalid YAML and a
non-mapping top level are errors naming the path; a ``script`` that is not
a string or a ``helpers`` that is not a list of strings is refused on shape
while well-shaped values survive verbatim; an input slot with a
non-convention type warns naming the slot and the contract still comes back
with its wiring intact; a valid input type comes back in canonical form.

The module-contract family (``parse_module_yaml``): ``module.yaml``
absent means nothing; a non-list ``contracts``, a non-mapping entry, and an
entry missing ``type`` or ``name`` are errors naming the entry's index; a
well-formed entry defaults ``value_type`` to null and ``required`` to true.

Catalog: ``parse-absent``, ``parse-malformed``, ``parse-script-helpers``,
``input-type-advisory``, ``parse-module``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.contracts import parse_method_yaml, parse_module_yaml

BASE = "env: parse-env\noutputs:\n  out:\n    type: .csv\n"


def _dir_with(tmp_path: Path, filename: str, content: str | None) -> Path:
    """A directory carrying ``filename`` with ``content``, or nothing."""
    d = tmp_path / "decl"
    d.mkdir()
    if content is not None:
        (d / filename).write_text(content)
    return d


def _warned_about(log, slot: str) -> bool:
    return any(r.levelno == logging.WARNING and f"'{slot}'" in r.getMessage()
               for r in log.records)


# (method.yaml content or None, ValueError match or None, check(contract, caplog))
METHOD_CELLS = [
    pytest.param(None, None, lambda c, log: c is None, id="parse-absent"),
    pytest.param("env: [unclosed\n", "method.yaml", None,
                 id="parse-malformed-invalid-yaml"),
    pytest.param("- not\n- a mapping\n", "method.yaml", None,
                 id="parse-malformed-non-mapping-top-level"),
    pytest.param(BASE + "script: [run.py]\n", "script", None,
                 id="parse-script-not-a-string"),
    pytest.param(BASE + "helpers: util.py\n", "helpers", None,
                 id="parse-helpers-not-a-list"),
    pytest.param(BASE + "helpers: [util.py, 7]\n", "helpers", None,
                 id="parse-helpers-non-string-item"),
    pytest.param(BASE + "script: run.py\nhelpers: [util.py, lib/extra.py]\n", None,
                 lambda c, log: (c["script"] == "run.py"
                                 and c["helpers"] == ["util.py", "lib/extra.py"]),
                 id="parse-script-helpers-survive-verbatim"),
    pytest.param(BASE + "inputs:\n  raw:\n    type: ''\n    required: true\n", None,
                 lambda c, log: (c["inputs"]["raw"]["required"] is True
                                 and _warned_about(log, "raw")),
                 id="input-type-advisory-warns-wiring-intact"),
    pytest.param(BASE + "inputs:\n  raw:\n    type: csv\n", None,
                 lambda c, log: c["inputs"]["raw"]["type"] == ".csv",
                 id="input-type-canonical-form"),
]


@pytest.mark.parametrize("content,refused,check", METHOD_CELLS)
@workflow(purpose="A method declaration parsed from a real file: absent means "
                  "nothing, malformed YAML or a non-mapping top level is an "
                  "error naming the path, script and helpers are refused on "
                  "shape and survive verbatim when well-shaped, an advisory "
                  "input type warns naming the slot without breaking the "
                  "wiring, and a valid input type comes back canonical",
          inputs="One method.yaml (or none) under tmp_path",
          outputs="None, the contract dict, or a ValueError naming the "
                  "path or the offending key")
def test_method_declaration_parse(tmp_path, caplog, content, refused, check):
    """One declaration file through ``parse_method_yaml``."""
    caplog.set_level(logging.WARNING, logger="wfc.contracts.declarations")
    method_dir = _dir_with(tmp_path, "method.yaml", content)
    if refused is not None:
        with pytest.raises(ValueError, match=refused):
            parse_method_yaml(method_dir)
    else:
        assert check(parse_method_yaml(method_dir), caplog)


# (module.yaml content or None, ValueError match or None, check(module))
MODULE_CELLS = [
    pytest.param(None, None, lambda m: m is None, id="parse-module-absent"),
    pytest.param("contracts: model\n", "contracts", None,
                 id="parse-module-contracts-not-a-list"),
    pytest.param("contracts:\n  - model\n", r"contract\[0\]", None,
                 id="parse-module-entry-not-a-mapping"),
    pytest.param("contracts:\n  - {type: output, name: model}\n  - {type: metric}\n",
                 r"contract\[1\]", None, id="parse-module-entry-missing-name"),
    pytest.param("description: trains a model\ncontracts:\n  - {type: output, name: model}\n",
                 None,
                 lambda m: (m["description"] == "trains a model"
                            and m["contracts"] == [{"type": "output", "name": "model",
                                                    "value_type": None, "required": True}]),
                 id="parse-module-well-formed-defaults"),
]


@pytest.mark.parametrize("content,refused,check", MODULE_CELLS)
@workflow(purpose="A module declaration parsed from a real file: absent means "
                  "nothing, a non-list contracts block, a non-mapping entry "
                  "and an entry missing type or name are errors naming the "
                  "entry's index, and a well-formed entry defaults "
                  "value_type to null and required to true",
          inputs="One module.yaml (or none) under tmp_path",
          outputs="None, the module dict, or a ValueError naming the entry")
def test_module_declaration_parse(tmp_path, content, refused, check):
    """One declaration file through ``parse_module_yaml``."""
    module_dir = _dir_with(tmp_path, "module.yaml", content)
    if refused is not None:
        with pytest.raises(ValueError, match=refused):
            parse_module_yaml(module_dir)
    else:
        assert check(parse_module_yaml(module_dir))


# -- slot names: the denylist ------------------------------------------------
#
# A slot name travels through the parent-entry grammar (input:output:run), the
# --ref-input flag (label=path) and process argv. A name carrying one of those
# delimiters does not fail there -- it reads as a different wiring. The refusal
# lands where the name is declared.

SLOT_NAME_CELLS = [
    pytest.param("inputs:\n  'a:b': a file\n", "input", "a:b",
                 id="slot-name-input-colon"),
    pytest.param("inputs:\n  'a=b': a file\n", "input", "a=b",
                 id="slot-name-input-equals"),
    pytest.param("inputs:\n  'a b': a file\n", "input", "a b",
                 id="slot-name-input-whitespace"),
    pytest.param("inputs:\n  '': a file\n", "input", "''",
                 id="slot-name-input-empty"),
    pytest.param("outputs:\n  'a:b':\n    type: .csv\n", "output", "a:b",
                 id="slot-name-output-colon"),
    pytest.param("outputs:\n  'a=b':\n    type: .csv\n", "output", "a=b",
                 id="slot-name-output-equals"),
    pytest.param("outputs:\n  'a b':\n    type: .csv\n", "output", "a b",
                 id="slot-name-output-whitespace"),
    pytest.param("outputs:\n  '':\n    type: .csv\n", "output", "''",
                 id="slot-name-output-empty"),
]


@pytest.mark.parametrize("block,kind,offender", SLOT_NAME_CELLS)
@workflow(purpose="A slot name carrying a grammar delimiter, whitespace, or "
                  "nothing at all is refused at registration naming the slot, "
                  "the half it is declared in and the method.yaml; a clean "
                  "contract still parses",
          inputs="One method.yaml declaring the slot name under tmp_path",
          outputs="A ValueError naming the slot and the file")
def test_slot_name_denylist(tmp_path, block, kind, offender):
    """One declared slot name through ``parse_method_yaml``."""
    method_dir = _dir_with(tmp_path, "method.yaml", "env: parse-env\n" + block)
    with pytest.raises(ValueError) as exc:
        parse_method_yaml(method_dir)
    message = str(exc.value)
    assert offender in message
    assert kind in message
    assert str(method_dir / "method.yaml") in message


def test_clean_slot_names_still_parse(tmp_path):
    """Tier 1: the denylist refuses only what it names -- hyphens, underscores,
    digits and dots in a slot name keep registering."""
    method_dir = _dir_with(
        tmp_path, "method.yaml",
        "env: parse-env\n"
        "inputs:\n  ref_panel-1: a file\n  counts.raw: a file\n"
        "outputs:\n  out-2:\n    type: .csv\n",
    )
    contract = parse_method_yaml(method_dir)
    assert set(contract["inputs"]) == {"ref_panel-1", "counts.raw"}
    assert set(contract["outputs"]) == {"out-2"}
