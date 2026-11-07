"""Canvas API route tests: the builder's node palette and a slot's output options.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``builder-module-palette``
and the output-options cases.
"""

from __future__ import annotations

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
    # Each module's colour is the one the Registry lists (its stored colour).
    colors = {m["name"]: m["color"] for m in
              canvas_client.get("/api/registry/modules").json()["modules"]}
    assert all(c and c.startswith("#") for c in colors.values()), colors

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
            "color": colors["segmentation"],
            "drafts": {},
        },
        "export": {
            "description": "export",
            "color": colors["export"],
            "drafts": {},
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


#: The output options every run case declares: one fixed name and one name
#: built from the ``channels`` parameter. The second version renames both.
RUN_OPTIONS_V1 = {"fixed": ["label"],
                  "from_params": [{"params": ["channels"], "pattern": "mean_{}"}]}
RUN_OPTIONS_V2 = {"fixed": ["label", "perimeter"],
                  "from_params": [{"params": ["channels"], "pattern": "median_{}"}]}


@pytest.fixture
def options_run_id(tmp_project, monkeypatch):
    """Complete a ``features.regionprops`` run on v1, then register and run v2.

    Both runs go through production's own phases. The second declares
    different output options, so registering it mints v2 and the first run
    stays on v1.

    Returns:
        The id of the run on v1, whose recorded params name one channel.
    """
    first = completed_run(
        tmp_project, monkeypatch=monkeypatch, method="regionprops",
        module="features", sample="s1", params={"channels": ["cy5"]},
        outputs={"table": ".csv"}, output_options={"table": RUN_OPTIONS_V1},
    )
    completed_run(
        tmp_project, monkeypatch=monkeypatch, method="regionprops",
        module="features", sample="s1", params={"channels": ["gfp"]},
        outputs={"table": ".csv"}, output_options={"table": RUN_OPTIONS_V2},
        pipeline_id="options-v2", name="options-v2",
    )
    return first.run_id


@workflow(
    purpose="A Run Reference upstream's options are the options its run's own "
            "version declares, built from the params the run ran with: a run "
            "completed on v1 answers v1's names after v2 is registered, "
            "whatever method the path names, and names the run's module and "
            "method for the picker's header, while the method itself answers "
            "v2's",
)
def test_a_run_reference_answers_its_runs_version_options(
        canvas_client, options_run_id, monkeypatch):
    bind_provider(monkeypatch, object())

    from_run = canvas_client.post(
        "/api/contracts/__run_reference__/output_options",
        json={"slot": "table", "run_id": str(options_run_id)})
    newest = canvas_client.post(
        "/api/contracts/features.regionprops/output_options",
        json={"slot": "table", "param_sets": [{"channels": ["cy5"]}]})

    assert from_run.status_code == 200, from_run.text
    assert from_run.json() == {
        "fixed": ["label"],
        "built": [{"params": ["channels"], "names": ["mean_cy5"]}],
        "patterns": [],
        "all": ["label", "mean_cy5"],
        "module": "features",
        "method": "regionprops",
    }
    assert newest.status_code == 200, newest.text
    assert newest.json()["all"] == ["label", "perimeter", "median_cy5"]


def _options_yaml(options: str, params: str = "") -> str:
    """A ``features.regionprops`` method.yaml with one ``table`` output.

    Args:
        options: The output's ``options`` block, already indented under it.
        params: The ``params`` block, or empty for none.
    """
    return ("inputs:\n  image:\n    type: .tif\n"
            "outputs:\n  table:\n    type: .csv\n    options:\n"
            f"{options}{params}env: seg\n")


#: Two parameters with defaults, and an options block that builds names from
#: one of them alone and from both together.
_BUILT_PARAMS = ("params:\n"
                 "  channel:\n    type: str\n    default: dapi\n"
                 "  stat:\n    type: str\n    default: mean\n")
_BUILT_OPTIONS = ("      fixed: [label, area]\n"
                  "      from_params:\n"
                  "        - params: [channel]\n          pattern: 'mean_{}'\n"
                  "        - params: [stat, channel]\n          pattern: '{}_{}'\n"
                  "      patterns: ['texture_*', 'texture_*']\n")


@pytest.fixture
def built_options_method(tmp_project, tmp_path_factory):
    """Register ``features.regionprops`` declaring ``_BUILT_OPTIONS`` and its params."""
    write_env_record(tmp_project, "seg")
    src = tmp_path_factory.mktemp("options-sources")
    _register_palette_method(tmp_project, src, "features", "regionprops",
                             _options_yaml(_BUILT_OPTIONS, _BUILT_PARAMS))


_OPTIONS_URL = "/api/contracts/features.regionprops/output_options"


@workflow(
    purpose="An upstream left at its declared defaults (no param sets) still "
            "answers built names: the defaults are filled before the options "
            "are expanded",
)
def test_declared_defaults_alone_build_names(canvas_client, built_options_method):
    resp = canvas_client.post(_OPTIONS_URL, json={"slot": "table"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "fixed": ["label", "area"],
        "built": [{"params": ["channel"], "names": ["mean_dapi"]}],
        "patterns": ["texture_*"],
        "all": ["label", "area", "mean_dapi"],
    }


@workflow(
    purpose="An upstream's base values, two variants and a per-sample override "
            "answer each name once: every set is filled with the declared "
            "defaults, each from_params entry is its own built group in "
            "first-seen order, a name a group already gave is not repeated, "
            "all is fixed then built in that order, and patterns come back "
            "literal",
)
def test_every_param_set_contributes_and_each_name_appears_once(
        canvas_client, built_options_method):
    param_sets = [
        {"channel": "dapi"},              # base values
        {"channel": "gfp"},               # variant a
        {"channel": ["gfp", "cy5"]},      # variant b
        {"stat": "max"},                  # a sample's override; channel defaults
    ]

    resp = canvas_client.post(_OPTIONS_URL,
                              json={"slot": "table", "param_sets": param_sets})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "fixed": ["label", "area"],
        "built": [
            {"params": ["channel"], "names": ["mean_dapi", "mean_gfp", "mean_cy5"]},
            {"params": ["stat", "channel"], "names": ["max_dapi"]},
        ],
        "patterns": ["texture_*"],
        "all": ["label", "area", "mean_dapi", "mean_gfp", "mean_cy5", "max_dapi"],
    }


@workflow(
    purpose="An options picker fed by a pinned upstream node reads that "
            "version's output options: the route takes the upstream node's "
            "version, answers the newest version's options without one, and "
            "refuses a version no entry carries with a 404 naming it",
)
def test_output_options_are_the_pinned_upstream_versions(
        canvas_client, tmp_project, tmp_path_factory):
    from wfc.registration.method_version import read_version_list

    write_env_record(tmp_project, "seg")
    src = tmp_path_factory.mktemp("options-sources")
    _register_palette_method(tmp_project, src, "features", "regionprops",
                             _options_yaml("      fixed: [label, area]\n"))
    (src / "features" / "regionprops" / "method.yaml").write_text(
        _options_yaml("      fixed: [label, perimeter]\n"), encoding="utf-8")
    # Only the output's options change: that alone mints v2.
    register_test_method(tmp_project, module_name="features",
                         method_dir=src / "features" / "regionprops")
    versions = read_version_list(tmp_project, "features", "regionprops")
    assert len(versions) == 2
    v1, _ = versions

    pinned = canvas_client.post(_OPTIONS_URL,
                                json={"slot": "table", "version": v1.identity})
    newest = canvas_client.post(_OPTIONS_URL, json={"slot": "table"})
    unknown = canvas_client.post(_OPTIONS_URL,
                                 json={"slot": "table", "version": "f" * 64})

    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["all"] == ["label", "area"]
    assert newest.status_code == 200, newest.text
    assert newest.json()["all"] == ["label", "perimeter"]
    assert unknown.status_code == 404, unknown.text
    assert "f" * 64 in unknown.json()["detail"]


@pytest.mark.parametrize(
    "method_full, body, expected_status",
    [
        pytest.param("regionprops", {"slot": "table"}, 400, id="no-dot"),
        pytest.param("features.regionprops", {"slot": "table", "run_id": "abc"},
                     400, id="run-id-not-integer"),
        pytest.param("imaging.regionprops", {"slot": "table"}, 404, id="unknown-module"),
        pytest.param("features.watershed", {"slot": "table"}, 404, id="unknown-method"),
        pytest.param("features.regionprops", {"slot": "table", "run_id": "999999"},
                     404, id="unknown-run"),
        pytest.param("features.regionprops", {"param_sets": []}, 422, id="no-slot"),
        pytest.param("features.regionprops", {"slot": "table", "param_sets": "x"},
                     422, id="param-sets-not-a-list"),
    ],
)
@workflow(
    purpose="The options route refuses a method name with no dot and a "
            "non-integer run id with 400, an unknown module, method or run "
            "with 404, and a body with no slot or with param sets that are "
            "not a list of objects with 422",
)
def test_output_options_refuse_malformed_and_unknown_inputs(
    canvas_client, built_options_method, monkeypatch, method_full, body,
    expected_status
):
    bind_provider(monkeypatch, object())

    resp = canvas_client.post(f"/api/contracts/{method_full}/output_options",
                              json=body)

    assert resp.status_code == expected_status, resp.text
