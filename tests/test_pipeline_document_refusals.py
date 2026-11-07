"""A pipeline document the preparation or the load refuses is one typed refusal.

Witness for catalog case 7 at the Execution level: every way a pipeline
document can fail to load -- a file that is not there, a file that is not
JSON, a method that is not registered, a Graph refusal, a ``param_sets``
that is not a mapping, an undeclared variable, an unknown key -- raises
``PipelineDocumentError`` from ``run_pipeline``, naming the node and the
field where it has one, before anything is launched; no other exception type
escapes. The check-pipeline entry refuses each of them too, and refuses a
document the verdict passes but the load does not, in the load's words.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.pipeline_verbs import fit_document, refusal_kinds, write_document
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.scenario import SELECTOR_ID


@workflow(purpose="Catalog case 7 at the Execution level: for each loader "
                  "refusal kind (missing file, bad JSON, a non-object, an "
                  "unregistered method, a Graph refusal, a non-mapping "
                  "param_sets, an undeclared variable, an unknown key) "
                  "run_pipeline raises PipelineDocumentError naming the node "
                  "and the field, nothing else escapes and nothing is "
                  "launched; check-pipeline refuses the same document, the "
                  "file kinds with the same refusal")
def test_every_loader_refusal_is_a_pipeline_document_error(
        tmp_project, monkeypatch, tmp_path):
    from wfc.execution.pipeline import run_pipeline
    from wfc.execution.validation import PipelineDocumentError, check_pipeline

    from tests.fixtures.fakes.engine import fake_engine_process

    口 = Step(step_num=1, name="Register fit",
             purpose="Production registration through the harness")
    scenario = Scenario(nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)])],
                        samples=["S1"], pipeline_id="refusals", name="refusals")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    launched: list = []

    for kind, (content, words) in refusal_kinds().items():
        path = write_document(tmp_path, kind, content)
        口 = Step(step_num=1.1, name="Run the kind",
                 purpose="run_pipeline raises PipelineDocumentError with the "
                         "kind's words; the engine is not launched")
        with fake_engine_process(only_snakemake=True,
                                 launch=lambda argv, **_: launched.append(argv)):
            with pytest.raises(PipelineDocumentError) as refused:
                run_pipeline(path, project_root=str(project.root))
        message = str(refused.value)
        assert all(w in message for w in words), (kind, message)
        assert launched == [], kind

        口 = Step(step_num=1.2, name="Check the kind",
                 purpose="check-pipeline raises the same refusal for a file "
                         "it cannot read, else answers not valid")
        if not isinstance(content, dict):
            with pytest.raises(PipelineDocumentError) as checked:
                check_pipeline(path, project.root)
            assert str(checked.value) == message, kind
        else:
            verdict = check_pipeline(path, project.root)
            assert verdict["valid"] is False and verdict["errors"], (kind, verdict)


@workflow(purpose="The check-pipeline entry adds the load's legality stage to "
                  "the verdict: a document the verdict passes and the load "
                  "refuses (a selector that selects no sample) is not "
                  "valid, its error the load's own words; a document both "
                  "pass is valid with no errors")
def test_check_pipeline_adds_the_loads_refusal_to_the_verdict(
        tmp_project, monkeypatch, tmp_path):
    from wfc.execution.prepare import prepare_document
    from wfc.execution.composer import load_pipeline_from_document
    from wfc.execution.validation import (
        PipelineDocumentError,
        check_document,
        check_pipeline,
        document_verdict,
    )

    口 = Step(step_num=1, name="Register fit",
             purpose="Production registration through the harness")
    scenario = Scenario(nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)])],
                        samples=["S1"], pipeline_id="check", name="check")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="A document both stages pass",
             purpose="Valid, no errors, and the verdict's own shape")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(fit_document()), encoding="utf-8")
    verdict = check_pipeline(str(good), project.root)
    assert verdict["valid"] is True and verdict["errors"] == []
    assert set(verdict) >= {"valid", "errors", "warnings", "draftRefusals"}

    口 = Step(step_num=3, name="A document only the load refuses",
             purpose="A selector that selects no sample: the verdict passes "
                     "it; the check answers not valid with the load's refusal "
                     "as its one error")
    dangling = fit_document(samples=[])
    dangling["nodes"][0]["samples"] = []
    assert document_verdict(dangling, project.root)["valid"] is True
    with pytest.raises(PipelineDocumentError) as loaded:
        load_pipeline_from_document(prepare_document(dangling))
    checked = check_document(dangling, project.root)
    assert checked["valid"] is False
    assert checked["errors"] == [str(loaded.value)]
