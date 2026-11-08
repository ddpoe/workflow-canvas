"""The stop-only count behind ``wfc list-envs``' footer and the doctor's leftovers row.

The footer and the row ask Environments only for what there is to stop: the
run containers whose runs have ended and the orphaned env builds. Neither
lists the env images nor asks for the build cache's size (``wfc prune-envs``
still does), and a running container's labels decide it without an inspect;
only a container with no wfc label, which the labels cannot place, is
inspected for its variables. The daemon fake records every Docker call;
Execution's liveness is the production one.
"""

from __future__ import annotations

import logging
import re

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    fake_build_processes,
    fake_leftover_daemon,
    stub_docker_image_inspect,
)
from wfc.cli import cli_main
from wfc.environments import container_labels, leftover_counts


def _kept_queries(calls: list[list[str]]) -> list[list[str]]:
    """The calls that list the env images or ask for the disk usage."""
    return [c for c in calls if c[0] == "images" or c[:2] == ["system", "df"]]


def _inspected(calls: list[list[str]]) -> set[str]:
    """Every container id an inspect asked about."""
    return {cid for c in calls if c[0] == "inspect" for cid in c[1:]}


def _a_project_with_leftovers(root, monkeypatch):
    """An ended run's container, a live pipeline's, bystanders, an orphaned build."""
    from wfc import layout
    from wfc.execution.pipeline_record import ENTRY_POINT_CLI, write_pipeline_record

    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    labelled = [
        daemon.start(container_labels(root, pipeline_id="p-ended", run_id="7"),
                     name="step-7"),
        daemon.start(container_labels(root.parent / "other", pipeline_id="p-ended",
                                      run_id="7"), name="other-project"),
        daemon.start(container_labels(root), name="wfc-shell"),
    ]
    (layout.pipelines_dir(root) / "p-live").mkdir(parents=True)
    write_pipeline_record(root, "p-live", ENTRY_POINT_CLI)  # owned by this process
    labelled.append(daemon.start(container_labels(root, pipeline_id="p-live", run_id="8"),
                                 name="step-8"))
    unlabelled = {
        daemon.start(variables={"WFC_PIPELINE_ID": "p-elsewhere", "WFC_RUN_ID": "3"},
                     name="someone-elses"),
        daemon.start(name="postgres"),
    }
    procs.build(root / ".wfc" / "build" / "alpha", "alpha")
    daemon.images = [("local/alpha", "sha256:" + "a" * 64)]
    daemon.cache_size = "1.5GB"
    return daemon, labelled, unlabelled


@workflow(purpose="list-envs' footer and the doctor's leftovers row count what "
                  "prune-envs --dry-run lists to stop (one ended run's "
                  "container, one orphaned build) without listing the env "
                  "images or asking for the disk usage, and inspect only the "
                  "containers with no wfc label; prune-envs --dry-run still "
                  "shows the image and build-cache counts")
def test_the_footer_and_the_row_ask_only_for_what_there_is_to_stop(
        tmp_project, monkeypatch, capsys):
    """The footer's and the row's Docker calls, and their counts against the plan's."""
    from wfc.execution import check_leftovers

    daemon, labelled, unlabelled = _a_project_with_leftovers(tmp_project, monkeypatch)
    stub_docker_image_inspect(monkeypatch, RuntimeError("Cannot connect to the Docker daemon"))

    口 = Step(step_num=1, name="What prune-envs --dry-run lists",
             purpose="One run container and one env build to stop, and the image "
                     "and build-cache counts it keeps")
    capsys.readouterr()
    assert cli_main(["prune-envs", "--dry-run"]) == 0
    plan = capsys.readouterr().out
    to_stop = {kind: int(n) for kind, n in
               re.findall(r"^(Run containers|Env builds) to stop \((\d+)\)", plan, re.M)}
    assert to_stop == {"Run containers": 1, "Env builds": 1}, plan
    assert "Env images no env or method version uses: 0" in plan
    assert "Build cache: 1.5 GB" in plan

    口 = Step(step_num=2, name="list-envs' footer",
             purpose="The same counts, naming wfc prune-envs; no image listing, "
                     "no disk-usage query, no inspect of a labelled container")
    daemon.calls.clear()
    assert cli_main(["list-envs"]) == 0
    footer = capsys.readouterr().out.rstrip().splitlines()[-1]
    assert footer == ("Left running by an earlier run or crash: 1 run container to "
                      "stop, 1 env build to stop. Run `wfc prune-envs` to see them "
                      "and stop them."), footer
    assert _kept_queries(daemon.calls) == [], daemon.calls
    assert _inspected(daemon.calls) == unlabelled
    assert not _inspected(daemon.calls) & set(labelled)

    口 = Step(step_num=3, name="The doctor's leftovers row",
             purpose="A warn with the same counts and wfc prune-envs; the same "
                     "calls only")
    daemon.calls.clear()
    row = check_leftovers(tmp_project)
    assert (row.status, row.message) == (
        "warn", "Left running by an earlier run or crash: 1 run container to stop, "
                "1 env build to stop."), row
    assert "wfc prune-envs" in row.fix_hint
    assert _kept_queries(daemon.calls) == [], daemon.calls
    assert _inspected(daemon.calls) == unlabelled


def test_a_stop_only_count_inspects_only_what_the_labels_cannot_place(tmp_path, monkeypatch):
    """Labels decide a labelled container; an unlabelled one is inspected only with pipeline ids."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    fake_build_processes(monkeypatch)
    daemon.start(container_labels(project, pipeline_id="p-1", run_id="7"), name="step-7")
    unlabelled = daemon.start(variables={"WFC_PIPELINE_ID": "p-2", "WFC_RUN_ID": "3"},
                              name="old-step")

    def ended(pipeline_id, run_id):
        return False

    counts = leftover_counts(project, run_running=ended, stop_only=True)
    assert (counts.containers, counts.builds) == (1, 0)
    assert (counts.unused_images, counts.cache_bytes) == (None, None)
    assert [c[0] for c in daemon.calls] == ["ps"], daemon.calls

    daemon.calls.clear()
    counts = leftover_counts(project, run_running=ended, pipeline_ids={"p-2"},
                             stop_only=True)
    assert counts.containers == 2
    assert [c for c in daemon.calls if c[0] != "ps"] == [["inspect", unlabelled]]


def test_the_footer_logs_the_count_it_could_not_make(tmp_project, monkeypatch, capsys, caplog):
    """A count that raises prints no footer, and the error is logged at debug."""
    from wfc.environments.verbs import list_envs

    def broken(project_dir):
        raise RuntimeError("the process table could not be read")

    with caplog.at_level(logging.DEBUG, logger="wfc.environments.verbs"):
        assert list_envs(leftovers=broken) == 0
    assert "prune-envs" not in capsys.readouterr().out
    [record] = [r for r in caplog.records if r.name == "wfc.environments.verbs"]
    assert record.levelno == logging.DEBUG and record.exc_info is not None
    assert "the process table could not be read" in str(record.exc_info[1])
