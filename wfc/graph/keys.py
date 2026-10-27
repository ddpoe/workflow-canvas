"""The keys a pipeline document may carry, and the refusal of any other.

A key wfc does not read is refused by name rather than ignored: a misspelt
``parmas`` or a leftover node ``script`` would otherwise load and run as
something the author did not write. Each refusal lists the keys allowed
where the unknown one sits, so a hand-written document can be corrected
without guessing. The node keys include what the canvas
posts (system-node fields too, which the canvas's model emits on every node),
what enrichment adds to the frozen copy (``slot_outputs``, ``slot_types``,
``env``) and the version pin. Pure: the document is the only input.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from axiom_annotations import task

#: Top-level keys of a pipeline document.
TOP_LEVEL_KEYS = frozenset({
    "name", "nodes", "links", "samples", "param_sets", "explicit_combos",
    "keep_going", "variables",
})

#: Keys a node may carry, method and system nodes alike.
NODE_KEYS = frozenset({
    # identity and canvas layout
    "id", "type", "label", "position",
    # method nodes
    "module", "method", "params", "version", "output_ext",
    # what enrichment writes into the frozen copy, and the env alias
    "slot_outputs", "slot_types", "env", "env_strategy",
    # system nodes (input_selector, run_reference); a selector's
    # ``keep_going`` is the canvas's own: it compiles it onto the node and
    # reads it back to set the run's top-level switch
    "samples", "run_id", "source", "fan_mode", "keep_going",
})


@task(purpose="Refuse every top-level and node key a pipeline document carries "
              "that wfc does not read, naming the node and the key",
      inputs="A pipeline document (after variable substitution)",
      outputs="One refusal message per unknown key (empty when none)")
def document_key_refusals(document: Mapping[str, Any]) -> list[str]:
    """List the document's unknown top-level and node keys.

    Args:
        document: The pipeline document.

    Returns:
        One message per unknown key, top-level keys first, then nodes in
        document order; empty when every key is known.
    """
    top_level_allowed = ", ".join(sorted(TOP_LEVEL_KEYS))
    node_allowed = ", ".join(sorted(NODE_KEYS))
    refusals = [
        f"Pipeline: unknown top-level key '{key}' "
        f"(a pipeline takes {top_level_allowed})"
        for key in document if key not in TOP_LEVEL_KEYS
    ]
    for node in document.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        for key in node:
            if key not in NODE_KEYS:
                hint = (" (a node's code is its method version's; remove it)"
                        if key == "script" else
                        f" (a node takes {node_allowed})")
                refusals.append(
                    f"Node '{node.get('id')}': unknown key '{key}'{hint}")
    return refusals
