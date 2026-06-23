"""
E2E Workflow: Project Initialization

Conservative core stories only:
1) Fresh project scaffold with usable database
2) Config override for external database URL
"""

from dflow.core.decorators import workflow, Step, AutoStep

from wfc.init import init_project, read_config


@workflow(
    purpose="Scaffold a new wfc project from scratch with wfc init"
)
def test_init_creates_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    口 = AutoStep(step_num=1)
    created = init_project(tmp_path, init_git=True)

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
    created = init_project(tmp_path, init_git=True)

    口 = Step(step_num=2, name="Verify git repo created",
             purpose="The project directory is now a git repository")
    assert (tmp_path / ".git").is_dir()
    assert created[".git/"] is True

    口 = Step(step_num=3, name="Verify no warning printed",
             purpose="No warning shown when git init succeeds")
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


@workflow(
    purpose="Override database to Postgres via config and verify config read"
)
def test_config_postgres_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    口 = AutoStep(step_num=1)
    init_project(tmp_path, init_git=True)

    口 = Step(step_num=2, name="Override config URL",
             purpose="Set wf-canvas.toml database URL to a Postgres connection string")
    config_path = tmp_path / ".wfc" / "wf-canvas.toml"
    config_path.write_text(
        '[database]\nurl = "postgresql://user:pass@localhost/test_db"\n\n'
        '[project]\nname = "override_test"\n'
    )

    口 = AutoStep(step_num=3)
    config = read_config(tmp_path)

    口 = Step(step_num=4, name="Verify override",
             purpose="Reader returns the overridden Postgres URL and project name")
    assert "postgresql" in config["database_url"]
    assert config["project_name"] == "override_test"
