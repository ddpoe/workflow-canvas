"""Reference fan-in and reference lineage, expressed against the harness.

Two user-story cases and the Tier-1 edges around them:

* **Fan-in** — N ``run_reference`` nodes wired into one input slot deliver N
  artifacts under that one slot name. The generator emits the real slot name
  once per occurrence in the shell, and an indexed key per occurrence in the
  ``input:`` block (a Snakemake ``input:`` block is Python keyword syntax, so
  a repeated key is a syntax error rather than a silent overwrite).
* **Lineage and invalidation** — a run fed by a reference records the
  referenced run as a lineage source, repointing the reference at a
  different run changes the consumer's own cache key, and leaving the
  reference alone leaves the consumer cacheable.

The last two are one property stated from both sides, and neither half
holds it alone. Without the repoint half a reference contributing *nothing*
to the fingerprint would pass; without the unchanged half a reference
contributing something *non-deterministic* would pass, and the cache would
be silently dead for every reference-fed run.

Both run on the stub rung. The standing invariant pack is asserted by the
harness after each run, so each test states only what its own case is about.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.harness import (
    Phase,
    Scenario,
    build_project,
    completed,
    node,
    reference,
    run_target,
    selector,
    wire,
)
from tests.fixtures.routes import claimed_run, completed_run

T1 = ("n1", "s1", "default")


def _names(paths) -> list[str]:
    """Return the basenames of a slot's resolved input paths."""
    return [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths]


def _rule_block(snakefile: str, node_id: str) -> str:
    """Return the lines of one rule from a generated Snakefile."""
    lines = snakefile.splitlines()
    start = lines.index(f"rule {node_id}:")
    end = start + 1
    while end < len(lines) and (lines[end].startswith(" ") or not lines[end]):
        if not lines[end] and end + 1 < len(lines) and lines[end + 1].startswith("rule "):
            break
        end += 1
    return "\n".join(lines[start:end])


def _snakefile_for(project) -> str:
    """Generate the Snakefile production would run for a built project."""
    from wfc.execution import load_pipeline_from_path
    from wfc.orchestration import generate_snakefile

    return generate_snakefile(load_pipeline_from_path(project.pipeline_json),
                              str(project.root))


# =============================================================================
# One slot holds every run wired into it
# =============================================================================

@workflow(purpose="Two run_reference nodes wired into one input slot deliver "
                  "both artifacts under that slot name, with the slot name "
                  "repeated in the generated shell and no artifact arriving "
                  "under a slot the method never declared")
def test_two_references_into_one_slot_deliver_both(git_project, monkeypatch):
    口 = Step(step_num=1, name="Declare two references into one slot",
             purpose="A single-input method whose only wiring is two "
                     "run_reference edges onto the same target_slot — the "
                     "multi-replicate combiner shape. Each reference is one "
                     "of two seeded runs, which save their outputs under "
                     "file names that tell them apart")
    scn = Scenario(
        nodes=[
            selector(),
            node("src_a", inputs=[wire("sel")],
                 output_files={"data": "ref_a.csv"}),
            node("src_b", inputs=[wire("sel")],
                 output_files={"data": "ref_b.csv"}),
            reference("ref_a", run_of="src_a"),
            reference("ref_b", run_of="src_b"),
            node("n1", inputs=[wire("ref_a", source_slot=None),
                               wire("ref_b", source_slot=None)]),
        ],
        prior_runs=[completed("src_a"), completed("src_b")],
    )

    口 = Step(step_num=2, name="Run the consumer through materialize",
             purpose="Materialize is where the --ref-input occurrences are "
                     "accumulated back into per-slot path lists")
    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    口 = Step(step_num=3, name="Both artifacts arrive under the declared slot",
             purpose="Order is document order; nothing sorts or de-duplicates")
    slot_paths = obs.phase_args("dispatch", T1)["slot_paths"]
    assert _names(slot_paths["data"]) == ["ref_a.csv", "ref_b.csv"]
    assert list(slot_paths) == ["data"], (
        f"an artifact arrived under a slot the method never declared: "
        f"{list(slot_paths)}"
    )

    口 = Step(step_num=4, name="The shell repeats the real slot name",
             purpose="The wfc run-step argv contract is frozen: the only "
                     "thing that changes for a second reference is the "
                     "number of --ref-input occurrences")
    rule = _rule_block(_snakefile_for(obs.project), "n1")
    assert rule.count("--ref-input data=") == 2, rule


@workflow(purpose="A single reference delivers one artifact, one slot, "
                  "one --ref-input occurrence")
def test_single_reference_is_unchanged(git_project, monkeypatch):
    scn = Scenario(
        nodes=[
            selector(),
            node("src", inputs=[wire("sel")],
                 output_files={"data": "ref_only.csv"}),
            reference("ref", run_of="src"),
            node("n1", inputs=[wire("ref", source_slot=None)]),
        ],
        prior_runs=[completed("src")],
    )

    obs = run_target(scn, "n1", root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    slot_paths = obs.phase_args("dispatch", T1)["slot_paths"]
    assert _names(slot_paths["data"]) == ["ref_only.csv"]
    rule = _rule_block(_snakefile_for(obs.project), "n1")
    assert rule.count("--ref-input data=") == 1, rule


def test_single_reference_shell_line_is_byte_identical():
    """A one-reference rule's shell line matches its pinned form, character
    for character.

    Only the ``input:`` block's dependency-declaration key carries the
    reference namespace; the shell line names the real slot, so a
    one-reference rule's command does not depend on how many references
    the generator can fan in.
    """
    from wfc.graph import StepDef
    from wfc.orchestration.snakemake import _generate_rule

    step = StepDef(method_name="m", module_name="mod", script_path="s.py",
                   params={}, node_id="n1", env="container:e",
                   run_ref_inputs={"data": ["/w/a.csv"]})
    lines = _generate_rule(step, {"n1": step}, pipeline_id="p")

    assert lines[-3] == "    shell:"
    assert lines[-2] == (
        '        "{sys.executable} -m wfc run-step '
        '--node-id {params.node_id} '
        '--sample {wildcards.sample} '
        '--variant {params.variant}'
        ' --ref-input data=/w/a.csv"'
    )
    # The dependency-declaration key carries the reference namespace.
    assert '        ref0_data="/w/a.csv"' in lines


def test_reference_keys_cannot_collide_with_an_upstream_on_the_same_slot():
    """A wired upstream and a reference on one slot emit distinct ``input:`` keys.

    A Snakemake ``input:`` block is Python keyword syntax, so a repeated key
    is a syntax error in the generated file rather than a silent overwrite.
    """
    from wfc.graph import StepDef
    from wfc.orchestration.snakemake import _generate_rule

    up = StepDef(method_name="up", module_name="mod", script_path="u.py",
                 params={}, node_id="up", env="container:e")
    step = StepDef(method_name="m", module_name="mod", script_path="s.py",
                   params={}, node_id="n1", env="container:e",
                   depends_on=["up"], inputs={"data": ["up"]},
                   run_ref_inputs={"data": ["/w/a.csv", "/w/b.csv"]})
    lines = _generate_rule(step, {"n1": step, "up": up}, pipeline_id="p")

    keys = [ln.strip().split("=", 1)[0]
            for ln in "\n".join(lines).split(",\n")
            if ln.strip().startswith(("data", "ref"))]
    assert len(keys) == len(set(keys)), keys
    assert "ref0_data" in keys and "ref1_data" in keys


# =============================================================================
# Lineage and invalidation
# =============================================================================

#: The consumer target both halves of the lineage story are asserted about.
SINK = ("sink", "s1", "default")


def _referenced_pipeline(source: str) -> Scenario:
    """A consumer fed by one reference pointing at ``source``'s seeded run.

    Two sources are always seeded and always present in the document; only
    which of them the reference names varies. Everything that feeds the
    consumer's cache key other than that choice — its method, its params, its
    env and its sample — is therefore identical between the two shapes.

    Args:
        source: Node id of the seeded run the reference points at.

    Returns:
        The scenario.
    """
    return Scenario(
        nodes=[
            selector(),
            node("src_a", inputs=[wire("sel")], params={"k": 1}),
            node("src_b", inputs=[wire("sel")], params={"k": 2}),
            reference("ref", run_of=source),
            node("sink", inputs=[wire("ref", source_slot=None)]),
        ],
        prior_runs=[completed("src_a"), completed("src_b")],
    )


@workflow(purpose="A run fed by a run_reference records the referenced run as "
                  "its lineage source, and repointing the reference at a "
                  "different run changes the consumer's own cache key")
def test_reference_is_recorded_as_a_parent_and_moves_the_cache_key(
        tmp_path_factory, monkeypatch):
    口 = Step(step_num=1, name="Run the consumer against the first source",
             purpose="The reference is bound to a seeded run after that run "
                     "has executed, so the document names a run id a real "
                     "run produced")
    root_a = tmp_path_factory.mktemp("point_at_a")
    obs_a = run_target(_referenced_pipeline("src_a"), "sink",
                       root=root_a, monkeypatch=monkeypatch)

    口 = Step(step_num=2, name="The referenced run is a lineage source",
             purpose="Run history has to be able to answer what a "
                     "reference-fed result was computed from")
    referenced = _referenced_run_id(obs_a, "ref")
    parents = [(r["input_name"], r["source_run_id"])
               for r in obs_a.run_input_rows
               if r["run_id"] == obs_a.runs[SINK].run_id]
    assert parents == [("data", referenced)]

    口 = Step(step_num=3, name="The reference did not become a second input path",
             purpose="A reference-derived parent reaches registration and "
                     "nothing else. Leaking it into the value the "
                     "orchestrator forwards to materialize would resolve the "
                     "artifact into a slot the reference merge fills again")
    assert len(obs_a.phase_args("dispatch", SINK)["slot_paths"]["data"]) == 1

    口 = Step(step_num=4, name="Repoint the reference and run again",
             purpose="An otherwise identical pipeline whose reference names "
                     "the other seeded run")
    root_b = tmp_path_factory.mktemp("point_at_b")
    obs_b = run_target(_referenced_pipeline("src_b"), "sink",
                       root=root_b, monkeypatch=monkeypatch)
    assert _referenced_run_id(obs_b, "ref") != referenced

    口 = Step(step_num=5, name="The consumer's cache key moved with it",
             purpose="Equal keys would mean a consumer handed back a result "
                     "computed from data it no longer reads")
    assert obs_a.run_row(SINK)["cache_key"] != obs_b.run_row(SINK)["cache_key"]


def _referenced_run_id(obs, ref_node_id: str) -> int:
    """Return the run id the named reference node was bound to."""
    return int(_reference_node(obs, ref_node_id)["run_id"])


def _reference_node(obs, ref_node_id: str) -> dict:
    """Return one reference node as the executed document carries it."""
    doc = json.loads(Path(obs.project.pipeline_json).read_text())
    return next(n for n in doc["nodes"] if n["id"] == ref_node_id)


@workflow(purpose="A reference-fed run seeded as a prior run keys the same as "
                  "the same pipeline's scheduled run: running the consumer "
                  "again, with the reference still naming the same run, "
                  "claims a cache hit on the seeded run -- the consumer jumps "
                  "claim -> record and is served the seeded result")
def test_reference_left_alone_leaves_the_consumer_cacheable(git_project,
                                                            monkeypatch):
    口 = Step(step_num=1, name="Seed the consumer's first run, for real",
             purpose="The consumer is itself a prior run: seeding binds its "
                     "reference to the seeded source run before running it, "
                     "so the first execution consumes the reference exactly "
                     "as a scheduled run would")
    scn = _referenced_pipeline("src_a")
    scn.prior_runs.append(completed("sink"))

    口 = Step(step_num=2, name="Run the same pipeline's consumer again",
             purpose="Same project, same database, same reference -- the only "
                     "thing that differs between the two executions is that "
                     "one of them has already happened")
    obs = run_target(scn, "sink", root=git_project, monkeypatch=monkeypatch)
    referenced = _referenced_run_id(obs, "ref")
    seeded = [r for r in obs.rows_for_node("sink")
              if r["id"] != obs.runs[SINK].run_id]
    assert len(seeded) == 1 and seeded[0]["status"] == "completed"
    assert seeded[0]["cache_source_run_id"] is None

    口 = Step(step_num=3, name="The consumer short-circuits",
             purpose="Not merely an equal cache key — the phases that do the "
                     "work never run, which is what a user would notice")
    assert obs.phases_ran(SINK) == ["claim", "record"]
    assert obs.runs[SINK].ending == "cached"
    assert obs.run_row(SINK)["cache_source_run_id"] == seeded[0]["id"]

    口 = Step(step_num=4, name="The reference was in the claim that hit",
             purpose="A hit reached by ignoring the reference would prove "
                     "nothing. Both the seeded run and the cached one register "
                     "the referenced run as their lineage parent, so the "
                     "reference is inside the fingerprint that matched")
    for run_id in (seeded[0]["id"], obs.runs[SINK].run_id):
        parents = [(r["input_name"], r["source_run_id"])
                   for r in obs.run_input_rows if r["run_id"] == run_id]
        assert parents == [("data", referenced)], run_id


def test_reference_naming_an_unresolvable_run_fails_the_load(git_project,
                                                             monkeypatch):
    """A reference to a run that is not there stops the pipeline, loudly.

    A run that is not there has no outputs, so the reference cannot serve
    the link and the load refuses the document before any step claims.
    Silently skipping it would write a lineage row pointing at nothing while
    the fingerprint builder — which skips runs it cannot find — quietly left
    it out of the cache key.
    """
    from sqlmodel import select

    from wfc.execution import load_pipeline_from_path
    from wfc.persistence import Run, get_session

    scn = Scenario(nodes=[
        reference("ref", run_id="999999"),
        node("n1", inputs=[wire("ref", source_slot=None)]),
    ])
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        load_pipeline_from_path(project.pipeline_json)

    message = str(excinfo.value)
    assert "ref" in message
    assert "999999" in message
    with get_session() as session:
        assert session.exec(select(Run)).all() == []


def _two_slot_consumer(*, on_a: str, on_b: str) -> Scenario:
    """Two selector-fed producers feeding one consumer on two distinct slots.

    Args:
        on_a: The producer wired into the consumer's ``a`` slot.
        on_b: The producer wired into the consumer's ``b`` slot.

    Returns:
        The scenario; the producers are the same two nodes either way round.
    """
    return Scenario(
        nodes=[
            selector(),
            node("pa", inputs=[wire("sel")]),
            node("pb", inputs=[wire("sel")]),
            node("consumer", inputs=[wire(on_a, target_slot="a"),
                                     wire(on_b, target_slot="b")]),
        ],
        samples=["s1"],
        pipeline_id="two-slots",
        name="two-slots",
    )


def _parents_by_slot(run) -> dict[str, tuple[int, str]]:
    """Return ``{input slot: (source run id, source slot)}`` the claim recorded."""
    return {r["input_name"]: (r["source_run_id"], r["source_slot"])
            for r in run.observation.run_input_rows
            if r["run_id"] == run.run_id}


@workflow(purpose="Two selector-fed producers wired into two distinct input "
                  "slots of one consumer each reach the consumer's claim as "
                  "their own slot-qualified part, so swapping the two "
                  "upstreams between the slots moves the consumer's cache key")
def test_two_upstreams_on_distinct_slots_each_render_their_own_part(
        git_project, monkeypatch):
    口 = Step(step_num=1, name="Complete both producers and claim the consumer",
             purpose="The producers run for real so the consumer's claim "
                     "resolves each slot's parent from the run-id sidecar "
                     "production wrote, never from an id the test hands in")
    straight = _two_slot_consumer(on_a="pa", on_b="pb")
    pa = completed_run(git_project, monkeypatch=monkeypatch,
                       scenario=straight, target="pa")
    pb = completed_run(git_project, monkeypatch=monkeypatch,
                       scenario=straight, target="pb")
    first = claimed_run(git_project, monkeypatch=monkeypatch,
                        scenario=straight, target="consumer")

    口 = Step(step_num=2, name="Each slot records its own parent",
             purpose="The lineage rows are the slot-qualified identities the "
                     "claim fingerprinted: one parent per slot, neither "
                     "collapsed into the other; the two producers carry "
                     "distinct keys, so a swap has something to move")
    assert _parents_by_slot(first) == {"a": (pa.run_id, "data"),
                                       "b": (pb.run_id, "data")}
    assert pa.cache_key != pb.cache_key

    口 = Step(step_num=3, name="Swap the upstreams between the slots and claim again",
             purpose="The same two runs, the same consumer, the same params "
                     "and env; only which slot each parent arrives through "
                     "changes")
    swapped = _two_slot_consumer(on_a="pb", on_b="pa")
    second = claimed_run(git_project, monkeypatch=monkeypatch,
                         scenario=swapped, target="consumer")
    assert _parents_by_slot(second) == {"a": (pb.run_id, "data"),
                                        "b": (pa.run_id, "data")}

    口 = Step(step_num=4, name="The consumer's cache key moved with the wiring",
             purpose="Equal keys would mean a part that dropped its input "
                     "slot, so two wirings of the same content collapsed to "
                     "one contribution")
    assert second.run_id != first.run_id
    assert first.cache_key != second.cache_key
