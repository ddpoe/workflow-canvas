"""
The save_artifact path boundary.

Under the single-results-channel model a method declares each output by
writing a file and calling ``ctx.save_artifact(name, path)``; return values
are not parsed. The client's
only guard is that the path resolves inside ``WFC_RUN_DIR`` — extension/type
correctness is validated host-side after the run.
"""

import pytest

from axiom_annotations import Step, workflow

from wfc_client import RunContext


class TestSaveArtifactBoundary:
    """The save_artifact path boundary."""

    # ------------------------------------------------------------------
    # save_artifact path boundary: path-inside-WFC_RUN_DIR only
    # ------------------------------------------------------------------

    @workflow(purpose="save_artifact rejects a source path outside WFC_RUN_DIR before recording it")
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

    @workflow(purpose="save_artifact accepts any extension for a path inside WFC_RUN_DIR")
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
