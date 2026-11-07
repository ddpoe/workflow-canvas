"""Shared pieces of the pipeline-document witnesses.

The documents the refusal catalogs exercise (catalog cases 4, 6 and 7 and the
validation doors), the file each is written to, the Docker-up project the
CLI's pipeline verbs run in, one ``wfc`` invocation with the engine process
stubbed, and the ``ERROR:`` lines of a stderr. Every document is canvas form
and runs the harness's ``fit`` (``DEFAULT_MODULE``) on sample ``S1``.

Pins: the project helper stubs the Docker image probes and the readiness
probes (Docker said to be up); ``launching`` stubs the engine process.
Does not prove: anything about a real Docker daemon or a real Snakemake run.
"""

from __future__ import annotations

import json

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import fake_engine_process, stub_readiness_probes
from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.harness import Scenario, build_project, selector
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import DEFAULT_MODULE

M = DEFAULT_MODULE

#: The params the second version of ``fit`` declares.
TYPED_PARAMS = {
    "count": {"type": "int", "required": True,
              "constraints": {"min": 1, "max": 10}},
    "mode": {"type": "str", "required": False,
             "constraints": {"enum": ["fast", "slow"]}},
    "ratio": {"type": "float", "required": False},
}


def fit_document(**changes) -> dict:
    """A canvas-form document running ``fit`` on S1, with ``changes`` applied.

    Args:
        **changes: Top-level keys to set on the document.

    Returns:
        The document.
    """
    document = {
        "name": "refusals", "samples": ["S1"],
        "nodes": [{"id": "sel", "type": "input_selector", "fan_mode": "out",
                   "samples": ["S1"]},
                  {"id": "fit", "type": "method", "module": M, "method": "fit",
                   "params": {}}],
        "links": [{"source": "sel", "target": "fit", "targetHandle": "data"}],
    }
    document.update(changes)
    return document


def refusal_kinds() -> dict[str, tuple[str | dict | None, list[str]]]:
    """Each loader refusal kind of catalog case 7, with the words its refusal carries.

    Returns:
        Kind name to (content, words): the content is the file's text, a
        document, or None for no file.
    """
    ghost = fit_document()
    ghost["nodes"][1] = {"id": "ghost", "type": "method", "module": M,
                         "method": "missing", "params": {}}
    ghost["links"][0]["target"] = "ghost"
    variable = fit_document()
    variable["nodes"][1]["params"] = {"count": {"$var": "nope"}}
    keyed = fit_document()
    keyed["nodes"][1]["colour"] = "red"
    dangling = fit_document()
    dangling["links"].append({"source": "nowhere", "target": "fit",
                              "targetHandle": "data"})
    return {
        "missing file": (None, ["absent.json", "not found"]),
        "bad JSON": ('{"nodes": [', ["bad JSON.json", "is not valid JSON",
                                     "line 1"]),
        "not an object": ("[1, 2]", ["not an object.json", "JSON object"]),
        "unregistered method": (ghost, ["Node 'ghost'",
                                        f"'{M}.missing' is not registered"]),
        "Graph refusal": (dangling, ["unknown source node nowhere"]),
        "non-mapping param_sets": (fit_document(param_sets=["wide"]),
                                   ["'param_sets' must be a mapping"]),
        "undeclared variable": (variable, ["Unknown pipeline variable: 'nope'"]),
        "unknown key": (keyed, ["Node 'fit': unknown key 'colour'"]),
    }


def malformed_document() -> dict:
    """A canvas-form document with one of each param and key refusal the validator gives.

    Returns:
        The document (``fit``, ``fit2`` and ``fit3`` against fit's typed version).
    """
    def method(node_id: str, params: dict, **extra) -> dict:
        return {"id": node_id, "type": "method", "module": M, "method": "fit",
                "params": params, **extra}
    return {
        "name": "doors", "bogus_top": 1, "samples": ["S1"],
        "nodes": [
            {"id": "sel", "type": "input_selector", "fan_mode": "out",
             "samples": ["S1"]},
            method("fit", {"extra": 1, "mode": "medium", "ratio": True},
                   colour="red"),
            method("fit2", {"count": True}),
            method("fit3", {"count": 11}),
        ],
        "links": [{"source": "sel", "target": t, "targetHandle": "data"}
                  for t in ("fit", "fit2", "fit3")],
    }


def undeclared_slot_document() -> dict:
    """A document whose one link names an input ``fit`` does not declare (case 4's link half).

    Returns:
        The document: ``sel`` feeds ``fit`` at ``table``; ``fit`` declares ``data``.
    """
    document = fit_document()
    document["nodes"][1]["params"] = {"count": 2}
    document["links"][0]["targetHandle"] = "table"
    return document


def write_document(tmp_path, name: str, content) -> str:
    """Write a kind's file under ``tmp_path`` and return its path.

    Args:
        tmp_path: The directory to write in.
        name: The file's stem.
        content: The file's text, a document, or None for no file.

    Returns:
        The file's path (``absent.json``, never written, for None).
    """
    if content is None:
        return str(tmp_path / "absent.json")
    path = tmp_path / f"{name}.json"
    text = content if isinstance(content, str) else json.dumps(content)
    path.write_text(text, encoding="utf-8")
    return str(path)


def docker_up_project(tmp_project, monkeypatch, *nodes):
    """A project with the selector and *nodes* registered, Docker said to be up.

    Args:
        tmp_project: The project root fixture.
        monkeypatch: The test's monkeypatch.
        *nodes: Harness node specs to register after the selector.

    Returns:
        The harness project over sample S1.
    """
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch, docker="ok")
    return build_project(Scenario(nodes=[selector(), *nodes], samples=["S1"]),
                         root=tmp_project, monkeypatch=monkeypatch)


def error_lines(stderr: str) -> list[str]:
    """The ``ERROR:`` lines of a stderr, in order."""
    return [line for line in stderr.splitlines() if line.startswith("ERROR:")]


def launching(cli, *argv):
    """Run one wfc invocation with the engine stubbed.

    Args:
        cli: The ``cli`` fixture.
        *argv: The invocation's arguments.

    Returns:
        (result, the engine launches the invocation made).
    """
    gen_patch, _ = mocked_snakemake(0)
    with gen_patch, fake_engine_process(only_snakemake=True) as popen:
        result = cli(*argv)
    launches = [c for c in popen.call_args_list
                if "snakemake" in " ".join(map(str, c.args[0]))]
    return result, launches
