"""The demo project's options picker: ``__demo__summarize.group_by`` lists
the names ``__demo__label`` writes.

Catalog case in ``docs/system/canvas-api/catalog.json``: the demo's
output-options case.
"""

from __future__ import annotations

import json
import shutil

from axiom_annotations import workflow

from tests.fixtures.routes import register_test_method, write_env_record
from wfc import layout
from wfc.demo.scaffold import ASSETS_DIR, DEMO_ENV, DEMO_MODULES, DEMO_PREFIX

#: label and summarize share one demo module.
LABELING = next(m for m, sources in DEMO_MODULES.items() if "label" in sources)


def _register_demo_method(root, src_root, source_name: str) -> None:
    """Register one shipped demo method under its prefixed name.

    The asset directory's ``method.yaml`` and script are copied as they
    ship; the script is renamed to the prefixed method name so the
    registration's ``{method_name}.py`` probe finds it, as ``wfc demo``'s
    ``script_name`` does.
    """
    method = f"{DEMO_PREFIX}{source_name}"
    asset = ASSETS_DIR / layout.METHODS_DIR_NAME / source_name
    method_dir = src_root / method
    method_dir.mkdir()
    shutil.copy2(asset / "method.yaml", method_dir / "method.yaml")
    shutil.copy2(asset / f"{source_name}.py", method_dir / f"{method}.py")
    register_test_method(root, module_name=LABELING, method_dir=method_dir,
                         allow_reserved=True)


@workflow(
    purpose="In the demo project, __demo__summarize's group_by takes its "
            "options from its data input, and the upstream wired there in "
            "the shipped pipeline, __demo__label at the pipeline's values, "
            "answers the label column built from label_column beside the "
            "four columns the samples carry",
)
def test_demo_group_by_lists_the_label_column_beside_the_sample_columns(
        canvas_client, tmp_project, tmp_path_factory):
    write_env_record(tmp_project, DEMO_ENV)
    src = tmp_path_factory.mktemp("demo-sources")
    for source_name in ("label", "summarize"):
        _register_demo_method(tmp_project, src, source_name)
    pipeline = json.loads((ASSETS_DIR / "pipeline.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in pipeline["nodes"]}
    summarize = next(n for n in pipeline["nodes"]
                     if n["method"] == f"{DEMO_PREFIX}summarize")

    palette = canvas_client.get("/api/modules")
    group_by = (palette.json()[LABELING]["methods"][f"{DEMO_PREFIX}summarize"]
                ["params_schema"]["group_by"])
    link = next(lk for lk in pipeline["links"]
                if lk["target"] == summarize["id"]
                and lk["targetHandle"] == group_by["options_from"])
    upstream = nodes[link["source"]]
    resp = canvas_client.post(
        f"/api/contracts/{upstream['module']}.{upstream['method']}/output_options",
        json={"slot": link["sourceHandle"], "param_sets": [upstream["params"]]})

    assert palette.status_code == 200, palette.text
    assert group_by["options_from"] == "data"
    assert upstream["method"] == f"{DEMO_PREFIX}label"
    assert upstream["module"] == LABELING
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "fixed": ["id", "intensity", "area", "quality"],
        "built": [{"params": ["label_column"], "names": ["label"]}],
        "patterns": [],
        "all": ["id", "intensity", "area", "quality", "label"],
    }
