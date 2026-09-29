"""
NID (Node ID) System Tests
===========================

Tests for the auto-versioned run identity system that assigns
v1, v2, v3... identifiers per (sample, method) pair, with
support for custom NID labels from canvas node labels.

Tier 2 tests: @workflow(purpose=...), no Step markers.
"""

from pathlib import Path

from axiom_annotations import workflow

from tests.fixtures.routes import completed_run

#: The module every method here is registered under.
MODULE = "data_preprocessing"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(project_root: Path):
    """Create a WfcProvider instance pointing at the given project root."""
    from wfc.canvas.wfc_provider import WfcProvider
    return WfcProvider(str(project_root))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@workflow(purpose="Three runs with same (sample, method) get auto-versioned NIDs v1, v2, v3 in chronological order")
def test_auto_version_chronological(tmp_project, monkeypatch):
    """Re-running the same method on the same sample produces v1, v2, v3."""
    # Three runs for the same (sample, method), in order. Each declares its
    # own params: the same method, sample and params twice is a cache hit,
    # not a second execution.
    ids = [
        completed_run(
            tmp_project, monkeypatch=monkeypatch,
            method="ploidy_filter", module=MODULE, sample="Pa16c",
            params={"pass": i}, pipeline_id=f"nid-{i}",
        ).run_id
        for i in (1, 2, 3)
    ]

    provider = _make_provider(tmp_project)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs[str(ids[0])].nid == "v1"
    assert runs[str(ids[1])].nid == "v2"
    assert runs[str(ids[2])].nid == "v3"


@workflow(purpose="Runs across different (sample, method) pairs get independent version sequences")
def test_independent_version_sequences(tmp_project, monkeypatch):
    """Different methods or samples start their own sequence."""
    def run(method: str, sample: str, i: int) -> int:
        return completed_run(
            tmp_project, monkeypatch=monkeypatch,
            method=method, module=MODULE, sample=sample,
            params={"pass": i}, pipeline_id=f"nid-{i}",
        ).run_id

    # Two runs for ploidy_filter/Pa16c, with the other pairs' runs between
    # them in time.
    first = run("ploidy_filter", "Pa16c", 1)
    # One run for binary_labeling/Pa16c (different method, same sample)
    other_method = run("binary_labeling", "Pa16c", 2)
    # One run for ploidy_filter/Pa22b (same method, different sample)
    other_sample = run("ploidy_filter", "Pa22b", 3)
    second = run("ploidy_filter", "Pa16c", 4)

    provider = _make_provider(tmp_project)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    # ploidy_filter/Pa16c: v1, v2
    assert runs[str(first)].nid == "v1"
    assert runs[str(second)].nid == "v2"
    # binary_labeling/Pa16c: v1 (independent sequence)
    assert runs[str(other_method)].nid == "v1"
    # ploidy_filter/Pa22b: v1 (independent sequence)
    assert runs[str(other_sample)].nid == "v1"


@workflow(purpose="Run with custom nid shows that value instead of auto-version")
def test_custom_nid_replaces_auto_version(tmp_project, monkeypatch):
    """Setting a canvas node label causes the run card to display that label."""
    labelled = completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method="ploidy_filter", module=MODULE, sample="Pa16c",
        params={"pass": 1}, label="fast_set", pipeline_id="nid-1",
    ).run_id
    unlabelled = completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method="ploidy_filter", module=MODULE, sample="Pa16c",
        params={"pass": 2}, pipeline_id="nid-2",
    ).run_id

    provider = _make_provider(tmp_project)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs[str(labelled)].nid == "fast_set"
    # Second run has no custom nid, auto-versions as v2 (it's the second
    # run chronologically for this (sample, method) pair)
    assert runs[str(unlabelled)].nid == "v2"


@workflow(purpose="Custom NID then NULL NID correctly auto-versions the NULL run")
def test_custom_then_null_nid(tmp_project, monkeypatch):
    """Clearing the label before a subsequent run restores auto-versioning."""
    # First run is labelled; the label is cleared before the next two.
    labelled = completed_run(
        tmp_project, monkeypatch=monkeypatch,
        method="ploidy_filter", module=MODULE, sample="Pa16c",
        params={"pass": 1}, label="aggressive", pipeline_id="nid-1",
    ).run_id
    cleared = [
        completed_run(
            tmp_project, monkeypatch=monkeypatch,
            method="ploidy_filter", module=MODULE, sample="Pa16c",
            params={"pass": i}, pipeline_id=f"nid-{i}",
        ).run_id
        for i in (2, 3)
    ]

    provider = _make_provider(tmp_project)
    provider.load()
    runs = {r.id: r for r in provider._runs.values()}

    assert runs[str(labelled)].nid == "aggressive"
    assert runs[str(cleared[0])].nid == "v2"
    assert runs[str(cleared[1])].nid == "v3"


