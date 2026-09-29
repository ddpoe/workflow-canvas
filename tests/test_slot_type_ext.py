"""Behavior-first tests for the slot ``type`` == file-extension scheme.

The method-contract ``type`` field on an output slot IS the file extension —
dot optional (``.h5ad`` and ``h5ad`` both normalise to the canonical dotted
form) — or the directory marker ``dir`` / ``directory``; no semantic
data-type is translated into an extension.  These tests pin the contract:

  exact-extension naming with no silent ``.csv`` default
  fail-loud on an unusable output ``type`` (registration + enrich)
  ``dir`` and ``directory`` both resolve to a canonical directory
  dotted and bare spellings normalise to the same canonical type
"""

from pathlib import Path

import pytest
from sqlmodel import select

from axiom_annotations import workflow

from wfc.persistence import MethodContract, get_session

from tests.fixtures.routes import register_test_method


def _write_method_yaml(method_dir: Path, outputs_block: str) -> Path:
    """Write a minimal valid method.yaml with the given outputs block."""
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        "inputs:\n"
        "  data:\n"
        "    type: .csv\n"
        "    required: true\n"
        f"{outputs_block}"
        "params: {}\n"
        "executor: python\n"
        "env: fixture-env\n",
        encoding="utf-8",
    )
    return method_dir


def _register_m(project_root: Path, output_slots: dict) -> None:
    """Register method ``m`` under ``data_preprocessing`` with the given output slots.

    Writes the method's script and ``method.yaml`` under the project and
    registers them through production, so the stored contract is the one
    registration parsed and validated.

    Args:
        project_root: The project to register into.
        output_slots: Output slot name -> ``{"type": <declared type>}``.
    """
    outputs_block = "outputs:\n" + "".join(
        f"  {slot}:\n    type: {spec['type']}\n"
        for slot, spec in output_slots.items()
    )
    method_dir = _write_method_yaml(project_root / "methods" / "m", outputs_block)
    (method_dir / "m.py").write_text("def main():\n    pass\n", encoding="utf-8")
    register_test_method(project_root, module_name="data_preprocessing",
                         method_dir=method_dir)


def _enrich_single_node():
    """Build + enrich a one-node pipeline targeting the seeded method ``m``."""
    from wfc.canvas.models import PipelineInput, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline

    pipeline = PipelineInput(
        name="t",
        nodes=[PipelineNode(id="n1", method="m", module="data_preprocessing", params={})],
        links=[],
        samples=["S1"],
    )
    return _enrich_pipeline(pipeline)


@workflow(purpose="Output slot type is the file extension, named verbatim with no silent .csv default")
def test_enrich_names_files_from_extension_verbatim(tmp_project):
    """A contract declaring ``.h5ad`` + ``.parquet`` produces ``<slot>.h5ad`` /
    ``<slot>.parquet`` filenames — never a silent ``.csv`` default."""
    _register_m(
        tmp_project,
        output_slots={
            "embedding": {"type": ".h5ad"},
            "table": {"type": ".parquet"},
        },
    )
    node = _enrich_single_node()["nodes"][0]
    assert node["slot_outputs"] == {
        "embedding": "embedding.h5ad",
        "table": "table.parquet",
    }
    assert node["slot_types"] == {"embedding": ".h5ad", "table": ".parquet"}


@workflow(purpose="Registration rejects an output slot type that is empty or missing")
@pytest.mark.parametrize("bad_type_block", [
    "outputs:\n  out:\n    type: ''\n",        # empty
    "outputs:\n  out:\n    required: true\n",  # missing entirely
])
def test_parse_method_yaml_rejects_unusable_output_type(tmp_path, bad_type_block):
    """parse_method_yaml fails loud (ValueError) on an unusable output ``type``."""
    from wfc.contracts import parse_method_yaml

    method_dir = _write_method_yaml(tmp_path / "methods" / "bad", bad_type_block)
    with pytest.raises(ValueError, match=r"(?i)extension|dir"):
        parse_method_yaml(method_dir)


@workflow(purpose="Dotted and bare extension spellings both parse and normalise to the canonical dotted type")
@pytest.mark.parametrize("declared,canonical", [
    (".csv", ".csv"),        # dotted, verbatim
    ("csv", ".csv"),         # bare — dot is optional
    ("tar.gz", ".tar.gz"),   # bare compound extension
])
def test_parse_method_yaml_normalises_extension_type(tmp_path, declared, canonical):
    """Both spellings are valid; the stored contract carries the dotted form."""
    from wfc.contracts import parse_method_yaml

    method_dir = _write_method_yaml(
        tmp_path / "methods" / "ok",
        f"outputs:\n  out:\n    type: {declared}\n",
    )
    contract = parse_method_yaml(method_dir)
    assert contract["outputs"]["out"]["type"] == canonical


@workflow(purpose="A persisted contract with an invalid output type still raises at enrich (backstop)")
def test_enrich_backstop_raises_on_invalid_persisted_type(tmp_project):
    """A DB contract whose output ``type`` never passed registration
    validation raises at enrich."""
    # Registration refuses an empty output type, so no writer produces this
    # row: the method is registered with a valid type and the one persisted
    # ``output_slots`` value is then corrupted in place. This proves enrich
    # refuses a persisted invalid type; it does not prove any writer can
    # produce one.
    _register_m(tmp_project, output_slots={"out": {"type": ".csv"}})
    with get_session() as session:
        contract = session.exec(select(MethodContract)).one()
        contract.output_slots = {
            **contract.output_slots,
            "out": {**contract.output_slots["out"], "type": ""},
        }
        session.add(contract)
        session.commit()
    with pytest.raises(ValueError, match=r"(?i)extension|dir"):
        _enrich_single_node()


@workflow(purpose="Both 'dir' and 'directory' resolve to a canonical directory slot with no extension")
def test_dir_and_directory_both_detected_as_directory(tmp_project):
    """``type: dir`` and ``type: directory`` both yield extension-less filenames,
    canonical ``directory`` slot_types, and is_directory_slot True."""
    from wfc.contracts import is_directory_slot

    _register_m(
        tmp_project,
        output_slots={
            "tiles": {"type": "dir"},
            "masks": {"type": "directory"},
        },
    )
    node = _enrich_single_node()["nodes"][0]
    assert node["slot_outputs"] == {"tiles": "tiles", "masks": "masks"}
    assert node["slot_types"] == {"tiles": "directory", "masks": "directory"}
    assert is_directory_slot(node, "tiles") is True
    assert is_directory_slot(node, "masks") is True


@workflow(purpose="A mixed directory + typed-file node emits slot_types and slot_outputs one-to-one")
def test_enrich_mixed_directory_and_file_node(tmp_project):
    """A contract with a directory slot and a .json slot enriches to parallel
    slot_types (directory / .json) and slot_outputs (bare dir name / config.json),
    one-to-one — directory detection needs no filename-shape heuristics."""
    _register_m(
        tmp_project,
        output_slots={
            "tiles_dir": {"type": "directory"},
            "config": {"type": ".json"},
        },
    )
    node = _enrich_single_node()["nodes"][0]
    assert node["slot_types"] == {"tiles_dir": "directory", "config": ".json"}
    assert node["slot_outputs"] == {"tiles_dir": "tiles_dir", "config": "config.json"}
