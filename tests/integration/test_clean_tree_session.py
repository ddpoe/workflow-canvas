"""A clean git tree across a whole wfc session, over real git, DVC and Docker.

Every stage goes through the ``wfc`` verbs a user types (``run_cli`` runs the
CLI entry point in-process): ``init``, ``register-env``, ``register-module``,
``register-method``, ``register-sample``, ``restore-sample`` and
``run-pipeline``. After each one ``git status --porcelain`` is read back:
everything wfc created is either committed by wfc or ignored by the
``.gitignore`` init wrote. The user then makes the obvious commit
(``git add -A``) and runs again, and the run gate does not refuse it.

The unit-level tests in ``tests/test_clean_tree.py`` stand the env record in
for a Docker build; the ``register-env`` commit is covered here, where the
byo registration inspects and digest-pins a real image.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.fixtures.conftest import build_pipeline_json
from tests.fixtures.routes import project_archive_dir, run_cli, sample_source_dir

pytestmark = [pytest.mark.integration, requires_docker]

REPO = Path(__file__).resolve().parents[2]
FIXTURE_METHOD = REPO / "tests" / "fixtures" / "methods" / "transform"

#: The env the transform fixture method's ``method.yaml`` names.
ENV_NAME = "fixture-env"
#: The minimal wfc image the ``minimal_image`` fixture builds and tags.
IMAGE_REF = "docker://local/wfc-test-minimal:latest"


def _git(root: Path, *args: str) -> str:
    """Run git in *root* and return stdout (raises on failure)."""
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout


def _porcelain(root: Path) -> str:
    return _git(root, "status", "--porcelain", "--untracked-files=all").strip()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _head_files(root: Path) -> set[str]:
    out = _git(root, "show", "--name-only", "--pretty=format:", "HEAD")
    return {ln for ln in out.splitlines() if ln}


def _ok(result, verb: str) -> None:
    assert result.returncode == 0, (
        f"wfc {verb} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.fixture
def session_project(git_project, monkeypatch):
    """A git repository with no wfc in it, the process pinned to it.

    ``git_project`` is a repository with one commit and a local identity;
    the tests run ``wfc init`` themselves because init is part of the
    session under test.

    Yields:
        The project root, not yet initialised.
    """
    from wfc.persistence import reset_engine

    monkeypatch.chdir(git_project)
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(git_project))
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///{git_project / '.wfc' / 'wfc.db'}")
    # A standalone registration pushes synchronously, as a user's does.
    monkeypatch.delenv("WFC_PIPELINE_ID", raising=False)
    reset_engine()
    yield git_project
    reset_engine()


def _init(project: Path) -> None:
    """``wfc init --archive <sibling> --yes``; the archive stays out of home."""
    from wfc.persistence import reset_engine

    _ok(run_cli("init", "--dir", str(project),
                "--archive", str(project_archive_dir(project)), "--yes"), "init")
    reset_engine()


@workflow(purpose="register-env over a real image commits .wfc/envs.json by "
                  "itself, and the tree is clean afterwards")
def test_register_env_commits_the_manifest(session_project, minimal_image):
    project = session_project
    _init(project)
    assert _porcelain(project) == ""
    head = _head(project)

    _ok(run_cli("register-env", ENV_NAME, "--backend", "byo",
                "--image", IMAGE_REF), "register-env")

    assert _head(project) != head
    assert _head_files(project) == {".wfc/envs.json"}
    record = json.loads((project / ".wfc" / "envs.json").read_text())
    assert "@sha256:" in record["envs"][ENV_NAME]["container"]
    assert _porcelain(project) == ""


@workflow(
    purpose="A whole session -- init, register an env, a module, a method and "
            "a sample, restore the sample, run a pipeline -- leaves nothing wfc "
            "created in git status; after the user commits with git add -A "
            "the commit holds only the user's file and a second run is not "
            "refused",
    inputs="a git repository, the transform fixture method kept outside it, "
           "a CSV kept outside it, and the minimal wfc image",
    outputs="an empty git status after every verb, and two completed runs",
)
def test_a_whole_session_leaves_a_clean_tree(session_project, minimal_image,
                                             tmp_path_factory):
    project = session_project

    口 = Step(step_num=1, name="Initialise the project",
             purpose="init commits its four files and ignores the paths a run "
                     "will write")
    _init(project)
    assert _porcelain(project) == ""

    口 = Step(step_num=2, name="Register the environment",
             purpose="The byo registration digest-pins the image and commits "
                     "the manifest")
    _ok(run_cli("register-env", ENV_NAME, "--backend", "byo",
                "--image", IMAGE_REF), "register-env")
    assert _porcelain(project) == ""

    口 = Step(step_num=3, name="Register a module from a module.yaml the user wrote",
             purpose="register-module commits the in-repository file it read")
    module_dir = project / "modules" / "test_pipeline"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("description: session\ncontracts: []\n")
    _ok(run_cli("register-module", "--name", "test_pipeline",
                "--module-dir", str(module_dir)), "register-module")
    assert _porcelain(project) == ""

    口 = Step(step_num=4, name="Register a method kept outside the repository",
             purpose="The registration snapshot under methods/ is committed")
    source = tmp_path_factory.mktemp("elsewhere") / "transform"
    shutil.copytree(FIXTURE_METHOD, source)
    _ok(run_cli("register-method", str(source), "--module", "test_pipeline"),
        "register-method")
    assert (project / "methods" / "transform" / "transform.py").exists()
    assert _porcelain(project) == ""

    口 = Step(step_num=5, name="Register a sample and restore it",
             purpose="The DVC cache entry and the restored copy under data/ are "
                     "both ignored")
    csv_source = sample_source_dir(project) / "s1" / "data.csv"
    csv_source.parent.mkdir(parents=True, exist_ok=True)
    csv_source.write_text("id,value\n1,10\n2,20\n")
    _ok(run_cli("register-sample", "--name", "s1", "--source", str(csv_source)),
        "register-sample")
    _ok(run_cli("restore-sample", "--name", "s1"), "restore-sample")
    assert any((project / "data").rglob("data.csv"))
    assert _porcelain(project) == ""

    口 = Step(step_num=6, name="The user writes a pipeline document and commits it",
             purpose="The document is the user's file, not wfc's; committing it "
                     "keeps the tree clean before the run")
    pipeline = build_pipeline_json(
        project, "session",
        nodes=[
            {"id": "sel", "type": "input_selector", "samples": ["s1"]},
            {"id": "t1", "method": "transform", "module": "test_pipeline"},
        ],
        links=[{"source": "sel", "target": "t1"}],
        samples=[],
    )
    _git(project, "add", pipeline.name)
    _git(project, "commit", "-q", "-m", "user adds a pipeline")
    assert _porcelain(project) == ""

    口 = Step(step_num=7, name="Run the pipeline",
             purpose="Everything the run writes -- .runs/, .snakemake/, the "
                     "database, the DVC cache and the archive pass -- is ignored")
    _ok(run_cli("run-pipeline", "--pipeline", str(pipeline),
                "--project-root", str(project), "--cores", "1"), "run-pipeline")
    assert any((project / ".runs").rglob("output.csv"))
    assert _porcelain(project) == ""

    口 = Step(step_num=8, name="The user commits with git add -A",
             purpose="The obvious commit sweeps in only the user's own file",
             critical="A wfc-created path that is neither committed nor ignored "
                      "would land in this commit")
    (project / "NOTES.md").write_text("first run done\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "user notes")
    assert _head_files(project) == {"NOTES.md"}

    口 = Step(step_num=9, name="Run again",
             purpose="Committing between runs does not block the next run")
    _ok(run_cli("run-pipeline", "--pipeline", str(pipeline),
                "--project-root", str(project), "--cores", "1"), "run-pipeline")
    assert _porcelain(project) == ""
