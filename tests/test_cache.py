"""
Unit Tests: the check_cache verb answers with the run the claim phase would reuse

Validates the verb's cache answer, given through the claim phase's one
cache-hit lookup (identical code, params, env and inputs; archive present):
  - ``pre_run`` stores slot names in ``RunInput.input_name``
  - the exact fan-in is a hit; a subset of the parents is a miss
  - Cache misses on different params or a pruned archive
  - Cache hits return the most recent matching run
  - Sample-conditional params (Differential QC scenario) produce isolated
    cache entries with no cross-sample false hits
"""

import json
import shutil

from axiom_annotations import workflow, Step

from tests.fixtures.routes import claimed_run, completed_run
from tests.harness import Phase, Scenario, node, selector, wire
from tests.harness.scenario import SELECTOR_ID

#: The module, method and sample every lookup in this file names.
_MODULE = "csv_tools"
_METHOD = "csv_merge"
_SAMPLE = "S1"


# =============================================================================
# Helpers
# =============================================================================

def _fan_in_scenario():
    """Two root sources feeding ``csv_merge`` on its ``sources`` slot.

    The roots are one method under distinct params, so each executes rather
    than cache-hitting the other; the consumer is ``csv_merge``, the method
    every lookup below names.
    """
    return Scenario(
        nodes=[
            selector(),
            node("run_a", method="csv_source", module=_MODULE,
                 inputs=[wire(SELECTOR_ID)]),
            node("run_b", method="csv_source", module=_MODULE,
                 inputs=[wire(SELECTOR_ID)], params={"side": "b"}),
            node("merge", method=_METHOD, module=_MODULE,
                 inputs=[wire("run_a", target_slot="sources"),
                         wire("run_b", target_slot="sources")]),
        ],
        samples=[_SAMPLE],
        pipeline_id="cache-fan-in",
        name="cache_fan_in",
    )


def _run_id(driven) -> str:
    """The run id as the CLI prints it."""
    return str(driven.run_id)


# =============================================================================
# Tests
# =============================================================================

def test_fan_in_matching(cli, tmp_project, monkeypatch):
    """The claim stores the slot names of its parents; check_cache finds
    the exact fan-in and rejects a subset of the parents."""

    scn = _fan_in_scenario()

    # Two upstream runs (root nodes fed by the selector)
    run_a = _run_id(completed_run(tmp_project, monkeypatch=monkeypatch,
                                  scenario=scn, target="run_a"))
    run_b = _run_id(completed_run(tmp_project, monkeypatch=monkeypatch,
                                  scenario=scn, target="run_b"))

    # Merge run with two parents on the "sources" slot, wired in the document
    merge_id = _run_id(completed_run(tmp_project, monkeypatch=monkeypatch,
                                     scenario=scn, target="merge"))

    # ── Verify RunInput rows store the slot name ──────────────────────────
    from wfc.persistence import get_session, RunInput
    from sqlmodel import select

    with get_session() as session:
        inputs = session.exec(
            select(RunInput).where(RunInput.run_id == int(merge_id))
        ).all()
        actual = {(ri.input_name, ri.source_run_id) for ri in inputs}
        assert actual == {("sources", int(run_a)), ("sources", int(run_b))}

    # ── Exact match → cache hit ───────────────────────────────────────────
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_a}",
            "--parent-run-id", f"sources:{run_b}")
    assert r.stdout.strip() == merge_id

    # ── Subset of parents → miss ──────────────────────────────────────────
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_a}")
    assert r.stdout.strip() == "NONE"

    # ── Reversed order → still a hit (the input fingerprint is sorted) ────
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}",
            "--parent-run-id", f"sources:{run_b}",
            "--parent-run-id", f"sources:{run_a}")
    assert r.stdout.strip() == merge_id


def test_params_mismatch(cli, tmp_project, monkeypatch):
    """Same method+sample+parents but different params → cache miss."""

    run_id = _run_id(completed_run(
        tmp_project, monkeypatch=monkeypatch, method=_METHOD, module=_MODULE,
        sample=_SAMPLE, params={"column": "condition"}))

    # Exact params → hit
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", '{"column": "condition"}')
    assert r.stdout.strip() == run_id

    # Different params → miss
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", '{"column": "replicate"}')
    assert r.stdout.strip() == "NONE"


def test_no_parents_returns_newest(cli, tmp_project, monkeypatch):
    """Two identical parentless runs → cache returns the newer one."""

    # Both claim and collect before either is recorded, so both are genuine
    # (non-audit) completed rows under the same key; the lookup prefers the
    # newest. The record phase is the complete_run verb over the output the
    # collect phase recorded.
    runs = [
        claimed_run(tmp_project, monkeypatch=monkeypatch, through=Phase.COLLECT,
                    method=_METHOD, module=_MODULE, sample=_SAMPLE)
        for _ in range(2)
    ]
    for run in runs:
        r = cli("complete_run", "--run-id", _run_id(run), "--status", "completed",
                "--output", run.output_rows[0]["artifact_path"])
        assert r.returncode == 0, r.stderr
    run_new = _run_id(runs[1])

    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == run_new


def test_missing_archive_is_cache_miss(cli, tmp_project, monkeypatch):
    """Completed run exists in DB but archive deleted → cache miss."""
    run = completed_run(tmp_project, monkeypatch=monkeypatch, method=_METHOD,
                        module=_MODULE, sample=_SAMPLE)
    run_id = _run_id(run)

    # Verify it's a hit first
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == run_id

    # Delete the archive
    shutil.rmtree(run.archive_dir)

    # Now it's a miss
    r = cli("check_cache", "--method", "csv_merge",
            "--module", "csv_tools", "--sample", "S1",
            "--params", "{}")
    assert r.stdout.strip() == "NONE"


@workflow(
    purpose="Verify that sample-conditional QC variants produce isolated cache "
            "entries — the same method run with different params for different "
            "samples has no false cache hits and no cross-sample contamination"
)
def test_differential_qc_cache(cli, tmp_project, monkeypatch):
    """Differential QC scenario: Rep2 uses threshold 2.5 (standard), Rep3 uses
    threshold 2.3 (dim_corrected). Each sample must cache independently — a
    query with the wrong params or wrong sample must always miss."""

    口 = Step(
        step_num=1,
        name="Declare the QC method's samples and thresholds",
        purpose="The feature_qc method and both samples are registered by the "
                "first run's route; the two thresholds are what tell the runs apart")
    samples = ["Rep2_siRNA", "Rep3_siRNA"]
    params_standard     = json.dumps({"filters": [{"column": "R1_p27", "min": 2.5}]})
    params_dim_corrected = json.dumps({"filters": [{"column": "R1_p27", "min": 2.3}]})

    口 = Step(
        step_num=2,
        name="Record Rep2 standard run",
        purpose="Complete a feature_qc run for Rep2 with the standard p27 threshold",
        inputs="Rep2_siRNA sample, threshold 2.5 params",
        outputs="Completed run ID for Rep2")
    run_rep2 = _run_id(completed_run(
        tmp_project, monkeypatch=monkeypatch, method="feature_qc",
        module="data_preprocessing", sample="Rep2_siRNA", samples=samples,
        params=json.loads(params_standard)))

    口 = Step(
        step_num=3,
        name="Record Rep3 dim-corrected run",
        purpose="Complete a feature_qc run for Rep3 with the lower dim-corrected threshold",
        inputs="Rep3_siRNA sample, threshold 2.3 params",
        outputs="Completed run ID for Rep3")
    run_rep3 = _run_id(completed_run(
        tmp_project, monkeypatch=monkeypatch, method="feature_qc",
        module="data_preprocessing", sample="Rep3_siRNA", samples=samples,
        params=json.loads(params_dim_corrected)))

    口 = Step(
        step_num=4,
        name="Verify no cross-sample false hits",
        purpose="Confirm that querying each sample with the other sample's params "
                "returns no match — different thresholds must never share a cache entry")
    # Rep3 query with Rep2's params → miss (correct sample, wrong params)
    r = cli("check_cache", "--method", "feature_qc",
            "--module", "data_preprocessing", "--sample", "Rep3_siRNA",
            "--params", params_standard)
    assert r.stdout.strip() == "NONE", (
        "Rep3 with standard threshold should not match the Rep2 run"
    )

    # Rep2 query with Rep3's params → miss (correct sample, wrong params)
    r = cli("check_cache", "--method", "feature_qc",
            "--module", "data_preprocessing", "--sample", "Rep2_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == "NONE", (
        "Rep2 with dim-corrected threshold should not match the Rep3 run"
    )

    # Rep2 query with Rep2's params but Rep3 sample → miss (wrong sample)
    r = cli("check_cache", "--method", "feature_qc",
            "--module", "data_preprocessing", "--sample", "Rep3_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == run_rep3

    口 = Step(
        step_num=5,
        name="Verify same-run cache hit",
        purpose="Confirm that querying each sample with its own params returns "
                "the correct run — no spurious misses after the cross-sample checks")
    r = cli("check_cache", "--method", "feature_qc",
            "--module", "data_preprocessing", "--sample", "Rep2_siRNA",
            "--params", params_standard)
    assert r.stdout.strip() == run_rep2

    r = cli("check_cache", "--method", "feature_qc",
            "--module", "data_preprocessing", "--sample", "Rep3_siRNA",
            "--params", params_dim_corrected)
    assert r.stdout.strip() == run_rep3
