"""Registration writes the version list: one entry per distinct content, one commit.

One entry per registration, the labels, changed helpers, a missing list,
and the row sync (rows follow the list). Real ``git`` in temporary
repositories; every version is made by production ``register_method`` (two
versions means registering twice).
"""

from __future__ import annotations

import re
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
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _commit_env(root: Path, **record) -> None:
    write_env_record(root, "fixture-env", **record)
    _git(root, "add", ".wfc/envs.json", ".wfc/captures")
    _git(root, "commit", "-q", "-m", "user commits the env")


@pytest.fixture
def project(git_project, monkeypatch):
    """A project ``wfc init`` produced, with its env committed: a clean tree."""
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    _commit_env(git_project)
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


def _entries(root: Path):
    from wfc.registration.method_version import read_version_list
    return read_version_list(root, MODULE, METHOD)


def _rows():
    from sqlmodel import select
    from wfc.persistence import MethodVersion, get_session
    with get_session() as s:
        rows = s.exec(select(MethodVersion).order_by(MethodVersion.id)).all()
        for r in rows:
            s.expunge(r)
        return rows


def _edit_script(source_dir: Path, marker: str) -> None:
    script = source_dir / f"{METHOD}.py"
    script.write_text(script.read_text() + f"\n# {marker}\n")


@workflow(purpose="Registering a method writes one version-list entry "
                  "(label, code fingerprint, env name, env fingerprint, image "
                  "reference, registration time, tree hash) and commits it with "
                  "the snapshot in one commit naming only those; the database row "
                  "and the version's contract match the entry")
def test_registration_writes_one_entry_committed_with_its_snapshot(project, source):
    from wfc import layout
    from wfc.environments import get as env_get
    from wfc.environments import resolve_env_fingerprint
    from wfc.registration import method_contract

    口 = Step(step_num=1, name="Register the method",
             purpose="Production register_method from a source outside the repo")
    before = _head(project)
    _register(source)
    head = _head(project)
    assert head != before

    口 = Step(step_num=2, name="Read the version list",
             purpose="One entry, every field filled from the snapshot and the "
                     "env's manifest record")
    (entry,) = _entries(project)
    snapshot = layout.method_dir(project, MODULE, METHOD)
    snapshot_rel = snapshot.relative_to(project).as_posix()
    assert entry.label == 1
    assert HEX64.match(entry.code_fingerprint)
    assert entry.env_name == "fixture-env"
    assert entry.env_fingerprint == resolve_env_fingerprint("fixture-env", project)
    assert entry.image_ref == env_get("fixture-env", project).container
    assert entry.registered_at
    assert entry.tree_hash == _git(project, "rev-parse", f"HEAD:{snapshot_rel}").strip()

    口 = Step(step_num=3, name="Check the commit",
             purpose="One commit holds the snapshot and the list, nothing else")
    list_rel = layout.method_versions_path(project, MODULE, METHOD) \
        .relative_to(project).as_posix()
    files = [f for f in _git(project, "show", "--name-only", "--format=", head)
             .splitlines() if f]
    assert list_rel in files
    assert all(f == list_rel or f.startswith(snapshot_rel + "/") for f in files)
    assert _git(project, "status", "--porcelain").strip() == ""

    口 = Step(step_num=4, name="Check the row and its contract",
             purpose="The row mirrors the entry and carries the commit")
    (row,) = _rows()
    assert (row.label, row.code_fingerprint, row.env_name, row.env_fingerprint,
            row.image_ref, row.tree_hash, row.git_commit) == (
        1, entry.code_fingerprint, entry.env_name, entry.env_fingerprint,
        entry.image_ref, entry.tree_hash, head)
    contract = method_contract(MODULE, METHOD)
    assert contract.version_id == row.id
    assert set(contract.output_slots) == {"output"}


@workflow(purpose="Registering A, B, then A again makes three labels; the "
                  "same code under a changed env makes another; registering the "
                  "newest version's content again makes none and commits nothing")
def test_each_distinct_registration_makes_the_next_label(project, source):
    original = (source / f"{METHOD}.py").read_bytes()

    口 = Step(step_num=1, name="Register A, B, then A again",
             purpose="An earlier version's content is a new label, not a reuse")
    _register(source)
    _edit_script(source, "version B")
    _register(source)
    # A again, byte for byte: the registry keeps a file's bytes, so a text
    # write that changed its line endings would be different content.
    (source / f"{METHOD}.py").write_bytes(original)
    _register(source)
    entries = _entries(project)
    assert [e.label for e in entries] == [1, 2, 3]
    assert entries[0].code_fingerprint == entries[2].code_fingerprint
    assert entries[0].code_fingerprint != entries[1].code_fingerprint
    assert entries[0].tree_hash == entries[2].tree_hash

    口 = Step(step_num=2, name="Change only the env and register",
             purpose="The env's record changes its image digest: same code, "
                     "another env fingerprint, a new label")
    _commit_env(project, digest="b" * 64)
    _register(source)
    entries = _entries(project)
    assert [e.label for e in entries] == [1, 2, 3, 4]
    assert entries[3].code_fingerprint == entries[2].code_fingerprint
    assert entries[3].env_fingerprint != entries[2].env_fingerprint
    assert entries[3].identity != entries[2].identity

    口 = Step(step_num=3, name="Register the newest content again",
             purpose="Nothing to add: no entry, no commit, no row")
    head = _head(project)
    _register(source)
    assert [e.label for e in _entries(project)] == [1, 2, 3, 4]
    assert _head(project) == head
    assert [r.label for r in _rows()] == [1, 2, 3, 4]


@workflow(purpose="A changed helper of any file type (a declared lookup.tsv) "
                  "makes the next version on re-registration")
def test_a_changed_declared_helper_makes_a_new_version(project, source):
    口 = Step(step_num=1, name="Declare a data helper and register",
             purpose="method.yaml's helpers: names lookup.tsv; v1 copies it")
    (source / "lookup.tsv").write_text("gene\tid\nA\t1\n")
    spec = source / "method.yaml"
    spec.write_text(spec.read_text().rstrip("\n") + "\nhelpers:\n  - lookup.tsv\n")
    _register(source)

    口 = Step(step_num=2, name="Change only the helper and register again",
             purpose="The every-file fingerprint covers it: v2")
    (source / "lookup.tsv").write_text("gene\tid\nA\t1\nB\t2\n")
    _register(source)
    entries = _entries(project)
    assert [e.label for e in entries] == [1, 2]
    assert entries[0].code_fingerprint != entries[1].code_fingerprint


def test_a_method_with_no_version_list_is_refused_naming_register_method(
        project, source):
    """A registered method whose list is gone is malformed; the
    refusal names ``wfc register-method`` and does not call it an old version."""
    from wfc import layout
    from wfc.registration import MalformedMethodRecordError, method_contract

    _register(source)
    list_rel = layout.method_versions_path(project, MODULE, METHOD) \
        .relative_to(project).as_posix()
    _git(project, "rm", "-q", "--", list_rel)
    _git(project, "commit", "-q", "-m", "user deletes the version list")

    with pytest.raises(MalformedMethodRecordError) as exc:
        method_contract(MODULE, METHOD)
    message = str(exc.value)
    assert "wfc register-method" in message
    assert "version list" in message
    assert "old version" not in message.lower()


def test_rows_are_rebuilt_from_the_list_with_contracts_from_git(project, source):
    """A database without the rows (a fresh clone) gets them from the list:
    labels, fingerprints and each version's own contract, parsed from the
    entry's git tree; no commit is recorded for a version it did not see
    registered."""
    from wfc.persistence import reset_engine
    from wfc.registration import method_contract, register_method, register_module
    from wfc.registration.registered_code import sync_version_rows

    spec = source / "method.yaml"
    _register(source)
    spec.write_text(spec.read_text().replace("output:", "renamed:", 1))
    _register(source)
    assert [e.label for e in _entries(project)] == [1, 2]

    reset_engine()
    (project / ".wfc" / "wfc.db").unlink()
    reset_engine()
    register_module(name=MODULE, contracts=[])
    register_method(method_dir=source, module_name=MODULE, assume_yes=True)  # newest: no-op

    versions = sync_version_rows(MODULE, METHOD, project)
    assert [v.entry.label for v in versions] == [1, 2]
    assert [v.git_commit for v in versions] == [None, None]
    assert [r.label for r in _rows()] == [1, 2]
    v1 = method_contract(MODULE, METHOD, versions[0].entry.identity)
    v2 = method_contract(MODULE, METHOD)
    assert set(v1.output_slots) == {"output"}
    assert set(v2.output_slots) == {"renamed"}
