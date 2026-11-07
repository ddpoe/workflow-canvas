"""Contracts unit: which ``method.yaml`` edits move the code fingerprint.

The contract projection is the whole parsed contract except descriptions,
formatting (key order, layout, comments), ``env`` and ``helpers``. Each row
edits one thing in a real ``method.yaml``, parses it with
``parse_method_yaml``, and fingerprints the directory through
``build_code_fingerprint`` over ``render_contract_projection``: the
production recipe ``snapshot_code_fingerprint`` runs. The scripts never
change, so the contract is the only thing that can move the fingerprint.

Catalog: ``enrichment.contract-projection``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wfc.contracts import parse_method_yaml, render_contract_projection
from wfc.identity import build_code_fingerprint

BASELINE = """\
# a leading comment
env: proj-env
executor: python
script: m.py
gpus: false
inputs:
  data:
    type: .csv
    description: the table to read
outputs:
  table:
    type: .csv
    description: the table written
    options:
      fixed: [x, y]
params:
  count:
    type: int
    default: 1
    description: how many
    constraints:
      min: 0
      max: 5
  ratio:
    type: float
    default: 1.0
  col:
    type: str
    options_from: data
"""

#: The baseline with every mapping's keys in another order and the comments moved.
REORDERED = """\
params:
  col:
    options_from: data
    type: str
  ratio:
    default: 1.0
    type: float
  count:
    constraints:
      max: 5
      min: 0
    description: how many
    default: 1
    type: int
outputs:
  table:
    description: the table written
    options:
      fixed: [x, y]
    type: .csv
inputs:
  data:
    description: the table to read
    type: .csv
gpus: false
# a comment somewhere else
script: m.py
executor: python
env: proj-env
"""


def _edit(old: str, new: str) -> str:
    """The baseline with the one occurrence of ``old`` replaced by ``new``."""
    assert BASELINE.count(old) == 1, old
    return BASELINE.replace(old, new)


# label -> (the method.yaml replacing the baseline, does the fingerprint move?)
EDITS = {
    # --- a parameter's declaration ---
    "param-type": (_edit("    type: int\n", "    type: float\n"), True),
    "param-required": (_edit("    default: 1\n", "    required: true\n    default: 1\n"),
                       True),
    "param-default": (_edit("    default: 1\n", "    default: 2\n"), True),
    "param-constraint": (_edit("      max: 5\n", "      max: 6\n"), True),
    "param-options-from": (_edit("    options_from: data\n",
                                 "    options_from: [data, other]\n"), True),
    "param-options-from-removed": (_edit("    options_from: data\n", ""), True),
    "param-other-key": (_edit("    default: 1.0\n", "    default: 1.0\n    widget: slider\n"),
                        True),
    "param-added": (BASELINE + "  extra:\n    type: bool\n", True),
    # --- an input slot's declaration ---
    "input-required-explicit-true": (_edit("    description: the table to read\n",
                                           "    description: the table to read\n"
                                           "    required: true\n"), True),
    "input-required-false": (_edit("    description: the table to read\n",
                                   "    description: the table to read\n"
                                   "    required: false\n"), True),
    "input-type": (_edit("  data:\n    type: .csv\n", "  data:\n    type: .tsv\n"), True),
    # --- an output slot's declaration ---
    "output-required": (_edit("    description: the table written\n",
                              "    description: the table written\n"
                              "    required: false\n"), True),
    "output-options": (_edit("      fixed: [x, y]\n", "      fixed: [x, z]\n"), True),
    "output-options-list-order": (_edit("      fixed: [x, y]\n", "      fixed: [y, x]\n"),
                                  True),
    "output-contents": (_edit("    description: the table written\n",
                              "    description: the table written\n"
                              "    contents: [part.csv]\n"), True),
    # --- how the script is invoked ---
    "executor": (_edit("executor: python\n", "executor: r\n"), True),
    "script": (_edit("script: m.py\n", "script: alt.py\n"), True),
    "gpus": (_edit("gpus: false\n", "gpus: true\n"), True),
    # --- what only describes, formats or is keyed elsewhere ---
    "input-description": (_edit("the table to read", "rewritten"), False),
    "output-description": (_edit("the table written", "rewritten"), False),
    "param-description": (_edit("how many", "rewritten"), False),
    "top-level-description": ("description: what it does\n" + BASELINE, False),
    "mapping-reorder-and-comment": (REORDERED, False),
    "comment": (BASELINE + "# a trailing comment\n", False),
    "env": (_edit("env: proj-env\n", "env: other-env\n"), False),
    "helpers": (BASELINE + "helpers: []\n", False),
    # --- a declared default spelled in another form of its type ---
    "int-default-written-as-float": (_edit("    default: 1\n", "    default: 1.0\n"), False),
    "float-default-written-as-int": (_edit("    default: 1.0\n", "    default: 1\n"), False),
}


def _fingerprint(directory: Path, method_yaml: str) -> str:
    """Write ``method_yaml`` beside fixed scripts and fingerprint the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "m.py").write_text("print('m')\n", encoding="utf-8")
    (directory / "alt.py").write_text("print('alt')\n", encoding="utf-8")
    (directory / "method.yaml").write_text(method_yaml, encoding="utf-8")
    return build_code_fingerprint(
        directory, render_contract_projection(parse_method_yaml(directory)))


@pytest.mark.parametrize("label", list(EDITS))
def test_a_method_yaml_edit_moves_the_fingerprint_unless_it_only_describes(
        tmp_path, label):
    """An edit to a declaration moves it; a description, layout, env or helpers edit does not."""
    edited, moves = EDITS[label]
    before = _fingerprint(tmp_path / "before", BASELINE)
    after = _fingerprint(tmp_path / "after", edited)
    if moves:
        assert after != before, f"'{label}' changed the contract but not the fingerprint"
    else:
        assert after == before, f"'{label}' changed nothing that runs but moved the fingerprint"


def test_the_shorthand_and_mapping_spellings_of_a_slot_project_alike():
    """``table: .csv`` and ``table: {type: .csv}`` declare one slot."""
    base = {"inputs": {}, "params": {}, "executor": "python", "script": None,
            "gpus": False, "env": "e", "helpers": None}
    shorthand = render_contract_projection({**base, "outputs": {"table": ".csv"}})
    mapping = render_contract_projection(
        {**base, "outputs": {"table": {"type": ".csv", "description": "d"}}})
    assert shorthand == mapping
