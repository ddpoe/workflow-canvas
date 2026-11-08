"""A draft step's entry records the process running it (decisions::d-28).

A `wfc try` writes its draft step's entry ``running`` before the method
runs; the entry names the process running the step by its pid and creation
time, so Execution's liveness can tell a dead try from a live one. The
project is built through the scenario harness (a registered method feeding a
draft); the container launch is the harness's stub rung, the tried folder's
own script run as a local process. That script copies the draft run's record
while it runs, which is how the test sees the ``running`` entry.

Pins: the Docker image and readiness probes (the draft project's fakes).
Does not prove: anything about a real Docker daemon or a real container.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from axiom_annotations import Step, workflow

from tests.fixtures.draft_project import built_draft_project
from wfc.cli import cli_main
from wfc.execution.draft import (
    DRAFT_STEP_PROCESS_KEY,
    draft_step_process,
    list_draft_steps,
    read_draft_run,
    record_draft_step,
)
from wfc.execution.pipeline_record import ProcessIdentity, process_identity

#: The tried method: it copies its draft run's record, as it stands while
#: the step runs, into its one output.
_SNAPSHOT_SCRIPT = '''\
import os
from pathlib import Path

run_dir = Path(os.environ["WFC_RUN_DIR"])
record = (run_dir.parent.parent / "draft-run.json").read_text(encoding="utf-8")
(run_dir / "result.csv").write_text(record, encoding="utf-8")
'''


def _tried_folder(parent: Path, env: str) -> Path:
    folder = parent / "snap"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n    required: true\n"
        "outputs:\n  result:\n    type: .csv\n    required: true\n"
        f"executor: local\nenv: {env}\ngpus: false\n", encoding="utf-8")
    (folder / "snap.py").write_text(_SNAPSHOT_SCRIPT, encoding="utf-8")
    return folder


@contextmanager
def _launch(tmp_project, scenario, monkeypatch):
    """The stub rung's container launch, for a step outside a scenario's own."""
    from tests.fixtures.fakes.engine import stub_method_process
    from tests.harness.drivers import STUB
    from tests.harness.observe import TargetRun

    project = SimpleNamespace(root=tmp_project, method_scripts={}, scenario=scenario)
    with stub_method_process(project, SimpleNamespace(method_name="snap"),
                             TargetRun(target=("up", "s1", "default")),
                             monkeypatch, STUB):
        yield


def _tried(tmp_project, monkeypatch, capsys) -> tuple[str, dict, dict]:
    """Try the snapshot folder once; return its module, its record and its step."""
    scenario, module, _up_run, _mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _tried_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           scenario.env_name)
    capsys.readouterr()
    with _launch(tmp_project, scenario, monkeypatch):
        rc = cli_main(["try", str(folder), "--module", module, "--sample", "s1"])
    assert rc == 0, capsys.readouterr().err
    record = read_draft_run(module, "snap", tmp_project)
    return module, record, record["steps"]["snap"]["s1"]


@workflow(purpose="A wfc try's draft step entry names the process running it: "
                  "while the method runs the entry is running with this "
                  "process's pid and creation time, after it ends the entry "
                  "keeps them, and the run listing exposes them for a probe")
def test_a_tried_draft_step_records_the_process_running_it(
        tmp_project, monkeypatch, capsys):
    from wfc import layout

    me = process_identity(os.getpid())

    口 = Step(step_num=1, name="Try a folder whose method copies its draft run",
             purpose="wfc try runs the step in this process; the method reads "
                     "draft-run.json while its own entry is running")
    module, record, step = _tried(tmp_project, monkeypatch, capsys)
    assert step["status"] == "completed"

    口 = Step(step_num=2, name="While it ran: running, with this process",
             purpose="The copy the method took holds the step's running entry "
                     "and the identity of the process running the step")
    copy = layout.draft_dir(tmp_project, module, "snap") / step["outputs"]["result"]
    while_running = json.loads(copy.read_text(encoding="utf-8"))["steps"]["snap"]["s1"]
    assert while_running["status"] == "running"
    assert while_running[DRAFT_STEP_PROCESS_KEY] == me.to_json()

    口 = Step(step_num=3, name="After it ended: the entry keeps it",
             purpose="The completed entry still names the same process, and "
                     "the listing exposes it as an identity a probe takes")
    assert step[DRAFT_STEP_PROCESS_KEY] == me.to_json()
    (listing,) = list_draft_steps(method=f"{module}.snap", root=tmp_project)
    assert listing.status == "completed"
    assert listing.process == me


def test_an_entry_with_no_recorded_process_still_lists(tmp_project, monkeypatch, capsys):
    module, record, step = _tried(tmp_project, monkeypatch, capsys)
    del step[DRAFT_STEP_PROCESS_KEY]
    record_draft_step(module, "snap", pipeline_id=record["pipeline_id"],
                      pipeline_name=record["pipeline_name"], step=step,
                      root=tmp_project)

    (listing,) = list_draft_steps(method=f"{module}.snap", root=tmp_project)
    assert listing.status == "completed"
    assert listing.process is None


def test_an_unknown_creation_time_reads_as_not_recorded(tmp_project, monkeypatch, capsys):
    module, record, step = _tried(tmp_project, monkeypatch, capsys)
    step[DRAFT_STEP_PROCESS_KEY] = ProcessIdentity(pid=os.getpid(), created=None).to_json()
    record_draft_step(module, "snap", pipeline_id=record["pipeline_id"],
                      pipeline_name=record["pipeline_name"], step=step,
                      root=tmp_project)

    stored = read_draft_run(module, "snap", tmp_project)["steps"]["snap"]["s1"]
    assert stored[DRAFT_STEP_PROCESS_KEY] == {"pid": os.getpid(), "created": None}
    (listing,) = list_draft_steps(method=f"{module}.snap", root=tmp_project)
    assert listing.process is None


def test_a_stored_process_that_is_not_an_identity_reads_as_not_recorded():
    assert draft_step_process({}) is None
    assert draft_step_process({DRAFT_STEP_PROCESS_KEY: None}) is None
    assert draft_step_process({DRAFT_STEP_PROCESS_KEY: 1234}) is None
    assert draft_step_process({DRAFT_STEP_PROCESS_KEY: {"created": 1.0}}) is None
    assert draft_step_process({DRAFT_STEP_PROCESS_KEY: {"pid": "x", "created": 1.0}}) is None
    assert draft_step_process(
        {DRAFT_STEP_PROCESS_KEY: {"pid": 7, "created": 1.5}}) == ProcessIdentity(7, 1.5)
