"""Ctrl-C in ``wfc run-pipeline`` stops the pipeline's containers.

A step container outlives the engine that started it: Docker does not stop a
container when its client goes. When an interrupt ends ``run_pipeline``, it
asks Environments to stop every container carrying this project's label and
the pipeline's label, then lets the interrupt through unchanged and leaves
the pipeline record for the recovery, as before. The engine is the mocked
Snakemake process; the daemon is the registered ``fake_container_daemon``
model at Environments' docker edge.
"""

from __future__ import annotations

import re

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import fake_container_daemon, stub_docker_image_inspect
from tests.harness import Scenario, build_project, node, selector, wire
from wfc.environments import container_labels
from wfc.execution.pipeline_record import read_pipeline_record

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731

#: A Docker command named in a message (case 10 forbids it).
_DOCKER_COMMAND = re.compile(r"\bdocker\s+(ps|kill|run|rm|stop|container|image|build|buildx)\b",
                             re.IGNORECASE)


def _chain(pid: str) -> Scenario:
    """sel -> a -> b over two samples."""
    return Scenario(
        nodes=[selector(), node("a", inputs=[wire("sel")]), node("b", inputs=[wire("a")])],
        samples=["S1", "S2"], pipeline_id=pid,
    )


def _interrupt_while_running(project, pid: str, during_engine) -> None:
    """Run ``run_pipeline`` with an engine whose wait is interrupted.

    Args:
        project: The built scenario project.
        pid: The pipeline id.
        during_engine: Called while the engine runs (to start its step
            containers), just before the wait raises ``KeyboardInterrupt``.
    """
    from wfc.execution import run_pipeline

    def _wait(*_args, **_kwargs):
        during_engine()
        raise KeyboardInterrupt

    gen_patch, popen_patch = mocked_snakemake(1)
    popen_patch.kwargs["return_value"].wait.side_effect = _wait
    with gen_patch, popen_patch, pytest.raises(KeyboardInterrupt):
        run_pipeline(pipeline_path=str(project.pipeline_json),
                     project_root=str(project.root), pipeline_id=pid)


@workflow(purpose="Ctrl-C while the engine runs a pipeline's step container: "
                  "run_pipeline stops the containers carrying the project's and "
                  "the pipeline's labels, leaves another pipeline's and another "
                  "project's running, lets the interrupt through and keeps the "
                  "pipeline record for the recovery")
def test_ctrl_c_in_run_pipeline_stops_the_pipelines_containers(
        git_project, tmp_path, monkeypatch, capsys):
    pid = "pipe-ctrl-c-stop"
    口 = Step(step_num=1, name="Build the project; three containers will run",
             purpose="The pipeline's step container, another pipeline's of this "
                     "project, and one of another project sharing the pipeline id")
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    daemon = fake_container_daemon(monkeypatch)
    started: dict[str, str] = {}

    def _engine_runs_steps() -> None:
        started["mine"] = daemon.start(
            container_labels(project.root, pipeline_id=pid, run_id="1"))
        started["other_pipeline"] = daemon.start(
            container_labels(project.root, pipeline_id="another", run_id="2"))
        started["other_project"] = daemon.start(
            container_labels(tmp_path / "elsewhere", pipeline_id=pid, run_id="1"))
    capsys.readouterr()

    口 = Step(step_num=2, name="Ctrl-C while the engine runs",
             purpose="The engine's wait raises KeyboardInterrupt, which "
                     "run_pipeline lets through unchanged")
    _interrupt_while_running(project, pid, _engine_runs_steps)

    口 = Step(step_num=3, name="Only the pipeline's container was stopped",
             purpose="The kill names exactly the container with this project's "
                     "and this pipeline's labels")
    assert daemon.killed == [started["mine"]]
    assert set(daemon.running) == {started["other_pipeline"], started["other_project"]}

    口 = Step(step_num=4, name="The record is still there",
             purpose="The recovery and wfc cancel still close the pipeline out")
    assert read_pipeline_record(project.root, pid) is not None
    out = capsys.readouterr()
    assert not _DOCKER_COMMAND.search(out.out + out.err)


def test_a_failed_stop_says_so_without_a_docker_command_and_ctrl_c_still_ends_it(
        git_project, monkeypatch, capsys):
    """Docker not answering the stop: one line naming no Docker command; the interrupt propagates."""
    pid = "pipe-ctrl-c-stop-fails"
    project = build_project(_chain(pid), root=git_project, monkeypatch=monkeypatch)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    daemon = fake_container_daemon(monkeypatch)

    def _docker_goes_away() -> None:
        daemon.start(container_labels(project.root, pipeline_id=pid, run_id="1"))
        daemon.failure = "Cannot connect to the Docker daemon. Is the daemon running?"
    capsys.readouterr()

    _interrupt_while_running(project, pid, _docker_goes_away)

    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if "may still be running" in line]
    assert len(lines) == 1 and pid in lines[0], err
    assert not _DOCKER_COMMAND.search(err)
    assert read_pipeline_record(project.root, pid) is not None
