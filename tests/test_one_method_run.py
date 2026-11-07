"""The one-method run: save the one-step document, then run it as any pipeline.

Execution's ``run_one_method`` (catalog case 9, its Execution half): the
document for one registered method on one sample is saved as
``pipelines/<module>.<method>-<sample>.json`` and handed to
``run_pipeline``; the same file is reused, a different one is refused and
never overwritten, and a method the sample could feed several inputs of is
refused before anything is saved.

Declared fixture deviations: none. The project is built by the harness
through production registration; the engine process is the only thing
stubbed (``fake_engine_process``).
"""
from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import fake_engine_process
from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc import layout
from wfc.execution import OneMethodRunRefusedError, run_one_method


def _project(tmp_project, monkeypatch, *nodes):
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    return build_project(Scenario(nodes=[selector(), *nodes], samples=["s1"]),
                         root=tmp_project, monkeypatch=monkeypatch)


def _run(project, qualified: str, **kwargs):
    """run_one_method with the engine process stubbed; returns (saved, engine launches)."""
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True) as popen:
        saved = run_one_method(qualified, "s1", root=project.root, **kwargs)
    launches = [c for c in popen.call_args_list if "snakemake" in " ".join(map(str, c.args[0]))]
    return saved, launches


@workflow(purpose="Case 9, Execution half: a one-method run saves its one-step "
                  "document at pipelines/<module>.<method>-<sample>.json and runs "
                  "it; run again, the same file is reused and run; a different "
                  "file at the path is refused naming it, left as it was, and "
                  "nothing is launched")
def test_a_one_method_run_saves_its_document_and_never_overwrites(tmp_project,
                                                                  monkeypatch):
    project = _project(tmp_project, monkeypatch, node("a", inputs=[wire(SELECTOR_ID)]))
    spec = project.scenario.node_by_id("a")
    qualified = f"{spec.module}.{spec.method_name}"
    path = layout.one_method_pipeline_path(project.root, spec.module, spec.method_name, "s1")

    口 = Step(step_num=1, name="Run the method on the sample",
             purpose="The document is saved, then launched")
    seen = []
    saved, launches = _run(project, qualified, on_saved=seen.append)
    assert saved.path == path and not saved.reused and seen == [saved]
    assert json.loads(path.read_text(encoding="utf-8")) == saved.document
    assert [n["type"] for n in saved.document["nodes"]] == ["input_selector", "method"]
    assert saved.document["links"][0]["targetHandle"] == "data"
    assert len(launches) == 1

    口 = Step(step_num=2, name="Run it again",
             purpose="The same document: the file is reused, untouched, and run")
    before = path.stat().st_mtime_ns
    again, launches = _run(project, qualified)
    assert again.reused and path.stat().st_mtime_ns == before and len(launches) == 1

    口 = Step(step_num=3, name="A different file at the path",
             purpose="Refused naming the path; never overwritten; nothing launched")
    path.write_text('{"name": "mine"}\n', encoding="utf-8")
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True) as popen:
        with pytest.raises(OneMethodRunRefusedError, match="never overwrites") as caught:
            run_one_method(qualified, "s1", root=project.root)
    assert str(path) in str(caught.value)
    assert path.read_text(encoding="utf-8") == '{"name": "mine"}\n'
    assert not popen.call_args_list


def test_several_inputs_the_sample_could_feed_are_refused_before_saving(tmp_project,
                                                                         monkeypatch):
    """Two required inputs: refused naming both and check-pipeline; nothing saved."""
    project = _project(tmp_project, monkeypatch,
                       node("a", inputs=[wire(SELECTOR_ID)]),
                       node("b", inputs=[wire(SELECTOR_ID),
                                         wire("a", target_slot="other")]))
    spec = project.scenario.node_by_id("b")
    with pytest.raises(OneMethodRunRefusedError, match="check-pipeline") as caught:
        run_one_method(f"{spec.module}.{spec.method_name}", "s1", root=project.root)
    assert "'data'" in str(caught.value) and "'other'" in str(caught.value)
    assert not layout.project_pipelines_dir(project.root).exists()


def test_an_unregistered_sample_or_method_is_refused_before_saving(tmp_project,
                                                                   monkeypatch):
    """Neither writes a file."""
    project = _project(tmp_project, monkeypatch, node("a", inputs=[wire(SELECTOR_ID)]))
    spec = project.scenario.node_by_id("a")
    with pytest.raises(OneMethodRunRefusedError, match="not registered"):
        run_one_method(f"{spec.module}.{spec.method_name}", "nope", root=project.root)
    with pytest.raises(OneMethodRunRefusedError):
        run_one_method(f"{spec.module}.missing", "s1", root=project.root)
    with pytest.raises(OneMethodRunRefusedError, match="module.method"):
        run_one_method("bare", "s1", root=project.root)
    assert not layout.project_pipelines_dir(project.root).exists()


def _param_project(tmp_project, monkeypatch):
    """One method on the sample, declaring ints ``n`` (default 5) and ``m`` (default 1)."""
    project = _project(tmp_project, monkeypatch,
                       node("a", inputs=[wire(SELECTOR_ID)],
                            param_types={"n": "int", "m": "int"},
                            param_defaults={"n": 5, "m": 1}))
    spec = project.scenario.node_by_id("a")
    return project, spec, f"{spec.module}.{spec.method_name}"


def _frozen_documents(root):
    """Every frozen pipeline document the runs left, as parsed JSON."""
    run_dirs = layout.pipelines_dir(root)
    return [json.loads(layout.pipeline_doc_path(root, d.name).read_text(encoding="utf-8"))
            for d in sorted(run_dirs.iterdir())
            if layout.pipeline_doc_path(root, d.name).is_file()] if run_dirs.is_dir() else []


@workflow(purpose="Case 9, params and name: a one-method run's params reach the "
                  "method node of the saved and the frozen document; with params "
                  "and no name the file and the document's name carry the params' "
                  "fingerprint; with a name the file is pipelines/<name>.json and "
                  "the document carries that name; with a save path and a name the "
                  "name is the document's only")
def test_a_one_method_run_carries_its_params_and_its_name(tmp_project, monkeypatch,
                                                          tmp_path):
    from wfc.identity import canonical_json_hash

    project, spec, qualified = _param_project(tmp_project, monkeypatch)
    fp = canonical_json_hash({"n": 3})[:8]

    口 = Step(step_num=1, name="Params, no name",
             purpose="The file and the name carry the fingerprint; the node and "
                     "the frozen document carry the params")
    saved, launches = _run(project, qualified, params={"n": 3})
    expected = layout.one_method_pipeline_path(project.root, spec.module,
                                               spec.method_name, "s1", fp)
    assert saved.path == expected and expected.name.endswith(f"-s1-{fp}.json")
    assert saved.document["name"] == expected.stem
    method_node = next(n for n in saved.document["nodes"] if n["type"] == "method")
    assert method_node["params"] == {"n": 3} and len(launches) == 1
    frozen = _frozen_documents(project.root)
    assert len(frozen) == 1
    # The frozen form fills the declared defaults the run leaves out (m=1).
    assert [n["params"] for n in frozen[0]["nodes"] if n.get("type") == "method"] \
        == [{"n": 3, "m": 1}]

    口 = Step(step_num=2, name="A name",
             purpose="pipelines/<name>.json, the document named so, no fingerprint")
    named, _ = _run(project, qualified, params={"n": 3}, name="try-n3")
    assert named.path == layout.named_pipeline_path(project.root, "try-n3")
    assert named.path.name == "try-n3.json" and named.document["name"] == "try-n3"

    口 = Step(step_num=3, name="A save path and a name",
             purpose="The file is the save path; the name is the document's")
    there = tmp_path / "elsewhere.json"
    both, _ = _run(project, qualified, name="mine", save=there)
    assert both.path == there and both.document["name"] == "mine"


def test_the_same_params_reuse_one_file_and_other_params_get_another(tmp_project,
                                                                     monkeypatch):
    """Key order does not change the file; a different value does; {} is no params."""
    project, spec, qualified = _param_project(tmp_project, monkeypatch)
    first, _ = _run(project, qualified, params={"n": 3, "m": 2})
    again, _ = _run(project, qualified, params={"m": 2, "n": 3})
    assert again.path == first.path and again.reused
    other, _ = _run(project, qualified, params={"n": 4, "m": 2})
    assert other.path != first.path and not other.reused
    empty, _ = _run(project, qualified, params={})
    assert empty.path == layout.one_method_pipeline_path(project.root, spec.module,
                                                         spec.method_name, "s1")


@pytest.mark.parametrize("kwargs,named", [
    pytest.param({"params": {"z": 1}}, "'z'", id="undeclared-param"),
    pytest.param({"params": {"n": "three"}}, "'n'", id="wrong-type"),
    pytest.param({"name": "a/b"}, "name", id="unusable-name"),
    pytest.param({"name": "taken"}, "taken.json", id="different-file-at-the-name"),
])
def test_each_refusal_comes_before_anything_is_saved_or_run(tmp_project, monkeypatch,
                                                            kwargs, named):
    """The verdict and the name rule refuse before the save; nothing is launched."""
    from wfc.execution import PipelineDocumentError

    project, _spec, qualified = _param_project(tmp_project, monkeypatch)
    pipelines = layout.project_pipelines_dir(project.root)
    if kwargs.get("name") == "taken":
        pipelines.mkdir(parents=True)
        layout.named_pipeline_path(project.root, "taken").write_text(
            '{"name": "taken"}\n', encoding="utf-8")
    before = sorted(p.name for p in pipelines.iterdir()) if pipelines.exists() else []
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True) as popen:
        with pytest.raises(PipelineDocumentError) as caught:
            run_one_method(qualified, "s1", root=project.root, **kwargs)
    assert named in str(caught.value)
    after = sorted(p.name for p in pipelines.iterdir()) if pipelines.exists() else []
    assert after == before and not popen.call_args_list
