"""
E2E Workflow: Project Initialization

Conservative core stories only:
1) Fresh project scaffold with usable database
2) ``--git`` initializes a repository
"""

from axiom_annotations import workflow, Step, AutoStep

from tests.fixtures.conftest import project_archive_dir
from wfc import layout
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
    assert layout.marker_path(tmp_path).exists()
    assert layout.db_path(tmp_path).exists()
    assert (tmp_path / ".gitignore").exists()
    assert created[f"{layout.STATE_DIR_NAME}/"] is True
    for absent in ("methods", "modules", "data", ".runs"):
        assert not (tmp_path / absent).exists(), absent

    口 = Step(step_num=3, name="Verify gitignore entries",
             purpose="The run archive and the sample workspace under .wfc/ are "
                     "excluded from version control")
    gitignore = (tmp_path / ".gitignore").read_text().splitlines()
    assert f"{layout.artifact_store_relpath()}/" in gitignore
    assert f"{layout.restore_workspace_relpath()}/" in gitignore


@workflow(
    purpose="A write in progress on the project database leaves git's tree "
            "clean: SQLite's sidecar file beside .wfc/wfc.db is ignored like "
            "the database itself"
)
def test_a_write_in_progress_on_the_database_leaves_the_tree_clean(tmp_path, monkeypatch):
    """SQLite's rollback journal, present while a write is open, is ignored."""
    import subprocess

    from sqlalchemy import text

    from wfc.persistence import get_session, use_project

    monkeypatch.chdir(tmp_path)

    口 = AutoStep(step_num=1)
    init_project(tmp_path, init_git=True,
                 archive=str(project_archive_dir(tmp_path)),
                 assume_yes=True)

    口 = Step(step_num=2, name="Hold a write transaction open",
             purpose="A session on the project's database writes and has not "
                     "committed, as the push worker's does while a run goes "
                     "on; SQLite keeps its rollback journal beside the database")
    with use_project(tmp_path), get_session() as session:
        # DDL commits at once; the INSERT opens the write transaction.
        session.execute(text("CREATE TABLE write_in_progress (x INTEGER)"))
        session.execute(text("INSERT INTO write_in_progress VALUES (1)"))
        journal = layout.db_path(tmp_path).with_name(
            layout.db_path(tmp_path).name + "-journal")
        assert journal.exists()

        口 = Step(step_num=3, name="git sees nothing",
                 purpose="git status lists no file, tracked or untracked")
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout
        assert status == ""
        session.rollback()


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
