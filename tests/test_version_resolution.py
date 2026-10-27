"""Preparation resolves every node's version; run-pipeline names what it resolved.

Every document ``prepare_document`` returns carries a ``version`` on every
method node: a pin is kept and an unpinned node gains its method's newest
version, so the frozen ``pipeline.json`` records what each node ran while
``pipeline.editable.json`` keeps the author's choices. ``wfc run-pipeline``
prints one line per node the submitted document left unpinned, and
a node whose ``module.method`` is not registered is refused by name, even when
it carries its own ``env``.

Methods register through production registration into a real git project;
only the engine spawn is stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes.engine import fake_engine_process
from tests.fixtures.routes import (
    register_test_method,
    sample_source_dir,
    write_env_record,
)

MODULE = "vr"
ENV = "vr-env"

pytestmark = pytest.mark.usefixtures("ready_preflight")


def _register(root: Path, method: str, revision: int) -> None:
    """Register ``vr.<method>`` with its script at ``revision`` (one version each)."""
    method_dir = sample_source_dir(root) / "_methods" / method
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(
        f"env: {ENV}\n"
        "inputs:\n  data:\n    type: .csv\n    required: false\n"
        "outputs: {}\n"
        "params: {}\n",
        encoding="utf-8",
    )
    (method_dir / f"{method}.py").write_text(
        f"REVISION = {revision}\n\n\ndef main():\n    pass\n", encoding="utf-8")
    register_test_method(root, module_name=MODULE, method_dir=method_dir,
                         method_name=method)


def _project(root: Path) -> dict[str, list]:
    """Register ``first`` and ``second`` twice each; return their version lists."""
    from wfc.registration.method_version import read_version_list

    # A registry image; a test that runs the pipeline arms the daemon as
    # holding it (_image_on_this_machine), so the pre-flight runs it as it is.
    write_env_record(root, ENV, digest="e" * 64,
                     image="ghcr.io/wfc-test/vr")
    for revision in (1, 2):
        for method in ("first", "second"):
            _register(root, method, revision)
    return {m: read_version_list(root, MODULE, m) for m in ("first", "second")}


def _image_on_this_machine(monkeypatch) -> None:
    """The daemon holds the env's image: the run-time rule needs no pull."""
    from tests.fixtures.fakes import stub_docker_image_inspect

    stub_docker_image_inspect(monkeypatch, "sha256:" + "e" * 64)


def _document(first_pin: str | None) -> dict:
    first = {"id": "f", "type": "method", "method": "first", "module": MODULE,
             "params": {}}
    if first_pin is not None:
        first["version"] = first_pin
    return {
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["s1"]},
            first,
            {"id": "s", "type": "method", "method": "second", "module": MODULE,
             "params": {}},
        ],
        "links": [{"source": "sel", "target": "f", "target_slot": "data"},
                  {"source": "sel", "target": "s", "target_slot": "data"}],
        "samples": ["s1"],
    }


def _version_lines(out: str) -> list[str]:
    return [line for line in out.splitlines()
            if line.startswith("[run_pipeline] node ")]


@workflow(purpose="run-pipeline on a document with one pinned and one unpinned "
                  "node prints one line, for the unpinned node, naming its "
                  "module/method and the label it resolved; the frozen "
                  "pipeline.json pins both nodes and pipeline.editable.json "
                  "keeps the author's one pin; running the frozen copy again "
                  "prints no line")
def test_run_pipeline_names_the_version_each_unpinned_node_resolved(
        wfc_root, capsys, monkeypatch):
    from wfc import layout
    from wfc.execution import run_pipeline

    口 = Step(step_num=1, name="Two methods, two versions each",
             purpose="first is pinned to v1; second is left unpinned")
    root = Path(wfc_root)
    versions = _project(root)
    _image_on_this_machine(monkeypatch)
    v1_first = versions["first"][0]
    v2_second = versions["second"][-1]
    doc_path = root / "pipeline.json"
    doc_path.write_text(json.dumps(_document(v1_first.identity)), encoding="utf-8")
    capsys.readouterr()

    口 = Step(step_num=2, name="Run the pipeline from the CLI path",
             purpose="Only the engine spawn is stubbed")
    with fake_engine_process():
        run_pipeline(pipeline_path=str(doc_path), project_root=str(root),
                     wfc_root=str(root), cores=1, pipeline_id="vr-cli")

    口 = Step(step_num=3, name="One line, for the unpinned node",
             purpose="Naming the node, module/method and the resolved label")
    assert _version_lines(capsys.readouterr().out) == [
        f"[run_pipeline] node 's' ({MODULE}/second) is not pinned: "
        f"runs v{v2_second.label}, the newest version",
    ]

    口 = Step(step_num=4, name="The frozen copy pins every node; the editable form "
                              "keeps the author's choices",
             purpose="pipeline.json records what ran; pipeline.editable.json is "
                     "the submitted document")
    frozen = json.loads(layout.pipeline_doc_path(root, "vr-cli").read_text(
        encoding="utf-8"))
    pins = {n["id"]: n.get("version") for n in frozen["nodes"]}
    assert pins == {"sel": None, "f": v1_first.identity, "s": v2_second.identity}
    editable = json.loads(layout.pipeline_editable_doc_path(root, "vr-cli")
                          .read_text(encoding="utf-8"))
    assert {n["id"]: n.get("version") for n in editable["nodes"]} == {
        "sel": None, "f": v1_first.identity, "s": None}

    口 = Step(step_num=5, name="The frozen copy runs with no version line",
             purpose="As the canvas path does: its document is pinned throughout")
    frozen_path = root / "frozen.json"
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    with fake_engine_process():
        run_pipeline(pipeline_path=str(frozen_path), project_root=str(root),
                     wfc_root=str(root), cores=1, pipeline_id="vr-frozen")
    assert _version_lines(capsys.readouterr().out) == []


@pytest.mark.parametrize("enriched", [False, True], ids=["sparse", "own-env"])
@workflow(purpose="A node whose module.method is not registered is refused by "
                  "preparation naming the node and module.method, whether the "
                  "document is sparse or the node carries its own env and slot "
                  "map")
def test_a_node_whose_method_is_not_registered_is_refused_by_name(
        wfc_root, enriched):
    from wfc.contracts import MethodNotFoundError
    from wfc.execution.prepare import prepare_document

    root = Path(wfc_root)
    _project(root)
    document = _document(None)
    document["nodes"][2] = {"id": "ghost", "type": "method", "method": "missing",
                            "module": MODULE, "params": {}}
    if enriched:
        for n in document["nodes"][1:]:
            n.update({"env": ENV, "slot_outputs": {}, "slot_types": {}})
    document["links"][1]["target"] = "ghost"

    with pytest.raises(MethodNotFoundError) as refused:
        prepare_document(document)

    assert str(refused.value).startswith(
        f"Node 'ghost': method '{MODULE}.missing' is not registered")


@workflow(purpose="History's download returns a pipeline run's frozen copy, "
                  "byte for byte, as a JSON attachment with every method node "
                  "pinned to the version it ran (the unpinned node to the "
                  "newest it resolved); an unknown pipeline is a 404 and an id "
                  "naming another directory a 400")
def test_download_returns_the_frozen_copy_with_every_node_pinned(
        wfc_root, monkeypatch):
    from tests.fixtures.fakes import bind_provider
    from tests.fixtures.routes import canvas_client
    from wfc import layout
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.execution import run_pipeline

    root = Path(wfc_root)
    versions = _project(root)
    _image_on_this_machine(monkeypatch)
    doc_path = root / "pipeline.json"
    doc_path.write_text(json.dumps(_document(versions["first"][0].identity)),
                        encoding="utf-8")
    with fake_engine_process():
        run_pipeline(pipeline_path=str(doc_path), project_root=str(root),
                     wfc_root=str(root), cores=1, pipeline_id="vr-dl")
    client = canvas_client(root, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(root)))

    resp = client.get("/api/pipelines/vr-dl/download")
    missing = client.get("/api/pipelines/no-such-pipeline/download")
    escaping_encoded = client.get("/api/pipelines/..%5Cvr-dl/download")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="vr-dl.pipeline.json"')
    assert resp.content == layout.pipeline_doc_path(root, "vr-dl").read_bytes()
    pins = {n["id"]: n.get("version") for n in resp.json()["nodes"]}
    assert pins == {"sel": None, "f": versions["first"][0].identity,
                    "s": versions["second"][-1].identity}
    assert missing.status_code == 404, missing.text
    assert escaping_encoded.status_code == 400, escaping_encoded.text
