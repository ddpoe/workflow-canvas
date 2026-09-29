"""Tier 1 test: the pure record-composition kernel.

``compose_record`` maps a step ending to exactly which rows and sidecars
the record tail writes — plain values in, plan dict out, no I/O.
"""
from __future__ import annotations

from wfc.execution.record import compose_record


def test_each_ending_composes_its_exact_writes():
    """The exit classes keep their distinct tails: cache hit writes sentinel
    and sidecars but never flips the run row; success flips, writes, and
    enqueues the push; failures flip to failed with a console line (launch
    failure keeps its distinct prefix); the SLURM carve-out writes nothing."""
    cached = compose_record("cached")
    assert cached["run_status"] is None
    assert cached["touch_sentinel"] and cached["write_run_id_sidecar"]
    assert not cached["enqueue_push"]
    assert cached["outcome_status"] == "cached"
    assert cached["rc"] == 0

    done = compose_record("completed")
    assert done["run_status"] == "completed"
    assert done["touch_sentinel"] and done["write_run_id_sidecar"]
    assert done["enqueue_push"]
    assert done["outcome_status"] == "completed"
    assert done["rc"] == 0

    failed = compose_record("missing-slot", "Method 'm' did not produce declared slot 's'")
    assert failed["run_status"] == "failed"
    assert not failed["touch_sentinel"] and not failed["enqueue_push"]
    assert failed["outcome_status"] == "failed"
    assert failed["console"] == "ERROR: Method 'm' did not produce declared slot 's'"
    assert failed["rc"] == 1

    launch = compose_record("launch-failure", "boom")
    assert launch["console"] == "ERROR: method execution failed: boom"
    assert launch["run_status"] == "failed"

    slurm = compose_record("slurm", "cluster Apptainer dispatch (executor=slurm) is not supported")
    assert slurm["run_status"] is None
    assert not slurm["touch_sentinel"] and not slurm["write_run_id_sidecar"]
    assert slurm["outcome_status"] is None, "the carve-out writes no outcome sidecar"
    assert slurm["console"].startswith("ERROR: cluster Apptainer dispatch")
    assert slurm["rc"] == 1
