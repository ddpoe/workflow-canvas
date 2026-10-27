"""A clean git tree after ``wfc init`` and every registration verb.

Every wfc commit names its paths, runs after the last write, and leaves what
the user staged alone; a refused registration leaves nothing; init sets DVC up
before its commit and, in an existing project, asks first; every verb acts on
the resolved project root. Real ``git`` in temporary repositories throughout.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import stub_interactive_prompt, stub_terminal
from tests.fixtures.routes import init_test_project, project_archive_dir
from tests.fixtures.routes.registration import write_env_record

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"


def _git(root: Path, *args: str) -> str:
    """Run git in *root* and return stdout (raises on failure)."""
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout


def _porcelain(root: Path) -> str:
    return _git(root, "status", "--porcelain").strip()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _head_files(root: Path) -> set[str]:
    out = _git(root, "show", "--name-only", "--pretty=format:", "HEAD")
    return {ln for ln in out.splitlines() if ln}


@pytest.fixture
def clean_project(git_project, monkeypatch):
    """A project ``wfc init`` produced, with its env committed: a clean tree.

    The env record stands in for a Docker build (``write_env_record``, the
    registration serializer), and the user commits it as they would after a
    real ``register-env`` -- whose own commit is covered in the integration
    suite.
    """
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    write_env_record(git_project, "fixture-env")
    _git(git_project, "add", ".wfc/envs.json", ".wfc/captures")
    _git(git_project, "commit", "-q", "-m", "user commits the env")
    reset_engine()
    assert _porcelain(git_project) == ""
    yield git_project
    reset_engine()


@pytest.fixture
def outside_source(tmp_path_factory) -> Path:
    """A method directory outside the repository."""
    dest = tmp_path_factory.mktemp("elsewhere") / "transform"
    shutil.copytree(FIXTURE_METHOD, dest)
    return dest


def _snapshot(root: Path, module: str = "mod", method: str = "transform") -> Path:
    """Return the method's snapshot directory, as Layout derives it."""
    from wfc.layout import tree
    return tree.method_dir(root, module, method)


def _snapshot_rel(root: Path, name: str) -> str:
    """Return *name* inside the transform snapshot, relative to *root* (posix)."""
    return (_snapshot(root) / name).relative_to(root).as_posix()


def _register(method_dir: Path, module: str = "mod", contracts=None) -> None:
    from wfc.registration import register_method, register_module
    register_module(name=module, contracts=contracts or [])
    register_method(method_dir=method_dir, module_name=module, assume_yes=True)


@workflow(purpose="Registering a method from outside the repository leaves git "
                  "status empty, and the registration commit holds the snapshot "
                  "and the method's version list, nothing else")
def test_register_outside_source_leaves_tree_clean(clean_project, outside_source):
    from wfc.layout import tree
    _register(outside_source)
    assert _porcelain(clean_project) == ""
    head_files = _head_files(clean_project)
    list_rel = tree.method_versions_path(clean_project, "mod", "transform") \
        .relative_to(clean_project).as_posix()
    assert _snapshot_rel(clean_project, "transform.py") in head_files
    assert _snapshot_rel(clean_project, "method.yaml") in head_files
    assert list_rel in head_files
    assert all(f.startswith(_snapshot_rel(clean_project, "")) or f == list_rel
               for f in head_files)


@workflow(purpose="A file the user staged before registering is still staged, "
                  "and not in the registration commit")
def test_user_staged_file_is_not_swept_in(clean_project, outside_source):
    (clean_project / "notes.txt").write_text("mine\n")
    _git(clean_project, "add", "notes.txt")
    _register(outside_source)
    assert "notes.txt" not in _head_files(clean_project)
    assert _porcelain(clean_project) == "A  notes.txt"


@workflow(purpose="Re-registering a committed method with changed code leaves "
                  "the tree clean, and the run gate accepts it")
def test_reregister_changed_method_keeps_tree_clean(clean_project, outside_source):
    from wfc.version import get_git_commit
    _register(outside_source)
    first = _head(clean_project)
    script = outside_source / "transform.py"
    script.write_text(script.read_text() + "\n# changed\n")
    _register(outside_source)
    assert _head(clean_project) != first
    assert _porcelain(clean_project) == ""
    assert get_git_commit(clean_project) == _head(clean_project)


def _refuse_at_contract(project: Path, source: Path) -> None:
    _register(source, contracts=[{"type": "output", "name": "absent_output"}])


def _refuse_at_commit(project: Path, source: Path) -> None:
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _register(source)


@workflow(purpose="A registration refused at a late step -- the module-contract "
                  "check or the git commit itself -- leaves no rows, no snapshot "
                  "and no commit")
@pytest.mark.parametrize("refuse", [_refuse_at_contract, _refuse_at_commit],
                         ids=["contract-check", "git-commit"])
def test_late_refusal_leaves_nothing(clean_project, outside_source, refuse):
    from sqlmodel import select
    from wfc.persistence import get_session, Method, MethodContract

    head = _head(clean_project)
    with pytest.raises((ValueError, RuntimeError)):
        refuse(clean_project, outside_source)
    with get_session() as s:
        assert s.exec(select(Method)).all() == []
        assert s.exec(select(MethodContract)).all() == []
    assert not _snapshot(clean_project).exists()
    assert _head(clean_project) == head
    assert _porcelain(clean_project) == ""


@workflow(purpose="A registration whose git commit hangs in a pre-commit hook is "
                  "stopped at the commit timeout and refused loudly, leaving no "
                  "rows, no snapshot, no commit, nothing staged and no index "
                  "lock")
def test_a_hanging_commit_hook_is_stopped_and_leaves_nothing(
        clean_project, outside_source, monkeypatch):
    import time
    from sqlmodel import select
    from wfc.persistence import get_session, Method

    head = _head(clean_project)
    hook = clean_project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 120\n")
    hook.chmod(0o755)
    monkeypatch.setenv("WFC_GIT_COMMIT_TIMEOUT", "3")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="did not finish within 3 s"):
        _register(outside_source)
    assert time.monotonic() - started < 60, "the hung hook was waited on"

    with get_session() as s:
        assert s.exec(select(Method)).all() == []
    assert not _snapshot(clean_project).exists()
    assert not (clean_project / ".git" / "index.lock").exists()
    assert _head(clean_project) == head
    assert _porcelain(clean_project) == ""


@workflow(purpose="register-module commits the in-repository module.yaml it read, "
                  "leaving the tree clean")
def test_register_module_commits_module_yaml(clean_project):
    from wfc.registration import register_module
    mod_dir = clean_project / "modules" / "seg"
    mod_dir.mkdir(parents=True)
    (mod_dir / "module.yaml").write_text(
        "description: segmentation\ncontracts: []\n"
    )
    register_module(name="seg", module_dir=mod_dir)
    assert _porcelain(clean_project) == ""
    assert "modules/seg/module.yaml" in _head_files(clean_project)


@workflow(purpose="wfc demo --remove deletes the demo module's snapshot directory, "
                  "the demo method's version list with it, and the demo env's "
                  "captures folder, and commits the deletion, with the env "
                  "manifest it changed, leaving the tree "
                  "clean and a user's same-named method and its list intact")
def test_demo_remove_commits_the_snapshot_deletion(clean_project, outside_source):
    from wfc.demo.remove import remove_demo
    from wfc.demo.scaffold import DEMO_ENV, DEMO_MODULE, write_demo_stamp
    from wfc.layout import tree
    from wfc.registration import register_method, register_module
    from wfc.version import get_git_commit

    # The demo's env, committed as register-env's own commit would.
    write_env_record(clean_project, DEMO_ENV, image="local/wfc-demo-env")
    _git(clean_project, "add", ".wfc/envs.json", ".wfc/captures")
    _git(clean_project, "commit", "-q", "-m", "user commits the demo env")
    register_module(name=DEMO_MODULE, contracts=[], allow_reserved=True)
    # A demo method names the demo env, as every bundled asset does: the
    # removal deletes every env the demo's methods name. Same source, its
    # env line pointed at the demo env.
    import shutil
    demo_source = outside_source.parent.parent / "demo-source" / "transform"
    shutil.copytree(outside_source, demo_source)
    demo_yaml = demo_source / "method.yaml"
    demo_yaml.write_text(
        demo_yaml.read_text("utf-8").replace("fixture-env", DEMO_ENV), "utf-8")
    register_method(
        method_dir=demo_source, module_name=DEMO_MODULE,
        method_name=f"{DEMO_MODULE}transform", script_name="transform.py",
        allow_reserved=True,
        assume_yes=True,
    )
    # The user's own `transform`, from the same source, in another module.
    _register(outside_source)
    demo_rel = tree.module_methods_dir(clean_project, DEMO_MODULE) \
        .relative_to(clean_project).as_posix()
    demo_list = tree.method_versions_path(
        clean_project, DEMO_MODULE, f"{DEMO_MODULE}transform")
    user_list = tree.method_versions_path(clean_project, "mod", "transform")
    demo_list_rel = demo_list.relative_to(clean_project).as_posix()
    assert _git(clean_project, "ls-files", "--", demo_rel) != ""
    assert _git(clean_project, "ls-files", "--", demo_list_rel) != ""
    assert _porcelain(clean_project) == ""

    demo_captures = f".wfc/captures/{DEMO_ENV}"
    assert _git(clean_project, "ls-files", "--", demo_captures) != ""
    # The stamp, written and committed as `wfc demo` does.
    write_demo_stamp(clean_project)
    assert _git(clean_project, "ls-files", "--", ".wfc/demo.json") != ""
    assert _porcelain(clean_project) == ""

    assert remove_demo(clean_project, assume_yes=True) == 0

    assert not (clean_project / ".wfc" / "demo.json").exists()
    assert _git(clean_project, "ls-files", "--", ".wfc/demo.json") == ""
    assert not (clean_project / demo_captures).exists()
    assert _git(clean_project, "ls-files", "--", demo_captures) == ""
    assert any(f.startswith(f"{demo_captures}/") for f in _head_files(clean_project))
    assert not (clean_project / demo_rel).exists()
    assert not demo_list.exists()
    assert _git(clean_project, "ls-files", "--", demo_rel) == ""
    assert _git(clean_project, "ls-files", "--", demo_list_rel) == ""
    assert user_list.is_file()
    assert _porcelain(clean_project) == ""
    assert get_git_commit(clean_project) == _head(clean_project)
    assert all(
        f.startswith((f"{demo_rel}/", f"{demo_captures}/"))
        or f in (".wfc/envs.json", ".wfc/demo.json")
        for f in _head_files(clean_project)
    )
    assert _snapshot_rel(clean_project, "transform.py") in _git(
        clean_project, "ls-files", "--", _snapshot_rel(clean_project, ""),
    )


# --- init -------------------------------------------------------------------

def _is_ignored(root: Path, relpath: str) -> bool:
    """Return whether git would ignore *relpath* in *root* (it need not exist)."""
    return subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", relpath], cwd=root,
    ).returncode == 0


@workflow(purpose="init in a fresh directory creates only .wfc/, .gitignore, "
                  ".gitattributes and the DVC files; it sets DVC up before its "
                  "commit, which holds exactly its five files; .gitattributes "
                  "keeps captures byte-exact; every path it creates is "
                  "committed or ignored, and so does the registry; the run "
                  "archive and sample workspace "
                  "are ignored and the registry is not; a second init changes "
                  "nothing")
def test_init_fresh_directory_leaves_clean_tree(tmp_path):
    proj = tmp_path / "fresh"
    init_test_project(proj)
    top = {p.name for p in proj.iterdir()} - {".git"}
    assert top <= {".wfc", ".gitignore", ".gitattributes", ".dvc",
                   ".dvcignore"}, top
    assert {".wfc", ".gitignore", ".gitattributes", ".dvc"} <= top
    assert _porcelain(proj) == ""
    assert _head_files(proj) == {
        ".gitignore", ".gitattributes", ".wfc/wf-canvas.toml", ".dvc/config",
        ".dvc/.gitignore",
    }
    attributes = (proj / ".gitattributes").read_text().splitlines()
    assert ".wfc/captures/** -text" in attributes
    assert ".wfc/registry/** -text" in attributes
    ignore = (proj / ".gitignore").read_text().splitlines()
    for line in (".snakemake/", ".dvc/cache/", ".wfc/build/", ".wfc/runs/",
                 ".wfc/workspace/"):
        assert line in ignore
    assert ".runs/" not in ignore and "data/" not in ignore
    assert _is_ignored(proj, ".wfc/runs/00000001/out.csv")
    assert _is_ignored(proj, ".wfc/workspace/samples/s1/s1.csv")
    assert not _is_ignored(proj, ".wfc/registry/methods/mod/filter/filter.py")
    head = _head(proj)
    init_test_project(proj)
    assert _head(proj) == head
    assert _porcelain(proj) == ""


@workflow(purpose="The generated config holds only [dvc]; a user-added [pixi] "
                  "root and [conda] root are still read")
def test_generated_config_holds_only_dvc(tmp_path):
    from wfc.persistence import read_config
    proj = tmp_path / "cfg"
    init_test_project(proj)
    cfg_path = proj / ".wfc" / "wf-canvas.toml"
    assert set(tomllib.loads(cfg_path.read_text())) == {"dvc"}
    pixi_root = (tmp_path / "shared-pixi").as_posix()
    conda_root = (tmp_path / "conda-base").as_posix()
    cfg_path.write_text(
        cfg_path.read_text()
        + f'\n[pixi]\nroot = "{pixi_root}"\n\n[conda]\nroot = "{conda_root}"\n'
    )
    cfg = read_config(proj)
    assert Path(cfg["pixi_root"]) == Path(pixi_root).resolve()
    assert Path(cfg["conda_root"]) == Path(conda_root).resolve()


@pytest.fixture
def existing_repo(tmp_path) -> Path:
    """A user's repository with history and no wfc: the adoption case."""
    repo = tmp_path / "theirs"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "analysis.py").write_text("print('hi')\n")
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=u@u", "-c", "user.name=u", "commit", "-q", "-m", "mine")
    return repo


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@workflow(purpose="init in an existing project lists what it would change, and "
                  "answering no leaves the tree byte-identical")
def test_init_existing_project_no_changes_nothing(existing_repo, monkeypatch, capsys):
    from wfc.init import init_project
    before, head = _tree_snapshot(existing_repo), _head(existing_repo)
    stub_terminal(monkeypatch, True)
    stub_interactive_prompt(monkeypatch, "n")
    assert init_project(existing_repo, archive=str(project_archive_dir(existing_repo))) == {}
    out = capsys.readouterr().out
    assert "[dvc]" in out and ".snakemake/" in out and "DVC: set up" in out
    assert _tree_snapshot(existing_repo) == before
    assert _head(existing_repo) == head


@workflow(purpose="wfc init in an existing project, run as a real process whose "
                  "stdin is a pipe and without --yes, exits non-zero naming "
                  "--yes and changes nothing: no config, ignore lines, DVC "
                  "setup, staged entry or commit")
def test_init_existing_project_without_tty_refuses(existing_repo):
    before = _tree_snapshot(existing_repo)
    porcelain, head = _porcelain(existing_repo), _head(existing_repo)
    result = subprocess.run(
        [sys.executable, "-m", "wfc", "init", "--dir", str(existing_repo),
         "--archive", str(project_archive_dir(existing_repo))],
        stdin=subprocess.PIPE, capture_output=True, text=True,
        cwd=existing_repo, timeout=120,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "--yes" in result.stderr
    assert _tree_snapshot(existing_repo) == before
    assert not (existing_repo / ".wfc").exists()
    assert not (existing_repo / ".dvc").exists()
    assert _porcelain(existing_repo) == porcelain
    assert _head(existing_repo) == head


@workflow(purpose="init --yes in an existing project applies and commits only the "
                  "listed files, keeps the user's staged work staged, and an "
                  "explicit --archive reaches [dvc]")
def test_init_existing_project_yes_commits_only_listed(existing_repo):
    (existing_repo / "draft.py").write_text("x = 1\n")
    _git(existing_repo, "add", "draft.py")
    archive = project_archive_dir(existing_repo)
    init_test_project(existing_repo)  # archive=project_archive_dir, assume_yes
    assert _head_files(existing_repo) == {
        ".gitignore", ".gitattributes", ".wfc/wf-canvas.toml", ".dvc/config",
        ".dvc/.gitignore",
    }
    assert _porcelain(existing_repo) == "A  draft.py"
    cfg = tomllib.loads((existing_repo / ".wfc" / "wf-canvas.toml").read_text())
    assert Path(cfg["dvc"]["url"]) == archive.resolve()
    assert "*.log" in (existing_repo / ".gitignore").read_text()


# --- one project root --------------------------------------------------------

def _root_via_readiness(root: Path) -> Path:
    from wfc.execution.readiness import default_project_dir
    return default_project_dir()


def _root_via_env_register(root: Path) -> Path:
    from wfc.environments.build import _validate_request
    resolved, _ = _validate_request("fresh-env", "byo", None, False, None, False)
    return resolved


def _root_via_pixi_fallback(root: Path) -> Path:
    from wfc.environments.host import _local_pixi_env_dir
    (root / ".pixi" / "envs" / "e").mkdir(parents=True)
    return _local_pixi_env_dir("e").parent.parent.parent


def _root_via_register_method(root: Path) -> Path:
    src = root / "src" / "transform"
    shutil.copytree(FIXTURE_METHOD, src)
    _register(src)
    assert (_snapshot(root) / "transform.py").exists()
    return root


@workflow(purpose="Each verb that takes the project root, run from a "
                  "subdirectory, acts on the project root")
@pytest.mark.parametrize("probe", [
    _root_via_readiness, _root_via_env_register, _root_via_pixi_fallback,
    _root_via_register_method,
], ids=["doctor-readiness", "register-env", "pixi-fallback", "register-method"])
def test_verbs_from_subdirectory_act_on_project_root(clean_project, monkeypatch, probe):
    from wfc.persistence import reset_engine
    sub = clean_project / "deep" / "er"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    monkeypatch.delenv("WFC_PROJECT_ROOT")
    reset_engine()
    assert Path(probe(clean_project)).resolve() == clean_project.resolve()
