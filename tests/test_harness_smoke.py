"""Smoke coverage for the scenario harness's own contract.

These are Tier 1: they pin the instrument itself (default scenario shape,
stopping points, bundle shape) rather than any production behavior. The
behavior proofs live in the Execution catalog module and the modules that
drive scenarios through the harness.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import requires_docker
from tests.fixtures.conftest import FIXTURE_ENV_NAME
from axiom_annotations import workflow

from tests.harness import (
    ENGINE,
    InvariantReport,
    Observation,
    Phase,
    Scenario,
    TargetRun,
    build_project,
    chain,
    check_invariants,
    exits,
    node,
    observe_after_pipeline,
    reference,
    run_scenario,
    run_target,
    selector,
    wire,
)


def test_no_argument_scenario_is_a_valid_one_node_run(git_project, monkeypatch):
    """`Scenario()` is one node, one sample, one variant, exit zero."""
    obs = run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch)

    assert obs.targets == [("n1", "s1", "default")]
    assert obs.exit_code(("n1", "s1", "default")) == 0
    assert ("n1", "s1", "default") in obs.sentinels
    assert obs.outcomes[("n1", "s1", "default")]["status"] == "completed"
    assert obs.invariants.failures == {}


def test_build_project_executes_nothing(git_project, monkeypatch):
    """`build_project` constructs the project and runs no target."""
    project = build_project(Scenario(), root=git_project, monkeypatch=monkeypatch)

    assert project.pipeline_json.exists()
    assert (project.root / "methods" / "n1" / "n1.py").exists()
    assert not (project.root / ".runs" / "sentinels").exists()


def test_build_project_leaves_no_readiness_sentinel_behind(git_project,
                                                           monkeypatch):
    """A built project carries no ``.sample_ready`` unless the scenario asks.

    ``.sample_ready`` is the Snakemake restore rule's OUTPUT, so one present
    before the engine runs makes the rung skip the rule — and that rule is
    what proves the sample's bytes actually reached the DVC cache. Step 8 of
    ``build_project`` materializes each sample through production
    ``restore_sample``, which touches the sentinel on its way out, so the
    harness removes it again; with that removal gone every downstream
    assertion still holds (the bundles match either way) and nothing goes
    red. This is the assertion that does.
    """
    from wfc import layout

    project = build_project(Scenario(samples=["s1"]), root=git_project,
                            monkeypatch=monkeypatch)

    sentinel = layout.sample_ready_sentinel(project.root, "s1")
    assert not sentinel.exists(), (
        f"{sentinel} exists before anything ran. The restore rule's own "
        "output being present makes Snakemake skip the rule, so the run "
        "never proves the sample is in the cache. Only a scenario declaring "
        "sample_ready_sentinel=True may leave one."
    )


def test_build_project_stages_a_sentinel_when_the_scenario_declares_one(
        git_project, monkeypatch):
    """``sample_ready_sentinel=True`` still gets one.

    The positive control for the test above: without it, a harness that
    simply never wrote the sentinel would satisfy that assertion while
    making the declared 'the restore rule is already satisfied' scenario
    unreachable.
    """
    from wfc import layout

    project = build_project(
        Scenario(samples=["s1"], sample_ready_sentinel=True),
        root=git_project, monkeypatch=monkeypatch)

    assert layout.sample_ready_sentinel(project.root, "s1").exists()


def test_build_project_registers_every_sample_the_modern_way(git_project,
                                                             monkeypatch):
    """Every declared sample gets a content-addressed row, staged or not.

    Registration and staging are separate acts in production, so a
    sample the scenario declared missing or empty still carries a row —
    row-present/file-absent is the normal pre-``restore_sample`` state.
    Distinct content per sample means distinct hashes, which is what
    lets a scenario observe the sample axis of the cache key at all.
    """
    from wfc.persistence import get_session, Sample

    scn = Scenario(samples=["s1", "s2", "s3"], missing_samples=("s3",))
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)

    assert set(project.sample_ids) == {"s1", "s2", "s3"}

    with get_session() as session:
        rows = {name: session.get(Sample, rid)
                for name, rid in project.sample_ids.items()}

    assert all(r is not None and r.content_hash for r in rows.values()), (
        "every sample row carries a DVC content hash — the modern shape "
        f"register_sample produces; got "
        f"{ {n: (r.content_hash if r else None) for n, r in rows.items()} }"
    )
    hashes = [r.content_hash for r in rows.values()]
    assert len(set(hashes)) == 3, (
        "each sample must hash distinctly, or a same-size membership swap "
        f"between two bundles computes one key; got {hashes}"
    )


def test_five_node_chain_over_three_samples_is_two_fields(git_project, monkeypatch):
    """A five-node chain over three samples is a two-field declaration."""
    scn = Scenario(
        nodes=chain("qc", "align", "count", "normalize", "report"),
        samples=["s1", "s2", "s3"],
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert len(obs.targets) == 15
    assert set(obs.exit_codes().values()) == {0}


def test_stopping_through_materialize_exposes_resolved_input_paths(
    git_project, monkeypatch
):
    """Through materialize: the resolved per-slot input paths are assertable."""
    obs = run_target(Scenario(), "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    target = ("n1", "s1", "default")
    assert obs.runs[target].phases_ran == ["claim", "materialize"]
    assert obs.runs[target].stopped_at == "dispatch"
    slot_paths = obs.phase_args("dispatch", target)["slot_paths"]
    assert [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            for p in slot_paths["data"]] == ["s1.csv"]


def test_stopping_before_record_names_the_lifecycle_skip(git_project, monkeypatch):
    """An early-stopped run never appears to have proven a lifecycle invariant."""
    obs = run_target(Scenario(), "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.COLLECT)

    assert "completion-signals-agree" in obs.invariants.skipped
    assert "completion-signals-agree" not in obs.invariants.passed


@workflow(
    purpose="A waiver is an inventory entry for a live divergence, not a "
            "mute button: waiving an invariant this scenario does not "
            "violate fails the test, while the rest of the pack still "
            "asserts (Tier 2).",
)
def test_a_waived_invariant_that_passes_fails_the_test(git_project, monkeypatch):
    """The strict-waiver rule, driven end to end and at its mechanism.

    First half: a clean scenario waives ``single-run-record``, which it
    does not violate.  The unexpected pass is the finding, and it is
    raised with the waiver's own reason quoted back.

    Second half: a genuine violation under the same waiver is tolerated
    while an unwaived one still fails.  Producing a real standing-invariant
    divergence would mean breaking production, which a test must not do,
    so that half is driven at ``assert_clean`` -- the mechanism the first
    half proves is reached.
    """
    with pytest.raises(AssertionError) as excinfo:
        run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch,
                     waive="single-run-record",
                     reason="expected to double-register while X is open")

    message = str(excinfo.value)
    assert "single-run-record" in message
    assert "PASSED" in message
    assert "expected to double-register while X is open" in message

    tolerated = InvariantReport(
        checked=["single-run-record", "completion-signals-agree"],
        passed=["completion-signals-agree"],
        failures={"single-run-record": "two rows for (n1, s1, default)"},
        waived="single-run-record",
        waiver_reason="expected to double-register while X is open",
    )
    tolerated.assert_clean()

    still_asserts = InvariantReport(
        checked=["single-run-record", "completion-signals-agree"],
        failures={
            "single-run-record": "two rows for (n1, s1, default)",
            "completion-signals-agree": "rc=0 but no sentinel",
        },
        waived="single-run-record",
        waiver_reason="expected to double-register while X is open",
    )
    with pytest.raises(AssertionError) as unwaived:
        still_asserts.assert_clean()
    assert "completion-signals-agree" in str(unwaived.value)
    assert "'single-run-record' diverged" not in str(unwaived.value)


def test_waiving_an_unknown_invariant_raises():
    """A waiver must name an invariant the pack actually holds."""
    with pytest.raises(ValueError, match="unknown invariant 'nope'"):
        check_invariants(Observation(project=None), waive="nope", reason="x")


def test_waiving_without_a_reason_raises():
    """No reason, no waiver: a waiver is an inventory entry, not a mute."""
    with pytest.raises(ValueError, match="requires a reason"):
        check_invariants(Observation(project=None), waive="single-run-record")


def test_a_positional_phase_call_is_recorded_and_delegated(monkeypatch):
    """The interposed wrappers pass a positional call through.

    Production calls every phase by keyword today. Should that change, the
    wrapper records the call under the phase function's own parameter names
    and delegates it, instead of the harness rejecting production's call
    with a signature error.
    """
    from types import SimpleNamespace

    from tests.harness.drivers import PHASE_ENTRY_POINTS, _interpose

    delegated: list[tuple] = []

    def run_record(ending, run_id, node_id, sample, variant):
        delegated.append((ending, run_id, node_id, sample, variant))
        return 1

    def other_phase(**kwargs):
        return None

    fake_orchestrator = SimpleNamespace(**{
        attr: run_record if phase is Phase.RECORD else other_phase
        for phase, attr in PHASE_ENTRY_POINTS.items()
    })
    record = TargetRun(target=("n1", "s1", "default"))

    with _interpose(fake_orchestrator, record, None, monkeypatch):
        rc = fake_orchestrator.run_record("completed", 7, "n1", "s1", "default")

    assert rc == 1
    assert delegated == [("completed", 7, "n1", "s1", "default")]
    assert record.phases_ran == ["record"]
    assert (record.ending, record.run_id) == ("completed", 7)
    assert record.phase_args["record"] == {
        "ending": "completed", "run_id": 7, "node_id": "n1",
        "sample": "s1", "variant": "default",
    }


def _bundle_shape(obs) -> dict:
    """Reduce a bundle to the facts both rungs must agree on.

    Paths and run ids are project- and database-local, so the comparable
    surface is the target list, the exit codes, the completion signals and
    the names of the artifacts each target published.

    Args:
        obs: An observation bundle.

    Returns:
        The rung-independent projection of the bundle.
    """
    return {
        "targets": list(obs.targets),
        "exit_codes": obs.exit_codes(),
        "sentinels": obs.sentinels,
        "outcomes": {t: o["status"] for t, o in obs.outcomes.items()},
        "row_status": {t: (obs.run_row(t) or {}).get("status")
                       for t in obs.targets},
        "outputs": {t: [r["output_name"] for r in obs.output_rows_for(t)]
                    for t in obs.targets},
    }


@pytest.mark.integration
@requires_docker
def test_both_rungs_return_the_same_bundle_for_one_scenario(
    git_project, tmp_path_factory, monkeypatch, fixture_container_image
):
    """One declaration, two rungs, one bundle shape.

    The engine rung is the whole reason the declaration is rung-independent:
    the same `Scenario` is handed to the stub rung and to the real Snakemake
    engine running the methods in real containers, and the two bundles agree
    on every fact that is not project- or database-local.
    """
    scn = Scenario(nodes=chain("head", "tail"), env_name=FIXTURE_ENV_NAME)

    stub = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)
    stub_shape = _bundle_shape(stub)

    engine = run_scenario(scn, root=tmp_path_factory.mktemp("engine"),
                          monkeypatch=monkeypatch, fidelity=ENGINE,
                          image_digest=fixture_container_image)

    assert _bundle_shape(engine) == stub_shape
    assert engine.invariants.failures == {}
    # The engine schedules in its own worker processes, so the harness
    # observes no phase and says so rather than asserting against a fiction.
    assert "single-run-record" in engine.invariants.passed
    assert set(engine.invariants.skipped) == {
        "completion-signals-agree", "output-writers-agree"
    }


def test_read_inputs_fails_the_method_on_a_slot_that_did_not_arrive(
        git_project, monkeypatch):
    """A method told to read a slot nothing fed exits 2 and names the slot.

    ``read_inputs`` is the engine rung's only delivery observation, so it
    must fail when a slot is absent rather than pass vacuously; the slots
    that did arrive are read without complaint.
    """
    from tests.harness import Behavior

    nodes = chain("n1")
    nodes[-1].behavior = Behavior(read_inputs=("data", "absent"))
    obs = run_scenario(Scenario(nodes=nodes), root=git_project,
                       monkeypatch=monkeypatch)

    target = ("n1", "s1", "default")
    assert obs.exit_code(target) != 0
    stderr = obs.runs[target].stderr_log.read_text()
    assert "input slot absent did not arrive" in stderr
    assert "input slot data" not in stderr


def test_observation_renders_what_the_run_actually_did(git_project, monkeypatch):
    """The diagram shows the prune, not the pipeline someone hoped for.

    `qc` fails on `s2` only, so `s1` runs clean end to end while `s2`
    stops at `qc` and leaves `align` and `report` unscheduled. Every
    verdict in the rendering is read off the bundle, so a diagram cannot
    show a target succeeding that did not.
    """
    nodes = chain("qc", "align", "report")
    next(n for n in nodes if n.id == "qc").behavior_by_sample = {"s2": exits(1)}
    obs = run_scenario(Scenario(nodes=nodes, samples=["s1", "s2"]),
                       root=git_project, monkeypatch=monkeypatch)

    diagram = obs.to_mermaid()
    print("\n" + diagram)

    assert diagram.startswith("flowchart LR")
    # s1 ran clean; s2's qc failed and pruned everything downstream of it.
    assert 'qc_s1_default["qc<br/>completed"]:::ok' in diagram
    assert 'qc_s2_default["qc<br/>FAILED (exit 1)"]:::failed' in diagram
    assert 'align_s2_default["align<br/>not scheduled"]:::skipped' in diagram
    assert 'report_s2_default["report<br/>not scheduled"]:::skipped' in diagram
    # The selector expands to no target, so it is drawn as a system node
    # and feeds the head of each sample's chain.
    assert 'sel(["sel<br/>input_selector"]):::system' in diagram
    assert "sel --> qc_s1_default" in diagram
    assert "qc_s1_default --> align_s1_default" in diagram


@workflow(purpose="One script per method plays each node's declared behavior "
                  "by node and variant, so two nodes sharing a method can fail "
                  "on different axes without one declaration overwriting the "
                  "other")
def test_nodes_sharing_a_method_resolve_behavior_by_node_and_variant(
    git_project, monkeypatch,
):
    """`al` fails under `loose` only; `ar` shares its method and never fails.

    Two branches on `method_a` feed their own `method_b` nodes over two
    variants. One script serves `method_a`, and the failed run's dispatch
    env names the variant and the node the script resolved by. Each pair
    shares its method under identical params, so every box carries a label
    — production's node identity within a pipeline — to keep its records
    apart.
    """
    scn = Scenario(
        nodes=[
            selector(),
            node("al", method="method_a", inputs=[wire("sel")],
                 label="left-box-fails-under-loose",
                 behavior_by_variant={"loose": exits(1)}),
            node("ar", method="method_a", inputs=[wire("sel")],
                 label="right-box-never-fails"),
            node("dl", method="method_b", inputs=[wire("al")],
                 label="downstream-of-left-box"),
            node("dr", method="method_b", inputs=[wire("ar")],
                 label="downstream-of-right-box"),
        ],
        samples=["s1"],
        variants={"strict": {"t": 0.1}, "loose": {"t": 0.9}},
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch)

    assert obs.run_row(("al", "s1", "loose"))["status"] == "failed"
    assert obs.run_row(("al", "s1", "strict"))["status"] == "completed"
    for variant in ("strict", "loose"):
        assert obs.run_row(("ar", "s1", variant))["status"] == "completed"
        assert obs.run_row(("dr", "s1", variant))["status"] == "completed"
    assert obs.runs[("dl", "s1", "loose")].skipped
    assert obs.run_row(("dl", "s1", "strict"))["status"] == "completed"
    assert set(obs.project.method_scripts) == {"method_a", "method_b"}
    failed = obs.runs[("al", "s1", "loose")]
    assert failed.dispatch_env["WFC_VARIANT"] == "loose"
    assert failed.dispatch_env["WFC_NODE_ID"] == "al"


def test_build_project_refuses_conflicting_shared_method_declarations(
    git_project, monkeypatch,
):
    """Both override maps on one node, or two contracts for one method, are loud."""
    both_axes = Scenario(nodes=[
        selector(),
        node("x", inputs=[wire("sel")], behavior_by_sample={"s1": exits(1)},
             behavior_by_variant={"default": exits(1)}),
    ])
    with pytest.raises(ValueError, match="both behavior_by_sample"):
        build_project(both_axes, root=git_project, monkeypatch=monkeypatch)

    two_contracts = Scenario(nodes=[
        selector(),
        node("p", method="m", inputs=[wire("sel")], outputs={"a": ".csv"}),
        node("q", method="m", inputs=[wire("sel")], outputs={"b": ".csv"}),
    ])
    with pytest.raises(ValueError, match="share method 'm'"):
        build_project(two_contracts, root=git_project, monkeypatch=monkeypatch)


@workflow(purpose="Two unlabelled nodes sharing a method under identical "
                  "params on one sample, each with a child, one branch "
                  "failing: after the pipeline-end walk the failing branch's "
                  "child is cancelled against that branch's failed run, the "
                  "healthy branch's child completed, and the standing pack "
                  "is clean")
def test_unlabelled_nodes_sharing_a_method_under_identical_params_are_two_targets(
    git_project, monkeypatch,
):
    """Same-method, same-params, unlabelled nodes need no label to be told apart.

    Every run row records its document node's raw id, so the pack reads
    ``left`` and ``right`` as two targets and the walk cancels only the
    failed branch's child.
    """
    from wfc.execution.lifecycle import _write_cancelled_rows

    scn = Scenario(
        nodes=[
            selector(),
            node("left", method="m", inputs=[wire("sel")], behavior=exits(1)),
            node("right", method="m", inputs=[wire("sel")]),
            node("left_child", method="c", inputs=[wire("left")]),
            node("right_child", method="c", inputs=[wire("right")]),
        ],
        samples=["s1"],
    )
    obs = run_scenario(scn, root=git_project, monkeypatch=monkeypatch,
                       pipeline_end=False)
    left_failed = int(obs.runs[("left", "s1", "default")].run_id)

    _write_cancelled_rows(obs.project.pipeline_id, str(obs.project.root))
    fresh = observe_after_pipeline(obs)

    assert fresh.invariants.failures == {}, fresh.invariants
    assert "single-run-record" in fresh.invariants.checked, fresh.invariants

    def terminal(node_id):
        return [(r["status"], r["cancelled_due_to_run_id"])
                for r in fresh.rows_for_node(node_id)
                if (r["id"] or 0) > fresh.baseline_run_id]

    assert terminal("left") == [("failed", None)]
    assert terminal("left_child") == [("cancelled", left_failed)]
    assert terminal("right") == [("completed", None)]
    assert terminal("right_child") == [("completed", None)]


@workflow(
    purpose="The observation bundle records the URL of the database its "
            "rows were read from, and on a green rung that URL is the one "
            "the project was bound with",
    inputs="The no-argument scenario on the stub rung",
    outputs="A bundle whose database_url equals Layout's URL for the "
            "project root",
)
def test_bundle_records_the_database_it_observed(git_project, monkeypatch):
    """The bundle names the database it read, and it is the project's own.

    ``build_project`` binds the project's database through the override.
    If the process engine were still bound to another test's database, the
    bundle would carry that database's rows under this scenario's name, and
    no other field in the bundle would show it.
    """
    from wfc.layout import database_url

    obs = run_scenario(Scenario(), root=git_project, monkeypatch=monkeypatch)

    assert obs.exit_code(("n1", "s1", "default")) == 0
    assert obs.database_url == database_url(obs.project.root)


@workflow(purpose="A node's declared output columns block reaches the registered "
                  "contract through the harness's method.yaml, so a reader over "
                  "the contract sees exactly what the node declared")
def test_build_project_stores_a_declared_output_columns_block(git_project, monkeypatch):
    """The block is written beneath the output slot and registration keeps it.

    ``build_project`` writes the declaration; production's parser validates
    the block and registration stores it on the contract row. Nothing in the
    harness reads it back, so the stored row is the only witness that the
    declaration survived the trip.
    """
    from sqlmodel import select

    from tests.harness import Scenario, build_project, node, selector, wire
    from tests.harness.scenario import SELECTOR_ID
    from wfc.persistence import Method, MethodContract, get_session

    columns = {"strict": ["label", "area"],
               "from_params": [{"params": ["channels"], "pattern": "mean_{}"}],
               "patterns": ["^texture_.*"]}
    scn = Scenario(nodes=[
        selector(),
        node("cols", inputs=[wire(SELECTOR_ID)],
             outputs={"table": ".csv", "plain": ".csv"},
             output_columns={"table": columns}),
    ])

    build_project(scn, root=git_project, monkeypatch=monkeypatch)

    with get_session() as session:
        method = session.exec(select(Method).where(Method.name == "cols")).one()
        contract = session.exec(
            select(MethodContract).where(MethodContract.method_id == method.id)
        ).one()
    assert contract.output_slots["table"]["columns"] == columns
    assert contract.output_slots["table"]["type"] == ".csv"
    assert "columns" not in contract.output_slots["plain"]


def test_build_project_registers_a_declared_module_description_and_contracts(
    git_project, monkeypatch,
):
    """A declared module's description and contracts are the rows registration writes.

    ``register_module`` upserts the module on every method registration and
    replaces its contracts with the list it is handed, so a declaration that
    reached only the first registration would be wiped by the second method's.
    Two methods on one module is the shape that proves the declaration
    survives; a module a node names without declaring keeps the empty
    contract list it always had. The two methods declare the required
    output: registration refuses a method that does not produce one of its
    module's required outputs, so the declaration is only buildable when
    the nodes honour it.
    """
    from sqlmodel import select

    from tests.harness import ModuleSpec, Scenario, build_project, node
    from wfc.persistence import Module, ModuleContract, get_session

    contracts = (
        {"type": "output", "name": "matrix", "value_type": ".h5ad", "required": True},
        {"type": "metric", "name": "score", "value_type": "float", "required": False},
    )
    scn = Scenario(
        nodes=[node("first", module="declared", outputs={"matrix": ".h5ad"}),
               node("second", module="declared", outputs={"matrix": ".h5ad"}),
               node("third", module="plain")],
        modules={"declared": ModuleSpec(description="Declared up front.",
                                        contracts=contracts)},
    )

    build_project(scn, root=git_project, monkeypatch=monkeypatch)

    with get_session() as session:
        declared = session.exec(select(Module).where(Module.name == "declared")).one()
        plain = session.exec(select(Module).where(Module.name == "plain")).one()
        rows = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == declared.id)
        ).all()
        plain_rows = session.exec(
            select(ModuleContract).where(ModuleContract.module_id == plain.id)
        ).all()
    assert declared.description == "Declared up front."
    assert sorted((r.contract_type, r.name, r.value_type, r.required) for r in rows) == sorted(
        (c["type"], c["name"], c["value_type"], c["required"]) for c in contracts
    )
    assert plain_rows == []


@workflow(purpose="A reference declared by a pipeline-qualified locator binds "
                  "to the run the other pipeline produced, so a scenario over "
                  "one root reaches another scenario's run by name and never "
                  "by an id handed in from the test")
def test_a_reference_binds_across_pipelines_by_locator(git_project, monkeypatch):
    """p1 runs `producer`; p2's reference names it by locator and pipeline.

    After binding, the document's reference carries the run id p1's run
    wrote, and driving the consumer resolves its slot from that run's
    recorded output. A locator naming a pipeline that never ran over this
    root still raises with the "declare it" message.
    """
    p1 = Scenario(
        nodes=[selector(), node("producer", inputs=[wire("sel")])],
        samples=["s1"], pipeline_id="p1", name="p1",
    )
    first = run_scenario(p1, root=git_project, monkeypatch=monkeypatch)
    producer_id = first.runs[("producer", "s1", "default")].run_id
    assert producer_id is not None

    p2 = Scenario(
        nodes=[
            reference("ref", run_of=("producer", "s1", "default"), pipeline="p1"),
            node("consumer", inputs=[wire("ref")]),
        ],
        samples=["s1"], pipeline_id="p2", name="p2",
    )
    second = run_scenario(p2, root=git_project, monkeypatch=monkeypatch)

    doc = json.loads(second.project.pipeline_json.read_text())
    entry = next(n for n in doc["nodes"] if str(n["id"]) == "ref")
    assert entry["run_id"] == str(producer_id)
    consumer = next(t for t in second.runs if t[0] == "consumer")
    assert second.run_row(consumer)["status"] == "completed"
    recorded = [r["artifact_path"] for r in second.output_rows_for_run(producer_id)]
    assert recorded
    assert second.input_paths(consumer)["data"] == recorded

    never_ran = Scenario(
        nodes=[
            reference("ref", run_of=("producer", "s1", "default"),
                      pipeline="never-ran"),
            node("consumer", inputs=[wire("ref")]),
        ],
        samples=["s1"], pipeline_id="p3", name="p3",
    )
    with pytest.raises(KeyError, match="declare it"):
        run_scenario(never_ran, root=git_project, monkeypatch=monkeypatch)
