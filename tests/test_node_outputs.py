"""
Unit tests for wfc.node_outputs (ADR-010).

The module owns the mapping from a pipeline-JSON node config to the set of
workspace paths it will produce.  Both wfc.snakemake_gen (rule generation)
and wfc.cli.run_step (execution) depend on this helper so the names each
side expects cannot drift.
"""

from pathlib import Path

import pytest


class TestResolveNodeOutputsLegacyFallback:
    """When slot_outputs is empty, the helper degenerates to a single
    ``output{output_ext}`` mapping so legacy single-output pipelines keep
    working unchanged."""

    def test_empty_slot_outputs_default_parquet(self, tmp_path):
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {"slot_outputs": {}}
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert result == {"output": tmp_path / "output.parquet"}

    def test_missing_slot_outputs_key(self, tmp_path):
        """Legacy node configs may not even have the key."""
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {}
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert result == {"output": tmp_path / "output.parquet"}

    def test_output_ext_respected_when_legacy(self, tmp_path):
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {"output_ext": ".csv"}
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert result == {"output": tmp_path / "output.csv"}


class TestResolveNodeOutputsSlotBased:
    """When slot_outputs is present, each slot becomes a workspace path
    using the slot filename verbatim."""

    def test_single_file_slot(self, tmp_path):
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {"slot_outputs": {"config": "extraction_config.json"}}
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert result == {"config": tmp_path / "extraction_config.json"}

    def test_multiple_slots_preserve_order(self, tmp_path):
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {
            "slot_outputs": {
                "predictions": "predictions.csv",
                "model": "model.pkl",
                "metrics": "metrics.json",
            }
        }
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert list(result.keys()) == ["predictions", "model", "metrics"]
        assert result["predictions"] == tmp_path / "predictions.csv"
        assert result["model"] == tmp_path / "model.pkl"
        assert result["metrics"] == tmp_path / "metrics.json"

    def test_mixed_file_and_directory_slots(self, tmp_path):
        from wfc.node_outputs import resolve_node_outputs
        node_cfg = {
            "slot_outputs": {
                "config": "extraction_config.json",
                "tiles_dir": "tiles_dir",
            },
            "slot_types": {"config": "JSON", "tiles_dir": "directory"},
        }
        result = resolve_node_outputs(node_cfg, tmp_path)
        assert result["config"] == tmp_path / "extraction_config.json"
        assert result["tiles_dir"] == tmp_path / "tiles_dir"


class TestIsDirectorySlot:
    """Directory-slot detection consults slot_types (the single source of
    truth).  Filename shape heuristics are explicitly rejected."""

    def test_file_slot_is_not_directory(self):
        from wfc.node_outputs import is_directory_slot
        node_cfg = {
            "slot_outputs": {"config": "config.json"},
            "slot_types": {"config": "JSON"},
        }
        assert is_directory_slot(node_cfg, "config") is False

    def test_directory_slot_detected_via_slot_types(self):
        from wfc.node_outputs import is_directory_slot
        node_cfg = {
            "slot_outputs": {"tiles_dir": "tiles_dir"},
            "slot_types": {"tiles_dir": "directory"},
        }
        assert is_directory_slot(node_cfg, "tiles_dir") is True

    def test_directory_slot_case_insensitive(self):
        from wfc.node_outputs import is_directory_slot
        node_cfg = {
            "slot_outputs": {"tiles_dir": "tiles_dir"},
            "slot_types": {"tiles_dir": "DIRECTORY"},
        }
        assert is_directory_slot(node_cfg, "tiles_dir") is True

    def test_missing_slot_types_means_file_slot(self):
        """Strict additivity: absent slot_types → treat as file slot."""
        from wfc.node_outputs import is_directory_slot
        node_cfg = {"slot_outputs": {"predictions": "predictions.csv"}}
        assert is_directory_slot(node_cfg, "predictions") is False

    def test_unknown_slot_name_is_not_directory(self):
        from wfc.node_outputs import is_directory_slot
        node_cfg = {
            "slot_outputs": {"config": "config.json"},
            "slot_types": {"config": "JSON"},
        }
        assert is_directory_slot(node_cfg, "no_such_slot") is False


class TestHelperIsPure:
    """The helper must not depend on snakemake_gen or cli (no reverse dep)."""

    def _collect_imported_modules(self):
        """Parse wfc.node_outputs with ast and return imported module names."""
        import ast
        import wfc.node_outputs as mod
        source = Path(mod.__file__).read_text()
        tree = ast.parse(source)
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_dependency_on_snakemake_gen(self):
        """The helper must not import from wfc.snakemake_gen or wfc.cli."""
        names = self._collect_imported_modules()
        for n in names:
            assert "snakemake_gen" not in n, f"forbidden import: {n}"
            assert "wfc.cli" not in n, f"forbidden import: {n}"
            assert n != "cli", f"forbidden import: {n}"

    def test_no_database_imports(self):
        names = self._collect_imported_modules()
        for n in names:
            assert "database" not in n, f"forbidden import: {n}"
            assert "models" not in n, f"forbidden import: {n}"
            assert "sqlmodel" not in n, f"forbidden import: {n}"
