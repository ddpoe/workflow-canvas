"""Where your files live doesn't matter: wfc reads a file anywhere on disk.

A pipeline file, a draft's method folder and a sample's source file can sit
outside the project; the work is done against the one project the user is
in, or names with ``--project-root``. The cases already witnessed elsewhere
(check-pipeline on a file outside the project, a draft step run from a
folder outside it, register-method from a folder outside it) are listed in
the cycle's builder manifest; this file holds the rest.

Declared fixture deviations: none. Projects are built by the harness through
production registration; the engine process is the only thing stubbed
(``launching``), and the readiness and image probes say Docker is up.
"""
from __future__ import annotations

import json
from pathlib import Path

from axiom_annotations import Step, workflow

from tests.conftest import stub_readiness_probes
from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.fixtures.pipeline_verbs import docker_up_project, error_lines, launching
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID
from wfc import layout


def _pipeline_ids(root: Path) -> list[str]:
    """The pipeline ids recorded in *root*'s own database."""
    from sqlmodel import select

    from wfc.persistence import PipelineRun, get_session, use_project

    with use_project(root), get_session() as session:
        return [row.pipeline_id for row in session.exec(select(PipelineRun)).all()]


def _outside_any_project(tmp_path_factory, monkeypatch) -> Path:
    """A folder outside every project, made the current directory, nothing pinned."""
    from wfc.persistence import reset_engine

    elsewhere = tmp_path_factory.mktemp("downloads").resolve()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv(layout.ROOT_ENV_VAR, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engine()
    return elsewhere


@workflow(purpose="run-pipeline runs a pipeline file kept outside the project: "
                  "from inside the project, and from a folder outside every "
                  "project with --project-root; each run is recorded in the "
                  "project's database with its frozen document in the project")
def test_run_pipeline_runs_a_pipeline_file_kept_outside_the_project(
        cli, tmp_project, monkeypatch, tmp_path_factory):
    """Case (a): a pipeline file outside the project runs against the project."""
    from wfc.persistence import reset_engine

    project = docker_up_project(tmp_project, monkeypatch,
                                node("fit", inputs=[wire(SELECTOR_ID)]))
    outside = tmp_path_factory.mktemp("elsewhere").resolve() / "downloaded.json"
    outside.write_text(project.pipeline_json.read_text(encoding="utf-8"), encoding="utf-8")
    assert not outside.is_relative_to(Path(project.root).resolve())

    口 = Step(step_num=1, name="From inside the project",
             purpose="The file is outside; the project is the current folder's")
    result, launches = launching(cli, "run-pipeline", "--pipeline", str(outside))
    assert result.returncode == 0, result.stderr
    assert len(launches) == 1
    [first] = _pipeline_ids(project.root)
    assert layout.pipeline_doc_path(project.root, first).is_file()

    口 = Step(step_num=2, name="From a folder outside every project",
             purpose="--project-root names the project; the file stays outside")
    _outside_any_project(tmp_path_factory, monkeypatch)
    try:
        result, launches = launching(cli, "run-pipeline", "--pipeline", str(outside),
                                     "--project-root", str(project.root))
    finally:
        reset_engine()
    assert result.returncode == 0, result.stderr
    assert len(launches) == 1
    second = set(_pipeline_ids(project.root)) - {first}
    assert len(second) == 1, second
    assert layout.pipeline_doc_path(project.root, second.pop()).is_file()


@workflow(purpose="check-pipeline passes a document whose draft node's method "
                  "folder (method.yaml and script) lies outside the project, "
                  "reading the draft from that folder")
def test_check_pipeline_passes_a_draft_whose_folder_is_outside_the_project(
        cli, tmp_project, monkeypatch):
    """Case (c), the check: a draft folder outside the project is read and passes."""
    from wfc.registration import read_draft

    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")
    scenario = Scenario(nodes=[selector(), node("mid", inputs=[wire(SELECTOR_ID)])],
                        samples=["S1"], pipeline_id="drafted", name="drafted",
                        drafts=("mid",))
    mid = scenario.node_by_id("mid")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    folder = read_draft(mid.module, mid.method_name, project.root).folder.resolve()
    assert not folder.is_relative_to(Path(project.root).resolve()), folder
    assert (folder / "method.yaml").is_file()

    result = cli("check-pipeline", "--pipeline", str(project.pipeline_json), "--json")
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)
    assert verdict["valid"] and verdict["draftRefusals"] == [], verdict


@workflow(purpose="register-sample takes a source file outside the project, and "
                  "run-method runs a method on that sample from a folder outside "
                  "every project with --project-root, saving the one-step "
                  "document in the project and recording the run there")
def test_a_sample_registered_from_outside_the_project_runs_with_run_method(
        cli, tmp_project, monkeypatch, tmp_path_factory):
    """Cases (e) and (f): a sample from outside the project, run with run-method."""
    from wfc.persistence import reset_engine
    from wfc.registration import list_samples

    project = docker_up_project(tmp_project, monkeypatch,
                                node("fit", inputs=[wire(SELECTOR_ID)]))
    source = tmp_path_factory.mktemp("instrument").resolve() / "plate.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    assert not source.is_relative_to(Path(project.root).resolve())

    口 = Step(step_num=1, name="Register the outside file as a sample",
             purpose="From inside the project; the source stays where it is")
    result = cli("register-sample", "--name", "outside", "--source", str(source))
    assert result.returncode == 0, result.stderr
    assert "outside" in {s.name for s in list_samples(project.root)}
    assert source.read_text(encoding="utf-8") == "a,b\n1,2\n"

    口 = Step(step_num=2, name="Run fit on it from outside every project",
             purpose="--project-root names the project; the one-step document "
                     "is saved in its pipelines folder and the run is recorded "
                     "in its database")
    _outside_any_project(tmp_path_factory, monkeypatch)
    try:
        result, launches = launching(cli, "run-method",
                                     f"{DEFAULT_MODULE}.fit", "--sample", "outside",
                                     "--project-root", str(project.root))
    finally:
        reset_engine()
    assert result.returncode == 0, result.stderr
    assert error_lines(result.stderr) == []
    assert len(launches) == 1
    saved = layout.one_method_pipeline_path(project.root, DEFAULT_MODULE, "fit", "outside")
    assert saved.is_file() and str(saved) in result.stdout
    assert len(_pipeline_ids(project.root)) == 1
