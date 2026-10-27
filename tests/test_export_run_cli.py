"""``wfc export-run``: print the plan, refuse with every reason, ask about local-only images.

Every call runs the real CLI in process (the ``cli`` fixture). Runs come from
``tests.fixtures.routes.completed_run``: production's phases on the
harness's stub rung, whose env image was never pushed, so Environments'
``pullable_ref`` answers ``None`` and every step's image is one only this
machine holds. pytest's stdin is not a terminal, so the no-terminal refusal
needs no fake; the answer typed at a terminal goes through the registered
``stub_terminal`` (a signed use for this verb) and ``stub_interactive_prompt``
fakes.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_docker_image_inspect, stub_interactive_prompt, stub_terminal
from tests.fixtures.routes import completed_run
from tests.harness.scenario import DEFAULT_MODULE, DEFAULT_SAMPLE

PROCEED = "Proceed? [y/N]: "


def _old_run(method: str) -> int:
    """A completed run made through the route that records no invocation."""
    from wfc.execution.claim import register_run
    from wfc.execution.record import complete_run

    run_id = register_run(method, DEFAULT_MODULE, DEFAULT_SAMPLE)
    complete_run(run_id)
    return run_id


@workflow(purpose="A step whose image was never pushed is warned about and "
                  "asked about. With no terminal and no --yes the export is refused "
                  "naming --yes; answering no writes nothing; --yes writes the "
                  "bundle, one progress line per step and its size, and the bundle "
                  "and its README mark the step's image as never pushed",
          inputs="One completed run on the stub env, whose image has no push record",
          outputs="Three exit codes and one zip, written only under --yes")
def test_a_local_only_image_is_asked_about_and_only_yes_writes_the_bundle(
    cli, tmp_project, monkeypatch, tmp_path,
):
    """No terminal refuses, no writes nothing, --yes writes and marks."""
    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="local").run_id
    out = tmp_path / "bundle.zip"
    argv = ("export-run", "--run", str(run_id), "--out", str(out))

    口 = Step(step_num=1, name="No terminal, no --yes",
             purpose="pytest's own stdin is not a terminal: the plan and the warning "
                     "print, then the export is refused naming --yes")
    refused = cli(*argv)
    assert refused.returncode == 1, refused
    assert f"run {run_id}" in refused.stdout
    assert "only this machine holds" in refused.stdout
    assert "cannot be rerun or compared" in refused.stdout
    assert "--yes" in refused.stderr and "Nothing was written" in refused.stderr
    assert not out.exists()
    from wfc.run_bundle import plan_export
    shown = plan_export([run_id]).to_dict()
    assert len(shown["warning_lines"]) == 1, "one never-pushed image, one sentence"
    assert f"WARNING: {shown['warning_lines'][0]}" in refused.stdout, (
        "the CLI prints the plan's own warning text, the one the canvas shows")

    口 = Step(step_num=2, name="Answer no at the terminal",
             purpose="The question is asked once; no writes nothing")
    stub_terminal(monkeypatch, True)
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "n", prompts=prompts)
    declined = cli(*argv)
    assert declined.returncode == 1, declined
    assert prompts == [PROCEED]
    assert "only this machine holds" in declined.stdout
    assert "Nothing was written." in declined.stdout
    assert not out.exists()
    assert not list(tmp_path.glob(".bundle.zip.*")), "a temporary file was left"

    口 = Step(step_num=3, name="--yes",
             purpose="No question; one progress line per step, then the size; the "
                     "bundle and README mark the never-pushed image")
    prompts.clear()
    written = cli(*argv, "--yes")
    assert written.returncode == 0, written
    assert prompts == []
    assert out.is_file()
    with zipfile.ZipFile(out) as bundle:
        manifest = json.loads(bundle.read("wfc-run.json"))
        readme = bundle.read("README.md").decode("utf-8")
    (step,) = manifest["steps"]
    progress = [line for line in written.stdout.splitlines()
                if line.startswith("Wrote steps/")]
    assert progress == [f"Wrote steps/{step['folder']}"]
    assert f"{out.stat().st_size:,} bytes" in written.stdout
    assert step["run_id"] == run_id and step["env"]["pullable_ref"] is None
    assert "Images only the exporting machine holds" in readme
    assert f"(run {run_id}), was never pushed to a registry" in readme


@workflow(purpose="Under --with-image a never-pushed image is carried, not local-only: the "
                  "plan has no local-only warning (so the CLI asks nothing) and "
                  "export-run's plan lines do not call it local only; an image the daemon "
                  "holds under none of its references is a refusal naming the image and "
                  "how to get it back",
          inputs="One completed run on the stub env, whose image has no push record; "
                 "the daemon's image inspect answered by the registered fake",
          outputs="A plan with the image to save and no warning; a plan refused by name")
def test_with_image_carries_a_never_pushed_image_and_refuses_one_the_daemon_lacks(
    cli, tmp_project, monkeypatch, tmp_path,
):
    """A never-pushed image under --with-image, and the refusal when the daemon lacks it."""
    from wfc.environments.docker import ImageNotFoundError
    from wfc.run_bundle import plan_export

    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="carried").run_id

    口 = Step(step_num=1, name="The daemon holds the image",
             purpose="The plan saves it and warns of nothing; the printed plan line does "
                     "not call the image local only")
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    plan = plan_export([run_id], with_image=True)
    assert plan.ok, plan.refusals
    assert plan.local_only == () and plan.to_dict()["warnings"] == []
    assert len(plan.images) == 1 and plan.images[0].ref
    folder = tmp_path / "bundles"
    folder.mkdir()
    printed = cli("export-run", "--run", str(run_id), "--out", str(folder), "--with-image")
    assert printed.returncode == 1 and "--out names the directory" in printed.stderr
    assert f"run {run_id}" in printed.stdout
    assert "local only" not in printed.stdout
    # The CLI stops at the --out refusal before the warning would print; the
    # plan's empty local_only and warnings above are what witness no warning.

    口 = Step(step_num=2, name="The daemon holds none of its references",
             purpose="A refusal naming the image and the rebuild, and no image to save")
    stub_docker_image_inspect(monkeypatch, ImageNotFoundError("Error: No such image"))
    refused = plan_export([run_id], with_image=True)
    assert not refused.ok and refused.images == ()
    reasons = "\n".join(r.reason for r in refused.refusals)
    assert "--with-image: the image of env" in reasons
    assert "is not on this machine" in reasons and "wfc register-env" in reasons


@workflow(purpose="wfc export-run lists every reason the bundle cannot be complete, "
                  "exits non-zero and writes nothing: a run made before the "
                  "invocation record is named with the missing record")
def test_export_run_lists_every_refusal_and_writes_nothing(
    cli, tmp_project, monkeypatch, tmp_path,
):
    """A run made before the invocation record, through the CLI."""
    completed_run(tmp_project, monkeypatch=monkeypatch, method="legacy")
    old = _old_run("legacy")
    out = tmp_path / "old.zip"
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "y", prompts=prompts)

    result = cli("export-run", "--run", str(old), "--out", str(out), "--yes")

    assert result.returncode == 1, result
    assert f"Run {old} " in result.stderr and "no invocation record" in result.stderr
    assert "Nothing was written." in result.stderr
    assert prompts == [], "a refused export asks nothing"
    assert not out.exists()


@workflow(purpose="wfc export-run with --out naming a directory is refused: --out "
                  "names the zip file, and nothing is written into the directory")
def test_export_run_refuses_an_out_that_is_a_directory(
    cli, tmp_project, monkeypatch, tmp_path,
):
    """An --out that is a directory is refused (open question 9's default)."""
    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="dir_out").run_id
    folder = tmp_path / "bundles"
    folder.mkdir()

    result = cli("export-run", "--run", str(run_id), "--out", str(folder), "--yes")

    assert result.returncode == 1, result
    assert "--out names the directory" in result.stderr
    assert list(folder.iterdir()) == []
    assert not list(Path(tmp_path).glob(".bundles.*"))
