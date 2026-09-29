"""Layout unit: one diverse end-to-end path.

A uniform happy-path suite is structurally unable to trip this unit's defect
class. Every other scenario test builds its project at pytest's ``tmp_path``
and lets the harness pin ``WFC_PROJECT_ROOT`` to it, so the run never
resolves a root at all and a resolver that silently fell back to the cwd
would look identical to one that worked.

This test drives a real run the hostile way: the environment
override unset, the working directory somewhere below the root, and the
root itself carrying a space (and, on Windows, a drive letter). Silent cwd
fallback and separator leakage are what it is here to catch.

The root name is parametrized so the space is a *variable* rather than a
special case: ``plainroot`` is the control. If both cells fail the space is
innocent; if only ``wf canvas proj`` fails, the space is the cause — a
single hostile cell could not tell you which.

Requirement: ``docs/system/layout.json``, section ``testing.requirements``
("One diverse end-to-end path").
"""
from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.harness import Scenario, build_project, drive_target
from wfc.persistence import reset_engine

TARGET = ("n1", "s1", "default")


@pytest.mark.parametrize("root_name", ["plainroot", "wf canvas proj"])
@workflow(purpose="A run driven from a nested cwd with no WFC_PROJECT_ROOT "
                  "resolves its own root, writes every path under that root, "
                  "and leaves the invocation directory untouched — including "
                  "when the root's path contains a space",
          inputs="A root directory name, plain or space-bearing",
          outputs="A completed one-node run whose recorded paths are all "
                  "under the resolved root")
def test_run_from_hostile_root(root_name, tmp_path, monkeypatch):
    """Drive a one-node run from a root the harness did not pin.

    ``tmp_path`` supplies the drive letter on Windows, so the root is
    ``C:\\...\\wf canvas proj`` there. On POSIX that clause of the hostile
    shape is vacuous — the space and the nested cwd still apply, and the
    test is meaningful on both platforms rather than skipped on one.
    """
    口 = Step(step_num=1, name="Build the project at the hostile root",
             purpose="build_project is the only door that constructs a "
                     "project, so the tree under test is one production "
                     "wrote rather than one the test hand-assembled",
             outputs="A one-node project rooted at a path that may carry a "
                     "space")
    root = tmp_path / root_name
    project = build_project(Scenario(), root=root, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Take the environment away and move the cwd",
             purpose="build_project pinned WFC_PROJECT_ROOT and chdir'd to "
                     "the root; undoing both is what forces the run to "
                     "resolve a root for itself, which is the whole subject",
             outputs="No WFC_PROJECT_ROOT, cwd at <root>/methods",
             critical="reset_engine() is load-bearing, not hygiene: the "
                      "per-process cache still holds the root build_project "
                      "resolved, so without it the upward walk never runs "
                      "and this test passes vacuously")
    invocation_cwd = project.root / "methods"
    monkeypatch.delenv("WFC_PROJECT_ROOT")
    monkeypatch.chdir(invocation_cwd)
    reset_engine()

    口 = Step(step_num=3, name="Drive the target",
             purpose="drive_target runs an already-built project, so the "
                     "run happens under the environment this test arranged "
                     "rather than the one the harness would pin",
             outputs="The observation bundle for a completed run")
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)

    assert obs.exit_code(TARGET) == 0, "the hostile-root run did not complete"

    口 = Step(step_num=4, name="The cwd-fallback tripwire",
             purpose="The claim mkdirs the run's archive under .runs/ at "
                     "whatever root it "
                     "resolved, so a .runs/ appearing beside the invocation "
                     "directory is a resolver that fell back to the cwd",
             critical="This is the assertion the whole arrangement exists "
                      "to make possible")
    assert not (invocation_cwd / ".runs").exists(), (
        f"a .runs/ directory was created under the invocation cwd "
        f"{invocation_cwd} — the run resolved its root to the cwd instead "
        f"of walking up to {project.root}"
    )

    口 = Step(step_num=5, name="Every recorded path sits under the root",
             purpose="A path recorded against the wrong root is the same "
                     "defect the tripwire catches, seen from the database "
                     "side rather than the filesystem side",
             inputs="The run's archive directory and its RunOutput rows",
             outputs="Confirmation that the space survived into every "
                     "recorded path")
    from wfc.persistence import project_root as get_project_root
    from wfc.layout import run_archive_dir

    run_id = obs.runs[TARGET].run_id
    recorded = [run_archive_dir(get_project_root(), run_id)]
    recorded += [row["artifact_path"] for row in obs.output_rows
                 if row["run_id"] == run_id and row["artifact_path"]]
    assert len(recorded) > 1, "the run recorded no output rows to check"

    for path in recorded:
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(project.root), (
            f"recorded path {resolved} is not under the resolved root "
            f"{project.root}"
        )
        assert root_name in str(resolved), (
            f"recorded path {resolved} lost the root directory name "
            f"{root_name!r} — a space-bearing segment was mangled"
        )

    口 = Step(step_num=6, name="Container-side values stay POSIX under /work",
             purpose="The host root is bind-mounted at /work, so a host path "
                     "reaching the container untranslated — or translated "
                     "with backslashes — is separator leakage",
             inputs="The docker argv the dispatch phase built",
             critical="The translation itself is witnessed by "
                      "test_run_step_container_dispatch.py::"
                      "test_run_step_translates_wfc_paths_for_container; "
                      "what is new here is a space-bearing root surviving it")
    cmd = obs.runs[TARGET].dispatch_cmd
    assert cmd is not None, "the dispatch phase built no container argv"
    forwarded = dict(
        cmd[i + 1].split("=", 1)
        for i in range(len(cmd) - 1)
        if cmd[i] == "-e" and "=" in cmd[i + 1]
    )

    assert "WFC_RUN_DIR" in forwarded, f"WFC_RUN_DIR was not forwarded: {cmd}"
    run_dir = forwarded["WFC_RUN_DIR"]
    assert run_dir.startswith("/work"), (
        f"WFC_RUN_DIR reached the container as {run_dir!r} — a host path, "
        f"not the /work-relative translation"
    )
    assert "\\" not in run_dir, (
        f"WFC_RUN_DIR carries backslashes into the container: {run_dir!r}"
    )
