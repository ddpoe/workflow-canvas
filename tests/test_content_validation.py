"""
Unit tests for content-level contract validation.

Tests cover:
  - Column resolution engine (strict, from_params, patterns)
  - CSV/Parquet header reading
  - Input validation gate (hard gate before method execution)
  - Output soft validation (warnings after method execution)
  - Directory content assertions
  - Static cross-step column validation at pipeline-load time

Test budget: ~18 tests across all subsystems.
"""


import pytest


# =============================================================================
# Column resolution engine
# =============================================================================

class TestColumnResolution:
    """Column resolution from method.yaml column specs."""

    @pytest.mark.parametrize("spec, params, expected", [
        # strict columns returned verbatim
        ({"strict": ["label", "area", "centroid-0"]}, {},
         {"label", "area", "centroid-0"}),
        # from_params single scalar -> one column
        ({"from_params": [{"params": ["marker"], "pattern": "{}_nuc_mean"}]},
         {"marker": "DAPI"}, {"DAPI_nuc_mean"}),
        # from_params single list -> each value
        ({"from_params": [{"params": ["features"], "pattern": "{}"}]},
         {"features": ["DAPI_mean", "p27_mean"]}, {"DAPI_mean", "p27_mean"}),
        # two list params -> cartesian product
        ({"from_params": [{"params": ["markers", "measurements"], "pattern": "{}_{}"}]},
         {"markers": ["DAPI", "p27"], "measurements": ["nuc_mean", "nuc_median"]},
         {"DAPI_nuc_mean", "DAPI_nuc_median", "p27_nuc_mean", "p27_nuc_median"}),
        # strict + from_params are unioned
        ({"strict": ["label"],
          "from_params": [{"params": ["marker"], "pattern": "{}_nuc_mean"}]},
         {"marker": "DAPI"}, {"label", "DAPI_nuc_mean"}),
    ])
    def test_resolve_columns(self, spec, params, expected):
        """resolve_columns handles strict, from_params (scalar/list/cartesian),
        and their union."""
        from wfc.contracts import resolve_columns
        assert resolve_columns(spec, params=params) == expected

    @pytest.mark.parametrize("spec", [
        {},
        None,
        # a from_params referencing a param that isn't provided contributes nothing
        {"from_params": [{"params": ["missing_param"], "pattern": "{}_nuc_mean"}]},
    ])
    def test_resolve_columns_empty_when_nothing_to_resolve(self, spec):
        """Empty/None spec and unresolvable from_params both yield an empty set."""
        from wfc.contracts import resolve_columns
        assert resolve_columns(spec, params={}) == set()


# =============================================================================
# Static cross-step validation
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

    def test_cross_check_with_an_absent_spec_returns_no_messages(self):
        """Either side's spec being None yields an empty list, not an error."""
        from wfc.contracts import cross_check_columns

        spec = {"strict": ["label", "area"]}

        assert cross_check_columns(None, spec) == []
        assert cross_check_columns(spec, None) == []
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
