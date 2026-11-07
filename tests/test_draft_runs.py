"""Draft runs in pipelines: replacement, clearing, the launch refusals, registering.

A draft keeps only its latest draft run. Running it again in another
pipeline replaces its folder and the downstream draft-mode steps it owns,
and clears the later drafts' results computed from it, never anything
upstream; the pipelines it cleared say why. A changed contract is read
again on run; an unreadable contract, a ✗ of the Registration check, an
undeclared param or link slot, or a draft run still in progress refuses the
launch and keeps the previous draft run; registering a draft makes its next
run a recorded one.

Every state comes from the scenario harness's stub rung (``Scenario.drafts``
adds each draft through Registration's ``add_draft``) and production's own
entries; the engine of ``run_pipeline`` is mocked, as in
``tests/test_pipeline_run_records.py``.
"""
from __future__ import annotations

import dataclasses
import json
import re

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import fake_engine_process
from tests.fixtures.fakes.docker import (
    stub_docker_image_content,
    stub_docker_image_inspect,
)
from tests.harness import Scenario, drive_target, exits, node, run_scenario, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.execution.draft import qualified_draft, read_draft_run
from wfc.persistence import PipelineRun, Run, get_session


def _chain(pipeline_id: str, **kwargs) -> Scenario:
    """sel -> up -> mid -> low over one sample."""
    return Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["s1"], pipeline_id=pipeline_id, name=pipeline_id.replace("-", "_"),
        **kwargs)


def _images(monkeypatch) -> None:
    """The daemon reports the stub rung's image present (the draft's env image)."""
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)


def _rows(pid: str) -> list[tuple]:
    """The pipeline's run rows as ``(id, node, status, finished_at)``."""
    with get_session() as s:
        return [(r.id, r.node_id, r.status, r.finished_at) for r in s.exec(
            select(Run).where(Run.pipeline_id == pid).order_by(Run.id)).all()]


def _row(pid: str) -> PipelineRun | None:
    with get_session() as s:
        return s.get(PipelineRun, pid)


def _steps(record: dict | None) -> set[tuple[str, str]]:
    """The ``(node, sample)`` entries of a draft run."""
    if record is None:
        return set()
    return {(n, s) for n, by_sample in record["steps"].items() for s in by_sample}


_HHMM = re.compile(r"replaced by the draft run of \d\d:\d\d:\d\d")


@workflow(purpose="Case 2: a draft run, then the same draft run again in another "
                  "pipeline: the draft's folder is replaced and the downstream "
                  "draft-mode steps it owns go with it; the upstream recorded "
                  "runs are untouched; the first pipeline's record reads "
                  "'replaced by the draft run of HH:MM:SS' and keeps what it recorded")
def test_a_draft_run_again_in_another_pipeline_replaces_the_first(tmp_project,
                                                                  monkeypatch):
    from wfc.execution.draft_clearing import replaced_reason

    _images(monkeypatch)
    first = _chain("pipe-a", drafts=("mid",))
    mid = first.node_by_id("mid")

    口 = Step(step_num=1, name="Run up -> mid (draft) -> low in pipeline A",
             purpose="up is recorded; mid and low are A's draft run")
    run_scenario(first, root=tmp_project, monkeypatch=monkeypatch)
    a_rows = _rows("pipe-a")
    a_record = read_draft_run(mid.module, mid.method_name, tmp_project)
    assert a_record["pipeline_id"] == "pipe-a"
    assert _steps(a_record) == {("mid", "s1"), ("low", "s1")}
    assert [n for _i, n, _s, _f in a_rows] == ["up"]
    assert _row("pipe-a").cleared_reason is None

    口 = Step(step_num=2, name="Run the same nodes in pipeline B",
             purpose="mid's next draft run, in another pipeline")
    second = dataclasses.replace(first, pipeline_id="pipe-b", name="pipe_b")
    run_scenario(second, root=tmp_project, monkeypatch=monkeypatch)

    口 = Step(step_num=3, name="mid's folder holds B's draft run only",
             purpose="A's mid and low entries went with the folder; B's are there")
    record = read_draft_run(mid.module, mid.method_name, tmp_project)
    assert record["pipeline_id"] == "pipe-b"
    assert record["started_at"] != a_record["started_at"]
    assert _steps(record) == {("mid", "s1"), ("low", "s1")}
    folder = layout.draft_dir(tmp_project, mid.module, mid.method_name)
    for step in (record["steps"]["mid"]["s1"], record["steps"]["low"]["s1"]):
        assert all((folder / rel).is_file() for rel in step["outputs"].values())

    口 = Step(step_num=4, name="Upstream recorded runs are untouched",
             purpose="A's up row is as it was; nothing of A's was deleted")
    assert _rows("pipe-a") == a_rows

    口 = Step(step_num=5, name="A's record says it was replaced, and when",
             purpose="The cleared reason names B's run's start as HH:MM:SS; A keeps "
                     "its row (it recorded up), its status is its rows' and its "
                     "draft-step count says what it ran")
    a_row, b_row = _row("pipe-a"), _row("pipe-b")
    assert a_row.cleared_reason == replaced_reason(record)
    assert _HHMM.fullmatch(a_row.cleared_reason), a_row.cleared_reason
    assert a_row.cleared_at is not None
    assert a_row.status == "succeeded" and a_row.draft_step_count == 2
    assert (b_row.cleared_reason, b_row.draft_step_count) == (None, 2)
    assert b_row.drafts == [qualified_draft(mid.module, mid.method_name)]


@workflow(purpose="Case 3: two drafts in one pipeline, the upstream one rerun in "
                  "another pipeline: the later draft's results computed from it "
                  "are cleared and the first pipeline names both drafts' reason; "
                  "nothing upstream is touched")
def test_rerunning_the_upstream_draft_clears_the_later_drafts_results(tmp_project,
                                                                      monkeypatch):
    from wfc.execution.draft_clearing import replaced_reason

    _images(monkeypatch)
    both = Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("a", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("b", inputs=[wire("a", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("b", source_slot="table")])],
        samples=["s1"], pipeline_id="pipe-p", name="pipe_p", drafts=("a", "b"))
    a, b = both.node_by_id("a"), both.node_by_id("b")

    口 = Step(step_num=1, name="Run up -> a (draft) -> b (draft) -> low in P",
             purpose="b's draft run reads a's results through a's draft string")
    run_scenario(both, root=tmp_project, monkeypatch=monkeypatch)
    up_rows = _rows("pipe-p")
    b_record = read_draft_run(b.module, b.method_name, tmp_project)
    assert b_record is not None and b_record["pipeline_id"] == "pipe-p"

    口 = Step(step_num=2, name="Rerun a alone in Q",
             purpose="sel -> up -> a with a's draft: a's next draft run")
    upper = Scenario(nodes=both.nodes[:3], samples=["s1"], pipeline_id="pipe-q",
                     name="pipe_q", drafts=("a",))
    run_scenario(upper, root=tmp_project, monkeypatch=monkeypatch)

    口 = Step(step_num=3, name="b's results from a are cleared; nothing upstream",
             purpose="b has no draft run; a's is Q's; P's up row is as it was")
    a_record = read_draft_run(a.module, a.method_name, tmp_project)
    assert a_record["pipeline_id"] == "pipe-q"
    assert read_draft_run(b.module, b.method_name, tmp_project) is None
    assert not [p for p in layout.draft_dir(tmp_project, b.module, b.method_name)
                .iterdir() if p.is_dir() and p.name in {"b", "low"}]
    assert _rows("pipe-p") == up_rows

    口 = Step(step_num=4, name="P names both drafts' reason",
             purpose="A pipeline that ran two drafts says why each was cleared")
    reason = replaced_reason(a_record)
    assert _row("pipe-p").cleared_reason == (
        f"{qualified_draft(a.module, a.method_name)}: {reason}; "
        f"{qualified_draft(b.module, b.method_name)}: {reason}")


def _run_pipeline(project, pid: str, monkeypatch, *, exit_code: int = 0) -> None:
    """Launch ``run_pipeline`` over the built project with a mocked engine."""
    from wfc.execution import run_pipeline

    gen_patch, _ = mocked_snakemake(exit_code)
    with gen_patch, fake_engine_process(returncode=exit_code, only_snakemake=True):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root),
                     pipeline_id=pid)


@workflow(purpose="Case 4, the run half (re-read on run): a draft whose method.yaml "
                  "changed is read again by the run, which goes ahead with no "
                  "Update and stores the contract on the pointer; a script-only "
                  "change runs as it is")
def test_a_changed_contract_is_read_again_on_run_and_a_script_change_runs(
        git_project, monkeypatch):
    from wfc.registration import read_draft, snapshot_selected_script

    _images(monkeypatch)
    scenario = _chain("pipe-stale", drafts=("mid",))
    mid = scenario.node_by_id("mid")
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch)
    folder = read_draft(mid.module, mid.method_name, git_project).folder

    口 = Step(step_num=1, name="Change method.yaml, press no Update",
             purpose="The pointer still holds the old method.yaml's stat")
    contract = folder / "method.yaml"
    contract.write_text(contract.read_text(encoding="utf-8")
                        + "\n# a changed contract\n", encoding="utf-8")

    口 = Step(step_num=2, name="The run reads it again and goes ahead",
             purpose="The preflight's writing re-read stores method.yaml's stat")
    _run_pipeline(obs.project, "pipe-stale-2", monkeypatch)
    pointer = read_draft(mid.module, mid.method_name, git_project)
    assert pointer.snapshot["method.yaml"][1] == contract.stat().st_mtime_ns

    口 = Step(step_num=3, name="Change only the script",
             purpose="A script change only marks the folder's files updated")
    script = snapshot_selected_script(folder, mid.method_name)
    script.write_text(script.read_text(encoding="utf-8") + "\n# a script edit\n",
                      encoding="utf-8")

    口 = Step(step_num=4, name="The launch goes ahead and the step runs as it is",
             purpose="No refusal; mid runs from the changed folder")
    _run_pipeline(obs.project, "pipe-stale", monkeypatch)
    drive_target(obs.project, ("mid", "s1", "default"), monkeypatch=monkeypatch)
    record = read_draft_run(mid.module, mid.method_name, git_project)
    assert record["steps"]["mid"]["s1"]["status"] == "completed"


def _break_contract(folder, _document, _monkeypatch):
    """method.yaml no longer parses."""
    (folder / "method.yaml").write_text("inputs: [unclosed\n", encoding="utf-8")
    return "method.yaml"


def _break_check(folder, _document, _monkeypatch):
    """method.yaml names an env that is not registered: the check's ✗."""
    path = folder / "method.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"(?m)^env:.*$", "env: no-such-env", text), encoding="utf-8")
    return ("Registration check", "no-such-env")


def _break_script_name(folder, _document, _monkeypatch):
    """The script reads an input the contract does not declare: the scan's ✗."""
    from wfc.registration import snapshot_selected_script

    snapshot_selected_script(folder, folder.name).write_text(
        "import wfc_client as wfc\n\n\n"
        "@wfc.method\n"
        "def mid(ctx):\n"
        "    src = ctx.input(\"dta\")\n"
        "    ctx.save_artifact(\"table\", ctx.workdir / \"out.csv\")\n",
        encoding="utf-8")
    return ("Registration check", "'dta'")


def _break_retired_columns(folder, _document, _monkeypatch):
    """method.yaml declares the retired ``columns:`` on its output."""
    import yaml

    path = folder / "method.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract["outputs"]["table"]["columns"] = ["gene"]
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    return ("Registration check", "`columns`")


def _break_params(_folder, document, _monkeypatch):
    """The document sets a param the draft's contract does not declare."""
    next(n for n in document["nodes"] if n["id"] == "mid")["params"] = {"nope": 1}
    return "parameter 'nope'"


def _break_link(_folder, document, _monkeypatch):
    """The document links mid from an output slot its draft does not declare."""
    link = next(lk for lk in document["links"] if lk.get("source") == "mid")
    link["source_slot"] = "gone"
    link.pop("sourceHandle", None)
    return "'gone'"


def _break_unwired_input(folder, _document, _monkeypatch):
    """method.yaml now declares a required input ``ref`` the pipeline does not connect."""
    _add_required_input(folder, "ref")
    return "'ref'"


_GONE_DIGEST = "e" * 64


def _break_env_image(folder, _document, monkeypatch):
    """method.yaml names another registered env whose image the daemon lacks.

    The recorded nodes' env image stays present, so the refusal is the draft
    preflight's own; the other image was never pushed, so it cannot be pulled.
    """
    from tests.fixtures.routes.registration import write_env_record
    from wfc.environments.docker import ImageNotFoundError
    from wfc.persistence import project_root

    write_env_record(project_root(), "gone-env", digest=_GONE_DIGEST)
    path = folder / "method.yaml"
    path.write_text(re.sub(r"(?m)^env:.*$", "env: gone-env",
                           path.read_text(encoding="utf-8")), encoding="utf-8")

    def inspect(ref):
        if _GONE_DIGEST in ref:
            raise ImageNotFoundError(f"Error: No such image: {ref}")
        return f"sha256:{STUB_DIGEST}"

    stub_docker_image_inspect(monkeypatch, inspect)
    return ("gone-env", "is not on this machine")


def _add_required_input(folder, name: str) -> None:
    """Declare one more required input in a draft's method.yaml."""
    import yaml

    path = folder / "method.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract["inputs"][name] = {"type": ".csv", "required": True}
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")


@workflow(purpose="Case 2 (property over refusal kinds) with case 3 and case 4's "
                  "params half: after a good draft run, a run refused before "
                  "launch -- an unreadable contract, a ✗ of the Registration "
                  "check (an unregistered env, a script name the contract does "
                  "not declare, a retired columns: form), an undeclared param, "
                  "a link to an undeclared slot, a required input nothing is "
                  "connected to, an env image that cannot be made runnable -- "
                  "names the node and the reason, writes no "
                  "pipeline row, leaves the pipeline file as written and the "
                  "previous draft run and its outputs as they were")
@pytest.mark.parametrize("breaker", [_break_contract, _break_check,
                                     _break_script_name, _break_retired_columns,
                                     _break_params, _break_link,
                                     _break_unwired_input, _break_env_image],
                         ids=["contract", "check-env", "check-script-name",
                              "check-retired-columns", "params", "link",
                              "unwired-input", "env-not-runnable"])
def test_a_refusal_before_launch_keeps_the_previous_draft_run(git_project,
                                                              monkeypatch, breaker):
    from wfc.execution.draft import DraftLaunchRefusedError
    from wfc.execution.validation import PipelineRefusedError
    from wfc.registration import read_draft

    _images(monkeypatch)
    scenario = _chain("pipe-good", drafts=("mid",))
    mid = scenario.node_by_id("mid")

    口 = Step(step_num=1, name="A good draft run",
             purpose="mid and low are the draft run of pipe-good")
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch)
    before = read_draft_run(mid.module, mid.method_name, git_project)
    folder = layout.draft_dir(git_project, mid.module, mid.method_name)
    outputs = [folder / rel for step in (before["steps"]["mid"]["s1"],)
               for rel in step["outputs"].values()]
    assert outputs and all(p.is_file() for p in outputs)

    口 = Step(step_num=2, name="Break one thing and run",
             purpose="The draft's folder, the pipeline document or the daemon")
    document = json.loads(obs.project.pipeline_json.read_text(encoding="utf-8"))
    named = breaker(read_draft(mid.module, mid.method_name, git_project).folder,
                    document, monkeypatch)
    obs.project.pipeline_json.write_text(json.dumps(document), encoding="utf-8")
    written = obs.project.pipeline_json.read_text(encoding="utf-8")
    with pytest.raises((PipelineRefusedError, DraftLaunchRefusedError, ValueError)) as refused:
        _run_pipeline(obs.project, "pipe-refused", monkeypatch)

    口 = Step(step_num=3, name="Refused naming it; nothing written or lost",
             purpose="No row; the file as written; the previous draft run kept")
    for text in (named if isinstance(named, tuple) else (named,)):
        assert text in str(refused.value), (text, refused.value)
    assert "mid" in str(refused.value), refused.value
    assert _row("pipe-refused") is None
    assert obs.project.pipeline_json.read_text(encoding="utf-8") == written
    assert read_draft_run(mid.module, mid.method_name, git_project) == before
    assert all(p.is_file() for p in outputs)


def _step_params(document):
    """The step's params name one its contract does not declare."""
    next(n for n in document["nodes"] if n["id"] == "mid")["params"] = {"nope": 1}
    return [], "parameter 'nope'"


def _step_parent_entry(_document):
    """The links are walked in a pipeline that never ran up: no run-id sidecar."""
    return None, "is wired from node 'up', but there is no run-id sidecar"


def _step_materialize(_document):
    """The parent entry names a run that does not exist: materialize refuses."""
    return ["data:table:999999"], "999999"


@workflow(purpose="d-11 with case 2's params and inputs kinds: a draft step "
                  "refused before its record -- its params, a parent entry its "
                  "links cannot spell, an input materialize cannot resolve -- "
                  "returns non-zero with ERROR naming it and leaves another "
                  "pipeline's draft run as it was")
@pytest.mark.parametrize("breaker", [_step_params, _step_parent_entry,
                                     _step_materialize],
                         ids=["params", "inputs-parent-entry", "inputs-materialize"])
def test_a_draft_step_refused_before_its_record_keeps_the_previous_run(
        tmp_project, monkeypatch, capsys, breaker):
    from wfc.execution.draft import run_draft_step

    _images(monkeypatch)
    scenario = _chain("pipe-kept", drafts=("mid",))
    mid = scenario.node_by_id("mid")
    obs = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    before = read_draft_run(mid.module, mid.method_name, tmp_project)
    document = json.loads(obs.project.pipeline_json.read_text(encoding="utf-8"))
    parent_run_ids, named = breaker(document)

    rc = run_draft_step("mid", "s1", pipeline_id="pipe-other", document=document,
                        parent_run_ids=parent_run_ids)
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err and named in err, err
    assert read_draft_run(mid.module, mid.method_name, tmp_project) == before


@workflow(purpose="d-12: a draft whose draft run is still in progress refuses "
                  "another pipeline's launch naming the running pipeline; its own "
                  "pipeline is never in its way")
def test_a_draft_run_in_progress_refuses_another_launch(tmp_project, monkeypatch):
    from wfc.execution.draft import DraftLaunchRefusedError, preflight_drafts

    _images(monkeypatch)
    scenario = _chain("pipe-busy", drafts=("mid",))
    obs = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch,
                       pipeline_end=False)
    assert _row("pipe-busy").status == "running"
    document = json.loads(obs.project.pipeline_json.read_text(encoding="utf-8"))

    with pytest.raises(DraftLaunchRefusedError) as refused:
        preflight_drafts(document, tmp_project, pipeline_id="pipe-other")
    assert "still in progress in pipeline" in str(refused.value)
    # Named by its name when it has one, else its id.
    assert re.search(r"pipeline pipe[-_]busy\b", str(refused.value)), refused.value
    preflight_drafts(document, tmp_project, pipeline_id="pipe-busy")


@workflow(purpose="The draft preflight reads the draft's method.yaml again: once "
                  "it declares a required input the pipeline does not connect, "
                  "the preflight refuses naming the node, the draft and the "
                  "input, before any record; the previous draft run stays")
def test_the_draft_preflight_refuses_a_required_input_its_draft_now_declares(
        tmp_project, monkeypatch):
    from wfc.execution.draft import DraftLaunchRefusedError, preflight_drafts
    from wfc.registration import read_draft

    _images(monkeypatch)
    scenario = _chain("pipe-wired", drafts=("mid",))
    mid = scenario.node_by_id("mid")

    口 = Step(step_num=1, name="A good draft run; the preflight passes",
             purpose="mid's draft declares only the input the pipeline connects")
    obs = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    before = read_draft_run(mid.module, mid.method_name, tmp_project)
    document = json.loads(obs.project.pipeline_json.read_text(encoding="utf-8"))
    preflight_drafts(document, tmp_project, pipeline_id="pipe-next")

    口 = Step(step_num=2, name="The draft's method.yaml adds a required input",
             purpose="No Update: the preflight's re-read is what sees it")
    _add_required_input(read_draft(mid.module, mid.method_name, tmp_project).folder,
                        "ref")

    口 = Step(step_num=3, name="The preflight refuses naming node, draft and input",
             purpose="Graph's unwired-input words over the contract read now; "
                     "the previous draft run is as it was")
    with pytest.raises(DraftLaunchRefusedError) as refused:
        preflight_drafts(document, tmp_project, pipeline_id="pipe-next")
    assert (f"Node 'mid': {mid.module}.{mid.method_name}'s draft declares input "
            f"'ref', which nothing is connected to") in str(refused.value)
    assert read_draft_run(mid.module, mid.method_name, tmp_project) == before


@workflow(purpose="Case 6: registering a draft of an existing method adds vN+1, "
                  "removes the draft folder and reads 'registered as vN' on the "
                  "pipeline that ran it; the next run records the method's step "
                  "and the one below it, dispatching them, adopting no draft output")
def test_registering_a_draft_makes_the_next_run_a_recorded_one(tmp_project,
                                                               monkeypatch):
    from wfc.execution.draft_clearing import register_draft_and_clear
    from wfc.registration import read_draft, snapshot_selected_script

    _images(monkeypatch)
    scenario = _chain("pipe-reg", drafts=("mid",))
    mid = scenario.node_by_id("mid")

    口 = Step(step_num=1, name="Run with the draft, then change its script",
             purpose="The draft differs from v1, so registering it adds v2")
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    folder = read_draft(mid.module, mid.method_name, tmp_project).folder
    script = snapshot_selected_script(folder, mid.method_name)
    script.write_text(script.read_text(encoding="utf-8") + "\n# the draft's edit\n",
                      encoding="utf-8")
    draft_dir = layout.draft_dir(tmp_project, mid.module, mid.method_name)
    assert draft_dir.is_dir()

    口 = Step(step_num=2, name="Register the draft",
             purpose="vN+1; the draft folder goes; the pipeline says so")
    registration = register_draft_and_clear(mid.module, mid.method_name,
                                            assume_yes=True, root=tmp_project)
    assert registration.label == 2
    assert not draft_dir.exists()
    row = _row("pipe-reg")
    assert row.cleared_reason == "registered as v2"
    assert [n for _i, n, _s, _f in _rows("pipe-reg")] == ["up"]

    口 = Step(step_num=3, name="The next run is recorded",
             purpose="The same nodes with no draft: mid and low dispatch and are "
                     "recorded; no draft output is adopted")
    recorded = dataclasses.replace(scenario, pipeline_id="pipe-next",
                                   name="pipe_next", drafts=())
    obs = run_scenario(recorded, root=tmp_project, monkeypatch=monkeypatch)
    for node_id in ("mid", "low"):
        run = obs.runs[(node_id, "s1", "default")]
        assert run.run_id is not None and "dispatch" in run.phases_ran, run
    assert {n for _i, n, _s, _f in _rows("pipe-next")} == {"up", "mid", "low"}
    assert read_draft_run(mid.module, mid.method_name, tmp_project) is None


@workflow(purpose="The reworded every-expected-run-ends-with-a-record: a pipeline "
                  "with a draft whose draft step fails ends with one record per "
                  "run it was meant to record and none for a draft-mode step; "
                  "its draft steps say what happened to each, and its status "
                  "counts them")
def test_draft_mode_steps_are_never_recorded_and_say_what_happened(git_project,
                                                                  monkeypatch):
    from wfc.execution.draft import pipeline_draft_steps

    _images(monkeypatch)
    pid = "pipe-draft-ends"
    scenario = Scenario(
        nodes=[selector(),
               node("a", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("a", source_slot="table")],
                    outputs={"table": ".csv"},
                    behavior_by_sample={"S1": exits(1)}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["S1", "S2"], pipeline_id=pid, drafts=("mid",))

    口 = Step(step_num=1, name="Run it and end it with the engine's failure",
             purpose="mid fails on S1, so low never runs there")
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch,
                       pipeline_end=False)
    gen_patch, _ = mocked_snakemake(1)
    from wfc.execution import run_pipeline

    with gen_patch, fake_engine_process(returncode=1, only_snakemake=True), \
            pytest.raises(RuntimeError):
        run_pipeline(pipeline_path=str(obs.project.pipeline_json),
                     project_root=str(git_project),
                     pipeline_id=pid)

    口 = Step(step_num=2, name="One record per recorded run, none for a draft step",
             purpose="a's two rows; no row, cancelled or not, for mid or low")
    rows = _rows(pid)
    assert sorted((n, s) for _i, n, s, _f in rows) == [("a", "completed"),
                                                       ("a", "completed")]

    口 = Step(step_num=3, name="The draft steps say what happened",
             purpose="mid failed on S1 and completed on S2; low ran on S2 only")
    steps = {(step["node_id"], step["sample"]): step["status"]
             for _d, step in pipeline_draft_steps(pid, git_project)}
    assert steps == {("mid", "S1"): "failed", ("mid", "S2"): "completed",
                     ("low", "S2"): "completed"}
    row = _row(pid)
    assert (row.status, row.draft_mode, row.draft_step_count) == ("mixed", True, 3)
    assert row.ended_at is not None
