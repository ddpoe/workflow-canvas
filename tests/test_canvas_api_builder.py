"""Canvas API route tests: the builder's node palette and a slot's output columns.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``builder-module-palette``
and ``columns-output-columns``.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import workflow
from sqlmodel import Session

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import completed_run
from wfc.canvas import state as canvas_state
from wfc.persistence import Method, MethodContract, Module


@workflow(
    purpose="The palette nests every module's methods with their contracts, fills "
            "the fixed version and description, gives a method with no contract "
            "empty slots and the python executor, and lets a module's name stand "
            "in for a missing description",
)
def test_palette_nests_each_modules_methods_and_contracts(canvas_client, canvas_db):
    with Session(canvas_db) as session:
        seg = Module(name="segmentation", description="Cell segmentation.")
        export = Module(name="export")
        session.add_all([seg, export])
        session.flush()
        watershed = Method(name="watershed", module_id=seg.id, env="container:seg")
        threshold = Method(name="threshold", module_id=seg.id, env="container:seg")
        to_csv = Method(name="to_csv", module_id=export.id, env="container:io")
        session.add_all([watershed, threshold, to_csv])
        session.flush()
        session.add_all([
            MethodContract(method_id=watershed.id,
                           input_slots={"image": {"type": ".tif"}},
                           output_slots={"labels": {"type": ".npy"}},
                           params_schema={"sigma": {"type": "float"}},
                           executor="python"),
            MethodContract(method_id=to_csv.id,
                           input_slots={"labels": {"type": ".npy"}},
                           output_slots={"table": {"type": ".csv"}},
                           params_schema={},
                           executor="nextflow"),
        ])
        session.commit()

    resp = canvas_client.get("/api/modules")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "segmentation": {
            "description": "Cell segmentation.",
            "methods": {
                "watershed": {
                    "inputs": {"image": {"type": ".tif"}},
                    "outputs": {"labels": {"type": ".npy"}},
                    "version": "1.0.0",
                    "description": "segmentation — watershed",
                    "params_schema": {"sigma": {"type": "float"}},
                    "env": "container:seg",
                    "executor": "python",
                },
                "threshold": {
                    "inputs": {},
                    "outputs": {},
                    "version": "1.0.0",
                    "description": "segmentation — threshold",
                    "params_schema": {},
                    "env": "container:seg",
                    "executor": "python",
                },
            },
        },
        "export": {
            "description": "export",
            "methods": {
                "to_csv": {
                    "inputs": {"labels": {"type": ".npy"}},
                    "outputs": {"table": {"type": ".csv"}},
                    "version": "1.0.0",
                    "description": "export — to_csv",
                    "params_schema": {},
                    "env": "container:io",
                    "executor": "nextflow",
                },
            },
        },
    }


#: The slot declaration every columns case resolves against.
COLUMNS = {
    "strict": ["label", "area"],
    "from_params": [{"params": ["channels"], "pattern": "mean_{}"}],
    "patterns": ["^texture_.*"],
}


@pytest.fixture
def columns_run_id(tmp_project, monkeypatch):
    """Register ``features.regionprops`` with a column-declaring slot and one completed run.

    Returns:
        The id of the completed run, whose recorded params name one channel.
    """
    return completed_run(
        tmp_project, monkeypatch=monkeypatch, method="regionprops",
        module="features", sample="s1", params={"channels": ["cy5"]},
        outputs={"table": ".csv"}, output_columns={"table": COLUMNS},
    ).run_id


@pytest.mark.parametrize(
    "query, expected_all",
    [
        pytest.param({"params": json.dumps({"channels": ["dapi", "gfp"]})},
                     ["area", "label", "mean_dapi", "mean_gfp"], id="literal-params"),
        pytest.param({"run_id": "{run_id}"}, ["area", "label", "mean_cy5"], id="run-id",
                     marks=pytest.mark.xfail(
                         strict=True,
                         reason="The run_id branch builds the method name from "
                                "run.module and run.method; Run has no module field, "
                                "so a valid run_id answers 500")),
        pytest.param({}, ["area", "label"], id="neither"),
    ],
)
@workflow(
    purpose="A slot's declared columns come back as strict, from_params and "
            "patterns, with all the sorted union resolved from literal params, "
            "from a referenced run's recorded params and its own method, or from "
            "no params",
)
def test_output_columns_resolve_from_params_a_run_or_neither(
    canvas_client, columns_run_id, monkeypatch, query, expected_all
):
    bind_provider(monkeypatch, object())
    query = {k: v.replace("{run_id}", str(columns_run_id)) for k, v in query.items()}

    resp = canvas_client.get(
        "/api/contracts/features.regionprops/output_columns",
        params={"slot": "table", **query},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "strict": COLUMNS["strict"],
        "from_params": COLUMNS["from_params"],
        "patterns": COLUMNS["patterns"],
        "all": expected_all,
    }


@pytest.mark.parametrize(
    "method_full, query, expected_status",
    [
        pytest.param("regionprops", {}, 400, id="no-dot"),
        pytest.param("features.regionprops", {"params": "{not json"}, 400, id="params-not-json"),
        pytest.param("features.regionprops", {"run_id": "abc"}, 400, id="run-id-not-integer"),
        pytest.param("imaging.regionprops", {}, 404, id="unknown-module"),
        pytest.param("features.watershed", {}, 404, id="unknown-method"),
        pytest.param("features.regionprops", {"run_id": "999999"}, 404, id="unknown-run"),
    ],
)
@workflow(
    purpose="The columns route refuses a method name with no dot, params that are "
            "not JSON and a non-integer run id with 400, and an unknown module, "
            "method or run with 404",
)
def test_output_columns_refuse_malformed_and_unknown_inputs(
    canvas_client, columns_run_id, monkeypatch, method_full, query, expected_status
):
    bind_provider(monkeypatch, object())

    resp = canvas_client.get(
        f"/api/contracts/{method_full}/output_columns",
        params={"slot": "table", **query},
    )

    assert resp.status_code == expected_status, resp.text
