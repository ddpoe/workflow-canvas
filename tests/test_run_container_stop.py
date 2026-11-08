"""Run containers carry labels, and wfc stops them by label on Ctrl-C.

Case 1: a step's ``docker run`` carries the project, pipeline and run labels
and the invocation record (which ``command.sh`` is rendered from) is the
same with or without them. Case 2: Ctrl-C while ``wfc try``'s step runs
stops the containers carrying that run's labels, and the draft step ends
cancelled. Case 10 for the stop: its messages name no Docker command. The
daemon is the registered ``fake_container_daemon`` model at Environments'
docker edge.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import fake_container_daemon, interrupt_labelled_launch
from tests.fixtures.fakes.containers import argv_labels
from wfc.environments import (
    PIPELINE_LABEL,
    PROJECT_LABEL,
    RUN_LABEL,
    build_docker_command,
    container_labels,
    project_label,
    stop_containers,
)
from wfc.environments.docker import DockerTimeoutError

IMAGE = "sha256:" + "a" * 64

#: A Docker command named in a message (case 10 forbids it).
_DOCKER_COMMAND = re.compile(r"\bdocker\s+(ps|kill|run|rm|stop|container|image|build|buildx)\b",
                             re.IGNORECASE)


def test_a_step_argv_carries_its_labels_and_the_record_is_unchanged(tmp_path):
    """Case 1: the three labels are on the argv; the invocation record ignores them."""
    from wfc import layout
    from wfc.execution.dispatch import _invocation_record

    root = tmp_path / "proj"
    cache = root / ".dvc" / "cache"
    cache.mkdir(parents=True)
    labels = container_labels(root, pipeline_id="p-1", run_id="17")
    plain = build_docker_command(IMAGE, root, cache, ["python", "/work/x.py"], uid=1, gid=2)
    labelled = build_docker_command(IMAGE, root, cache, ["python", "/work/x.py"],
                                    uid=1, gid=2, labels=labels)

    assert argv_labels(labelled) == {PROJECT_LABEL: project_label(root),
                                     PIPELINE_LABEL: "p-1", RUN_LABEL: "17"}
    first = labelled.index("--label")
    assert labelled[:first] + labelled[first + 2 * len(labels):] == plain
    assert first < labelled.index(IMAGE)
    mounts = layout.mount_table(root, cache)
    record = {"image_ref": IMAGE, "interpreter": "python", "script": "/work/x.py",
              "mounts": mounts, "copy_writable": False, "env_flags": ["-e", "A=1"],
              "uid": 1, "gid": 2, "gpus": False}
    assert _invocation_record(**record, cmd=labelled) == _invocation_record(**record, cmd=plain)
    assert "wfc.run" not in repr(_invocation_record(**record, cmd=labelled))


def test_the_project_label_is_one_hash_of_the_resolved_root(tmp_path, monkeypatch):
    """Two spellings of one root give one label; another root gives another."""
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    value = project_label(root)
    assert re.fullmatch(r"[0-9a-f]{12}", value)
    assert project_label("proj") == value == project_label(root / "sub" / "..")
    assert project_label(tmp_path / "other") != value


def test_a_stop_kills_only_containers_carrying_every_label(tmp_path, monkeypatch):
    """The run's container goes; its pipeline's other run and another project's stay."""
    root, other = tmp_path / "a", tmp_path / "b"
    daemon = fake_container_daemon(monkeypatch)
    mine = daemon.start(container_labels(root, pipeline_id="p", run_id="1"))
    sibling = daemon.start(container_labels(root, pipeline_id="p", run_id="2"))
    foreign = daemon.start(container_labels(other, pipeline_id="p", run_id="1"))
    dev = daemon.start(container_labels(root))

    stop = stop_containers(container_labels(root, pipeline_id="p", run_id="1"))
    assert stop.stopped == (mine,) and stop.error is None
    assert set(daemon.running) == {sibling, foreign, dev}

    stop = stop_containers(container_labels(root, pipeline_id="p"))
    assert stop.stopped == (sibling,) and stop.error is None
    assert set(daemon.running) == {foreign, dev}
    assert stop_containers({RUN_LABEL: "1"}).error is not None
    assert foreign in daemon.running


@pytest.mark.parametrize("failure", [
    DockerTimeoutError("listing wfc's containers did not finish within 15 s: "
                       "Docker is not answering"),
    FileNotFoundError(2, "The system cannot find the file specified"),
    "Cannot connect to the Docker daemon. Is the daemon running?",
], ids=["timeout", "no-client", "refused"])
def test_a_stop_that_cannot_finish_says_why_without_a_docker_command(
        tmp_path, monkeypatch, failure):
    """Case 10: a failed stop never raises and its message names no Docker command."""
    daemon = fake_container_daemon(monkeypatch)
    daemon.failure = failure

    stop = stop_containers(container_labels(tmp_path, run_id="9"))

    assert stop.stopped == () and stop.error
    assert not _DOCKER_COMMAND.search(stop.error), stop.error


@workflow(purpose="Case 2 through wfc try: Ctrl-C while the draft step's "
                  "container runs stops the containers carrying that run's "
                  "project, pipeline and run labels, leaves another run's "
                  "container of the project running, lets the interrupt "
                  "through, and the draft step ends cancelled")
def test_ctrl_c_in_try_stops_the_runs_container_and_the_step_ends_cancelled(
        tmp_project, monkeypatch, capsys):
    from tests.fixtures.draft_project import built_draft_project
    from tests.test_cli_try import _try_argv, _write_folder
    from wfc.cli import cli_main
    from wfc.execution.draft import read_draft_run

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    daemon = fake_container_daemon(monkeypatch)
    bystander = daemon.start(container_labels(tmp_project, pipeline_id="other",
                                              run_id="other-run"))
    capsys.readouterr()

    口 = Step(step_num=1, name="Interrupt the try while its container runs",
             purpose="The launch starts the labelled container, then the wait "
                     "raises KeyboardInterrupt")
    launched = interrupt_labelled_launch(monkeypatch, daemon)
    with pytest.raises(KeyboardInterrupt):
        cli_main(_try_argv(folder, module, up_run, mid_ref))

    口 = Step(step_num=2, name="The run's container was stopped by its labels",
             purpose="Exactly the container the launch labelled is killed; "
                     "the other run's container is still running")
    record = read_draft_run(module, "fresh", tmp_project)
    labels = argv_labels(launched[0])
    assert labels[PROJECT_LABEL] == project_label(tmp_project)
    assert labels[PIPELINE_LABEL] == record["pipeline_id"]
    assert labels[RUN_LABEL]
    assert len(daemon.killed) == 1 and daemon.killed[0] not in daemon.running
    assert list(daemon.running) == [bystander]
    listing = daemon.calls[0]
    assert listing[0] == "ps"
    assert {f"label={k}={v}" for k, v in labels.items()} <= set(listing)

    口 = Step(step_num=3, name="The run ends cancelled",
             purpose="The draft step is cancelled, nothing names a Docker command")
    assert record["steps"]["fresh"]["s1"]["status"] == "cancelled"
    out = capsys.readouterr()
    assert not _DOCKER_COMMAND.search(out.out + out.err)


def test_an_interrupted_launch_whose_stop_fails_still_raises_the_interrupt(
        tmp_project, monkeypatch, capsys):
    """A stop that cannot finish is printed, without a Docker command; Ctrl-C still ends the try."""
    from tests.fixtures.draft_project import built_draft_project
    from tests.test_cli_try import _try_argv, _write_folder
    from wfc.cli import cli_main

    scenario, module, up_run, mid_ref = built_draft_project(tmp_project, monkeypatch)
    folder = _write_folder(tmp_project.parent / f"{tmp_project.name}-tried",
                           "fresh", scenario.env_name)
    daemon = fake_container_daemon(monkeypatch)
    interrupt_labelled_launch(monkeypatch, daemon)
    daemon.failure = DockerTimeoutError(
        "listing wfc's containers did not finish within 15 s: Docker is not answering")
    capsys.readouterr()

    with pytest.raises(KeyboardInterrupt):
        cli_main(_try_argv(folder, module, up_run, mid_ref))

    err = capsys.readouterr().err
    assert "container may still be running" in err
    assert not _DOCKER_COMMAND.search(err)
