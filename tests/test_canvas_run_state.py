"""Status aggregation over plain values: rules no status-route test reaches.

The route tests in ``tests/test_canvas_run.py`` and ``tests/test_canvas_api_runs.py``
witness the settled, failed, mixed, cancelled, error, log, cache-hit and push
cases. This module covers the rules they leave: a running row outranks a
failed node; pending outputs with nothing in flight or failed read
``pending``; an ended thread with an error and a node still pending reads
failed, or cancelled after a user cancel; a cancelled node names the failed
run behind it and that run's node; and two nodes of one method each project
from their own rows.
"""

from __future__ import annotations

from wfc.canvas.run_state import (
    OutputRecord,
    RunRecord,
    derive_overall_status,
    project_node_states,
    settle_unstarted_nodes,
    tally_nodes,
)


def _run(run_id: int, node_id: str | None, status: str, **extra) -> RunRecord:
    fields = dict(run_id=run_id, node_id=node_id, status=status, sample="s1",
                  error_message=None, cache_source_run_id=None, cache_key=None)
    fields.update(extra)
    return RunRecord(**fields)


def test_a_running_row_outranks_failures_and_pending_outputs_read_pending():
    runs = [_run(3, "n_align", "running"), _run(2, "n_align", "failed"),
            _run(1, "n_count", "failed")]
    outputs = [OutputRecord("n_align", "pending"), OutputRecord("n_align", "pushed")]
    step_map = {"n_align": "align", "n_count": "count"}

    tallies = tally_nodes(runs, outputs)
    node_states = settle_unstarted_nodes(
        step_map, project_node_states(step_map, tallies), thread_alive=True, error=None,
    )

    assert tallies.status == {"n_align": "running", "n_count": "failed"}
    align = node_states["n_align"]
    assert (align["push_state"], align["push_pending_count"], align["push_failed_count"]) == (
        "pending", 1, 0,
    )
    assert derive_overall_status(node_states, thread_alive=True, error=None) == "running"


def test_a_dead_thread_with_an_error_fails_a_pipeline_with_a_node_still_pending():
    """A node that never wrote a row will never start once the thread died
    with an error, so the pipeline is failed -- whether or not other nodes
    finished first -- rather than running forever."""
    step_map = {"n_head": "head", "n_tail": "tail"}
    tallies = tally_nodes([_run(1, "n_head", "completed")], [])
    node_states = settle_unstarted_nodes(
        step_map, project_node_states(step_map, tallies),
        thread_alive=False, error="engine failed",
    )

    assert node_states["n_tail"]["status"] == "pending"
    assert derive_overall_status(node_states, thread_alive=False,
                                 error="engine failed") == "failed"
    assert derive_overall_status(node_states, thread_alive=True,
                                 error="engine failed") == "running"


def test_a_user_cancel_with_a_node_still_pending_reads_cancelled_unless_a_node_failed():
    """A user cancel ends the thread with the cancel's error recorded and
    leaves the unstarted nodes pending. That pipeline was cancelled, not
    failed; a node that genuinely failed before the cancel still fails it."""
    step_map = {"n_head": "head", "n_mid": "mid", "n_tail": "tail"}
    stopped = _run(2, "n_mid", "cancelled", error_message="Cancelled by user")

    def overall(runs, cancel_requested):
        tallies = tally_nodes(runs, [])
        node_states = settle_unstarted_nodes(
            step_map, project_node_states(step_map, tallies),
            thread_alive=False, error="cancelled",
        )
        assert node_states["n_tail"]["status"] == "pending"
        return derive_overall_status(node_states, thread_alive=False,
                                     error="cancelled",
                                     cancel_requested=cancel_requested)

    assert overall([stopped, _run(1, "n_head", "completed")], True) == "cancelled"
    assert overall([stopped, _run(1, "n_head", "completed")], False) == "failed"
    assert overall([stopped, _run(1, "n_head", "failed")], True) == "failed"


def test_a_cancelled_node_names_the_failed_run_and_the_node_behind_it():
    """A row the pipeline-end walk cancelled carries the failed run that
    caused it, and that run's node, onto the node state, which is what
    separates a node cancelled by an upstream failure from one the user
    stopped. A cause run outside the pipeline's rows names no node."""
    cancelled = _run(5, "n_tail", "cancelled", cancelled_due_to_run_id=4,
                     cause_node_id="n_head")
    orphaned = _run(7, "n_side", "cancelled", cancelled_due_to_run_id=99)
    stopped = _run(6, "n_other", "cancelled", error_message="Cancelled by user")
    step_map = {"n_tail": "tail", "n_other": "other", "n_side": "side"}
    states = project_node_states(step_map, tally_nodes([orphaned, stopped, cancelled], []))

    assert states["n_tail"]["cancelled_due_to_run_id"] == "4"
    assert states["n_tail"]["upstream_run_id"] == "4"
    assert states["n_tail"]["upstream_node_id"] == "n_head"
    assert states["n_side"]["upstream_run_id"] == "99"
    assert "upstream_node_id" not in states["n_side"]
    assert "cancelled_due_to_run_id" not in states["n_other"]


def test_two_nodes_of_one_method_each_project_from_their_own_rows():
    """Two canvas nodes run the same method: one failed, one reused a cached
    run. Each node's status, run ids, newest error and cache source come
    from the rows it recorded -- never a ``mixed`` blend of both -- and a
    row that recorded no node is not counted toward either."""
    runs = [
        _run(4, "n_right", "completed", cache_source_run_id=1, cache_key="ck"),
        _run(3, "n_left", "failed", error_message="left blew up", sample="s2"),
        _run(2, "n_left", "failed", error_message="older failure"),
        _run(9, None, "running"),
    ]
    step_map = {"n_left": "align", "n_right": "align"}

    states = project_node_states(step_map, tally_nodes(runs, []))

    left, right = states["n_left"], states["n_right"]
    assert (left["status"], left["run_ids"]) == ("failed", ["3", "2"])
    assert (left["error"], left["error_run_id"], left["error_sample"]) == (
        "left blew up", "3", "s2",
    )
    assert "cache_hit" not in left
    assert (right["status"], right["run_ids"]) == ("completed", ["4"])
    assert (right["cache_hit"], right["original_run_id"], right["cache_key"]) == (
        True, "1", "ck",
    )
    assert "error" not in right
    assert derive_overall_status(states, thread_alive=False, error=None) == "failed"
