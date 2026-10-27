"""``wfc verify`` re-hashes everything wfc holds and lists what is not what it recorded.

The project state is built through production paths: register a sample,
run steps, archive, push to the project's local remote, then edit bytes on
disk as a user would (stripping the read-only bits first, a declared
deviation shared with the other integrity witnesses). A restored sample or a
checkout member is a view that may share its inode with the cache object, so
a change to the view alone is a new file saved at its path; a write through
the view changes the cache entry itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures import fakes
from tests.harness import Behavior, Scenario, build_project, drive_target, node, selector, wire
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.persistence import Blob, PushStatus, RunOutput, Sample, get_session

TAMPERED = b"id,value\n1,TAMPERED\n"
DIR_FILES = {"f.csv": "id,value\n1,DIR\n", "sub/g.csv": "id,value\n2,DIR\n"}
KINDS = ["samples", "outputs", "objects", "checkouts", "blobs"]


def _writable(path: Path) -> None:
    """Declared deviation: strip the read-only bits, as a hand edit would."""
    from wfc.storage.cache import _make_writable

    _make_writable(path)


def _edit(path: Path, data: bytes = TAMPERED) -> None:
    _writable(path)
    path.write_bytes(data)


def _replace_view(path: Path, data: bytes = TAMPERED, *, keep_mtime: bool = False) -> None:
    """Save other bytes at a view's path as a new file, as an editor that saves by rename does.

    A restored sample or a checkout member is a view that may share its
    inode with the cache object; the edit never writes into it or chmods
    it, so the cache entry stays intact.
    """
    from wfc.storage import links
    from wfc.storage.cache import open_dirs

    before = path.stat()
    open_dirs(path.parent)
    tmp = path.with_name(path.name + ".edited")
    tmp.write_bytes(data)
    if keep_mtime:
        os.utime(tmp, ns=(before.st_atime_ns, before.st_mtime_ns))
    links.replace_over(tmp, path)


def _verify(capsys, *args: str) -> tuple[int, str]:
    from wfc.cli import cli_main

    capsys.readouterr()
    code = cli_main(["verify", *args])
    return code, capsys.readouterr().out


def _subjects(report: dict, key: str = "problems") -> dict[str, list[str]]:
    return {kind: sorted(p["subject"] for p in report[key][kind]) for kind in KINDS}


def _output(run_id: int) -> RunOutput:
    with get_session() as session:
        return session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).one()


def _scenario() -> Scenario:
    def file_node(node_id: str) -> object:
        return node(node_id, inputs=[wire(SELECTOR_ID)],
                    behavior=Behavior(outputs={"data": f"id,value\n1,{node_id}\n"}))

    return Scenario(
        nodes=[selector(), file_node("pre"), file_node("pushed"), file_node("unpushed"),
               node("tree", inputs=[wire(SELECTOR_ID)], outputs={"data": "directory"},
                    behavior=Behavior(outputs={"data": dict(DIR_FILES)}))],
        samples=["S1"])


@workflow(
    purpose="wfc verify re-hashes every sample, output, cache object, checkout "
            "and blob, lists each changed one grouped by kind and exits "
            "non-zero; --json parses; --repair puts back the sample copy, the "
            "checkout, and the pushed object and blob, and lists the changed "
            "pre-archive output and the unpushed object, which a second verify "
            "still lists alone",
    inputs="a project with one changed item of each kind: a restored sample "
           "copy, a pre-archive output, an archived object pushed and one "
           "unpushed, a checkout member and a pushed blob",
    outputs="exit codes 1/1/1, the grouped text and JSON reports, the genuine "
            "bytes back where wfc could restore them",
)
def test_verify_lists_every_changed_kind_and_repair_fixes_what_wfc_can(
        tmp_path, monkeypatch, capsys):
    """One changed item of each kind: listed, then repaired where wfc can."""
    from wfc.storage import archive_outputs, cache_push, resolve_output

    _ = Step(step_num=1, name="Build, run, archive and push",
             purpose="Production registration, runs, archive pass and push; the "
                     "unpushed run is archived after the push")
    project = build_project(_scenario(), root=tmp_path / "proj", monkeypatch=monkeypatch)
    root = project.root
    runs = {}
    for node_id in ("pre", "pushed", "unpushed", "tree"):
        target = (node_id, "S1", "default")
        obs = drive_target(project, target, monkeypatch=monkeypatch)
        assert obs.exit_code(target) == 0
        runs[node_id] = obs.runs[target].run_id
    archive_outputs(root, run_id=runs["pushed"])
    archive_outputs(root, run_id=runs["tree"])
    cache_push(root)
    archive_outputs(root, run_id=runs["unpushed"])
    pushed, unpushed, tree = (_output(runs[n]) for n in ("pushed", "unpushed", "tree"))
    assert pushed.push_status == tree.push_status == PushStatus.pushed.value
    assert unpushed.content_hash and unpushed.push_status != PushStatus.pushed.value
    checkout = Path(resolve_output(runs["tree"], "data", project_dir=root)[0])
    with get_session() as session:
        sample = session.exec(select(Sample).where(Sample.name == "S1")).one()
        blob = session.exec(select(Blob).where(
            Blob.push_status == PushStatus.pushed.value)).first()
    assert blob is not None, "the launch stored a pipeline document, pushed"
    copy = layout.restored_sample_path(root, "S1", sample.registered_path)
    assert copy.is_file()
    genuine = {
        "copy": copy.read_bytes(),
        "pushed": layout.dvc_cache_entry(root, pushed.content_hash).read_bytes(),
        "member": (checkout / "f.csv").read_bytes(),
        "blob": layout.dvc_cache_entry(root, blob.hash).read_bytes(),
    }

    _ = Step(step_num=2, name="Change one item of each kind",
             purpose="Hand edits in the workspace, the run archive, the cache "
                     "and a checkout")
    _replace_view(copy)
    _edit(Path(_output(runs["pre"]).artifact_path))
    _edit(layout.dvc_cache_entry(root, pushed.content_hash))
    _edit(layout.dvc_cache_entry(root, unpushed.content_hash))
    _replace_view(checkout / "f.csv")
    _edit(layout.dvc_cache_entry(root, blob.hash), b"not the document\n")

    _ = Step(step_num=3, name="wfc verify",
             purpose="One line per problem, grouped by kind, and a non-zero exit")
    code, text = _verify(capsys)
    assert code == 1
    for kind in KINDS:
        assert f"{kind} (problems: {2 if kind == 'objects' else 1}):" in text
    assert "wfc verify: 6 problem(s)" in text
    assert "sample 'S1' restored copy" in text
    assert f"run {runs['pre']} output 'data'" in text
    assert f"changed after run {runs['pre']} wrote it" in text

    _ = Step(step_num=4, name="wfc verify --json",
             purpose="The same problems, grouped by kind, for CI")
    code, out = _verify(capsys, "--json")
    assert code == 1
    report = json.loads(out)
    assert report["ok"] is False
    assert _subjects(report) == {
        "samples": ["sample 'S1' restored copy"],
        "outputs": [f"run {runs['pre']} output 'data'"],
        "objects": sorted([f"run {runs['pushed']} output 'data' cache entry",
                           f"run {runs['unpushed']} output 'data' cache entry"]),
        "checkouts": [f"checkout of {tree.content_hash}"],
        "blobs": [f"blob {blob.kind} cache entry"],
    }
    assert _output(runs["pre"]).integrity_status == "changed"

    _ = Step(step_num=5, name="wfc verify --repair",
             purpose="The sample copy, the checkout and the pushed object and "
                     "blob are put back; the output and the unpushed object are "
                     "listed with their repair, and the exit stays non-zero")
    code, out = _verify(capsys, "--repair", "--json")
    assert code == 1
    report = json.loads(out)
    assert _subjects(report, "repaired") == {
        "samples": ["sample 'S1' restored copy"], "outputs": [],
        "objects": [f"run {runs['pushed']} output 'data' cache entry"],
        "checkouts": [f"checkout of {tree.content_hash}"],
        "blobs": [f"blob {blob.kind} cache entry"],
    }
    left = report["problems"]
    assert f"Re-run run {runs['pre']}" in left["outputs"][0]["action"]
    assert f"Re-run run {runs['unpushed']}" in left["objects"][0]["action"]
    assert copy.read_bytes() == genuine["copy"]
    assert not os.access(copy, os.W_OK)
    assert layout.dvc_cache_entry(root, pushed.content_hash).read_bytes() == genuine["pushed"]
    assert (checkout / "f.csv").read_bytes() == genuine["member"]
    assert not os.access(checkout / "f.csv", os.W_OK)
    assert layout.dvc_cache_entry(root, blob.hash).read_bytes() == genuine["blob"]
    # The sample copy and the checkout member are rebuilt as views of their
    # cache objects, not as copies, where the filesystem links.
    from wfc.storage import links
    sample_obj = layout.dvc_cache_entry(root, sample.content_hash)
    member_obj = layout.dvc_cache_entry(root, hashlib.md5(genuine["member"]).hexdigest())
    for view, obj in ((copy, sample_obj), (checkout / "f.csv", member_obj)):
        # The view's own folder is read-only; the tier is probed beside it.
        beside = next(d for d in view.parents if os.access(d, os.W_OK))
        if links.probe_pair(obj.parent, beside).tier == links.TIER_HARDLINK:
            assert os.path.samefile(view, obj), f"{view} is rebuilt as a view"

    _ = Step(step_num=6, name="wfc verify again",
             purpose="Only what could not be repaired is listed")
    code, out = _verify(capsys, "--json")
    assert code == 1
    assert _subjects(json.loads(out)) == {
        "samples": [], "outputs": [f"run {runs['pre']} output 'data'"],
        "objects": [f"run {runs['unpushed']} output 'data' cache entry"],
        "checkouts": [], "blobs": [],
    }


def _sample(name: str) -> Sample:
    with get_session() as session:
        return session.exec(select(Sample).where(Sample.name == name)).one()


@workflow(
    purpose="a changed sample cache entry is pulled again by --repair when "
            "pushed and listed, not repaired, when never pushed; an entry two "
            "samples share is listed, and repaired, under each of them",
    inputs="samples A and A2 registered from the same bytes (one cache entry) "
           "and pushed; sample B registered inside a pipeline, never pushed; "
           "both entries edited in the cache",
    outputs="exit codes 1/1/1; A's and A2's entry repaired and back to its "
            "bytes; B's listed with its re-registration and still changed",
)
def test_a_changed_sample_entry_is_pulled_again_when_pushed_and_listed_when_not(
        tmp_project, monkeypatch, capsys):
    """A sample's changed cache entry: pulled again when pushed, listed when not."""
    from wfc.registration import register_sample

    _ = Step(step_num=1, name="Register: two pushed at once, one left pending",
             purpose="A and A2 share one entry, pushed by standalone "
                     "registration; B is registered inside a pipeline, so its "
                     "push is left to the worker and no record shows it pushed")
    src = tmp_project.parent / "a.csv"
    src.write_bytes(b"id,value\n1,A\n")
    register_sample("A", src, project_root=tmp_project)
    register_sample("A2", src, project_root=tmp_project)
    src_b = tmp_project.parent / "b.csv"
    src_b.write_bytes(b"id,value\n1,B\n")
    monkeypatch.setenv("WFC_PIPELINE_ID", "p")
    register_sample("B", src_b, project_root=tmp_project)
    monkeypatch.delenv("WFC_PIPELINE_ID")
    a, a2, b = _sample("A"), _sample("A2"), _sample("B")
    assert a.content_hash == a2.content_hash
    assert a.push_status == a2.push_status == PushStatus.pushed.value
    assert b.push_status != PushStatus.pushed.value
    shared = layout.dvc_cache_entry(tmp_project, a.content_hash)
    genuine = shared.read_bytes()

    _ = Step(step_num=2, name="Change both entries in the cache",
             purpose="Hand edits of the shared pushed entry and of B's")
    _edit(shared)
    _edit(layout.dvc_cache_entry(tmp_project, b.content_hash))

    _ = Step(step_num=3, name="wfc verify",
             purpose="Each sample naming a changed entry is listed")
    code, out = _verify(capsys, "--json")
    assert code == 1
    assert _subjects(json.loads(out))["samples"] == [
        "sample 'A' cache entry", "sample 'A2' cache entry", "sample 'B' cache entry"]

    _ = Step(step_num=4, name="wfc verify --repair",
             purpose="The shared pushed entry is pulled again once and listed "
                     "repaired under both samples; B's is listed with its repair")
    pulled = fakes.spy_pull_again(monkeypatch)
    code, out = _verify(capsys, "--repair", "--json")
    assert code == 1
    assert pulled == [a.content_hash], "the shared entry is pulled again once per verify"
    report = json.loads(out)
    assert _subjects(report, "repaired")["samples"] == [
        "sample 'A' cache entry", "sample 'A2' cache entry"]
    left = report["problems"]["samples"]
    assert [p["subject"] for p in left] == ["sample 'B' cache entry"]
    assert "never pushed" in left[0]["action"]
    assert "wfc register-sample --name B" in left[0]["action"]
    assert shared.read_bytes() == genuine
    assert layout.dvc_cache_entry(tmp_project, b.content_hash).read_bytes() == TAMPERED

    _ = Step(step_num=5, name="wfc verify again",
             purpose="Only B's entry is still listed")
    code, out = _verify(capsys, "--json")
    assert code == 1
    assert _subjects(json.loads(out))["samples"] == ["sample 'B' cache entry"]


@workflow(
    purpose="an archived object deleted from the local cache and never pushed "
            "is listed as lost and exits non-zero; a changed checkout whose "
            "never-pushed entry is itself changed is listed and not rebuilt",
    inputs="a file output and a directory output, archived and never pushed; "
           "the file's object deleted; one directory member object and the "
           "checkout's copy of it edited",
    outputs="exit code 1 with and without --repair; nothing repaired; the "
            "checkout still changed",
)
def test_a_lost_object_and_a_checkout_that_cannot_be_rebuilt_are_listed(
        tmp_path, monkeypatch, capsys):
    """A lost object is named lost; a checkout of a changed entry is not rebuilt."""
    import hashlib

    from wfc.storage import archive_outputs, resolve_output

    _ = Step(step_num=1, name="Build, run and archive, no push",
             purpose="Production runs and archive pass; nothing is pushed")
    scenario = Scenario(
        nodes=[selector(),
               node("lost", inputs=[wire(SELECTOR_ID)],
                    behavior=Behavior(outputs={"data": "id,value\n1,lost\n"})),
               node("tree", inputs=[wire(SELECTOR_ID)], outputs={"data": "directory"},
                    behavior=Behavior(outputs={"data": dict(DIR_FILES)}))],
        samples=["S1"])
    project = build_project(scenario, root=tmp_path / "proj", monkeypatch=monkeypatch)
    root = project.root
    runs = {}
    for node_id in ("lost", "tree"):
        target = (node_id, "S1", "default")
        obs = drive_target(project, target, monkeypatch=monkeypatch)
        assert obs.exit_code(target) == 0
        runs[node_id] = obs.runs[target].run_id
        archive_outputs(root, run_id=runs[node_id])
    lost, tree = _output(runs["lost"]), _output(runs["tree"])
    assert lost.content_hash and tree.content_hash
    assert PushStatus.pushed.value not in (lost.push_status, tree.push_status)
    checkout = Path(resolve_output(runs["tree"], "data", project_dir=root)[0])
    member_md5 = hashlib.md5((checkout / "f.csv").read_bytes()).hexdigest()

    _ = Step(step_num=2, name="Lose one object, change the other entry and its checkout",
             purpose="Delete the file output's object; edit a directory member "
                     "object and the checkout's copy of it")
    gone = layout.dvc_cache_entry(root, lost.content_hash)
    _writable(gone)
    gone.unlink()
    _edit(layout.dvc_cache_entry(root, member_md5))
    _writable(checkout)
    (checkout / "f.csv").write_bytes(TAMPERED)

    _ = Step(step_num=3, name="wfc verify",
             purpose="The lost object is named, and the exit is non-zero")
    code, text = _verify(capsys)
    assert code == 1
    assert (f"run {runs['lost']} output 'data' cache entry at {gone}: lost: not "
            f"in the local cache and never pushed") in text

    _ = Step(step_num=4, name="wfc verify --repair",
             purpose="Nothing can be repaired: the lost object, the changed "
                     "entry and the checkout are listed with why")
    code, out = _verify(capsys, "--repair", "--json")
    assert code == 1
    report = json.loads(out)
    assert _subjects(report, "repaired") == {kind: [] for kind in KINDS}
    left = report["problems"]
    assert _subjects(report)["objects"] == sorted([
        f"run {runs['lost']} output 'data' cache entry",
        f"run {runs['tree']} output 'data' cache entry"])
    by_subject = {p["subject"]: p for p in left["objects"]}
    lost_problem = by_subject[f"run {runs['lost']} output 'data' cache entry"]
    assert lost_problem["detail"] == "lost: not in the local cache and never pushed"
    assert f"Re-run run {runs['lost']}" in lost_problem["action"]
    assert [p["subject"] for p in left["checkouts"]] == [f"checkout of {tree.content_hash}"]
    assert "cannot be rebuilt" in left["checkouts"][0]["action"]
    assert (checkout / "f.csv").read_bytes() == TAMPERED


def test_a_restore_that_pulls_after_a_failed_pull_shows_the_entry_repaired(
        tmp_project, monkeypatch, capsys):
    """The restore's own pull again is what later records see.

    A's step-1.1 pull of the shared entry is refused; the restore of A's
    changed copy checks the entry first and pulls it again itself. A's entry
    and A2's, listed after, show it repaired, not the refused pull.
    """
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    src = tmp_project.parent / "a.csv"
    src.write_bytes(b"id,value\n1,A\n")
    register_sample("A", src, project_root=tmp_project)
    register_sample("A2", src, project_root=tmp_project)
    restore_sample("A", project_root=tmp_project)
    a = _sample("A")
    assert a.push_status == PushStatus.pushed.value
    copy = layout.restored_sample_path(tmp_project, "A", a.registered_path)
    shared = layout.dvc_cache_entry(tmp_project, a.content_hash)
    genuine = shared.read_bytes()
    _edit(shared)
    _edit(copy)

    pulled = fakes.fail_first_pull_again(monkeypatch)
    code, out = _verify(capsys, "--repair", "--json")
    report = json.loads(out)
    assert pulled == [a.content_hash, a.content_hash], "refused once, then the restore's"
    assert _subjects(report, "repaired")["samples"] == [
        "sample 'A' cache entry", "sample 'A' restored copy", "sample 'A2' cache entry"]
    assert report["problems"]["samples"] == [] and code == 0
    assert shared.read_bytes() == genuine and copy.read_bytes() == genuine


def test_a_changed_checkout_whose_entry_is_not_local_is_listed_not_rebuilt(
        tmp_path, monkeypatch, capsys):
    """A changed checkout whose entry is not complete locally says it cannot be rebuilt here.

    The directory output is pushed, so its missing member is not listed
    (the next read pulls it); the changed checkout cannot be rebuilt here
    and says so.
    """
    import hashlib

    from wfc.storage import archive_outputs, cache_push, resolve_output

    scenario = Scenario(
        nodes=[selector(),
               node("tree", inputs=[wire(SELECTOR_ID)], outputs={"data": "directory"},
                    behavior=Behavior(outputs={"data": dict(DIR_FILES)}))],
        samples=["S1"])
    project = build_project(scenario, root=tmp_path / "proj", monkeypatch=monkeypatch)
    root = project.root
    target = ("tree", "S1", "default")
    obs = drive_target(project, target, monkeypatch=monkeypatch)
    assert obs.exit_code(target) == 0
    run_id = obs.runs[target].run_id
    archive_outputs(root, run_id=run_id)
    cache_push(root)
    tree = _output(run_id)
    assert tree.push_status == PushStatus.pushed.value
    checkout = Path(resolve_output(run_id, "data", project_dir=root)[0])
    member = layout.dvc_cache_entry(
        root, hashlib.md5((checkout / "f.csv").read_bytes()).hexdigest())
    _writable(member)
    member.unlink()
    _writable(checkout)
    (checkout / "f.csv").write_bytes(TAMPERED)

    code, out = _verify(capsys, "--repair", "--json")
    assert code == 1
    report = json.loads(out)
    assert _subjects(report, "repaired") == {kind: [] for kind in KINDS}
    assert _subjects(report) == {
        "samples": [], "outputs": [], "objects": [],
        "checkouts": [f"checkout of {tree.content_hash}"], "blobs": []}
    assert "is not complete in the local cache" in report["problems"]["checkouts"][0]["action"]
    assert (checkout / "f.csv").read_bytes() == TAMPERED


def test_a_malformed_entry_and_its_restored_copy_are_listed_as_malformed(
        tmp_project, monkeypatch, capsys):
    """A tree at an unsuffixed address is named malformed, pushed or not, and so is its copy.

    Each sample's restored copy is a directory, as the old shape restored
    it. Neither a pull nor a restore converts the entry, so ``--repair``
    tries no restore of the copy and leaves both listed with the
    re-registration.
    """
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    sources = {}
    for name, pipeline in (("A", None), ("B", "p")):
        sources[name] = tmp_project.parent / f"{name}.csv"
        sources[name].write_bytes(f"id,value\n1,{name}\n".encode())
        if pipeline:
            monkeypatch.setenv("WFC_PIPELINE_ID", pipeline)
        register_sample(name, sources[name], project_root=tmp_project)
        monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
        restore_sample(name, project_root=tmp_project)
    a, b = _sample("A"), _sample("B")
    assert a.push_status == PushStatus.pushed.value != b.push_status
    from wfc.storage.cache import open_dirs
    for sample in (a, b):
        obj = layout.dvc_cache_entry(tmp_project, sample.content_hash)
        copy = layout.restored_sample_path(tmp_project, sample.name, sample.registered_path)
        for tree in (obj, copy):
            _writable(tree)
            open_dirs(tree.parent)  # POSIX refuses an unlink in a read-only folder
            tree.unlink()
            tree.mkdir()
            (tree / "x.csv").write_bytes(b"id\n1\n")

    for args in ((), ("--repair",)):
        code, out = _verify(capsys, *args, "--json")
        assert code == 1
        report = json.loads(out)
        assert _subjects(report, "repaired")["samples"] == []
        left = report["problems"]["samples"]
        assert [p["subject"] for p in left] == [
            "sample 'A' cache entry", "sample 'A' restored copy",
            "sample 'B' cache entry", "sample 'B' restored copy"]
        for problem in left:
            name = problem["subject"].split("'")[1]
            assert "never pushed" not in problem["detail"] + problem["action"], problem
            assert f"wfc register-sample --name {name}" in problem["action"], problem
            if problem["subject"].endswith("cache entry"):
                assert problem["detail"].startswith("malformed: "), problem
            else:
                assert "cache entry is malformed" in problem["action"], problem


def test_a_restore_that_pulls_again_shows_an_earlier_sample_s_shared_entry_repaired(
        tmp_project, monkeypatch, capsys):
    """An earlier sample's line for a shared entry shows a later restore's pull.

    A has no restored copy: its pull of the shared entry is refused and
    nothing restores for it. A2's changed copy is restored after the restore
    pulls the entry again itself, so A's line shows the entry repaired too
    and verify exits 0.
    """
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    src = tmp_project.parent / "a.csv"
    src.write_bytes(b"id,value\n1,A\n")
    register_sample("A", src, project_root=tmp_project)
    register_sample("A2", src, project_root=tmp_project)
    restore_sample("A2", project_root=tmp_project)
    a2 = _sample("A2")
    copy = layout.restored_sample_path(tmp_project, "A2", a2.registered_path)
    assert not layout.restored_sample_path(
        tmp_project, "A", _sample("A").registered_path).exists()
    shared = layout.dvc_cache_entry(tmp_project, a2.content_hash)
    genuine = shared.read_bytes()
    _edit(shared)
    _edit(copy)

    pulled = fakes.fail_first_pull_again(monkeypatch)
    code, out = _verify(capsys, "--repair", "--json")
    report = json.loads(out)
    assert pulled == [a2.content_hash, a2.content_hash], "refused for A, then A2's restore's"
    assert _subjects(report, "repaired")["samples"] == [
        "sample 'A' cache entry", "sample 'A2' cache entry", "sample 'A2' restored copy"]
    assert report["problems"]["samples"] == [] and code == 0
    assert shared.read_bytes() == genuine and copy.read_bytes() == genuine


def test_a_clean_project_verifies_with_exit_zero(tmp_project, capsys):
    """Every kind is a key of the JSON report; nothing wrong exits 0."""
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    src = tmp_project.parent / "src.csv"
    src.write_bytes(b"id\n1\n")
    register_sample("S1", src, project_root=tmp_project)
    restore_sample("S1", project_root=tmp_project)

    code, out = _verify(capsys, "--json")
    assert code == 0
    report = json.loads(out)
    assert report == {"ok": True, "repair": False,
                      "problems": {k: [] for k in KINDS},
                      "repaired": {k: [] for k in KINDS}}
    code, text = _verify(capsys)
    assert code == 0 and "everything is what wfc recorded" in text


def test_verify_re_hashes_whatever_the_setting(tmp_project, capsys):
    """Verify finds a same-size edit with the mtime put back, under the default.

    A stamped copy replaced at its path by other bytes of the same size,
    mtime put back (its cache entry intact): verify re-hashes it and lists
    the copy alone.
    """
    from wfc.registration import register_sample
    from wfc.storage import restore_sample

    src = tmp_project.parent / "src.csv"
    src.write_bytes(b"id\n1\n")
    register_sample("S1", src, project_root=tmp_project)
    restore_sample("S1", project_root=tmp_project)
    with get_session() as session:
        sample = session.exec(select(Sample).where(Sample.name == "S1")).one()
    copy = layout.restored_sample_path(tmp_project, "S1", sample.registered_path)
    st = copy.stat()
    _replace_view(copy, bytes(b ^ 0x01 for b in copy.read_bytes()), keep_mtime=True)
    assert (copy.stat().st_size, copy.stat().st_mtime_ns) == (st.st_size, st.st_mtime_ns)

    code, out = _verify(capsys, "--json")
    assert code == 1
    assert _subjects(json.loads(out))["samples"] == ["sample 'S1' restored copy"]
