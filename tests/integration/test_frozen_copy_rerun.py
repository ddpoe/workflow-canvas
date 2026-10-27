"""Tier 3 integration test: rerunning a frozen copy after a newer version.

A pipeline whose method node names no version runs the newest version, and
``wfc run-pipeline`` says so. The frozen ``pipeline.json`` it writes records
the version every node ran, while ``pipeline.editable.json`` keeps the
document as submitted. After a newer version is registered, running the frozen
copy again runs the version it recorded under the same key and prints no
version line; the unpinned document now runs the newer version under another
key.

Every step goes through production: ``git init`` and ``wfc init``, the CLI's
``register-env`` (byo, a locally built image), production method registration,
the canvas enrichment for the pipeline document, and ``wfc run-pipeline`` as a
subprocess.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    init_test_project,
    register_sample_row,
    register_test_method,
    sample_source_dir,
)
from tests.integration.test_pinned_method_version_runs import (
    _METHOD_SOURCE,
    _METHOD_YAML,
    ENV,
    METHOD,
    MODULE,
    _build_image,
    _commit,
    _git,
    _run,
    _stamp_runs,
    _version_row_id,
    _wfc,
)
from wfc import layout

pytestmark = [pytest.mark.integration, requires_docker]

_VERSION_LINE = f"[run_pipeline] node 'st' ({MODULE}/{METHOD}) is not pinned"


def _write_unpinned_pipeline(proj: Path) -> Path:
    """Write the canvas-enriched document with the method node's pin removed."""
    from wfc.canvas.models import PipelineInput, PipelineLink, PipelineNode
    from wfc.canvas.submission import _enrich_pipeline

    enriched = _enrich_pipeline(PipelineInput(
        name="frozen-rerun",
        nodes=[
            PipelineNode(id="sel", type="input_selector", samples=["s1"]),
            PipelineNode(id="st", type="method", method=METHOD, module=MODULE),
        ],
        links=[PipelineLink(source="sel", target="st", targetHandle="trigger")],
        samples=["s1"],
    ))
    node = next(n for n in enriched["nodes"] if n["id"] == "st")
    node.pop("version", None)
    path = proj / "pipeline.json"
    path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    _commit(proj, "pipeline: unpinned")
    return path


def _run_document(proj: Path, env: dict, document: Path):
    return _wfc(proj, env, "run-pipeline", "--pipeline", str(document),
                "--project-root", str(proj), "--no-archive", "--cores", "1")


def _pipeline_ids(proj: Path) -> set[str]:
    root = layout.pipelines_dir(proj)
    return {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()


def _method_node(document: Path) -> dict:
    data = json.loads(document.read_text(encoding="utf-8"))
    return next(n for n in data["nodes"] if n["id"] == "st")


@workflow(purpose="A pipeline run with an unpinned method node freezes the "
                  "version it ran: after a newer version is registered, "
                  "rerunning the frozen pipeline.json runs the recorded "
                  "version under the same key and names no resolution, while "
                  "the unpinned document runs the newer version under another "
                  "key")
def test_rerunning_the_frozen_copy_after_a_newer_version_gives_the_same_keys(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    from wfc.registration.method_version import read_version_list

    tag = f"local/wfc-test-frozen-{uuid.uuid4().hex[:8]}:one"
    image_ids: list[str] = []
    proj = tmp_path / "proj"
    proj.mkdir()
    try:
        口 = Step(step_num=1, name="Make the project, register the env and v1",
                 purpose="git init, wfc init, `wfc register-env --backend byo` "
                         "of a locally built image, then one registration of "
                         "the method")
        _git(proj, "init")
        _git(proj, "config", "user.email", "wfc@wfc")
        _git(proj, "config", "user.name", "wfc")
        _git(proj, "config", "core.autocrlf", "false")
        _git(proj, "config", "core.fileMode", "false")
        (proj / ".gitattributes").write_text("* -text\n", encoding="utf-8")
        monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")
        init_test_project(proj)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        image_ids.append(_build_image(tmp_path / "image", tag, "frozen"))
        registered = _wfc(proj, env, "register-env", ENV, "--backend", "byo",
                          "--image", f"docker://{tag}")
        assert registered.returncode == 0, registered.stderr

        source = sample_source_dir(proj) / "_methods" / METHOD
        source.mkdir(parents=True)
        (source / "method.yaml").write_text(_METHOD_YAML, encoding="utf-8")
        (source / f"{METHOD}.py").write_text(_METHOD_SOURCE.format(mark="one"),
                                             encoding="utf-8")
        register_test_method(project_dir=proj, module_name=MODULE,
                             method_dir=source, method_name=METHOD)
        (v1,) = read_version_list(proj, MODULE, METHOD)

        sample = sample_source_dir(proj) / "s1"
        sample.mkdir(parents=True)
        (sample / "trigger.txt").write_text("trigger", encoding="utf-8")
        register_sample_row(proj, "s1", sample / "trigger.txt")

        口 = Step(step_num=2, name="Run the unpinned document",
                 purpose="v1, the newest, runs; run-pipeline names the node, "
                         "its module/method and v1's label; the frozen copy "
                         "pins the node to v1 and the editable copy keeps it "
                         "unpinned")
        document = _write_unpinned_pipeline(proj)
        assert "version" not in _method_node(document)
        first_run = _run_document(proj, env, document)
        assert first_run.returncode == 0, f"{first_run.stdout}\n{first_run.stderr}"
        assert (f"{_VERSION_LINE}: runs v{v1.label}, the newest version"
                in first_run.stdout)
        (first,) = _stamp_runs()
        assert first.status == "completed", first.error_message
        assert first.version_id == _version_row_id(v1)
        (pipeline_id,) = _pipeline_ids(proj)
        frozen = layout.pipeline_doc_path(proj, pipeline_id)
        assert _method_node(frozen)["version"] == v1.identity
        editable = layout.pipeline_editable_doc_path(proj, pipeline_id)
        assert "version" not in _method_node(editable)

        口 = Step(step_num=3, name="Register a newer version",
                 purpose="The edited script registers as v2")
        (source / f"{METHOD}.py").write_text(_METHOD_SOURCE.format(mark="two"),
                                             encoding="utf-8")
        register_test_method(project_dir=proj, module_name=MODULE,
                             method_dir=source, method_name=METHOD)
        _, v2 = read_version_list(proj, MODULE, METHOD)
        assert v2.identity != v1.identity

        口 = Step(step_num=4, name="Rerun the frozen copy",
                 purpose="v1 runs again under the first run's key, and no "
                         "version line is printed: every node is pinned")
        rerun = _run_document(proj, env, frozen)
        assert rerun.returncode == 0, f"{rerun.stdout}\n{rerun.stderr}"
        assert _VERSION_LINE not in rerun.stdout
        runs = _stamp_runs()
        assert {run.cache_key for run in runs} == {first.cache_key}
        assert {run.version_id for run in runs} == {_version_row_id(v1)}

        口 = Step(step_num=5, name="Rerun the unpinned document",
                 purpose="Contrast: the unpinned document now runs v2 under "
                         "another key and names v2's label")
        newest = _run_document(proj, env, document)
        assert newest.returncode == 0, f"{newest.stdout}\n{newest.stderr}"
        assert (f"{_VERSION_LINE}: runs v{v2.label}, the newest version"
                in newest.stdout)
        latest = _stamp_runs()[-1]
        assert latest.status == "completed", latest.error_message
        assert latest.version_id == _version_row_id(v2)
        assert latest.cache_key != first.cache_key
    finally:
        for ref in (tag, *image_ids):
            _run(["docker", "rmi", "-f", ref], tmp_path, timeout=120)
