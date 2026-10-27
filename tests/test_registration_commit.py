"""Registration commits exactly the tree it verified, or refuses having changed nothing.

A file git would leave out or change is refused naming the file and the
reason (HEAD, the user's index, the snapshot and the version list are as
they were); skip-worktree and a case-only rename cannot change what is
committed; a snapshot whose paths overflow one command line registers; what
the user staged stays staged (a refusing hook commits nothing); a reader of
the snapshot and the version list finds old bytes or new ones, never a
partial or missing file; a same-content registration whose tree was
garbage-collected commits it again; the registry's ``.gitattributes`` line;
and two processes registering one method at once. Real ``git`` in a project ``wfc init``
produced; every version is made by production ``register_method``.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures import fakes
from tests.fixtures.fakes.process import fake_os_replace

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"
MODULE, METHOD = "mod", "transform"


def _git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=root, check=check,
                          capture_output=True, text=True).stdout


def _git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True).stdout


def _source(tmp_path: Path, name: str = "src") -> Path:
    """The fixture method, copied outside the project, with LF bytes.

    Its ``method.yaml`` names the main script, so the helper scripts the
    tests add beside it register.
    """
    dest = tmp_path / name / METHOD
    shutil.copytree(FIXTURE_METHOD, dest)
    for path in dest.iterdir():
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    yaml_path = dest / "method.yaml"
    yaml_path.write_bytes(yaml_path.read_bytes() + f"script: {METHOD}.py\n".encode())
    return dest


def _register(source_dir: Path):
    from wfc.registration import register_method
    return register_method(method_dir=source_dir, module_name=MODULE, assume_yes=True)


@pytest.fixture
def module(tmp_project):
    from wfc.registration import register_module
    register_module(name=MODULE, contracts=[])
    return tmp_project


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _files(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


def _state(root: Path) -> dict:
    """HEAD, the user's index, ``git status`` and every registry file's bytes."""
    from wfc import layout
    return {
        "head": _git(root, "rev-parse", "HEAD"),
        "index": _git(root, "ls-files", "-s"),
        "status": _git(root, "status", "--porcelain"),
        "registry": _files(layout.methods_dir(root)),
    }


def _registry_status(root: Path) -> str:
    return _git(root, "status", "--porcelain", "--", ".wfc/registry", ".gitattributes")


def _version_rows() -> int:
    from sqlmodel import select

    from wfc.persistence import MethodVersion, get_session
    with get_session() as session:
        return len(session.exec(select(MethodVersion)).all())


def _commit_all(root: Path, *paths: str, message: str = "user change") -> None:
    _git(root, "add", "--", *paths)
    _git(root, "commit", "-q", "-m", message)


def _hook(root: Path, name: str, body: bytes) -> Path:
    hooks = root / _git(root, "rev-parse", "--git-path", "hooks").strip()
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / name
    hook.write_bytes(b"#!/bin/sh\n" + body)
    hook.chmod(0o755)
    return hook


def _leg_ignored(root: Path) -> str:
    gitignore = root / ".gitignore"
    held = gitignore.read_bytes() if gitignore.is_file() else b""
    if held and not held.endswith(b"\n"):
        held += b"\n"
    gitignore.write_bytes(held + b"helper.txt\n")
    _commit_all(root, ".gitignore")
    line = len(gitignore.read_bytes().splitlines())
    return f".gitignore:{line}:helper.txt"


def _leg_export_ignore(root: Path) -> str:
    info = root / _git(root, "rev-parse", "--git-path", "info/attributes").strip()
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_bytes(b"helper.txt export-ignore\n")
    return "export-ignore"


def _leg_line_ending(root: Path) -> str:
    # A registry line after the -text line overrides it for the helper.
    attrs = root / ".gitattributes"
    held = attrs.read_bytes()
    if not held.endswith(b"\n"):
        held += b"\n"
    attrs.write_bytes(held + b".wfc/registry/**/helper.txt text eol=crlf\n")
    _commit_all(root, ".gitattributes")
    return "eol=crlf"


LEGS = {"ignored": _leg_ignored, "export-ignore": _leg_export_ignore,
        "line-ending": _leg_line_ending}


@pytest.mark.parametrize("leg", sorted(LEGS))
@workflow(purpose="Property: a strict-mode helper git would leave out "
                  "of the registered version (ignored, export-ignore) or change "
                  "(a line-ending conversion) is refused naming the file and the "
                  "reason (the ignore rule for an ignored file); HEAD, the user's "
                  "index, git status, the snapshot, the version list and the "
                  "version rows are as they were")
def test_a_helper_git_would_not_give_back_is_refused_and_nothing_changes(
        module, tmp_path, leg):
    root = module

    口 = Step(step_num=1, name="Register v1, then make git mistreat a helper",
             purpose="v1 has no helper; the leg's rule or attribute is the user's")
    source = _source(tmp_path)
    _register(source)
    reason = LEGS[leg](root)
    helper = b"one\r\ntwo\r\n" if leg == "line-ending" else b"helper\n"
    (source / "helper.txt").write_bytes(helper)
    yaml = source / "method.yaml"
    yaml.write_bytes(yaml.read_bytes() + b"helpers:\n  - helper.txt\n")
    before, rows = _state(root), _version_rows()

    口 = Step(step_num=2, name="Register v2 with the helper",
             purpose="Refused naming helper.txt and why")
    with pytest.raises(ValueError) as refused:
        _register(source)
    message = str(refused.value)
    assert "helper.txt" in message
    assert reason in message

    口 = Step(step_num=3, name="Nothing changed",
             purpose="HEAD, index, status, registry bytes and rows as before")
    assert _state(root) == before
    assert _version_rows() == rows


@workflow(purpose="A skip-worktree bit on the snapshot's script in "
                  "the user's index does not change what registration commits; "
                  "the new version holds the new script, runs, and git status "
                  "shows nothing under the registry")
def test_skip_worktree_on_the_snapshot_does_not_change_what_is_committed(module, tmp_path):
    from wfc import layout
    from wfc.registration.registered_code import resolve_method_code

    root = module
    source = _source(tmp_path)
    _register(source)
    rel = _rel(root, layout.method_dir(root, MODULE, METHOD))
    _git(root, "update-index", "--skip-worktree", f"{rel}/{METHOD}.py")

    script = source / f"{METHOD}.py"
    new = script.read_bytes() + b"\n# v2\n"
    script.write_bytes(new)
    assert _register(source).label == 2

    assert _git_bytes(root, "cat-file", "-p", f"HEAD:{rel}/{METHOD}.py") == new
    code = resolve_method_code(MODULE, METHOD, root)
    assert code.entry.label == 2
    assert code.script.read_bytes() == new
    assert _registry_status(root) == ""


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.write_text("")
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.unlink()


@workflow(purpose="On a case-insensitive filesystem, a helper "
                  "renamed only in case registers under its new name: HEAD, the "
                  "snapshot and the extracted version hold util.py, the version "
                  "runs, and git status shows nothing under the registry")
def test_a_case_only_rename_registers_under_the_new_name(module, tmp_path):
    from wfc import layout
    from wfc.registration.registered_code import resolve_method_code

    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (the rename is a no-op elsewhere)")
    root = module
    source = _source(tmp_path)
    (source / "Util.py").write_bytes(b"X = 1\n")
    _register(source)

    (source / "Util.py").rename(source / "tmp-name.py")
    (source / "tmp-name.py").rename(source / "util.py")
    (source / "util.py").write_bytes(b"X = 2\n")
    assert _register(source).label == 2

    snapshot = layout.method_dir(root, MODULE, METHOD)
    names = _git(root, "ls-tree", "--name-only", f"HEAD:{_rel(root, snapshot)}").split()
    assert "util.py" in names and "Util.py" not in names
    assert "util.py" in os.listdir(snapshot) and "Util.py" not in os.listdir(snapshot)
    code = resolve_method_code(MODULE, METHOD, root)
    assert "util.py" in os.listdir(code.code_dir)
    assert (code.code_dir / "util.py").read_bytes() == b"X = 2\n"
    assert _registry_status(root) == ""


@workflow(purpose="On a case-insensitive filesystem, a helper directory renamed "
                  "only in case registers under its new name: HEAD and the "
                  "snapshot hold lib/ with its file, and git status shows "
                  "nothing under the registry")
def test_a_case_only_directory_rename_registers_under_the_new_name(module, tmp_path):
    from wfc import layout

    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive filesystem (the rename is a no-op elsewhere)")
    root = module
    source = _source(tmp_path)
    (source / "Lib").mkdir()
    (source / "Lib" / "util.py").write_bytes(b"X = 1\n")
    _register(source)

    (source / "Lib").rename(source / "tmp-name")
    (source / "tmp-name").rename(source / "lib")
    (source / "lib" / "util.py").write_bytes(b"X = 2\n")
    assert _register(source).label == 2

    snapshot = layout.method_dir(root, MODULE, METHOD)
    names = _git(root, "ls-tree", "-r", "--name-only", f"HEAD:{_rel(root, snapshot)}").split()
    assert "lib/util.py" in names and "Lib/util.py" not in names
    assert "lib" in os.listdir(snapshot) and "Lib" not in os.listdir(snapshot)
    assert (snapshot / "lib" / "util.py").read_bytes() == b"X = 2\n"
    assert _registry_status(root) == ""


# Windows caps a command line at 32,767 characters (CreateProcess); one git
# command naming every file of a snapshot exceeds it (WinError 206).
WINDOWS_COMMAND_LINE_LIMIT = 32_767


@workflow(purpose="A method whose snapshot's paths total well over "
                  "Windows' 32,767-character command line registers; the commit "
                  "holds exactly the verified tree (every file, its bytes) and "
                  "git status shows nothing under the registry")
def test_a_snapshot_too_long_for_one_command_line_registers(module, tmp_path):
    """Registration names the snapshot's files to git through stdin, not argv.

    The size is chosen past Windows' limit, where the argv form fails with
    WinError 206. POSIX's limit (ARG_MAX: about 2 MB on Linux, 1 MB on
    macOS) would take thousands of files, each its own ``git hash-object``,
    too slow for the suite; there the test checks the same behavior below
    the limit. The names stay short enough that every path on disk stays
    under Windows' 260-character path limit.
    """
    from wfc import layout
    from wfc.registration.method_version import newest_entry, read_version_list

    root = module

    口 = Step(step_num=1, name="Write a method with 400 long-named helpers",
             purpose="Their snapshot paths, one argument each, total past the limit")
    source = _source(tmp_path)
    names = [f"helper-{i:03d}-" + "x" * 53 + ".txt" for i in range(400)]
    for i, name in enumerate(names):
        (source / name).write_bytes(f"helper {i}\n".encode())
    yaml = source / "method.yaml"
    yaml.write_bytes(yaml.read_bytes() + b"helpers:\n"
                     + b"".join(f"  - {n}\n".encode() for n in names))
    rel = _rel(root, layout.method_dir(root, MODULE, METHOD))
    assert sum(len(f"{rel}/{n}") + 1 for n in names) > WINDOWS_COMMAND_LINE_LIMIT

    口 = Step(step_num=2, name="Register it",
             purpose="It registers as v1")
    assert _register(source).label == 1

    口 = Step(step_num=3, name="The commit holds exactly the verified tree",
             purpose="HEAD's snapshot tree is the version's, with every source file's bytes")
    entry = newest_entry(read_version_list(root, MODULE, METHOD))
    assert _git(root, "rev-parse", f"HEAD:{rel}").strip() == entry.tree_hash
    # Archived from HEAD's root, so the registry's -text attribute applies.
    with tarfile.open(fileobj=io.BytesIO(_git_bytes(root, "archive", "--format=tar",
                                                     "HEAD", "--", rel))) as tar:
        committed = {m.name.removeprefix(f"{rel}/"): tar.extractfile(m).read()
                     for m in tar if m.isfile()}
    assert committed == _files(source)
    assert _files(layout.method_dir(root, MODULE, METHOD)) == _files(source)
    assert _registry_status(root) == ""


# Windows' classic path limit; git without core.longpaths cannot open a longer path.
WINDOWS_MAX_PATH = 260
# Git for Windows cannot find a repository whose root path is this long or
# longer, core.longpaths or not.
GIT_WINDOWS_MAX_REPO_ROOT = 247


def _long_paths_enabled() -> bool:
    """Whether this is Windows with the ``LongPathsEnabled`` registry value set to 1."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


@pytest.mark.skipif(not _long_paths_enabled(),
                    reason="needs Windows with LongPathsEnabled=1: Python cannot "
                           "create a path past 260 characters otherwise")
@workflow(purpose="A method registers in a project whose root is deep enough that "
                  "its staged and registered files' paths pass Windows' "
                  "260-character limit, even in a repository whose own config "
                  "does not allow long paths; the repository wfc init creates "
                  "allows them for the user's own git commands")
def test_a_method_registers_in_a_project_deeper_than_windows_path_limit(
        tmp_path, monkeypatch, request):
    from tests.fixtures.routes import init_test_project
    from wfc import layout
    from wfc.persistence import reset_engine
    from wfc.registration import register_module
    from wfc.registration.method_version import newest_entry, read_version_list

    口 = Step(step_num=1, name="Make a project root deep enough",
             purpose="A staged method.yaml and a registered one both lie past "
                     "260 characters, and the root stays short enough for git "
                     "to find the repository")
    root = tmp_path
    suffixes = [len(str(path)) - len(str(root)) for path in (
        layout.registry_staging_dir(root) / "m-xxxxxxxx" / METHOD / "method.yaml",
        layout.method_dir(root, MODULE, METHOD) / "method.yaml")]
    # The shortest root that puts both files past the limit: deep steps while
    # one more leaves room for a padding component, then the padding lands on it.
    need = WINDOWS_MAX_PATH + 1 - min(suffixes)
    deep = "deep-" + "d" * 40
    while need - len(str(root)) > len(deep) + 2:
        root = root / deep
    root = root / ("p" * (need - len(str(root)) - 1))
    assert len(str(root)) == need, (
        f"tmp_path is {len(str(tmp_path))} characters: too long to land a root on {need}")
    assert need < GIT_WINDOWS_MAX_REPO_ROOT, (
        f"a {need}-character root: git finds no repository at "
        f"{GIT_WINDOWS_MAX_REPO_ROOT} characters or more")
    assert min(len(str(root)) + suffix for suffix in suffixes) > WINDOWS_MAX_PATH
    root.mkdir(parents=True)

    口 = Step(step_num=2, name="Build the project through wfc init",
             purpose="init_project makes the repository; the module registers "
                     "through production")
    monkeypatch.chdir(root)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{layout.db_path(root)}")
    request.addfinalizer(reset_engine)
    init_test_project(root)
    fakes.fixture_env_record(root)
    reset_engine()
    register_module(name=MODULE, contracts=[])

    口 = Step(step_num=3, name="The repository init made allows long paths",
             purpose="The user's own git reads core.longpaths from it")
    assert _git(root, "config", "--local", "--get", "core.longpaths").strip() == "true"

    口 = Step(step_num=4, name="Stand for a repository init did not make",
             purpose="With the setting gone, only wfc's own git calls can "
                     "reach the long paths")
    _git(root, "config", "--local", "--unset", "core.longpaths")

    口 = Step(step_num=5, name="Register the method",
             purpose="It registers as v1")
    source = _source(tmp_path)
    assert _register(source).label == 1

    口 = Step(step_num=6, name="The commit holds the verified tree",
             purpose="HEAD's snapshot tree is the version's, the registered files "
                     "are the source's, and git status shows nothing under the "
                     "registry")
    # This git has no core.longpaths of its own now, so it names the option too.
    rel = _rel(root, layout.method_dir(root, MODULE, METHOD))
    entry = newest_entry(read_version_list(root, MODULE, METHOD))
    assert _git(root, "-c", "core.longpaths=true", "rev-parse",
                f"HEAD:{rel}").strip() == entry.tree_hash
    assert _files(layout.method_dir(root, MODULE, METHOD)) == _files(source)
    assert _git(root, "-c", "core.longpaths=true", "status", "--porcelain", "--",
                ".wfc/registry", ".gitattributes") == ""


@workflow(purpose="method.commit: with an unrelated file staged, a "
                  "registration whose pre-commit hook refuses commits nothing and "
                  "leaves the staged file staged and nothing else; once the hook "
                  "allows it, registration commits only the registry and the "
                  "staged file is still staged and not committed")
def test_the_users_staged_entry_stays_staged_through_a_refused_and_a_made_commit(
        module, tmp_path):
    from wfc import layout

    root = module
    source = _source(tmp_path)
    (root / "notes.txt").write_bytes(b"mine\n")
    _git(root, "add", "notes.txt")
    hook = _hook(root, "pre-commit", b"exit 1\n")
    head = _git(root, "rev-parse", "HEAD")

    口 = Step(step_num=1, name="A refusing hook", purpose="Nothing committed or staged")
    with pytest.raises(RuntimeError):
        _register(source)
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "diff", "--cached", "--name-only").split() == ["notes.txt"]
    assert not layout.method_dir(root, MODULE, METHOD).exists()
    assert not layout.method_versions_path(root, MODULE, METHOD).exists()
    assert _registry_status(root) == ""

    口 = Step(step_num=2, name="The hook allows it", purpose="Only the registry is committed")
    hook.unlink()
    _register(source)
    assert _git(root, "rev-parse", "HEAD") != head
    assert _git(root, "diff", "--cached", "--name-only").split() == ["notes.txt"]
    assert _git(root, "ls-tree", "HEAD", "notes.txt") == ""
    assert _registry_status(root) == ""


@workflow(purpose="Snapshot and version list: while a registration "
                  "commits, the registry holds the old version's snapshot and "
                  "list bytes; after every rename that puts the new version in, "
                  "the snapshot directory exists and every file both versions "
                  "hold is present with its old bytes or its new ones, and the "
                  "list is the old list or the new one")
def test_a_reader_finds_old_or_new_bytes_never_a_partial_or_missing_file(
        module, tmp_path, monkeypatch):
    from wfc import layout

    root = module
    snapshot = layout.method_dir(root, MODULE, METHOD)
    list_path = layout.method_versions_path(root, MODULE, METHOD)

    口 = Step(step_num=1, name="v1, and a v2 that changes, adds and drops files",
             purpose="old.py only in v1, new.py only in v2, the script changed")
    source = _source(tmp_path)
    (source / "old.py").write_bytes(b"OLD = 1\n")
    _register(source)
    v1, v1_list = _files(snapshot), list_path.read_bytes()
    (source / "old.py").unlink()
    (source / "new.py").write_bytes(b"NEW = 1\n")
    script = source / f"{METHOD}.py"
    script.write_bytes(script.read_bytes() + b"\n# v2\n")

    口 = Step(step_num=2, name="Probe mid-commit and after every rename",
             purpose="A pre-commit hook copies the module's registry; each "
                     "os.replace is followed by a look at the snapshot and list")
    probe = root / ".wfc" / "build" / "probe"
    module_rel = _rel(root, layout.module_methods_dir(root, MODULE))
    _hook(root, "pre-commit", f'cp -r "{module_rel}" "{probe.relative_to(root).as_posix()}"\n'
          .encode())
    seen: list[tuple[bool, dict[str, bytes], bytes | None]] = []
    real_replace = os.replace

    def replace(src, dst):
        real_replace(src, dst)
        seen.append((snapshot.is_dir(), _files(snapshot),
                     list_path.read_bytes() if list_path.is_file() else None))

    fake_os_replace(monkeypatch, replace)
    assert _register(source).label == 2
    monkeypatch.undo()
    v2, v2_list = _files(snapshot), list_path.read_bytes()

    口 = Step(step_num=3, name="Mid-commit the registry was v1",
             purpose="Nothing of v2 is on disk until the commit is made")
    assert _files(probe / METHOD) == v1
    assert (probe / list_path.name).read_bytes() == v1_list

    口 = Step(step_num=4, name="Every state after a rename is old or new per file",
             purpose="Never absent, never partial")
    assert v2 != v1 and set(v1) & set(v2)
    assert len(seen) >= len(v2) + 1  # the list and each file of the snapshot
    for present, files, listed in seen:
        assert present
        assert listed in (v1_list, v2_list)
        for name in set(v1) & set(v2):
            assert name in files
            assert files[name] in (v1[name], v2[name])
        for name, data in files.items():
            assert data in (v1.get(name), v2.get(name))
    assert _registry_status(root) == ""


def _lose_history(root: Path, before: str, tree: str) -> None:
    """Remove the registration's commit from history and garbage-collect its tree."""
    _git(root, "reset", "-q", "--soft", before)
    _git(root, "rm", "-r", "-q", "--cached", ".wfc/registry")
    _git(root, "commit", "-q", "--allow-empty", "-m", "history rewritten")
    _git(root, "update-ref", "-d", "ORIG_HEAD", check=False)
    _git(root, "reflog", "expire", "--expire=now", "--all")
    _git(root, "gc", "-q", "--prune=now")
    gone = subprocess.run(["git", "cat-file", "-e", tree], cwd=root, capture_output=True)
    assert gone.returncode != 0, "the version's tree is still in the object store"


@workflow(purpose="snapshot.copy-reextracted: register, remove the "
                  "version's commit from history and garbage-collect, register "
                  "the same content again: no version is added, a new commit "
                  "holds the version's tree, the list is not rewritten, git "
                  "status is clean and the version runs")
def test_same_content_whose_tree_was_collected_is_committed_again(module, tmp_path):
    from wfc import layout
    from wfc.registration.method import SAME
    from wfc.registration.method_version import newest_entry, read_version_list
    from wfc.registration.registered_code import resolve_method_code

    root = module
    source = _source(tmp_path)
    before = _git(root, "rev-parse", "HEAD").strip()
    _register(source)
    entry = newest_entry(read_version_list(root, MODULE, METHOD))
    list_path = layout.method_versions_path(root, MODULE, METHOD)
    list_bytes = list_path.read_bytes()
    _lose_history(root, before, entry.tree_hash)

    result = _register(source)
    assert result.outcome == SAME and result.label == 1
    rel = _rel(root, layout.method_dir(root, MODULE, METHOD))
    assert _git(root, "rev-parse", f"HEAD:{rel}").strip() == entry.tree_hash
    assert list_path.read_bytes() == list_bytes
    assert _git_bytes(root, "cat-file", "-p", f"HEAD:{_rel(root, list_path)}") == list_bytes
    assert _registry_status(root) == ""
    code = resolve_method_code(MODULE, METHOD, root)
    assert code.entry.label == 1


@workflow(purpose="A same-content registration whose tree was "
                  "garbage-collected, from a source that differs only in what "
                  "runs do not use (a method.yaml description), cannot rebuild "
                  "the recorded tree and is refused naming the version; nothing "
                  "is committed")
def test_same_content_that_cannot_rebuild_the_collected_tree_is_refused(module, tmp_path):
    from wfc.registration.method_version import newest_entry, read_version_list

    root = module
    source = _source(tmp_path)
    before = _git(root, "rev-parse", "HEAD").strip()
    _register(source)
    entry = newest_entry(read_version_list(root, MODULE, METHOD))
    _lose_history(root, before, entry.tree_hash)
    yaml = source / "method.yaml"
    yaml.write_bytes(yaml.read_bytes().replace(b"Input CSV to transform",
                                               b"The CSV to transform"))
    state = _state(root)

    with pytest.raises(ValueError) as refused:
        _register(source)
    assert "v1" in str(refused.value) and entry.tree_hash in str(refused.value)
    assert _state(root) == state


def _all_rows() -> dict[str, list[tuple]]:
    """Every row of every table in the project database, sorted per table."""
    from sqlalchemy import inspect, text

    from wfc.persistence import get_session
    with get_session() as session:
        conn = session.connection()
        return {name: sorted((tuple(r) for r in conn.execute(
                    text(f'SELECT * FROM "{name}"')).all()), key=repr)
                for name in inspect(conn).get_table_names()}


def _version_labels() -> list[int | None]:
    from sqlmodel import select

    from wfc.persistence import Method, MethodVersion, get_session
    with get_session() as session:
        method = session.exec(select(Method).where(Method.name == METHOD)).first()
        if method is None:
            return []
        return sorted(v.label for v in session.exec(select(MethodVersion).where(
            MethodVersion.method_id == method.id)))


@pytest.mark.parametrize("leg", ["new-method", "new-version"])
@workflow(purpose="Registration: a failure at the version rows, after "
                  "the method row, its tracked functions and the version rows "
                  "are written in registration's transaction, leaves none of "
                  "them: every table holds what it held before (no method row "
                  "for a new method, the earlier version's rows intact for a "
                  "new version); registering again writes the rows for the "
                  "version the failed registration committed")
def test_a_failure_at_the_version_rows_leaves_no_registration_rows(
        module, tmp_path, monkeypatch, leg):
    from tests.fixtures import fakes
    from wfc.registration.method_version import read_version_list

    口 = Step(step_num=1, name="Set up the leg",
             purpose="new-method: nothing registered; new-version: v1 "
                     "registered and a function added to the script, so v2's "
                     "tracked functions differ from v1's")
    root = module
    source = _source(tmp_path)
    if leg == "new-version":
        _register(source)
        script = source / f"{METHOD}.py"
        script.write_bytes(script.read_bytes()
                           + b"\n\ndef scale(value, factor=2):\n    return value * factor\n")
    label = 2 if leg == "new-version" else 1
    rows_before = _all_rows()

    口 = Step(step_num=2, name="Register with the version rows failing",
             purpose="The real rows are flushed into the transaction, then the "
                     "write raises")
    with monkeypatch.context() as mp:
        flushed = fakes.fault_at_version_rows_write(mp, RuntimeError("database went away"))
        with pytest.raises(RuntimeError, match="database went away"):
            _register(source)
    assert flushed and label in flushed[-1], "the version rows were never written"

    口 = Step(step_num=3, name="No row of the failed registration is left",
             purpose="Every table, the method, tracked-function, parameter and "
                     "version tables among them, as before")
    assert _all_rows() == rows_before
    assert _version_labels() == ([1] if leg == "new-version" else [])

    口 = Step(step_num=4, name="Register again",
             purpose="The commit had landed; the next registration writes the "
                     "rows for the version list it committed")
    _register(source)
    listed = [e.label for e in read_version_list(root, MODULE, METHOD)]
    assert listed[-1] == label
    assert _version_labels() == listed


@pytest.mark.parametrize("attributes", ["missing", "lacking"])
@workflow(purpose="The registry's .gitattributes line: with .gitattributes "
                  "missing, or present without the line, and core.autocrlf=true, "
                  "registration commits the line with the version, git status is "
                  "clean and the version extracts byte for byte")
def test_registration_commits_the_registry_line_when_head_lacks_it(
        module, tmp_path, attributes):
    from wfc import layout
    from wfc.registration.method_version import newest_entry, read_version_list
    from wfc.registration.registered_code import extract_version_copy

    root = module
    attrs = root / ".gitattributes"
    if attributes == "missing":
        if _git(root, "ls-files", ".gitattributes").strip():
            _git(root, "rm", "-q", ".gitattributes")
            _git(root, "commit", "-q", "-m", "drop attributes")
        attrs.unlink(missing_ok=True)
    else:
        attrs.write_bytes(b"*.png binary\n")
        _commit_all(root, ".gitattributes")
    _git(root, "config", "core.autocrlf", "true")
    source = _source(tmp_path)
    helper = b"X = 1\r\nY = 2\r\n"
    (source / "util.py").write_bytes(helper)

    _register(source)
    held = _git_bytes(root, "cat-file", "-p", "HEAD:.gitattributes").decode()
    assert layout.REGISTRY_GITATTRIBUTES_LINE in held.splitlines()
    assert _registry_status(root) == ""
    entry = newest_entry(read_version_list(root, MODULE, METHOD))
    copy = extract_version_copy(MODULE, METHOD, entry, root)
    assert (copy / "util.py").read_bytes() == helper


CHILD = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
name, source, signals = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
order = sys.argv[5]

import pytest
from tests.fixtures import fakes

# The barrier the test names: each process's first `git commit` waits for
# the other to reach its own; B's commit then runs only after A's has landed.
getattr(fakes, order)(pytest.MonkeyPatch(), name, signals)

from wfc.registration import register_method
print("LABEL", register_method(method_dir=source, module_name="mod", assume_yes=True).label)
'''


@workflow(purpose="Two processes register different content of one "
                  "method at once, their commits ordered so the second is built "
                  "on the HEAD the first moved; the second is taken back off "
                  "HEAD and retried under the next label, so the list holds "
                  "v1, v2 and v3, both new versions run with their own code and "
                  "git status is clean")
def test_two_processes_registering_one_method_get_distinct_labels(module, tmp_path):
    from wfc.registration.method_version import read_version_list
    from wfc.registration.registered_code import resolve_method_code

    root = module
    _register(_source(tmp_path, "v1"))
    sources = {}
    for name in ("A", "B"):
        sources[name] = _source(tmp_path, name)
        script = sources[name] / f"{METHOD}.py"
        script.write_bytes(script.read_bytes() + f"\n# {name}\n".encode())
    signals = tmp_path / "signals"
    signals.mkdir()
    child = tmp_path / "child_register.py"
    child.write_text(CHILD, newline="\n")

    procs = {name: subprocess.Popen(
        [sys.executable, str(child), str(REPO), name, str(sources[name]), str(signals),
         fakes.order_registration_commits.__name__],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for name in ("A", "B")}
    outputs = {name: proc.communicate(timeout=300) for name, proc in procs.items()}
    for name, proc in procs.items():
        assert proc.returncode == 0, outputs[name][1]
    labels = {name: next(line.split()[1] for line in out.splitlines()
                         if line.startswith("LABEL")) for name, (out, _) in outputs.items()}
    assert labels == {"A": "2", "B": "3"}, (_git(root, "reflog", "HEAD"), outputs)

    entries = read_version_list(root, MODULE, METHOD)
    assert [e.label for e in entries] == [1, 2, 3]
    assert "wfc: undo a commit built on a moved HEAD" in _git(root, "reflog", "HEAD")
    for entry, name in zip(entries[1:], ("A", "B")):
        code = resolve_method_code(MODULE, METHOD, root, version=entry.identity)
        assert code.script.read_bytes() == (sources[name] / f"{METHOD}.py").read_bytes()
    assert _registry_status(root) == ""


def test_commit_over_head_refuses_while_a_merge_is_in_progress(git_project):
    from wfc.version import commit_over_head

    root = git_project
    main = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(root, "checkout", "-q", "-b", "side")
    (root / "side.txt").write_bytes(b"side\n")
    _commit_all(root, "side.txt")
    _git(root, "checkout", "-q", main)
    (root / "main.txt").write_bytes(b"main\n")
    _commit_all(root, "main.txt")
    _git(root, "merge", "-q", "--no-commit", "--no-ff", "side")
    head = _git(root, "rev-parse", "HEAD").strip()

    with pytest.raises(RuntimeError, match="merge"):
        commit_over_head(root, head, blobs={root / "x.txt": b"x\n"}, message="m")
    assert _git(root, "rev-parse", "HEAD").strip() == head
    assert _git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).strip()
