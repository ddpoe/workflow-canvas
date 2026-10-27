"""Canvas API route tests: the builder's node palette and a slot's output columns.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``builder-module-palette``
and ``columns-output-columns``.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import completed_run, register_test_method, write_env_record
from wfc.canvas import state as canvas_state


def _register_palette_method(root, src_root, module, name, yaml_text, *,
                             description=None):
    """Write a method's source and ``method.yaml``, then register it through production."""
    method_dir = src_root / module / name
    method_dir.mkdir(parents=True)
    (method_dir / f"{name}.py").write_text("print('ok')\n", encoding="utf-8")
    (method_dir / "method.yaml").write_text(yaml_text, encoding="utf-8")
    register_test_method(root, module_name=module, method_dir=method_dir,
                         module_description=description)


def _palette_version(root, module, method, label, contract, *, newest):
    """The palette's entry for one version: identity and date read from its list."""
    from wfc.registration.method_version import read_version_list

    entry = next(e for e in read_version_list(root, module, method)
                 if e.label == label)
    return {"label": label, "identity": entry.identity,
            "registered_at": entry.registered_at, "env": entry.env_name,
            "newest": newest, **contract}


_WATERSHED_V1 = ("inputs:\n  image:\n    type: .tif\n"
                 "outputs:\n  labels:\n    type: .npy\n"
                 "params:\n  sigma:\n    type: float\n"
                 "executor: python\nenv: seg\n")
_WATERSHED_V2 = ("inputs:\n  image:\n    type: .tif\n"
                 "outputs:\n  labels:\n    type: .npy\n  mask:\n    type: .npy\n"
                 "params:\n  min_size:\n    type: int\n"
                 "executor: python\nenv: seg\n")


@workflow(
    purpose="The palette nests every registered module's methods, each with "
            "every version in label order (label, identity, registration "
            "date, env, the newest marked) and that version's own contract; "
            "the method's own fields are its newest version's, a method that "
            "declares no outputs, params or executor gets empty ones and the "
            "python executor, and a module's name stands in for a missing "
            "description",
)
def test_palette_lists_each_methods_versions_with_their_contracts(
        canvas_client, tmp_project, tmp_path_factory):
    write_env_record(tmp_project, "seg")
    write_env_record(tmp_project, "io", digest="b" * 64)
    src = tmp_path_factory.mktemp("palette-sources")
    _register_palette_method(tmp_project, src, "segmentation", "watershed",
                             _WATERSHED_V1, description="Cell segmentation.")
    (src / "segmentation" / "watershed" / "method.yaml").write_text(
        _WATERSHED_V2, encoding="utf-8")
    register_test_method(tmp_project, module_name="segmentation",
                         method_dir=src / "segmentation" / "watershed")
    _register_palette_method(tmp_project, src, "segmentation", "threshold",
                             "inputs:\n  image:\n    type: .tif\nenv: seg\n")
    _register_palette_method(
        tmp_project, src, "export", "to_csv",
        "inputs:\n  labels:\n    type: .npy\n"
        "outputs:\n  table:\n    type: .csv\n"
        "executor: nextflow\nenv: io\n")
    watershed_v1 = {"inputs": {"image": {"type": ".tif"}},
                    "outputs": {"labels": {"type": ".npy"}},
                    "params_schema": {"sigma": {"type": "float"}},
                    "executor": "python"}
    watershed_v2 = {"inputs": {"image": {"type": ".tif"}},
                    "outputs": {"labels": {"type": ".npy"}, "mask": {"type": ".npy"}},
                    "params_schema": {"min_size": {"type": "int"}},
                    "executor": "python"}
    threshold = {"inputs": {"image": {"type": ".tif"}}, "outputs": {},
                 "params_schema": {}, "executor": "python"}
    to_csv = {"inputs": {"labels": {"type": ".npy"}},
              "outputs": {"table": {"type": ".csv"}},
              "params_schema": {}, "executor": "nextflow"}

    resp = canvas_client.get("/api/modules")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "segmentation": {
            "description": "Cell segmentation.",
            "methods": {
                "watershed": {
                    **watershed_v2,
                    "description": "segmentation — watershed",
                    "env": "seg",
                    "versions": [
                        _palette_version(tmp_project, "segmentation", "watershed",
                                         1, watershed_v1, newest=False),
                        _palette_version(tmp_project, "segmentation", "watershed",
                                         2, watershed_v2, newest=True),
                    ],
                },
                "threshold": {
                    **threshold,
                    "description": "segmentation — threshold",
                    "env": "seg",
                    "versions": [_palette_version(
                        tmp_project, "segmentation", "threshold", 1, threshold,
                        newest=True)],
                },
            },
        },
        "export": {
            "description": "export",
            "methods": {
                "to_csv": {
                    **to_csv,
                    "description": "export — to_csv",
                    "env": "io",
                    "versions": [_palette_version(
                        tmp_project, "export", "to_csv", 1, to_csv, newest=True)],
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


def _columns_yaml(strict: list[str]) -> str:
    return ("inputs:\n  image:\n    type: .tif\n"
            "outputs:\n  table:\n    type: .csv\n    columns:\n"
            f"      strict: [{', '.join(strict)}]\n"
            "env: seg\n")


@workflow(
    purpose="A column_of_input dropdown fed by a pinned upstream node reads "
            "that version's output columns: the route takes the upstream "
            "node's version, answers the newest version's columns without "
            "one, and refuses a version no entry carries with a 404 naming it",
)
def test_output_columns_are_the_pinned_upstream_versions(
        canvas_client, tmp_project, tmp_path_factory):
    from wfc.registration.method_version import read_version_list

    write_env_record(tmp_project, "seg")
    src = tmp_path_factory.mktemp("columns-sources")
    _register_palette_method(tmp_project, src, "features", "regionprops",
                             _columns_yaml(["label", "area"]))
    (src / "features" / "regionprops" / "method.yaml").write_text(
        _columns_yaml(["label", "perimeter"]), encoding="utf-8")
    # Only the output's columns change: that alone mints v2.
    register_test_method(tmp_project, module_name="features",
                         method_dir=src / "features" / "regionprops")
    versions = read_version_list(tmp_project, "features", "regionprops")
    assert len(versions) == 2
    v1, _ = versions
    url = "/api/contracts/features.regionprops/output_columns"

    pinned = canvas_client.get(url, params={"slot": "table", "version": v1.identity})
    newest = canvas_client.get(url, params={"slot": "table"})
    unknown = canvas_client.get(url, params={"slot": "table", "version": "f" * 64})

    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["all"] == ["area", "label"]
    assert newest.status_code == 200, newest.text
    assert newest.json()["all"] == ["label", "perimeter"]
    assert unknown.status_code == 404, unknown.text
    assert "f" * 64 in unknown.json()["detail"]


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
