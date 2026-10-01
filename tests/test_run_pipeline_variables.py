"""``wfc run-pipeline`` substitutes a document's pipeline variables before
the load, so a ``{$var}``-bound parameter reaches its method as the literal.

Through ``wfc.execution.run_pipeline`` on a harness-built project whose document
carries a ``variables`` block; only the Snakemake spawn is stubbed. The
generated rules hand ``wfc run-step`` no ``--params`` -- it reads the node's
params from ``WFC_PIPELINE_JSON`` -- so the literal reaches the method through
two facts asserted here: the frozen ``pipeline.json`` carries the literal and
the Snakefile's ``PIPELINE_JSON`` points at that frozen file (the PARAMS table
the generator embeds carries the literal as well). The pre-substitution form
freezes beside it as ``pipeline.editable.json``, as the canvas path writes it.
An unknown variable name fails before anything is scheduled, naming it.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import fake_engine_process, stub_docker_image_inspect
from tests.harness import Scenario, build_project, node, selector, wire
from wfc import layout


def _bind(pipeline_json, ref: dict, variables: dict | None) -> None:
    """Rewrite the frozen-to-be document: ``clean.threshold`` bound to ``ref``."""
    doc = json.loads(pipeline_json.read_text(encoding="utf-8"))
    (clean,) = [n for n in doc["nodes"] if n["id"] == "clean"]
    clean["params"] = {"threshold": ref}
    if variables is not None:
        doc["variables"] = variables
    pipeline_json.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _node(document: dict, node_id: str) -> dict:
    (found,) = [n for n in document["nodes"] if n["id"] == node_id]
    return found


@workflow(
    purpose="wfc run-pipeline on a document with a variables block and a "
            "{$var}-bound parameter freezes the literal in pipeline.json, "
            "points the Snakefile's PIPELINE_JSON at that frozen file and "
            "embeds the literal in PARAMS, keeps the pre-substitution form as "
            "pipeline.editable.json, and refuses an unknown variable name "
            "before anything is scheduled (Tier 3)",
)
def test_run_pipeline_substitutes_variables_before_the_load(git_project, monkeypatch):
    from wfc.execution import run_pipeline

    Step(step_num=1, name="Build the project and bind a parameter to a variable",
         purpose="A selector-rooted one-node pipeline whose only param is a "
                 "{$var} ref resolved by the document's variables block")
    pid = "pipe-variables-1"
    project = build_project(
        Scenario(
            nodes=[selector(), node("clean", params={"threshold": 0.5},
                                    inputs=[wire("sel")])],
            samples=["S1"],
            pipeline_id=pid,
        ),
        root=git_project, monkeypatch=monkeypatch,
    )
    variables = {"cut": {"type": "number", "value": 0.9}}
    _bind(project.pipeline_json, {"$var": "cut"}, variables)

    Step(step_num=2, name="Run through run_pipeline with the Snakemake spawn stubbed",
         purpose="Substitution, the load, the freeze and generation all run "
                 "for real; only the snakemake process is a stub")
    # The scenario env's image is in the Docker daemon (run_pipeline's env
    # pre-flight probes it); the engine is stubbed, so no container runs.
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    with fake_engine_process():
        run_pipeline(
            pipeline_path=str(project.pipeline_json),
            project_root=str(project.root),
            wfc_root=str(project.root),
            pipeline_id=pid,
        )

    Step(step_num=3, name="The frozen document and the Snakefile carry the literal",
         purpose="pipeline.json holds 0.9 and no variables block; PIPELINE_JSON "
                 "names that file (what wfc run-step reads its params from); "
                 "PARAMS holds 0.9; pipeline.editable.json keeps the ref")
    run_dir = layout.pipeline_run_dir(project.root, pid)
    frozen_path = run_dir / "pipeline.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    assert _node(frozen, "clean")["params"] == {"threshold": 0.9}
    assert "variables" not in frozen

    snakefile = (run_dir / "Snakefile").read_text(encoding="utf-8")
    assert f'PIPELINE_JSON = r"{frozen_path.resolve()}"' in snakefile, (
        "run-step must read the frozen (substituted) document, not the caller's file"
    )
    params_section = snakefile.split("PARAMS = {")[1].split("\n}")[0]
    assert "0.9" in params_section
    assert "$var" not in params_section

    editable = json.loads((run_dir / "pipeline.editable.json").read_text(encoding="utf-8"))
    assert _node(editable, "clean")["params"] == {"threshold": {"$var": "cut"}}
    assert editable["variables"] == variables

    Step(step_num=4, name="An unknown variable name fails before scheduling, naming it",
         purpose="No pipeline directory, no Snakefile, no snakemake spawn")
    _bind(project.pipeline_json, {"$var": "nope"}, variables)
    with fake_engine_process() as spawn:
        with pytest.raises(ValueError, match="Unknown pipeline variable: 'nope'"):
            run_pipeline(
                pipeline_path=str(project.pipeline_json),
                project_root=str(project.root),
                wfc_root=str(project.root),
                pipeline_id="pipe-variables-2",
            )
    spawn.assert_not_called()
    assert not layout.pipeline_run_dir(project.root, "pipe-variables-2").exists()
