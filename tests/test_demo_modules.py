"""Subsystem tests: the demo's steps sit in three modules.

``wfc demo`` registers its five methods across three ``__demo__``-prefixed
modules (cleanup, labeling, plotting), so a fresh demo shows three module
colours in the canvas, and the shipped pipeline names each step's module.
``wfc demo --remove`` removes every demo module and leaves the user's own.

Projects are built through production paths (``wfc init``, ``wfc demo``,
``register_module``); Docker is faked as in ``tests/test_demo_env_rename.py``.
"""

from __future__ import annotations

import json

from axiom_annotations import workflow

from tests.test_demo_env_rename import _demo, _git, _project


def _demo_methods_by_module() -> dict[str, set[str]]:
    from sqlmodel import select

    from wfc.persistence import Method, Module, get_session

    with get_session() as session:
        modules = {m.id: m.name for m in session.exec(select(Module)).all()}
        out: dict[str, set[str]] = {}
        for method in session.exec(select(Method)).all():
            out.setdefault(modules[method.module_id], set()).add(method.name)
    return out


@workflow(purpose="A fresh wfc demo registers its five methods in three "
                  "__demo__ modules with three different default colours, "
                  "and every method node of the demo pipeline names the "
                  "module its method is registered in")
def test_a_fresh_demo_shows_three_module_colours(tmp_path, monkeypatch, capsys):
    from wfc.persistence import use_project
    from wfc.registration import read_module_colors

    target, _ = _project(tmp_path, monkeypatch)
    assert _demo(target) == 0, capsys.readouterr().err

    with use_project(target):
        by_module = _demo_methods_by_module()
    assert by_module == {
        "__demo__cleanup": {"__demo__preprocess", "__demo__filter_cells"},
        "__demo__labeling": {"__demo__label", "__demo__summarize"},
        "__demo__plotting": {"__demo__plot"},
    }
    colors = read_module_colors(target)
    assert len({colors[name].color for name in by_module}) == 3

    pipeline = json.loads((target / "demo-pipeline.json").read_text("utf-8"))
    module_of = {m: mod for mod, methods in by_module.items() for m in methods}
    method_nodes = [n for n in pipeline["nodes"] if n["type"] == "method"]
    assert len(method_nodes) == 5
    for node in method_nodes:
        assert node["module"] == module_of[node["method"]], node


@workflow(purpose="wfc demo --remove --yes removes all three demo modules, "
                  "their methods and their snapshot directories, keeps the "
                  "user's module and method, warns that the user's method "
                  "names the demo env and names none of the demo's own "
                  "methods in that warning, and leaves a clean tree")
def test_demo_remove_takes_every_demo_module_and_keeps_the_users(
        tmp_path, monkeypatch, capsys):
    from sqlmodel import select

    from wfc import layout
    from wfc.demo.scaffold import ASSETS_DIR
    from wfc.persistence import Module, get_session, use_project
    from wfc.registration import register_method, register_module

    target, _ = _project(tmp_path, monkeypatch)
    assert _demo(target) == 0, capsys.readouterr().err
    # The user's own method on the demo's env: the bundled plot asset,
    # registered in the user's module under a name of its own.
    monkeypatch.chdir(target)
    with use_project(target):
        register_module(name="mine", contracts=[])
        register_method(method_dir=ASSETS_DIR / "methods" / "plot",
                        module_name="mine", method_name="myplot",
                        script_name="plot.py", assume_yes=True)
    capsys.readouterr()

    assert _demo(target, "--remove", "--yes") == 0
    err = capsys.readouterr().err

    warning = next(line for line in err.splitlines()
                   if "non-demo method(s) reference env" in line)
    assert warning.split("will no longer resolve it: ", 1)[1] == "mine/myplot", warning
    with use_project(target):
        with get_session() as session:
            names = [m.name for m in session.exec(select(Module)).all()]
        by_module = _demo_methods_by_module()
    assert names == ["mine"]
    assert by_module == {"mine": {"myplot"}}
    methods_root = layout.methods_dir(target)
    assert sorted(p.name for p in methods_root.iterdir() if p.is_dir()) == ["mine"]
    assert _git(target, "status", "--porcelain") == ""
