"""The CLI's reading verbs: ``show-run`` and every ``--json`` verb.

Catalog case 12 (CLI half): ``wfc show-run`` on a failed recorded run and on
a draft string prints its status, error and stderr tail; ``--log`` prints a
whole log. Catalog case 13: every ``--json`` verb prints one JSON object
with ``ok`` on stdout and nothing else there, and exits as its plain form
does, on success and on a failure.

Declared fixture deviations: none. Runs are made by the harness through
production registration and ``run_step`` (the stub rung's method process)
or by the canvas route (``completed_run``), and archived by Storage's
archive pass.
"""
from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import stub_readiness_probes
from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.fixtures.routes import completed_run
from tests.harness import Scenario, exits, node, run_scenario, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID, Behavior
from wfc.execution import RunDetailError, run_detail, run_log
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


@workflow(purpose="Case 12, CLI half: wfc show-run on a failed recorded run prints "
                  "its status, its error and the last 40 lines of its stderr; "
                  "--log stderr prints the whole log; --json is the detail with ok")
def test_show_run_prints_a_failed_runs_error_and_its_stderr_tail(cli, tmp_project,
                                                                 monkeypatch):
    _images(monkeypatch)

    口 = Step(step_num=1, name="Run a method that fails, writing 60 stderr lines",
             purpose="A recorded failed run with a stderr log")
    obs = run_scenario(_failing(), root=tmp_project, monkeypatch=monkeypatch)
    run_id = obs.runs[("a", "s1", "default")].run_id

    口 = Step(step_num=2, name="show-run prints the run",
             purpose="Status, error and the stderr tail, exit 0")
    result = cli("show-run", "--run", str(run_id))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"Run {run_id}: failed"
    assert any(line.startswith("Error: ") for line in lines)
    assert "Last 40 lines of stderr:" in lines
    assert lines[-1] == f"  {_LINES[-1]}"
    assert f"  {_LINES[19]}" not in lines and f"  {_LINES[20]}" in lines

    口 = Step(step_num=3, name="--log stderr prints the whole log",
             purpose="All 60 lines, nothing else")
    result = cli("show-run", "--run", str(run_id), "--log", "stderr")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == _LINES
    assert run_log(run_id, "stderr", tmp_project).splitlines() == _LINES

    口 = Step(step_num=4, name="--json is the detail as one object",
             purpose="ok, the status, the error and the tail")
    body = json.loads(cli("show-run", "--run", str(run_id), "--json").stdout)
    assert (body["ok"], body["status"], body["run"]) == (True, "failed", str(run_id))
    assert body["error"] and body["stderr_tail"] == _LINES[-40:]

    口 = Step(step_num=5, name="--log names a log that is not there",
             purpose="Execution refuses with the log's path; exit 1, nothing on stdout")
    stderr_log = run_detail(run_id, tmp_project).stderr_log
    stderr_log.unlink()
    with pytest.raises(RunDetailError, match="has no stderr log at"):
        run_log(run_id, "stderr", tmp_project)
    result = cli("show-run", "--run", str(run_id), "--log", "stderr")
    assert result.returncode == 1 and result.stdout == ""
    assert f"has no stderr log at {stderr_log}" in result.stderr


@workflow(purpose="Case 12, CLI half: wfc show-run on a failed draft step's draft "
                  "string prints the draft step's status, error and stderr tail")
def test_show_run_reads_a_failed_draft_step_by_its_draft_string(cli, tmp_project,
                                                                monkeypatch):
    _images(monkeypatch)

    口 = Step(step_num=1, name="Run the method as a draft that fails",
             purpose="A draft step with an error and a stderr log, no run row")
    scenario = _failing(pipeline_id="pipe-d", name="pipe_d", drafts=("a",))
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    spec = scenario.node_by_id("a")
    ref = spell_draft_ref(spec.module, spec.method_name, "a", "s1")

    口 = Step(step_num=2, name="show-run reads it",
             purpose="The draft step's status line, error and tail")
    result = cli("show-run", "--run", ref)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"Draft step {ref}: failed"
    assert any(line.startswith("Error: ") for line in lines)
    assert lines[-1] == f"  {_LINES[-1]}"


def _one_json_object(result) -> dict:
    """stdout is exactly one JSON object carrying ``ok``."""
    body = json.loads(result.stdout)
    assert isinstance(body, dict) and isinstance(body["ok"], bool), result.stdout
    return body


def test_every_json_verb_prints_one_object_with_ok_and_exits_as_its_plain_form(
        cli, tmp_project, monkeypatch):
    """Case 13: each ``--json`` verb, on success and on a failure."""
    run_id = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="expmeth", module="expmod",
        sample="s1", outputs={"masks": ".tif"}, output_files={"masks": "masks.tif"},
        behavior=Behavior(outputs={"masks": "mask-bytes"})).run_id
    from wfc.storage import archive_outputs
    archive_outputs(tmp_project, run_id=run_id)
    stub_readiness_probes(monkeypatch, dvc="ok", samples="ok")

    succeeding = [
        ("runs",), ("list-methods",), ("list-modules",), ("list-samples",),
        ("list-envs",), ("show-run", "--run", str(run_id)), ("doctor",),
        ("export", "--run", str(run_id), "--slot", "masks", "--path"),
        ("export", "--run", str(run_id), "--all", "--path"), ("verify",),
    ]
    failing = [
        ("runs", "--method", "unqualified"),
        ("show-run", "--run", "999"),
        ("show-run", "--run", "draft:m.x/x@s1"),
        ("export", "--run", str(run_id), "--slot", "nope", "--path"),
        ("export", "--run", "draft:m.x/x@s1", "--slot", "masks", "--path"),
    ]
    for argv in succeeding + failing:
        plain = cli(*argv)
        as_json = cli(*argv, "--json")
        assert as_json.returncode == plain.returncode, (argv, as_json.stderr)
        body = _one_json_object(as_json)
        assert body["ok"] is (plain.returncode == 0), argv
        if argv in failing:
            assert plain.returncode == 1, argv
            assert body["error"] and f"ERROR: {body['error']}" in as_json.stderr, argv

    items = _one_json_object(cli("runs", "--json"))["items"]
    assert [(i["run"], i["method"], i["status"]) for i in items] == [
        (run_id, "expmod.expmeth v1", "completed")]
    paths = _one_json_object(cli("export", "--run", str(run_id), "--all", "--path",
                                 "--json"))["items"]
    plain_path = cli("export", "--run", str(run_id), "--slot", "masks", "--path")
    assert [(p["slot"], p["path"]) for p in paths] == [
        ("masks", plain_path.stdout.strip())]
