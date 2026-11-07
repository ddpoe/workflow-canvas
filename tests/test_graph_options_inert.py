"""Options are inert to the load and the run (Graph unit, catalog case 9).

An output's ``options`` block and a parameter's ``options_from`` are what the
canvas's picker reads; the engine never consults them. A pipeline whose
methods declare both loads to the same ``PipelineDef`` as the same pipeline
without them, and runs to the same results. Stub rung through the scenario
runner, two projects built from the two declarations.
"""
from __future__ import annotations

from dataclasses import replace

from axiom_annotations import Step, workflow

from tests.harness import (
    DEFAULT_MODULE,
    Scenario,
    node,
    run_scenario,
    selector,
    wire,
)
from wfc.execution import load_pipeline_from_path
from wfc.registration import method_contract

#: The upstream's ``table`` output offers fixed names, one built from its
#: ``label_column`` parameter, and a pattern.
OPTIONS = {"fixed": ["id", "intensity"],
           "from_params": [{"params": ["label_column"], "pattern": "{}"}],
           "patterns": ["*_mean"]}


def _scenario(with_options: bool, pipeline_id: str) -> Scenario:
    """The two-step pipeline, with or without the options declarations."""
    label = node("label", inputs=[wire("sel")], outputs={"table": ".csv"},
                 params={"label_column": "label"})
    summarize = node("summarize",
                     inputs=[wire("label", source_slot="table",
                                  target_slot="table")],
                     params={"group_by": "label"})
    if with_options:
        label = replace(label, output_options={"table": OPTIONS})
        summarize = replace(summarize, param_options_from={"group_by": "table"})
    return Scenario(nodes=[selector(), label, summarize], samples=["s1", "s2"],
                    pipeline_id=pipeline_id, name=pipeline_id)


def _results(obs) -> dict:
    """What a run produced, with the per-project ids and paths left out."""
    runs = sorted(
        (r["node_id"], r["status"], r["params"]) for r in obs.run_rows
    )
    outputs = sorted(
        (r["output_name"], r["content_hash"]) for r in obs.output_rows
    )
    targets = sorted((t, run.rc, run.ending) for t, run in obs.runs.items())
    return {"runs": runs, "outputs": outputs, "targets": targets}


@workflow(purpose="A pipeline whose methods declare an output's options and a "
                  "parameter's options_from loads to the same PipelineDef and "
                  "runs to the same results as the same pipeline without them",
          inputs="Two projects from one two-step pipeline: label (an options "
                 "block on its table output) feeding summarize (group_by "
                 "options_from table), and the same pipeline with neither",
          outputs="Equal PipelineDefs and equal run, output and target results",
          critical="Stub rung: the method processes are the harness's local "
                   "stand-ins")
def test_options_declarations_change_neither_the_load_nor_the_run(
        git_project, tmp_path_factory, monkeypatch):
    口 = Step(step_num=1, name="Run the pipeline without options",
             purpose="The baseline: the definition the load gives and the "
                     "results the run records when no method declares options",
             outputs="The plain definition and results")
    plain = run_scenario(_scenario(False, "plain"), root=git_project,
                         monkeypatch=monkeypatch)
    plain_def = load_pipeline_from_path(plain.project.pipeline_json)

    口 = Step(step_num=2, name="Run the same pipeline with options declared",
             purpose="label's table output declares options and summarize's "
                     "group_by declares options_from table; the document "
                     "and the params are unchanged",
             outputs="The definition and results with the declarations, "
                     "and the registered contracts carrying both")
    declared = run_scenario(_scenario(True, "plain"),
                            root=tmp_path_factory.mktemp("with_options"),
                            monkeypatch=monkeypatch)
    declared_def = load_pipeline_from_path(declared.project.pipeline_json)
    assert method_contract(DEFAULT_MODULE, "label").output_slots["table"][
        "options"] == OPTIONS
    assert method_contract(DEFAULT_MODULE, "summarize").params_schema[
        "group_by"]["options_from"] == "table"

    口 = Step(step_num=3, name="The load and the run are the same",
             purpose="The declarations reach neither the PipelineDef nor "
                     "anything the run records",
             outputs="Equal definitions and results")
    assert declared_def == plain_def
    assert _results(declared) == _results(plain)
    assert [r["status"] for r in declared.run_rows] == ["completed"] * 4
