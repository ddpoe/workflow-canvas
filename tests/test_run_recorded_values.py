"""What a run records it ran with: its commit, env, wfc version and document.

The claim writes the commit the run ran at, the claimed version's env name,
the installed wfc version and the pipeline document's hash in the run row's
own INSERT, on a new run and on a cache-hit audit row alike. The launch
keeps the frozen pipeline document in the cache by its canonical-JSON hash
before the engine starts, and the pipeline-end walk stamps that hash on the
failed (claim-refused) and cancelled rows it writes, which leave the three
per-run values blank.

Declared fixture deviations: none. Projects are built by the harness through
production registration; runs are claimed by production ``run_step`` /
``pre_run``; the engine process is the only thing stubbed
(``mocked_snakemake``, ``fake_engine_process``).
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import (
    stub_distribution_metadata_missing,
    stub_docker_image_content,
    stub_docker_image_inspect,
)
from tests.fixtures.routes import claimed_run, completed_run
from tests.harness import (
    Scenario,
    build_project,
    drive_target,
    node,
    run_scenario,
    selector,
    wire,
)
from wfc import layout
from wfc.identity import canonical_json_bytes, canonical_json_hash
from wfc.persistence import Blob, Method, Run, get_session

ENV_NAME = "image-io"
OTHER_ENV = "other-env"
OTHER_DIGEST = "e" * 64


def _head(root: Path) -> str:
    """The repository's HEAD commit."""
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit_file(root: Path, name: str, text: str) -> None:
    """Commit one new file, so HEAD moves and the tree stays clean."""
    (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {name}"], cwd=root,
                   check=True, capture_output=True)


def _run(run_id: int) -> Run:
    with get_session() as session:
        row = session.get(Run, run_id)
        session.expunge(row)
        return row


def _pipeline_rows(pipeline_id: str) -> list[Run]:
    with get_session() as session:
        rows = session.exec(select(Run).where(Run.pipeline_id == pipeline_id)
                            .order_by(Run.id)).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _document_blobs() -> list[Blob]:
    with get_session() as session:
        rows = session.exec(select(Blob).where(Blob.kind == "pipeline-document")).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _document_hash(path: Path) -> str:
    """The canonical-JSON hash of a pipeline document file."""
    return canonical_json_hash(json.loads(Path(path).read_text(encoding="utf-8")))


def _assert_document_kept(root: Path, path: Path) -> str:
    """The document's canonical bytes are cached under its hash, with a blob row."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = canonical_json_hash(document)
    entry = layout.dvc_cache_entry(root, digest)
    assert entry.is_file(), f"document {digest} is not in the cache"
    assert entry.read_bytes() == canonical_json_bytes(document)
    assert digest in {b.hash for b in _document_blobs()}
    return digest


# =============================================================================
# The commit, the env name and the wfc version
# =============================================================================


def _register_other_env(root: Path, monkeypatch) -> None:
    """Register a second, byo env through production (its commit included)."""
    from wfc import environments as envs_mod

    stub_docker_image_inspect(monkeypatch, f"sha256:{OTHER_DIGEST}")
    stub_docker_image_content(monkeypatch, {
        "layers": [f"sha256:{OTHER_DIGEST}"], "env": None,
        "user": None, "workdir": None})
    envs_mod.register(name=OTHER_ENV, backend="byo",
                      source={"image": "docker://local/other-env:latest"},
                      project_dir=root)


def _register_in_env(spec, env: str) -> None:
    """Register the node's method again through production, declaring *env*."""
    from tests.harness.project import method_source_dir
    from wfc.persistence import project_root
    from wfc.registration import register_method

    source_dir = method_source_dir(project_root(), spec)
    method_yaml = source_dir / "method.yaml"
    method_yaml.write_text(
        method_yaml.read_text(encoding="utf-8")
        .replace(f"env: {ENV_NAME}\n", f"env: {env}\n"),
        encoding="utf-8")
    register_method(method_dir=source_dir, module_name=spec.module, assume_yes=True)


@workflow(purpose="A node pinned to an older method version whose env differs "
                  "from the newest version's is claimed twice, a miss that runs "
                  "and then a cache hit after HEAD moved: each row records its "
                  "own claim's HEAD, the pinned version's env name (never the "
                  "method's newest env), the installed wfc version and the "
                  "hash of the pipeline document the claim read")
def test_a_run_and_its_cache_hit_record_what_they_ran_with(git_project, monkeypatch):
    from wfc.registration.method_version import read_version_list

    口 = Step(step_num=1, name="Register v2 of the method in another env",
             purpose="v1 runs in image-io, v2 (same code) in other-env; the "
                     "method's own env now names other-env")
    project = build_project(
        Scenario(nodes=[selector(), node("a", module="test", inputs=[wire("sel")])],
                 samples=["s1"], env_name=ENV_NAME),
        root=git_project, monkeypatch=monkeypatch)
    root = project.root
    spec = project.scenario.node_by_id("a")
    _register_other_env(root, monkeypatch)
    _register_in_env(spec, OTHER_ENV)
    v1, v2 = read_version_list(root, spec.module, spec.method_name)
    assert (v1.env_name, v2.env_name) == (ENV_NAME, OTHER_ENV)
    with get_session() as session:
        method_env = session.exec(
            select(Method.env).where(Method.name == spec.method_name)).one()
    assert method_env == OTHER_ENV

    口 = Step(step_num=2, name="Pin the node to v1 and run it",
             purpose="The cache miss: a fresh row")
    document = json.loads(project.pipeline_json.read_text())
    next(n for n in document["nodes"] if n["id"] == "a")["version"] = v1.identity
    project.pipeline_json.write_text(json.dumps(document, indent=2))
    subprocess.run(["git", "commit", "-q", "-am", "pin a to v1"], cwd=root,
                   check=True, capture_output=True)
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    target = ("a", "s1", "default")
    first = drive_target(project, target, monkeypatch=monkeypatch)
    assert first.runs[target].ending == "completed", first.runs[target].ending
    first_head = _head(root)

    口 = Step(step_num=3, name="Move HEAD and claim it again",
             purpose="The commit is not in the key, so this is a cache hit "
                     "whose audit row is claimed at another HEAD")
    _commit_file(root, "notes.txt", "a later commit\n")
    second = drive_target(project, target, monkeypatch=monkeypatch)
    second_head = _head(root)
    assert second_head != first_head

    口 = Step(step_num=4, name="Each row records its own claim",
             purpose="Same env name, wfc version and document; each its own "
                     "commit")
    fresh, audit = _run(first.runs[target].run_id), _run(second.runs[target].run_id)
    assert fresh.cache_source_run_id is None
    assert audit.cache_source_run_id == fresh.id
    assert (fresh.git_commit, audit.git_commit) == (first_head, second_head)
    installed = importlib.metadata.version("workflow-canvas")
    digest = _document_hash(project.pipeline_json)
    for row in (fresh, audit):
        assert row.env_name == ENV_NAME
        assert row.wfc_version == installed
        assert row.pipeline_doc_hash == digest
    _assert_document_kept(root, project.pipeline_json)


@workflow(purpose="A claim handed a commit (the --git-commit flag of pre-run "
                  "and run-step) records it as given, and a claim with no "
                  "pipeline document records no document hash")
def test_a_given_commit_is_recorded_as_given(tmp_project, monkeypatch):
    from wfc.execution.claim import pre_run

    completed_run(tmp_project, monkeypatch=monkeypatch, method="rv_given",
                  module="rv_mod", sample="rv_a", params={"other": 0})
    given = "c" * 40
    flag, run_id = pre_run(method_name="rv_given", module_name="rv_mod",
                           sample="rv_a", params={"other": 1},
                           selector_slot="data", git_commit=given)
    assert flag == "NEW"
    row = _run(run_id)
    assert row.git_commit == given != _head(tmp_project)
    assert row.pipeline_doc_hash is None


def test_a_claim_writes_its_row_when_the_wfc_version_is_missing(
        tmp_project, monkeypatch, caplog):
    """No installed metadata: the row is written, its wfc version blank."""
    from wfc.execution.claim import pre_run

    completed_run(tmp_project, monkeypatch=monkeypatch, method="rv_noversion",
                  module="rv_mod", sample="rv_a", params={"other": 0})
    stub_distribution_metadata_missing(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="wfc.version"):
        flag, run_id = pre_run(method_name="rv_noversion", module_name="rv_mod",
                               sample="rv_a", params={"other": 1},
                               selector_slot="data", git_commit="c" * 40)
    assert flag == "NEW"
    row = _run(run_id)
    assert row.wfc_version is None
    assert row.env_name and row.git_commit == "c" * 40
    assert "not recorded" in caplog.text


# =============================================================================
# The pipeline document is kept, and every row names it
# =============================================================================


def _refused_mid_chain(pipeline_id: str) -> Scenario:
    """Head -> mid -> tail; mid's link names an output head does not declare."""
    return Scenario(nodes=[
        selector(),
        node("head", inputs=[wire("sel")], output_files={"data": "data.csv"}),
        node("mid", inputs=[wire("head", source_slot="nope")]),
        node("tail", inputs=[wire("mid")]),
    ], pipeline_id=pipeline_id)


def _launch(project, pipeline_id: str) -> None:
    """run_pipeline over the frozen document, the engine exiting non-zero.

    The stub rung ran the claims before this launch, reading the document
    ``run_scenario`` froze at the pipeline's path; the engine's claims read
    the frozen file, so the launch is handed that same file (the launch's
    write branch is covered in ``test_blob_push``).
    """
    from wfc.execution import run_pipeline

    frozen = layout.pipeline_doc_path(project.root, pipeline_id)
    gen_patch, popen_patch = mocked_snakemake(1)
    with gen_patch, popen_patch:
        with pytest.raises(RuntimeError):
            run_pipeline(pipeline_path=str(frozen),
                         project_root=str(project.root),
                         wfc_root=str(project.root), pipeline_id=pipeline_id)


@workflow(purpose="run_pipeline over a pipeline whose middle step is refused at "
                  "its claim and whose last step is cancelled, launched twice: "
                  "the run, the cache-hit audit row, the claim-refusal failed "
                  "rows and the cancelled rows all name one document hash, the "
                  "cache holds the canonical document under it, the failed and "
                  "cancelled rows leave the per-run values blank, and the "
                  "second launch stores nothing new")
def test_every_row_of_a_launched_pipeline_names_its_document(git_project,
                                                              monkeypatch):
    口 = Step(step_num=1, name="Run the targets, then launch the pipeline",
             purpose="head runs, mid is refused at its claim; the launch's "
                     "pipeline-end walk writes mid failed and tail cancelled")
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    obs = run_scenario(_refused_mid_chain("doc-launch-1"), root=git_project,
                       monkeypatch=monkeypatch, pipeline_end=False)
    project = obs.project
    _launch(project, "doc-launch-1")
    frozen = layout.pipeline_doc_path(project.root, "doc-launch-1")
    digest = _assert_document_kept(project.root, frozen)
    assert digest == _document_hash(project.pipeline_json)

    口 = Step(step_num=2, name="Every row names the document",
             purpose="The claimed row carries the per-run values; the walk's "
                     "rows carry the hash only")
    rows = {r.node_id: r for r in _pipeline_rows("doc-launch-1")}
    assert {k: r.status for k, r in rows.items()} == {
        "head": "completed", "mid": "failed", "tail": "cancelled"}
    assert {r.pipeline_doc_hash for r in rows.values()} == {digest}
    assert rows["head"].git_commit and rows["head"].env_name and rows["head"].wfc_version
    for walked in (rows["mid"], rows["tail"]):
        assert (walked.git_commit, walked.env_name, walked.wfc_version) == (None, None, None)

    口 = Step(step_num=3, name="Launch the same document again",
             purpose="head is a cache hit; one document, one blob row")
    obs2 = run_scenario(_refused_mid_chain("doc-launch-2"), root=git_project,
                        monkeypatch=monkeypatch, pipeline_end=False)
    _launch(obs2.project, "doc-launch-2")
    rows2 = {r.node_id: r for r in _pipeline_rows("doc-launch-2")}
    assert rows2["head"].cache_source_run_id == rows["head"].id
    assert {r.status for r in rows2.values()} == {"completed", "failed", "cancelled"}
    assert {r.pipeline_doc_hash for r in rows2.values()} == {digest}
    assert [b.hash for b in _document_blobs()] == [digest]


@workflow(purpose="A canvas submission writes the frozen document itself and "
                  "hands it to run_pipeline: the launch keeps that file's "
                  "canonical form, and a claim reading it, as the engine's "
                  "run-step does, records the same hash")
@pytest.mark.usefixtures("ready_preflight")
def test_a_canvas_submission_keeps_its_frozen_document(git_project, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import pin_project_root
    from tests.fixtures.fakes import fake_engine_process
    from wfc.canvas.server import app
    from wfc.canvas.state import _active_jobs
    from wfc.execution.claim import run_claim

    口 = Step(step_num=1, name="Submit through the canvas run route",
             purpose="The route writes the frozen pipeline.json and starts "
                     "run_pipeline on it; only the engine spawn is stubbed")
    project = build_project(
        Scenario(nodes=[node("prep_1", method="prep", module="canvas_mod",
                             outputs={"data": ".csv"})],
                 samples=["s1"]),
        root=git_project, monkeypatch=monkeypatch)
    pin_project_root(monkeypatch, project.root)
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    _active_jobs.clear()
    client = TestClient(app, raise_server_exceptions=False)
    payload = {"name": "canvas-doc", "samples": ["s1"], "links": [],
               "nodes": [{"id": "prep_1", "type": "method", "method": "prep",
                          "module": "canvas_mod", "params": {}}]}
    with fake_engine_process(only_snakemake=True):
        resp = client.post("/api/workflow/run", json=payload)
        assert resp.status_code == 200, resp.text
        pipeline_id = resp.json()["job_id"]
        _active_jobs[pipeline_id]["thread"].join(timeout=30)
        error = _active_jobs[pipeline_id]["error"]
    _active_jobs.clear()
    assert error is None, error

    口 = Step(step_num=2, name="The launch kept the canvas's frozen file",
             purpose="Its canonical bytes are in the cache under its hash")
    frozen = layout.pipeline_doc_path(project.root, pipeline_id)
    digest = _assert_document_kept(project.root, frozen)

    口 = Step(step_num=3, name="A claim reading the frozen file names it",
             purpose="What the engine's run-step does for each target")
    claim = run_claim(node_id="prep_1", sample="s1", variant="default",
                      method_name=None, module_name=None, params=None,
                      parent_run_ids=None, pipeline_id=pipeline_id,
                      pipeline_json=str(frozen), git_commit=None)
    assert claim["ok"], claim
    assert _run(claim["run_id"]).pipeline_doc_hash == digest


@workflow(purpose="wfc run-step --pipeline-json on its own, with the document "
                  "never launched or stored: the claim stores it and its row "
                  "names it")
def test_a_standalone_run_step_stores_its_document(tmp_project, monkeypatch):
    assert _document_blobs() == []
    run = claimed_run(tmp_project, monkeypatch=monkeypatch, method="rv_alone",
                      module="rv_mod", sample="rv_a")
    digest = _assert_document_kept(tmp_project, run.project.pipeline_json)
    assert _run(run.run_id).pipeline_doc_hash == digest
