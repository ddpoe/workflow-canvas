"""register-env commits its captures with the record, atomically.

Every registration writes ``.wfc/envs.json`` and the image's captures folder
``.wfc/captures/<env>/<fingerprint>/`` and commits them in one pathspec
commit with ``.gitattributes`` (whose ``.wfc/captures/** -text`` line it adds
when a project lacks it). A captures path git would ignore is refused before
any build; a refused commit leaves the tree as it was; and a folder, once
written, is never changed, except ``image.json``'s interpreter while no method
version records the image. A folder changed outside wfc is refused, naming a
new env name.

Envs are registered through production ``register`` with the Docker boundary
faked (``tests.fixtures.fakes``).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    refuse_docker,
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
    stub_readiness_probes,
)
from tests.fixtures.routes import init_test_project

ATTRIBUTES_LINE = ".wfc/captures/** -text"

SOURCES = {
    "pixi": {"pixi_lock_content": "version: 6\nenvironments: {}\n",
             "pixi_toml_content": '[project]\nname = "tools"\n',
             "pip_freeze_content": "numpy==1.26.4\n"},
    "conda": {"explicit_list_content":
              "@EXPLICIT\nhttps://x/python-3.11.0-h.conda#0a\n",
              "pip_freeze_content": "numpy==1.24.0\n"},
    "byo": {"image": "docker://ghcr.io/lab/tools:1.0"},
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _fake_docker(monkeypatch, layer: str = "1") -> None:
    """Fake every Docker call a registration makes; *layer* sets the content."""
    stub_docker_build(monkeypatch, None)
    stub_docker_pull(monkeypatch)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)
    stub_docker_repo_digest(monkeypatch,
                            lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_image_content(monkeypatch, {
        "layers": ["sha256:" + layer * 64], "env": ["PATH=/usr/bin"],
        "user": "", "workdir": "/"})


def _register(project: Path, backend: str, name: str = "tools", **kwargs):
    from wfc import environments as envs_mod

    return envs_mod.register(name=name, backend=backend,
                             source=dict(SOURCES[backend]),
                             project_dir=project, **kwargs)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under *root* by relative path, with its bytes."""
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def project(git_project, monkeypatch):
    """A ``wfc init`` project in git, tree clean."""
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    reset_engine()
    assert _git(git_project, "status", "--porcelain").strip() == ""
    yield git_project
    reset_engine()


@workflow(purpose="Whatever the backend, register-env commits everything it "
                  "writes outside .wfc/build/ in one commit: envs.json, the "
                  "image's captures folder, and .gitattributes with the "
                  "captures line an older project lacks; the tree is clean")
@pytest.mark.parametrize("backend", ["pixi", "conda", "byo"])
def test_register_commits_record_captures_and_attributes(
        project, monkeypatch, backend):
    # A project made before captures existed has no captures line.
    (project / ".gitattributes").unlink()
    _git(project, "add", "-A", "--", ".gitattributes")
    _git(project, "commit", "-q", "-m", "a project from before captures")
    _fake_docker(monkeypatch)

    record = _register(project, backend)

    assert _git(project, "status", "--porcelain").strip() == ""
    committed = set(_git(project, "show", "--name-only", "--format=",
                         "HEAD").split())
    folder = f".wfc/captures/tools/{record.env_fingerprint}"
    on_disk = {f"{folder}/{name}"
               for name in _tree_bytes(project / folder)}
    assert on_disk and committed == {".wfc/envs.json", ".gitattributes",
                                     *on_disk}
    assert ATTRIBUTES_LINE in (project / ".gitattributes").read_text(
        encoding="utf-8").splitlines()


@workflow(purpose="A project whose .gitignore ignores *.lock would not "
                  "commit packages.lock, and one ignoring pushed.json would "
                  "not commit a push record, for any backend: register-env "
                  "refuses before any Docker call, naming the rule, and "
                  "writes nothing")
@pytest.mark.parametrize(("rule", "backend"), [
    ("*.lock", "pixi"), ("pushed.json", "pixi"), ("pushed.json", "byo")])
def test_register_refuses_an_ignored_capture_path_before_any_build(
        project, monkeypatch, rule, backend):
    with open(project / ".gitignore", "a", encoding="utf-8") as fh:
        fh.write(f"{rule}\n")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-q", "-m", "ignore a capture file")
    refuse_docker(monkeypatch)

    with pytest.raises(ValueError) as exc:
        _register(project, backend)

    assert ".gitignore" in str(exc.value) and rule in str(exc.value)
    assert not (project / ".wfc" / "envs.json").exists()
    assert not (project / ".wfc" / "captures").exists()
    assert _git(project, "status", "--porcelain").strip() == ""


def _bytes_or_none(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


@workflow(purpose="When git refuses the register commit (a pre-commit hook), "
                  "envs.json, .wfc/captures/ and .gitattributes are "
                  "byte-identical to before and nothing is staged, including "
                  "on a project whose .gitattributes lacked the captures line "
                  "(absent, or other rules only) that register-env added")
@pytest.mark.parametrize("first, attributes", [
    (True, "init"), (False, "init"), (True, None), (True, "*.png binary\n")],
    ids=["first-env", "second-env", "no-gitattributes", "other-attributes"])
def test_refused_register_commit_leaves_the_tree_as_it_was(
        project, monkeypatch, first, attributes):
    attributes_path = project / ".gitattributes"
    if attributes != "init":
        # A project from before captures: no captures line to keep.
        attributes_path.unlink()
        if attributes is not None:
            attributes_path.write_text(attributes, encoding="utf-8", newline="\n")
        _git(project, "add", "-A", "--", ".gitattributes")
        _git(project, "commit", "-q", "-m", "a project from before captures")
    _fake_docker(monkeypatch)
    if not first:
        _register(project, "conda", name="other")
    manifest = project / ".wfc" / "envs.json"
    before = (_bytes_or_none(manifest),
              _tree_bytes(project / ".wfc" / "captures"),
              _bytes_or_none(attributes_path))
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)
    _fake_docker(monkeypatch, layer="2")

    with pytest.raises(RuntimeError, match="Nothing was recorded"):
        _register(project, "pixi")

    after = (_bytes_or_none(manifest),
             _tree_bytes(project / ".wfc" / "captures"),
             _bytes_or_none(attributes_path))
    assert after == before
    assert _git(project, "diff", "--cached", "--name-only").strip() == ""
    assert _git(project, "status", "--porcelain").strip() == ""


@workflow(purpose="When writing .wfc/envs.json fails after the captures "
                  "folder was written, register-env removes the new folder: "
                  "no record, no folder, nothing committed or staged")
def test_failed_manifest_write_removes_the_new_captures_folder(
        project, monkeypatch):
    _fake_docker(monkeypatch)
    manifest = project / ".wfc" / "envs.json"
    assert not manifest.exists()
    head = _git(project, "rev-parse", "HEAD")

    def build_then_block_the_manifest(path, tag):
        # A directory where envs.json goes: the atomic replace fails with a
        # real OSError on every OS, after the build and the captures write.
        manifest.mkdir()

    stub_docker_build(monkeypatch, build_then_block_the_manifest)

    with pytest.raises(OSError):
        _register(project, "pixi")

    manifest.rmdir()
    assert _tree_bytes(project / ".wfc" / "captures") == {}
    assert _git(project, "rev-parse", "HEAD") == head
    assert _git(project, "status", "--porcelain").strip() == ""


@workflow(purpose="Re-registering an env with a different image adds a "
                  "second captures folder and keeps the first one "
                  "byte-identical, both committed")
def test_re_register_keeps_the_first_captures_folder(project, monkeypatch):
    _fake_docker(monkeypatch, layer="1")
    first = _register(project, "pixi")
    first_dir = project / ".wfc" / "captures" / "tools" / first.env_fingerprint
    first_bytes = _tree_bytes(first_dir)

    _fake_docker(monkeypatch, layer="2")
    second = _register(project, "pixi", force=True)

    assert second.env_fingerprint != first.env_fingerprint
    assert _tree_bytes(first_dir) == first_bytes
    assert (project / ".wfc" / "captures" / "tools"
            / second.env_fingerprint).is_dir()
    assert _git(project, "status", "--porcelain").strip() == ""


FIRST_PYTHON = "/opt/first/bin/python"
SECOND_PYTHON = "/opt/second/bin/python"


def _cli_register_pixi(name: str, lock: Path, interpreter: str,
                       *, force: bool) -> int:
    """``wfc register-env <name> --from <lock> --backend pixi --interpreter X``."""
    from wfc.cli import cli_main

    argv = ["register-env", name, "--from", str(lock), "--backend", "pixi",
            "--interpreter", interpreter]
    return cli_main(argv + (["--force"] if force else []))


@workflow(purpose="register-env --force over the same image with another "
                  "--interpreter, when no method version records the image, "
                  "rewrites only image.json's interpreter: envs.json and "
                  "image.json name the new interpreter, packages.lock and the "
                  "Dockerfile are byte-identical, the register commit holds "
                  "the change and the image's interpreter answer is the new one")
def test_re_register_with_another_interpreter_rewrites_only_image_json(
        project, monkeypatch, tmp_path_factory):
    from wfc.environments import version_interpreter

    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    _fake_docker(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")

    口 = Step(step_num=1, name="Register the env with the first interpreter",
             purpose="No method is registered, so no version records the image")
    assert _cli_register_pixi("tools", lock, FIRST_PYTHON, force=False) == 0
    first = json.loads((project / ".wfc" / "envs.json").read_text())["envs"]["tools"]
    folder = project / ".wfc" / "captures" / "tools" / first["env_fingerprint"]
    before = _tree_bytes(folder)
    assert json.loads(before["image.json"])["interpreter"] == FIRST_PYTHON

    口 = Step(step_num=2, name="Re-register the same image with another interpreter",
             purpose="--force with --interpreter naming the second path")
    assert _cli_register_pixi("tools", lock, SECOND_PYTHON, force=True) == 0

    口 = Step(step_num=3, name="Only the interpreter changed, and it is committed",
             purpose="envs.json and image.json agree on the new interpreter; "
                     "every other file of the folder is byte-identical")
    record = json.loads((project / ".wfc" / "envs.json").read_text())["envs"]["tools"]
    assert record["env_fingerprint"] == first["env_fingerprint"]
    assert record["python"] == SECOND_PYTHON
    after = _tree_bytes(folder)
    assert set(after) == set(before)
    assert ({k: v for k, v in after.items() if k != "image.json"}
            == {k: v for k, v in before.items() if k != "image.json"})
    assert after["packages.lock"] == before["packages.lock"]
    assert after["Dockerfile"] == before["Dockerfile"]
    rewritten = json.loads(after["image.json"])
    assert rewritten == {**json.loads(before["image.json"]),
                         "interpreter": SECOND_PYTHON}
    fingerprint_dir = f".wfc/captures/tools/{first['env_fingerprint']}"
    committed = set(_git(project, "show", "--name-only", "--format=",
                         "HEAD").split())
    assert {".wfc/envs.json", f"{fingerprint_dir}/image.json"} <= committed
    assert _git(project, "status", "--porcelain").strip() == ""
    assert version_interpreter("tools", first["env_fingerprint"],
                               project) == SECOND_PYTHON


@workflow(purpose="register-env --force over the same image with another "
                  "--interpreter fails closed when a method version list cannot "
                  "be read: exit 1 naming the unreadable list; envs.json, the "
                  "captures folder and HEAD are byte-identical")
def test_re_register_with_another_interpreter_is_refused_on_an_unreadable_version_list(
        project, monkeypatch, capsys, tmp_path_factory):
    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    _fake_docker(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")

    口 = Step(step_num=1, name="Register the env, then damage a version list",
             purpose="A version list that is not JSON, committed, so wfc cannot "
                     "tell whether a method version records the image")
    assert _cli_register_pixi("tools", lock, FIRST_PYTHON, force=False) == 0
    broken = (project / ".wfc" / "registry" / "methods" / "data_transform"
              / "broken.versions.json")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")
    _git(project, "add", "--", broken.relative_to(project).as_posix())
    _git(project, "commit", "-q", "-m", "a damaged version list")
    manifest = project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
              _git(project, "rev-parse", "HEAD"))

    口 = Step(step_num=2, name="Re-register the same image with another interpreter",
             purpose="--force with --interpreter naming the second path")
    capsys.readouterr()
    rc = _cli_register_pixi("tools", lock, SECOND_PYTHON, force=True)
    err = capsys.readouterr().err

    口 = Step(step_num=3, name="Refused, naming the list, and nothing was written",
             purpose="The record, the folder and HEAD are as they were")
    assert rc == 1
    assert "broken.versions.json" in err
    assert (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
            _git(project, "rev-parse", "HEAD")) == before
    assert _git(project, "status", "--porcelain").strip() == ""


def _damage(folder: Path, damage: str) -> None:
    """Change one capture file outside wfc, as a user's hand edit would."""
    target = folder / ("image.json" if damage.startswith("image") else "packages.lock")
    if damage.endswith("missing"):
        target.unlink()
    elif damage == "image-unreadable":
        target.write_text("{not json", encoding="utf-8")
    else:
        target.write_bytes(target.read_bytes() + b"numpy==9.9.9\n")


@workflow(purpose="register-env --force over a captures folder that was "
                  "changed outside wfc (image.json deleted or unreadable, "
                  "packages.lock deleted or edited), uncommitted or committed, "
                  "with the same or another interpreter, is a refusal, not a "
                  "traceback: exit 1 naming the one plain remedy (register "
                  "under a new env name) and no git command; envs.json, the "
                  "captures folder, the working tree and HEAD are as they were")
@pytest.mark.parametrize(("damage", "committed", "interpreter"), [
    pytest.param("image-missing", False, SECOND_PYTHON, id="image-missing-other"),
    pytest.param("image-missing", False, FIRST_PYTHON, id="image-missing-same"),
    pytest.param("image-unreadable", False, SECOND_PYTHON,
                 id="image-unreadable-other"),
    pytest.param("image-unreadable", False, FIRST_PYTHON,
                 id="image-unreadable-same"),
    pytest.param("lock-missing", False, SECOND_PYTHON, id="lock-missing-other"),
    pytest.param("lock-missing", False, FIRST_PYTHON, id="lock-missing-same"),
    pytest.param("lock-edited", False, SECOND_PYTHON, id="lock-edited-other"),
    pytest.param("lock-edited", False, FIRST_PYTHON, id="lock-edited-same"),
    pytest.param("image-missing", True, SECOND_PYTHON,
                 id="committed-image-missing"),
    pytest.param("image-unreadable", True, FIRST_PYTHON,
                 id="committed-image-unreadable"),
    pytest.param("lock-missing", True, SECOND_PYTHON, id="committed-lock-missing"),
    pytest.param("lock-edited", True, FIRST_PYTHON, id="committed-lock-edited"),
])
def test_re_register_over_a_changed_captures_folder_is_refused(
        project, monkeypatch, capsys, tmp_path_factory, damage, committed,
        interpreter):
    from wfc.environments import capture_remedy

    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    _fake_docker(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")

    口 = Step(step_num=1, name="Register the env, then change a capture file",
             purpose="image.json deleted or not JSON, or packages.lock deleted "
                     "or edited; for the committed cases the change committed")
    assert _cli_register_pixi("tools", lock, FIRST_PYTHON, force=False) == 0
    record = json.loads((project / ".wfc" / "envs.json").read_text())["envs"]["tools"]
    fingerprint = record["env_fingerprint"]
    folder = project / ".wfc" / "captures" / "tools" / fingerprint
    relfolder = f".wfc/captures/tools/{fingerprint}"
    _damage(folder, damage)
    if committed:
        _git(project, "add", "-A", "--", relfolder)
        _git(project, "commit", "-q", "-m", f"{damage} by hand")
    manifest = project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
              _git(project, "rev-parse", "HEAD"),
              _git(project, "status", "--porcelain"))

    口 = Step(step_num=2, name="Re-register the same image with --force",
             purpose="The same lock, so the image's captures folder is the "
                     "changed one")
    capsys.readouterr()
    rc = _cli_register_pixi("tools", lock, interpreter, force=True)
    out = capsys.readouterr()

    口 = Step(step_num=3, name="Refused with the plain remedy, nothing written",
             purpose="exit 1, the remedy naming a new env name, no git command; "
                     "the record, the folder, the working tree and HEAD are as "
                     "they were")
    assert rc == 1
    assert out.err.startswith("ERROR: ")
    printed = (out.err + out.out).replace(str(project), "<project>")
    assert "Traceback" not in printed
    if damage == "lock-edited":
        # The folder's lock is not this registration's list: what was seen
        # is a differing list, not necessarily a change outside wfc.
        assert _lock_differs_lead("tools", fingerprint) in out.err
        assert "changed outside wfc" not in out.err
    else:
        assert capture_remedy("tools", fingerprint) in out.err
        assert (f"env 'tools''s capture files under {relfolder}/ were "
                f"changed outside wfc.") in out.err
    assert "`wfc register-env <new-name> ...`" in out.err
    assert "git" not in printed
    assert "--force" not in out.err
    assert "re-run" not in out.err
    assert (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
            _git(project, "rev-parse", "HEAD"),
            _git(project, "status", "--porcelain")) == before


def _lock_differs_lead(env: str, fingerprint: str) -> str:
    """The register refusal's lead for a folder whose lock is another list."""
    return (f"env {env!r} already has a capture of this image under "
            f".wfc/captures/{env}/{fingerprint}/, and its packages.lock differs "
            f"from the package list this registration produced. wfc doesn't "
            f"rewrite a capture folder.")


LOCK_A = "version: 6\n"
LOCK_B = "version: 6\nenvironments: {}\n"


@workflow(purpose="register-env over an image whose captures folder holds a "
                  "packages.lock other than this registration's list -- an "
                  "older image's folder whose lock was edited by hand "
                  "(uncommitted or committed) while another image is current, "
                  "or the same image registered from other list text -- is a "
                  "refusal: exit 1 saying the folder's packages.lock differs "
                  "from this registration's list, the new-env-name remedy, no "
                  "git command; envs.json, the captures, the working tree and "
                  "HEAD are as they were")
@pytest.mark.parametrize("case", [
    "older-folder-edited", "older-folder-edited-committed",
    "same-image-other-list"])
def test_re_register_over_a_folder_holding_another_list_is_refused(
        project, monkeypatch, capsys, tmp_path_factory, case):
    locks = tmp_path_factory.mktemp("lock")
    lock_a = locks / "a" / "pixi.lock"
    lock_b = locks / "b" / "pixi.lock"
    for path, text in ((lock_a, LOCK_A), (lock_b, LOCK_B)):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8", newline="\n")
    stub_readiness_probes(monkeypatch, docker="ok")

    口 = Step(step_num=1, name="Register the env so the image's folder holds "
                              "another list",
             purpose="Older folder: register from lock A (image 1), from lock "
                     "B (image 2), then edit image 1's packages.lock, the edit "
                     "committed or not. Same image: register from lock A; the "
                     "next registration brings lock B to the same image")
    _fake_docker(monkeypatch, layer="1")
    assert _cli_register_pixi("tools", lock_a, FIRST_PYTHON, force=False) == 0
    fingerprint = json.loads((project / ".wfc" / "envs.json").read_text(
        ))["envs"]["tools"]["env_fingerprint"]
    relfolder = f".wfc/captures/tools/{fingerprint}"
    again = lock_b
    if case.startswith("older-folder"):
        _fake_docker(monkeypatch, layer="2")
        assert _cli_register_pixi("tools", lock_b, FIRST_PYTHON, force=True) == 0
        _damage(project / relfolder, "lock-edited")
        if case.endswith("committed"):
            _git(project, "add", "-A", "--", relfolder)
            _git(project, "commit", "-q", "-m", "lock edited by hand")
        _fake_docker(monkeypatch, layer="1")
        again = lock_a
    manifest = project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
              _git(project, "rev-parse", "HEAD"),
              _git(project, "status", "--porcelain"))

    口 = Step(step_num=2, name="Register image 1 again",
             purpose="Its folder is kept as it is, so its packages.lock is not "
                     "this registration's list")
    capsys.readouterr()
    rc = _cli_register_pixi("tools", again, FIRST_PYTHON, force=True)
    out = capsys.readouterr()

    口 = Step(step_num=3, name="Refused, saying what it saw, nothing written",
             purpose="exit 1, the lock-differs lead and the new-env-name "
                     "remedy, no git command; the record, the captures, the "
                     "working tree and HEAD are as they were")
    assert rc == 1
    assert out.err.startswith("ERROR: ")
    printed = (out.err + out.out).replace(str(project), "<project>")
    assert "Traceback" not in printed
    assert _lock_differs_lead("tools", fingerprint) in out.err
    assert "`wfc register-env <new-name> ...`" in out.err
    assert "git" not in printed
    assert "--force" not in out.err
    assert "re-run" not in out.err
    assert (manifest.read_bytes(), _tree_bytes(project / ".wfc" / "captures"),
            _git(project, "rev-parse", "HEAD"),
            _git(project, "status", "--porcelain")) == before


def test_interpreter_refusal_offers_the_current_interpreter_and_names_the_default(
        project, monkeypatch):
    """A refused interpreter change offers keeping the recorded interpreter
    with ``--interpreter <current>``; when no interpreter was given, it says
    the requested one is only the backend's default."""
    from wfc.environments.interpreter import default_python_for_backend

    _fake_docker(monkeypatch)
    _register(project, "pixi", python_override=FIRST_PYTHON)

    with pytest.raises(ValueError) as exc:
        _register(project, "pixi", force=True,
                  recording_read_error="a version list cannot be read")

    msg = str(exc.value)
    assert f"--interpreter {FIRST_PYTHON}" in msg
    assert default_python_for_backend("pixi", "tools") in msg
    assert "--interpreter was not given" in msg


def test_refused_commit_restores_a_rewritten_image_json(project, monkeypatch):
    _fake_docker(monkeypatch)
    first = _register(project, "pixi", python_override=FIRST_PYTHON)
    folder = project / ".wfc" / "captures" / "tools" / first.env_fingerprint
    manifest = project / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(folder))
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)

    with pytest.raises(RuntimeError, match="Nothing was recorded"):
        _register(project, "pixi", force=True, python_override=SECOND_PYTHON)

    assert (manifest.read_bytes(), _tree_bytes(folder)) == before
    assert _git(project, "status", "--porcelain").strip() == ""


def test_failed_manifest_write_restores_a_rewritten_image_json(project, monkeypatch):
    _fake_docker(monkeypatch)
    first = _register(project, "pixi", python_override=FIRST_PYTHON)
    folder = project / ".wfc" / "captures" / "tools" / first.env_fingerprint
    before = _tree_bytes(folder)
    manifest = project / ".wfc" / "envs.json"

    def build_then_block_the_manifest(path, tag):
        # A directory where envs.json goes: the atomic replace fails with a
        # real OSError, after the build and the image.json rewrite.
        manifest.unlink()
        manifest.mkdir()

    stub_docker_build(monkeypatch, build_then_block_the_manifest)
    with pytest.raises(OSError):
        _register(project, "pixi", force=True, python_override=SECOND_PYTHON)

    manifest.rmdir()
    _git(project, "checkout", "--", ".wfc/envs.json")
    assert _tree_bytes(folder) == before
    assert _git(project, "status", "--porcelain").strip() == ""


@workflow(purpose="register-env --force over the same image with another "
                  "--interpreter is refused when a method version records the "
                  "image: exit 1 naming the version, the new-env-name route "
                  "and wfc register-method; envs.json, the captures folder and "
                  "HEAD are byte-identical; the version still runs under the "
                  "interpreter it recorded")
def test_re_register_with_another_interpreter_is_refused_when_a_version_runs_it(
        git_project, monkeypatch, capsys, tmp_path_factory):
    from tests.harness import Scenario, build_project, drive_target, node, selector, wire
    from tests.harness.project import method_source_dir
    from wfc import environments as envs_mod
    from wfc.registration import register_method, version_entry

    env = "image-io"
    口 = Step(step_num=1, name="Register env E with the first interpreter, and "
                             "method M on it",
             purpose="M's newest version records (E, F)")
    project = build_project(
        Scenario(nodes=[selector(), node("a", inputs=[wire("sel")])],
                 samples=["S1"], pipeline_id="pipe-fixed-interpreter",
                 env_name=env, env_backend="pixi"),
        root=git_project, monkeypatch=monkeypatch)
    root = project.root
    stub_docker_build(monkeypatch, None)
    stub_docker_image_content(monkeypatch,
                              lambda ref: {"layers": [f"layer-of-{ref}"]})
    stub_docker_image_inspect(monkeypatch, "sha256:" + "a" * 64)
    stub_readiness_probes(monkeypatch, docker="ok")
    # Both registrations come from the same lock: a registration whose
    # package list differs from the image's folder is refused on its own.
    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8", newline="\n")
    assert _cli_register_pixi(env, lock, FIRST_PYTHON, force=True) == 0
    record = envs_mod.get(env, root)
    spec = project.scenario.node_by_id("a")
    register_method(method_dir=method_source_dir(root, spec),
                    module_name=spec.module, assume_yes=True)
    version = version_entry(spec.module, spec.method_name)
    assert version.env_fingerprint == record.env_fingerprint
    reference = f"{spec.module}/{spec.method_name} v{version.label}"
    manifest = root / ".wfc" / "envs.json"
    before = (manifest.read_bytes(), _tree_bytes(root / ".wfc" / "captures"),
              _git(root, "rev-parse", "HEAD"))

    口 = Step(step_num=2, name="Re-register E over the same image with another "
                             "interpreter",
             purpose="The CLI reads the versions recording E and passes them in")
    capsys.readouterr()
    rc = _cli_register_pixi(env, lock, SECOND_PYTHON, force=True)
    err = capsys.readouterr().err

    口 = Step(step_num=3, name="Refused, and nothing was written",
             purpose="The message names the version and both commands of the "
                     "fix; the record, the folder and HEAD are as they were")
    assert rc == 1
    assert reference in err
    assert f"--interpreter {FIRST_PYTHON}" in err
    assert "wfc register-env <new-name>" in err
    assert "wfc register-method" in err
    assert (manifest.read_bytes(), _tree_bytes(root / ".wfc" / "captures"),
            _git(root, "rev-parse", "HEAD")) == before
    assert _git(root, "status", "--porcelain").strip() == ""

    口 = Step(step_num=4, name="The version still runs its own interpreter",
             purpose="The docker argv dispatch builds for M's version names the "
                     "first interpreter, not the second")
    document = json.loads(project.pipeline_json.read_text())
    next(n for n in document["nodes"] if n["id"] == "a")["version"] = version.identity
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    subprocess.run(["git", "commit", "-q", "-am", "pin a"], cwd=root,
                   check=True, capture_output=True)
    cmd = drive_target(project, "a", monkeypatch=monkeypatch).dispatch_cmd(
        ("a", "S1", "default"))
    assert FIRST_PYTHON in cmd
    assert SECOND_PYTHON not in cmd
