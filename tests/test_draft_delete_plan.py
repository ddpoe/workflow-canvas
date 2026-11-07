"""The delete-draft plan: what Remove draft and Delete outputs delete, and its token.

Execution plans a delete before it writes anything: the draft folder (or,
for Delete outputs, its draft run and results), every later draft's
results computed from it, and the pipelines whose cleared line changes,
with an opaque token. The apply half takes that token and refuses, having
deleted nothing, when a fresh plan differs from it. ``wfc delete-draft``
and the canvas's two delete routes confirm against the same plan.

States come from the scenario harness's stub rung (``Scenario.drafts`` adds
each draft through Registration's ``add_draft``) and production's own
entries, as in ``tests/test_draft_runs.py``.
"""
from __future__ import annotations

import re
import shutil

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes.docker import (
    stub_docker_image_content,
    stub_docker_image_inspect,
)
from tests.fixtures.method_versions import register_versions
from tests.fixtures.routes import register_test_method
from tests.harness import Scenario, node, run_scenario, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.execution.draft import draft_run_time, qualified_draft, read_draft_run
from wfc.persistence import PipelineRun, get_session


def _images(monkeypatch) -> None:
    """The daemon reports the stub rung's image present (the draft's env image)."""
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)


#: A size as the plan words it (``size_label``'s form).
_SIZE = r"\d+(?:\.\d)? (?:B|KB|MB|GB)"


def _folder_bytes(folder) -> int:
    """Every byte under a folder, whatever wfc keeps there."""
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def test_a_size_reads_in_the_largest_unit_it_fills():
    """Bytes below a kibibyte, then KB, MB and GB with one decimal."""
    from wfc.execution.draft_clearing import size_label

    assert [size_label(n) for n in (0, 512, 1536, 5 * 1024 ** 2, 3 * 1024 ** 3)] == [
        "0 B", "512 B", "1.5 KB", "5.0 MB", "3.0 GB"]


def _two_drafts() -> Scenario:
    """sel -> up -> a (draft) -> b (draft) -> low: b reads a's draft string."""
    return Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("a", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("b", inputs=[wire("a", source_slot="table")],
                    outputs={"table": ".csv"}),
               node("low", inputs=[wire("b", source_slot="table")])],
        samples=["s1"], pipeline_id="pipe-p", drafts=("a", "b"))


@workflow(purpose="Case 5, Execution half: the plan of Remove draft and of Delete "
                  "outputs names what goes -- the draft folder or its draft run, "
                  "a later draft's results computed from it, and the pipeline "
                  "whose cleared line changes -- and writes nothing; applying "
                  "with a token a fresh plan no longer has is refused having "
                  "deleted nothing; the fresh plan's token applies it")
def test_the_delete_plan_names_what_goes_and_a_stale_token_is_refused(
        tmp_project, monkeypatch):
    from wfc.execution.draft_clearing import (
        DraftDeletePlanChangedError,
        delete_draft_outputs_and_clear,
        plan_draft_delete,
        remove_draft_and_clear,
    )
    from wfc.registration import read_draft

    _images(monkeypatch)
    scenario = _two_drafts()
    a, b = scenario.node_by_id("a"), scenario.node_by_id("b")
    a_name, b_name = (qualified_draft(a.module, a.method_name),
                      qualified_draft(b.module, b.method_name))
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    a_folder = layout.draft_dir(tmp_project, a.module, a.method_name)
    author_folder = read_draft(a.module, a.method_name, tmp_project).folder
    b_record = read_draft_run(b.module, b.method_name, tmp_project)
    assert b_record is not None

    口 = Step(step_num=1, name="Plan Remove draft",
             purpose="The draft folder, b's results computed from a, and pipe-p "
                     "are named; nothing is deleted")
    plan = plan_draft_delete(a.module, a.method_name, root=tmp_project)
    assert not plan.outputs_only
    assert [d.draft for d in plan.later_drafts] == [b_name]
    assert [p.pipeline_id for p in plan.pipelines] == ["pipe-p"]
    a_record = read_draft_run(a.module, a.method_name, tmp_project)
    assert a_record is not None
    a_time = draft_run_time(a_record)
    # The size's value is checked against the bytes Delete outputs frees in
    # test_a_draft_without_a_draft_run_plans_only_itself; here its form.
    sized = re.search(rf"its outputs \(({_SIZE})\)$", plan.deletes[0])
    assert sized, plan.deletes[0]
    a_size = sized.group(1)
    # The harness records no pipeline name, so the pipeline reads as its id.
    assert plan.deletes == (
        f"the draft and everything wfc keeps for it: its link to your method "
        f"folder, its draft-run record of {a_time} and its outputs ({a_size})",
        f"results of later drafts made from it: {b_name}",
        "draft results shown in pipelines: pipe-p (they read 'draft removed')",
    ), plan.deletes
    assert plan.keeps[0] == (
        f"your method folder and every file the draft used {author_folder} "
        f"(never touched — start a draft from it again any time)")
    # The harness registers a as v1 before drafting it.
    assert plan.keeps[1:] == ("registered versions and their recorded runs",)
    assert plan.hint == "Only want to free the results? Use Delete outputs."
    for line in (*plan.deletes, *plan.keeps, plan.hint):
        assert line in plan.message, plan.message
    assert plan.message.index("Deletes:") < plan.message.index("Keeps:")
    assert a_folder.is_dir()
    assert read_draft_run(b.module, b.method_name, tmp_project) == b_record

    口 = Step(step_num=2, name="Plan Delete outputs",
             purpose="a's draft run and b's results go, the draft is kept; its "
                     "token is not Remove draft's")
    outputs = plan_draft_delete(a.module, a.method_name, outputs_only=True,
                                root=tmp_project)
    assert outputs.outputs_only and outputs.draft_run is not None
    assert [d.draft for d in outputs.later_drafts] == [b_name]
    from wfc.execution.draft import _record_steps
    steps = len(_record_steps(a_record))
    assert steps > 0
    assert outputs.deletes == (
        f"the draft run of {a_time} and its results ({a_size}, {steps} "
        f"step{'' if steps == 1 else 's'})",
        f"results of later drafts made from it: {b_name}",
        "draft results shown in pipelines: pipe-p",
    ), outputs.deletes
    assert outputs.keeps == (
        "the draft itself (its script and method.yaml) — run it again any time",
        "registered version v1 and its recorded runs",
        f"your method folder {author_folder}",
    ), outputs.keeps
    assert outputs.hint == ("Want wfc to forget this draft's script and contract "
                            "too? Use Remove draft instead.")
    for line in (*outputs.deletes, *outputs.keeps, outputs.hint):
        assert line in outputs.message, outputs.message
    assert outputs.token != plan.token

    口 = Step(step_num=3, name="What goes changes after the plan",
             purpose="b's outputs are deleted through its own plan; a's Remove "
                     "draft plan no longer names b")
    applied = delete_draft_outputs_and_clear(
        b.module, b.method_name, tmp_project,
        planned=plan_draft_delete(b.module, b.method_name, outputs_only=True,
                                  root=tmp_project).token)
    assert applied.done_message == f"Deleted the draft run of {b_name}; the draft is kept."
    assert read_draft_run(b.module, b.method_name, tmp_project) is None

    口 = Step(step_num=4, name="The stale token is refused",
             purpose="Remove draft with the first plan's token deletes nothing "
                     "and says to plan it again")
    with pytest.raises(DraftDeletePlanChangedError, match="Nothing was deleted"):
        remove_draft_and_clear(a.module, a.method_name, tmp_project,
                               planned=plan.token)
    assert a_folder.is_dir()
    assert read_draft_run(a.module, a.method_name, tmp_project) is not None

    口 = Step(step_num=5, name="The fresh plan's token applies",
             purpose="The draft folder goes, the author's folder stays, pipe-p "
                     "records the clearing")
    fresh = plan_draft_delete(a.module, a.method_name, root=tmp_project)
    assert fresh.later_drafts == () and fresh.token != plan.token
    removed = remove_draft_and_clear(a.module, a.method_name, tmp_project,
                                     planned=fresh.token)
    assert removed.done_message == (f"Removed the draft of {a_name}. Your method "
                                    f"folder {author_folder} is untouched.")
    assert not a_folder.exists()
    assert author_folder.is_dir()
    with get_session() as s:
        row = s.get(PipelineRun, "pipe-p")
    assert row is not None and f"{a_name}: removed" in row.cleared_reason


def test_a_draft_without_a_draft_run_plans_only_itself(tmp_project, monkeypatch):
    """No draft run: Remove draft names the folder alone; Delete outputs deletes nothing."""
    from wfc.execution.draft_clearing import (
        delete_draft_outputs_and_clear,
        plan_draft_delete,
        size_label,
    )
    from wfc.registration import DraftNotFoundError

    _images(monkeypatch)
    scenario = _two_drafts()
    a = scenario.node_by_id("a")
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    folder = layout.draft_dir(tmp_project, a.module, a.method_name)
    first = plan_draft_delete(a.module, a.method_name, outputs_only=True,
                              root=tmp_project)
    before = _folder_bytes(folder)
    delete_draft_outputs_and_clear(a.module, a.method_name, tmp_project,
                                   planned=first.token)
    # The size the plan stated is what Delete outputs freed, measured on disk.
    freed = size_label(before - _folder_bytes(folder))
    assert re.search(rf"its results \({_SIZE},", first.deletes[0])
    assert f"its results ({freed}, " in first.deletes[0], (freed, first.deletes)

    plan = plan_draft_delete(a.module, a.method_name, root=tmp_project)
    assert plan.draft_run is None and plan.later_drafts == () and plan.pipelines == ()
    assert plan.deletes == ("the draft and everything wfc keeps for it: its link "
                            "to your method folder (it has no draft run)",)
    assert "no draft run" in plan.message, plan.message
    # Remove draft still deletes the draft folder; Delete outputs deletes nothing.
    assert plan.nothing_to_delete is False
    outputs = plan_draft_delete(a.module, a.method_name, outputs_only=True,
                                root=tmp_project)
    assert outputs.nothing_to_delete is True
    assert "nothing is deleted" in outputs.message, outputs.message
    # Applying it (the canvas route does; the CLI stops at the plan) says so.
    applied = delete_draft_outputs_and_clear(a.module, a.method_name, tmp_project,
                                             planned=outputs.token)
    name = qualified_draft(a.module, a.method_name)
    assert applied.done_message == (f"{name} had no draft run: nothing was deleted. "
                                    f"The draft is kept.")

    with pytest.raises(DraftNotFoundError):
        plan_draft_delete(a.module, "no_such_method", root=tmp_project)


def test_delete_outputs_names_several_registered_versions_as_a_range(
        tmp_project, monkeypatch):
    """A method registered three times keeps "v1..v3" under Delete outputs.

    The harness registers a as v1; its script then changes and it is
    registered again until it has three versions, through Registration.
    """
    from wfc.execution.draft_clearing import plan_draft_delete
    from wfc.registration import read_draft

    _images(monkeypatch)
    scenario = _two_drafts()
    a = scenario.node_by_id("a")
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    source = read_draft(a.module, a.method_name, tmp_project).folder
    assert register_versions(tmp_project, a.module, a.method_name, source,
                             count=3) == ["1", "2", "3"]

    plan = plan_draft_delete(a.module, a.method_name, outputs_only=True,
                             root=tmp_project)
    assert plan.keeps == (
        "the draft itself (its script and method.yaml) — run it again any time",
        "registered versions v1..v3 and their recorded runs",
        f"your method folder {source}",
    ), plan.keeps


def test_a_draft_of_a_never_registered_method_keeps_no_versions_line(
        tmp_project, tmp_path_factory):
    """A DRAFT method has no registered versions, so Keeps names none."""
    from wfc.execution.draft_clearing import plan_draft_delete
    from wfc.registration import add_draft

    register_test_method(tmp_project, module_name="mod",
                         method_dir=tmp_project / "methods" / "transform")
    folder = tmp_path_factory.mktemp("author") / "fresh"
    shutil.copytree(tmp_project / "methods" / "transform", folder)
    (folder / "transform.py").rename(folder / "fresh.py")
    add_draft("mod", folder, tmp_project)

    plan = plan_draft_delete("mod", "fresh", root=tmp_project)
    assert plan.keeps == (
        f"your method folder and every file the draft used {folder.resolve()} "
        f"(never touched — start a draft from it again any time)",), plan.keeps


def test_an_unreadable_version_list_still_keeps_a_versions_line(tmp_project, monkeypatch):
    """A version list that cannot be read still names the registered versions as kept.

    The plan cannot say which versions there are, so both modes keep the
    generic line Remove draft uses rather than dropping it.
    """
    from wfc.execution.draft_clearing import plan_draft_delete

    _images(monkeypatch)
    scenario = _two_drafts()
    a = scenario.node_by_id("a")
    run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch)
    versions = layout.method_versions_path(tmp_project, a.module, a.method_name)
    versions.parent.mkdir(parents=True, exist_ok=True)
    versions.write_text("not a version list {", encoding="utf-8")

    generic = "registered versions and their recorded runs"
    for outputs_only in (False, True):
        plan = plan_draft_delete(a.module, a.method_name, outputs_only=outputs_only,
                                 root=tmp_project)
        assert generic in plan.keeps, plan.keeps
        assert generic in plan.message, plan.message
