"""wfc prune-envs' apply against the real daemon and real build clients.

Cases 7 and 8 for real: the apply stops a labelled container whose run has
ended and leaves one whose run is still running. Case 16: a real sleeping
build started through wfc's own ``docker.build`` under this (temporary)
project's ``.wfc/build/``, whose ``docker.exe`` was killed, leaving its
``docker-buildx`` plugin holding the build, is found marked as wfc's and
ended by the apply's interrupt, and its history record leaves ``Running``
within seconds, while a bystander build in a temp dir keeps running. The
same orphan started without wfc's mark is sent no interrupt: its process
tree is terminated. Both are Windows-only: on Linux and macOS a killed
``docker`` client takes its plugin and its build with it, which the POSIX
case checks (no orphaned build is left for the plan to list).

Every container and build here is the test's own; it stops only those.
Nothing is faked.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid

import psutil
import pytest
from axiom_annotations import workflow

from tests.conftest import requires_docker
from tests.integration.test_docker_build_interrupt_docker import _history, _running_entry_for
from wfc.environments import apply_prune, container_labels, plan_prune
from wfc.environments import docker as docker_runner
from wfc.environments.docker import (
    _own_group_kwargs,
    _process_tree,
    interrupt_process_group,
    terminate_process_tree,
)

pytestmark = [pytest.mark.integration, requires_docker]

#: How long a history record may take to leave ``Running`` (Docker's history
#: reads lag under load; the cancel itself takes a second or two).
_ENDS_WITHIN_S = 30.0


def _docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def _start_container(labels: dict[str, str]) -> str:
    flags = [f for k, v in labels.items() for f in ("--label", f"{k}={v}")]
    run = _docker("run", "-d", "--rm", *flags, "alpine:3.20", "sleep", "300")
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def _running(container_id: str) -> bool:
    run = _docker("inspect", "--format", "{{.State.Running}}", container_id)
    return run.returncode == 0 and run.stdout.strip() == "true"


@workflow(purpose="Cases 7 and 8 for real: the apply kills a real container "
                  "carrying this project's labels whose run has ended, and "
                  "leaves the one whose run is still running")
def test_apply_stops_an_ended_runs_container_and_leaves_a_running_one(tmp_path):
    """A real ended run's container goes; the running run's stays."""
    project = tmp_path / "proj"
    project.mkdir()
    ended = _start_container(container_labels(project, pipeline_id="p", run_id="ended"))
    live = _start_container(container_labels(project, pipeline_id="p", run_id="live"))
    try:
        plan = plan_prune(project, run_running=lambda pipeline, run: run == "live")
        assert [c.id for c in plan.containers] == [ended]

        result = apply_prune(plan)

        assert [c.id for c in result.containers] == [ended] and not result.errors
        assert not _running(ended) and _running(live)
    finally:
        _docker("kill", ended, live)


def _start_build(context, tag: str) -> subprocess.Popen:
    """Start ``docker build`` as wfc does: its own process group, output drained."""
    proc = subprocess.Popen(
        ["docker", "build", "--provenance=false", "-t", tag, str(context)],
        env={**os.environ, "DOCKER_BUILDKIT": "1"}, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        **_own_group_kwargs())
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
    return proc


def _sleeping_context(path, nonce: str):
    path.mkdir(parents=True)
    (path / "Dockerfile").write_text(
        f"FROM alpine:3.20\nRUN echo {nonce} && sleep 150\n", newline="\n")
    return path


def _wait_running(context) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        entry = _running_entry_for(context)
        if entry is not None:
            return entry
        time.sleep(0.5)
    pytest.fail(f"no Running history record for {context}")


def _status(ref: str) -> str:
    short = ref.rsplit("/", 1)[-1]
    rows = {row["ref"].rsplit("/", 1)[-1]: row for row in _history() or []}
    return rows[short]["status"] if short in rows else "<no record>"


def _start_through_wfc(context, tag: str) -> int:
    """Start the build through wfc's own ``docker.build``, on a thread.

    Returns:
        The pid of the ``docker`` client it started (marked as wfc's). The
        thread ends on its own once the client is gone (the build raises
        there, and the thread keeps what it raised).
    """
    def run() -> None:
        try:
            docker_runner.build(context, tag)
        except BaseException:  # the killed client fails the build: expected
            pass

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for child in psutil.Process().children():
            try:
                if tag in child.cmdline():
                    return child.pid
            except psutil.Error:
                continue
        time.sleep(0.2)
    pytest.fail(f"wfc's build did not start a docker client for {tag}")


def _start_unmarked(context, tag: str) -> int:
    """Start the build as an older wfc did: its own group, no mark.

    Returns:
        The ``docker`` client's pid.
    """
    return _start_build(context, tag).pid


def _end(proc: subprocess.Popen) -> None:
    """End one of the test's own build clients: interrupt, then its tree."""
    tree = _process_tree(proc.pid)
    if interrupt_process_group(proc.pid):
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    terminate_process_tree([p for p in tree if p.is_running()])


def _orphan_and_prune(tmp_path, start_orphan, *, marked: bool, how: str) -> None:
    """Case 16's flow: orphan a real sleeping build, plan, apply, check.

    The orphan's ``docker.exe`` is killed, leaving its ``docker-buildx``
    plugin holding the build. The plan must find it, marked or not as
    *marked* says; the apply must end it *how*; its history record must
    leave ``Running`` while a bystander build in a temp dir keeps running.
    """
    if _history() is None:
        pytest.skip("docker buildx history is not available on this daemon")
    nonce = uuid.uuid4().hex[:10]
    project = tmp_path / "proj"
    env = f"prunetest-{nonce}"
    orphan_ctx = _sleeping_context(project / ".wfc" / "build" / env, f"orphan-{nonce}")
    bystander_ctx = _sleeping_context(tmp_path / f"bystander-{nonce}", f"bystander-{nonce}")
    client_pid = start_orphan(orphan_ctx, f"local/{env}:_wfc-build")
    bystander = _start_build(bystander_ctx, f"local/prunetest-bystander-{nonce}:_wfc-build")
    plugin = None
    try:
        orphan_ref = _wait_running(orphan_ctx)["ref"]
        bystander_ref = _wait_running(bystander_ctx)["ref"]
        time.sleep(3)  # let the RUN steps start sleeping
        client = psutil.Process(client_pid)
        plugins = [p for p in client.children(recursive=True)
                   if "buildx" in p.name().lower()]
        assert plugins, "the docker client has no buildx plugin child"
        plugin = plugins[0]
        client.kill()  # docker.exe only: the plugin holds the build
        client.wait(timeout=10)
        assert plugin.is_running()

        plan = plan_prune(project, run_running=lambda pipeline, run: True)

        assert [(b.env, [p.pid for p in b.processes], b.group_id, b.marked)
                for b in plan.builds] == [(env, [plugin.pid], client_pid, marked)]
        result = apply_prune(plan)
        assert [(s.stopped, s.how) for s in result.builds] == [(True, how)]

        deadline = time.monotonic() + _ENDS_WITHIN_S
        status = _status(orphan_ref)
        while status == "Running" and time.monotonic() < deadline:
            time.sleep(1)
            status = _status(orphan_ref)
        assert status != "Running", "the orphaned build is still Running"
        assert _status(bystander_ref) == "Running", "the bystander build was touched"
        assert bystander.poll() is None
    finally:
        if plugin is not None and plugin.is_running():
            terminate_process_tree([plugin])
        _end(bystander)


#: Only on Windows does a killed ``docker.exe`` leave its plugin holding the
#: build; on Linux and macOS the plugin and the build end with the client.
_PLUGIN_OUTLIVES_ITS_CLIENT = pytest.mark.skipif(
    sys.platform != "win32",
    reason="on POSIX a killed docker client takes its buildx plugin and build with it")


@_PLUGIN_OUTLIVES_ITS_CLIENT
@workflow(purpose="Case 16: a real sleeping build started through wfc's own "
                  "docker.build under this project's .wfc/build/, whose "
                  "docker.exe was killed, leaving its plugin holding the "
                  "build, is found by the plan as marked by wfc and ended by "
                  "the apply's interrupt; its history record leaves Running "
                  "within seconds while a bystander build in a temp dir keeps "
                  "running")
def test_an_orphaned_build_whose_client_was_killed_is_ended_by_the_apply(tmp_path):
    """Case 16 for real: wfc's marked client is interrupted."""
    _orphan_and_prune(tmp_path, _start_through_wfc, marked=True, how="interrupted")


@_PLUGIN_OUTLIVES_ITS_CLIENT
@workflow(purpose="An orphaned real build whose client carries no wfc mark "
                  "(started as an older wfc did) is sent no interrupt: the "
                  "apply terminates its process tree, the build's history "
                  "record leaves Running, and a bystander build keeps running")
def test_an_orphaned_build_wfc_did_not_mark_is_ended_by_its_tree_end(tmp_path):
    """The unmarked orphan for real: terminated, never interrupted."""
    _orphan_and_prune(tmp_path, _start_unmarked, marked=False, how="terminated")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="on Windows the plugin outlives its client (case 16)")
@workflow(purpose="On Linux and macOS a real sleeping build started through "
                  "wfc's own docker.build, whose docker client was killed, "
                  "ends by itself: its buildx plugin exits, its history "
                  "record leaves Running, and the plan lists no orphaned "
                  "build, while a bystander build keeps running")
def test_on_posix_a_killed_client_leaves_no_orphaned_build(tmp_path):
    """Case 16 does not arise on POSIX: nothing is left for prune-envs."""
    if _history() is None:
        pytest.skip("docker buildx history is not available on this daemon")
    nonce = uuid.uuid4().hex[:10]
    project = tmp_path / "proj"
    env = f"prunetest-{nonce}"
    orphan_ctx = _sleeping_context(project / ".wfc" / "build" / env, f"orphan-{nonce}")
    bystander_ctx = _sleeping_context(tmp_path / f"bystander-{nonce}", f"bystander-{nonce}")
    client_pid = _start_through_wfc(orphan_ctx, f"local/{env}:_wfc-build")
    bystander = _start_build(bystander_ctx, f"local/prunetest-bystander-{nonce}:_wfc-build")
    tree: list = []
    try:
        orphan_ref = _wait_running(orphan_ctx)["ref"]
        bystander_ref = _wait_running(bystander_ctx)["ref"]
        time.sleep(3)  # let the RUN steps start sleeping
        client = psutil.Process(client_pid)
        tree = client.children(recursive=True)
        client.kill()
        client.wait(timeout=10)

        deadline = time.monotonic() + _ENDS_WITHIN_S
        while (any(p.is_running() for p in tree) or _status(orphan_ref) == "Running") \
                and time.monotonic() < deadline:
            time.sleep(1)
        assert not any(p.is_running() for p in tree), "the plugin outlived its client"
        assert _status(orphan_ref) != "Running", "the build is still Running"
        assert list(plan_prune(project, run_running=lambda pipeline, run: True).builds) == []
        assert _status(bystander_ref) == "Running", "the bystander build was touched"
        assert bystander.poll() is None
    finally:
        left = [p for p in tree if p.is_running()]
        if left:
            terminate_process_tree(left)
        _end(bystander)
