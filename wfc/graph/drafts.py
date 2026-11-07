"""Draft mode: the steps a pipeline runs as a draft run, and the draft each belongs to.

A method node pinned to ``draft`` runs its method's draft. That node and
every step downstream of it run in draft mode: they always run, and they
leave no run row, cache entry, archive or push. Each draft-mode step belongs
to one draft, whose folder holds its results: a draft owns itself, and a
step below drafts belongs to the first of the drafts it is reached from in
Graph's topological order (ties broken by document order), so a step below
a chain of drafts belongs to the nearest one and a join of two drafts to the
earlier. Execution, the CLI and the Canvas API all ask this one rule.

Pure: the document is the only input, in either form (sparse or enriched:
both name a link's ``source`` and ``target``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from axiom_annotations import task

from ..contracts import DRAFT_PIN
from .model import StepDef
from .order import topo_sort_steps

_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


def is_draft_node(node: Mapping[str, Any]) -> bool:
    """Whether a document node runs its method's draft.

    Args:
        node: A document node.

    Returns:
        True for a method node pinned to ``draft``.
    """
    return ((node.get("type") or "method") not in _SYSTEM_NODE_TYPES
            and node.get("version") == DRAFT_PIN)


@task(purpose="Name every draft-mode step of a pipeline document (a draft and "
              "every step downstream of it) with the draft that owns it: the "
              "draft itself, else the first of its upstream drafts in Graph's "
              "topological order",
      inputs="A pipeline document, sparse or enriched",
      outputs="{draft-mode node id: owning draft node id}; empty with no draft",
      critical="One rule for every caller: a step below two drafts has one home")
def draft_owners(document: Mapping[str, Any]) -> dict[str, str]:
    """Each draft-mode step of a document, with the draft that owns it.

    Args:
        document: The pipeline document.

    Returns:
        ``{node id: owning draft's node id}`` for the drafts and every method
        node downstream of one, in Graph's topological order; steps upstream
        of every draft, and system nodes, are not in it.

    Raises:
        ValueError: The document's method wiring has a cycle.
    """
    nodes = [n for n in document.get("nodes") or []
             if isinstance(n, Mapping)
             and (n.get("type") or "method") not in _SYSTEM_NODE_TYPES]
    if not any(is_draft_node(n) for n in nodes):
        return {}
    ids = {str(n.get("id")) for n in nodes}
    parents: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for link in document.get("links") or []:
        source, target = str(link.get("source")), str(link.get("target"))
        if source in ids and target in ids and source not in parents[target]:
            parents[target].append(source)
    steps = [StepDef(method_name=str(n.get("method") or ""),
                     module_name=str(n.get("module") or ""), params={},
                     node_id=str(n.get("id")), depends_on=parents[str(n.get("id"))])
             for n in nodes]
    order = [s.node_id for s in topo_sort_steps(steps)]
    rank = {node_id: i for i, node_id in enumerate(order)}
    drafts = {str(n.get("id")) for n in nodes if is_draft_node(n)}
    owners: dict[str, str] = {}
    for node_id in order:
        if node_id in drafts:
            owners[node_id] = node_id
            continue
        upstream = [owners[p] for p in parents[node_id] if p in owners]
        if upstream:
            owners[node_id] = min(upstream, key=rank.__getitem__)
    return owners
