"""
E2E Workflow: Project Initialization

Conservative core stories only:
1) Fresh project scaffold with usable database
2) ``--git`` initializes a repository
"""

from axiom_annotations import workflow, Step, AutoStep

from tests.fixtures.conftest import project_archive_dir
from wfc.init import init_project


@workflow(
    purpose="Scaffold a new wfc project from scratch with wfc init"
)
def test_init_creates_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    口 = AutoStep(step_num=1)
    # The archive is named so the default ~/.wfc/archives/<project> is never
    # resolved and init_dvc pre-creates nothing in the developer's home.
    created = init_project(tmp_path, init_git=True,
                           archive=str(project_archive_dir(tmp_path)),
                           assume_yes=True)

    口 = Step(step_num=2, name="Verify scaffold structure",
             purpose="Expected project directories and files are created")
    assert (tmp_path / ".wfc" / "wf-canvas.toml").exists()
    assert (tmp_path / ".wfc" / "wfc.db").exists()
    assert (tmp_path / "methods").is_dir()
    assert (tmp_path / ".runs").is_dir()
    assert (tmp_path / "data" / "samples").is_dir()
    assert (tmp_path / ".gitignore").exists()
    assert created[".wfc/"] is True
    assert created["methods/"] is True
    assert created[".runs/"] is True
    assert created["data/samples/"] is True

    口 = Step(step_num=3, name="Verify gitignore entries",
             purpose="Data directory and runs directory are excluded from version control")
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "data/" in gitignore
    assert ".runs/" in gitignore


@workflow(
    purpose="wfc init --git initializes a git repo when none exists"
)
def test_init_git_flag_initializes_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    口 = AutoStep(step_num=1)
    created = init_project(tmp_path, init_git=True,
                           archive=str(project_archive_dir(tmp_path)),
                           assume_yes=True)

    口 = Step(step_num=2, name="Verify git repo created",
             purpose="The project directory is now a git repository")
    assert (tmp_path / ".git").is_dir()
    assert created[".git/"] is True

    口 = Step(step_num=3, name="Verify no warning printed",
             purpose="No warning shown when git init succeeds")
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out
