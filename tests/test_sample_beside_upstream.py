"""A node reading its sample beside an upstream method's output, and the
refusals and failure visibility around it.

* **Delivery and key** -- a per-sample selector and an upstream method (and,
  in the three-input shape, a run reference) feeding one node: every slot
  arrives at materialize, and the sample moves the node's own cache key
  while the upstream run stays the same one.
* **Honest refusals** -- a slot the document wires but the claim was not
  handed is reported as a wfc defect; a reference whose run exists but
  whose artifact does not resolve is refused at the claim; a per-sample
  reader whose sample directory is missing or empty is refused at
  materialize, by name.
* **A refused step is visibly a failure** -- the pipeline-end walk records
  the refused target as failed with its message and cancels its
  descendants against it; the canvas status and the pipeline summary read
  the same rows and agree.

Tier 2 on the stub rung. The standing invariant pack is asserted by the
harness after each run. Two engine-rung tests (``-m integration``) run the
three-input shape and the refused-mid-chain pipeline through the real
engine in real containers.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from axiom_annotations import Step, workflow
from sqlmodel import select

from tests.conftest import requires_docker
from tests.fixtures.conftest import FIXTURE_ENV_NAME
from tests.fixtures.fakes import stub_docker_image_inspect
from tests.harness import (
    ENGINE,
    Behavior,
    Phase,
    Scenario,
    build_project,
    completed,
    drive_target,
    expand_targets,
    node,
    reference,
    run_scenario,
    run_target,
    selector,
    selector_beside_method,
    selector_beside_method_and_reference,
    wire,
)

Q = ("quantify", "s1", "default")


def _slot_names(obs, target=Q) -> dict[str, int]:
    """Slot -> how many paths materialize resolved into it."""
    return {slot: len(paths)
            for slot, paths in obs.phase_args("dispatch", target)["slot_paths"].items()}


def _parents(obs, target=Q) -> list[tuple[str, int]]:
    run_id = obs.runs[target].run_id
    return sorted((r["input_name"], r["source_run_id"])
                  for r in obs.run_input_rows
                  if r["run_id"] == run_id and r["source_run_id"] is not None)


def _input_rows(obs, target=Q) -> list[tuple]:
    """Every input row of the target's run, as ``(slot, source run, sample, hash)``."""
    run_id = obs.runs[target].run_id
    return sorted((r["input_name"], r["source_run_id"], r["sample_name"],
                   r["content_hash"])
                  for r in obs.run_input_rows if r["run_id"] == run_id)


def _sample_hash(name: str) -> str:
    """The content hash the sample registry holds for one sample now."""
    from wfc.persistence import Sample, get_session
    with get_session() as session:
        return session.exec(
            select(Sample).where(Sample.name == name)).one().content_hash


# =============================================================================
# Delivery and key
# =============================================================================

@workflow(purpose="A node fed by a per-sample selector and an upstream method "
                  "receives both inputs at materialize and records both reads "
                  "-- the upstream run and the sample with its content hash "
                  "-- on its new run and on a cache hit's audit row; "
                  "changing the sample's content moves its cache key while "
                  "its upstream run stays the same run")
def test_sample_beside_an_upstream_is_delivered_and_keyed(git_project, monkeypatch):
    口 = Step(step_num=1, name="Run the consumer after its seeded upstream",
             purpose="The raw image on one slot and the upstream's mask on "
                     "another -- the shape the loader accepts")
    scn = Scenario(nodes=selector_beside_method(),
                   prior_runs=[completed("segment")])
    first = run_target(scn, "quantify", root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Both slots arrived",
             purpose="The sample and the upstream output each reach the "
                     "method on the slot the document wires")
    assert _slot_names(first) == {"raw": 1, "mask": 1}
    upstream = _parents(first)
    assert [slot for slot, _ in upstream] == ["mask"]

    口 = Step(step_num=3, name="The new run records both reads",
             purpose="One parent row naming the upstream run on its slot, "
                     "and one sample row naming the sample and the content "
                     "hash the key is over")
    [(_, segment_run)] = upstream
    reads = [("mask", segment_run, None, None),
             ("raw", None, "s1", _sample_hash("s1"))]
    assert _input_rows(first) == reads

    口 = Step(step_num=4, name="Change only the sample's content and claim again",
             purpose="Rebuilding over the same root re-registers the sample "
                     "with new bytes; the upstream is not re-run, so the "
                     "consumer's parent is the same run as before")
    changed = replace(scn, prior_runs=[], sample_content="id,value\n1,other\n")
    project = build_project(changed, root=git_project, monkeypatch=monkeypatch)
    second = drive_target(project, "quantify", monkeypatch=monkeypatch)

    口 = Step(step_num=5, name="The key moved with the sample alone",
             purpose="Same parent run, different sample bytes: an equal key "
                     "would serve the old result for data the step no "
                     "longer reads. The new run's sample row carries the "
                     "new hash")
    assert _parents(second) == upstream
    changed_hash = _sample_hash("s1")
    assert changed_hash != reads[1][3]
    changed_reads = [reads[0], ("raw", None, "s1", changed_hash)]
    assert _input_rows(second) == changed_reads
    assert second.run_row(Q)["cache_key"] != first.run_row(Q)["cache_key"]
    assert second.phases_ran(Q)[1] == "materialize"

    口 = Step(step_num=6, name="Claim again: the cache hit records the same reads",
             purpose="Nothing changed since the second run, so the claim "
                     "serves it; the audit row records what that claim read "
                     "-- the upstream run and the sample with its hash -- "
                     "not only the parent")
    again = drive_target(project, "quantify", monkeypatch=monkeypatch)
    assert again.run_row(Q)["cache_source_run_id"] == second.runs[Q].run_id
    assert _input_rows(again) == changed_reads


@workflow(purpose="A node fed by a per-sample selector, an upstream method and "
                  "a run reference receives all three inputs, and its cache "
                  "key reacts to the sample and to the upstream")
def test_sample_beside_an_upstream_and_a_reference(tmp_path_factory, monkeypatch):
    口 = Step(step_num=1, name="Run the three-input consumer",
             purpose="The reference names a seeded run whose sample is one "
                     "of the selector's samples")
    def scenario(segment_params: dict) -> Scenario:
        nodes = selector_beside_method_and_reference()
        next(n for n in nodes if n.id == "segment").params = segment_params
        return Scenario(nodes=nodes,
                        prior_runs=[completed("train"), completed("segment")])

    root = tmp_path_factory.mktemp("three_inputs")
    first = run_target(scenario({"k": 1}), "quantify", root=root,
                       monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="All three slots arrived",
             purpose="Sample, upstream output and referenced artifact, each "
                     "on its own slot")
    assert _slot_names(first) == {"raw": 1, "mask": 1, "model": 1}
    parents = _parents(first)
    assert [slot for slot, _ in parents] == ["mask", "model"]

    口 = Step(step_num=3, name="Change only the sample's content",
             purpose="Same root, the upstream and the referenced run left as "
                     "they are: the reference now names that run outright")
    model_run = dict(parents)["model"]
    nodes = [reference("model", run_id=str(model_run)) if n.id == "model" else n
             for n in scenario({"k": 1}).nodes]
    project = build_project(
        Scenario(nodes=nodes, sample_content="id,value\n1,other\n"),
        root=root, monkeypatch=monkeypatch)
    resampled = drive_target(project, "quantify", monkeypatch=monkeypatch)
    assert _parents(resampled) == parents
    assert resampled.run_row(Q)["cache_key"] != first.run_row(Q)["cache_key"]

    口 = Step(step_num=4, name="Change only the upstream",
             purpose="A second root whose upstream runs under other params; "
                     "the sample and the referenced run's key are unchanged")
    other = run_target(scenario({"k": 2}), "quantify",
                       root=tmp_path_factory.mktemp("other_upstream"),
                       monkeypatch=monkeypatch)
    assert other.run_row(Q)["cache_key"] != first.run_row(Q)["cache_key"]


# =============================================================================
# Honest refusals
# =============================================================================

@workflow(purpose="A slot the document wires but the claim was not handed is "
                  "refused as a wfc defect naming the slot and its source "
                  "node, before any run row exists")
def test_a_wired_slot_not_delivered_is_refused_as_a_wfc_defect(git_project,
                                                               monkeypatch, capsys):
    口 = Step(step_num=1, name="Seed the upstream, then lose its run-id sidecar",
             purpose="A run production really made, whose sidecar is then "
                     "gone: the claim's sidecar walk finds nothing to hand "
                     "the wired slot")
    scn = Scenario(nodes=selector_beside_method(),
                   prior_runs=[completed("segment", sentinel_deleted=True)])
    obs = run_target(scn, "quantify", root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="Refused with the defect message, before any row",
             purpose="The user wired the slot, so the message must not tell "
                     "them to wire it")
    err = capsys.readouterr().err
    assert obs.exit_code(Q) == 1
    assert "'mask' (wired from node 'segment')" in err
    assert "wfc defect" in err
    assert "which nothing feeds" not in err
    assert obs.rows_for_node("quantify") == []
    assert obs.outcomes[Q]["phase"] == "claim"


@workflow(purpose="A reference whose run exists but recorded no output for the "
                  "slot the link names is refused at the claim, before any "
                  "run row, parent record or key part")
def test_a_reference_whose_artifact_does_not_resolve_is_refused_at_claim(
        git_project, monkeypatch, capsys):
    from wfc.execution.claim import run_claim
    from wfc.persistence import Run, RunInput, get_session

    口 = Step(step_num=1, name="Produce the referenced run",
             purpose="A real run with one recorded output, under its own "
                     "pipeline")
    producer = Scenario(nodes=[selector(), node("src", inputs=[wire("sel")])],
                        pipeline_id="producer", name="producer")
    src_run = run_target(producer, "src", root=git_project,
                         monkeypatch=monkeypatch).runs[("src", "s1", "default")].run_id

    口 = Step(step_num=2, name="Claim a consumer whose link names another output",
             purpose="A direct claim drive: the load, which refuses the same "
                     "link, does not precede it")
    consumer = Scenario(nodes=[selector(), node("src", inputs=[wire("sel")]),
                               reference("ref", run_id=str(src_run)),
                               node("sink", inputs=[wire("ref", source_slot="nope")])])
    project = build_project(consumer, root=git_project, monkeypatch=monkeypatch)
    capsys.readouterr()
    result = run_claim(node_id="sink", sample="s1", variant="default",
                       method_name=None, module_name=None, script_path=None,
                       params=None, parent_run_ids=None,
                       pipeline_id=project.pipeline_id,
                       pipeline_json=str(project.pipeline_json), git_commit=None)

    口 = Step(step_num=3, name="Refused before any row",
             purpose="No run row, no lineage row pointing at the referenced "
                     "run: nothing claims an input the method never received")
    assert result == {"ok": False, "rc": 1}
    err = capsys.readouterr().err
    assert "'ref'" in err and "is invalid for run" in err
    with get_session() as session:
        assert session.exec(select(RunInput).where(
            RunInput.source_run_id == src_run)).all() == []
        assert session.exec(select(Run).where(
            Run.pipeline_id == project.pipeline_id,
            Run.version_id.is_not(None))).all() == []


@workflow(purpose="A per-sample reader whose sample directory is missing, or "
                  "holds no data file, is refused at materialize naming the "
                  "sample and pointing at the restore, rather than running "
                  "with an empty slot")
def test_a_missing_or_empty_sample_directory_is_refused_by_name(
        tmp_path_factory, monkeypatch, capsys):
    for state in ("missing_samples", "empty_samples"):
        obs = run_target(Scenario(**{state: ("s1",)}), "n1",
                         root=tmp_path_factory.mktemp(state),
                         monkeypatch=monkeypatch)
        err = capsys.readouterr().err
        assert obs.exit_code(("n1", "s1", "default")) == 1, state
        assert "sample 's1'" in err and "restore_sample" in err, state
        assert "dispatch" not in obs.phases_ran(("n1", "s1", "default")), state


# =============================================================================
# A refused step is visibly a failure
# =============================================================================

def _refused_mid_chain() -> Scenario:
    """Head -> mid -> tail, where mid's link names an output head does not declare."""
    return Scenario(nodes=[
        selector(),
        node("head", inputs=[wire("sel")], output_files={"data": "data.csv"}),
        node("mid", inputs=[wire("head", source_slot="nope")]),
        node("tail", inputs=[wire("mid")]),
    ])


@workflow(purpose="A pipeline whose middle step is refused at the claim ends "
                  "with one failed row for that step carrying its message and "
                  "each descendant cancelled against it; the canvas status "
                  "is terminal failed and the pipeline summary agrees with it")
def test_a_claim_refusal_is_a_visible_failure(git_project, monkeypatch):
    from wfc.canvas.run_state import aggregate_pipeline_status
    from wfc.execution.lifecycle import _write_cancelled_rows, summarize_pipeline

    口 = Step(step_num=1, name="Run the pipeline and its pipeline-end walk",
             purpose="The refused step writes no row; the walk writes its "
                     "failed row after the fact")
    obs = run_scenario(_refused_mid_chain(), root=git_project, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The refused step failed, with its message",
             purpose="One failed row, no version and no input records of "
                     "either kind: a record written after the refusal, not "
                     "a run the claim registered. Every run_inputs row in "
                     "the pipeline is the head's own read of its sample")
    [mid] = obs.rows_for_node("mid")
    assert mid["status"] == "failed"
    assert "'nope'" in mid["error_message"]
    assert mid["version_id"] is None and mid["cache_key"] is None
    assert mid["node_id"] == "mid"
    head_runs = {r["id"] for r in obs.rows_for_node("head")}
    assert [(r["run_id"] in head_runs, r["source_run_id"], r["sample_name"])
            for r in obs.run_input_rows] == [(True, None, "s1")]

    口 = Step(step_num=3, name="Its descendant is cancelled because of it",
             purpose="The walk's own ancestor search found the failed row")
    [tail] = obs.rows_for_node("tail")
    assert tail["status"] == "cancelled"
    assert tail["cancelled_due_to_run_id"] == mid["id"]
    assert tail["node_id"] == "tail"

    口 = Step(step_num=4, name="The walk is idempotent",
             purpose="A second pass finds every target present")
    assert _write_cancelled_rows(obs.project.pipeline_id, str(obs.project.root)) == 0
    assert len(obs.rows_for_node("mid")) == 1

    口 = Step(step_num=5, name="The canvas status is terminal failed",
             purpose="The engine thread died with an error; the refused node "
                     "carries its message and the descendant names its cause: "
                     "the failed run and the canvas node that run ran for")
    step_map = {"head": "head", "mid": "mid", "tail": "tail"}
    status = aggregate_pipeline_status(obs.project.pipeline_id, step_map,
                                       thread_alive=False, error="engine failed",
                                       log_dir=None)
    assert status.overall_status == "failed"
    assert "'nope'" in status.node_states["mid"]["error"]
    assert status.node_states["tail"]["cancelled_due_to_run_id"] == str(mid["id"])
    assert status.node_states["tail"]["upstream_run_id"] == str(mid["id"])
    assert status.node_states["tail"]["upstream_node_id"] == "mid"

    口 = Step(step_num=6, name="The summary agrees with the status",
             purpose="Both read the same rows after the walk, so the failure "
                     "count includes the refused step")
    summary = summarize_pipeline(obs.project.pipeline_id)
    assert summary.status == status.overall_status
    assert (summary.completed, summary.failed, summary.cancelled) == (1, 1, 1)
    tallied = sum(sum(t.values()) for t in
                  (ns.get("tally", {}) for ns in status.node_states.values()))
    assert summary.total == tallied


@workflow(purpose="wfc run-pipeline over a pipeline the engine reports as "
                  "failed prints one ERROR line on stderr and returns 1, "
                  "with the summary printed and no traceback")
@pytest.mark.usefixtures("ready_preflight")
def test_run_pipeline_reports_a_failed_pipeline_as_an_error_line(
        git_project, monkeypatch, capsys):
    from tests.fixtures.conftest import mocked_snakemake
    from wfc.cli import cli_main

    口 = Step(step_num=1, name="Build the project the verb launches",
             purpose="The pipeline document, its registered methods and its "
                     "samples; the verb mints its own pipeline id and runs "
                     "from here")
    project = build_project(_refused_mid_chain(), root=git_project,
                            monkeypatch=monkeypatch)
    capsys.readouterr()

    口 = Step(step_num=2, name="Launch through the CLI verb",
             purpose="The engine process exits non-zero, as it does when a "
                     "job is refused; everything around the spawn runs for "
                     "real")
    gen_patch, popen_patch = mocked_snakemake(1)
    # The scenario env's image is in the Docker daemon (run_pipeline's env
    # pre-flight probes it); the engine is stubbed, so no container runs.
    stub_docker_image_inspect(monkeypatch, lambda ref: ref)
    with gen_patch, popen_patch:
        rc = cli_main(["run-pipeline", "--pipeline", str(project.pipeline_json),
                       "--project-root", str(project.root),
                       "--wfc-root", str(project.root), "--cores", "1",
                       "--no-archive"])

    口 = Step(step_num=3, name="One ERROR line, exit 1, no traceback",
             purpose="The failure reaches the user as the CLI's own refusal "
                     "shape, after the summary")
    captured = capsys.readouterr()
    assert rc == 1
    error_lines = [line for line in captured.err.splitlines()
                   if line.startswith("ERROR: ")]
    assert len(error_lines) == 1, captured.err
    assert "Traceback" not in captured.err
    assert "PIPELINE SUMMARY" in captured.out


# =============================================================================
# The real engine, in real containers
# =============================================================================

@pytest.mark.integration
@requires_docker
@workflow(purpose="Under the real engine in a real container, a node fed by a "
                  "per-sample selector, an upstream method and a run "
                  "reference runs its composed rule and receives all three "
                  "inputs where the method runs")
def test_three_inputs_arrive_under_the_real_engine(tmp_path_factory, monkeypatch,
                                                   fixture_container_image):
    口 = Step(step_num=1, name="Produce the referenced run",
             purpose="A run production's run_step made under its own "
                     "pipeline over the same root, so the reference names a "
                     "real run id. It runs on the stub rung: a second engine "
                     "run over one root would find the first run's engine "
                     "bookkeeping committed and refuse the dirty tree")
    root = tmp_path_factory.mktemp("engine_three_inputs")
    producer = Scenario(nodes=[selector(), node("src", inputs=[wire("sel")])],
                        pipeline_id="producer", name="producer",
                        env_name=FIXTURE_ENV_NAME)
    produced = run_scenario(producer, root=root, monkeypatch=monkeypatch)
    src_run = produced.runs[("src", "s1", "default")].run_id
    assert src_run is not None

    口 = Step(step_num=2, name="Run the three-input consumer under the engine",
             purpose="The consumer's method exits non-zero naming any slot "
                     "whose paths did not arrive inside the container, so "
                     "its completion is the delivery observation")
    nodes = selector_beside_method_and_reference("quantify", "up", source="src",
                                                 reference_id="ref")
    nodes = [reference("ref", run_id=str(src_run)) if n.id == "ref" else n
             for n in nodes]
    next(n for n in nodes if n.id == "quantify").behavior = Behavior(
        read_inputs=("raw", "mask", "model"))
    engine = run_scenario(Scenario(nodes=nodes, env_name=FIXTURE_ENV_NAME),
                          root=root, monkeypatch=monkeypatch, fidelity=ENGINE,
                          image_digest=fixture_container_image)

    口 = Step(step_num=3, name="Every target completed and all three arrived",
             purpose="The engine's verdict, the run row and the recorded "
                     "parents agree: the upstream on its slot, the "
                     "referenced run on its own")
    assert set(engine.exit_codes().values()) == {0}
    assert engine.run_row(Q)["status"] == "completed"
    assert engine.invariants.failures == {}
    parents = dict(_parents(engine))
    assert set(parents) == {"mask", "model"}
    assert parents["model"] == src_run
    assert parents["mask"] == engine.runs[("up", "s1", "default")].run_id


def _verdicts(obs) -> dict:
    """Per target: ran and exited 0, failed, or never scheduled."""
    def verdict(run):
        if run.skipped:
            return "skipped"
        return "ran" if run.rc == 0 else "failed"
    return {t: verdict(r) for t, r in obs.runs.items()}


@pytest.mark.integration
@requires_docker
@workflow(purpose="A pipeline whose middle step is refused at the claim ends "
                  "the same way on the real engine as on the stub rung: the "
                  "head ran, the refused step failed, its descendant was "
                  "never scheduled, and the rows record the same outcome")
def test_a_claim_refusal_agrees_across_rungs(git_project, tmp_path_factory,
                                             monkeypatch, fixture_container_image):
    from tests.harness.drivers import _ancestor_map, _blocked_by

    口 = Step(step_num=1, name="Run the refused-mid-chain pipeline on both rungs",
             purpose="One declaration; the stub rung sequences it itself, "
                     "the engine rung hands it to Snakemake")
    scn = replace(_refused_mid_chain(), env_name=FIXTURE_ENV_NAME)
    stub = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    engine = run_scenario(scn, root=tmp_path_factory.mktemp("engine_refused"),
                          monkeypatch=monkeypatch, fidelity=ENGINE,
                          image_digest=fixture_container_image)

    口 = Step(step_num=2, name="The engine's per-target verdict",
             purpose="The refused step exits 1 and leaves its failed outcome "
                     "sidecar; the head ran clean; the tail never ran")
    head, mid, tail = (("head", "s1", "default"), ("mid", "s1", "default"),
                       ("tail", "s1", "default"))
    assert _verdicts(engine) == {head: "ran", mid: "failed", tail: "skipped"}
    assert engine.outcomes[mid]["status"] == "failed"

    口 = Step(step_num=3, name="The stub rung and its blocking model agree",
             purpose="The stub rung's skip rule is a model of the engine's "
                     "scheduling; the engine's own verdict is what checks it")
    assert _verdicts(stub) == _verdicts(engine)
    failed = [t for t, v in _verdicts(engine).items() if v == "failed"]
    ancestors = _ancestor_map(expand_targets(engine.project))
    for target, v in _verdicts(engine).items():
        if v != "failed":
            assert (v == "skipped") == _blocked_by(target, failed, ancestors), target

    口 = Step(step_num=4, name="Both rungs leave the same rows",
             purpose="The pipeline-end walk ran on each: one failed row for "
                     "the refused step with its message, the tail cancelled "
                     "against it")
    for obs in (stub, engine):
        [mid_row] = obs.rows_for_node("mid")
        [tail_row] = obs.rows_for_node("tail")
        assert mid_row["status"] == "failed" and "'nope'" in mid_row["error_message"]
        assert tail_row["status"] == "cancelled"
        assert tail_row["cancelled_due_to_run_id"] == mid_row["id"]
        assert [r["status"] for r in obs.rows_for_node("head")] == ["completed"]
