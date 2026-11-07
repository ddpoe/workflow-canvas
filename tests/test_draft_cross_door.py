"""A draft's edited ``method.yaml`` at every door a draft runs or is checked through.

Catalog case 1: after the author edits a draft's ``method.yaml`` and presses
no Update, a canvas run, ``wfc run-pipeline`` and ``wfc try`` each read the
contract again and run with it. Once the contract no longer accepts what a
document sets, those three doors, ``wfc check-pipeline`` and canvas Validate
refuse it with one verdict in the same words.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import yaml
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import fake_engine_process, mocked_snakemake
from tests.fixtures.fakes.docker import (
    stub_docker_image_content,
    stub_docker_image_inspect,
)
from tests.fixtures.fakes.readiness import stub_readiness_probes
from tests.fixtures.routes import canvas_client
from tests.fixtures.routes.clients import run_cli
from tests.harness import Scenario, node, selector, wire
from tests.harness.drivers import build_project
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID


def _edit(path: Path, contract: dict) -> None:
    """Write ``method.yaml`` so its stat changes even within one clock tick."""
    before = path.stat().st_mtime_ns
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    after = path.stat()
    if after.st_mtime_ns == before:
        os.utime(path, ns=(after.st_atime_ns, before + 1_000_000_000))


def _add_param(path: Path, contract: dict, name: str) -> None:
    """Declare one more optional int param in the draft's ``method.yaml``."""
    contract["params"] = {**(contract.get("params") or {}),
                          name: {"type": "int", "required": False, "default": 1}}
    _edit(path, contract)


@contextmanager
def _engine():
    """The engine process faked; everything before its launch runs for real.

    Yields:
        The launches made so far: a list the engine spawns are appended to.
    """
    launches: list = []
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True,
                                        launch=lambda argv, **_: launches.append(argv)):
        yield launches


@contextmanager
def _method_launch(project, spec, monkeypatch):
    """The stub rung's container launch for the try's one step.

    Yields:
        The step's record: its ``dispatch_cmd`` is set once the step launches.
    """
    from tests.fixtures.fakes.engine import stub_method_process
    from tests.harness.drivers import STUB
    from tests.harness.observe import TargetRun

    record = TargetRun(target=(spec.id, "s1", "default"))
    with stub_method_process(project, spec, record, monkeypatch, STUB):
        yield record


#: run-pipeline's one ``ERROR:`` line over the validator's refusals, which it
#: lists indented under it (``check-pipeline`` prints an ``ERROR:`` line each).
_REFUSED_HEADER = "ERROR: the pipeline document is refused:"


def _refusals(stderr: str) -> list[str]:
    """The refusal lines a CLI door printed, without its framing."""
    lines = stderr.strip().splitlines()
    if lines and lines[0] == _REFUSED_HEADER:
        return [line.strip() for line in lines[1:]]
    return [line.removeprefix("ERROR: ") for line in lines if line.startswith("ERROR: ")]


@workflow(purpose="Case 1: a draft's method.yaml edited with no Update is read "
                  "again by a canvas run, wfc run-pipeline and wfc try, each of "
                  "which runs with the new contract and stores it on the "
                  "pointer; once the contract drops a param the document sets, "
                  "canvas Validate, wfc check-pipeline, the canvas run, wfc "
                  "run-pipeline and wfc try refuse it with the same line")
def test_an_edited_draft_runs_without_update_and_one_mismatch_gets_one_verdict(
        tmp_project, monkeypatch, tmp_path):
    from wfc.canvas.state import _active_jobs
    from wfc.execution.draft import read_draft_run
    from wfc.registration import read_draft

    口 = Step(step_num=1, name="A project whose mid is a draft",
             purpose="sel -> mid over one sample, mid pinned to its draft; the "
                     "canvas client and the CLI share the project")
    scenario = Scenario(nodes=[selector(), node("mid", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1"], pipeline_id="edited", name="edited",
                        drafts=("mid",))
    mid = scenario.node_by_id("mid")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")
    client = canvas_client(project.root, monkeypatch)
    document = json.loads(project.pipeline_json.read_text(encoding="utf-8"))
    mid_node = next(n for n in document["nodes"] if n["id"] == "mid")
    folder = read_draft(mid.module, mid.method_name, project.root).folder
    path = folder / "method.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    pipeline_file = tmp_path / "edited.json"

    def stored(param: str) -> bool:
        pointer = read_draft(mid.module, mid.method_name, project.root)
        return (param in pointer.contract["params"]
                and pointer.snapshot["method.yaml"][1] == path.stat().st_mtime_ns)

    口 = Step(step_num=2, name="Canvas run with no Update",
             purpose="method.yaml declares canvas_gain; a document setting it is "
                     "accepted, and the pointer holds the contract read now")
    _add_param(path, contract, "canvas_gain")
    mid_node["params"] = {"canvas_gain": 2}
    with _engine() as launches:
        resp = client.post("/api/workflow/run", json=document)
        assert resp.status_code == 200, resp.text
        job = _active_jobs[resp.json()["job_id"]]
        job["thread"].join(timeout=30)
    _active_jobs.clear()
    assert not job.get("error"), job.get("error")
    assert len(launches) == 1
    assert stored("canvas_gain")

    口 = Step(step_num=3, name="wfc run-pipeline with no Update",
             purpose="method.yaml adds cli_gain; the document setting it runs "
                     "(exit 0), and the pointer holds the contract read now")
    _add_param(path, contract, "cli_gain")
    mid_node["params"] = {"canvas_gain": 2, "cli_gain": 3}
    pipeline_file.write_text(json.dumps(document), encoding="utf-8")
    with _engine() as launches:
        result = run_cli("run-pipeline", "--pipeline", str(pipeline_file))
    assert result.returncode == 0, result.stderr
    assert len(launches) == 1
    assert stored("cli_gain")

    口 = Step(step_num=4, name="wfc try with no Update",
             purpose="method.yaml adds try_gain; trying the draft's folder with "
                     "it set runs the step to completion")
    _add_param(path, contract, "try_gain")
    try_argv = ["try", str(folder), "--module", mid.module, "--sample", "s1",
                "--params", json.dumps({"try_gain": 4})]
    with _method_launch(project, mid, monkeypatch) as tried_step:
        result = run_cli(*try_argv)
    assert result.returncode == 0, result.stderr
    assert tried_step.dispatch_cmd is not None
    record = read_draft_run(mid.module, mid.method_name, project.root)
    assert record["steps"]["mid"]["s1"]["status"] == "completed"
    assert stored("try_gain")

    口 = Step(step_num=5, name="The contract drops canvas_gain",
             purpose="The document still sets canvas_gain, and the try passes it "
                     "too: neither matches the contract now")
    del contract["params"]["canvas_gain"]
    _edit(path, contract)
    mid_node["params"] = {"canvas_gain": 2}
    pipeline_file.write_text(json.dumps(document), encoding="utf-8")
    try_argv[-1] = json.dumps({"canvas_gain": 2})

    口 = Step(step_num=6, name="Canvas Validate and wfc check-pipeline",
             purpose="The verdict: not valid, one error naming node mid and the "
                     "param; check-pipeline prints that line as its ERROR: line")
    verdict = client.post("/api/workflow/validate", json=document).json()
    assert verdict["valid"] is False, verdict
    expected = verdict["errors"]
    assert len(expected) == 1 and expected[0].startswith("Node 'mid'"), expected
    assert "'canvas_gain'" in expected[0], expected
    checked = run_cli("check-pipeline", "--pipeline", str(pipeline_file))
    assert checked.returncode == 1, checked.stderr
    assert _refusals(checked.stderr) == expected

    口 = Step(step_num=7, name="The canvas run, wfc run-pipeline and wfc try",
             purpose="Each refuses with the same line and launches nothing: the "
                     "canvas answers 400 with the validator's line (its param "
                     "check reads the contract read now), the CLI doors exit 1")
    with _engine() as canvas_launches:
        resp = client.post("/api/workflow/run", json=document)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].split("\n") == expected
    assert canvas_launches == [] and not _active_jobs
    with _engine() as cli_launches:
        refused = run_cli("run-pipeline", "--pipeline", str(pipeline_file))
    assert refused.returncode == 1, refused.stderr
    assert _refusals(refused.stderr) == expected
    assert cli_launches == []
    with _method_launch(project, mid, monkeypatch) as tried_step:
        tried = run_cli(*try_argv)
    assert tried.returncode == 1, tried.stderr
    assert _refusals(tried.stderr) == expected
    assert tried_step.dispatch_cmd is None
