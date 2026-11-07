"""One project per process: a run's files and its rows belong to the same project.

``--project-root`` binds the process to the project it names before anything
reads the database, so a run started from inside another project records in
the project it was told to use. Execution's entry points that take a root
refuse one that is not the process's project, so a library caller cannot
put one project's files beside another project's rows either.

Declared fixture deviations: none. Both projects are built by the harness
through production registration; the engine process is the only thing
stubbed (``launching``), and the readiness and image probes say Docker is up
(``docker_up_project``).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.pipeline_verbs import (
    docker_up_project,
    fit_document,
    launching,
    write_document,
)
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID
from wfc import layout


def _recorded(root: Path) -> tuple[list[str], int]:
    """The pipeline ids and the number of run rows in *root*'s own database."""
    from sqlmodel import select

    from wfc.persistence import PipelineRun, Run, get_session, use_project

    with use_project(root), get_session() as session:
        pipelines = [row.pipeline_id for row in session.exec(select(PipelineRun)).all()]
        runs = len(session.exec(select(Run)).all())
    return pipelines, runs


def _inside(project_dir: Path, monkeypatch) -> None:
    """Make *project_dir* the current folder, with no project or database pinned."""
    from wfc.persistence import reset_engine

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(layout.ROOT_ENV_VAR, raising=False)
    monkeypatch.chdir(project_dir)
    reset_engine()


@workflow(purpose="run-pipeline --project-root A, started from inside project B, "
                  "records its pipeline and its rows in A's database and none in "
                  "B's, and the engine's steps are handed project A")
def test_run_pipeline_with_project_root_records_in_that_project_from_inside_another(
        cli, tmp_project, monkeypatch, tmp_path_factory):
    """The run's files and rows are both project A's, though the cwd is in B."""
    from wfc.persistence import reset_engine

    口 = Step(step_num=1, name="Two projects with the same method",
             purpose="B is built first, then A; each registers fit and S1 in "
                     "its own database")
    other = build_project(Scenario(nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)])],
                                   samples=["S1"]),
                          root=tmp_path_factory.mktemp("project-b"), monkeypatch=monkeypatch)
    project = docker_up_project(tmp_project, monkeypatch,
                                node("fit", inputs=[wire(SELECTOR_ID)]))

    口 = Step(step_num=2, name="Run A's pipeline from inside B",
             purpose="No project pinned in the environment and no DATABASE_URL, "
                     "as at a user's terminal; --project-root names A")
    _inside(other.root, monkeypatch)
    try:
        result, launches = launching(cli, "run-pipeline", "--pipeline",
                                     str(project.pipeline_json),
                                     "--project-root", str(project.root))
    finally:
        reset_engine()
    assert result.returncode == 0, result.stderr
    assert len(launches) == 1

    口 = Step(step_num=3, name="A holds the run, B holds nothing",
             purpose="The launch's pipeline row and the end walk's rows are in "
                     "A's database; B's has no pipeline and no run; the engine's "
                     "environment names A")
    pipelines, runs = _recorded(project.root)
    assert len(pipelines) == 1 and runs >= 1, (pipelines, runs)
    assert _recorded(other.root) == ([], 0)
    assert layout.pipeline_run_dir(project.root, pipelines[0]).is_dir()
    engine_env = launches[0].kwargs["env"]
    assert Path(engine_env[layout.ROOT_ENV_VAR]).resolve() == Path(project.root).resolve()


def _entry_points(project, other: Path):
    """Each Execution entry point that takes a root, called with *other*."""
    from wfc.execution import run_one_method, run_pipeline, save_one_method_pipeline
    from wfc.execution.try_run import TryRequest, run_try
    from wfc.execution.validation import check_document, check_pipeline, document_verdict

    path = str(project.pipeline_json)
    document = {"name": "x", "nodes": [], "links": []}
    request = TryRequest(module=DEFAULT_MODULE, method="fit", folder=other, params={},
                         sample="S1", parent_entries=[])
    return {
        "run_pipeline": lambda: run_pipeline(path, project_root=str(other)),
        "run_one_method": lambda: run_one_method(f"{DEFAULT_MODULE}.fit", "S1", root=other),
        "save_one_method_pipeline": lambda: save_one_method_pipeline(
            f"{DEFAULT_MODULE}.fit", "S1", root=other),
        "check_pipeline": lambda: check_pipeline(path, other),
        "check_document": lambda: check_document(document, other),
        "document_verdict": lambda: document_verdict(document, other),
        "run_try": lambda: run_try(request, other),
    }


@pytest.mark.parametrize("entry", ["run_pipeline", "run_one_method",
                                   "save_one_method_pipeline", "check_pipeline",
                                   "check_document", "document_verdict", "run_try"])
def test_execution_refuses_a_root_that_is_not_the_process_s_project(
        entry, tmp_project, monkeypatch, tmp_path_factory):
    """A root naming another folder is refused naming both folders; nothing is written.

    The process is bound to the harness's project; nothing is saved, frozen
    or recorded in either folder.
    """
    from wfc.persistence import ProjectMismatchError

    project = docker_up_project(tmp_project, monkeypatch,
                                node("fit", inputs=[wire(SELECTOR_ID)]))
    other = tmp_path_factory.mktemp("other").resolve()
    before = sorted(p.relative_to(other) for p in other.rglob("*"))

    with pytest.raises(ProjectMismatchError) as refused:
        _entry_points(project, other)[entry]()
    message = str(refused.value)
    assert str(other.resolve()) in message and str(Path(project.root).resolve()) in message
    assert sorted(p.relative_to(other) for p in other.rglob("*")) == before
    assert _recorded(project.root)[0] == []
    assert not layout.one_method_pipeline_path(project.root, DEFAULT_MODULE, "fit",
                                               "S1").exists()


def test_execution_takes_the_process_s_project_however_its_root_is_spelled(
        tmp_project, monkeypatch, tmp_path):
    """The process's own root, spelled relative to the current folder, is the same project."""
    from wfc.execution.validation import check_pipeline

    project = docker_up_project(tmp_project, monkeypatch,
                                node("fit", inputs=[wire(SELECTOR_ID)]))
    path = write_document(tmp_path, "good", fit_document())
    monkeypatch.chdir(layout.db_path(project.root).parent)
    verdict = check_pipeline(path, "..")
    assert verdict["valid"], verdict["errors"]
