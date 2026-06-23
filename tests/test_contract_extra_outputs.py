"""
Unit tests: ContractViolation.extra_outputs field and the strict-raise behaviour
when a @wfc_method returns a key not declared in the module contract.

Story: Before the fix, returning an undeclared output key (e.g. "ghost") would
silently call ctx.save(), writing an untracked side-file with no error.

After the fix:
  - ContractViolation has an ``extra_outputs`` field populated with the offending key.
  - The dispatcher raises immediately, before any file is written.
  - The error message includes an "Undeclared outputs returned" section.
  - ctx.save() remains available as the explicit escape hatch for true free-form
    side-files — the dispatcher no longer reaches for it silently.
"""

import json

import pandas as pd
import pytest

from dflow.core.decorators import task, Step

from wfc.init import init_project
from wfc.method import ContractViolation


@task(purpose="ContractViolation.extra_outputs field and strict-raise on undeclared return keys")
class TestExtraOutputsContractViolation:
    """ContractViolation.extra_outputs field and strict-raise on undeclared return keys."""

    # ------------------------------------------------------------------
    # ContractViolation constructor tests (no I/O required)
    # ------------------------------------------------------------------

    def test_extra_outputs_field_stored(self):
        """extra_outputs passed to ContractViolation is stored on the instance."""
        口 = Step(
            step_num=1,
            name="Construct with extra_outputs",
            purpose="Verify the field is initialised and accessible")
        exc = ContractViolation(
            method="sirna_filter",
            extra_outputs=["ghost"])

        assert exc.extra_outputs == ["ghost"]

    def test_extra_outputs_in_error_message(self):
        """Error message contains the 'Undeclared outputs returned' section with the key."""
        口 = Step(
            step_num=1,
            name="Construct ContractViolation",
            purpose="Build exception with one undeclared output key")
        exc = ContractViolation(
            method="sirna_filter",
            module="data_preprocessing",
            extra_outputs=["ghost"])

        口 = Step(
            step_num=2,
            name="Assert message content",
            purpose="Confirm the undeclared-outputs section and key name appear in str(exc)")
        msg = str(exc)
        assert "Undeclared outputs returned" in msg
        assert "ghost" in msg

    def test_no_extra_outputs_section_absent(self):
        """When extra_outputs is empty the 'Undeclared outputs' section is not printed."""
        口 = Step(
            step_num=1,
            name="Construct with no extras",
            purpose="Build a missing-outputs violation (no extra_outputs) and inspect message")
        exc = ContractViolation(
            method="sirna_filter",
            missing_outputs=["filtered"],
            available_outputs=[])

        assert "Undeclared outputs returned" not in str(exc)

    def test_ctx_save_hint_in_message(self):
        """Error message mentions ctx.save() as the explicit escape hatch."""
        口 = Step(
            step_num=1,
            name="Construct with extra_outputs",
            purpose="Verify the hint pointing developers to ctx.save() is present")
        exc = ContractViolation(method="sirna_filter", extra_outputs=["ghost"])

        assert "ctx.save()" in str(exc)

    # ------------------------------------------------------------------
    # Dispatcher integration tests (require tmp_project + env wiring)
    # ------------------------------------------------------------------

    def test_execute_raises_on_undeclared_output(self, tmp_project, monkeypatch):
        """_execute_wfc_method raises ContractViolation when the function returns
        a key not present in the slot_outputs contract."""
        口 = Step(
            step_num=1,
            name="Write _run_context.json",
            purpose="Declare one contracted output ('filtered') so the dispatcher has a contract",
            inputs="_run_context.json with slot_outputs mapping one name → filename")
        init_project(tmp_project)
        ctx_json = {
            "method_name": "sirna_filter",
            "module_name": "data_preprocessing",
            "slot_outputs": {"filtered": "filtered.csv"},
        }
        (tmp_project / "_run_context.json").write_text(json.dumps(ctx_json))

        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(tmp_project))
        monkeypatch.setenv("WFC_PARAMS", "{}")

        口 = Step(
            step_num=2,
            name="Run dispatcher with undeclared key",
            purpose="Return both the contracted 'filtered' key and an undeclared 'ghost' key",
            critical="Must raise before any file is written for 'ghost'")
        from wfc.method import _execute_wfc_method

        def fake_method(input_data, params):
            df = pd.DataFrame({"gene": ["KRAS"]})
            return {"filtered": df, "ghost": df}, {}

        with pytest.raises(ContractViolation) as exc_info:
            _execute_wfc_method(fake_method)

        口 = Step(
            step_num=3,
            name="Assert violation fields",
            purpose="Confirm extra_outputs is populated with 'ghost'")
        assert "ghost" in exc_info.value.extra_outputs

    def test_execute_no_free_form_fallback_file(self, tmp_project, monkeypatch):
        """No file is written for the undeclared key — the old silent ctx.save() path is gone."""
        口 = Step(
            step_num=1,
            name="Write _run_context.json",
            purpose="Declare one contracted output so the dispatcher has a known contract")
        init_project(tmp_project)
        ctx_json = {
            "method_name": "sirna_filter",
            "module_name": "data_preprocessing",
            "slot_outputs": {"filtered": "filtered.csv"},
        }
        (tmp_project / "_run_context.json").write_text(json.dumps(ctx_json))

        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(tmp_project))
        monkeypatch.setenv("WFC_PARAMS", "{}")

        口 = Step(
            step_num=2,
            name="Run dispatcher and catch violation",
            purpose="Trigger the raise for the undeclared 'ghost' key")
        from wfc.method import _execute_wfc_method

        def fake_method(input_data, params):
            df = pd.DataFrame({"gene": ["KRAS"]})
            return {"filtered": df, "ghost": df}, {}

        with pytest.raises(ContractViolation):
            _execute_wfc_method(fake_method)

        口 = Step(
            step_num=3,
            name="Assert no ghost file written",
            purpose="Confirm no file with 'ghost' in its name was created in the run dir")
        ghost_files = list(tmp_project.glob("ghost*"))
        assert ghost_files == [], f"Unexpected ghost files: {ghost_files}"
