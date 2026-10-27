"""``wfc register-method`` says what it will do and asks before it adds a version.

Every registration runs the real CLI (``cli_main``) against a real git
project. Registration plans before it writes: one of three messages
(registers v1, adds vN naming what changed, matches the newest version),
then ``Proceed? [y/N]`` for a new method or a new version. ``--yes`` skips
the question; with no terminal and no ``--yes`` the registration is refused
having changed nothing. pytest's stdin is not a terminal; the two answers
typed at a terminal ("n" and an empty answer) go through the registered
``stub_terminal`` / ``stub_interactive_prompt`` fakes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_interactive_prompt, stub_terminal
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
    return cli_main(["register-method", *flags, str(source), "--module", MODULE])


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _state(root: Path) -> tuple:
    """Everything a registration writes: HEAD, the list, the snapshot, the rows."""
    from sqlmodel import select

    from wfc import layout
    from wfc.identity import fingerprinted_file_digests
    from wfc.persistence import Method, MethodVersion, get_session

    list_path = layout.method_versions_path(root, MODULE, METHOD)
    snapshot = layout.method_dir(root, MODULE, METHOD)
    with get_session() as s:
        rows = [(m.name, m.script_path) for m in s.exec(select(Method)).all()]
        versions = [v.label for v in s.exec(select(MethodVersion)).all()]
    return (_git(root, "rev-parse", "HEAD").strip(),
            list_path.read_bytes() if list_path.is_file() else None,
            fingerprinted_file_digests(snapshot) if snapshot.is_dir() else None,
            (snapshot / "method.yaml").read_bytes() if snapshot.is_dir() else None,
            rows, versions)


@workflow(purpose="Through the CLI with --yes, a new method, then a changed "
                  "script and parameter, then the same content print, in order, "
                  "'registers it as v1', 'already registered (newest v1); this "
                  "adds v2' naming the changed file and the params change, and "
                  "'matches its newest version v2; no version is added'")
def test_register_method_states_each_outcome_before_it_writes(project, source, capsys):
    口 = Step(step_num=1, name="Register a new method",
             purpose="The new outcome's message prints and v1 is registered")
    assert _cli(source, "--yes") == 0
    out = capsys.readouterr().out
    assert f"{MODULE}/{METHOD} is not registered yet; this registers it as v1." in out

    口 = Step(step_num=2, name="Change one code file and one parameter, register again",
             purpose="The changed outcome names the file and the params change")
    script = source / f"{METHOD}.py"
    script.write_text(script.read_text() + "\n# v2\n")
    _edit(source / "method.yaml", 'default: "_transformed"', 'default: "_t2"')
    assert _cli(source, "--yes") == 0
    out = capsys.readouterr().out
    assert (f"{MODULE}/{METHOD} is already registered (newest v1); this adds v2."
            in out)
    assert f"Code files changed since v1: {METHOD}.py" in out
    assert "Params/contract (method.yaml) changed since v1: params: suffix changed" in out
    assert out.index("this adds v2") < out.index("committed as v2")

    口 = Step(step_num=3, name="Register the same content again",
             purpose="The same outcome: nothing added, nothing asked")
    assert _cli(source, "--yes") == 0
    out = capsys.readouterr().out
    assert (f"{MODULE}/{METHOD} matches its newest version v2; no version is added."
            in out)
    assert "Proceed?" not in out
    assert [r for r in _state(project)[5]] == [1, 2]


@pytest.mark.parametrize("answer", ["n", ""], ids=["no", "empty"])
@workflow(purpose="Answering no (or nothing) at Proceed? [y/N] for a change "
                  "that would add a version exits non-zero with 'Nothing was "
                  "registered.' and leaves the snapshot, the version list, git "
                  "HEAD and the database rows as they were")
def test_answering_no_registers_nothing(project, source, capsys, monkeypatch, answer):
    assert _cli(source, "--yes") == 0
    script = source / f"{METHOD}.py"
    script.write_text(script.read_text() + "\n# would be v2\n")
    before = _state(project)
    capsys.readouterr()

    口 = Step(step_num=1, name="Answer at the terminal",
             purpose="The terminal and the answer are the only substitutions")
    stub_terminal(monkeypatch, True)
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, answer, prompts=prompts)
    assert _cli(source) != 0

    口 = Step(step_num=2, name="Nothing changed",
             purpose="The message and the question were shown; nothing written")
    out = capsys.readouterr().out
    assert "this adds v2" in out
    assert prompts == ["Proceed? [y/N]: "]
    assert "Nothing was registered." in out
    assert _state(project) == before


@workflow(purpose="--yes with input that would answer no prints the message, "
                  "shows no question and adds the version")
def test_yes_skips_the_question_and_still_prints(project, source, capsys, monkeypatch):
    prompts: list[str] = []
    stub_interactive_prompt(monkeypatch, "n", prompts=prompts)
    assert _cli(source, "--yes") == 0
    out = capsys.readouterr().out
    assert "registers it as v1" in out
    assert prompts == []
    assert _state(project)[5] == [1]


@workflow(purpose="With no terminal and no --yes, a new version is refused "
                  "naming --yes, exit non-zero, nothing changed; the same-as-newest "
                  "outcome under the same conditions prints its message and exits 0")
def test_no_terminal_without_yes_refuses_a_new_version_only(project, source, capsys):
    assert _cli(source, "--yes") == 0
    script = source / f"{METHOD}.py"
    original = script.read_text()
    script.write_text(original + "\n# would be v2\n")
    before = _state(project)
    capsys.readouterr()

    口 = Step(step_num=1, name="A new version, no terminal, no --yes",
             purpose="Refused before anything is written")
    assert _cli(source) == 1
    captured = capsys.readouterr()
    assert "this adds v2" in captured.out
    assert "--yes" in captured.err
    assert "Nothing was registered" in captured.err
    assert _state(project) == before

    口 = Step(step_num=2, name="The newest content, no terminal, no --yes",
             purpose="Nothing to confirm: the message prints and it exits 0")
    script.write_text(original)
    assert _cli(source) == 0
    assert "matches its newest version v1; no version is added." in capsys.readouterr().out
    assert _state(project)[5] == [1]


@workflow(purpose="Re-registering after a description-only method.yaml edit "
                  "says the edit is not kept, asks nothing, and the snapshot keeps "
                  "the committed method.yaml")
def test_a_description_only_edit_is_named_as_not_kept(project, source, capsys):
    assert _cli(source, "--yes") == 0
    committed_yaml = _state(project)[3]
    _edit(source / "method.yaml", '"Input CSV to transform"', '"The CSV to read"')
    capsys.readouterr()

    assert _cli(source) == 0
    out = capsys.readouterr().out
    assert "matches its newest version v1; no version is added." in out
    assert "method.yaml edits change nothing a run uses" in out
    assert "not kept" in out
    assert "Proceed?" not in out
    assert _state(project)[3] == committed_yaml


def test_plan_token_names_the_method():
    """Two plans that differ only in their module or method name write onto
    different version lists, so their tokens differ; the same plan gives the
    same token."""
    from wfc.registration.method import plan_token

    same = ("code-fp", "env-fp", 1)
    token = plan_token("mod", "transform", *same)
    assert plan_token("mod", "transform", *same) == token
    assert plan_token("mod", "filter", *same) != token
    assert plan_token("other", "transform", *same) != token
