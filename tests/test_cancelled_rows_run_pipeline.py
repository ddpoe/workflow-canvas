"""
Tests for the cancelled-run-rows feature (Docker-free; Snakemake mocked).

These exercise the full wired path through ``wfc.execution.run_pipeline`` --
including the fail_pipeline -> _write_cancelled_rows ordering on failure
and the always-on walk on --keep-going partial-prune success exits --
plus the SSE log-stream endpoint's terminal event for cancelled rows.

Snakemake itself is mocked (subprocess.Popen + generate_snakefile) because
running the real engine is too heavy for a unit test.  The run state
Snakemake would have left behind is not mocked: the scenario's targets are
really run on the harness's stub rung first, so the rows the walk
reconciles are rows production wrote -- a failed run because a method
exited non-zero, an in-flight run because the claim registered it and the
abort landed before the record.
"""

from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import stub_docker_image_inspect
from tests.fixtures.routes import canvas_client
from tests.harness import (
    Scenario,
    completed,
    exits,
    node,
    observe_after_pipeline,
    run_scenario,
    selector,
    wire,
)
from wfc.persistence import Run, get_session

#: Both run_pipeline scenarios fan the same two samples across the DAG.
SAMPLES = ["S1", "S2"]

A_S1 = ("a", "S1", "default")
A_S2 = ("a", "S2", "default")
B_S1 = ("b", "S1", "default")
B_S2 = ("b", "S2", "default")
C_S1 = ("c", "S1", "default")
C_S2 = ("c", "S2", "default")

#: The scenario env's image is in the Docker daemon: run_pipeline's env
#: pre-flight finds it present. The engine is stubbed, so no container runs.
_IMAGE_PRESENT = lambda ref: ref  # noqa: E731


# =============================================================================
# Scenario declarations
# =============================================================================


def _partial_prune_scenario(pid: str) -> Scenario:
    """A->B->C over two samples, with B failing for S1 only.

    Node ids stay distinct from method names so the walk's node-keyed
    lookups are exercised rather than its legacy ``node_id == method_name``
    collapse.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("a", method="method_a", inputs=[wire("sel")]),
            node("b", method="method_b", inputs=[wire("a")],
                 behavior_by_sample={"S1": exits(1)}),
            node("c", method="method_c", inputs=[wire("b")]),
        ],
        samples=list(SAMPLES),
        pipeline_id=pid,
    )


def _hard_abort_scenario(pid: str) -> Scenario:
    """A->B over two samples, with A failing for S1 only.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("a", method="method_a", inputs=[wire("sel")],
                 behavior_by_sample={"S1": exits(1)}),
            node("b", method="method_b", inputs=[wire("a")]),
        ],
        samples=list(SAMPLES),
        pipeline_id=pid,
    )


def _cache_hit_scenario(pid: str) -> Scenario:
    """A->B over two samples, with A already run for S1 before the scenario.

    Seeding A at S1 makes that one target a cache hit when the scenario
    proper runs: production writes an audit row carrying
    ``cache_source_run_id`` rather than executing the method again. A at S2
    and B at both samples run fresh, so the post-pipeline population holds a
    cache-hit row beside ordinary executed ones.

    The seeded run is written before the harness snapshots its run-id
    baseline, so the source row sits below that boundary and the audit row
    is the only row the cache-hit target contributes.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("a", method="method_a", inputs=[wire("sel")]),
            node("b", method="method_b", inputs=[wire("a")]),
        ],
        samples=list(SAMPLES),
        prior_runs=[completed("a", sample="S1")],
        pipeline_id=pid,
    )


def _sse_cancelled_scenario(pid: str) -> Scenario:
    """A->B over one sample, with A failing.

    The pipeline-end walk writes B cancelled under A's failure; that row is
    the SSE log-stream test's subject.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("a", method="method_a", inputs=[wire("sel")], behavior=exits(1)),
            node("b", method="method_b", inputs=[wire("a")]),
        ],
        samples=["S1"],
        pipeline_id=pid,
    )


# =============================================================================
# Tier 3: end-to-end run_pipeline paths
# =============================================================================


@workflow(
    purpose="--keep-going partial prune: one sample fails mid-DAG, surviving "
            "sample completes, cancelled rows appear only for the failed "
            "sample's descendants (Tier 3).",
)
def test_keep_going_partial_prune_writes_cancelled_rows_for_failed_sample_only(
    git_project, monkeypatch,
):
    """Full run_pipeline path exercised with a mocked Snakemake subprocess.

    Scenario: A->B->C, two samples S1/S2. S1 fails at B (so C never runs for
    S1); S2 succeeds all the way through. After run_pipeline returns, we
    expect one cancelled row (C for S1) linked to S1's B-failure.
    """
    from wfc.execution import run_pipeline

    pid = "pipe-keep-going-1"

    Step(step_num=1, name="Run the DAG's targets with S1 failing at B",
         purpose="Produce the state --keep-going leaves behind, by running it")

    # pipeline_end=False: the walk under test is the one run_pipeline makes.
    obs = run_scenario(_partial_prune_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, pipeline_end=False)
    project = obs.project
    method_ids = project.method_ids
    b_s1_failed = obs.runs[B_S1].run_id

    # The premise, produced rather than asserted by construction: B failed
    # for S1, C never started for S1, and S2 ran the whole way through.
    assert obs.run_row(B_S1)["status"] == "failed"
    assert obs.runs[C_S1].skipped, "C must not have run for the failed sample"
    assert obs.run_row(C_S2)["status"] == "completed"

    Step(step_num=2, name="Invoke run_pipeline with mocked Snakemake",
         purpose="Keep-going returncode 0 -- success path invokes walk")

    gen_patch, popen_patch = mocked_snakemake(0)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch:
        run_pipeline(
            pipeline_path=str(project.pipeline_json),
            project_root=str(project.root),
            wfc_root=str(project.root),
            pipeline_id=pid,
        )

    Step(step_num=3, name="Assert cancelled row for S1's C only",
         purpose="Partial-prune: only failed sample's descendants get cancelled rows")

    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()

    assert len(cancelled) == 1, (
        f"Expected exactly 1 cancelled row (S1's C); got {len(cancelled)}: "
        f"{[(r.method_id, r.sample) for r in cancelled]}"
    )
    only = cancelled[0]
    assert only.sample == "S1"
    assert only.method_id == method_ids["method_c"]
    assert only.cancelled_due_to_run_id == b_s1_failed

    # And S2 should have no cancelled rows whatsoever.
    with get_session() as s:
        s2_cancelled = s.exec(
            select(Run).where(
                Run.pipeline_id == pid,
                Run.status == "cancelled",
                Run.sample == "S2",
            )
        ).all()
    assert s2_cancelled == []


@workflow(
    purpose="Hard-abort (no --keep-going): a failure fail-fasts Snakemake, "
            "all descendants across all samples become cancelled rows "
            "(Tier 3).",
)
def test_hard_abort_writes_cancelled_rows_for_all_sample_descendants(
    git_project, monkeypatch,
):
    """Scenario: A->B, two samples. S1's A fails; Snakemake exits non-zero
    before running anything for S2. fail_pipeline flips S2's in-flight
    rows to 'failed'; the walk then writes cancelled rows for every
    descendant of every failed ancestor.
    """
    from wfc.execution import run_pipeline

    pid = "pipe-hard-abort-1"

    Step(step_num=1, name="Run the DAG until the abort catches S2's A in flight",
         purpose="Produce a real failed run and a real in-flight run")

    # S1's A fails. S2's A is the target the abort interrupts: it runs
    # through the claim phase only, so its row is the 'running' row
    # production's own claim writes, and nothing after it is scheduled.
    obs = run_scenario(_hard_abort_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, interrupted=A_S2,
                       pipeline_end=False)
    project = obs.project
    method_ids = project.method_ids
    a_s1_failed = obs.runs[A_S1].run_id
    a_s2_running = obs.runs[A_S2].run_id

    assert obs.run_row(A_S1)["status"] == "failed"
    assert obs.run_row(A_S2)["status"] == "running"
    assert obs.runs[B_S1].skipped and obs.runs[B_S2].skipped, (
        "a fail-fast abort schedules nothing downstream for either sample"
    )

    Step(step_num=2, name="run_pipeline with failing Snakemake",
         purpose="Hard-abort returncode != 0 drives fail_pipeline + walk")

    gen_patch, popen_patch = mocked_snakemake(1)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch:
        with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
            run_pipeline(
                pipeline_path=str(project.pipeline_json),
                project_root=str(project.root),
                wfc_root=str(project.root),
                pipeline_id=pid,
            )

    Step(step_num=3, name="Assert both samples' B are cancelled",
         purpose="fail_pipeline flipped S2's A to failed; walk fills B for S1 & S2")

    with get_session() as s:
        # fail_pipeline should have flipped S2's running A to failed.
        a_s2_row = s.exec(
            select(Run).where(Run.id == a_s2_running)
        ).first()
        assert a_s2_row.status == "failed"

        cancelled = s.exec(
            select(Run).where(
                Run.pipeline_id == pid, Run.status == "cancelled"
            ).order_by(Run.sample)
        ).all()

    # Both S1's B and S2's B should be cancelled.
    cancelled_by_sample = {(r.sample, r.method_id): r for r in cancelled}
    assert (("S1", method_ids["method_b"]) in cancelled_by_sample), (
        f"Missing S1/method_b cancelled row; got {list(cancelled_by_sample)}"
    )
    assert (("S2", method_ids["method_b"]) in cancelled_by_sample), (
        f"Missing S2/method_b cancelled row; got {list(cancelled_by_sample)}"
    )

    # S1's cancelled B points at S1's failed A; S2's cancelled B points at S2's now-failed A.
    assert (cancelled_by_sample[("S1", method_ids["method_b"])]
            .cancelled_due_to_run_id == a_s1_failed)
    assert (cancelled_by_sample[("S2", method_ids["method_b"])]
            .cancelled_due_to_run_id == a_s2_running)


@workflow(
    purpose="A cancelled run's failure ending: the engine exits non-zero while "
            "the caller's cancellation probe answers true, so fail_pipeline is "
            "skipped and the in-flight row keeps its state, the walk still "
            "writes the cancelled rows under the real failure, and "
            "run_pipeline still raises the failure message (Tier 2).",
)
def test_cancelled_run_keeps_its_rows_and_still_walks_and_raises(
    git_project, monkeypatch,
):
    """Same starting state as the hard abort, with the caller cancelling.

    The cancel handler owns row state once the probe answers true: the
    ending must not flip S2's in-flight A to failed. The walk and the raise
    are unconditional, so S1's B still gets its cancelled row under S1's
    failed A, and S2's B gets none (its ancestor is not a failure).
    """
    from wfc.execution import run_pipeline

    pid = "pipe-cancelled-ending-1"

    obs = run_scenario(_hard_abort_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, interrupted=A_S2,
                       pipeline_end=False)
    project = obs.project
    method_ids = project.method_ids
    a_s1_failed = obs.runs[A_S1].run_id
    a_s2_running = obs.runs[A_S2].run_id
    assert obs.run_row(A_S1)["status"] == "failed"
    assert obs.run_row(A_S2)["status"] == "running"

    gen_patch, popen_patch = mocked_snakemake(1)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch:
        with pytest.raises(RuntimeError, match="Snakemake pipeline failed"):
            run_pipeline(
                pipeline_path=str(project.pipeline_json),
                project_root=str(project.root),
                wfc_root=str(project.root),
                pipeline_id=pid,
                is_cancelled=lambda: True,
            )

    with get_session() as s:
        a_s2_row = s.exec(select(Run).where(Run.id == a_s2_running)).first()
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()

    # The probe answered true, so fail_pipeline did not run: the in-flight
    # row is untouched.
    assert a_s2_row.status == "running"

    # The walk still ran: S1's B under S1's failed A, and nothing for S2,
    # whose A is in flight rather than failed.
    cancelled_by_sample = {(r.sample, r.method_id): r for r in cancelled}
    assert set(cancelled_by_sample) == {("S1", method_ids["method_b"])}, (
        f"got {list(cancelled_by_sample)}"
    )
    assert (cancelled_by_sample[("S1", method_ids["method_b"])]
            .cancelled_due_to_run_id == a_s1_failed)


@workflow(
    purpose="A cache-hit target counts once at the post-pipeline door: the "
            "audit row production wrote is the target's only terminal row, "
            "and invariant 1 reports checked and clean rather than reading "
            "the audit row and its cache source as a double write (Tier 3).",
)
def test_pack_counts_a_cache_hit_target_once_after_run_pipeline(git_project,
                                                                monkeypatch):
    """The post-pipeline door's fresh run-table query over a cache hit.

    Invariant 1's artifact-derived expression re-queries the run table after
    the pipeline-level call, keyed on (method, sample, params). A cache hit
    is the one starting state where two rows share that key -- the seeded
    source run and the audit row pointing at it -- so it is where a
    population that ignored the run-id baseline would report a double write
    against correct production behavior.
    """
    from wfc.execution import run_pipeline

    pid = "pipe-cache-hit-pack"

    Step(step_num=1, name="Run the DAG with A already run for S1",
         purpose="Produce a real cache hit by running the scenario, rather "
                 "than writing an audit row by hand",
         critical="pipeline_end=False: the walk under test is run_pipeline's")

    obs = run_scenario(_cache_hit_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, pipeline_end=False)

    # The premise, produced rather than asserted by construction.
    audit_row = obs.run_row(A_S1)
    assert audit_row["cache_source_run_id"] is not None, (
        "A at S1 must be a cache-hit audit row for this test to mean anything"
    )
    assert obs.run_row(A_S2)["cache_source_run_id"] is None, (
        "A at S2 has no seeded prior run and must have executed"
    )
    assert obs.run_row(B_S1)["status"] == "completed"
    assert obs.run_row(B_S2)["status"] == "completed"

    Step(step_num=2, name="Invoke run_pipeline with mocked Snakemake",
         purpose="Returncode 0 -- the success path invokes the walk, which is "
                 "what the post-pipeline door exists to observe")

    gen_patch, popen_patch = mocked_snakemake(0)
    stub_docker_image_inspect(monkeypatch, _IMAGE_PRESENT)
    with gen_patch, popen_patch:
        run_pipeline(
            pipeline_path=str(obs.project.pipeline_json),
            project_root=str(obs.project.root),
            wfc_root=str(obs.project.root),
            pipeline_id=pid,
        )

    Step(step_num=3, name="Evaluate the pack at the post-pipeline door",
         purpose="Invariant 1 must be CHECKED, not skipped, and clean: the "
                 "cache-hit target contributes exactly one terminal row")

    fresh = observe_after_pipeline(obs)
    report = fresh.invariants
    assert "single-run-record" in report.checked, report
    assert "single-run-record" not in report.skipped, report
    assert report.failures == {}, report

    Step(step_num=4, name="Two rows share the key; the population holds one",
         purpose="Naming what makes the cache hit the discriminating starting "
                 "state — the source row and the audit row are both on disk "
                 "under the same (method, sample, params) key, and only the "
                 "baseline filter keeps invariant 1 from reading them as a "
                 "double write")

    rows = fresh.rows_for_node("a", sample="S1")
    assert len(rows) == 2, (
        f"expected the seeded source row and the audit row that points at it; "
        f"got {len(rows)}: {rows}"
    )
    source, audit = rows
    assert source["cache_source_run_id"] is None, source
    assert audit["cache_source_run_id"] == source["id"], (source, audit)
    assert audit["id"] == audit_row["id"]

    counted = [r for r in rows if (r["id"] or 0) > fresh.baseline_run_id]
    assert counted == [audit], (
        f"invariant 1 counts rows above the run-id baseline "
        f"({fresh.baseline_run_id}); the seeded source must sit below it and "
        f"the audit row above it, got {counted}"
    )
    assert fresh.rows_for_node("a", sample="S1", status="cancelled") == []


# =============================================================================
# Tier 3: SSE log-stream endpoint on a cancelled run
# =============================================================================


@workflow(
    purpose="SSE log-stream endpoint treats cancelled runs as terminal and "
            "emits a terminal event with status='cancelled' (Tier 3).",
)
def test_stream_logs_cancelled_run_emits_terminal_event_and_closes(
    git_project, monkeypatch,
):
    """GET /api/wfc/run/{id}/stream-logs on a cancelled run returns a terminal
    SSE event with status='cancelled', then closes.

    Cancelled rows have no stdout/stderr log files on disk (they never ran).
    The endpoint must still classify them as terminal (not try to live-tail)
    and must pass the status through unchanged (unlike 'completed'->'success').
    """
    pid = "pipe-sse-cancelled-1"
    # A fails; the pipeline-end walk writes B cancelled under that failure.
    obs = run_scenario(_sse_cancelled_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch)
    a_failed = obs.runs[A_S1].run_id
    assert obs.run_row(A_S1)["status"] == "failed"
    (cancelled_row,) = obs.rows_for_node("b", status="cancelled")
    assert cancelled_row["cancelled_due_to_run_id"] == a_failed
    cancelled_id = cancelled_row["id"]

    Step(step_num=1, name="Hit /api/wfc/run/{id}/stream-logs",
         purpose="Cancelled run is terminal; endpoint must not block live-polling")

    client = canvas_client(obs.project.root, monkeypatch)
    resp = client.get(f"/api/wfc/run/{cancelled_id}/stream-logs")

    Step(step_num=2, name="Assert terminal cancelled event + clean close",
         purpose="Exactly one terminal event with status='cancelled'")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # Parse SSE body.
    events = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines = [
            line[len("data: "):]
            for line in block.splitlines()
            if line.startswith("data: ")
        ]
        if data_lines:
            events.append(json.loads("".join(data_lines)))

    terminal = [e for e in events if e.get("type") == "terminal"]
    assert len(terminal) == 1, f"Expected one terminal event; got {events}"
    assert terminal[0]["status"] == "cancelled", (
        f"Expected status='cancelled'; got {terminal[0]}"
    )
