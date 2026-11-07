"""Registration refuses the retired options forms and a bad ``options_from``.

The options family replaced ``columns`` (on outputs and inputs),
``column_of_input``, ``new_column`` and ``strict``. Registration refuses a
method.yaml declaring any of them, each message naming the slot or parameter
and what replaces the form, and refuses an ``options_from`` naming an input
the method does not declare. Nothing is registered. A well-formed ``options``
block beside an ``options_from`` parameter registers. The parser stays
lenient, so versions registered before stay readable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from wfc.persistence import Method, get_session

from tests.fixtures.routes import register_test_method

PLAIN_INPUT = "  data:\n    type: .csv\n    required: true\n"
PLAIN_OUTPUT = "  out:\n    type: .csv\n"


def _write_method(project_root: Path, *, inputs: str = PLAIN_INPUT,
                  outputs: str = PLAIN_OUTPUT, params: str = "  k:\n    type: str\n"
                  ) -> Path:
    """Write method ``m``'s script and a method.yaml from the given blocks."""
    method_dir = project_root / "methods" / "m"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        f"inputs:\n{inputs}outputs:\n{outputs}params:\n{params}"
        "executor: python\nenv: fixture-env\n",
        encoding="utf-8",
    )
    (method_dir / "m.py").write_text("def main():\n    pass\n", encoding="utf-8")
    return method_dir


def _registered() -> list[str]:
    with get_session() as session:
        return [m.name for m in session.exec(select(Method)).all()]


RETIRED = {
    "output-columns": (
        {"outputs": PLAIN_OUTPUT + "    columns:\n      strict: [id]\n"},
        "output slot 'out' declares `columns`, which is now `options:`"),
    "input-columns": (
        {"inputs": PLAIN_INPUT + "    columns:\n      strict: [id]\n"},
        "input slot 'data' declares `columns`, which is removed: declare "
        "`options:` on the upstream method's output"),
    "input-options": (
        {"inputs": PLAIN_INPUT + "    options:\n      fixed: [id]\n"},
        "input slot 'data' declares `options`, which is removed: declare "
        "`options:` on the upstream method's output"),
    "column-of-input": (
        {"params": "  k:\n    type: str\n    column_of_input: data\n"},
        "parameter 'k' declares `column_of_input`, which is now `options_from`"),
    "new-column": (
        {"params": "  k:\n    type: str\n    new_column: true\n"},
        "parameter 'k' declares `new_column`, which is removed"),
    "strict-in-options": (
        {"outputs": PLAIN_OUTPUT + "    options:\n      strict: [id]\n"},
        "output slot 'out' declares `strict` in its `options`, which is now "
        "`fixed`"),
    "options-from-not-names": (
        {"params": "  k:\n    type: str\n    options_from: 3\n"},
        "parameter 'k' `options_from` must be an input slot name or a list of "
        "input slot names, got int"),
}


@pytest.mark.parametrize("blocks, message", list(RETIRED.values()),
                         ids=list(RETIRED))
@workflow(purpose="Each retired options form -- columns on an output or an "
                  "input, options on an input, column_of_input, new_column, "
                  "strict inside options -- and a malformed options_from is "
                  "refused at registration naming the slot or parameter and "
                  "its replacement, and no method is registered")
def test_each_retired_form_is_refused_naming_its_replacement(
        tmp_project, blocks, message):
    method_dir = _write_method(tmp_project, **blocks)
    with pytest.raises(ValueError) as exc:
        register_test_method(tmp_project, module_name="data_preprocessing",
                             method_dir=method_dir)
    assert message in str(exc.value)
    assert "Method 'm': method.yaml" in str(exc.value)
    assert _registered() == []


@pytest.mark.parametrize("options_from", ["mask", "[data, mask]"],
                         ids=["string", "list"])
@workflow(purpose="A parameter whose options_from names an input the method "
                  "does not declare, as a string or inside a list, is refused "
                  "at registration naming the parameter and the input")
def test_options_from_naming_an_undeclared_input_is_refused(
        tmp_project, options_from):
    method_dir = _write_method(
        tmp_project, params=f"  k:\n    type: str\n    options_from: {options_from}\n")
    with pytest.raises(ValueError) as exc:
        register_test_method(tmp_project, module_name="data_preprocessing",
                             method_dir=method_dir)
    assert ("parameter 'k' `options_from` names input 'mask', which the method "
            "does not declare (inputs: data)") in str(exc.value)
    assert "'data'" not in str(exc.value)
    assert _registered() == []


@workflow(purpose="A method with a well-formed options block on its output and "
                  "a parameter whose options_from names its declared inputs "
                  "registers")
def test_a_well_formed_options_declaration_registers(tmp_project):
    method_dir = _write_method(
        tmp_project,
        inputs=PLAIN_INPUT + "  other:\n    type: .csv\n    required: false\n",
        outputs=PLAIN_OUTPUT + "    options:\n      fixed: [id]\n"
                "      from_params:\n        - params: [k]\n"
                "          pattern: '{}_mean'\n      patterns: ['*_sd']\n",
        params="  k:\n    type: str\n    options_from: [data, other]\n"
               "  j:\n    type: str\n    options_from: data\n")
    register_test_method(tmp_project, module_name="data_preprocessing",
                         method_dir=method_dir)
    assert _registered() == ["m"]
