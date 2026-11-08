"""A crashed pipeline's real container is stopped by wfc prune-envs; a live one's is not.

Two pipelines, each cut off with a run row still ``running`` (the harness's
claim), each with a real container carrying this project's labels and that
run's id. One pipeline's record names an owner and an engine that ran and
exited: it has crashed, and ``wfc prune-envs --yes`` stops its container and
names ``wfc cancel <id>``. The other's record names a live owner: its
container keeps running.

Every container here is the test's own; it stops only those. Docker is not
faked; the rows are written through Execution (the harness's stub rung runs
the steps as local processes).
"""

from __future__ import annotations

import subprocess

import pytest
from axiom_annotations import workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import ended_process as _ended
from tests.harness import Scenario, node, run_scenario, selector, wire
from wfc import layout
from wfc.cli import cli_main
from wfc.environments import container_labels
from wfc.execution.pipeline_record import (
    ENTRY_POINT_CLI,
    process_identity,
    record_engine,
    write_pipeline_record,
)
from wfc.persistence import Run, get_session

pytestmark = [pytest.mark.integration, requires_docker]


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


def _cut_off(root, monkeypatch, pid: str) -> int:
    """A pipeline cut off with S1's a in flight; return its running row's id."""
    run_scenario(Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                          samples=["S1"], pipeline_id=pid),
                 root=root, monkeypatch=monkeypatch, interrupted=("a", "S1", "default"),
                 pipeline_end=False)
    with get_session() as s:
        (row,) = s.exec(select(Run).where(Run.pipeline_id == pid)).all()
    assert row.status == "running"
    return int(row.id)


@workflow(purpose="A real labelled container whose pipeline's record names a "
                  "gone owner and engine is stopped by wfc prune-envs --yes, "
                  "which names wfc cancel for it; the container of a pipeline "
                  "whose owner lives keeps running")
def test_prune_envs_stops_a_crashed_pipelines_real_container_only(
        tmp_project, monkeypatch, capsys, processes):
    crashed_row = _cut_off(tmp_project, monkeypatch, "p-crashed")
    live_row = _cut_off(tmp_project, monkeypatch, "p-owned")
    for pid in ("p-crashed", "p-owned"):
        (layout.pipelines_dir(tmp_project) / pid).mkdir(parents=True, exist_ok=True)
    write_pipeline_record(tmp_project, "p-crashed", ENTRY_POINT_CLI, owner=_ended(processes))
    record_engine(tmp_project, "p-crashed", _ended(processes).pid)
    write_pipeline_record(tmp_project, "p-owned", ENTRY_POINT_CLI,
                          owner=process_identity(processes().pid))
    crashed = _start_container(container_labels(tmp_project, pipeline_id="p-crashed",
                                                run_id=str(crashed_row)))
    live = _start_container(container_labels(tmp_project, pipeline_id="p-owned",
                                             run_id=str(live_row)))
    try:
        capsys.readouterr()
        rc = cli_main(["prune-envs", "--yes"])
        out = capsys.readouterr().out

        assert rc == 0, out
        assert "`wfc cancel p-crashed` closes its record" in out
        assert "p-owned" not in out
        assert not _running(crashed) and _running(live)
    finally:
        _docker("kill", crashed, live)
