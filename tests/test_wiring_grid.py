"""The wiring grid: every input-kind combination the load accepts runs, and
every one it refuses is refused at load.

The grid is over what feeds one method node: a per-sample selector (S), a
fan-in selector (C), a method parent (M) and a run reference (R). An
accepted cell is driven through claim and materialize and must deliver
every slot the node's document wires; a refused cell must be refused by
the load, before anything is scheduled. A cell accepted at load and then
failing after the work is the shape this sweep exists to catch.

Tier 2 on the stub rung, one scenario per cell. The standing invariant pack
is asserted by the harness after each accepted cell.
"""
from __future__ import annotations

import pytest
from axiom_annotations import workflow

from tests.harness import (
    Phase,
    Scenario,
    build_project,
    chain,
    completed,
    fan_in,
    node,
    reference,
    run_target,
    selector,
    selector_beside_method,
    selector_beside_method_and_reference,
    wire,
)


def _sourced(*extra, consumer_inputs):
    """A selector, a seeded reference source, any extra producers, the consumer."""
    return [selector(), node("src", inputs=[wire("sel")]),
            reference("ref", run_of="src"), *extra,
            node("c", inputs=consumer_inputs)]


#: cell -> (nodes, prior runs). Each consumer is node "c" (or "quantify").
ACCEPTED = {
    "S": ([selector(), node("c", inputs=[wire("sel")])], []),
    "C": ([selector(fan_mode="in"), node("c", inputs=[wire("sel", bundle=True)])], []),
    "M": (chain("up", "c"), ["up"]),
    "R": (_sourced(consumer_inputs=[wire("ref", source_slot=None)]), ["src"]),
    "M+M one slot": ([selector(), node("a", inputs=[wire("sel")]),
                      node("b", inputs=[wire("sel")]),
                      node("c", inputs=fan_in("a", "b"))], ["a", "b"]),
    "S+M": (selector_beside_method("c", "up"), ["up"]),
    "S+R": (_sourced(consumer_inputs=[
        wire("sel", target_slot="raw"),
        wire("ref", source_slot=None, target_slot="model")]), ["src"]),
    "M+R": (_sourced(node("up", inputs=[wire("sel")]), consumer_inputs=[
        wire("up", target_slot="mask"),
        wire("ref", source_slot=None, target_slot="model")]), ["src", "up"]),
    "S+M+R": (selector_beside_method_and_reference("c", "up", source="src",
                                                   reference_id="ref"),
              ["src", "up"]),
}

def _bundle_beside_reference(run_id: str):
    """A fan-in bundle and a reference to a real run, both into one consumer."""
    return [selector("bundle", fan_mode="in"), reference("ref", run_id=run_id),
            node("c", inputs=[wire("bundle", bundle=True, target_slot="sources"),
                              wire("ref", source_slot=None, target_slot="model")])]


#: cell -> (nodes, a fragment the load's refusal names). A callable cell
#: takes the id of a real run for its reference to name.
REFUSED = {
    "S+S": ([selector("s_a"), selector("s_b"),
             node("c", inputs=[wire("s_a", target_slot="a"),
                               wire("s_b", target_slot="b")])],
            "selector"),
    "C+C": ([selector("c_a", fan_mode="in"), selector("c_b", fan_mode="in"),
             node("c", inputs=[wire("c_a", bundle=True, target_slot="a"),
                               wire("c_b", bundle=True, target_slot="b")])],
            "fan-in"),
    "C+M": ([selector("bundle", fan_mode="in"), selector(),
             node("up", inputs=[wire("sel")]),
             node("c", inputs=[wire("bundle", bundle=True, target_slot="sources"),
                               wire("up", target_slot="mask")])],
            "sole upstream"),
    "C+S": ([selector("bundle", fan_mode="in"), selector(),
             node("c", inputs=[wire("bundle", bundle=True, target_slot="sources"),
                               wire("sel", target_slot="raw")])],
            "sole upstream"),
    "C+R": (_bundle_beside_reference, "sole upstream"),
}


def _consumer(nodes) -> str:
    return nodes[-1].id


@pytest.mark.parametrize("cell", sorted(ACCEPTED))
@workflow(purpose="Every grid cell the load accepts runs through claim and "
                  "materialize with every slot its document wires delivered")
def test_an_accepted_wiring_runs_with_every_slot_delivered(cell, git_project,
                                                          monkeypatch):
    nodes, seeded = ACCEPTED[cell]
    consumer = _consumer(nodes)
    wired = {w.target_slot for w in nodes[-1].inputs}
    scn = Scenario(nodes=nodes, prior_runs=[completed(n) for n in seeded])

    obs = run_target(scn, consumer, root=git_project, monkeypatch=monkeypatch,
                     through=Phase.MATERIALIZE)

    slot_paths = obs.phase_args("dispatch")["slot_paths"]
    assert set(slot_paths) == wired, cell
    assert all(slot_paths[slot] for slot in wired), slot_paths
    if cell == "M+M one slot":
        assert len(slot_paths["data"]) == 2


@pytest.mark.parametrize("cell", sorted(REFUSED))
@workflow(purpose="Every grid cell the load refuses is refused at load, "
                  "before the document has any run row")
def test_a_refused_wiring_is_refused_at_load(cell, git_project, monkeypatch):
    from wfc.execution import load_pipeline_from_path
    from wfc.persistence import Run, get_session
    from sqlmodel import select

    nodes, fragment = REFUSED[cell]
    if callable(nodes):
        # The reference names a run that really exists, produced under its
        # own pipeline, so the only thing wrong with the document is its
        # wiring.
        producer = Scenario(nodes=[selector(), node("src", inputs=[wire("sel")])],
                            pipeline_id="producer", name="producer")
        produced = run_target(producer, "src", root=git_project,
                              monkeypatch=monkeypatch)
        nodes = nodes(str(produced.runs[("src", "s1", "default")].run_id))
    project = build_project(Scenario(nodes=nodes), root=git_project,
                            monkeypatch=monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        load_pipeline_from_path(project.pipeline_json)
    assert fragment in str(excinfo.value), str(excinfo.value)
    with get_session() as session:
        assert session.exec(select(Run).where(
            Run.pipeline_id == project.pipeline_id)).all() == []
