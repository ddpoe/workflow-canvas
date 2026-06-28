"""Focused test for ADR-010 required-artifact #4.

``_enrich_pipeline`` must emit a parallel ``slot_types`` field on every
contract-backed node so that both ``snakemake_gen`` and ``run_step`` can
consult contract-declared types as the single source of truth (rather
than falling back to filename-shape heuristics for directory detection).
"""

from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from wfc.models import Method, MethodContract, Module


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """In-memory SQLite with wfc schema + a seeded module.

    Duplicates the minimal setup from ``tests/test_canvas_run.py`` so this
    module does not transitively import the (currently collection-broken)
    ``test_canvas_run`` top-level imports.
    """
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
            name="tile_and_config",
            module_id=mod.id,
            script_path="methods/tile_and_config/tile_and_config.py",
            env="container:demo",
        )
        session.add(meth)
        session.flush()

        session.add(
            MethodContract(
                method_id=meth.id,
                input_slots={},
                output_slots={
                    "tiles_dir": {"type": "directory"},
                    "config": {"type": ".json"},
                },
                params_schema={},
            )
        )
        session.commit()

    yield engine


def test_enrich_pipeline_emits_slot_types_for_mixed_node(db_engine):
    """A node whose method contract declares a directory slot and a JSON
    slot produces a pipeline-JSON dict where the node carries a
    ``slot_types`` mapping with contract-declared type strings.

    This is ADR-010 required-artifact #4 — the contract between
    ``_enrich_pipeline`` and the rule-generator / run-step is that
    ``slot_types`` accompanies ``slot_outputs`` one-to-one so directory
    slots can be detected without filename-shape heuristics.
    """
    # Ensure the canvas module can import even when the built SPA dist
    # directory is absent in a fresh worktree.
    dist = Path(__file__).parent.parent / "wfc" / "canvas" / "static" / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    from wfc.canvas.server import (  # noqa: E402 — needs dist dir first
        PipelineInput,
        PipelineNode,
        _enrich_pipeline,
    )

    pipeline = PipelineInput(
        name="t",
        nodes=[
            PipelineNode(
                id="n1",
                method="tile_and_config",
                module="data_preprocessing",
                params={},
            )
        ],
        links=[],
        samples=["S1"],
    )

    enriched = _enrich_pipeline(pipeline)

    assert "nodes" in enriched and len(enriched["nodes"]) == 1
    node = enriched["nodes"][0]
    assert "slot_types" in node, (
        f"_enrich_pipeline must emit slot_types; got keys={list(node.keys())}"
    )
    assert node["slot_types"] == {
        "tiles_dir": "directory",
        "config": ".json",
    }, (
        f"slot_types must mirror contract-declared types exactly; "
        f"got {node['slot_types']!r}"
    )
    # slot_outputs remains alongside slot_types (ADR-010 adds, doesn't replace).
    assert node["slot_outputs"] == {
        "tiles_dir": "tiles_dir",
        "config": "config.json",
    }
