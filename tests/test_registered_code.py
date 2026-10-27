"""Registered code: restore-on-change at registration, the resolver, the verify rule.

A snapshot changed outside wfc is put back at the next registration, which
stops; every reader finds a method's script through one resolver that
refuses a record not leading inside the snapshot; the verify rule accepts a
snapshot only when it is the code a registered version recorded. Real
``git`` in temporary repositories throughout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.routes import init_test_project
from tests.fixtures.routes.registration import write_env_record

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"
MODULE, METHOD = "mod", "transform"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _commit_files(root: Path, commit: str) -> set[str]:
    out = _git(root, "show", "--name-only", "--pretty=format:", commit)
    return {ln for ln in out.splitlines() if ln}


def _subject(root: Path, commit: str) -> str:
    return _git(root, "log", "-1", "--format=%s", commit).strip()


@pytest.fixture
def project(git_project, monkeypatch):
    """A project ``wfc init`` produced, with its env committed: a clean tree."""
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    write_env_record(git_project, "fixture-env")
    _git(git_project, "add", ".wfc/envs.json", ".wfc/captures")
    _git(git_project, "commit", "-q", "-m", "user commits the env")
    reset_engine()
    yield git_project
    reset_engine()


@pytest.fixture
def source(tmp_path_factory) -> Path:
    """The method's source directory, outside the repository."""
    dest = tmp_path_factory.mktemp("elsewhere") / METHOD
    shutil.copytree(FIXTURE_METHOD, dest)
    return dest


def _register(source_dir: Path) -> None:
    from wfc.registration import register_method, register_module
    register_module(name=MODULE, contracts=[])
    register_method(method_dir=source_dir, module_name=MODULE, assume_yes=True)


def _snapshot(root: Path) -> Path:
    from wfc import layout
    return layout.method_dir(root, MODULE, METHOD)


def _versions() -> list[tuple[int, str | None]]:
    from sqlmodel import select
    from wfc.persistence import MethodVersion, get_session
    with get_session() as s:
        return [(v.id, v.git_commit) for v in s.exec(select(MethodVersion)).all()]


def _labelled() -> list[tuple[int, int | None, str | None]]:
    """Every version row as (id, label, commit), in id order."""
    from sqlmodel import select
    from wfc.persistence import MethodVersion, get_session
    with get_session() as s:
        rows = s.exec(select(MethodVersion).order_by(MethodVersion.id)).all()
        return [(v.id, v.label, v.git_commit) for v in rows]


def _list_labels(root: Path) -> list[int]:
    from wfc.registration.method_version import read_version_list
    return [e.label for e in read_version_list(root, MODULE, METHOD)]


def _committed_edit(root: Path) -> None:
    script = _snapshot(root) / f"{METHOD}.py"
    script.write_text(script.read_text() + "\n# hand edit\n")
    _git(root, "add", "--", script.relative_to(root).as_posix())
    _git(root, "commit", "-q", "-m", "user edits the registered copy")


def _uncommitted_edit(root: Path) -> None:
    script = _snapshot(root) / f"{METHOD}.py"
    script.write_text(script.read_text() + "\n# hand edit\n")


def _stray_file(root: Path) -> None:
    (_snapshot(root) / "hand_edit.txt").write_text("stray\n")


@workflow(purpose="A registered copy changed outside wfc -- a committed hand edit, "
                  "an uncommitted one or a stray file -- is restored at the next "
                  "registration: the discarded diff is printed, registration "
                  "stops with the restore message, the restore commit is a "
                  "registration commit naming only the snapshot, and every "
                  "version's commit is a registration commit")
@pytest.mark.parametrize("edit", [_committed_edit, _uncommitted_edit, _stray_file],
                         ids=["committed-edit", "uncommitted-edit", "stray-file"])
def test_snapshot_changed_outside_wfc_is_restored_at_registration(
        project, source, edit, capsys):
    from wfc.registration import SnapshotChangedError
    from wfc.registration.advice import issues_url

    口 = Step(step_num=1, name="Register, then change the registered copy",
             purpose="The version is minted at registration; the edit is made "
                     "in the snapshot, not the source")
    _register(source)
    versions = _versions()
    registered_script = (_snapshot(project) / f"{METHOD}.py").read_text()
    edit(project)
    head_before = _head(project)
    capsys.readouterr()

    口 = Step(step_num=2, name="Register again from the unchanged source",
             purpose="Registration finds the difference, restores, and stops")
    with pytest.raises(SnapshotChangedError) as exc:
        _register(source)
    assert str(exc.value) == (
        f"The registered copy of `{MODULE}/{METHOD}` was changed outside wfc. "
        f"The changes above were reverted. Make changes in your source and run "
        f"`wfc register-method`. If you edited the registered copy for a reason "
        f"wfc doesn't support, please open an issue: {issues_url()}."
    )
    discarded = capsys.readouterr().out  # the edited line, or the stray file's name
    assert "hand edit" in discarded or "hand_edit.txt" in discarded

    口 = Step(step_num=3, name="Check the snapshot, the tree and the commits",
             purpose="The snapshot is the registered version again; a committed "
                     "edit gets a restore commit naming only the snapshot")
    assert (_snapshot(project) / f"{METHOD}.py").read_text() == registered_script
    assert not (_snapshot(project) / "hand_edit.txt").exists()
    assert _git(project, "status", "--porcelain").strip() == ""
    snapshot_rel = _snapshot(project).relative_to(project).as_posix() + "/"
    if edit is _committed_edit:
        head = _head(project)
        assert head != head_before
        assert _subject(project, head).startswith(f"Register method {MODULE}/{METHOD}")
        assert all(f.startswith(snapshot_rel) for f in _commit_files(project, head))
    else:
        assert _head(project) == head_before
    assert _versions() == versions
    for _, commit in versions:
        assert _subject(project, commit).startswith("Register method ")

    口 = Step(step_num=4, name="Register once more",
             purpose="With the copy restored, registration proceeds")
    _register(source)
    assert _versions() == versions


def _set_source(source_dir: Path, original: dict[str, str], state: str) -> None:
    """Put the source in *state*: ``A`` as copied, ``B`` with a script change,
    ``A'`` with only a ``method.yaml`` description changed (same fingerprint)."""
    for name, text in original.items():
        (source_dir / name).write_text(text)
    if state == "B":
        script = source_dir / f"{METHOD}.py"
        script.write_text(script.read_text() + "\n# version B\n")
    elif state == "A'":
        spec = source_dir / "method.yaml"
        spec.write_text(spec.read_text().replace(
            "Input CSV to transform", "Input table to transform"))


@workflow(purpose="Re-registering code registered earlier is never taken for a hand "
                  "edit. A, B, A makes three versions, and registering A once more "
                  "registers nothing, restores nothing and leaves HEAD alone; A "
                  "then a description-only method.yaml edit A' is the newest "
                  "version's content, so A' registers nothing twice and the "
                  "snapshot keeps the committed method.yaml. The verify rule "
                  "returns the newest version and its commit")
@pytest.mark.parametrize("sequence, labels", [
    (("A", "B", "A", "A"), [1, 2, 3]),
    (("A", "A'", "A'"), [1]),
], ids=["A-B-A-A", "A-description-edit"])
def test_reregistering_earlier_code_is_not_restored(project, source, sequence, labels,
                                                    capsys):
    from wfc.registration import verify_registered_snapshot

    original = {p.name: p.read_text() for p in source.iterdir() if p.is_file()}

    口 = Step(step_num=1, name="Register each state but the last",
             purpose="A, B, A: the third registration's content is v1's, and it "
                     "is a new version because only the newest counts")
    for state in sequence[:-1]:
        _set_source(source, original, state)
        _register(source)
    last_registration_commit = _head(project)
    capsys.readouterr()

    口 = Step(step_num=2, name="Register the last state from the unchanged source",
             purpose="Nothing changed outside wfc, so nothing is restored; the "
                     "content is the newest version's, so nothing is registered")
    _register(source)
    assert _head(project) == last_registration_commit
    assert "diff --git" not in capsys.readouterr().out
    assert _git(project, "status", "--porcelain").strip() == ""
    assert _list_labels(project) == labels
    rows = _labelled()
    assert [label for _, label, _ in rows] == labels
    for name, text in original.items():  # A's files; A' changed no committed byte
        assert (_snapshot(project) / name).read_text() == text

    口 = Step(step_num=3, name="Ask the verify rule",
             purpose="The snapshot is the newest version's code, carrying the "
                     "registration commit that last wrote the snapshot")
    newest_id, _, newest_commit = rows[-1]
    verified = verify_registered_snapshot(MODULE, METHOD, project)
    assert verified.version_id == newest_id
    assert verified.git_commit == newest_commit == last_registration_commit


@workflow(purpose="Registrations that all read one clock value still order: with the "
                  "wall clock standing still, registering A, then B, then A again "
                  "makes three labels whose registration times strictly increase, "
                  "every row matching its entry; registering A once more restores "
                  "nothing and the verify rule returns the third version")
def test_registrations_on_a_clock_that_stands_still_still_order(
        project, source, monkeypatch, capsys):
    import time

    from tests.fixtures.fakes import stub_wall_clock
    from wfc.registration import verify_registered_snapshot
    from wfc.registration.method_version import read_version_list

    original = {p.name: p.read_text() for p in source.iterdir() if p.is_file()}

    口 = Step(step_num=1, name="Stop the clock",
             purpose="Every registration below reads the same wall-clock value, "
                     "so only the list's tie rule can order them")
    stub_wall_clock(monkeypatch, time.time())

    口 = Step(step_num=2, name="Register A, then B, then A again",
             purpose="Three entries, each strictly later than the one before, "
                     "so a row is matched to its entry by identity and time")
    for state in ("A", "B", "A"):
        _set_source(source, original, state)
        _register(source)
    last_registration_commit = _head(project)
    capsys.readouterr()
    entries = read_version_list(project, MODULE, METHOD)
    assert [e.label for e in entries] == [1, 2, 3]
    times = [e.registered_datetime for e in entries]
    assert times[0] < times[1] < times[2]
    assert entries[0].identity == entries[2].identity != entries[1].identity
    assert [label for _, label, _ in _labelled()] == [1, 2, 3]

    口 = Step(step_num=3, name="Register A once more",
             purpose="The third version is the newest and holds A: nothing is "
                     "restored or registered")
    _register(source)
    assert _head(project) == last_registration_commit
    assert "diff --git" not in capsys.readouterr().out
    third_id = _labelled()[-1][0]
    verified = verify_registered_snapshot(MODULE, METHOD, project)
    assert verified.version_id == third_id
    assert verified.git_commit == last_registration_commit


@pytest.mark.parametrize("which", ["commit-lacks-path", "missing-commit"])
def test_restore_from_a_commit_without_the_path_changes_nothing(git_project, which):
    """A restore checks before it deletes: a commit that is missing, or does not
    hold the path, is refused naming both, and the directory on disk and its
    index entries are left exactly as they were."""
    from wfc.version import restore_path_from_commit

    root = git_project
    lacking = _head(root)  # the initial commit holds only .gitkeep
    snap = root / "snap"
    snap.mkdir()
    (snap / "a.txt").write_text("committed\n")
    _git(root, "add", "--", "snap")
    _git(root, "commit", "-q", "-m", "add snap")
    (snap / "a.txt").write_text("edited and staged\n")
    _git(root, "add", "--", "snap/a.txt")
    (snap / "stray.txt").write_text("stray\n")
    index_before = _git(root, "ls-files", "-s", "--", "snap")
    status_before = _git(root, "status", "--porcelain")

    commit = lacking if which == "commit-lacks-path" else "0" * 40
    with pytest.raises(RuntimeError) as exc:
        restore_path_from_commit(root, commit, snap)
    assert (snap / "a.txt").read_text() == "edited and staged\n"
    assert (snap / "stray.txt").read_text() == "stray\n"
    assert _git(root, "ls-files", "-s", "--", "snap") == index_before
    assert _git(root, "status", "--porcelain") == status_before
    assert commit in str(exc.value)
    assert "snap" in str(exc.value)


@workflow(purpose="A version whose registration commit is gone from the repository "
                  "(history reset and garbage-collected, taking the snapshot and "
                  "the version list with it) has nothing to restore to: the next "
                  "registration proceeds without a restore and starts a new list "
                  "at v1, committed with the snapshot; the old row keeps its id "
                  "(run history still points at it) but no longer holds a label")
def test_registration_proceeds_when_the_versions_commit_is_gone(project, source, capsys):
    from wfc.registration import verify_registered_snapshot

    口 = Step(step_num=1, name="Register, then remove the registration commit",
             purpose="Real git operations: reset to before it, expire the reflog, "
                     "prune")
    before = _head(project)
    _register(source)
    ((version_id, gone),) = _versions()
    assert gone != before
    _git(project, "reset", "-q", "--hard", before)
    _git(project, "reflog", "expire", "--expire=now", "--all")
    _git(project, "gc", "-q", "--prune=now")
    assert subprocess.run(["git", "cat-file", "-e", f"{gone}^{{commit}}"],
                          cwd=project, capture_output=True).returncode != 0
    capsys.readouterr()

    口 = Step(step_num=2, name="Register again",
             purpose="Nothing to compare with, so no restore; the snapshot and a "
                     "new list are written and committed")
    _register(source)
    head = _head(project)
    assert head != before
    assert "diff --git" not in capsys.readouterr().out
    assert _list_labels(project) == [1]
    ((old_id, old_label, old_commit), (new_id, new_label, new_commit)) = _labelled()
    assert (old_id, old_label, old_commit) == (version_id, None, gone)
    assert (new_label, new_commit) == (1, head)
    assert _subject(project, head).startswith(f"Register method {MODULE}/{METHOD}")
    assert verify_registered_snapshot(MODULE, METHOD, project).version_id == new_id


@workflow(purpose="A first registration that finds the snapshot directory already "
                  "present, with no version to restore to, is refused naming the "
                  "directory and removes nothing")
def test_first_registration_refuses_a_present_snapshot_dir(project, source):
    from sqlmodel import select
    from wfc.persistence import Method, get_session
    from wfc.registration import SnapshotPresentError

    planted = _snapshot(project) / "mine.txt"
    planted.parent.mkdir(parents=True)
    planted.write_text("not wfc's\n")
    with pytest.raises(SnapshotPresentError, match="wfc register-method") as exc:
        _register(source)
    assert str(_snapshot(project)) in str(exc.value)
    assert planted.read_text() == "not wfc's\n"
    with get_session() as s:
        assert s.exec(select(Method)).all() == []


# --- the resolver --------------------------------------------------------------

@workflow(purpose="The resolver takes a version: unpinned it answers the newest "
                  "version's extracted copy, and a pin to an older version's "
                  "content identity answers that version's tree and script, "
                  "each copy fingerprinting as its version records")
def test_resolver_answers_the_named_version_and_the_newest_unpinned(project, source):
    from wfc import layout
    from wfc.registration import resolve_method_code
    from wfc.registration.method_version import (
        read_version_list,
        snapshot_code_fingerprint,
    )

    口 = Step(step_num=1, name="Register two versions",
             purpose="The second changes the script")
    original = (source / f"{METHOD}.py").read_text()
    _register(source)
    (source / f"{METHOD}.py").write_text(original + "\n# v2\n")
    _register(source)
    v1, v2 = read_version_list(project, MODULE, METHOD)

    口 = Step(step_num=2, name="Resolve unpinned, then pinned to v1",
             purpose="Each answer is its version's copy under .wfc/runs/code")
    newest = resolve_method_code(MODULE, METHOD, project)
    pinned = resolve_method_code(MODULE, METHOD, project, version=v1.identity)

    assert newest.entry == v2
    assert newest.code_dir == layout.code_copy_dir(project, v2.tree_hash)
    assert snapshot_code_fingerprint(newest.code_dir) == v2.code_fingerprint
    assert newest.script.read_text().endswith("# v2\n")
    assert pinned.entry == v1
    assert pinned.code_dir == layout.code_copy_dir(project, v1.tree_hash)
    assert pinned.script == pinned.code_dir / f"{METHOD}.py"
    assert pinned.script.read_text().replace("\r\n", "\n") == original.replace("\r\n", "\n")
    assert pinned.version_id != newest.version_id


def _no_version_list(root: Path) -> str:
    from wfc import layout
    layout.method_versions_path(root, MODULE, METHOD).unlink()
    return None


def _unknown_version(root: Path) -> str:
    return "f" * 64


@pytest.mark.parametrize("break_record, message", [
    (_no_version_list, "no version list.*wfc register-method"),
    (_unknown_version, "no version f{64} .*its versions: v1"),
], ids=["no-version-list", "unknown-version"])
def test_resolver_refuses_a_version_it_cannot_name(project, source, break_record,
                                                   message):
    """A method with no list is malformed; a pin no entry carries is refused
    naming the pin and the versions there are."""
    from wfc.registration import MalformedMethodRecordError, resolve_method_code
    _register(source)
    version = break_record(project)
    with pytest.raises(MalformedMethodRecordError, match=message):
        resolve_method_code(MODULE, METHOD, project, version=version)


# --- the verify rule -------------------------------------------------------------

def test_verify_rule_returns_the_registered_version(project, source):
    from wfc import layout
    from wfc.registration import verify_registered_snapshot
    from wfc.registration.method_version import read_version_list
    _register(source)
    ((version_id, commit),) = _versions()
    (entry,) = read_version_list(project, MODULE, METHOD)
    verified = verify_registered_snapshot(MODULE, METHOD, project)
    assert verified.version_id == version_id
    assert verified.git_commit == commit
    assert verified.code_fingerprint == entry.code_fingerprint
    copy = layout.code_copy_dir(project, entry.tree_hash)
    assert verified.code.script == copy / f"{METHOD}.py"


@workflow(purpose="An edit to the staging snapshot -- committed, uncommitted, or "
                  "a stray file -- never reaches a run: the verify rule answers "
                  "the registered version's extracted copy, unchanged")
@pytest.mark.parametrize("change", [_committed_edit, _uncommitted_edit, _stray_file],
                         ids=["committed-edit", "uncommitted-edit", "untracked-file"])
def test_an_edit_to_the_snapshot_never_reaches_a_run(project, source, change):
    from wfc.registration import verify_registered_snapshot
    from wfc.registration.method_version import snapshot_code_fingerprint

    口 = Step(step_num=1, name="Register, then change the staging snapshot",
             purpose="The snapshot no longer holds the registered code")
    _register(source)
    before = verify_registered_snapshot(MODULE, METHOD, project)
    script_text = before.code.script.read_text()
    change(project)

    口 = Step(step_num=2, name="Verify what a run executes",
             purpose="The registered version's copy, as registered")
    after = verify_registered_snapshot(MODULE, METHOD, project)
    assert after.version_id == before.version_id
    assert after.code.code_dir == before.code.code_dir
    assert after.code.script.read_text() == script_text
    assert not (after.code.code_dir / "hand_edit.txt").exists()
    assert snapshot_code_fingerprint(after.code.code_dir) == before.code_fingerprint


@workflow(purpose="A registration of changed code that lands while a step is "
                  "being claimed, after the claim composed its cache key and "
                  "before it verified the snapshot, is refused before any Run "
                  "row: the key and the recorded version must describe the "
                  "same code. The claim writes no version either")
def test_claim_refuses_a_registration_between_key_and_verify(tmp_project,
                                                             monkeypatch):
    from sqlmodel import func, select

    from tests.fixtures.fakes import register_during_claim
    from tests.harness import Scenario, build_project, node, selector, wire
    from tests.harness.project import method_source_dir
    from tests.harness.scenario import SELECTOR_ID
    from wfc.execution.claim import pre_run
    from wfc.persistence import MethodVersion, Run, get_session
    from wfc.registration import UnregisteredSnapshotError, register_method

    def _count(model) -> int:
        with get_session() as session:
            return session.exec(select(func.count()).select_from(model)).one()

    scenario = Scenario(nodes=[selector(), node("a", inputs=[wire(SELECTOR_ID)])],
                        samples=["s1"], pipeline_id="race", name="race")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    spec = scenario.method_nodes()[0]
    source_dir = method_source_dir(tmp_project, spec)
    script_name = project.method_scripts[spec.method_name].name
    runs_before = _count(Run)
    versions_after_registration: list[int] = []

    口 = Step(step_num=1, name="Change the source and register it mid-claim",
             purpose="Production register_method of changed code lands after "
                     "pre_run composed its key and before it verifies")

    def register_changed_code() -> None:
        script = source_dir / script_name
        script.write_text(script.read_text(encoding="utf-8") + "\n# changed\n",
                          encoding="utf-8")
        register_method(method_dir=source_dir, module_name=spec.module, assume_yes=True)
        versions_after_registration.append(_count(MethodVersion))

    register_during_claim(monkeypatch, register_changed_code)

    口 = Step(step_num=2, name="The claim refuses the step",
             purpose="The message says the method was registered again and to "
                     "run the step again")
    with pytest.raises(UnregisteredSnapshotError, match="registered again"):
        pre_run(method_name=spec.method_name, module_name=spec.module,
                sample="s1", params={}, git_commit="e" * 40)

    口 = Step(step_num=3, name="Nothing was written by the claim",
             purpose="No Run row; the only new version is the registration's")
    assert versions_after_registration, "the registration never landed"
    assert _count(Run) == runs_before
    assert _count(MethodVersion) == versions_after_registration[0]


# --- the entry script is part of the registered copy ------------------------------

@workflow(purpose="Registering with --script naming a script the copy would "
                  "not select on its own records that script in the copy's "
                  "method.yaml, never the source's, and the version's code "
                  "resolves to it. Registering again with --script naming the "
                  "other script is refused before any write: a new version "
                  "keeps its main script's file name")
def test_script_choice_is_part_of_the_registered_version(project, source):
    import yaml

    from wfc.registration import register_method, register_module, resolve_method_code

    register_module(name=MODULE, contracts=[])
    (source / "alt.py").write_text((source / f"{METHOD}.py").read_text() + "\n# alt\n")
    source_yaml = (source / "method.yaml").read_bytes()

    口 = Step(step_num=1, name="Register choosing the second script",
             purpose="The copy's method.yaml names alt.py; the source's bytes "
                     "are untouched; the version's code resolves to alt.py")
    register_method(method_dir=source, module_name=MODULE, script_name="alt.py",
                    assume_yes=True)
    assert (source / "method.yaml").read_bytes() == source_yaml
    assert yaml.safe_load((_snapshot(project) / "method.yaml").read_text())["script"] == "alt.py"
    assert resolve_method_code(MODULE, METHOD, project).script.name == "alt.py"

    口 = Step(step_num=2, name="Register again choosing the first script",
             purpose="Refused before any write, naming both scripts")
    head, versions = _head(project), _versions()
    with pytest.raises(ValueError) as refused:
        register_method(method_dir=source, module_name=MODULE,
                        script_name=f"{METHOD}.py", assume_yes=True)
    assert "alt.py" in str(refused.value) and f"{METHOD}.py" in str(refused.value)
    assert _head(project) == head and _versions() == versions


def test_each_version_runs_the_script_its_copy_selects(project, source):
    """A snapshot put back by hand to an earlier version's commit changes
    nothing that runs: unpinned, the newest version's copy runs its own
    ``alt.py`` (registered with ``--script alt.py``); pinned to the first
    version, that version's copy runs the first ``alt.py``."""
    from wfc.registration import register_method, register_module, verify_registered_snapshot
    from wfc.registration.method_version import read_version_list
    alt = source / "alt.py"
    alt.write_text((source / f"{METHOD}.py").read_text() + "\n# alt\n")
    register_module(name=MODULE, contracts=[])
    register_method(method_dir=source, module_name=MODULE, script_name="alt.py", assume_yes=True)
    ((_, first_commit),) = _versions()
    alt.write_text(alt.read_text() + "# second\n")
    register_method(method_dir=source, module_name=MODULE, script_name="alt.py", assume_yes=True)
    snapshot = _snapshot(project).relative_to(project).as_posix()
    _git(project, "checkout", first_commit, "--", snapshot)
    _git(project, "commit", "-q", "-m", "user puts the registered copy back")
    first, _second = read_version_list(project, MODULE, METHOD)

    newest = verify_registered_snapshot(MODULE, METHOD, project).code.script
    assert newest.name == "alt.py" and "# second" in newest.read_text()
    pinned = verify_registered_snapshot(MODULE, METHOD, project, version=first.identity)
    assert pinned.code.script.name == "alt.py"
    assert "# second" not in pinned.code.script.read_text()
