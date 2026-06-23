"""
Unit tests: save_module requires an explicit ext — no silent .parquet default.

Story: Before the fix, ``save_module("feature_set_list", df)`` would silently
write ``feature_set_list.parquet`` while Snakemake expected ``feature_set_list.csv``
(from slot_outputs).  The rule would fail with no useful error — just a missing
output.

After the fix:
  - ``ext`` is a required argument on ``save_module``.
  - The ``@wfc_method`` dispatcher reads ``slot_outputs`` from ``_run_context.json``
    (written by the orchestrator before the method subprocess starts) and always
    passes the correct ext — no DB, no inference.
  - A caller that omits ext gets a ``TypeError`` immediately, not a silent wrong file.
"""

import json
import os

import pandas as pd
import pytest

from dflow.core.decorators import task, Step

from wfc.init import init_project


@task(purpose="save_module requires ext; the dispatcher supplies it from _run_context.json")
class TestSaveModuleRequiresExt:
    """save_module requires ext; the dispatcher supplies it from _run_context.json."""

    def test_save_module_without_ext_raises(self, tmp_project, monkeypatch):
        """Calling save_module without ext raises TypeError — silent .parquet write is gone."""
        口 = Step(
            step_num=1,
            name="Bootstrap RunContext",
            purpose="Set minimum PM_* env vars so RunContext.__init__ succeeds")
        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(tmp_project))

        from wfc.wfc_context import RunContext
        ctx = RunContext()

        口 = Step(
            step_num=2,
            name="Call save_module without ext",
            purpose="Confirm a TypeError is raised rather than a silent .parquet write")
        with pytest.raises(TypeError):
            ctx.save_module("feature_set_list", pd.DataFrame({"a": [1]}))

    def test_save_module_with_ext_writes_correct_filename(self, tmp_project, monkeypatch):
        """save_module(name, data, ext='.csv') writes name.csv and nothing else."""
        口 = Step(
            step_num=1,
            name="Bootstrap RunContext",
            purpose="Set minimum PM_* env vars so RunContext.__init__ succeeds")
        init_project(tmp_project)
        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(tmp_project))

        from wfc.wfc_context import RunContext
        ctx = RunContext()

        口 = Step(
            step_num=2,
            name="Save with explicit ext",
            purpose="Confirm the correct filename is written and no .parquet fallback exists")
        ctx.save_module("feature_set_list", pd.DataFrame({"gene": ["KRAS"]}), ext=".csv")

        assert (tmp_project / "feature_set_list.csv").exists()
        assert not (tmp_project / "feature_set_list.parquet").exists()

    def test_dispatcher_passes_ext_from_slot_outputs(self, tmp_project, monkeypatch):
        """The @wfc_method dispatcher reads slot_outputs from _run_context.json and
        passes the correct ext to save_module — no DB or sqlmodel required."""
        口 = Step(
            step_num=1,
            name="Write _run_context.json",
            purpose="Simulate the orchestrator writing context before launching the method subprocess",
            inputs="_run_context.json with slot_outputs mapping name → filename")
        init_project(tmp_project)
        ctx_json = {
            "method_name": "sirna_filter",
            "module_name": "data_preprocessing",
            "slot_outputs": {"feature_set_list": "feature_set_list.csv"},
        }
        (tmp_project / "_run_context.json").write_text(json.dumps(ctx_json))

        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(tmp_project))
        monkeypatch.setenv("WFC_PARAMS", "{}")

        口 = Step(
            step_num=2,
            name="Run dispatcher",
            purpose="Execute _execute_wfc_method with a fake function that returns a named DataFrame output",
            critical="No sqlmodel in scope — confirms DB-free path works end-to-end")
        from wfc.method import _execute_wfc_method

        def fake_method(input_data, params):
            return {"feature_set_list": pd.DataFrame({"gene": ["KRAS"]})}, {}

        _execute_wfc_method(fake_method)

        口 = Step(
            step_num=3,
            name="Assert correct output file",
            purpose="Confirm .csv was written and .parquet was not created")
        assert (tmp_project / "feature_set_list.csv").exists()
        assert not (tmp_project / "feature_set_list.parquet").exists()
