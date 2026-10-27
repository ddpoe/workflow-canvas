"""A method folder that does not clearly hold one method is refused before any write.

Three folder rules, Registration's, so ``wfc register-method`` and the
canvas refuse the same way: one ``method.yaml`` per method folder; more
than one recognised script needs ``script:`` naming the main one (every
other script is then a helper, and files that are not scripts are listed
as ignored); and a new version keeps its main script's file name. Every
registration runs the real CLI (``cli_main``) or ``plan_method_registration``
against a real git project.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.routes import init_test_project
from tests.fixtures.routes.registration import write_env_record

REPO = Path(__file__).resolve().parent.parent
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"
MODULE, METHOD = "mod", "transform"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def project(git_project, monkeypatch):
    """A ``wfc init`` project with its env committed and the module registered."""
    from wfc.persistence import reset_engine
    from wfc.registration import register_module

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    init_test_project(git_project)
    write_env_record(git_project, "fixture-env")
    _git(git_project, "add", ".wfc/envs.json", ".wfc/captures")
    _git(git_project, "commit", "-q", "-m", "user commits the env")
    reset_engine()
    register_module(name=MODULE, contracts=[])
    yield git_project
    reset_engine()


@pytest.fixture
def source(tmp_path_factory) -> Path:
    """The method's source directory, outside the repository."""
    dest = tmp_path_factory.mktemp("elsewhere") / METHOD
    shutil.copytree(FIXTURE_METHOD, dest)
    return dest


def _cli(source: Path, *flags: str) -> int:
    from wfc.cli import cli_main
    return cli_main(["register-method", "--yes", *flags, str(source),
                     "--module", MODULE])


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _add_script_key(source: Path, script: str) -> None:
    yaml_path = source / "method.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8") + f"script: {script}\n",
                         encoding="utf-8")


@workflow(purpose="A folder with a second method.yaml in a subfolder is refused "
                  "before any write, naming both files, by the CLI and the plan")
def test_a_second_method_yaml_is_refused_naming_both(project, source, capsys):
    from wfc.registration import plan_method_registration

    (source / "other").mkdir()
    shutil.copy2(source / "method.yaml", source / "other" / "method.yaml")
    before = _head(project)

    assert _cli(source) != 0
    err = capsys.readouterr().err
    assert "method.yaml" in err and "other/method.yaml" in err
    assert _head(project) == before

    with pytest.raises(ValueError) as refused:
        plan_method_registration(source, MODULE)
    assert "more than one method.yaml" in str(refused.value)
    assert "other/method.yaml" in str(refused.value)


@workflow(purpose="A folder with three scripts and no script: is refused, listing "
                  "them and the line to add; with script: the others register as "
                  "helpers and the files that are not scripts are listed as ignored")
def test_more_than_one_script_needs_script_key(project, source, capsys):
    from wfc.registration import plan_method_registration

    (source / "util.py").write_text("X = 1\n", encoding="utf-8")
    (source / "lib").mkdir()
    (source / "lib" / "more.py").write_text("Y = 2\n", encoding="utf-8")
    (source / "notes.md").write_text("notes\n", encoding="utf-8")
    before = _head(project)

    assert _cli(source) != 0
    err = capsys.readouterr().err
    for name in ("transform.py", "util.py", "lib/more.py"):
        assert name in err
    assert "script: transform.py" in err
    assert "the next registration adds a version" in " ".join(err.split())
    assert _head(project) == before

    _add_script_key(source, "transform.py")
    plan = plan_method_registration(source, MODULE)
    assert plan.holds == {"script": "transform.py",
                          "helpers": ["lib/more.py", "util.py"],
                          "ignored": ["notes.md"]}
    assert _cli(source) == 0


def test_cli_script_flag_names_the_main_script(project, source):
    (source / "util.py").write_text("X = 1\n", encoding="utf-8")
    assert _cli(source, "--script", "transform.py") == 0


@workflow(purpose="Registering an existing method from a folder whose main script "
                  "has another file name is refused before any write, naming both "
                  "scripts; the same file name registers the new version")
def test_a_new_version_keeps_its_script_file_name(project, source, tmp_path, capsys):
    assert _cli(source) == 0
    capsys.readouterr()
    before = _head(project)

    renamed = tmp_path / METHOD
    shutil.copytree(source, renamed)
    (renamed / "transform.py").rename(renamed / "main.py")
    _add_script_key(renamed, "main.py")
    assert _cli(renamed) != 0
    err = capsys.readouterr().err
    assert "main.py" in err and "transform.py" in err
    assert _head(project) == before

    script = source / "transform.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# v2\n", encoding="utf-8")
    assert _cli(source) == 0
    assert "this adds v2" in capsys.readouterr().out
