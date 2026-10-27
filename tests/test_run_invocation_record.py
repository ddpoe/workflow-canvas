"""The invocation record: what dispatch handed the container launch, on the run row.

Every run dispatch launches records, before the launch, the container-side
invocation: the image reference handed to Docker, the interpreter and the
script path, the mounts as (container path, role, read-only), the ``WFC_*``
values exactly as forwarded, the user, ``gpus``, the working directory and
the wfc version. Host paths are not stored. A cache-hit row records none.

Runs come from ``tests.fixtures.routes.completed_run``: production's five
phases on the harness's stub rung, whose one substitution is the container
launch (``fakes.stub_method_process``). It keeps the argv and environment
dispatch handed the launch on the harness record, which is what these tests
compare the stored record with. The dispatch-phase endings (no record for a
refusal, a record for a launch refused at the boundary) are asserted in
``tests/test_execution_catalog.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from axiom_annotations import workflow

from tests.fixtures.routes import completed_run
from tests.harness.scenario import exits


def _launched(driven) -> list[str]:
    """The argv dispatch handed the launch for the driven run."""
    (record,) = driven.observation.runs.values()
    assert record.dispatch_cmd is not None, "dispatch launched nothing"
    return record.dispatch_cmd


def _read_argv(cmd: list[str]) -> dict:
    """Split a ``docker run`` argv into the parts an invocation records.

    Args:
        cmd: The argv dispatch handed the launch.

    Returns:
        The forwarded ``-e`` values, the binds as (container path,
        read-only) in bind order, ``--user``, ``-w``, whether ``--gpus``
        was given, and the image, interpreter and script (the argv's tail).
    """
    env: dict[str, str] = {}
    binds: list[tuple[str, bool]] = []
    user = workdir = None
    i = 0
    while i < len(cmd) - 3:
        flag = cmd[i]
        if flag == "-e":
            name, _, value = cmd[i + 1].partition("=")
            env[name] = value
        elif flag == "-v":
            spec = cmd[i + 1]
            read_only = spec.endswith(":ro")
            if read_only:
                spec = spec[: -len(":ro")]
            binds.append((spec.rsplit(":", 1)[1], read_only))
        elif flag == "--user":
            user = cmd[i + 1]
        elif flag == "-w":
            workdir = cmd[i + 1]
        i += 2 if flag in ("-e", "-v", "--user", "-w", "--pull", "--gpus") else 1
    return {"env": env, "binds": binds, "user": user, "workdir": workdir,
            "gpus": "--gpus" in cmd, "image": cmd[-3], "interpreter": cmd[-2],
            "script": cmd[-1]}


@workflow(purpose="A run dispatch launched records on its row the container-side "
                  "invocation it handed Docker: image, interpreter, script, "
                  "mounts with their roles, the forwarded WFC_* values, user, "
                  "gpus, working directory and wfc version, and no host path",
          inputs="One run driven through production's five phases on the stub "
                 "rung, the launch argv the stub kept",
          outputs="The row's invocation equals what the argv carries")
def test_a_dispatched_run_records_the_invocation_it_launched(tmp_project, monkeypatch):
    driven = completed_run(tmp_project, monkeypatch=monkeypatch)
    invocation = driven.run_row["invocation"]
    argv = _read_argv(_launched(driven))

    assert invocation is not None, "a dispatched run has no invocation record"
    assert invocation["image_ref"] == argv["image"]
    assert invocation["interpreter"] == argv["interpreter"]
    assert invocation["script"] == argv["script"]
    assert invocation["env"] == argv["env"]
    assert "WFC_INPUT_PATHS" in invocation["env"], "the run read a sample"
    assert [(m["container"], m["read_only"]) for m in invocation["mounts"]] \
        == argv["binds"]
    roles = {m["container"]: m["role"] for m in invocation["mounts"]}
    assert roles["/work"] == "project" and roles["/dvc-cache"] == "cache"
    assert [m["role"] for m in invocation["mounts"][2:]] == ["code"], (
        "the version's code copy is the third bind"
    )
    assert invocation["user"] == argv["user"]
    assert invocation["workdir"] == argv["workdir"]
    assert invocation["gpus"] is argv["gpus"]
    assert invocation["wfc_version"] == driven.run_row["wfc_version"]

    # Container-side values only: no host path reaches the record.
    stored = json.dumps(invocation)
    root = Path(tmp_project).resolve()
    for host in {root.as_posix(), str(root), json.dumps(str(root))[1:-1]}:
        assert host not in stored, f"the record holds the host path {host}"


@workflow(purpose="The invocation is written before the launch, so a run whose "
                  "method fails keeps it; a cache-hit row launched nothing and "
                  "records none",
          inputs="A run whose method exits 1, then a completed run and the same "
                 "run again on one project",
          outputs="The failed row carries the launched invocation; the cache-hit "
                  "row's invocation is NULL while its original's is set")
def test_a_failed_method_keeps_its_invocation_and_a_cache_hit_has_none(
    tmp_project, monkeypatch,
):
    failed = completed_run(tmp_project, monkeypatch=monkeypatch,
                           method="fails_once", behavior=exits(1))
    assert failed.status == "failed"
    assert failed.run_row["invocation"] is not None
    assert failed.run_row["invocation"]["script"] == _launched(failed)[-1]

    original = completed_run(tmp_project, monkeypatch=monkeypatch,
                             method="reused")
    hit = completed_run(tmp_project, monkeypatch=monkeypatch, method="reused")
    assert hit.run_row["cache_source_run_id"] == original.run_id, (
        "the second run of the same key is a cache hit"
    )
    assert original.run_row["invocation"] is not None
    assert hit.run_row["invocation"] is None
