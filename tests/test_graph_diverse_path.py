"""The diverse end-to-end path through generation (Graph unit).

One document that carries every shape the Graph unit derives a schedule
from at once -- both system-node kinds as roots (a per-sample selector and
a run reference), a fan-in branch beside the per-sample branch, a reference
wired by source slot into a multi-output run, a variant table on one node
only, and a bound pipeline variable -- goes through substitution, the pure
load and the Snakefile emitter, and the emitted targets and PARAMS are
compared against literals.
"""

from __future__ import annotations

import os

from axiom_annotations import Step, workflow

from wfc import layout
from wfc.contracts import COLLAPSED_SAMPLE
from wfc.graph import load_pipeline, resolve_variables
from wfc.orchestration import generate_snakefile

PID = "pipe-diverse"
TABLE_PATH = ".runs/run-r/table/table.parquet"
PLOT_PATH = ".runs/run-r/plot/plot.png"


def _method(node_id: str, method: str, params: dict) -> dict:
    return {"id": node_id, "type": "method", "method": method, "module": "demo",
            "script": f"methods/{method}/{method}.py", "env": "container:demo",
            "params": params}


def _document() -> dict:
    return {
        "name": "diverse",
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["s1", "s2"]},
            {"id": "ref", "type": "run_reference", "run_id": "run-r"},
            {"id": "bundle", "type": "input_selector", "fan_mode": "in",
             "samples": ["s1", "s2"]},
            _method("qc", "qc", {"threshold": 0.7}),
            _method("report", "report", {}),
            _method("merge", "merge", {"cutoff": {"$var": "cut"}}),
            _method("summary", "summary", {}),
        ],
        "links": [
            {"source": "sel", "target": "qc", "target_slot": "data"},
            {"source": "qc", "target": "report",
             "source_slot": "out", "target_slot": "data"},
            {"source": "ref", "target": "report",
             "source_slot": "table", "target_slot": "baseline"},
            {"source": "bundle", "target": "merge", "target_slot": "sources"},
            {"source": "merge", "target": "summary",
             "source_slot": "out", "target_slot": "data"},
        ],
        "samples": ["s1", "s2"],
        "param_sets": {
            "qc": {"loose": {"threshold": 0.5}, "strict": {"threshold": 0.9}},
        },
        "variables": {"cut": {"type": "number", "value": 0.25}},
    }


@workflow(
    purpose="A document with a selector root, a reference root, a fan-in "
            "branch beside the per-sample branch, a source-slot pick from a "
            "multi-output run, one variant table and one bound variable goes "
            "through substitution, the load and the emitter; the Snakefile's "
            "targets and PARAMS match literals (Tier 3)",
)
def test_diverse_document_generates_the_literal_schedule(wfc_root, monkeypatch):
    口 = Step(step_num=1, name="Substitute the bound variable first",
              purpose="The one variable reaches the method as a literal before "
                      "the load sees the document")
    document = resolve_variables(_document())
    merge_node = next(n for n in document["nodes"] if n["id"] == "merge")
    assert merge_node["params"] == {"cutoff": 0.25}

    口 = Step(step_num=2, name="Load on literal values",
              purpose="Both roots bind: the selector gives the sample list, the "
                      "reference gives one artifact under the picked slot; the "
                      "fan-in chain collapses, the per-sample chain does not")
    pipeline = load_pipeline(
        document, contract_map={},
        reference_outputs={"ref": {"output_paths": {"table": TABLE_PATH,
                                                    "plot": PLOT_PATH},
                                   "sample": "s1"}},
    )
    steps = {s.node_id: s for s in pipeline.steps}
    assert pipeline.samples == ["s1", "s2"]
    assert steps["report"].run_ref_inputs == {"baseline": [TABLE_PATH]}
    assert steps["report"].depends_on == ["qc"]
    assert [s.node_id for s in pipeline.steps if s.sample_collapsed] == ["merge", "summary"]

    口 = Step(step_num=3, name="Generate the Snakefile",
              purpose="The emitter takes the loaded definition and the pipeline id")
    snakefile = generate_snakefile(pipeline, wfc_root, pipeline_id=PID)

    口 = Step(step_num=4, name="Targets: each leaf on its own axis",
              purpose="report expands over samples x variants; summary once per "
                      "variant at the collapsed sample")
    rule_all = snakefile.split("rule all:")[1].split("\nrule ")[0]
    report_target = layout.run_sentinel_relpath(PID, "report", "{sample}", "{variant}")
    summary_target = layout.run_sentinel_relpath(PID, "summary", COLLAPSED_SAMPLE, "{variant}")
    assert f'expand("{report_target}", sample=SAMPLES, variant=VARIANT_NAMES)' in rule_all, rule_all
    assert f'expand("{summary_target}", variant=VARIANT_NAMES)' in rule_all, rule_all

    口 = Step(step_num=5, name="PARAMS: one table swept, every node padded",
              purpose="The axis is default + the swept node's names; the swept "
                      "node's default row is its box params; every unswept node "
                      "carries its base params under every name, the bound "
                      "variable already a literal")
    # The Python section writes WFC_PROJECT_ROOT, WFC_PIPELINE_JSON and
    # WFC_PIPELINE_ID into os.environ for the Snakemake process it is meant
    # to run in; executed here, those writes land in the test process and
    # would outlive the test. Pinning each key first records its current
    # value (or absence) so monkeypatch undoes the preamble's writes at
    # teardown.
    for key in ("WFC_PROJECT_ROOT", "WFC_PIPELINE_JSON", "WFC_PIPELINE_ID"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    namespace: dict = {}
    exec(snakefile.split("rule all:")[0], namespace)
    assert namespace["SAMPLES"] == ["s1", "s2"]
    assert namespace["VARIANT_NAMES"] == ["default", "loose", "strict"]
    assert namespace["PARAMS"] == {
        "qc": {"default": {"threshold": 0.7}, "loose": {"threshold": 0.5},
               "strict": {"threshold": 0.9}},
        "report": {"default": {}, "loose": {}, "strict": {}},
        "merge": {"default": {"cutoff": 0.25}, "loose": {"cutoff": 0.25},
                  "strict": {"cutoff": 0.25}},
        "summary": {"default": {}, "loose": {}, "strict": {}},
    }
