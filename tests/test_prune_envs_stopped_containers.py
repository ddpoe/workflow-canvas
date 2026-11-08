"""prune-envs removes this project's labelled containers that are not running and whose maker is gone.

A copy-out container (project and creator labels) a crash left, created
and never started, is listed and removed by a plain remove; one whose
creator lives is not. An exited container of an ended run is removed; a
created container of a running run is not. Another project's and an
unlabelled container are never listed. A container that started between
the plan and the remove is refused by Docker and reported, never forced.
The daemon and the process table are the leftovers fakes.
"""

from __future__ import annotations

from axiom_annotations import workflow

from tests.fixtures.fakes import fake_build_processes, fake_leftover_daemon
from wfc.environments import apply_prune, container_labels, leftover_counts, plan_prune
from wfc.environments.containers import CREATOR_LABEL, creator_label, project_label


def _copy_out_labels(root, process) -> dict[str, str]:
    """The labels copy_out's container carries, naming *process* as its creator."""
    return {"wfc.project": project_label(root),
            CREATOR_LABEL: creator_label(process.pid, process.created)}


def _ended(pipeline_id, run_id) -> bool:
    return False


@workflow(purpose="A created copy-out container whose creating wfc process is "
                  "gone is listed apart from the containers to stop and removed "
                  "by a plain remove; one whose creator lives is left alone")
def test_a_copy_out_container_whose_creator_is_gone_is_removed(tmp_path, monkeypatch):
    """Creator gone: planned under "To remove" and removed; creator alive: kept."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    gone, alive = procs.wfc("register-env", "a"), procs.wfc("register-env", "b")
    procs.kill(gone)
    left = daemon.create(_copy_out_labels(project, gone), name="wfc-hardened-left")
    held = daemon.create(_copy_out_labels(project, alive), name="wfc-hardened-held")

    plan = plan_prune(project, run_running=_ended)

    assert [c.id for c in plan.unfinished] == [left] and plan.containers == ()
    lines = plan.lines()
    assert "To remove: 1 container wfc made that never finished." in lines
    assert not any("docker " in line for line in lines)
    result = apply_prune(plan)
    assert [c.id for c in result.removed] == [left] and not result.errors
    assert daemon.removed == [left] and held in daemon.stopped
    assert ["rm", left] in daemon.calls


@workflow(purpose="An exited container of an ended run is removed; a created "
                  "container of a running run, another project's and an "
                  "unlabelled one are never listed")
def test_only_this_projects_containers_whose_run_ended_are_listed(tmp_path, monkeypatch):
    """Run label: listed only when that run ended; never another project's or unlabelled."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    fake_build_processes(monkeypatch)
    exited = daemon.create(container_labels(project, pipeline_id="p", run_id="ended"),
                           state="exited")
    daemon.create(container_labels(project, pipeline_id="p", run_id="live"))
    daemon.create(container_labels(tmp_path / "other", pipeline_id="p", run_id="ended"),
                  state="exited")
    daemon.create({}, name="someone-elses", state="exited")

    def running(pipeline_id, run_id):
        return run_id == "live"

    plan = plan_prune(project, run_running=running, pipeline_ids={"p"})

    assert [c.id for c in plan.unfinished] == [exited]
    counts = leftover_counts(project, run_running=running, pipeline_ids={"p"},
                             stop_only=True)
    assert (counts.containers, counts.unfinished, counts.to_clear) == (0, 1, 1)
    assert apply_prune(plan).removed[0].id == exited
    assert not [c for c in daemon.calls if c[0] == "inspect"]


@workflow(purpose="A container that started between the plan and the remove is "
                  "refused by Docker and reported as left alone, never force-removed")
def test_a_container_that_started_since_the_plan_is_reported_not_forced(
        tmp_path, monkeypatch):
    """The plain remove is refused; the result reports it and exits 0."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    gone = procs.wfc("register-env", "a")
    procs.kill(gone)
    cid = daemon.create(_copy_out_labels(project, gone), name="wfc-hardened-x")
    plan = plan_prune(project, run_running=_ended)

    def start_before_the_remove(args):
        if args[0] == "rm" and cid in daemon.stopped:
            daemon.start_created(cid)

    daemon.on_call = start_before_the_remove
    result = apply_prune(plan)

    assert result.removed == [] and result.errors == [] and result.exit_code == 0
    assert any("started since the plan" in why for why in result.skipped), result.skipped
    assert cid in daemon.running
    assert not any("--force" in c or "-f" in c for c in daemon.calls)


def test_the_footer_and_the_row_count_a_container_to_remove(tmp_project, monkeypatch, capsys):
    """Task 24's equality with such a container: footer and row = the dry run's."""
    from wfc.cli import cli_main
    from wfc.execution import check_leftovers

    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    gone = procs.wfc("register-env", "a")
    procs.kill(gone)
    daemon.create(_copy_out_labels(tmp_project, gone), name="wfc-hardened-left")
    capsys.readouterr()
    assert cli_main(["prune-envs", "--dry-run"]) == 0
    assert "To remove: 1 container wfc made that never finished." in capsys.readouterr().out

    daemon.calls.clear()
    assert cli_main(["list-envs"]) == 0
    footer = capsys.readouterr().out.rstrip().splitlines()[-1]
    assert footer == ("Left by an earlier run or crash: 1 unfinished wfc container to "
                      "remove. Run `wfc prune-envs` to see them and remove them.")
    row = check_leftovers(tmp_project)
    assert (row.status, row.message) == (
        "warn", "Left by an earlier run or crash: 1 unfinished wfc container to remove.")
    assert not [c for c in daemon.calls if c[0] in ("images", "inspect")
                or c[:2] == ["system", "df"]]
