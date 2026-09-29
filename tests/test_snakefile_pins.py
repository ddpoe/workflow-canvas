"""Whole-file pins of the emitted Snakefile over three fixed pipeline shapes.

Each shape is a pipeline document loaded through the Graph unit on literal
values (an empty contract map; the referenced run's outputs handed in) and
emitted whole, with the sample hashes handed to the emitter as a literal
table. The text is compared byte for byte against a golden file
under ``tests/fixtures/snakefiles/``. The only run-specific text in the
emitted file is the resolved project root, which is replaced by a token
before the comparison; the pipeline id and the frozen document's path are
handed in as literals.

The three shapes -- a collapsed fan-in with a restore rule, a
``run_reference`` root, and a step reading its sample beside an upstream
method's output and a reference -- are the standing whole-file witnesses for
the wiring column of the emitter's input table, where fragment assertions are
weakest. The third pins the composed rule: the restore sentinel, the upstream
sentinel and the reference path declared together on one rule. A change to the
emitter that alters the emitted text shows up here as a diff against the
golden file. When a change to the text is intended, regenerate the golden
files with::

    WFC_UPDATE_GOLDEN=1 poetry run pytest tests/test_snakefile_pins.py

and commit the diff beside the change that caused it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.graph import load_pipeline
from wfc.orchestration import generate_snakefile

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "snakefiles"

#: Literal handed to the emitter so the golden text carries no tmp path.
PIPELINE_JSON_LITERAL = "<PIPELINE_JSON>"
#: Replaces the resolved project root in the emitted text.
PROJECT_ROOT_TOKEN = "<PROJECT_ROOT>"

TABLE_PATH = ".runs/run-r/table/table.parquet"
PLOT_PATH = ".runs/run-r/plot/plot.png"

#: The collapsed fan-in shape hands the emitter hashes for two of its three
#: samples, so its file carries the restore rule and the hashless sample
#: is absent from the table.
COLLAPSED_SAMPLE_HASHES = {"s1": "a" * 32, "s2": "b" * 32}

#: The three-input shape reads both of its samples, so each has a hash.
PER_SAMPLE_HASHES = {"s1": "c" * 32, "s2": "d" * 32}
MODEL_PATH = ".runs/run-m/model/model.pkl"


def _method(node_id: str, method: str, params: dict | None = None) -> dict:
    return {"id": node_id, "type": "method", "method": method, "module": "demo",
            "script": f"methods/{method}/{method}.py", "env": "container:demo",
            "params": params or {}}


def _selector(node_id: str, samples: list[str], fan_mode: str = "out") -> dict:
    return {"id": node_id, "type": "input_selector", "fan_mode": fan_mode,
            "samples": list(samples)}


def _collapsed_fan_in() -> dict:
    return {
        "name": "collapsed-fan-in",
        "nodes": [_selector("bundle", ["s1", "s2", "s3"], fan_mode="in"),
                  _method("merge", "merge"), _method("filter", "filter")],
        "links": [{"source": "bundle", "target": "merge",
                   "target_slot": "sources"},
                  {"source": "merge", "target": "filter",
                   "source_slot": "out", "target_slot": "data"}],
        "samples": ["s1", "s2", "s3"],
    }


def _reference_root() -> dict:
    return {
        "name": "reference-root",
        "nodes": [{"id": "ref", "type": "run_reference", "run_id": "run-r"},
                  _method("report", "report")],
        "links": [{"source": "ref", "target": "report",
                   "source_slot": "table", "target_slot": "baseline"}],
        "samples": [],
    }


def _reference_outputs() -> dict:
    return {"ref": {"output_paths": {"table": TABLE_PATH, "plot": PLOT_PATH},
                    "sample": "s1"}}


def _selector_beside_method_and_reference() -> dict:
    """A quantify step reading the raw sample, a mask and a model at once."""
    return {
        "name": "selector-beside-method-and-reference",
        "nodes": [_selector("images", ["s1", "s2"]),
                  _method("segment", "segment"), _method("quantify", "quantify"),
                  {"id": "model", "type": "run_reference", "run_id": "run-m"}],
        "links": [{"source": "images", "target": "segment",
                   "target_slot": "data"},
                  {"source": "images", "target": "quantify",
                   "target_slot": "raw"},
                  {"source": "segment", "target": "quantify",
                   "source_slot": "mask", "target_slot": "mask"},
                  {"source": "model", "target": "quantify",
                   "source_slot": "model", "target_slot": "model"}],
        "samples": ["s1", "s2"],
    }


def _model_outputs() -> dict:
    return {"model": {"output_paths": {"model": MODEL_PATH}, "sample": "s1"}}


#: shape -> (document builder, the referenced runs' outputs handed to the load)
SHAPES: dict[str, tuple] = {
    "collapsed-fan-in": (_collapsed_fan_in, {}),
    "reference-root": (_reference_root, _reference_outputs()),
    "selector-beside-method-and-reference": (
        _selector_beside_method_and_reference, _model_outputs()),
}

#: shape -> the sample hashes handed to the emitter.
HASHES: dict[str, dict] = {
    "collapsed-fan-in": COLLAPSED_SAMPLE_HASHES,
    "reference-root": {},
    "selector-beside-method-and-reference": PER_SAMPLE_HASHES,
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
@workflow(
    purpose="The emitted Snakefile for a fixed pipeline shape (a collapsed "
            "fan-in with a restore rule, a run_reference root, a step reading "
            "its sample beside an upstream output and a reference) is "
            "byte-identical "
            "to its golden file once the resolved project root is replaced by a "
            "token; the standing whole-file witness for the emitter's wiring "
            "column (Tier 2)",
)
def test_emitted_snakefile_matches_its_golden_file(shape, wfc_root):
    build, reference_outputs = SHAPES[shape]
    sample_hashes = HASHES[shape]

    pipeline = load_pipeline(build(), contract_map={},
                             reference_outputs=reference_outputs)
    content = generate_snakefile(
        pipeline, project_root=wfc_root,
        pipeline_id=f"pin-{shape}", pipeline_json_path=PIPELINE_JSON_LITERAL,
        sample_hashes=sample_hashes,
    )
    normalised = content.replace(str(Path(wfc_root).resolve()), PROJECT_ROOT_TOKEN)

    golden = GOLDEN_DIR / f"{shape}.Snakefile"
    if os.environ.get("WFC_UPDATE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(normalised, encoding="utf-8", newline="\n")
    assert golden.exists(), (
        f"no golden file at {golden}; regenerate with WFC_UPDATE_GOLDEN=1"
    )
    assert normalised == golden.read_text(encoding="utf-8")
