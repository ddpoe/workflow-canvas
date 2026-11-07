"""Single-file inputs and undeclared names, host side (US-3).

Case 13: more than one file into an input without ``multiple: true`` is
refused before the run, naming the node and the input, by the rule canvas
validate (``validate_structure``) and ``wfc run-pipeline`` (``load_pipeline``)
both reach. Case 15 (registration half): a literal ``ctx.input`` name the
method does not declare is refused at registration, naming the declared
inputs. Contracts parses ``multiple`` (absent = one file).
"""

from __future__ import annotations

import textwrap

import pytest
from axiom_annotations import workflow

from wfc.contracts import declared_names, parse_method_yaml
from wfc.graph import load_pipeline, validate_structure


def _contract_map(multiple: bool | None) -> dict:
    data = {"type": ".csv"}
    if multiple is not None:
        data["multiple"] = multiple
    return {"mod.m": {"input_slots": {"data": data},
                      "output_slots": {"out": {"type": ".csv"}}}}


def _fan_in_doc(*, slot_key: str) -> dict:
    """A fan-in selector bundling two samples into node ``m1``'s ``data``."""
    return {
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "in",
             "samples": ["S1", "S2"]},
            {"id": "m1", "type": "method", "module": "mod", "method": "m",
             "env": "e"},
        ],
        "links": [{"source": "sel", "target": "m1", slot_key: "data"}],
        "samples": ["S1", "S2"],
    }


def _reference_doc() -> dict:
    """Two run references wired into node ``m1``'s ``data`` (sparse form)."""
    return {
        "nodes": [
            {"id": "r1", "type": "run_reference", "run_id": "a"},
            {"id": "r2", "type": "run_reference", "run_id": "b"},
            {"id": "m1", "type": "method", "module": "mod", "method": "m"},
        ],
        "links": [
            {"source": "r1", "target": "m1", "targetHandle": "data"},
            {"source": "r2", "target": "m1", "targetHandle": "data"},
        ],
    }


@workflow(purpose="More than one file into a single input is refused by canvas "
                  "validate and by the load run-pipeline reaches, naming the "
                  "node and the input; a multiple input takes them")
def test_several_files_into_a_single_input_refused_at_both_moments():
    for doc in (_fan_in_doc(slot_key="targetHandle"), _reference_doc()):
        result = validate_structure(doc, _contract_map(None))
        assert not result["valid"]
        refusal = [e for e in result["errors"] if "takes one file" in e]
        assert refusal and "'data'" in refusal[0] and "'m1'" in refusal[0]
        assert "multiple: true" in refusal[0]
        # Declared `multiple: true`, the same wiring raises no such error.
        allowed = validate_structure(doc, _contract_map(True))
        assert not [e for e in allowed["errors"] if "takes one file" in e]

    with pytest.raises(ValueError, match=r"Input 'data' on node 'm1' takes one file"):
        load_pipeline(_fan_in_doc(slot_key="target_slot"),
                      contract_map=_contract_map(False), reference_outputs={})
    pipeline = load_pipeline(_fan_in_doc(slot_key="target_slot"),
                             contract_map=_contract_map(True), reference_outputs={})
    assert [s.node_id for s in pipeline.steps] == ["m1"]


def test_multiple_must_be_a_boolean(tmp_path):
    (tmp_path / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n    multiple: several\n"
        "outputs:\n  out:\n    type: .csv\n"
        "env: e\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"input 'data' declares `multiple"):
        parse_method_yaml(tmp_path)


def test_declared_names_reads_multiple_and_optional():
    names = declared_names(
        {"data": {"type": ".csv"}, "many": {"multiple": True},
         "extra": {"required": False}},
        {"out": {"type": ".csv"}},
    )
    assert names == {
        "inputs": {"data": {"multiple": False, "optional": False},
                   "many": {"multiple": True, "optional": False},
                   "extra": {"multiple": False, "optional": True}},
        "outputs": ["out"],
    }


@workflow(purpose="Registration refuses a literal ctx.input name the method "
                  "does not declare, naming the declared inputs")
def test_registration_refuses_literal_undeclared_input_name(tmp_project):
    from tests.fixtures.routes import register_test_method

    method_dir = tmp_project / "methods" / "m"
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n    required: true\n"
        "outputs:\n  clean:\n    type: .csv\n    required: true\n"
        "params: {}\nexecutor: python\nenv: fixture-env\n", encoding="utf-8")
    (method_dir / "m.py").write_text(textwrap.dedent("""\
        import wfc_client as wfc

        @wfc.method
        def m(ctx):
            src = ctx.input("dta")
            out = ctx.workdir / "clean.csv"
            ctx.save_artifact("clean", out)
    """), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        register_test_method(tmp_project, module_name="data_preprocessing",
                             method_dir=method_dir)
    assert "dta" in str(exc.value)
    assert "Declared inputs: ['data']" in str(exc.value)
