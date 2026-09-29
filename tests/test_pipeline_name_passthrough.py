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
from wfc.persistence import get_session, Method, Module, Run


def test_get_all_runs_surfaces_none_for_pipeline_without_sidecar(tmp_project):
    """Legacy fallback: a run whose pipeline has no on-disk record at all
    surfaces ``pipelineName`` None, so the frontend falls back to the short
    pipeline id instead of inventing a label from a child run.

    The positive writer-reader path (a real submission writing the sidecar
    that the provider then reads back) is exercised in
    tests/test_canvas_pipeline_document_endpoint.py.
    """
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
        s.add(Run(method_id=meth.id, sample="S1", pipeline_id=unnamed_pid, status="completed"))
        s.commit()

    # No .runs/pipelines/<pid>/ record on disk for this pipeline (legacy shape).
    prov = WfcProvider(str(tmp_project))
    prov.load()
    by_pid = {r["pipelineId"]: r for r in prov.get_all_runs()}

    assert by_pid[unnamed_pid]["pipelineName"] is None
