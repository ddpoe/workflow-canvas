"""Execution's case catalog, expressed against the scenario harness.

One test per catalog case, in the catalog's order, plus the diverse
end-to-end path the unit's standing test requirements call for: fan-in,
a cache hit and one failing node in a single pipeline, which is the
combination that drives every record-tail exit in one run.

Every test here runs on the stub rung — no container daemon, default
selection. The standing invariant pack is asserted by the harness after
each run, so each test states only what its own case is about.
"""
from __future__ import annotations

import pytest
from axiom_annotations import Step, workflow

from tests.fixtures.fakes import (
    stub_docker_image_inspect,
    stub_readiness_probes,
    stub_record_writers,
)
from tests.harness import (
    Behavior,
    Phase,
    Scenario,
    build_project,
    chain,
    completed,
    exits,
    fan_in,
    node,
    reference,
    run_scenario,
    run_target,
    selector,
    two_node_chain,
    wire,
)

T1 = ("n1", "s1", "default")


def _names(paths) -> list[str]:
    """Return the basenames of a slot's resolved input paths."""
    return [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths]


# =============================================================================
# catalog.fresh-run
# =============================================================================

@workflow(purpose="Fresh run: all five phases run and the step leaves a "
                  "completed row, output rows, the sentinel and both sidecars")
def test_fresh_run_leaves_all_five_artifacts(git_project, monkeypatch):
    obs = run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch)

    assert obs.phases_ran(T1) == ["claim", "materialize", "dispatch",
                                  "collect", "record"]
    assert obs.exit_code(T1) == 0
    assert obs.run_row(T1)["status"] == "completed"
    assert [r["output_name"] for r in obs.output_rows_for(T1)] == ["data.csv"]
    assert T1 in obs.sentinels
    assert obs.run_id_sidecars[T1] == obs.runs[T1].run_id
    assert obs.outcomes[T1]["status"] == "completed"


# =============================================================================
# catalog.cache-hit
# =============================================================================

@workflow(purpose="Cache hit: a completed audit row at claim time, nothing "
                  "restored to disk, and materialize/dispatch/collect skipped")
def test_cache_hit_inserts_audit_row_and_skips_three_phases(git_project,
                                                            monkeypatch):
    scn = Scenario(prior_runs=[completed("n1")])
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)

    assert obs.phases_ran(T1) == ["claim", "record"]
    assert obs.runs[T1].ending == "cached"
    assert obs.outcomes[T1]["status"] == "cached"
    assert T1 in obs.sentinels

    rows = obs.rows_for_node("n1")
    source, audit = rows[0], rows[-1]
    assert len(rows) == 2
    assert audit["cache_source_run_id"] == source["id"]
    assert audit["status"] == "completed"
    # Nothing is restored to disk: the audit run gets no archive directory.
    assert not (obs.project.runs_dir / f"{audit['id']:08d}").exists()


@pytest.mark.parametrize(
    ("nodes", "target", "recorded_node_id"),
    [
        pytest.param([selector("sel"), node("n1", inputs=[wire("sel")])],
                     "n1", "n1", id="string-id-document"),
        # A legacy document numbers its nodes and names each by its (unique)
        # method; the run still records the node's own document id.
        pytest.param([selector("sel"),
                      node("1", method="legacy_m", inputs=[wire("sel")])],
                     "legacy_m", "1", id="legacy-numeric-id-document"),
    ],
)
@workflow(purpose="Every run records its node: the claimed run and a later "
                  "cache hit's audit row both carry the pipeline node's own "
                  "document id")
def test_every_run_records_its_node_on_new_and_cache_hit_rows(
        git_project, monkeypatch, nodes, target, recorded_node_id):
    scn = Scenario(nodes=nodes, prior_runs=[completed(target)])
    obs = run_target(scn, target, root=git_project, monkeypatch=monkeypatch)

    assert obs.runs[(target, "s1", "default")].ending == "cached"
    rows = obs.rows_for_node(recorded_node_id)
    source, audit = rows[0], rows[-1]
    assert len(rows) == 2
    assert audit["cache_source_run_id"] == source["id"]
    assert source["node_id"] == recorded_node_id
    assert audit["node_id"] == recorded_node_id


# =============================================================================
# catalog.hit-missing-archive
# =============================================================================

@workflow(purpose="A matching prior run whose archive is gone is a claim miss, "
                  "so the target executes fresh")
def test_deleted_archive_makes_the_claim_a_miss(git_project, monkeypatch):
    scn = Scenario(prior_runs=[completed("n1", archive_deleted=True)])
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)

    assert obs.phases_ran(T1) == ["claim", "materialize", "dispatch",
                                  "collect", "record"]
    assert obs.runs[T1].ending == "completed"
    assert obs.outcomes[T1]["status"] == "completed"


# =============================================================================
# catalog.parent-sidecar-failure
# =============================================================================

@workflow(purpose="A parent whose run-id sidecar is gone fails the step before "
                  "dispatch, with no completion signal written")
def test_missing_parent_sidecar_fails_before_dispatch(git_project, monkeypatch):
    scn = two_node_chain(prior_runs=[completed("head", sentinel_deleted=True)])
    target = ("tail", "s1", "default")
    obs = run_target(scn, "tail", root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(target) == 1
    assert "dispatch" not in obs.phases_ran(target)
    assert target not in obs.sentinels
    # The materialize failure is recorded like any failed run: a failed
    # outcome carrying the message, never a success.
    assert obs.outcomes[target]["status"] == "failed"
    assert obs.outcomes[target]["error"]


# =============================================================================
# catalog.dirty-repo
# =============================================================================

@workflow(purpose="A dirty working tree fails the claim before any run row is "
                  "written, with no escape hatch")
def test_dirty_tree_fails_the_claim_with_zero_rows(git_project, monkeypatch):
    obs = run_target(Scenario(dirty_repo=True), "n1",
                     root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(T1) == 1
    assert obs.phases_ran(T1) == ["claim"]
    assert obs.run_rows == []
    assert "single-run-record" in obs.invariants.skipped


# =============================================================================
# catalog.selector-root-fallback
# =============================================================================

@workflow(purpose="A root node with an incoming selector edge and no parent "
                  "runs is filled from the registered sample directory")
def test_selector_root_fills_the_target_slot_from_the_sample(git_project,
                                                             monkeypatch):
    obs = run_target(Scenario(), "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    slot_paths = obs.phase_args("dispatch", T1)["slot_paths"]
    assert _names(slot_paths["data"]) == ["s1.csv"]


# =============================================================================
# catalog.reference-root-suppression
# =============================================================================

@workflow(purpose="A reference-rooted node suppresses the sample fallback and "
                  "receives the referenced artifact under its declared slot")
def test_reference_root_suppresses_the_sample_fallback(git_project, monkeypatch):
    scn = Scenario(
        nodes=[
            selector(),
            node("src", inputs=[wire("sel")],
                 output_files={"data": "referenced_artifact.csv"}),
            reference("ref", run_of="src"),
            node("n1", inputs=[wire("ref", source_slot=None)]),
        ],
        prior_runs=[completed("src")],
    )

    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    slot_paths = obs.phase_args("dispatch", T1)["slot_paths"]
    assert _names(slot_paths["data"]) == ["referenced_artifact.csv"]


# =============================================================================
# catalog.collapsed-fan-in
# =============================================================================

@workflow(purpose="A collapsed fan-in root materializes one file per bundled "
                  "sample in the supplied order, and names an empty sample")
def test_collapsed_fan_in_preserves_order_and_names_the_offender(git_project,
                                                                 monkeypatch,
                                                                 tmp_path,
                                                                 capsys):
    samples = ["s3", "s1", "s2"]
    target = ("bundle", "__all__", "default")
    scn = Scenario(nodes=chain("bundle", fan_mode="in"), samples=samples)
    obs = run_target(scn, "bundle", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    slot_paths = obs.phase_args("dispatch", target)["slot_paths"]
    assert _names(slot_paths["data"]) == ["s3.csv", "s1.csv", "s2.csv"]

    empty = Scenario(nodes=chain("bundle", fan_mode="in"), samples=samples,
                     empty_samples=("s1",))
    capsys.readouterr()
    failed = run_target(empty, "bundle", root=tmp_path / "empty_sample_project",
                        monkeypatch=monkeypatch)

    assert failed.exit_code(target) == 1
    assert "dispatch" not in failed.phases_ran(target)
    err = capsys.readouterr().err
    assert "'s1'" in err and "bundle" in err


# =============================================================================
# catalog.method-failure
# =============================================================================

@workflow(purpose="A method exiting non-zero lifts its real error out of the "
                  "step's stderr log, marks the run failed and propagates")
def test_method_failure_lifts_the_error_and_marks_the_run_failed(git_project,
                                                                 monkeypatch):
    scn = Scenario(nodes=chain("n1"))
    scn.node_by_id("n1").behavior = exits(
        2, stderr="ValueError: the harness method refused to run\n")

    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(T1) == 1
    assert obs.runs[T1].ending == "method-failed"
    row = obs.run_row(T1)
    assert row["status"] == "failed"
    assert row["error_message"] == "ValueError: the harness method refused to run"
    assert obs.outcomes[T1]["status"] == "failed"
    assert T1 not in obs.sentinels


# =============================================================================
# catalog.missing-slot
# =============================================================================

@workflow(purpose="A method exiting zero without one declared output slot "
                  "fails the run with an error naming the method and the slot")
def test_missing_declared_slot_fails_the_run_naming_method_and_slot(git_project,
                                                                    monkeypatch):
    scn = Scenario(nodes=chain("n1"))
    spec = scn.node_by_id("n1")
    spec.outputs = {"data": ".csv", "extra": ".csv"}
    spec.behavior = Behavior(skip_slots=("extra",))

    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(T1) == 1
    assert obs.runs[T1].ending == "missing-slot"
    row = obs.run_row(T1)
    assert row["status"] == "failed"
    assert "n1" in row["error_message"] and "extra" in row["error_message"]
    assert obs.outcomes[T1]["status"] == "failed"


# =============================================================================
# catalog.dispatch-endings: no-container / script-missing / launch-failure
# =============================================================================

def _document_names_an_env_nothing_built(spec) -> None:
    """The document's env misses the manifest; method.yaml's still registers."""
    spec.document_env = "ghost-env"


def _document_names_a_script_not_on_disk(spec) -> None:
    """The method directory is real; the script the document names is not."""
    spec.script_name = "ghost.py"


def _launch_is_refused_at_the_boundary(spec) -> None:
    """Dispatch builds the argv; the process boundary refuses to start it."""
    spec.behavior = Behavior(launch_error="the harness refused to launch")


@pytest.mark.parametrize(
    ("ending", "declare", "error_head", "console_prefix", "starts_container"),
    [
        ("no-container", _document_names_an_env_nothing_built,
         "node 'n1' env 'ghost-env' does not resolve to a built container image",
         "ERROR: ", False),
        ("script-missing", _document_names_a_script_not_on_disk,
         "method script not found: methods/n1/ghost.py (node 'n1', method 'n1')",
         "ERROR: ", False),
        ("launch-failure", _launch_is_refused_at_the_boundary,
         "the harness refused to launch",
         "ERROR: method execution failed: ", True),
    ],
    ids=["no-container", "script-missing", "launch-failure"],
)
@workflow(purpose="A dispatch-phase ending — the document naming an env "
                  "nothing built, a script not on the host, or a launch "
                  "refused at the process boundary — takes the failed record "
                  "tail: a failed row carrying the error, no sentinel, a "
                  "failed outcome sidecar, and the ending's own console line")
def test_dispatch_phase_endings_take_the_failed_record_tail(
    git_project, monkeypatch, capsys, ending, declare, error_head, console_prefix,
    starts_container,
):
    scn = Scenario(nodes=chain("n1"))
    declare(scn.node_by_id("n1"))

    capsys.readouterr()
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)
    err = capsys.readouterr().err

    assert obs.runs[T1].ending == ending
    assert (obs.runs[T1].dispatch_cmd is not None) == starts_container, (
        "no-container and script-missing end before any container starts; "
        "launch-failure is refused at the boundary of one dispatch built"
    )
    phases = obs.phases_ran(T1)
    assert "dispatch" in phases and "collect" not in phases, (
        f"a dispatch-phase ending goes straight to record; ran {phases}"
    )
    assert obs.exit_code(T1) == 1
    row = obs.run_row(T1)
    assert row["status"] == "failed"
    assert row["error_message"].startswith(error_head), row["error_message"]
    assert T1 not in obs.sentinels
    outcome = obs.outcomes.get(T1)
    assert outcome is not None and outcome["status"] == "failed", (
        f"expected a failed outcome sidecar for {T1}; got {outcome}"
    )
    assert outcome["error"] == row["error_message"]
    assert f"{console_prefix}{row['error_message']}" in err, err


# =============================================================================
# catalog.slurm-carve-out
# =============================================================================

@workflow(purpose="A method declaring executor: slurm is refused at dispatch "
                  "and exits WITHOUT a record: neither record-tail writer is "
                  "called, the run row stays as the claim left it, no "
                  "sentinel, no outcome sidecar, exit 1, and the carve-out "
                  "message on the console")
def test_slurm_executor_exits_without_a_record(git_project, monkeypatch, capsys):
    from wfc.execution import record as _record

    scn = Scenario(nodes=chain("n1"))
    scn.node_by_id("n1").executor = "slurm"

    # Count the record tail's two writers. Installed before the harness
    # wraps complete_run for its own snapshot, so the harness's wrapper
    # delegates to these and these to the real functions.
    writes = {"complete_run": 0, "_write_outcome": 0}
    real_complete_run, real_write_outcome = _record.complete_run, _record._write_outcome

    def counting_complete_run(*args, **kwargs):
        writes["complete_run"] += 1
        return real_complete_run(*args, **kwargs)

    def counting_write_outcome(*args, **kwargs):
        writes["_write_outcome"] += 1
        return real_write_outcome(*args, **kwargs)

    stub_record_writers(monkeypatch, complete_run=counting_complete_run,
                        write_outcome=counting_write_outcome)

    capsys.readouterr()
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch)
    err = capsys.readouterr().err

    assert obs.runs[T1].ending == "slurm"
    assert obs.exit_code(T1) == 1
    assert writes == {"complete_run": 0, "_write_outcome": 0}, (
        f"exit-without-record must call neither writer; got {writes}"
    )
    row = obs.run_row(T1)
    assert row["status"] == "running", (
        f"the row must stay as the claim left it; got {row['status']!r}"
    )
    assert row["error_message"] is None
    assert T1 not in obs.sentinels
    assert T1 not in obs.outcomes
    assert "ERROR: cluster Apptainer dispatch (executor=slurm) is not supported" in err, err


# =============================================================================
# catalog.double-write-agreement
# =============================================================================

@workflow(purpose="The collect-phase output-row writer and the completion "
                  "writer produce byte-identical rows")
def test_the_two_output_row_writers_agree(git_project, monkeypatch):
    obs = run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch)

    run = obs.runs[T1]
    assert run.output_rows_before, "the collect phase wrote no output rows"
    assert run.output_rows_before == run.output_rows_after
    assert "output-writers-agree" in obs.invariants.passed


# =============================================================================
# catalog.cancellation
# =============================================================================

@workflow(purpose="One mid-pipeline failure cancels every expected target that "
                  "never started, each linked to its nearest failed ancestor")
def test_mid_pipeline_failure_cancels_the_descendants(git_project, monkeypatch):
    from wfc.execution.lifecycle import _write_cancelled_rows

    scn = Scenario(nodes=[
        selector(),
        node("root", inputs=[wire("sel")]),
        node("left", inputs=[wire("root")], behavior=exits(1)),
        node("right", inputs=[wire("root")]),
        node("sink", inputs=fan_in("left", "right")),
    ])
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(("left", "s1", "default")) == 1
    assert obs.exit_code(("right", "s1", "default")) == 0
    assert obs.runs[("sink", "s1", "default")].skipped

    cancelled = obs.rows_for_node("sink", status="cancelled")
    failed = obs.rows_for_node("left", status="failed")
    assert len(cancelled) == 1
    assert cancelled[0]["cancelled_due_to_run_id"] == failed[0]["id"]
    assert obs.rows_for_node("right", status="cancelled") == []

    # Idempotent: re-running the walk adds nothing.
    assert _write_cancelled_rows(obs.project.pipeline_id,
                                 str(obs.project.root)) == 0


# =============================================================================
# catalog.not-runnable
# =============================================================================

@pytest.mark.parametrize("verb", ["run-step", "run-pipeline"])
@workflow(purpose="A failing readiness probe stops execution at the door with "
                  "the one-door message, before anything dispatches: for one "
                  "step, and for a whole pipeline before its env pre-flight "
                  "can mistake a stopped daemon for a missing image")
def test_readiness_probe_failure_stops_before_dispatch(git_project, monkeypatch,
                                                       capsys, verb):
    from wfc.cli import cli_main

    project = build_project(Scenario(), root=git_project, monkeypatch=monkeypatch)
    stub_readiness_probes(monkeypatch, git=None, docker="fail")
    stub_docker_image_inspect(
        monkeypatch, AssertionError("an image was probed while Docker was down"))

    if verb == "run-step":
        argv = ["run-step", "--node-id", "n1", "--sample", "s1",
                "--variant", "default",
                "--pipeline-json", str(project.pipeline_json),
                "--pipeline-id", project.pipeline_id]
    else:
        argv = ["run-pipeline", "--pipeline", str(project.pipeline_json),
                "--project-root", str(project.root)]
    rc = cli_main(argv)

    assert rc == 1
    assert "isn't ready to run" in capsys.readouterr().err
    assert not (project.runs_dir / "sentinels").exists()


# =============================================================================
# testing.requirements — one diverse end-to-end path
# =============================================================================

@workflow(
    purpose="One pipeline carrying a collapsed fan-in, a seeded cache hit and "
            "one failing node over three samples and a variant sweep, driving "
            "every record-tail exit in a single run",
    inputs="a scenario declaration with prior runs, variants and a failing node",
    outputs="run rows, output rows, sentinels, sidecars and outcomes for every "
            "target of the launched pipeline",
)
def test_one_diverse_path_drives_every_record_tail_exit(git_project, monkeypatch):
    口 = Step(step_num=1, name="Declare the pipeline",
              purpose="One document carrying a two-parent fan-in, a seeded "
                      "cache hit and one failing node, over a variant sweep")
    scn = Scenario(
        nodes=[
            selector(),
            node("qc", inputs=[wire("sel")]),
            node("align", inputs=[wire("qc")]),
            node("assemble", inputs=[wire("qc")]),
            node("merge", inputs=fan_in("align", "assemble")),
            node("report", inputs=[wire("merge")]),
        ],
        samples=["s1", "s2", "s3"],
        variants={"loose": {"threshold": 0.5}, "strict": {"threshold": 0.9}},
        prior_runs=[completed("qc", sample="s1", variant="strict")],
    )
    scn.node_by_id("align").behavior = exits(
        3, stderr="RuntimeError: align refused the input\n")

    口 = Step(step_num=2, name="Run the pipeline",
              purpose="Claim through record for every scheduled target, then "
                      "the pipeline-end cancelled-rows walk")
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=3, name="Cached exit",
              purpose="The seeded target claims a hit and takes the cached "
                      "record tail")
    cached = ("qc", "s1", "strict")
    assert obs.phases_ran(cached) == ["claim", "record"]
    assert obs.outcomes[cached]["status"] == "cached"

    口 = Step(step_num=4, name="Completed exit",
              purpose="Every unseeded qc target and the surviving fan-in "
                      "parent take the completed record tail")
    completed_targets = [("qc", "s2", "strict"), ("qc", "s1", "loose"),
                         ("assemble", "s1", "strict"),
                         ("assemble", "s3", "loose")]
    for target in completed_targets:
        assert obs.exit_code(target) == 0, target
        assert obs.outcomes[target]["status"] == "completed", target
        assert target in obs.sentinels, target

    口 = Step(step_num=5, name="Failed exit",
              purpose="The failing node takes the method-failed record tail on "
                      "every sample and variant")
    failed = [t for t in obs.targets if t[0] == "align"]
    assert len(failed) == 6
    for target in failed:
        assert obs.exit_code(target) == 1, target
        assert obs.outcomes[target]["status"] == "failed", target
        assert target not in obs.sentinels, target
        assert obs.run_row(target)["error_message"] == (
            "RuntimeError: align refused the input")

    口 = Step(step_num=6, name="Cancelled tail",
              purpose="Every target downstream of the failure is cancelled and "
                      "linked to its nearest failed ancestor")
    failed_ids = {r["id"] for r in obs.rows_for_node("align", status="failed")}
    for downstream in ("merge", "report"):
        cancelled = obs.rows_for_node(downstream, status="cancelled")
        assert len(cancelled) == 6, downstream
        assert {r["cancelled_due_to_run_id"] for r in cancelled} <= failed_ids
        assert all(obs.runs[t].skipped
                   for t in obs.targets if t[0] == downstream)
