"""Contracts unit: the ``columns`` block shape family.

A ``columns`` block on an input or output slot is a declaration: it
documents the slot, feeds the canvas column dropdown and the load-time
declaration cross-check. The parser refuses the shapes that cannot resolve
-- a ``strict`` that is not a list of strings, a ``from_params`` entry
without its parameter list or its pattern, a ``patterns`` that is a bare
string, a block that is not a mapping -- naming the slot, so a malformed
block never parses clean only to resolve to nothing, or fail, at first use.
Well-formed blocks of each kind survive unchanged.

Nothing here opens a data file. Catalog: ``declaration-shape``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from axiom_annotations import workflow

from wfc.contracts import parse_method_yaml


def _parse_with_columns(tmp_path: Path, block, *, on: str) -> dict:
    """Parse a real ``method.yaml`` carrying ``block`` as a slot's ``columns``.

    ``on`` is ``"inputs"``, ``"outputs"`` or ``"both"``: the input slot is
    ``raw``, the output slot is ``out``.
    """
    method_dir = tmp_path / "columned_method"
    method_dir.mkdir()
    raw_slot = {"type": ".csv", "required": True}
    out_slot = {"type": ".csv"}
    if on in ("inputs", "both"):
        raw_slot["columns"] = block
    if on in ("outputs", "both"):
        out_slot["columns"] = block
    (method_dir / "method.yaml").write_text(yaml.safe_dump({
        "env": "shape-env",
        "inputs": {"raw": raw_slot},
        "outputs": {"out": out_slot},
        "params": {"marker": {"type": "str", "default": "cd3"}},
    }))
    contract = parse_method_yaml(method_dir)
    assert contract is not None
    return contract


STRICT = {"strict": ["cell_id", "area"]}
FROM_PARAMS = {"from_params": [{"params": ["marker"], "pattern": "{}_nuc_mean"}]}
PATTERNS = {"patterns": ["*_mean", "*_std"]}
COMBINED = {**STRICT, **FROM_PARAMS, **PATTERNS}

CELLS = [
    # (block, where it is authored, survives?)
    pytest.param(STRICT, "both", True, id="survives-strict"),
    pytest.param(FROM_PARAMS, "both", True, id="survives-from-params"),
    pytest.param(PATTERNS, "both", True, id="survives-patterns"),
    pytest.param(COMBINED, "both", True, id="survives-combined"),
    pytest.param("cell_id", "inputs", False, id="refused-block-not-a-mapping"),
    pytest.param({"strict": "cell_id"}, "inputs", False, id="refused-strict-bare-string"),
    pytest.param({"strict": ["cell_id", 7]}, "inputs", False, id="refused-strict-non-string-item"),
    pytest.param({"from_params": [{"pattern": "{}_nuc_mean"}]}, "inputs", False,
                 id="refused-from-params-no-params"),
    pytest.param({"from_params": [{"params": ["marker"]}]}, "outputs", False,
                 id="refused-from-params-no-pattern-on-output-slot"),
    pytest.param({"patterns": "*_mean"}, "inputs", False, id="refused-patterns-bare-string"),
]


@pytest.mark.parametrize("block,on,survives", CELLS)
@workflow(purpose="A columns block whose strict is not a list of strings, whose "
                  "from_params entry lacks its parameter list or its pattern, "
                  "or whose patterns is a bare string is refused at parse "
                  "naming the slot; well-formed blocks of each kind survive "
                  "unchanged on input and output slots alike",
          inputs="One columns block, the slot(s) it is declared on, and "
                 "whether it is well-formed",
          outputs="The block back verbatim, or a ValueError naming the slot")
def test_columns_block_shape(tmp_path, block, on, survives):
    """One columns block through the parser, on a real declaration file."""
    if survives:
        contract = _parse_with_columns(tmp_path, block, on=on)
        assert contract["inputs"]["raw"]["columns"] == block
        assert contract["outputs"]["out"]["columns"] == block
    else:
        slot = "raw" if on == "inputs" else "out"
        with pytest.raises(ValueError, match=f"slot '{slot}'"):
            _parse_with_columns(tmp_path, block, on=on)
