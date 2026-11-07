"""The draft step: the draft string, parent entries naming draft steps, the --draft rule.

Catalog case 1's dispatch half (a draft-mode step runs with no run row, cache
entry, archive or push) is witnessed beside these through the scenario
harness.
"""
from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow

from tests.harness.drivers import run_scenario
from tests.harness.scenario import SELECTOR_ID
from wfc.execution.draft import (
    DRAFT_REF_FORM,
    DraftRefError,
    is_draft_ref,
    parse_draft_ref,
    spell_draft_ref,
)
from wfc.execution.parents import parse_parent_entries


def test_draft_string_round_trips_every_part():
    text = spell_draft_ref("__demo__", "label", "n-3", "ctrl_01")
    assert text == "draft:__demo__.label/n-3@ctrl_01"
    ref = parse_draft_ref(text)
    assert (ref.module, ref.method, ref.node_id, ref.sample) == (
        "__demo__", "label", "n-3", "ctrl_01")
    assert ref.text == text and is_draft_ref(text) and not is_draft_ref("412")


@pytest.mark.parametrize("short", [
    "draft:__demo__.label",
    "draft:__demo__.label/n-3",
    "draft:__demo__.label@ctrl_01",
    "draft:label/n-3@ctrl_01",
    "draft:",
])
def test_a_draft_string_missing_a_part_is_refused_showing_the_full_form(short):
    with pytest.raises(DraftRefError) as caught:
        parse_draft_ref(short)
    assert DRAFT_REF_FORM in str(caught.value)
    assert "wfc runs" in str(caught.value)


def test_a_parent_entry_takes_a_draft_string_as_its_run_part():
    entries = parse_parent_entries(
        ["data:labeled:draft:__demo__.label/n-3@ctrl_01",
         "extra:draft:__demo__.label/n-4@ctrl_01",
         "more:7"],
        step="n-5", allow_drafts=True)
    assert entries[0].input_slot == "data" and entries[0].source_slot == "labeled"
    assert entries[0].run_id is None
    assert entries[0].draft == "draft:__demo__.label/n-3@ctrl_01"
    assert (entries[1].input_slot, entries[1].source_slot) == ("extra", None)
    assert entries[2].run_id == 7 and entries[2].draft is None


def test_a_slot_named_draft_is_a_recorded_parent():
    # A slot may be named ``draft``: only a full draft string is a draft run part.
    recorded = ["data:draft:412", "draft:7", "draft:draft:9"]
    assert parse_parent_entries(recorded, step="n2") == [
        ("data", "draft", 412, None), ("draft", None, 7, None),
        ("draft", "draft", 9, None)]
    entries = parse_parent_entries([*recorded, "more:draft:draft:m.x/n@s",
                                    "draft:draft:m.x/n@s"],
                                   step="n2", allow_drafts=True)
    assert entries[3] == ("more", "draft", None, "draft:m.x/n@s")
    assert entries[4] == ("draft", None, None, "draft:m.x/n@s")


def test_a_recorded_step_refuses_a_draft_parent_and_a_short_one_is_refused():
    with pytest.raises(ValueError, match="register the draft"):
        parse_parent_entries(["data:draft:m.x/n@s"], step="n2")
    with pytest.raises(DraftRefError):
        parse_parent_entries(["data:draft:m.x/n"], step="n2", allow_drafts=True)


def test_generated_rules_pass_draft_for_draft_mode_steps_only():
    from wfc.graph import PipelineDef, StepDef
    from wfc.orchestration.snakemake import generate_snakefile

    steps = [StepDef(method_name="up", module_name="m", params={}, node_id="up"),
             StepDef(method_name="mid", module_name="m", params={}, node_id="mid",
                     depends_on=["up"], inputs={"data": ["up"]})]
    text = generate_snakefile(PipelineDef(steps=steps, samples=["s1"]), ".",
                              pipeline_id="p1", draft_steps={"mid"})
    rules = {block.split(":", 1)[0]: block for block in text.split("rule ")[1:]}
    assert "--draft" in rules["mid"]
    assert "--draft" not in rules["up"]
    # The draft step still declares (and touches) its sentinel output.
    assert "output:" in rules["mid"]


# --- case 1, the dispatch half: a pipeline with a draft --------------------------


def _chain(**kwargs):
    from tests.harness import Scenario, node, selector, wire

    return Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("mid", source_slot="table")])],
        samples=["s1"], pipeline_id="draft-chain", name="draft_chain", **kwargs)


def _state(root) -> dict:
    """What a draft step must never add to: run rows, output rows, blobs, archives."""
    from wfc import layout
    from tests.harness.observe import _read_rows

    runs = _read_rows("Run")
    store = layout.run_archive_dir(root, 1).parent
    archives = [p.name for p in store.iterdir() if p.name.isdigit()] if store.is_dir() else []
    return {
        "runs": sorted((r["id"], r["node_id"]) for r in runs),
        "outputs": sorted((r["id"], r["run_id"]) for r in _read_rows("RunOutput")),
        "blobs": sorted(str(r) for r in _read_rows("Blob")),
        "archives": sorted(archives),
    }


@workflow(purpose="Case 1, dispatch half: a pipeline with a draft runs the draft "
                  "and every step below it as draft steps, every time, writing "
                  "no run row, output row, cache entry, archive or push-queue "
                  "entry; their results and what they did are in the draft's "
                  "folder and draft-run.json; the step above the draft runs and "
                  "caches as it would without the draft")
def test_a_pipeline_with_a_draft_runs_its_draft_steps_and_records_none_of_them(
        tmp_project, monkeypatch):
    from tests.fixtures.fakes.docker import (
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from tests.harness.project import STUB_DIGEST
    from wfc import layout
    from wfc.execution.draft import read_draft_run

    scenario = _chain(drafts=("mid",))
    mid = scenario.node_by_id("mid")
    # The draft runs on its env's current image, which it makes runnable
    # itself (a pipeline's pre-flight has before it; `wfc try` has none): the
    # daemon reports the stub rung's image present.
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)

    口 = Step(step_num=1, name="Run up -> mid (draft) -> low",
             purpose="The whole pipeline, its end walk and status helper "
                     "included: the walk writes no cancelled row for a "
                     "draft-mode step")
    first = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    after_first = _state(tmp_project)

    口 = Step(step_num=2, name="Only up was recorded; mid and low dispatched",
             purpose="Every run row, output row and archive is up's")
    up_target, mid_target, low_target = (("up", "s1", "default"),
                                         ("mid", "s1", "default"),
                                         ("low", "s1", "default"))
    assert {n for _id, n in after_first["runs"]} == {"up"}
    assert first.runs[up_target].run_id is not None
    assert "dispatch" in first.runs[up_target].phases_ran
    for target in (mid_target, low_target):
        run = first.runs[target]
        assert run.rc == 0, run
        assert run.dispatch_cmd is not None
        assert run.run_id is None and not run.phases_ran
    up_run = first.runs[up_target].run_id
    assert after_first["archives"] in ([], [layout.run_archive_name(up_run)])

    口 = Step(step_num=3, name="draft-run.json holds mid and low",
             purpose="Both completed, outputs inside the draft's folder, and "
                     "low's input named through mid's draft string")
    record = read_draft_run(mid.module, mid.method_name, tmp_project)
    assert record is not None and record["pipeline_id"] == "draft-chain"
    folder = layout.draft_dir(tmp_project, mid.module, mid.method_name)
    for node_id in ("mid", "low"):
        step = record["steps"][node_id]["s1"]
        assert step["status"] == "completed", step
        assert step["outputs"]
        for rel in step["outputs"].values():
            assert (folder / rel).is_file()
            assert rel.startswith(f"{node_id}/s1/")
    assert record["steps"]["mid"]["s1"]["version"] == "draft"
    mid_ref = spell_draft_ref(mid.module, mid.method_name, "mid", "s1")
    assert any(mid_ref in entry for entry in record["steps"]["low"]["s1"]["inputs"])
    assert layout.run_sentinel_path(tmp_project, "draft-chain", "low", "s1",
                                    "default").exists()

    口 = Step(step_num=4, name="Run the same pipeline again",
             purpose="up is a cache hit, as without the draft; mid and low "
                     "dispatch again and still add nothing")
    second = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    after_second = _state(tmp_project)
    assert "dispatch" not in second.runs[up_target].phases_ran
    assert "record" in second.runs[up_target].phases_ran
    for target in (mid_target, low_target):
        assert second.runs[target].rc == 0
        assert second.runs[target].dispatch_cmd is not None
    assert {n for _id, n in after_second["runs"]} == {"up"}
    assert after_second["outputs"] == after_first["outputs"]
    assert after_second["blobs"] == after_first["blobs"]
    assert after_second["archives"] == after_first["archives"]


# --- resolving a draft string: why one names no draft step now -------------------


@workflow(purpose="A draft string names the latest draft run's step, or is refused "
                  "saying why: a step named through the wrong draft gets the right "
                  "string, a node or sample that run did not use lists the ones it "
                  "did, a draft with no draft run or no draft at all says so")
def test_a_draft_string_that_names_no_draft_step_now_is_refused_saying_why(
        tmp_project, monkeypatch):
    from tests.fixtures.fakes.docker import (
        stub_docker_image_content,
        stub_docker_image_inspect,
    )
    from tests.harness import Scenario, node, selector, wire
    from tests.harness.project import STUB_DIGEST
    from wfc.execution.draft import clear_draft_run, resolve_draft_step
    from wfc.registration import remove_draft

    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)

    口 = Step(step_num=1, name="Run a pipeline with two drafts side by side",
             purpose="Each draft owns its own step: two draft runs of one pipeline")
    scenario = Scenario(
        nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)]),
               node("b", inputs=[wire(SELECTOR_ID)])],
        samples=["s1"], pipeline_id="two-drafts", name="two_drafts",
        drafts=("a", "b"))
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch,
                 pipeline_end=False)
    a, b = scenario.node_by_id("a"), scenario.node_by_id("b")
    a_ref = spell_draft_ref(a.module, a.method_name, "a", "s1")

    def refusal(text: str) -> str:
        with pytest.raises(DraftRefError) as caught:
            resolve_draft_step(text, tmp_project)
        return str(caught.value)

    口 = Step(step_num=2, name="The full string resolves to the step",
             purpose="The latest draft run's entry for that node and sample")
    ref, record, step = resolve_draft_step(a_ref, tmp_project)
    assert (ref.node_id, ref.sample) == ("a", "s1")
    assert record["pipeline_id"] == "two-drafts" and step["status"] == "completed"

    口 = Step(step_num=3, name="Refusals name what would resolve",
             purpose="The wrong draft names the right string; an unused node or "
                     "sample lists the ones the run used")
    wrong = spell_draft_ref(b.module, b.method_name, "a", "s1")
    assert a_ref in refusal(wrong)
    unused_node = refusal(spell_draft_ref(a.module, a.method_name, "zz", "s1"))
    assert "'zz'" in unused_node and "ran nodes: a" in unused_node
    unused_sample = refusal(spell_draft_ref(a.module, a.method_name, "a", "s9"))
    assert "'s9'" in unused_sample and "used samples: s1" in unused_sample

    口 = Step(step_num=4, name="No draft run, then no draft",
             purpose="A cleared draft run and a removed draft each say so")
    assert clear_draft_run(b.module, b.method_name, tmp_project)
    assert "has no draft run" in refusal(spell_draft_ref(b.module, b.method_name,
                                                         "b", "s1"))
    remove_draft(a.module, a.method_name, tmp_project)
    gone = refusal(a_ref)
    assert "registered or removed" in gone and "wfc runs" in gone
