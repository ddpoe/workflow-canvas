"""Choosing the stricter integrity check never changes a cache key.

``[integrity] inputs`` decides how Storage checks what a step reads; it is
read through the config reader by the check alone and never reaches the
key recipe or a Run column. Switching it keeps every key, so the next
launch reuses every hit it reused before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.harness import Behavior, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout

HEAD = ("head", "S1", "default")


def _set_inputs(root: Path, original: str, setting: str | None) -> None:
    """Write ``[integrity] inputs`` (or no section) into the config and commit it."""
    marker = layout.marker_path(root)
    section = "" if setting is None else f'\n[integrity]\ninputs = "{setting}"\n'
    marker.write_text(original + section, encoding="utf-8")
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-am", f"inputs {setting}"],
                   cwd=root, check=True)


@workflow(
    purpose="the [integrity] inputs setting never enters a cache key: one "
            "step's key composed with the setting unset, stat-then-hash and "
            "hash is the same key its run recorded, and a launch after "
            "switching to hash is a cache hit on that run",
    inputs="a one-step pipeline over a sample, run once with no [integrity] "
           "section, then keyed under each setting and launched under hash",
    outputs="three identical composed keys equal to the recorded key, and a "
            "cache-hit audit row naming the first run",
)
def test_switching_the_integrity_setting_keeps_every_key_and_every_hit(
        tmp_path, monkeypatch):
    from wfc.execution.claim import candidate_cache_key
    from wfc.storage.integrity import inputs_setting

    _ = Step(step_num=1, name="Run the step with the setting unset",
             purpose="The run records the key composed under the default")
    project = build_project(
        Scenario(nodes=[selector(), node("head", inputs=[wire(SELECTOR_ID)],
                                         behavior=Behavior(echo_input="data"))],
                 samples=["S1"]),
        root=tmp_path / "proj", monkeypatch=monkeypatch)
    original = layout.marker_path(project.root).read_text(encoding="utf-8")
    assert "[integrity]" not in original
    first = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert first.exit_code(HEAD) == 0
    recorded = first.run_row(HEAD)

    _ = Step(step_num=2, name="Compose the step's key under each setting",
             purpose="The recipe the claim phase uses, with the setting unset, "
                     "stat-then-hash and hash")
    keys = {}
    for setting in (None, "stat-then-hash", "hash"):
        _set_inputs(project.root, original, setting)
        assert inputs_setting(project.root) == (setting or "stat-then-hash")
        keys[setting] = candidate_cache_key("head", "harness_mod", "S1")
    assert set(keys.values()) == {recorded["cache_key"]}, keys

    _ = Step(step_num=3, name="Launch again under hash",
             purpose="The claim finds the first run under the same key and "
                     "serves it as a hit")
    again = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert again.exit_code(HEAD) == 0
    hit = again.run_row(HEAD)
    assert hit["id"] != recorded["id"]
    assert hit["cache_key"] == recorded["cache_key"]
    assert hit["cache_source_run_id"] == recorded["id"]
