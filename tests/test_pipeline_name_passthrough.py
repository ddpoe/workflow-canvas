"""Provider surfaces the submission-time pipeline name to the canvas.

The Pipelines-view card title comes from ``WfcRun.pipelineName``, which
the provider reads from the on-disk pipeline record
(``pipeline.editable.json`` first, ``pipeline.json`` as fallback).
Legacy or unnamed pipelines surface None so the frontend falls back to
the short pipeline id instead of inventing a label from a child run.
"""

from __future__ import annotations

import json

from wfc.canvas.wfc_provider import WfcProvider
from wfc.database import get_session
from wfc.models import Method, Module, Run


def test_get_all_runs_surfaces_pipeline_name(tmp_project):
    named_pid = "pipe-named"
    unnamed_pid = "pipe-unnamed"

    with get_session() as s:
        mod = Module(name="m", description="test module")
        s.add(mod)
        s.commit()
        s.refresh(mod)
        meth = Method(
            name="plot",
            module_id=mod.id,
            script_path="methods/plot/plot.py",
            env="container:demo",
        )
        s.add(meth)
        s.commit()
        s.refresh(meth)
        for pid in (named_pid, unnamed_pid):
            s.add(Run(method_id=meth.id, sample="S1", pipeline_id=pid, status="completed"))
        s.commit()

    # Named pipeline: sidecar carries the Builder toolbar name. The
    # unnamed pipeline gets no on-disk record at all (legacy shape).
    sidecar_dir = tmp_project / ".runs" / "pipelines" / named_pid
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "pipeline.editable.json").write_text(
        json.dumps({"name": "demo", "nodes": [], "links": [], "samples": []})
    )

    prov = WfcProvider(str(tmp_project))
    prov.load()
    by_pid = {r["pipelineId"]: r for r in prov.get_all_runs()}

    assert by_pid[named_pid]["pipelineName"] == "demo"
    assert by_pid[unnamed_pid]["pipelineName"] is None
