"""wfc prune-envs' plan and apply, and the leftover counts, in Environments.

Cases 7 and 8: the plan lists this project's leftover run containers (an
ended run's, labelled or found by its variables) and its orphaned env builds
(a ``docker.exe`` whose wfc parent died, a ``docker-buildx.exe`` whose
``docker.exe`` is gone), and never a running run's container, another
project's, a dev-loop container, or a build of another project's or a temp
dir's context. The apply re-checks, kills containers, and ends a build by
interrupting its client before anything is terminated, never ``docker.exe``
alone. Part of case 9: the counts, also with Docker unreachable. The daemon
and the process table are the registered fakes at Environments' docker and
psutil edges.
"""

from __future__ import annotations

import re
import signal

from axiom_annotations import workflow

from tests.fixtures.fakes import (
    fake_build_processes,
    fake_leftover_daemon,
    stub_posix_process_groups,
)
from wfc.environments import (
    apply_prune,
    container_labels,
    format_size,
    leftover_counts,
    plan_prune,
)
from wfc.environments.build_clients import (
    ORPHAN_INTERRUPT_WAIT_S,
    ProcessInfo,
    _build_target,
    _interrupt_client,
    _is_wfc_process,
    orphaned_builds,
)
from wfc.environments.docker import DockerTimeoutError

#: A Docker command named in a message (case 10 forbids it).
_DOCKER_COMMAND = re.compile(
    r"\bdocker\s+(ps|kill|run|rm|stop|container|image|images|build|buildx|system|inspect)\b",
    re.IGNORECASE)

#: Runs still running, by run id; every other run has ended.
_RUNNING = {"run-live"}


def _running(pipeline_id, run_id):
    return run_id in _RUNNING


def _build_dir(project, env):
    return project / ".wfc" / "build" / env


@workflow(purpose="Case 7: the plan lists an ended run's labelled container, "
                  "an unlabelled container of one of the project's pipelines "
                  "whose run ended, a docker.exe whose wfc parent died and a "
                  "docker-buildx.exe whose docker.exe is gone; the apply kills "
                  "both containers and ends both builds by their interrupt")
def test_the_plan_lists_this_projects_leftovers_and_the_apply_stops_them(
        tmp_path, monkeypatch):
    """Case 7 over faked listings."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    ended = daemon.start(container_labels(project, pipeline_id="p-1", run_id="7"),
                         name="step-7")
    unlabelled = daemon.start(variables={"WFC_PIPELINE_ID": "p-2", "WFC_RUN_ID": "3"},
                              name="old-step")
    dead_wfc = procs.wfc("register-env", "alpha")
    client_a, plugin_a = procs.build(_build_dir(project, "alpha"), "alpha", parent=dead_wfc)
    procs.kill(dead_wfc)
    live_wfc = procs.wfc("register-env", "beta")
    client_b, plugin_b = procs.build(_build_dir(project, "beta"), "beta", parent=live_wfc)
    procs.kill(client_b)

    plan = plan_prune(project, run_running=_running, pipeline_ids=["p-1", "p-2"])

    assert {c.id for c in plan.containers} == {ended, unlabelled}
    assert [(b.env, b.group_id, [p.pid for p in b.processes]) for b in plan.builds] == [
        ("alpha", client_a.pid, [plugin_a.pid, client_a.pid]),
        ("beta", client_b.pid, [plugin_b.pid])]
    text = "\n".join(plan.lines())
    assert "step-7 (pipeline p-1, run 7)" in text and "alpha (running for" in text
    assert not _DOCKER_COMMAND.search(text), text
    assert daemon.killed == [] and procs.events == []  # the plan stops nothing

    result = apply_prune(plan)

    assert set(daemon.killed) == {ended, unlabelled} and not daemon.running
    assert [(s.build.env, s.stopped, s.how) for s in result.builds] == [
        ("alpha", True, "interrupted"), ("beta", True, "interrupted")]
    assert procs.events == [
        ("interrupt", client_a.pid, plugin_a.pid), ("wait", ORPHAN_INTERRUPT_WAIT_S),
        ("interrupt", client_b.pid, plugin_b.pid), ("wait", ORPHAN_INTERRUPT_WAIT_S)]
    assert result.exit_code == 0 and procs.is_alive(live_wfc)
    assert not _DOCKER_COMMAND.search("\n".join(result.lines()))


@workflow(purpose="Case 8: never listed or touched are a running run's "
                  "container, another project's container, a dev-loop "
                  "container, an unlabelled container of another pipeline, a "
                  "live wfc command's build, and an orphaned build whose "
                  "context is another project's or a temp dir")
def test_the_plan_never_lists_what_is_not_this_projects_leftover(tmp_path, monkeypatch):
    """Case 8 over faked listings: nothing to stop, and the apply touches nothing."""
    project, other = tmp_path / "proj", tmp_path / "other"
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    daemon.start(container_labels(project, pipeline_id="p-1", run_id="run-live"))
    daemon.start(container_labels(other, pipeline_id="p-1", run_id="7"))
    daemon.start(container_labels(project), name="wfc-shell")
    daemon.start(variables={"WFC_PIPELINE_ID": "p-elsewhere", "WFC_RUN_ID": "3"})
    procs.build(_build_dir(project, "alpha"), "alpha", parent=procs.wfc("register-env"))
    procs.build(_build_dir(other, "plates-lab-lock"), "plates-lab-lock")
    procs.build(tmp_path / "Temp" / "wfc-killtest" / "x", "killtest")
    procs.build(project / "elsewhere" / "ctx", "stray")  # not under .wfc/build/
    before = set(daemon.running)

    plan = plan_prune(project, run_running=_running, pipeline_ids=["p-1"])

    assert plan.containers == () and plan.builds == () and not plan.to_stop
    assert "Nothing to stop." in plan.lines()
    result = apply_prune(plan)
    assert set(daemon.running) == before and daemon.killed == []
    assert procs.events == [] and result.exit_code == 0


@workflow(purpose="d-8's end rule for clients wfc marked as its own group "
                  "leaders: one that ignores its interrupt is terminated only "
                  "after the bounded wait, one whose console cannot be "
                  "attached is terminated at once, and every terminate takes "
                  "the plugin before docker.exe, never docker.exe alone")
def test_the_apply_interrupts_before_it_terminates_and_never_kills_docker_alone(
        tmp_path, monkeypatch):
    """The ignoring and the unattachable client; the order of every signal."""
    project = tmp_path / "proj"
    fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    stubborn, stubborn_plugin = procs.build(_build_dir(project, "a"), "a", marked=True)
    procs.ignoring.add(stubborn.pid)
    _, cut_off = procs.build(_build_dir(project, "b"), "b", marked=True)
    procs.unattachable.add(cut_off.pid)

    result = apply_prune(plan_prune(project, run_running=_running))

    assert [(s.build.env, s.how) for s in result.builds] == [
        ("a", "terminated"), ("b", "terminated")]
    assert procs.events == [
        ("interrupt", stubborn.pid, stubborn_plugin.pid), ("wait", ORPHAN_INTERRUPT_WAIT_S),
        ("terminate", ("docker-buildx.exe", "docker.exe")),
        ("interrupt", cut_off.ppid, cut_off.pid),
        ("terminate", ("docker-buildx.exe", "docker.exe"))]
    assert not procs.alive


def test_the_apply_leaves_alone_what_is_no_longer_a_leftover(tmp_path, monkeypatch):
    """A run that is running again and a build that ended are skipped, not touched."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    procs = fake_build_processes(monkeypatch)
    cid = daemon.start(container_labels(project, pipeline_id="p", run_id="9"))
    client, plugin = procs.build(_build_dir(project, "a"), "a")
    running: set[str] = set()
    plan = plan_prune(project, run_running=lambda p, r: r in running)
    assert len(plan.containers) == 1 and len(plan.builds) == 1

    running.add("9")
    procs.kill(plugin)
    procs.kill(client)
    result = apply_prune(plan)

    assert cid in daemon.running and daemon.killed == [] and procs.events == []
    assert len(result.skipped) == 2 and result.exit_code == 0


def test_a_liveness_that_fails_counts_the_run_as_running(tmp_path, monkeypatch):
    """When in doubt a container is not a leftover."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    daemon.start(container_labels(project, pipeline_id="p", run_id="1"))

    def broken(pipeline_id, run_id):
        raise RuntimeError("the database is locked")

    assert plan_prune(project, run_running=broken).containers == ()


@workflow(purpose="Part of case 9: with Docker unreachable the counts say so "
                  "instead of guessing (containers, images and the cache "
                  "unknown, the reason worded without a docker command) and "
                  "the orphaned-build count still comes from processes")
def test_the_counts_with_docker_unreachable_say_so_and_still_count_builds(
        tmp_path, monkeypatch):
    """Docker not answering: the build is still counted."""
    project = tmp_path / "proj"
    daemon = fake_leftover_daemon(monkeypatch)
    daemon.failure = DockerTimeoutError(
        "listing wfc's containers did not finish within 15 s: Docker is not answering")
    procs = fake_build_processes(monkeypatch)
    procs.build(_build_dir(project, "a"), "a")

    counts = leftover_counts(project, run_running=_running)

    assert (counts.containers, counts.builds, counts.unused_images, counts.cache_bytes) == (
        None, 1, None, None)
    assert "Docker is not answering" in counts.docker_error
    assert counts.to_stop == 1 and counts.phrases() == ["1 env build to stop"]
    text = "\n".join(plan_prune(project, run_running=_running).lines())
    assert "not checked" in text and not _DOCKER_COMMAND.search(text), text


def test_the_counts_name_the_unused_images_and_the_cache_size(tmp_project, monkeypatch):
    """Kept: the current image and one a version records; counted: the rest of local/<env>."""
    from tests.fixtures.routes.registration import write_env_record

    daemon = fake_leftover_daemon(monkeypatch)
    old, current, unused = ("sha256:" + c * 64 for c in "bcd")
    recorded_fp = write_env_record(tmp_project, "e", digest=old[7:])["env_fingerprint"]
    write_env_record(tmp_project, "e", digest=current[7:])
    daemon.images = [("local/e", old), ("local/e", current), ("local/e", unused),
                     ("local/other-projects-env", "sha256:" + "e" * 64)]
    daemon.cache_size = "1.5GB"

    counts = leftover_counts(tmp_project, run_running=_running,
                             recorded={"e": [("m/x v1", recorded_fp)]})

    assert (counts.unused_images, counts.cache_bytes) == (1, 1_500_000_000)
    assert counts.phrases() == ["1 env image nothing uses", "build cache 1.5 GB"]
    assert format_size(0) == "0 B"
    assert leftover_counts(tmp_project, run_running=_running).unused_images == 2


def test_a_build_client_is_read_from_its_command_line():
    """Tier 1: the env and context of docker build / buildx build, any tag spelling."""
    def info(*cmdline, name="docker.exe"):
        return ProcessInfo(pid=1, ppid=0, name=name, cmdline=cmdline, created=0.0)

    assert _build_target(info("docker", "build", "-t", "local/a:_wfc-build", "ctx")) == ("a", "ctx")
    assert _build_target(info("docker", "buildx", "build", "--tag=local/b.c:_wfc-build",
                              "--provenance=false", "C:\\p\\.wfc\\build\\b.c")) == (
        "b.c", "C:\\p\\.wfc\\build\\b.c")
    assert _build_target(info("x\\docker-buildx.exe", "buildx", "build", "-tlocal/d:_wfc-build",
                              "ctx", name="docker-buildx.exe")) == ("d", "ctx")
    assert _build_target(info("docker", "build", "-t", "local/a:latest", "ctx")) is None
    assert _build_target(info("docker", "run", "-t", "local/a:_wfc-build")) is None
    assert _build_target(info("git", "build", "-t", "local/a:_wfc-build", "x", name="git.exe")) is None
    assert _is_wfc_process(info("C:\\venv\\Scripts\\python.exe", "-m", "wfc", name="python.exe"))
    assert _is_wfc_process(info("/usr/bin/wfc", "canvas", name="wfc"))
    assert not _is_wfc_process(info("python", "-m", "pytest", name="python"))


def test_on_posix_a_plugin_whose_client_is_gone_names_its_own_process_group(tmp_path):
    """POSIX reparents an orphaned plugin to init: its group, not its parent, names the build."""
    project = tmp_path / "proj"
    context = str(_build_dir(project, "alpha"))
    init = ProcessInfo(pid=1, ppid=0, name="systemd", cmdline=("/sbin/init",), created=0.0)
    plugin = ProcessInfo(
        pid=301, ppid=1, name="docker-buildx",
        cmdline=("/usr/libexec/docker/cli-plugins/docker-buildx", "buildx", "build",
                 "-t", "local/alpha:_wfc-build", context),
        created=10.0, cwd=str(project), marked=True, pgid=300)

    builds = orphaned_builds({1: init, 301: plugin}, project)

    assert [(b.env, b.group_id, [p.pid for p in b.processes], b.marked)
            for b in builds] == [("alpha", 300, [301], True)]


def test_on_posix_the_interrupt_goes_to_the_clients_group_never_wfcs_own(monkeypatch):
    """d-8's unmeasured POSIX default: SIGINT to the client's process group."""
    sent = stub_posix_process_groups(monkeypatch, {300: 300, 301: 300, 400: 77},
                                     own_group=77)

    assert _interrupt_client(300, 301) is True
    assert _interrupt_client(400, 400) is False  # wfc's own group: refused
    assert _interrupt_client(500, 500) is False  # gone
    assert sent == [(300, signal.SIGINT)]
