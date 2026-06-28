"""Host-side manifest reader + archive parity (ADR-020 single results channel).

Tier 2 subsystem tests: the host reads ``_wfc_results.json`` into output
paths + metrics, resolves run-dir-relative paths, rejects escapes, and the
existing row-based ``archive_outputs`` sweep hashes/caches the rows. A pure
Tier-2 method with no manifest falls back to scanning the run dir.

No Docker — the manifest reader and the archive sweep are pure host-side
functions runnable against a populated run directory.
"""

import json

import pytest

from axiom_annotations import workflow, Step

from wfc.manifest import read_results_manifest


# =============================================================================
# read_results_manifest — Tier-1 manifest present
# =============================================================================

@workflow(purpose="Host reads _wfc_results.json into resolved output paths + metrics")
def test_read_manifest_resolves_outputs_and_metrics(tmp_path):
    run_dir = tmp_path / "run"
    workdir = run_dir / "_workdir"
    workdir.mkdir(parents=True)
    (workdir / "clean.csv").write_text("id\n1\n")
    (run_dir / "report.json").write_text("{}")

    manifest = {
        "outputs": {"clean": "_workdir/clean.csv", "report": "report.json"},
        "metrics": {"kept_rows": 100, "dropped_rows": 5},
    }
    (run_dir / "_wfc_results.json").write_text(json.dumps(manifest))

    result = read_results_manifest(run_dir)

    assert result is not None
    assert result.metrics == {"kept_rows": 100, "dropped_rows": 5}
    # Outputs resolve to absolute paths inside run_dir.
    assert result.outputs["clean"] == (run_dir / "_workdir" / "clean.csv").resolve()
    assert result.outputs["report"] == (run_dir / "report.json").resolve()


def test_read_manifest_absent_returns_none(tmp_path):
    """Tier-2 (no manifest) returns None so the caller scans the run dir."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert read_results_manifest(run_dir) is None


def test_read_manifest_rejects_path_escape(tmp_path):
    """A manifest output path that resolves outside run_dir is rejected."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (tmp_path / "secret.csv").write_text("x")
    manifest = {"outputs": {"clean": "../secret.csv"}, "metrics": {}}
    (run_dir / "_wfc_results.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError) as exc:
        read_results_manifest(run_dir)
    assert "run_dir" in str(exc.value).lower() or "WFC_RUN_DIR" in str(exc.value)


def test_read_manifest_empty_outputs_and_metrics(tmp_path):
    """A manifest with empty outputs/metrics parses to empty dicts."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "_wfc_results.json").write_text(json.dumps({"outputs": {}, "metrics": {}}))

    result = read_results_manifest(run_dir)
    assert result is not None
    assert result.outputs == {}
    assert result.metrics == {}


def test_read_manifest_missing_file_for_output_raises(tmp_path):
    """A manifest output whose file does not exist is a clear error."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {"outputs": {"clean": "_workdir/clean.csv"}, "metrics": {}}
    (run_dir / "_wfc_results.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError) as exc:
        read_results_manifest(run_dir)
    assert "clean" in str(exc.value)
