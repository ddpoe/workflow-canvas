"""One params dict per run: the run row, the frozen document and ``WFC_PARAMS``.

Preparation fills every declared default a node, or one of its ``param_sets``
variants, leaves out, and the claim records and dispatches that same dict.
Launching a real pipeline on the ``echo_params`` fixture method (it writes the
``WFC_PARAMS`` it read to its output) shows that, for every run, the params the
run row records, the params the frozen ``pipeline.json`` holds for it and the
params the method read are one dict, the defaults included.

Real containers: the method runs inside the fixture image, so this module is
``integration`` + ``requires_docker``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import (
    FIXTURE_METHODS_DIR,
    create_sample_csv,
    register_test_method,
)

pytestmark = [pytest.mark.integration, requires_docker]

MODULE = "test_pipeline"
METHOD = "echo_params"
SAMPLE = "sample_a"
PIPELINE_ID = "echo-params-defaults"

#: The defaults echo_params declares.
_DEFAULTS = {"sigma": 1.5, "label": "base", "tags": ["a", "b"]}


def _document() -> dict:
    """Two echo_params nodes: ``e`` has two variants, ``n`` only its own params.

    ``n`` has no table, so the variant axis is ``default``, ``lo`` and ``hi``:
    ``n``'s own params run under all three, and ``e`` runs ``lo``, ``hi`` and
    its own params under ``default``.
    """
    nodes = [{"id": "sel", "type": "input_selector", "fan_mode": "out",
              "samples": [SAMPLE], "source": "registered"}]
    links = []
    for node_id, params in (("e", {"count": 2}), ("n", {"count": 3, "label": "x"})):
        nodes.append({"id": node_id, "type": "method", "module": MODULE,
                      "method": METHOD, "params": params})
        links.append({"source": "sel", "target": node_id,
                      "sourceHandle": "output", "targetHandle": "data"})
    return {"name": "echo-params", "samples": [SAMPLE], "nodes": nodes,
            "links": links,
            "param_sets": {"e": {"lo": {"count": 1},
                                 "hi": {"count": 4, "sigma": 0.25}}}}


def _canonical(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def _artifact(project_dir: Path, artifact_path: str) -> Path:
    """A RunOutput's artifact path, resolved against the project root."""
    path = Path(artifact_path)
    return path if path.is_absolute() else project_dir / path


@workflow(purpose="For a launched pipeline with node params and param_sets "
                  "variants that leave declared defaults out, each run row's "
                  "params, the frozen document's params and the WFC_PARAMS the "
                  "method read are one dict, defaults included")
def test_run_rows_frozen_params_and_wfc_params_are_one_dict(
        register_fixture_methods, tmp_path):
    from wfc import layout
    from wfc.execution import run_pipeline
    from wfc.persistence import Run, RunOutput, get_session

    口 = Step(step_num=1, name="Register echo_params and a sample",
             purpose="Production registration into the fixture env")
    project_dir = register_fixture_methods
    register_test_method(project_dir=project_dir, module_name=MODULE,
                         method_dir=FIXTURE_METHODS_DIR / METHOD,
                         method_name=METHOD)
    create_sample_csv(project_dir, SAMPLE, num_rows=3)
    path = tmp_path / "echo-params.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    口 = Step(step_num=2, name="Launch the pipeline from the CLI path",
             purpose="The method runs in its container and writes what it read")
    run_pipeline(pipeline_path=str(path), project_root=str(project_dir),
                 wfc_root=str(project_dir), cores=1, pipeline_id=PIPELINE_ID,
                 archive=False)

    口 = Step(step_num=3, name="Read the three records",
             purpose="The run rows, the frozen document's params that run, and "
                     "for each run row the output that same run wrote")
    with get_session() as session:
        rows = session.exec(select(Run).where(Run.pipeline_id == PIPELINE_ID)).all()
        assert rows and all(r.status == "completed" for r in rows), [
            (r.id, r.status) for r in rows]
        # A run that executed holds the one output its method wrote; a cache
        # hit's audit row ran nothing and holds none.
        outputs = session.exec(select(RunOutput).join(Run, RunOutput.run_id == Run.id)
                               .where(Run.pipeline_id == PIPELINE_ID)).all()
    read = {}
    for output in outputs:
        assert output.run_id not in read, f"run {output.run_id} has two outputs"
        read[output.run_id] = json.loads(
            _artifact(project_dir, output.artifact_path).read_text(encoding="utf-8"))
    by_id = {r.id: r for r in rows}
    frozen = json.loads(layout.pipeline_doc_path(project_dir, PIPELINE_ID)
                        .read_text(encoding="utf-8"))
    own = {n["id"]: n.get("params") for n in frozen["nodes"]}
    frozen_run = [*frozen["param_sets"]["e"].values(), own["e"], own["n"]]

    口 = Step(step_num=4, name="One dict per run, defaults included",
             purpose="e's two variants, e's and n's own params, each filled; "
                     "each run's row is the WFC_PARAMS that run read")
    expected = {_canonical(p) for p in (
        {"count": 1, **_DEFAULTS},
        {"count": 2, **_DEFAULTS},
        {"count": 4, **_DEFAULTS, "sigma": 0.25},
        {"count": 3, **_DEFAULTS, "label": "x"})}
    assert {_canonical(r.params) for r in rows} == expected
    assert {_canonical(p) for p in frozen_run} == expected
    assert {_canonical(by_id[run_id].params) for run_id in read} == expected
    for run_id, wfc_params in read.items():
        assert wfc_params == by_id[run_id].params, (run_id, wfc_params)
