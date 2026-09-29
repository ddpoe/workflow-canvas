"""Order: the topological sort and the leaves, two questions over dependencies."""

from __future__ import annotations

from collections import defaultdict

from axiom_annotations import task

from .model import StepDef


@task(purpose="Topologically sort pipeline steps so upstream runs execute before downstream")
def topo_sort_steps(steps: list[StepDef]) -> list[StepDef]:
    """Topologically sort steps using Kahn's algorithm.

    Args:
        steps: List of StepDef objects with depends_on relationships.

    Returns:
        Steps in topological order (roots first).

    Raises:
        ValueError: If the DAG contains a cycle.
    """
    step_map = {s.node_id: s for s in steps}
    in_degree: dict[str, int] = {s.node_id: 0 for s in steps}
    adj: dict[str, list[str]] = defaultdict(list)

    for s in steps:
        for dep in s.depends_on:
            adj[dep].append(s.node_id)
            in_degree[s.node_id] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    ordered: list[str] = []

    while queue:
        name = queue.pop(0)
        ordered.append(name)
        for child in adj[name]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(ordered) != len(steps):
        raise ValueError("Pipeline DAG contains a cycle")

    return [step_map[name] for name in ordered]


def find_leaf_nodes(step_map: dict[str, StepDef]) -> list[str]:
    """Return node_ids that have no downstream dependents (leaf nodes).

    Args:
        step_map: ``{node_id: StepDef}`` for every step in the pipeline.

    Returns:
        The node ids no other step depends on, in ``step_map`` order.
    """
    has_children = set()
    for s in step_map.values():
        for dep in s.depends_on:
            has_children.add(dep)
    return [nid for nid in step_map if nid not in has_children]
