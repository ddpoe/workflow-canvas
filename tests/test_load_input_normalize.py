"""Tests for normalized load_input() — always returns dict[str, list[Path]]."""

import json
from pathlib import Path


class TestLoadInputAlwaysDict:
    """load_input() must return dict[str, list[Path]] for every topology."""

    def test_single_input_returns_dict_of_paths(self, tmp_path, monkeypatch):
        """Single-parent input returns dict with Path values, not DataFrame."""
        run_dir = tmp_path / "00000001"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_ID", "1")
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        monkeypatch.setenv("WFC_SAMPLE", "S1")

        input_file = tmp_path / "upstream_output.csv"
        input_file.write_text("col_a,col_b\n1,2\n")
        monkeypatch.setenv(
            "WFC_INPUT_PATHS",
            json.dumps({"data": [str(input_file)]}),
        )

        from wfc.wfc_context import RunContext
        ctx = RunContext()
        result = ctx.load_input()

        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert "data" in result
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 1
        # Must be a Path, not a DataFrame — methods own their I/O
        assert isinstance(result["data"][0], Path)

    def test_csv_input_returns_path_not_dataframe(self, tmp_path, monkeypatch):
        """CSV files must come back as Path, never auto-loaded as DataFrame."""
        run_dir = tmp_path / "00000002"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_ID", "2")
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        monkeypatch.setenv("WFC_SAMPLE", "S1")

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("x\n1\n")
        monkeypatch.setenv(
            "WFC_INPUT_PATHS",
            json.dumps({"data": [str(csv_file)]}),
        )

        from wfc.wfc_context import RunContext
        ctx = RunContext()
        result = ctx.load_input()

        assert isinstance(result["data"][0], Path)

    def test_fan_in_returns_dict(self, tmp_path, monkeypatch):
        """Fan-in (multiple slots) returns dict with correct slot keys."""
        run_dir = tmp_path / "00000003"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_ID", "3")
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        monkeypatch.setenv("WFC_SAMPLE", "S1")

        file_a = tmp_path / "a.csv"
        file_b = tmp_path / "b.csv"
        file_a.write_text("x\n1\n")
        file_b.write_text("x\n2\n")
        monkeypatch.setenv(
            "WFC_INPUT_PATHS",
            json.dumps({"sources": [str(file_a), str(file_b)]}),
        )

        from wfc.wfc_context import RunContext
        ctx = RunContext()
        result = ctx.load_input()

        assert isinstance(result, dict)
        assert "sources" in result
        assert len(result["sources"]) == 2
        assert all(isinstance(p, Path) for p in result["sources"])

    def test_no_input_returns_none(self, tmp_path, monkeypatch):
        """Root node with no input returns None."""
        run_dir = tmp_path / "00000004"
        run_dir.mkdir()
        monkeypatch.setenv("WFC_RUN_ID", "4")
        monkeypatch.setenv("WFC_RUN_DIR", str(run_dir))
        monkeypatch.setenv("WFC_SAMPLE", "S1")
        monkeypatch.delenv("WFC_INPUT_PATHS", raising=False)

        from wfc.wfc_context import RunContext
        ctx = RunContext()
        result = ctx.load_input()

        assert result is None
