"""An output that changed after its run wrote it is never used or archived.

Collect makes each output read-only and records its stat fields (a
directory's member list goes in its member stamp); under ``[integrity]
inputs = "hash"`` it also hashes the output into the row's collect hash.
Every reader of an output the archive pass has not reached -- the hit rule,
the resolver and the archive pass itself -- checks it against that record
through Storage's one check. A changed output is missing: its row is marked
changed, the consumer and the archive refuse it naming the file and the
run, and the next launch re-runs the step.

The user's edits below strip the read-only bits first, as a deliberate hand
edit would.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.fixtures.routes import canvas_client
from tests.harness import Behavior, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout

TAMPERED = b"id,value\n1,TAMPERED\n"
HEAD = ("head", "S1", "default")
TAIL = ("tail", "S1", "default")
DIR_FILES = {"f.csv": "id,value\n1,GOOD\n", "sub/g.csv": "id,value\n2,GOOD\n"}


def _scenario(kind: str, *, tail: bool = False) -> Scenario:
    """head (a file or directory output) over the selector, optionally a tail reading it."""
    if kind == "directory":
        head = node("head", inputs=[wire(SELECTOR_ID)], outputs={"data": "directory"},
                    behavior=Behavior(outputs={"data": dict(DIR_FILES)}))
    else:
        head = node("head", inputs=[wire(SELECTOR_ID)],
                    behavior=Behavior(echo_input="data"))
    nodes = [selector(), head]
    if tail:
        nodes.append(node("tail", inputs=[wire("head")],
                          behavior=Behavior(echo_input="data")))
    return Scenario(nodes=nodes, samples=["S1"])


def _set_inputs(root: Path, setting: str) -> None:
    """Set ``[integrity] inputs`` and commit it, as a user does before a run."""
    import subprocess

    marker = layout.marker_path(root)
    marker.write_text(marker.read_text(encoding="utf-8")
                      + f'\n[integrity]\ninputs = "{setting}"\n', encoding="utf-8")
    subprocess.run(["git", "commit", "-q", "-am", "integrity setting"], cwd=root,
                   check=True)


def _project(tmp_path, monkeypatch, kind: str = "file", *, setting: str | None = None,
             tail: bool = False):
    """Build the project, set ``[integrity] inputs``, run head; return (project, run id, output)."""
    project = build_project(_scenario(kind, tail=tail), root=tmp_path / "proj",
                            monkeypatch=monkeypatch)
    if setting is not None:
        _set_inputs(project.root, setting)
    obs = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs.exit_code(HEAD) == 0
    run_id = obs.runs[HEAD].run_id
    out = Path(obs.output_rows_for(HEAD)[0]["artifact_path"])
    return project, run_id, out


def _row(run_id: int):
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        return session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()


def _writable(path: Path) -> None:
    """Declared deviation: strip the read-only bits, as a hand edit would."""
    from wfc.storage.cache import _make_writable

    _make_writable(path)


def _edit_in_place_keeping_size_and_mtime(f: Path) -> None:
    """Overwrite a file with other bytes of the same length, then put its mtime back."""
    st = f.stat()
    data = f.read_bytes()
    _writable(f)
    f.write_bytes(bytes(b ^ 0x01 for b in data))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_size == st.st_size


def _member(out: Path, kind: str) -> Path:
    return out / "f.csv" if kind == "directory" else out


def _archive_status(results: list[dict], run_id: int) -> str:
    (entry,) = [r for r in results if r["run_id"] == run_id]
    return entry["status"]


# --- end to end ----------------------------------------------------------------

@workflow(
    purpose="an output edited after its run wrote it, before the archive pass, "
            "is never used: the downstream step is refused naming the file and "
            "the run, the archive pass records no hash for it, and the next "
            "launch of the step that wrote it re-runs it under the same key, "
            "and History shows the output unavailable",
    inputs="head then tail; head's pre-archive output edited in the run archive",
    outputs="a failed tail run naming the file and head's run, a row marked "
            "changed with no content hash, a fresh head run, and a History "
            "payload marking the edited output changed",
)
def test_an_edited_pre_archive_output_is_refused_and_its_step_re_runs(
        tmp_path, monkeypatch):
    """Flips adversarial B3a."""
    from wfc.storage import archive_outputs

    _ = Step(step_num=1, name="Run head",
             purpose="Collect records the output and makes it read-only")
    project, head_run, out = _project(tmp_path, monkeypatch, tail=True)
    assert layout.run_archive_dir(project.root, head_run) in out.parents
    assert not os.access(out, os.W_OK), "a collected output is read-only"

    _ = Step(step_num=2, name="Edit head's output",
             purpose="The user strips the read-only bit and rewrites it in place "
                     "at the same size, then puts its mtime back: collect "
                     "took no hash, so only the change time shows it")
    assert _row(head_run).collect_hash is None
    _edit_in_place_keeping_size_and_mtime(out)
    edited = out.read_bytes()

    _ = Step(step_num=3, name="Run the archive pass",
             purpose="Before any reader has marked the row, the archive pass "
                     "checks the output itself, refuses it naming the file and "
                     "head's run, marks the row and records no hash")
    assert _row(head_run).integrity_status is None
    results = archive_outputs(project.root)
    status = _archive_status(results, head_run)
    assert status.startswith("changed:"), status
    assert str(out) in status and f"run {head_run}" in status
    row = _row(head_run)
    assert (row.content_hash, row.integrity_status) == (None, "changed")

    _ = Step(step_num=4, name="Launch tail",
             purpose="The resolver refuses the marked output, naming the file, "
                     "head's run and the re-run")
    obs_t = drive_target(project, TAIL, monkeypatch=monkeypatch)
    assert obs_t.exit_code(TAIL) != 0
    message = obs_t.run_row(TAIL)["error_message"]
    assert str(out) in message
    assert f"changed after run {head_run} wrote it" in message
    assert f"Re-run run {head_run}" in message

    _ = Step(step_num=5, name="Launch head again",
             purpose="The hit rule finds its output missing, so head re-runs "
                     "under the same key and writes a genuine output")
    obs_h = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs_h.exit_code(HEAD) == 0
    again = obs_h.run_row(HEAD)
    assert again["id"] != head_run and again["cache_source_run_id"] is None
    first = next(r for r in obs_h.run_rows if r["id"] == head_run)
    assert again["cache_key"] == first["cache_key"]
    fresh = Path(obs_h.output_rows_for(HEAD)[0]["artifact_path"])
    assert fresh.read_bytes() != edited

    _ = Step(step_num=6, name="Open History",
             purpose="The runs payload marks the edited output unavailable "
                     "from the row's mark; the re-run's output is available")
    payload = _runs_payload(project.root, monkeypatch)
    assert payload[str(head_run)]["outputAvailability"] == {"data": "changed"}
    assert payload[str(again["id"])]["outputAvailability"] == {"data": "available"}


def _runs_payload(root: Path, monkeypatch) -> dict[str, dict]:
    """GET /api/wfc/runs from a canvas served on ``root``, by run id."""
    from wfc.persistence import reset_engine

    fakes.bind_provider(monkeypatch, None)
    try:
        with canvas_client(root, monkeypatch) as client:
            resp = client.get("/api/wfc/runs")
    finally:
        reset_engine()
    assert resp.status_code == 200, resp.text
    return {r["id"]: r for r in resp.json()}


# --- the output legs -------------------------------------------------------------

@pytest.mark.parametrize("setting", ["stat-then-hash", "hash"])
@pytest.mark.parametrize("kind", ["file", "directory"])
@workflow(
    purpose="a pre-archive output, file or directory, goes through Storage's "
            "one check before it is read, and a same-size edit with the mtime "
            "restored is caught: under the default on collect's stat record "
            "(its change time), under hash, where collect hashed the output, "
            "by the re-hash",
    inputs="one step's collected output, edited in place under each setting",
    outputs="the check's verdict, the row marked changed, the resolver's refusal",
)
def test_a_pre_archive_output_edit_is_caught_before_the_read(
        tmp_path, monkeypatch, kind, setting):
    from wfc.identity import hash_path
    from wfc.storage.integrity import CHANGED, INTACT, check_output
    from wfc.storage.resolve import OUTPUT_MISSING, output_location, resolve_input
    from wfc.storage.resolve import InputUnavailableError

    project, run_id, out = _project(tmp_path, monkeypatch, kind, setting=setting)
    row = _row(run_id)
    if setting == "hash":
        assert row.collect_hash == hash_path(out)
        untouched = check_output(row, out, project.root, setting)
        assert (untouched.status, untouched.hashed) == (INTACT, True)
    else:
        assert row.collect_hash is None
        if kind == "directory":
            assert layout.output_members_stamp(project.root, run_id, row.slot).is_file()
    _edit_in_place_keeping_size_and_mtime(_member(out, kind))

    verdict = check_output(row, out, project.root, setting)
    assert verdict.status == CHANGED
    assert verdict.hashed is (setting == "hash")
    assert output_location(row, project.root) == OUTPUT_MISSING
    assert _row(run_id).integrity_status == "changed"
    with pytest.raises(InputUnavailableError) as exc:
        resolve_input(run_id, "data")
    assert str(out) in str(exc.value) and f"run {run_id}" in str(exc.value)


@pytest.mark.parametrize("setting", ["stat-then-hash", "hash"])
def test_a_touched_but_unchanged_output_passes_only_its_re_hash(
        tmp_path, monkeypatch, setting):
    """Under hash a touched output re-hashes and passes; the default counts it changed."""
    from wfc.storage.resolve import OUTPUT_LOCAL, OUTPUT_MISSING, output_location

    project, run_id, out = _project(tmp_path, monkeypatch, setting=setting)
    _writable(out)
    st = out.stat()
    os.utime(out, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    row = _row(run_id)
    if setting == "hash":
        assert output_location(row, project.root) == OUTPUT_LOCAL
        assert _row(run_id).integrity_status is None
    else:
        assert output_location(row, project.root) == OUTPUT_MISSING
        assert _row(run_id).integrity_status == "changed"


@pytest.mark.parametrize("reader", ["resolver", "archive"])
@pytest.mark.parametrize("change", ["add", "remove", "edit"])
def test_a_directory_output_member_change_is_refused(tmp_path, monkeypatch, change, reader):
    """A member added, removed or edited after collect is caught from the member stamp."""
    from wfc.storage import archive_outputs
    from wfc.storage.resolve import InputUnavailableError, resolve_input

    project, run_id, out = _project(tmp_path, monkeypatch, "directory")
    os.chmod(out, 0o755)
    if change == "add":
        (out / "injected.csv").write_bytes(TAMPERED)
    elif change == "remove":
        os.chmod(out / "sub", 0o755)
        _writable(out / "sub" / "g.csv")
        (out / "sub" / "g.csv").unlink()
    else:
        _writable(out / "f.csv")
        (out / "f.csv").write_bytes(TAMPERED)

    if reader == "resolver":
        with pytest.raises(InputUnavailableError) as exc:
            resolve_input(run_id, "data")
        assert str(out) in str(exc.value)
    else:
        status = _archive_status(archive_outputs(project.root), run_id)
        assert status.startswith("changed:"), status
        assert str(out) in status and f"run {run_id}" in status
    row = _row(run_id)
    assert (row.integrity_status, row.content_hash) == ("changed", None)


def test_a_directory_output_the_listing_refuses_after_collect_is_changed_under_hash(
        tmp_path, monkeypatch):
    """Under hash, a directory output emptied after collect is refused and marked, not error:."""
    from wfc.storage import archive_outputs

    project, run_id, out = _project(tmp_path, monkeypatch, "directory", setting="hash")
    os.chmod(out, 0o755)
    os.chmod(out / "sub", 0o755)
    for member in (out / "f.csv", out / "sub" / "g.csv"):
        _writable(member)
        member.unlink()

    status = _archive_status(archive_outputs(project.root), run_id)
    assert status.startswith("changed:"), status
    assert str(out) in status and f"run {run_id}" in status
    row = _row(run_id)
    assert (row.integrity_status, row.content_hash) == ("changed", None)


# --- the archive pass ---------------------------------------------------------------

@pytest.mark.parametrize("edited", [False, True], ids=["intact", "edited"])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_the_archive_reuses_collects_hash_and_still_verifies_the_copy(
        tmp_path, monkeypatch, kind, edited):
    """Under hash the archive hashes nothing a second time; its copy must equal collect's hash."""
    from wfc.storage import archive_outputs

    project, run_id, out = _project(tmp_path, monkeypatch, kind, setting="hash")
    collected = _row(run_id).collect_hash
    assert collected
    if edited:
        target = _member(out, kind)
        _writable(target)
        target.write_bytes(TAMPERED)

    reads = fakes.spy_content_reads(monkeypatch)
    status = _archive_status(archive_outputs(project.root), run_id)
    row = _row(run_id)
    # The echoed file output equals the sample, whose object already sits at
    # the address; that existing object is checked, the output is not.
    output_hashes = [p for p in reads["hashed"] if Path(p) == out or out in Path(p).parents]
    assert output_hashes == [], "the archive pass hashes no output a second time"
    assert len(reads["copied"]) == (len(DIR_FILES) if kind == "directory" else 1)
    assert all(Path(p) == out or out in Path(p).parents for p in reads["copied"])
    if edited:
        assert status.startswith("changed:"), status
        assert (row.content_hash, row.integrity_status) == (None, "changed")
    else:
        assert status == "archived"
        assert row.content_hash == collected
        assert row.integrity_status is None


def test_a_source_that_changes_during_the_copy_is_never_recorded(tmp_path, monkeypatch):
    """The default re-checks after the copy; a change during it records no hash."""
    from wfc.storage import archive_outputs

    project, run_id, out = _project(tmp_path, monkeypatch)

    def change_during(real, src, target):
        md5 = real(src, target)
        _writable(Path(src))
        with open(src, "ab") as f:
            f.write(b"2,LATE\n")
        return md5

    fakes.fault_at_hashing_copy(monkeypatch, change_during)
    status = _archive_status(archive_outputs(project.root), run_id)
    assert status.startswith("changed:"), status
    row = _row(run_id)
    assert (row.content_hash, row.integrity_status) == (None, "changed")


def test_a_short_write_records_nothing_and_the_next_pass_archives(tmp_path, monkeypatch):
    """A write that fails part-way raises; no hash, no mark, and a later pass succeeds."""
    from wfc.storage import archive_outputs

    project, run_id, out = _project(tmp_path, monkeypatch)
    failures = []

    def short_write(real, src, target):
        if failures:
            return real(src, target)
        failures.append(src)
        Path(target).write_bytes(Path(src).read_bytes()[:3])
        raise OSError(28, "No space left on device")

    fakes.fault_at_hashing_copy(monkeypatch, short_write)
    status = _archive_status(archive_outputs(project.root), run_id)
    assert status.startswith("error:"), status
    row = _row(run_id)
    assert (row.content_hash, row.integrity_status) == (None, None)

    from wfc.identity import hash_path
    written = hash_path(out)  # the archived output leaves the run archive
    assert _archive_status(archive_outputs(project.root), run_id) == "archived"
    assert _row(run_id).content_hash == written


# --- collect under hash -------------------------------------------------------------

def test_an_output_collect_cannot_read_under_hash_fails_the_run(tmp_path, monkeypatch):
    """Under hash an unreadable output ends the run refused-output, naming slot and path."""
    project = build_project(_scenario("file"), root=tmp_path / "proj", monkeypatch=monkeypatch)
    _set_inputs(project.root, "hash")
    fakes.unreadable_output_hash(monkeypatch)
    obs = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs.exit_code(HEAD) != 0
    message = obs.run_row(HEAD)["error_message"]
    assert "slot 'data'" in message and "cannot read to hash it" in message
    assert str(layout.run_archive_dir(project.root, obs.runs[HEAD].run_id)) in message
    assert obs.output_rows_for(HEAD) == []


def test_a_failed_output_record_fails_the_run_naming_the_slot(tmp_path, monkeypatch):
    """Collect's stat record failing fails the run naming the slot; no row is written."""
    project = build_project(_scenario("file"), root=tmp_path / "proj", monkeypatch=monkeypatch)
    fakes.fault_at_record_output(monkeypatch, OSError("disk vanished"))
    obs = drive_target(project, HEAD, monkeypatch=monkeypatch)
    assert obs.exit_code(HEAD) != 0
    row = obs.run_row(HEAD)
    assert row["status"] == "failed"
    assert "slot 'data'" in row["error_message"] and "disk vanished" in row["error_message"]
    assert obs.output_rows_for(HEAD) == []


def test_the_setting_is_read_once_per_hit_pass_and_once_per_push(tmp_path, monkeypatch):
    """One [integrity] inputs read per hit-rule pass and per push.

    The hit-rule pass runs twice: before the archive (the outputs are still
    in the run directory) and after it (the archived-output branch).
    """
    from wfc.execution.claim import MISS, OUTPUTS_MISSING, classify_cache_key
    from wfc.persistence import Method, Module, Run, RunOutput, get_session
    from wfc.storage import archive_outputs, transport

    two = node("two", inputs=[wire(SELECTOR_ID)], outputs={"a": "file", "b": "file"},
               behavior=Behavior(outputs={"a": "id\n1\n", "b": "id\n2\n"}))
    project = build_project(Scenario(nodes=[selector(), two], samples=["S1"]),
                            root=tmp_path / "proj", monkeypatch=monkeypatch)
    target = ("two", "S1", "default")
    obs = drive_target(project, target, monkeypatch=monkeypatch)
    assert obs.exit_code(target) == 0
    run_id = obs.runs[target].run_id
    with get_session() as session:
        run = session.get(Run, run_id)
        method = session.get(Method, run.method_id)
        module = session.get(Module, method.module_id)
        identity = (method.name, module.name, run.sample, run.cache_key)

    reads = fakes.spy_setting_reads(monkeypatch)
    verdict = classify_cache_key(*identity)
    assert verdict.status not in (MISS, OUTPUTS_MISSING)
    assert len(reads) == 1, "one read for the pass, not one per output"

    archive_outputs(project.root, run_id=run_id)
    with get_session() as session:
        hashes = [r.content_hash for r in session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)).all()]
    assert len(set(hashes)) == 2 and all(hashes)
    reads.clear()
    verdict = classify_cache_key(*identity)
    assert verdict.status not in (MISS, OUTPUTS_MISSING)
    assert len(reads) == 1, "one read for the pass over archived outputs too"

    reads.clear()
    outcome = transport.push(hashes, project.root)
    assert not outcome.failed
    assert len(reads) == 1, "one read for the push, not one per entry"
