"""Every kind of input wfc hands a step is checked against its record before the read.

The property behind ``[integrity] inputs``: a restored sample copy, a
pre-archive file or directory output, a file cache object and a checkout
all go through Storage's one check, through the reader production uses for
each. The user's edit gives a file other bytes of the same length and puts
its mtime back: in place for a file wfc owns alone, by a new file saved at
the path for a view (a restored sample, a checkout member), whose cache
entry stays intact. Under ``stat-then-hash`` the change time
(``st_ctime`` on Linux and macOS, NTFS's ChangeTime on Windows) still
moves, so the stat fields catch it before the read. Under ``hash`` the
entry is re-hashed on every read: the check's stat read is held at the
recorded values (``fakes.hold_stat_record``), so the stamp or collect's
record still matches, and the read re-hashes and catches the edit anyway.

The user's edits strip the read-only bits first, as a deliberate hand edit
would.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.harness import Behavior, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout

HEAD = ("head", "S1", "default")
GENUINE_OUTPUT = "id,value\n1,GENUINE-OUTPUT\n"
DIR_FILES = {"f.csv": "id,value\n1,GOOD\n", "sub/g.csv": "id,value\n2,GOOD\n"}

KINDS = ["sample", "output-file", "output-directory", "cache-object", "checkout"]


@dataclass
class _Leg:
    """One kind's state: the file the user edits and the production read of it.

    Attributes:
        entry: The entry the check reads (the file, or the directory).
        target: The file the user edits.
        recorded: The stat record the reader compares against: the stamp's,
            or collect's.
        read: Reads the entry the way production does and asserts the edit
            was caught: repaired to the recorded bytes, or refused.
        view: The target is a view of a cache object (a restored sample, a
            checkout member), so the edit replaces it at its path and the
            cache entry stays intact.
    """

    entry: Path
    target: Path
    recorded: Callable[[], dict | None]
    read: Callable[[], None]
    view: bool = False


def _writable(path: Path) -> None:
    """Declared deviation: strip the read-only bits, as a hand edit would."""
    from wfc.storage.cache import _make_writable

    _make_writable(path)


def _stamped(stamp: Path) -> dict:
    """The stat record a stamp holds (its ``hash`` aside)."""
    data = json.loads(stamp.read_text(encoding="utf-8"))
    data.pop("hash")
    return data


def _set_inputs(root: Path, setting: str) -> None:
    """Set ``[integrity] inputs`` and commit it, as a user does before a run."""
    import subprocess

    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8")
                      + f'\n[integrity]\ninputs = "{setting}"\n', encoding="utf-8")
    subprocess.run(["git", "commit", "-q", "-am", "integrity setting"], cwd=root,
                   check=True)


def _head(kind: str):
    """head over the selector: an echo of the sample, a written file, or a directory."""
    if kind == "echo":
        return node("head", inputs=[wire(SELECTOR_ID)], behavior=Behavior(echo_input="data"))
    if kind == "directory":
        return node("head", inputs=[wire(SELECTOR_ID)], outputs={"data": "directory"},
                    behavior=Behavior(outputs={"data": dict(DIR_FILES)}))
    return node("head", inputs=[wire(SELECTOR_ID)],
                behavior=Behavior(outputs={"data": GENUINE_OUTPUT}))


def _run_head(tmp_path, monkeypatch, head_kind: str, setting: str):
    """Build the project, set the setting, run head; return (root, run id, output path)."""
    project = build_project(Scenario(nodes=[selector(), _head(head_kind)], samples=["S1"]),
                            root=tmp_path / "proj", monkeypatch=monkeypatch)
    _set_inputs(project.root, setting)
    obs = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs.exit_code(HEAD) == 0
    run_id = obs.runs[HEAD].run_id
    return project.root, run_id, Path(obs.output_rows_for(HEAD)[0]["artifact_path"])


def _row(run_id: int):
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        return session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()


def _tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


# --- one builder per kind -------------------------------------------------------------

def _sample(tmp_path, monkeypatch, setting) -> _Leg:
    """The restored copy of a file sample; materialize's check repairs it."""
    from wfc.storage import SAMPLE_REPAIRED, check_sample

    project = build_project(Scenario(nodes=[selector(), _head("echo")], samples=["S1"]),
                            root=tmp_path / "proj", monkeypatch=monkeypatch)
    _set_inputs(project.root, setting)
    copy = layout.restored_sample_path(project.root, "S1", "S1.csv")
    genuine = copy.read_bytes()

    def read():
        assert check_sample("S1", project.root).status == SAMPLE_REPAIRED
        assert copy.read_bytes() == genuine
        assert not os.access(copy, os.W_OK), "repaired as a read-only view"

    return _Leg(copy, copy, lambda: _stamped(layout.sample_stamp(project.root, "S1")), read,
                view=True)


def _output(tmp_path, monkeypatch, setting, kind) -> _Leg:
    """A pre-archive output; the resolver refuses it and the row is marked changed."""
    from wfc.storage.integrity import output_record
    from wfc.storage.resolve import InputUnavailableError, resolve_input

    root, run_id, out = _run_head(tmp_path, monkeypatch, kind, setting)
    assert (_row(run_id).collect_hash is not None) is (setting == "hash")
    target = out / "f.csv" if kind == "directory" else out

    def read():
        with pytest.raises(InputUnavailableError) as exc:
            resolve_input(run_id, "data")
        assert str(out) in str(exc.value) and f"run {run_id}" in str(exc.value)
        assert _row(run_id).integrity_status == "changed"

    return _Leg(out, target, lambda: output_record(_row(run_id), root), read)


def _cache_object(tmp_path, monkeypatch, setting) -> _Leg:
    """An archived file output's cache object, never pushed; the resolver refuses it."""
    from wfc.storage import archive_outputs
    from wfc.storage.resolve import InputUnavailableError, resolve_input

    root, run_id, _out = _run_head(tmp_path, monkeypatch, "file", setting)
    archive_outputs(root, run_id=run_id)
    md5 = _row(run_id).content_hash
    obj = layout.dvc_cache_entry(root, md5)
    assert obj.read_bytes() == GENUINE_OUTPUT.encode()
    assert resolve_input(run_id, "data") == str(obj), "the intact object is served"

    def read():
        with pytest.raises(InputUnavailableError) as exc:
            resolve_input(run_id, "data")
        assert f"run {run_id}" in str(exc.value)
        assert "changed in the local cache" in str(exc.value)

    return _Leg(obj, obj, lambda: _stamped(layout.cache_object_stamp(root, md5)), read)


def _checkout(tmp_path, monkeypatch, setting) -> _Leg:
    """The checkout of an archived directory output; the next read rebuilds it."""
    from wfc.storage import archive_outputs
    from wfc.storage.resolve import resolve_input

    root, run_id, _out = _run_head(tmp_path, monkeypatch, "directory", setting)
    archive_outputs(root, run_id=run_id)
    dir_hash = _row(run_id).content_hash
    checkout = Path(resolve_input(run_id, "data"))
    assert checkout == layout.checkout_dir(root, dir_hash)
    genuine = {rel: text.encode() for rel, text in DIR_FILES.items()}
    assert _tree(checkout) == genuine

    def read():
        assert Path(resolve_input(run_id, "data")) == checkout
        assert _tree(checkout) == genuine
        assert not os.access(checkout / "f.csv", os.W_OK), "rebuilt as a read-only view"

    return _Leg(checkout, checkout / "f.csv",
                lambda: _stamped(layout.checkout_stamp(root, dir_hash)), read, view=True)


def _leg(kind: str, tmp_path, monkeypatch, setting: str) -> _Leg:
    if kind == "sample":
        return _sample(tmp_path, monkeypatch, setting)
    if kind == "output-file":
        return _output(tmp_path, monkeypatch, setting, "file")
    if kind == "output-directory":
        return _output(tmp_path, monkeypatch, setting, "directory")
    if kind == "cache-object":
        return _cache_object(tmp_path, monkeypatch, setting)
    return _checkout(tmp_path, monkeypatch, setting)


def _edit_keeping_size_and_mtime(f: Path, *, view: bool) -> None:
    """Give a file other bytes of the same length, then put its mtime back.

    A file wfc owns alone is rewritten in place. A view may share its inode
    with the cache object, and a write through it would change the cache
    entry itself (a different case), so a view is replaced at its path by a
    new file, as an editor that saves by rename does; the cache entry stays
    intact.
    """
    st = f.stat()
    data = bytes(b ^ 0x01 for b in f.read_bytes())
    if view:
        from wfc.storage import links
        from wfc.storage.cache import open_dirs

        open_dirs(f.parent)
        tmp = f.with_name(f.name + ".edited")
        tmp.write_bytes(data)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        links.replace_over(tmp, f)
    else:
        _writable(f)
        f.write_bytes(data)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (f.stat().st_size, f.stat().st_mtime_ns) == (st.st_size, st.st_mtime_ns)


@pytest.mark.parametrize("setting", ["stat-then-hash", "hash"])
@pytest.mark.parametrize("kind", KINDS)
@workflow(
    purpose="every kind of input a step reads -- a restored sample copy, a "
            "pre-archive file or directory output, a file cache object and a "
            "checkout -- goes through Storage's one check before the read, "
            "and a same-size edit with the mtime restored is caught: under "
            "stat-then-hash by the change time in the stat fields, on Linux, "
            "macOS and Windows; under hash by the re-hash, with a stamp or "
            "stat record that still matches not vouching",
    inputs="one entry of each kind, written by the production path under the "
           "setting, then edited in place by the user; under hash the check's "
           "stat read held at the recorded values",
    outputs="the reader repairs the entry to its recorded bytes (sample copy, "
            "checkout) or refuses it naming the run (output, cache object); "
            "under hash the entry's bytes were re-hashed",
)
def test_every_kind_of_input_is_checked_before_it_is_read(
        tmp_path, monkeypatch, kind, setting):
    _ = Step(step_num=1, name="Write the entry through production",
             purpose="Restore the sample, or run head (and archive its output for "
                     "the cache object and the checkout) with the setting committed")
    leg = _leg(kind, tmp_path, monkeypatch, setting)

    _ = Step(step_num=2, name="Edit it in place",
             purpose="Same size, mtime put back; under hash the check's stat read "
                     "is held first, so the recorded stat record still matches")
    if setting == "hash":
        held = fakes.hold_stat_record(monkeypatch, leg.entry)
        assert leg.recorded() == held, "the stamp or collect's record matches"
    _edit_keeping_size_and_mtime(leg.target, view=leg.view)

    _ = Step(step_num=3, name="Read it",
             purpose="The production reader catches the edit before any step "
                     "reads the bytes; under hash it re-hashed the entry")
    reads = fakes.spy_content_reads(monkeypatch)
    leg.read()
    if setting == "hash":
        hashed = {Path(p) for p in reads["hashed"]}
        assert leg.target in hashed, "under hash the read re-hashes the entry"
