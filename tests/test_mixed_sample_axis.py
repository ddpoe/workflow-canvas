"""The mixed sample axis: a merge-all (fan-in) branch beside a per-sample branch.

A pipeline is *mixed* when it carries both a sample-collapsed branch (fed by
a fan-in ``input_selector``) and an ordinary per-sample branch (fed by a
plain one). Collapse is a whole-node property that propagates downstream,
so the only mixed shape the engine can run is the multi-root one declared
here: two selector roots, two disjoint chains. The per-sample chain runs
once per (sample, variant); the collapsed chain runs once per variant at
the collapsed-sample sentinel.

Three things have to hold for a canvas author who draws that shape:

* the pipeline-end cancelled-rows walk reconciles the per-sample side after
  a failure;
* the generated Snakefile schedules each leaf on its own sample axis;
* the one collapse shape the engine cannot run -- a collapsed node drawn
  downstream of a per-sample *method* step -- is rejected when the document loads, instead of exporting a Snakefile whose
  sample wildcard nothing can bind.

Snakemake itself is mocked in the walk test (the position-4 pattern of
``tests/test_cancelled_rows_run_pipeline.py``): the scenario's targets are
really run on the harness's stub rung first, so the rows the walk reconciles
are rows production wrote.
"""
from __future__ import annotations

import json

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.fixtures.conftest import mocked_snakemake
from tests.fixtures.fakes import stub_docker_image_inspect
from tests.harness import (
    Scenario,
    build_project,
    exits,
    node,
    observe_after_pipeline,
    run_scenario,
    selector,
    wire,
)
from wfc import layout
from wfc.contracts import COLLAPSED_SAMPLE
from wfc.persistence import Run, get_session

SAMPLES = ["s1", "s2"]
VARIANTS = {"loose": {"threshold": 0.5}, "strict": {"threshold": 0.9}}


# =============================================================================
# Scenario declarations
# =============================================================================


def _mixed_scenario(pid: str) -> Scenario:
    """A per-sample chain beside a collapsed chain, from two selector roots.

    ``sel`` (plain) feeds ``qc`` -> ``report``; ``bundle`` (fan-in) feeds
    ``merge`` -> ``summary``. ``qc`` fails for s1 only. ``report`` carries a
    canvas label, which the walk ignores: every node is keyed by its node id.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector("sel"),
            node("qc", inputs=[wire("sel")],
                 behavior_by_sample={"s1": exits(1)}),
            node("report", inputs=[wire("qc")], label="Report"),
            selector("bundle", fan_mode="in"),
            node("merge", inputs=[wire("bundle", bundle=True)]),
            node("summary", inputs=[wire("merge")]),
        ],
        samples=list(SAMPLES),
        variants={v: dict(p) for v, p in VARIANTS.items()},
        pipeline_id=pid,
    )


def _collapsed_below_per_sample_scenario(pid: str) -> Scenario:
    """The unsupported shape: a collapsed node fed by a per-sample method step.

    ``merge`` takes a fan-in bundle on one slot and ``a``'s per-sample
    output on another. Collapse is decided per node, so ``merge`` would run
    once at the collapsed sample while ``a`` runs once per sample -- a
    shape the engine cannot schedule.

    Args:
        pid: Pipeline execution id.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector("sel"),
            node("a", inputs=[wire("sel")]),
            selector("bundle", fan_mode="in"),
            node("merge", inputs=[
                wire("a", target_slot="data"),
                wire("bundle", target_slot="bundle", bundle=True),
            ]),
        ],
        samples=list(SAMPLES),
        pipeline_id=pid,
    )


def _rule_block(snakefile: str, rule_name: str) -> str:
    """Return one rule's text from a generated Snakefile."""
    head = f"rule {rule_name}:\n"
    assert head in snakefile, f"no {head.strip()!r} in the Snakefile"
    body = snakefile.split(head, 1)[1]
    return body.split("\nrule ", 1)[0].split("\nonsuccess:", 1)[0]


# =============================================================================
# Tier 3: the pipeline-end walk through run_pipeline
# =============================================================================


@workflow(
    purpose="A merge-all branch beside a per-sample branch: one per-sample "
            "failure produces cancelled rows for that sample's downstream "
            "per-sample steps only, each pointing at the failed run, and "
            "none on the collapsed branch (Tier 3)",
)
def test_mixed_pipeline_walk_cancels_the_per_sample_descendants_only(
    git_project, monkeypatch,
):
    from wfc.execution import run_pipeline

    pid = "pipe-mixed-walk"

    口 = Step(step_num=1, name="Run the mixed pipeline with qc failing for s1",
              purpose="Produce the state --keep-going leaves behind by running "
                      "it: qc fails for s1 on both variants, report is never "
                      "scheduled for s1, s2 and the collapsed chain complete",
              critical="pipeline_end=False: the walk under test is the one "
                       "run_pipeline makes, not the harness's own")
    obs = run_scenario(_mixed_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, pipeline_end=False)
    project = obs.project
    for variant in VARIANTS:
        assert obs.run_row(("qc", "s1", variant))["status"] == "failed"
        assert obs.runs[("report", "s1", variant)].skipped, (
            "report must not have run for the failed sample")
        assert obs.run_row(("report", "s2", variant))["status"] == "completed"
        assert obs.run_row(("merge", COLLAPSED_SAMPLE, variant))["status"] == "completed"
        assert obs.run_row(("summary", COLLAPSED_SAMPLE, variant))["status"] == "completed"

    口 = Step(step_num=2, name="Invoke run_pipeline with mocked Snakemake",
              purpose="Keep-going returncode 0 -- the success path runs the "
                      "always-on cancelled-rows walk")
    gen_patch, popen_patch = mocked_snakemake(0)
    # The scenario env's image is in the Docker daemon (run_pipeline's env
    # pre-flight probes it); the engine is stubbed, so no container runs.
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    with gen_patch, popen_patch:
        run_pipeline(
            pipeline_path=str(project.pipeline_json),
            project_root=str(project.root),
            wfc_root=str(project.root),
            pipeline_id=pid,
        )

    口 = Step(step_num=3, name="Assert cancelled rows for s1's report only",
              purpose="One row per variant for report@s1, each pointing at "
                      "qc@s1 of the same variant; nothing for s2; nothing on "
                      "the collapsed branch")
    with get_session() as s:
        cancelled = s.exec(
            select(Run).where(Run.pipeline_id == pid, Run.status == "cancelled")
        ).all()
        rows = [
            {"method_id": r.method_id, "sample": r.sample, "node_id": r.node_id,
             "params": r.params, "cause": r.cancelled_due_to_run_id}
            for r in cancelled
        ]

    assert len(rows) == len(VARIANTS), (
        f"expected one cancelled row per variant for report@s1 "
        f"({len(VARIANTS)}); got {len(rows)}: {rows}"
    )
    report_id = project.method_ids["report"]
    for row in rows:
        assert row["method_id"] == report_id, row
        assert row["sample"] == "s1", row
        assert row["node_id"] == "report", row

    # Each cancelled row points at the failed qc run of ITS variant.
    cause_by_params = {
        json.dumps(row["params"], sort_keys=True): row["cause"] for row in rows
    }
    for variant, params in VARIANTS.items():
        expected = obs.runs[("qc", "s1", variant)].run_id
        assert cause_by_params[json.dumps(params, sort_keys=True)] == expected, (
            f"report@s1@{variant} must point at qc@s1@{variant} "
            f"(run {expected}); got {cause_by_params}"
        )


# =============================================================================
# Tier 2: the generated Snakefile schedules each leaf on its own axis
# =============================================================================


@workflow(
    purpose="The Snakefile for a mixed document lists per-sample targets for "
            "the per-sample leaf and one-per-variant targets for the "
            "collapsed leaf, and per-sample rules keep their sample wildcard "
            "(Tier 2)",
)
def test_mixed_pipeline_snakefile_schedules_each_leaf_on_its_own_axis(
    git_project, monkeypatch,
):
    from wfc.execution import load_pipeline_from_path
    from wfc.orchestration import generate_snakefile

    pid = "pipe-mixed-gen"
    project = build_project(_mixed_scenario(pid), root=git_project,
                            monkeypatch=monkeypatch)
    pdef = load_pipeline_from_path(project.pipeline_json)
    snakefile = generate_snakefile(
        pdef, project_root=str(project.root), pipeline_id=pid,
    )

    rule_all = _rule_block(snakefile, "all")
    report_target = layout.run_sentinel_relpath(pid, "report", "{sample}", "{variant}")
    summary_target = layout.run_sentinel_relpath(pid, "summary", COLLAPSED_SAMPLE, "{variant}")
    assert f'expand("{report_target}", sample=SAMPLES, variant=VARIANT_NAMES)' in rule_all, (
        f"the per-sample leaf must expand over samples x variants:\n{rule_all}"
    )
    assert f'expand("{summary_target}", variant=VARIANT_NAMES)' in rule_all, (
        f"the collapsed leaf must expand over variants only:\n{rule_all}"
    )
    # Non-leaves are not targets.
    for inner in ("qc", "merge"):
        assert f"/{inner}/" not in rule_all, f"{inner} is not a leaf:\n{rule_all}"

    for per_sample in ("qc", "report"):
        assert "--sample {wildcards.sample}" in _rule_block(snakefile, per_sample), (
            f"rule {per_sample} must keep its sample wildcard")
    for collapsed in ("merge", "summary"):
        assert f"--sample {COLLAPSED_SAMPLE} " in _rule_block(snakefile, collapsed), (
            f"rule {collapsed} must run at the collapsed sample")


# =============================================================================
# Tier 2: the unsupported collapse shape is rejected at load
# =============================================================================


@workflow(
    purpose="A node drawn with a fan-in selector on one slot and a per-sample "
            "method on another is rejected when the document loads, by the "
            "same sole-upstream rule and the same message the canvas's "
            "structural validation gives",
)
def test_collapsed_node_below_per_sample_method_is_rejected_at_load(
    git_project, monkeypatch,
):
    from wfc.execution import load_pipeline_from_path

    project = build_project(
        _collapsed_below_per_sample_scenario("pipe-collapsed-below-per-sample"),
        root=git_project, monkeypatch=monkeypatch)

    with pytest.raises(ValueError,
                       match=r"only supported when it is the sole upstream") as excinfo:
        load_pipeline_from_path(project.pipeline_json)

    message = str(excinfo.value)
    assert "'bundle'" in message and "'merge'" in message, message
    assert "2 upstreams" in message, message


# =============================================================================
# Tier 3: the standing pack sees the walk through the post-pipeline door
# =============================================================================


@workflow(
    purpose="After run_pipeline's cancelled-rows walk, the standing pack "
            "evaluated at the post-pipeline door reports invariant 1 checked "
            "and clean against the rows actually in the database -- cancelled "
            "rows counting toward uniqueness only (Tier 3)",
)
def test_pack_sees_the_cancelled_walk_after_run_pipeline(git_project, monkeypatch):
    from wfc.execution import run_pipeline

    pid = "pipe-mixed-pack"

    口 = Step(step_num=1, name="Run the mixed pipeline, then run_pipeline's walk",
              purpose="The pack asserted at _finish is evaluated before the "
                      "pipeline-level call, so it cannot see the rows the "
                      "walk writes afterwards",
              critical="pipeline_end=False, exactly as the walk test: the "
                       "walk under test is run_pipeline's own")
    obs = run_scenario(_mixed_scenario(pid), root=git_project,
                       monkeypatch=monkeypatch, pipeline_end=False)
    assert "single-run-record" in obs.invariants.checked
    assert not [r for r in obs.run_rows if r["status"] == "cancelled"], (
        "the bundle read at _finish predates the walk")

    gen_patch, popen_patch = mocked_snakemake(0)
    # The scenario env's image is in the Docker daemon (run_pipeline's env
    # pre-flight probes it); the engine is stubbed, so no container runs.
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    with gen_patch, popen_patch:
        run_pipeline(
            pipeline_path=str(obs.project.pipeline_json),
            project_root=str(obs.project.root),
            wfc_root=str(obs.project.root),
            pipeline_id=pid,
        )

    口 = Step(step_num=2, name="Evaluate the pack at the post-pipeline door",
              purpose="A fresh query of the run table, not the bundle read at "
                      "_finish; invariant 1 must be CHECKED, not skipped, and "
                      "clean",
              critical="Invariants 2 and 3 keep their observed-phase "
                       "expressions and are unchanged by the door")
    fresh = observe_after_pipeline(obs)
    report = fresh.invariants
    assert "single-run-record" in report.checked, report
    assert "single-run-record" not in report.skipped, report
    assert report.failures == {}, report
    assert "completion-signals-agree" in report.checked, report
    assert "output-writers-agree" in report.checked, report

    口 = Step(step_num=3, name="The fresh bundle carries the walk's rows",
              purpose="One cancelled row per variant for report@s1 is what the "
                      "door re-read -- counted toward uniqueness, not toward "
                      "the claimed count")
    cancelled = fresh.rows_for_node("report", sample="s1", status="cancelled")
    assert len(cancelled) == len(VARIANTS), cancelled
    assert fresh.rows_for_node("report", sample="s2", status="cancelled") == []
