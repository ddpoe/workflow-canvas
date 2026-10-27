"""The run bundle's plan: which runs an export holds, and every reason it cannot.

The plan writes no bundle. It selects the requested runs and every run above
them, each once, with a cache-hit row replaced by the run it reused; runs
Storage's archive pass over their unarchived outputs; collects every refusal
before deciding; and lists the steps whose image only this machine holds.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung. A run made before the invocation record
comes from the legacy ``register_run`` and ``complete_run`` verbs, the route
that writes none.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import completed_run
from tests.harness import Scenario, node, selector, wire
from tests.harness.scenario import DEFAULT_MODULE, DEFAULT_SAMPLE, SELECTOR_ID
from wfc.persistence import MethodVersion, Run, get_session
from wfc.run_bundle import ExportRefusedError, plan_export, write_bundle
from wfc.version import git_toplevel


def _lineage() -> Scenario:
    """A shared upstream feeding three consumers, two of them joined (fan-in)."""
    return Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)]),
               node("a", inputs=[wire("up")]),
               node("b", inputs=[wire("up")], params={"k": 3}),
               node("c", inputs=[wire("up")], params={"k": 4}),
               node("join", inputs=[wire("a", target_slot="left"),
                                    wire("b", target_slot="right")])],
        samples=[DEFAULT_SAMPLE], pipeline_id="bundle-plan", name="bundle_plan",
    )


def _reasons(plan, run_id: int) -> str:
    return "\n".join(r.reason for r in plan.refusals if r.run_id == run_id)


def _old_run(method: str) -> int:
    """A completed run made through the route that records no invocation."""
    from wfc.execution.claim import register_run
    from wfc.execution.record import complete_run

    run_id = register_run(method, DEFAULT_MODULE, DEFAULT_SAMPLE)
    complete_run(run_id)
    return run_id


@workflow(purpose="An export plan holds the selected runs and every ancestor once: a "
                  "shared upstream is one step, a fan-in reads both branches, and a "
                  "cache-hit row, selected or named by a consumer's input edge, is "
                  "replaced by the run it reused",
          inputs="A shared upstream run and its cache hit, three consumers of the "
                 "hit row and a join over two of them, all driven through "
                 "production's phases",
          outputs="The plan's steps in dependency order with ties by run id, the "
                  "consumers' parents named by the original, one substitution")
def test_the_plan_selects_every_ancestor_once_with_originals_for_cache_hits(
    tmp_project, monkeypatch,
):
    """Every ancestor once, cache hits replaced by originals."""
    scenario = _lineage()

    def drive(target: str) -> int:
        return completed_run(tmp_project, monkeypatch=monkeypatch,
                             scenario=scenario, target=target).run_id

    up = drive("up")
    hit = drive("up")
    a, b, c, join = drive("a"), drive("b"), drive("c"), drive("join")
    with get_session() as session:
        assert session.get(Run, hit).cache_source_run_id == up

    plan = plan_export([join, c])

    assert [s.run_id for s in plan.steps] == [up, a, b, c, join]
    assert [s.number for s in plan.steps] == [1, 2, 3, 4, 5]
    assert plan.step(join).parent_run_ids == (a, b), "slot order, fan-in"
    assert plan.step(a).parent_run_ids == (up,), (
        "a consumer whose edge names the cache-hit row reads the original"
    )
    assert plan.substitutions == ((hit, up),)
    assert plan.ok, plan.refusals
    assert all(o.content_hash for s in plan.steps for o in s.outputs), (
        "the plan's archive pass hashed every selected output"
    )
    assert [w.run_id for w in plan.local_only] == [up, a, b, c, join], (
        "the stub env's image was never pushed, so every step is local-only"
    )

    only_hit = plan_export([hit])
    assert [s.run_id for s in only_hit.steps] == [up]
    assert only_hit.substitutions == ((hit, up),)


@workflow(purpose="A run made before the invocation record is refused by name: the "
                  "plan names the run and the missing record")
def test_a_run_made_before_the_invocation_record_is_refused_by_name(
    tmp_project, monkeypatch,
):
    """The legacy route's completed run is refused, naming the record."""
    completed_run(tmp_project, monkeypatch=monkeypatch, method="legacy")
    old = _old_run("legacy")

    plan = plan_export([old])

    assert not plan.ok
    reasons = _reasons(plan, old)
    assert f"Run {old} " in reasons and "no invocation record" in reasons


@workflow(purpose="An export that cannot be complete lists every reason, across the "
                  "selection, before deciding: a run with no invocation record, a "
                  "version whose tree git no longer holds, and an output the "
                  "archive pass refused as changed",
          inputs="Three runs on one project, one made by the legacy route, one "
                 "whose version tree is removed from git, one whose output is "
                 "edited after collect",
          outputs="One plan whose refusals name each of the three; the archive "
                  "pass hashed the intact outputs")
def test_every_reason_an_export_cannot_be_complete_is_listed(tmp_project, monkeypatch):
    """Three runs, three reasons, all in one plan."""
    口 = Step(step_num=1, name="Make three runs",
             purpose="A legacy run, a run whose tree will go, a run whose output "
                     "will change; each through production")
    completed_run(tmp_project, monkeypatch=monkeypatch, method="legacy")
    old = _old_run("legacy")
    gone = completed_run(tmp_project, monkeypatch=monkeypatch, method="tree_gone",
                         params={"which": "gone"})
    edited = completed_run(tmp_project, monkeypatch=monkeypatch, method="edited",
                           params={"which": "edited"})

    口 = Step(step_num=2, name="Remove a version tree from git",
             purpose="The loose tree object of one run's version is deleted, as a "
                     "history rewrite and gc would leave it (declared deviation: "
                     "no wfc verb drops a git object)")
    with get_session() as session:
        version_id = session.get(Run, gone.run_id).version_id
        tree = session.get(MethodVersion, version_id).tree_hash
    top = git_toplevel(Path(tmp_project))
    loose = top / ".git" / "objects" / tree[:2] / tree[2:]
    assert loose.is_file(), f"the tree {tree} is not a loose object"
    os.chmod(loose, stat.S_IWRITE | stat.S_IREAD)
    loose.unlink()

    口 = Step(step_num=3, name="Edit an output after collect",
             purpose="The run-archive output is rewritten, so the archive pass "
                     "refuses it as changed and it keeps no hash")
    output = edited.output_path("data")
    os.chmod(output, stat.S_IWRITE | stat.S_IREAD)
    output.write_text("someone edited this\n", encoding="utf-8")

    口 = Step(step_num=4, name="Plan the export of all three",
             purpose="Every reason is collected before anything is decided")
    plan = plan_export([old, gone.run_id, edited.run_id])

    assert not plan.ok
    assert "no invocation record" in _reasons(plan, old)
    assert f"git no longer holds the tree {tree}" in _reasons(plan, gone.run_id)
    edited_reasons = _reasons(plan, edited.run_id)
    assert "output 'data'" in edited_reasons and "no content hash" in edited_reasons
    assert plan.archived >= 1, "the intact outputs were archived by the plan"
    assert plan.step(gone.run_id).outputs[0].content_hash is not None

    口 = Step(step_num=5, name="Apply the refused plan",
             purpose="The apply writes nothing: no zip, no temporary file")
    out = Path(tmp_project) / "refused.zip"
    with pytest.raises(ExportRefusedError) as refused:
        write_bundle(plan, out)
    assert len(refused.value.reasons) == len(plan.refusals)
    assert not out.exists()
    assert not list(out.parent.glob(".refused.zip.*")), "a temporary file was left"


@workflow(purpose="A sample registered again with the same bytes from a source of another "
                  "name: the plan names each run's sample by the file that run read, not "
                  "by the name registered today",
          inputs="Two runs on one sample name and the same bytes, the second after the "
                 "sample was registered again from a source named renamed.csv",
          outputs="The first run's sample input names the sample's first file name, the "
                  "second run's names renamed.csv")
def test_the_same_sample_bytes_registered_under_a_new_name_keep_the_name_each_run_read(
    tmp_project, monkeypatch,
):
    """The read path names the file, even when today's row holds the same hash."""
    content = "id,value\n1,same\n"

    def on_sample(method: str, file_name: str | None) -> Scenario:
        return Scenario(nodes=[selector(), node(method, inputs=[wire(SELECTOR_ID)])],
                        samples=[DEFAULT_SAMPLE], sample_content=content,
                        sample_file_names={DEFAULT_SAMPLE: file_name} if file_name else {},
                        pipeline_id=f"{method}-pipeline", name=method)

    口 = Step(step_num=1, name="Run before and after a rename of the sample's source",
             purpose="The harness registers the sample again when its declared file "
                     "name changes; the bytes, and so the hash, stay the same")
    first = completed_run(tmp_project, monkeypatch=monkeypatch,
                          scenario=on_sample("first", None), target="first").run_id
    second = completed_run(tmp_project, monkeypatch=monkeypatch,
                           scenario=on_sample("second", "renamed.csv"),
                           target="second").run_id

    口 = Step(step_num=2, name="Plan each run",
             purpose="Each run's sample input carries the name of the file it read")
    (first_input,) = plan_export([first]).step(first).inputs
    (second_input,) = plan_export([second]).step(second).inputs
    assert first_input.content_hash == second_input.content_hash, "one version"
    assert first_input.sample_file == f"{DEFAULT_SAMPLE}.csv", (
        "the first run read the file under its first name, though today's row, "
        "with the same hash, says renamed.csv")
    assert second_input.sample_file == "renamed.csv"
