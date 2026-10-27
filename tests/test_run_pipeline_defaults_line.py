"""``wfc run-pipeline`` names the declared defaults each node runs.

Preparation fills every parameter a method node, or one of its
``param_sets`` variants, leaves out with the default its version declares.
``run_pipeline`` prints one line per node, and per variant, that runs such a
default, naming each parameter and its value (compact JSON, cut at 60
characters), next to the "not pinned" lines; a node that leaves nothing out
prints nothing, and an already filled document (the canvas's frozen copy)
prints none. The line is derived by comparing the submitted document with
the prepared one (``wfc.execution.prepare.filled_defaults``); nothing reads a
contract for it.

The method registers through production registration: the harness registers
``fit`` (v1), then its ``method.yaml`` params are edited and it is registered
again, minting the version that declares the defaults. Only the Snakemake
spawn is stubbed.
"""

from __future__ import annotations

import json

from axiom_annotations import Step, workflow

from tests.fixtures.fakes import stub_docker_image_inspect, stub_readiness_probes
from tests.fixtures.routes import register_again_with_params
from tests.harness import Scenario, build_project, node, selector, wire
from tests.harness.scenario import DEFAULT_MODULE, SELECTOR_ID

M = DEFAULT_MODULE

_TAGS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]

#: ``fit``'s defaults-declaring params: ``count`` is required with a default;
#: ``tags``' default is longer than 60 characters as compact JSON.
_DEFAULTED = {
    "count": {"type": "int", "required": True, "default": 3},
    "ratio": {"type": "float", "required": False, "default": 1.0},
    "label": {"type": "str", "required": False, "default": "base"},
    "tags": {"type": "list", "required": False, "default": _TAGS},
}

#: ``tags``' default as the line shows it: its first 60 characters and "…".
_TAGS_SHOWN = '["alpha","bravo","charlie","delta","echo","foxtrot","golf","…'

#: Every parameter written: the node runs no default.
_ALL_WRITTEN = {"count": 2, "ratio": 0.5, "label": "x", "tags": []}


def _document() -> dict:
    """Three fit nodes: one leaves everything out, one writes everything (with a
    variant that leaves two out and one that writes all), one writes everything."""
    nodes = [{"id": "sel", "type": "input_selector", "fan_mode": "out",
              "samples": ["S1"]}]
    links = []
    for node_id, params in (("a", {}), ("v", dict(_ALL_WRITTEN)),
                            ("b", dict(_ALL_WRITTEN))):
        nodes.append({"id": node_id, "type": "method", "module": M,
                      "method": "fit", "params": params})
        links.append({"source": "sel", "target": node_id, "targetHandle": "data"})
    return {"name": "defaults-line", "samples": ["S1"], "nodes": nodes,
            "links": links,
            "param_sets": {"v": {"lo": {"count": 1, "ratio": 0.2},
                                 "hi": {"count": 4, "ratio": 2.0, "label": "y",
                                        "tags": ["z"]}}}}


def _defaults_lines(out: str) -> list[str]:
    return [line for line in out.splitlines()
            if line.startswith("[run_pipeline] node ")
            and " runs declared defaults: " in line]


@workflow(purpose="run-pipeline on nodes that leave parameters unset prints "
                  "one line per such node naming each defaulted parameter and "
                  "its value (a required one included, unmarked), one line per "
                  "param_sets variant that runs defaults, naming it, and "
                  "nothing for a node or variant that leaves none out; running "
                  "the frozen copy prints no such line")
def test_run_pipeline_names_the_declared_defaults_each_node_runs(
        tmp_project, monkeypatch, capsys, tmp_path):
    from wfc import layout
    from wfc.execution import run_pipeline

    from tests.fixtures.fakes.engine import fake_engine_process

    口 = Step(step_num=1, name="Register fit; its next version declares defaults",
             purpose="Production registration: count is required with default "
                     "3, ratio 1.0, label 'base', tags a long list")
    scenario = Scenario(
        nodes=[selector(), node("fit", inputs=[wire(SELECTOR_ID)],
                                params={"count": 2})],
        samples=["S1"], pipeline_id="defaults-line", name="defaults_line")
    project = build_project(scenario, root=tmp_project, monkeypatch=monkeypatch)
    register_again_with_params(project.root, scenario.node_by_id("fit"), _DEFAULTED)
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    path = tmp_path / "defaults-line.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    capsys.readouterr()

    口 = Step(step_num=2, name="Run the sparse document from the CLI path",
             purpose="Only the Snakemake spawn is stubbed")
    with fake_engine_process(only_snakemake=True):
        run_pipeline(pipeline_path=str(path), project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id="defaults-line-cli")

    口 = Step(step_num=3, name="One line per node or variant running defaults",
             purpose="a names all four (count required, unmarked); v's own "
                     "params and b write everything; v's variant lo names the "
                     "two it left out, hi none")
    assert _defaults_lines(capsys.readouterr().out) == [
        f"[run_pipeline] node 'a' ({M}/fit) runs declared defaults: "
        f'count=3, ratio=1.0, label="base", tags={_TAGS_SHOWN}',
        f"[run_pipeline] node 'v' variant 'lo' ({M}/fit) runs declared "
        f'defaults: label="base", tags={_TAGS_SHOWN}',
    ]

    口 = Step(step_num=4, name="The frozen copy prints no defaults line",
             purpose="Its params are already filled, as the canvas's are")
    frozen = json.loads(layout.pipeline_doc_path(
        project.root, "defaults-line-cli").read_text(encoding="utf-8"))
    frozen_path = tmp_path / "frozen.json"
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    with fake_engine_process(only_snakemake=True):
        run_pipeline(pipeline_path=str(frozen_path), project_root=str(project.root),
                     wfc_root=str(project.root), pipeline_id="defaults-line-frozen")
    assert _defaults_lines(capsys.readouterr().out) == []


def _method(node_id: str, params: dict | None = None) -> dict:
    out = {"id": node_id, "type": "method", "module": "m", "method": "fit"}
    if params is not None:
        out["params"] = params
    return out


def test_a_nodes_own_params_are_named_only_where_they_run():
    """Every node has a variant table naming the whole axis: no node's own
    params run, so only the variants are named."""
    from wfc.execution.prepare import filled_defaults

    submitted = {"nodes": [_method("x", {"k": 1}), _method("y")],
                 "param_sets": {"x": {"p": {}, "q": {"k": 2}},
                                "y": {"p": {"k": 5}, "q": {}}}}
    prepared = {"nodes": [_method("x", {"k": 1, "d": 0}), _method("y", {"d": 0})],
                "param_sets": {"x": {"p": {"d": 0}, "q": {"k": 2, "d": 0}},
                               "y": {"p": {"k": 5, "d": 0}, "q": {"d": 0}}}}
    assert [(e.node_id, e.variant, e.values)
            for e in filled_defaults(submitted, prepared)] == [
        ("x", "p", {"d": 0}), ("x", "q", {"d": 0}),
        ("y", "p", {"d": 0}), ("y", "q", {"d": 0}),
    ]


def test_a_node_lacking_an_axis_name_runs_its_own_params_under_it():
    """y's table lacks x's variant q, so y's own params run under q and are named;
    a node with no params key in the submitted document is compared as empty."""
    from wfc.execution.prepare import filled_defaults

    submitted = {"nodes": [_method("x"), _method("y")],
                 "param_sets": {"x": {"q": {"d": 1}}, "y": {"p": {"d": 1}}}}
    prepared = {"nodes": [_method("x", {"d": 0}), _method("y", {"d": 0})],
                "param_sets": submitted["param_sets"]}
    assert [(e.node_id, e.variant, e.values)
            for e in filled_defaults(submitted, prepared)] == [
        ("x", None, {"d": 0}), ("y", None, {"d": 0}),
    ]


def test_a_filled_document_and_system_nodes_name_nothing():
    """A document compared with itself names nothing; selectors are skipped."""
    from wfc.execution.prepare import filled_defaults

    document = {"nodes": [{"id": "sel", "type": "input_selector"},
                          _method("x", {"d": 0})],
                "param_sets": {"x": {"p": {"d": 1}}}}
    assert filled_defaults(document, document) == []
