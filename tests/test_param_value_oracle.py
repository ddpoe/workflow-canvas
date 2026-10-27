"""The server half of the parameter value agreement oracle.

One table of (type, constraints, value, verdict) rows lives inside the canvas
source tree, where Vitest reads it for the canvas editor's check; this file
runs every row through the server's check, and asserts each row's
``normalized`` value (value and Python type) through the server's
normalization. The type vocabulary the table names must be the server's own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wfc.contracts import (
    PARAM_TYPES,
    check_param_value,
    normalize_param_value,
    param_refusals,
)

TABLE = (Path(__file__).resolve().parents[1] / "wfc" / "canvas" / "static"
         / "src" / "lib" / "shared" / "paramValueOracle.json")
_ROWS = json.loads(TABLE.read_text(encoding="utf-8"))["rows"]


def test_the_table_names_the_servers_type_vocabulary():
    """Both checks classify over one type set, and the table covers each type."""
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    assert tuple(table["types"]) == PARAM_TYPES
    assert {row["type"] for row in table["rows"]} == set(PARAM_TYPES)


@pytest.mark.parametrize("row", _ROWS, ids=[
    f"{r['type']}-{json.dumps(r.get('constraints'))}-{json.dumps(r['value'])}"
    f"-{'req' if r.get('required') else 'opt'}" for r in _ROWS])
def test_the_server_check_gives_each_rows_verdict(row):
    """The server accepts exactly the values the table accepts."""
    reason = check_param_value(row["type"], row.get("constraints"), row["value"],
                               required=bool(row.get("required")))
    verdict = "accept" if reason is None else "refuse"
    assert verdict == row["verdict"], reason
    if "normalized" in row:
        normalized = normalize_param_value(row["type"], row["value"],
                                           row.get("constraints"))
        assert normalized == row["normalized"]
        assert type(normalized) is type(row["normalized"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "-inf"])
def test_an_int_parameter_refuses_a_non_finite_float_naming_it(value):
    """Server only: JSON that JavaScript parses cannot carry these values."""
    refusals = param_refusals("n1", "mod.meth", "v1", {"count": value},
                              {"count": {"type": "int"}})
    assert len(refusals) == 1
    assert refusals[0].startswith(
        "Node 'n1' (mod.meth v1): parameter 'count' expects int, got ")
    assert normalize_param_value("int", value) is value
