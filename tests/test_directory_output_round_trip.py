"""A directory output survives the archive, the remote and a lost local cache,
and a downstream step reads it back whole.

Tier 3 on the scenario harness's stub rung, against the harness's real
local-filesystem DVC remote; the archive pass, the push and the resolver's
pull are the production ones. Declared fixture deviation: ``_lose_local``
deletes the local DVC cache, the checkouts and the producing run's archive,
the state a lost disk leaves, which no production verb produces.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from axiom_annotations import Step, workflow
from sqlmodel import select

from wfc.persistence import PushStatus, RunOutput, get_session
from tests.harness import (
    Behavior,
    Scenario,
    build_project,
    drive_target,
    node,
    selector,
    wire,
)

T0 = ("n0", "s1", "default")
T1 = ("n1", "s1", "default")
TREE = {"a.csv": "x,y\n1,2\n", "nested/b.csv": "x,y\n3,4\n"}


def _tree(path: Path) -> dict[str, str]:
    return {p.relative_to(path).as_posix(): p.read_text()
            for p in sorted(path.rglob("*")) if p.is_file()}


def _writable(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def _lose_local(root: Path, run_id: int) -> None:
    """Declared deviation: every local copy of the run's bytes goes missing."""
    from wfc import layout

    for gone in (root / ".dvc" / "cache", layout.checkouts_dir(root),
                 layout.run_archive_dir(root, run_id)):
        if gone.exists():  # a checkout exists only once something read it
            shutil.rmtree(gone, onerror=_writable)


@workflow(purpose="A step's directory output is archived and pushed; with every "
                  "local copy lost, the downstream step's input is pulled back "
                  "from the remote as the identical tree and read")
def test_a_directory_output_round_trips_to_a_downstream_reader(tmp_path_factory,
                                                              monkeypatch):
    from wfc.identity import hash_directory
    from wfc.storage import archive_outputs, entry_is_complete
    from wfc.storage.push_worker import _push_worker_tick

    口 = Step(step_num=1, name="A step produces a directory output",
             purpose="The producer saves a tree with a nested member; its row "
                     "is pre-archive")
    root = tmp_path_factory.mktemp("dir_output_round_trip")
    scn = Scenario(nodes=[
        selector(),
        node("n0", inputs=[wire("sel")], outputs={"tree": "directory"},
             output_files={"tree": "tree"},
             behavior=Behavior(outputs={"tree": dict(TREE)})),
        node("n1", inputs=[wire("n0", source_slot="tree")],
             behavior=Behavior(read_inputs=["data"])),
    ])
    project = build_project(scn, root=root, monkeypatch=monkeypatch)
    producer = drive_target(project, "n0", monkeypatch=monkeypatch)
    assert producer.exit_code(T0) == 0
    run_id = producer.run_row(T0)["id"]

    口 = Step(step_num=2, name="Archive and push the directory",
             purpose="The archive pass records a .dir hash; the push worker "
                     "sends it to the real local remote and records the row "
                     "pushed, the record the consumer's resolver pulls on")
    [archived] = archive_outputs(root, run_id=run_id)
    assert archived["status"] == "archived"
    content_hash = archived["content_hash"]
    assert content_hash.endswith(".dir")
    _push_worker_tick(root)
    with get_session() as session:
        [pushed_row] = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)).all()
        assert pushed_row.push_status == PushStatus.pushed.value, pushed_row

    口 = Step(step_num=3, name="Lose every local copy",
             purpose="The cache, the checkout and the run archive are gone, so "
                     "the remote is the only source")
    _lose_local(root, run_id)
    assert not entry_is_complete(root, content_hash)

    口 = Step(step_num=4, name="The downstream step reads the pulled tree",
             purpose="The consumer runs to completion reading its input; the "
                     "path it was handed is the identical tree, whose hash is "
                     "the recorded one")
    consumer = drive_target(project, "n1", monkeypatch=monkeypatch)
    assert consumer.exit_code(T1) == 0
    [handed] = consumer.phase_args("dispatch", T1)["slot_paths"]["data"]
    assert _tree(Path(handed)) == TREE
    assert hash_directory(Path(handed)) == content_hash
    assert entry_is_complete(root, content_hash)
