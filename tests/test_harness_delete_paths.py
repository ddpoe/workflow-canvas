"""The harness's ``Behavior(delete_paths=...)`` deletes a project path or fails naming it."""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.harness import Behavior, Scenario, node, run_scenario, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import DEFAULT_MODULE
from wfc.execution.draft import read_draft_run
from wfc.layout import method_versions_path
from wfc.registration import read_draft


def test_a_draft_step_finds_the_project_root_though_its_folder_is_outside(git_project,
                                                                        monkeypatch):
    """A draft whose folder lies outside the project still deletes under the project root.

    The draft's launch runs a copy of the author's folder from the draft's run
    area inside the project, and the generated script walks up from there: the
    delete landing shows the root was found for a draft step.
    """
    victim = method_versions_path(Path("."), DEFAULT_MODULE, "tail").as_posix()
    scenario = Scenario(nodes=[
        selector(),
        node("head", inputs=[wire("sel")],
             behavior=Behavior(delete_paths=(victim,))),
        node("tail", inputs=[wire("head")]),
    ], drafts=("head",))
    # A draft step checks its env image is runnable before it launches.
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch)
    head = scenario.node_by_id("head")
    root = Path(git_project).resolve()
    folder = read_draft(head.module, head.method_name, root).folder.resolve()
    assert not folder.is_relative_to(root), folder
    # A draft step's outcome is kept on the draft's run record, not a run row.
    record = read_draft_run(head.module, head.method_name, root)
    assert record["steps"]["head"]["s1"]["status"] == "completed", (
        obs.runs[("head", "s1", "default")].stderr_log.read_text(encoding="utf-8"))
    assert not (root / victim).exists()


def test_a_delete_paths_entry_naming_no_path_fails_the_method(git_project, monkeypatch):
    """An entry that names nothing under the project root exits non-zero naming it."""
    scenario = Scenario(nodes=[
        selector(),
        node("head", inputs=[wire("sel")],
             behavior=Behavior(delete_paths=("no/such/path.txt",))),
    ])
    obs = run_scenario(scenario, root=git_project, monkeypatch=monkeypatch)
    [head_row] = obs.rows_for_node("head")
    assert head_row["status"] == "failed", head_row
    log = obs.runs[("head", "s1", "default")].stderr_log
    assert "'no/such/path.txt' names no path under the project root" in log.read_text(
        encoding="utf-8")
