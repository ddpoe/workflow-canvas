"""Contracts unit: the ``options`` block shape family.

An ``options`` block on an output slot is a declaration: it names what a
downstream parameter may pick and feeds the canvas's options picker. The
parser refuses the shapes that cannot resolve -- a ``fixed`` that is not a
list of strings, a ``from_params`` entry without its parameter list or its
pattern, a ``patterns`` that is a bare string, a block that is not a
mapping -- naming the slot, so a malformed block never parses clean only to
resolve to nothing, or fail, at first use. Well-formed blocks of each kind
survive unchanged.

Nothing here opens a data file. Catalog: ``declaration-shape``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from axiom_annotations import workflow

from wfc.contracts import parse_method_yaml


def _parse_with_options(tmp_path: Path, block) -> dict:
    """Parse a real ``method.yaml`` carrying ``block`` as output ``out``'s ``options``."""
    method_dir = tmp_path / "optioned_method"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(yaml.safe_dump({
        "env": "shape-env",
        "inputs": {"raw": {"type": ".csv", "required": True}},
        "outputs": {"out": {"type": ".csv", "options": block}},
        "params": {"marker": {"type": "str", "default": "cd3"}},
    }))
    contract = parse_method_yaml(method_dir)
    assert contract is not None
    return contract


FIXED = {"fixed": ["cell_id", "area"]}
FROM_PARAMS = {"from_params": [{"params": ["marker"], "pattern": "{}_nuc_mean"}]}
PATTERNS = {"patterns": ["*_mean", "*_std"]}
COMBINED = {**FIXED, **FROM_PARAMS, **PATTERNS}

CELLS = [
    # (block, survives?)
    pytest.param(FIXED, True, id="survives-fixed"),
    pytest.param(FROM_PARAMS, True, id="survives-from-params"),
    pytest.param(PATTERNS, True, id="survives-patterns"),
    pytest.param(COMBINED, True, id="survives-combined"),
    pytest.param("cell_id", False, id="refused-block-not-a-mapping"),
    pytest.param({"fixed": "cell_id"}, False, id="refused-fixed-bare-string"),
    pytest.param({"fixed": ["cell_id", 7]}, False, id="refused-fixed-non-string-item"),
    pytest.param({"from_params": [{"pattern": "{}_nuc_mean"}]}, False,
                 id="refused-from-params-no-params"),
    pytest.param({"from_params": [{"params": ["marker"]}]}, False,
                 id="refused-from-params-no-pattern"),
    pytest.param({"patterns": "*_mean"}, False, id="refused-patterns-bare-string"),
]


@pytest.mark.parametrize("block,survives", CELLS)
@workflow(purpose="An output's options block whose fixed is not a list of "
                  "strings, whose from_params entry lacks its parameter list "
                  "or its pattern, or whose patterns is a bare string is "
                  "refused at parse naming the slot and the block's form; "
                  "well-formed blocks of each kind survive unchanged",
          inputs="One options block on an output slot, and whether it is "
                 "well-formed",
          outputs="The block back verbatim, or a ValueError naming the slot")
def test_options_block_shape(tmp_path, block, survives):
    """One options block through the parser, on a real declaration file."""
    if survives:
        contract = _parse_with_options(tmp_path, block)
        assert contract["outputs"]["out"]["options"] == block
    else:
        with pytest.raises(ValueError, match="slot 'out'") as refused:
            _parse_with_options(tmp_path, block)
        assert "`fixed`" in str(refused.value)
        assert "`from_params`" in str(refused.value)
        assert "`patterns`" in str(refused.value)
