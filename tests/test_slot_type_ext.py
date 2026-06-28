"""Behavior-first tests for the slot ``type`` == file-extension scheme.

The method-contract ``type`` field on an output slot no longer names a
semantic data-type that is translated through a hidden ``_TYPE_EXT_MAP``;
it now IS the file extension, declared verbatim (dotted, e.g. ``.h5ad``),
or the directory marker ``dir`` / ``directory``.  These tests pin the
new contract:

  US-1  exact-extension naming with no silent ``.csv`` default
  US-2  fail-loud on an unusable output ``type`` (registration + enrich)
  US-3  ``dir`` and ``directory`` both resolve to a canonical directory
"""

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from axiom_annotations import workflow

import wfc.models  # noqa: F401 — register tables on the shared metadata
from wfc.models import Method, MethodContract, Module


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
        "env: container:fixture-env\n",
        encoding="utf-8",
    )
    return method_dir


def _seed_engine(tmp_path, monkeypatch, output_slots: dict):
    """In-memory-on-disk wfc DB seeded with one method + the given output_slots."""
    db_path = tmp_path / ".wfc" / "wfc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    from wfc.database import reset_engine

    reset_engine()

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        mod = Module(name="data_preprocessing", description="Preprocessing")
        session.add(mod)
        session.flush()
        meth = Method(
            name="m", module_id=mod.id,
            script_path="methods/m/m.py", env="container:demo",
        )
        session.add(meth)
        session.flush()
        session.add(
            MethodContract(
                method_id=meth.id,
                input_slots={},
                output_slots=output_slots,
                params_schema={},
            )
        )
        session.commit()
    return engine


def _enrich_single_node():
    """Build + enrich a one-node pipeline targeting the seeded method ``m``."""
    dist = Path(__file__).parent.parent / "wfc" / "canvas" / "static" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    from wfc.canvas.server import PipelineInput, PipelineNode, _enrich_pipeline

    pipeline = PipelineInput(
        name="t",
        nodes=[PipelineNode(id="n1", method="m", module="data_preprocessing", params={})],
        links=[],
        samples=["S1"],
    )
    return _enrich_pipeline(pipeline)


@workflow(purpose="Output slot type is the file extension, named verbatim with no silent .csv default")
def test_enrich_names_files_from_extension_verbatim(tmp_path, monkeypatch):
    """A contract declaring ``.h5ad`` + ``.parquet`` produces ``<slot>.h5ad`` /
    ``<slot>.parquet`` filenames — never the old silent ``.csv``."""
    _seed_engine(
        tmp_path, monkeypatch,
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


@workflow(purpose="Registration rejects an output slot type that is neither a dotted extension nor a directory marker")
@pytest.mark.parametrize("bad_type_block", [
    "outputs:\n  out:\n    type: anndata\n",   # stale semantic name
    "outputs:\n  out:\n    type: csv\n",       # bare, un-dotted
    "outputs:\n  out:\n    type: ''\n",        # empty
    "outputs:\n  out:\n    required: true\n",  # missing entirely
])
def test_parse_method_yaml_rejects_unusable_output_type(tmp_path, bad_type_block):
    """parse_method_yaml fails loud (ValueError) on an unusable output ``type``."""
    from wfc.contracts import parse_method_yaml

    method_dir = _write_method_yaml(tmp_path / "methods" / "bad", bad_type_block)
    with pytest.raises(ValueError, match=r"(?i)extension|dir"):
        parse_method_yaml(method_dir)


@workflow(purpose="A valid dotted-extension output slot passes registration")
def test_parse_method_yaml_accepts_dotted_extension(tmp_path):
    """A dotted extension is a valid output ``type`` and parses cleanly."""
    from wfc.contracts import parse_method_yaml

    method_dir = _write_method_yaml(
        tmp_path / "methods" / "ok",
        "outputs:\n  out:\n    type: .csv\n",
    )
    contract = parse_method_yaml(method_dir)
    assert contract["outputs"]["out"]["type"] == ".csv"


@workflow(purpose="A persisted contract with an invalid output type still raises at enrich (backstop)")
def test_enrich_backstop_raises_on_invalid_persisted_type(tmp_path, monkeypatch):
    """A DB contract whose output ``type`` predates validation raises at enrich."""
    _seed_engine(tmp_path, monkeypatch, output_slots={"out": {"type": "anndata"}})
    with pytest.raises(ValueError, match=r"(?i)extension|dir"):
        _enrich_single_node()


@workflow(purpose="Both 'dir' and 'directory' resolve to a canonical directory slot with no extension")
def test_dir_and_directory_both_detected_as_directory(tmp_path, monkeypatch):
    """``type: dir`` and ``type: directory`` both yield extension-less filenames,
    canonical ``directory`` slot_types, and is_directory_slot True."""
    from wfc.node_outputs import is_directory_slot

    _seed_engine(
        tmp_path, monkeypatch,
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
