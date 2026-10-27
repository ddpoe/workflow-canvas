"""Contracts unit: pipeline enrichment attaches each module's own contract.

Enrichment turns the sparse document the canvas sends — node identities,
methods, modules, parameters — into the canonical document the engine
loads, by looking each method node up in a contract map keyed on module and
method together. The fixture stands up two modules declaring the same
method name, so a node identified by its name alone rather than by its
module and name together shows as the wrong module's contract; several
places outside this unit still identify a method by name alone.

The fixture is built through real registration so the contract rows are
what production writes, not hand-built dicts. Two same-named methods in two
modules enrich to two nodes whose slot maps, slot types and env are each
their own module's.

The node carrying no module at all is deliberately not asserted here: it
resolves nothing and is emitted unenriched, and pinning a silent miss would
lock the defect rather than the behavior.

Witness: ``docs/system/contracts.json``, section
``catalog.enrichment-module-qualified``.
"""
from __future__ import annotations

from pathlib import Path

from axiom_annotations import workflow

from tests.fixtures.conftest import write_env_record
from wfc.init import init_project
from wfc.registration import register_method, register_module

METHOD = "shared_method"


def _author_method(root: Path, module: str, *, env: str, outputs: dict) -> Path:
    """Write one method's source and contract under an authoring dir.

    Each module's source has its own authoring directory; registration
    snapshots each into its module's own registered location. The script saves every declared output
    slot, which registration's AST scan requires of a required output.
    """
    method_dir = root / "authoring" / module / METHOD
    method_dir.mkdir(parents=True)
    (method_dir / "method.yaml").write_text(
        f"env: {env}\n"
        "inputs:\n  data:\n    type: .csv\n"
        "outputs:\n"
        + "".join(f"  {slot}:\n    type: {slot_type}\n" for slot, slot_type in outputs.items())
    )
    (method_dir / f"{METHOD}.py").write_text(
        "import wfc_client as wfc\n\n"
        "@wfc.method\n"
        f"def {METHOD}(ctx):\n"
        + "".join(f"    ctx.save_artifact('{slot}', ctx.run_dir / '{slot}')\n"
                  for slot in outputs)
        + "\n"
        "if __name__ == '__main__':\n"
        "    wfc.run()\n"
    )
    return method_dir


@workflow(purpose="Two modules declaring the same method name enrich to two "
                  "nodes that each carry their own module's contract — slot "
                  "filenames, canonical slot types and env resolved on module "
                  "and method together, never on the method name alone",
          inputs="A project with shared_method registered under two modules "
                 "with different output slots and different envs, and a "
                 "document wiring one node from each",
          outputs="Two enriched nodes whose slot maps differ and whose env is "
                  "each module's own")
def test_enrichment_attaches_each_modules_own_contract(tmp_project, monkeypatch):
    """The catalog's own Test line: a two-module document whose same-named
    methods declare different output slots emits two nodes whose slot maps
    do not match each other."""
    from wfc.canvas.models import PipelineInput, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline

    init_project(tmp_project)
    write_env_record(tmp_project, "env-a", digest="a" * 64)
    write_env_record(tmp_project, "env-b", digest="b" * 64)

    dir_a = _author_method(tmp_project, "mod_a", env="env-a",
                           outputs={"table": "csv"})
    dir_b = _author_method(tmp_project, "mod_b", env="env-b",
                           outputs={"matrix": ".h5ad", "figures": "dir"})
    register_module(name="mod_a", contracts=[], description="first owner")
    register_method(method_dir=dir_a, module_name="mod_a", assume_yes=True)
    register_module(name="mod_b", contracts=[], description="second owner")
    register_method(method_dir=dir_b, module_name="mod_b", assume_yes=True)

    document = PipelineInput(nodes=[
        PipelineNode(id="a1", type="method", method=METHOD, module="mod_a"),
        PipelineNode(id="b1", type="method", method=METHOD, module="mod_b"),
    ], links=[])
    enriched = {node["id"]: node for node in _enrich_pipeline(document)["nodes"]}

    assert enriched["a1"]["slot_outputs"] == {"table": "table.csv"}
    assert enriched["a1"]["slot_types"] == {"table": ".csv"}
    assert enriched["a1"]["env"] == "env-a"

    assert enriched["b1"]["slot_outputs"] == {"matrix": "matrix.h5ad", "figures": "figures"}
    assert enriched["b1"]["slot_types"] == {"matrix": ".h5ad", "figures": "directory"}
    assert enriched["b1"]["env"] == "env-b"

    assert enriched["a1"]["slot_outputs"] != enriched["b1"]["slot_outputs"]
