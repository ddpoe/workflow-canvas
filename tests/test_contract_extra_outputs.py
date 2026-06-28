"""
ContractViolation message shape + save_artifact boundary (ADR-020).

``ContractViolation`` is the host-side error type for ADR-005 column/contract
validation. It now lives in the pure-stdlib ``wfc_client`` package (lifted
from the deleted in-tree ``wfc.method``); its message shape is unchanged so
existing error-message assertions stay stable.

Under the single-results-channel model there is no return-value parsing and
no silent free-form ``ctx.save()`` fallback. A method declares each output by
writing a file and calling ``ctx.save_artifact(name, path)``. The client's
only guard is that the path resolves inside ``WFC_RUN_DIR`` — extension/type
correctness is validated host-side after the run.
"""

import pytest

from axiom_annotations import task, Step

from wfc_client import ContractViolation, RunContext


@task(purpose="ContractViolation message shape + save_artifact path-boundary guard")
class TestContractViolationAndBoundary:
    """ContractViolation message shape and the save_artifact path boundary."""

    # ------------------------------------------------------------------
    # ContractViolation constructor / message-shape (no I/O)
    # ------------------------------------------------------------------

    def test_extra_outputs_field_stored(self):
        """extra_outputs passed to ContractViolation is stored on the instance."""
        口 = Step(
            step_num=1,
            name="Construct with extra_outputs",
            purpose="Verify the field is initialised and accessible")
        exc = ContractViolation(method="sirna_filter", extra_outputs=["ghost"])
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
        msg = str(exc)
        assert "Undeclared outputs returned" in msg
        assert "ghost" in msg

    def test_no_extra_outputs_section_absent(self):
        """When extra_outputs is empty the 'Undeclared outputs' section is not printed."""
        口 = Step(
            step_num=1,
            name="Construct with no extras",
            purpose="Build a missing-outputs violation and inspect message")
        exc = ContractViolation(
            method="sirna_filter",
            missing_outputs=["filtered"],
            available_outputs=[])
        assert "Undeclared outputs returned" not in str(exc)

    # ------------------------------------------------------------------
    # save_artifact path boundary (US-2): path-inside-WFC_RUN_DIR only
    # ------------------------------------------------------------------

    def test_save_artifact_rejects_path_outside_run_dir(self, tmp_path, monkeypatch):
        """A source path outside WFC_RUN_DIR raises an immediate, clear error."""
        口 = Step(
            step_num=1,
            name="Build a RunContext bound to a run dir",
            purpose="Set WFC_RUN_DIR so the client's path-boundary guard is active")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        ctx = RunContext()

        口 = Step(
            step_num=2,
            name="Save a path outside the run dir",
            purpose="A /tmp-style write outside WFC_RUN_DIR is rejected",
            critical="Must raise before recording the output")
        outside = tmp_path / "elsewhere.csv"
        outside.write_text("x")
        with pytest.raises(ValueError) as exc_info:
            ctx.save_artifact("filtered", outside)
        assert "WFC_RUN_DIR" in str(exc_info.value)

    def test_save_artifact_does_not_validate_extension(self, tmp_path, monkeypatch):
        """The client records the path without checking extension/type.

        Type/extension correctness surfaces host-side via wfc/contracts.py
        after the run, not in the client.
        """
        口 = Step(
            step_num=1,
            name="Save a mismatched-extension file inside the run dir",
            purpose="Confirm the client accepts any extension as long as the path is in-bounds")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        ctx = RunContext()

        weird = run_dir / "predictions.bin"
        weird.write_text("x")
        # No raise: extension is not the client's concern.
        ctx.save_artifact("predictions", weird)
