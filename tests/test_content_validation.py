"""
Unit tests for ADR-005 content-level contract validation.

Tests cover:
  - Column resolution engine (strict, from_params, patterns)
  - CSV/Parquet header reading
  - Input validation gate (hard gate before method execution)
  - Output soft validation (warnings after method execution)
  - Directory content assertions
  - Static cross-step column validation at pipeline-load time

Test budget: ~18 tests across all subsystems.
"""

import json
import logging
import os
from pathlib import Path

import pandas as pd
import pytest


# =============================================================================
# Task 1: Column resolution engine
# =============================================================================

class TestColumnResolution:
    """Column resolution from method.yaml column specs."""

    def test_strict_columns_returned_as_is(self):
        """strict columns are returned verbatim as a set."""
        from wfc.contracts import resolve_columns

        spec = {"strict": ["label", "area", "centroid-0"]}
        result = resolve_columns(spec, params={})
        assert result == {"label", "area", "centroid-0"}

    def test_from_params_single_scalar(self):
        """from_params with a single scalar param expands to one column."""
        from wfc.contracts import resolve_columns

        spec = {
            "from_params": [
                {"params": ["marker"], "pattern": "{}_nuc_mean"}
            ]
        }
        result = resolve_columns(spec, params={"marker": "DAPI"})
        assert result == {"DAPI_nuc_mean"}

    def test_from_params_single_list(self):
        """from_params with a single list param expands each value."""
        from wfc.contracts import resolve_columns

        spec = {
            "from_params": [
                {"params": ["features"], "pattern": "{}"}
            ]
        }
        result = resolve_columns(spec, params={"features": ["DAPI_mean", "p27_mean"]})
        assert result == {"DAPI_mean", "p27_mean"}

    def test_from_params_cartesian_product(self):
        """from_params with two list params produces the cartesian product."""
        from wfc.contracts import resolve_columns

        spec = {
            "from_params": [
                {"params": ["markers", "measurements"], "pattern": "{}_{}"}
            ]
        }
        result = resolve_columns(
            spec,
            params={"markers": ["DAPI", "p27"], "measurements": ["nuc_mean", "nuc_median"]},
        )
        assert result == {
            "DAPI_nuc_mean", "DAPI_nuc_median",
            "p27_nuc_mean", "p27_nuc_median",
        }

    def test_combined_strict_and_from_params(self):
        """strict and from_params are combined (union)."""
        from wfc.contracts import resolve_columns

        spec = {
            "strict": ["label"],
            "from_params": [
                {"params": ["marker"], "pattern": "{}_nuc_mean"}
            ],
        }
        result = resolve_columns(spec, params={"marker": "DAPI"})
        assert result == {"label", "DAPI_nuc_mean"}

    def test_empty_spec_returns_empty(self):
        """No columns spec returns empty set."""
        from wfc.contracts import resolve_columns

        assert resolve_columns({}, params={}) == set()
        assert resolve_columns(None, params={}) == set()

    def test_from_params_missing_param_returns_empty(self):
        """If a param referenced in from_params is missing, those columns are skipped."""
        from wfc.contracts import resolve_columns

        spec = {
            "from_params": [
                {"params": ["missing_param"], "pattern": "{}_nuc_mean"}
            ]
        }
        # Missing param -> skip (used for static validation where params are unknown)
        result = resolve_columns(spec, params={})
        assert result == set()


class TestHeaderReading:
    """Read headers from CSV and Parquet files without loading full data."""

    def test_read_csv_header(self, tmp_path):
        """read_file_columns reads CSV header from first row."""
        from wfc.contracts import read_file_columns

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("label,area,centroid-0\n1,100,50\n")

        result = read_file_columns(csv_file)
        assert result == ["label", "area", "centroid-0"]

    def test_read_csv_header_strips_whitespace(self, tmp_path):
        """CSV headers with leading/trailing whitespace are stripped."""
        from wfc.contracts import read_file_columns

        csv_file = tmp_path / "test.csv"
        csv_file.write_text(" label , area , centroid-0 \n1,100,50\n")

        result = read_file_columns(csv_file)
        assert result == ["label", "area", "centroid-0"]

    def test_read_empty_csv_returns_empty(self, tmp_path):
        """Zero-byte CSV returns empty list."""
        from wfc.contracts import read_file_columns

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("")

        result = read_file_columns(csv_file)
        assert result == []

    def test_read_parquet_columns(self, tmp_path):
        """read_file_columns reads Parquet column names from metadata."""
        from wfc.contracts import read_file_columns

        pq_file = tmp_path / "test.parquet"
        df = pd.DataFrame({"label": [1], "area": [100]})
        df.to_parquet(pq_file)

        result = read_file_columns(pq_file)
        assert result == ["label", "area"]

    def test_read_csv_with_bom_strips_marker(self, tmp_path):
        """CSV with UTF-8 BOM has the BOM stripped from the first column name."""
        from wfc.contracts import read_file_columns

        csv_file = tmp_path / "bom.csv"
        csv_file.write_bytes(b"\xef\xbb\xbflabel,area\n1,100\n")

        result = read_file_columns(csv_file)
        assert result == ["label", "area"]


class TestPatternMatching:
    """Pattern-based column matching using fnmatch."""

    def test_pattern_matches_existing_columns(self):
        """patterns check that at least one column matches each pattern."""
        from wfc.contracts import check_patterns

        available = ["DAPI_nuc_mean", "p27_nuc_mean", "label", "area"]
        missing = check_patterns(["*_nuc_mean"], available)
        assert missing == []

    def test_pattern_no_match_returns_pattern(self):
        """If no column matches a pattern, the pattern is returned as missing."""
        from wfc.contracts import check_patterns

        available = ["label", "area"]
        missing = check_patterns(["*_nuc_mean"], available)
        assert missing == ["*_nuc_mean"]


# =============================================================================
# Task 2: Input validation gate
# =============================================================================

class TestInputValidationGate:
    """Input column validation runs before method execution."""

    def test_validate_input_columns_pass(self, tmp_path):
        """No error when all required columns are present."""
        from wfc.contracts import validate_input_columns

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("label,area,DAPI_nuc_mean\n1,100,50\n")

        column_spec = {"strict": ["label", "area"]}
        # Should not raise
        validate_input_columns(csv_file, column_spec, params={})

    def test_validate_input_columns_missing_raises(self, tmp_path):
        """ContractViolation raised when required columns are missing."""
        from wfc.contracts import validate_input_columns
        from wfc.method import ContractViolation

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("label,area\n1,100\n")

        column_spec = {"strict": ["label", "area", "DAPI_nuc_mean"]}
        with pytest.raises(ContractViolation) as exc_info:
            validate_input_columns(csv_file, column_spec, params={})

        assert "DAPI_nuc_mean" in str(exc_info.value)
        assert "label" in str(exc_info.value)  # available columns listed

    def test_validate_input_from_params_expansion(self, tmp_path):
        """from_params columns are expanded and validated."""
        from wfc.contracts import validate_input_columns
        from wfc.method import ContractViolation

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("label,DAPI_nuc_mean\n1,50\n")

        column_spec = {
            "from_params": [
                {"params": ["marker"], "pattern": "{}_nuc_mean"}
            ]
        }
        # DAPI matches
        validate_input_columns(csv_file, column_spec, params={"marker": "DAPI"})

        # PCNA does not match
        with pytest.raises(ContractViolation):
            validate_input_columns(csv_file, column_spec, params={"marker": "PCNA"})


# =============================================================================
# Task 3: Output soft validation
# =============================================================================

class TestOutputSoftValidation:
    """Output column validation logs warnings without failing the run."""

    def test_validate_output_columns_warns_on_missing(self, tmp_path, caplog):
        """Missing output columns produce a warning, not an exception."""
        from wfc.contracts import validate_output_columns

        csv_file = tmp_path / "output.csv"
        csv_file.write_text("label,area\n1,100\n")

        column_spec = {"strict": ["label", "area", "predictions"]}
        with caplog.at_level(logging.WARNING):
            validate_output_columns(csv_file, column_spec, params={}, slot_name="labeled")

        assert any("predictions" in r.message for r in caplog.records)

    def test_validate_output_columns_no_warning_when_ok(self, tmp_path, caplog):
        """No warning when all declared columns are present."""
        from wfc.contracts import validate_output_columns

        csv_file = tmp_path / "output.csv"
        csv_file.write_text("label,area,predictions\n1,100,0\n")

        column_spec = {"strict": ["label", "area", "predictions"]}
        with caplog.at_level(logging.WARNING):
            validate_output_columns(csv_file, column_spec, params={}, slot_name="labeled")

        assert not any("missing" in r.message.lower() for r in caplog.records)


# =============================================================================
# Task 3b: Directory content assertions
# =============================================================================

class TestDirectoryContentValidation:
    """Directory output validation checks file patterns and CSV columns."""

    def test_directory_contents_glob_pass(self, tmp_path, caplog):
        """Glob pattern matching with sufficient files produces no warning."""
        from wfc.contracts import validate_directory_contents

        csv_dir = tmp_path / "csv_regionprops"
        csv_dir.mkdir()
        (csv_dir / "region1.csv").write_text("label,area\n1,100\n")

        contents_spec = [
            {"pattern": "csv_regionprops/*.csv", "min_count": 1}
        ]
        with caplog.at_level(logging.WARNING):
            validate_directory_contents(tmp_path, contents_spec, params={})

        assert not any("min_count" in r.message.lower() for r in caplog.records)

    def test_directory_contents_min_count_warning(self, tmp_path, caplog):
        """Warns when fewer files match than min_count."""
        from wfc.contracts import validate_directory_contents

        contents_spec = [
            {"pattern": "csv_regionprops/*.csv", "min_count": 1}
        ]
        with caplog.at_level(logging.WARNING):
            validate_directory_contents(tmp_path, contents_spec, params={})

        assert any("min_count" in r.message.lower() for r in caplog.records)

    def test_directory_contents_csv_column_check(self, tmp_path, caplog):
        """CSV files matched by glob are checked for declared columns."""
        from wfc.contracts import validate_directory_contents

        csv_dir = tmp_path / "csv_regionprops"
        csv_dir.mkdir()
        (csv_dir / "region1.csv").write_text("label,area\n1,100\n")

        contents_spec = [
            {
                "pattern": "csv_regionprops/*.csv",
                "min_count": 1,
                "columns": {"strict": ["label", "area", "centroid-0"]},
            }
        ]
        with caplog.at_level(logging.WARNING):
            validate_directory_contents(tmp_path, contents_spec, params={})

        assert any("centroid-0" in r.message for r in caplog.records)


# =============================================================================
# Task 4: Static cross-step validation
# =============================================================================

class TestStaticCrossStepValidation:
    """Cross-step column compatibility checked at pipeline-load time."""

    def test_cross_check_compatible_columns(self):
        """No warning when upstream output columns are a superset of downstream input."""
        from wfc.contracts import cross_check_columns

        upstream_output = {"strict": ["label", "area", "centroid-0"]}
        downstream_input = {"strict": ["label", "area"]}

        warnings = cross_check_columns(upstream_output, downstream_input)
        assert warnings == []

    def test_cross_check_missing_columns_warns(self):
        """Warning produced when downstream needs columns upstream does not declare."""
        from wfc.contracts import cross_check_columns

        upstream_output = {"strict": ["label", "area"]}
        downstream_input = {"strict": ["label", "area", "DAPI_nuc_mean"]}

        warnings = cross_check_columns(upstream_output, downstream_input)
        assert len(warnings) == 1
        assert "DAPI_nuc_mean" in warnings[0]

    def test_cross_check_skips_from_params(self):
        """from_params columns are not checked statically (deferred to runtime)."""
        from wfc.contracts import cross_check_columns

        upstream_output = {"strict": ["label"]}
        downstream_input = {
            "strict": ["label"],
            "from_params": [{"params": ["marker"], "pattern": "{}_nuc_mean"}],
        }

        warnings = cross_check_columns(upstream_output, downstream_input)
        assert warnings == []
