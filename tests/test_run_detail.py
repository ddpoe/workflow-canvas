"""Run detail: one run's status, metrics, outputs, error, logs and stderr tail.

Execution's ``run_detail`` is what ``wfc show-run`` prints (catalog case 12,
its Execution half): a recorded run is read from its row and its run
archive's logs, a draft string through the draft-run store, in one shape.

Declared fixture deviations: none. Projects and runs are built by the
harness through production registration and production ``run_step``; the
method process is the stub rung's.
"""
from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.harness import Scenario, exits, node, run_scenario, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.execution import RunDetailError, run_detail
from wfc.execution.draft import spell_draft_ref

_LINES = [f"stderr line {i}" for i in range(60)]


def _images(monkeypatch) -> None:
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)


def _failing(**kwargs) -> Scenario:
    return Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)],
                    behavior=exits(1, stderr="\n".join(_LINES) + "\n"))],
        samples=["s1"], **kwargs)


@workflow(purpose="Case 12, Execution half: a recorded run whose method failed "
                  "reads back as failed with its error, the paths of its two "
                  "logs in its run archive and the last 40 lines of its stderr; "
                  "the detail is plain JSON")
def test_a_failed_run_reads_back_with_its_error_and_its_stderr_tail(tmp_project,
                                                                    monkeypatch):
    _images(monkeypatch)

    口 = Step(step_num=1, name="Run a method that fails, writing 60 stderr lines",
             purpose="A recorded failed run with a stderr log")
    obs = run_scenario(_failing(), root=tmp_project, monkeypatch=monkeypatch)
    run_id = obs.runs[("a", "s1", "default")].run_id

    口 = Step(step_num=2, name="Read its detail",
             purpose="Status, error, log paths and the stderr tail from the row "
                     "and the run archive")
    detail = run_detail(run_id, tmp_project)
    assert (detail.run, detail.draft, detail.status) == (str(run_id), False, "failed")
    assert detail.error
    assert detail.sample == "s1" and detail.node_id == "a"
    assert detail.stderr_log == layout.run_log_path(tmp_project, run_id, "stderr")
    assert detail.stdout_log == layout.run_archive_dir(tmp_project, run_id) / "stdout.log"
    assert detail.stderr_tail == tuple(_LINES[-40:])

    口 = Step(step_num=3, name="The detail is plain JSON",
             purpose="What --json prints round-trips")
    assert json.loads(json.dumps(detail.as_dict()))["stderr_tail"] == _LINES[-40:]


@workflow(purpose="Case 12, Execution half: a draft string resolves through the "
                  "draft-run store to the same shape: the draft step's status, "
                  "error, its logs in its results folder and its stderr tail")
def test_a_failed_draft_step_reads_back_by_its_draft_string(tmp_project, monkeypatch):
    _images(monkeypatch)

    口 = Step(step_num=1, name="Run the method as a draft that fails",
             purpose="A draft step with an error and a stderr log, no run row")
    scenario = _failing(pipeline_id="pipe-d", name="pipe_d", drafts=("a",))
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    spec = scenario.node_by_id("a")
    ref = spell_draft_ref(spec.module, spec.method_name, "a", "s1")

    口 = Step(step_num=2, name="Read it by its draft string",
             purpose="The store's entry and the results folder's logs")
    detail = run_detail(ref, tmp_project)
    folder = layout.draft_result_dir(tmp_project, spec.module, spec.method_name, "a", "s1")
    assert (detail.run, detail.draft, detail.status) == (ref, True, "failed")
    assert detail.error and detail.traceback is None
    assert detail.stderr_log == folder / "stderr.log"
    assert detail.stderr_tail[-1] == _LINES[-1] and len(detail.stderr_tail) == 40


def test_an_id_or_string_naming_no_run_is_refused(tmp_project):
    """An unknown id, a non-number and a draft string with no draft each say why."""
    with pytest.raises(RunDetailError, match="Run 999 not found"):
        run_detail(999, tmp_project)
    with pytest.raises(RunDetailError, match="not a run id or a draft string"):
        run_detail("abc", tmp_project)
    with pytest.raises(RunDetailError, match="has no draft"):
        run_detail("draft:m.x/x@s1", tmp_project)
