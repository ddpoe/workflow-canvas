"""The shared shape corpus, pytest half (Graph unit, testing requirement
"Agreement: the shared shape corpus").

Two families of literal shape fixtures live under ``tests/shapes/graph/``
and are read by this module and by the Vitest half
(``wfc/canvas/static/src/lib/graph/__tests__/graph.corpus.test.ts``). Each
entry
carries the sparse canvas document (what ``exportPipeline()`` emits), a
literal contract map, literal reference outputs where the shape needs them,
and per-side verdicts. Neither side derives its own expectation.

Entry schema (``legality/<id>.json``)::

    {"id", "case", "note", "document", "contract_map", "reference_outputs",
     "package": {"validation": "accept"|"refuse"|null,
                 "load": "accept"|"refuse"|null},
     "client": {"answers": true, "candidate": <link>, "verdict": ...}
             | {"answers": false, "reason": ...}}

Entry schema (``projection/<id>.json``)::

    {"id", "case", "note", "document", "contract_map", "reference_outputs",
     "expect": {"collapsed": [node ids], "bundle": [samples],
                "rows": [{"node", "sample", "variant", "reused"}, ...]},
     "client": {"answers": true[, "divergesUntilCommitB": true]}
             | {"answers": false, "reason": ...}}

A ``null`` moment on the package side is not asserted. The corpus grows by
entries, not tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.contracts import enrich_pipeline
from wfc.graph import (
    expand_step_combos,
    load_pipeline,
    mark_reused,
    resolve_variant_model,
    validate_structure,
)

CORPUS = Path(__file__).parent / "shapes" / "graph"


def _entries(family: str) -> list[Path]:
    return sorted((CORPUS / family).glob("*.json"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Package calls: the structural core on the sparse document; the load on the
# enriched document (Contracts' enrichment is the one owner of the sparse ->
# canonical translation, exactly as production does).
# ---------------------------------------------------------------------------

def _load(document: dict, contract_map: dict, reference_outputs: dict):
    """Enrich the sparse document (the production translation) and load it."""
    canonical = enrich_pipeline(document, contract_map)
    return load_pipeline(canonical, contract_map=contract_map,
                         reference_outputs=reference_outputs)


def _rows(pipeline) -> tuple[list[str], list[str], list[dict]]:
    """Collapsed ids, the bundle and the marked per-step row list for a definition."""
    model = resolve_variant_model(pipeline)
    scheduled = expand_step_combos(
        pipeline.steps, pipeline.samples, model.tables, pipeline.explicit_combos
    )
    marks = mark_reused(pipeline.steps, scheduled, model.tables)
    rows = [
        {"node": step.node_id, "sample": combo["sample"],
         "variant": combo["variant"], "reused": reused}
        for (step, combo), reused in zip(scheduled, marks)
    ]
    collapsed = [s.node_id for s in pipeline.steps if s.sample_collapsed]
    bundle = next(
        (list(s.collapsed_samples) for s in pipeline.steps if s.sample_collapsed), []
    )
    return collapsed, bundle, rows


# ---------------------------------------------------------------------------
# Legality family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry_path", _entries("legality"), ids=lambda p: p.stem)
@workflow(purpose="Every legality corpus entry gets the recorded verdict from "
                  "the package: structural validation on the sparse document "
                  "and the literal contract map, and the load on the enriched "
                  "document, each moment asserted where the entry records one")
def test_corpus_legality(entry_path):
    entry = _read(entry_path)
    package = entry["package"]

    if package.get("validation") is not None:
        result = validate_structure(entry["document"], entry["contract_map"])
        expected_valid = package["validation"] == "accept"
        assert result["valid"] is expected_valid, (
            f"{entry['id']}: validation {result['errors']}"
        )

    if package.get("load") is not None:
        if package["load"] == "accept":
            _load(entry["document"], entry["contract_map"], entry["reference_outputs"])
        else:
            with pytest.raises(ValueError):
                _load(entry["document"], entry["contract_map"],
                      entry["reference_outputs"])


# ---------------------------------------------------------------------------
# Projection family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry_path", _entries("projection"), ids=lambda p: p.stem)
@workflow(purpose="Every projection corpus entry gets the recorded collapsed "
                  "ids, bundle and per-step row list from the package: the "
                  "sparse document through Contracts' enrichment, the load, "
                  "the variant derivation, per-step expansion and the reuse mark")
def test_corpus_projection(entry_path):
    entry = _read(entry_path)
    pipeline = _load(entry["document"], entry["contract_map"],
                     entry["reference_outputs"])
    collapsed, bundle, rows = _rows(pipeline)

    expect = entry["expect"]
    assert sorted(collapsed) == sorted(expect["collapsed"])
    assert bundle == expect["bundle"]
    expected_rows = [
        {"node": r["node"], "sample": r["sample"], "variant": r["variant"],
         "reused": r["reused"]}
        for r in expect["rows"]
    ]
    assert rows == expected_rows
