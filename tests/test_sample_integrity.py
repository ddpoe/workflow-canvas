"""A restored sample is what wfc registered before a step reads it.

The Snakemake restore rule does not run again while a sample's
``.sample_ready`` sentinel stands, so materialize asks Storage's
``check_sample`` before every read: a copy edited in the workspace is
restored from a checked cache entry, with a warning naming the sample.
Restored copies, files and directories alike, are read-only and stamped
only after their bytes match the sample's ``content_hash``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.harness import Behavior, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.persistence import Sample, get_session

TAMPERED = b"id,value\n1,TAMPERED\n"


def _row(name: str) -> Sample:
    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


def _register(root: Path, name: str, files: dict[str, bytes] | None = None,
              data: bytes | None = None) -> Path:
    """Register a file sample (``data``) or a directory sample (``files``)."""
    from wfc.registration import register_sample

    src_root = root.parent / f"{root.name}-src"
    if files is not None:
        src = src_root / name
        for rel, content in files.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(content)
    else:
        src = src_root / f"{name}.csv"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(data or b"id\n1\n")
    register_sample(name, src, project_root=root)
    return src


def _tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in sorted(path.rglob("*")) if p.is_file()}


def _writable(path: Path) -> None:
    """Declared deviation: strip the read-only bits, as a hand edit would."""
    from wfc.storage.cache import _make_writable

    _make_writable(path)


def _replace_view(path: Path, data: bytes) -> None:
    """Save other bytes at a restored view's path as a new file, as an editor that saves by rename does.

    A restored sample is a view that may share its inode with the cache
    object; this edit never writes into it or chmods it, so the cache entry
    stays intact. A write through the view is a change to the cache entry
    (``test_a_changed_copy_is_not_repaired_from_a_changed_cache_entry``).
    """
    from wfc.storage import links
    from wfc.storage.cache import open_dirs

    open_dirs(path.parent)
    tmp = path.with_name(path.name + ".edited")
    tmp.write_bytes(data)
    links.replace_over(tmp, path)


def _assert_view(path: Path, root: Path, md5: str) -> None:
    """``path`` is a read-only view of its intact cache object."""
    from wfc.storage import links

    obj = layout.dvc_cache_entry(root, md5)
    assert path.read_bytes() == obj.read_bytes()
    assert not os.access(path, os.W_OK), f"{path} is read-only"
    assert not os.access(obj, os.W_OK), "the cache object stays read-only"
    # The view's own folder is read-only; the tier is probed beside it.
    beside = next(d for d in path.parents if os.access(d, os.W_OK))
    if links.probe_pair(obj.parent, beside).tier == links.TIER_HARDLINK:
        assert os.path.samefile(path, obj), f"{path} is a view of its cache object"


@workflow(
    purpose="an edited restored sample is put back to its registered bytes "
            "before the step reads it, even with the ready sentinel present: "
            "the step log and console warn naming the sample, the output is "
            "computed from the registered bytes, and no later launch is "
            "served a result over edited bytes",
    inputs="a one-step pipeline over a file sample whose restored copy is "
           "edited in the workspace while its .sample_ready sentinel stands",
    outputs="the run's output equal to the genuine input, a warning naming "
            "the sample on stderr, a read-only re-stamped copy",
)
def test_an_edited_restored_sample_is_repaired_before_the_step_reads_it(
        tmp_path, monkeypatch, capfd):
    """Flips adversarial B1a."""
    _ = Step(step_num=1, name="Build the project with the sentinel standing",
             purpose="Production restore_sample writes the copy; the sentinel "
                     "keeps the engine from restoring it again")
    project = build_project(
        Scenario(nodes=[selector(), node("n1", inputs=[wire(SELECTOR_ID)],
                                         behavior=Behavior(echo_input="data"))],
                 samples=["S1"], sample_ready_sentinel=True),
        root=tmp_path / "proj", monkeypatch=monkeypatch)
    data = layout.restored_sample_path(project.root, "S1", "S1.csv")
    genuine = data.read_bytes()
    assert not os.access(data, os.W_OK), "a restored file sample is read-only"

    _ = Step(step_num=2, name="Edit the restored copy",
             purpose="The user saves other bytes at its path; the cache entry "
                     "is untouched")
    _replace_view(data, TAMPERED)
    assert layout.dvc_cache_entry(project.root, _row("S1").content_hash).read_bytes() == genuine
    capfd.readouterr()

    _ = Step(step_num=3, name="Launch the step",
             purpose="Materialize repairs the copy and warns naming the sample")
    t = ("n1", "S1", "default")
    obs = drive_target(project, t, monkeypatch=monkeypatch)
    assert obs.exit_code(t) == 0
    out = Path(obs.output_rows_for(t)[0]["artifact_path"]).read_bytes()
    assert out == genuine
    assert data.read_bytes() == genuine
    _assert_view(data, project.root, _row("S1").content_hash)
    err = capfd.readouterr().err
    assert "WARNING: sample 'S1'" in err and "restored the registered bytes" in err
    step_log = obs.runs[t].stderr_log
    assert step_log is not None, "the run wrote a step log"
    logged = step_log.read_text(encoding="utf-8")
    assert "WARNING: sample 'S1'" in logged
    assert "restored the registered bytes" in logged
    assert layout.sample_stamp(project.root, "S1").exists()

    _ = Step(step_num=4, name="Launch again",
             purpose="The hit serves the result computed from the registered bytes")
    obs2 = drive_target(project, t, monkeypatch=monkeypatch)
    assert obs2.exit_code(t) == 0
    from wfc.storage.resolve import resolve_input
    assert Path(resolve_input(obs2.run_row(t)["id"], "data")).read_bytes() == genuine


@workflow(
    purpose="a directory sample is restored read-only, and an edited member "
            "or an injected file is put back to the registered directory "
            "before materialize hands it to the step",
    inputs="a registered directory sample, restored, then edited and given "
           "an extra file in the workspace",
    outputs="the delivered directory equal to the registered one, read-only",
)
def test_an_edited_directory_sample_is_delivered_as_registered(tmp_project, capfd):
    """Flips adversarial B2b."""
    from wfc.execution.materialize import run_materialize
    from wfc.storage import restore_sample

    _ = Step(step_num=1, name="Register and restore a directory sample",
             purpose="The restore is read-only")
    registered = {"a.csv": b"id\n1\n", "sub/b.csv": b"id\n2\n"}
    _register(tmp_project, "D1", files=registered)
    restore_sample("D1", project_root=tmp_project)
    restored = layout.restored_sample_path(tmp_project, "D1", _row("D1").registered_path)
    assert _tree(restored) == registered
    assert not os.access(restored / "a.csv", os.W_OK)

    _ = Step(step_num=2, name="Edit a member and inject a file",
             purpose="Other bytes saved at a member's path and a file added; "
                     "the cache entry is untouched")
    _replace_view(restored / "a.csv", b"id\n999\n")
    (restored / "zz_injected.csv").write_bytes(TAMPERED)
    capfd.readouterr()

    _ = Step(step_num=3, name="Materialize",
             purpose="The delivered directory is the registered one, read-only")
    res = run_materialize("n", "D1", None, [], None, None)
    assert res["ok"], res
    delivered = Path(res["slot_paths"]["data"][0])
    assert _tree(delivered) == registered
    import hashlib
    _assert_view(delivered / "a.csv", tmp_project, hashlib.md5(registered["a.csv"]).hexdigest())
    assert "WARNING: sample 'D1'" in capfd.readouterr().err


@pytest.mark.parametrize("via", ["object", "view"])
@pytest.mark.parametrize("pushed", [True, False], ids=["pushed", "unpushed"])
@pytest.mark.parametrize("kind", ["file", "directory"])
@workflow(
    purpose="a sample whose own cache entry changed is never restored from "
            "it, whether the change was made in the cache or arrived through "
            "a hardlinked restored view: recorded pushed, the entry is pulled "
            "again and the copy repaired from the genuine bytes as a "
            "read-only view; never pushed, the repair is refused naming the "
            "sample and re-registration, and no stamp is left",
    inputs="a restored sample whose copy and whose cache object were both "
           "edited, or whose hardlinked view was written in place, pushed to "
           "the real local remote at registration or left to the push worker",
    outputs="a repaired copy, or a refused SampleCheck and a materialize "
            "failure naming the sample, with no stamp",
)
def test_a_changed_copy_is_not_repaired_from_a_changed_cache_entry(
        tmp_project, monkeypatch, kind, pushed, via):
    from wfc.execution.materialize import run_materialize
    from wfc.persistence import PushStatus
    from wfc.storage import SAMPLE_REPAIRED, check_sample, restore_sample

    _ = Step(step_num=1, name="Register and restore",
             purpose="A standalone registration pushes at once; inside a "
                     "pipeline the push is left to the worker")
    if not pushed:
        monkeypatch.setenv("WFC_PIPELINE_ID", "p")
    if kind == "file":
        _register(tmp_project, "S1", data=b"id\n1\n")
    else:
        _register(tmp_project, "S1", files={"a.csv": b"id\n1\n"})
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    restore_sample("S1", project_root=tmp_project)
    row = _row("S1")
    assert (row.push_status == PushStatus.pushed.value) is pushed
    genuine = _tree(tmp_project.parent / f"{tmp_project.name}-src")
    copy = layout.restored_sample_path(tmp_project, "S1", row.registered_path)

    _ = Step(step_num=2, name="Edit the copy and the cache object",
             purpose="The object a repair would copy from is no longer genuine")
    if kind == "file":
        obj = layout.dvc_cache_entry(tmp_project, row.content_hash)
        target = copy
    else:
        from wfc.storage.cache import read_manifest
        ((_rel, member),) = read_manifest(tmp_project, row.content_hash)
        obj = layout.dvc_cache_entry(tmp_project, member)
        target = copy / "a.csv"
    if via == "view":
        # The user unprotects the restored view and writes into it: a
        # hardlink shares the cache object's inode, so the entry changes too.
        if not os.path.samefile(target, obj):
            pytest.skip("the restored view is not a hardlink on this filesystem")
        os.chmod(target, 0o644)
        target.write_bytes(TAMPERED)
        assert obj.read_bytes() == TAMPERED, "the write reached the cache entry"
    else:
        _writable(obj)
        obj.write_bytes(b"id\nBAD\n")
        _writable(copy)
        target.write_bytes(TAMPERED)

    _ = Step(step_num=3, name="Check, then materialize",
             purpose="Pushed: repaired from the pulled bytes. Unpushed: both "
                     "refuse naming the sample and re-registration")
    checked = check_sample("S1", tmp_project)
    if pushed:
        assert checked.status == SAMPLE_REPAIRED
        restored = _tree(copy) if copy.is_dir() else {copy.name: copy.read_bytes()}
        assert set(restored.values()) == set(genuine.values())
        assert layout.sample_stamp(tmp_project, "S1").exists()
        _assert_view(target, tmp_project, hashlib.md5(target.read_bytes()).hexdigest())
        assert obj.read_bytes() in set(genuine.values()), "the entry was pulled again"
        return
    assert checked.refused
    assert "sample 'S1'" in checked.message and "Re-register" in checked.message
    assert not layout.sample_stamp(tmp_project, "S1").exists()
    res = run_materialize("n", "S1", None, [], None, None)
    assert not res["ok"]
    assert "sample 'S1'" in res["error_message"]


@pytest.mark.parametrize("pushed", [True, False], ids=["pushed", "unpushed"])
@workflow(
    purpose="a step over a sample whose cache entry is malformed (a directory "
            "tree at an address without the '.dir' suffix) and whose restored "
            "copy is a directory is refused, pushed or not: the run row is "
            "marked failed naming the malformed entry and the re-registration, "
            "and nothing raises",
    inputs="a one-step pipeline over a sample registered pushed at once or "
           "left to the push worker, whose cache object and restored copy are "
           "then directory trees, with the ready sentinel standing",
    outputs="a failed step and run row whose message names the malformed "
            "entry and `wfc register-sample --name S1`, no traceback",
)
def test_a_step_over_a_malformed_sample_entry_is_refused_naming_the_re_registration(
        tmp_path, monkeypatch, capfd, pushed):
    from wfc.persistence import PushStatus

    _ = Step(step_num=1, name="Build the project with the sentinel standing",
             purpose="Registered through production; inside a pipeline the "
                     "push is left to the worker")
    if not pushed:
        monkeypatch.setenv("WFC_PIPELINE_ID", "p")
    project = build_project(
        Scenario(nodes=[selector(), node("n1", inputs=[wire(SELECTOR_ID)],
                                         behavior=Behavior(echo_input="data"))],
                 samples=["S1"], sample_ready_sentinel=True),
        root=tmp_path / "proj", monkeypatch=monkeypatch)
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    row = _row("S1")
    assert (row.push_status == PushStatus.pushed.value) is pushed

    _ = Step(step_num=2, name="Give the entry and its copy the old directory shape",
             purpose="A directory tree at the unsuffixed address, and a "
                     "directory restored copy, as the old shape restored it")
    obj = layout.dvc_cache_entry(project.root, row.content_hash)
    copy = layout.restored_sample_path(project.root, "S1", row.registered_path)
    from wfc.storage.cache import open_dirs
    for tree in (obj, copy):
        _writable(tree)
        open_dirs(tree.parent)  # POSIX refuses an unlink in a read-only folder
        tree.unlink()
        tree.mkdir()
        (tree / "x.csv").write_bytes(b"id\n1\n")
    capfd.readouterr()

    _ = Step(step_num=3, name="Launch the step",
             purpose="Materialize's sample check refuses it; the run row is "
                     "failed with the refusal")
    t = ("n1", "S1", "default")
    obs = drive_target(project, t, monkeypatch=monkeypatch)
    assert obs.exit_code(t) != 0
    run = obs.run_row(t)
    assert run["status"] == "failed"
    message = run["error_message"]
    assert "sample 'S1'" in message, message
    assert f"Cache entry {row.content_hash} is a malformed cache entry" in message, message
    assert "wfc register-sample --name S1" in message, message
    assert "never pushed" not in message, message
    assert obs.output_rows_for(t) == []
    assert "Traceback" not in capfd.readouterr().err


def test_the_restore_copy_refuses_bytes_that_are_not_the_samples_hash(tmp_project):
    """The hashing copy of a changed object leaves no copy and no stamp."""
    from wfc.storage.cache import ChangedEntryError, restore_from_cache

    _register(tmp_project, "S1", data=b"id\n1\n")
    h = _row("S1").content_hash
    obj = layout.dvc_cache_entry(tmp_project, h)
    _writable(obj)
    obj.write_bytes(b"id\nBAD\n")
    dest = tmp_project / "out.csv"
    stamp = layout.sample_stamp(tmp_project, "S1")
    with pytest.raises(ChangedEntryError):
        restore_from_cache(h, dest, tmp_project, stamp=stamp)
    assert not dest.exists() and not stamp.exists()


def _drive_over_an_edited_copy(tmp_path, monkeypatch, behavior: Behavior):
    """Launch one step over a restored sample edited in the workspace.

    Materialize repairs the copy and hands dispatch a warning for the step
    log; ``behavior`` is what the method then does.
    """
    project = build_project(
        Scenario(nodes=[selector(), node("n1", inputs=[wire(SELECTOR_ID)],
                                         behavior=behavior)],
                 samples=["S1"], sample_ready_sentinel=True),
        root=tmp_path / "proj", monkeypatch=monkeypatch)
    data = layout.restored_sample_path(project.root, "S1", "S1.csv")
    _replace_view(data, TAMPERED)
    t = ("n1", "S1", "default")
    return t, drive_target(project, t, monkeypatch=monkeypatch)


def test_the_step_log_holds_wfcs_warning_then_the_methods_output(tmp_path, monkeypatch):
    """The warning is in stderr.log before the method starts, and the
    method's stderr follows it."""
    said = "the method's own line"
    t, obs = _drive_over_an_edited_copy(
        tmp_path, monkeypatch, Behavior(echo_input="data", stderr=f"{said}\n"))
    assert obs.exit_code(t) == 0
    at_launch = obs.runs[t].step_log_at_launch
    assert at_launch is not None, "the step log existed when the method started"
    assert at_launch.startswith("WARNING: sample 'S1'"), at_launch
    assert said not in at_launch
    logged = obs.runs[t].stderr_log.read_text(encoding="utf-8")
    assert logged.startswith(at_launch), "the log was appended to, not rewritten"
    assert logged.startswith("WARNING: sample 'S1'"), logged
    assert said in logged, "the method's output was kept"
    assert logged.rstrip().endswith(said), "the method's output follows the warning"


def test_a_failed_methods_error_is_its_own_not_wfcs_warning(tmp_path, monkeypatch):
    """The method-failed error is read from what the method wrote."""
    error = "ValueError: the method's own error"
    t, obs = _drive_over_an_edited_copy(
        tmp_path, monkeypatch, Behavior(stderr=f"{error}\n", exit_code=3))
    assert obs.exit_code(t) != 0
    row = obs.run_row(t)
    assert row["error_message"] == error
    assert "WARNING" not in row["error_traceback"], row["error_traceback"]
    assert row["error_traceback"].strip() == error
    at_launch = obs.runs[t].step_log_at_launch
    assert at_launch is not None and at_launch.startswith("WARNING: sample 'S1'"), \
        "the warning was in the step log before the method started"
    logged = obs.runs[t].stderr_log.read_text(encoding="utf-8")
    assert logged.startswith("WARNING: sample 'S1'"), "the step log still holds the warning"


def test_a_step_log_that_cannot_be_started_fails_the_step_before_the_launch(
        tmp_path, monkeypatch, capfd):
    """A step log wfc cannot write fresh is never appended to: the step
    fails naming the log and the cause, and no method process starts, so
    the console does not say the method's execution failed."""
    from tests.fixtures.fakes import refuse_path_write

    refuse_path_write(monkeypatch, "stderr.log")
    t, obs = _drive_over_an_edited_copy(
        tmp_path, monkeypatch, Behavior(echo_input="data"))
    err = capfd.readouterr().err
    assert "ERROR: could not start the step log" in err, err
    assert "the method was not launched" in err, err
    assert "method execution failed" not in err, err
    assert obs.exit_code(t) != 0
    assert obs.runs[t].dispatch_cmd is None, "no method process started"
    row = obs.run_row(t)
    assert row["status"] == "failed"
    assert "could not start the step log" in row["error_message"], row["error_message"]
    assert "stderr.log" in row["error_message"]
    assert "Permission denied" in row["error_message"]
    assert obs.output_rows_for(t) == []


def test_an_unstamped_copy_is_hashed_and_stamped(tmp_project):
    """A copy restored before stamps existed is hashed once, then stamped."""
    from wfc.storage import SAMPLE_INTACT, check_sample, restore_sample

    _register(tmp_project, "S1", data=b"id\n1\n")
    restore_sample("S1", project_root=tmp_project)
    stamp = layout.sample_stamp(tmp_project, "S1")
    stamp.unlink()
    assert check_sample("S1", tmp_project).status == SAMPLE_INTACT
    assert stamp.exists()
