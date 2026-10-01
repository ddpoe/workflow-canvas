"""Structural validation: the sparse canvas document against a contract map.

The core of the canvas's validate endpoint, as a function of two values:
the document the canvas exports (``sourceHandle`` / ``targetHandle`` on its
links) and Registration's contract map keyed ``module.method``. The
endpoint fetches the map and calls this; tests and in-process callers call
it directly. It answers ``{valid, errors, warnings}`` and never raises on
a shape question.
"""

from __future__ import annotations

from typing import Any

from axiom_annotations import task


def _node_type(node: dict) -> str:
    return node.get("type") or "method"


def second_selector_errors(method_selectors: dict[str, list[str]]) -> list[str]:
    """The second-selector rule, written once for both moments.

    A method node fed by more than one per-sample ``input_selector`` is
    refused: the engine schedules one sample axis per node, so a second
    per-sample selector has no meaning yet. Structural validation and the
    load's legality stage both call this with their own view of the wiring.

    Args:
        method_selectors: ``{method node id: [per-sample selector ids]}`` --
            the distinct per-sample selectors upstream of each method node,
            in wiring order. Fan-in selectors are not counted (the
            sole-upstream rule owns them).

    Returns:
        One message per offending method node, naming it and every selector.
    """
    errors: list[str] = []
    for node_id, selectors in method_selectors.items():
        if len(selectors) < 2:
            continue
        named = ", ".join(f"'{s}'" for s in selectors)
        errors.append(
            f"Method node '{node_id}' has {len(selectors)} per-sample input "
            f"selectors upstream ({named}); a method node takes one per-sample "
            f"selector. Wire the other selector into its own method node, or "
            f"select every sample on one selector."
        )
    return errors


def fan_in_upstream_errors(
    fan_in_upstreams: dict[str, list[str]],
    upstream_link_counts: dict[str, int],
) -> list[str]:
    """The fan-in sole-upstream rule, written once for both moments.

    A fan-in selector delivers one bundle of samples to one consumer, and
    the engine gives that consumer a single collapsed sample axis. Two
    shapes have no meaning yet and are refused:

    * more than one fan-in selector wired into the same method node --
      whichever arrived second used to silently replace the first, so two
      documents with different first-selector bundles computed the same
      cache key;
    * a fan-in selector beside any other upstream on the same consumer.

    Structural validation (the canvas's preview) and the load's legality
    stage (the launch path) both call this with their own view of the
    wiring, so a document gets the same verdict and the same message at
    either moment.

    Args:
        fan_in_upstreams: ``{method node id: [fan-in selector ids]}`` --
            the distinct fan-in selectors wired into each method node, in
            link order. Nodes with no fan-in upstream may be omitted.
        upstream_link_counts: ``{method node id: incoming link count}``
            over every upstream, fan-in or not. Links are counted rather
            than distinct sources: one selector wired into two slots feeds
            only one of them, so it stays refused.

    Returns:
        One message per offending method node.
    """
    errors: list[str] = []
    for node_id, selectors in fan_in_upstreams.items():
        if not selectors:
            continue
        if len(selectors) > 1:
            named = ", ".join(f"'{s}'" for s in selectors)
            errors.append(
                f"Method node '{node_id}' has {len(selectors)} fan-in input "
                f"selectors upstream ({named}); a method node takes one "
                f"fan-in selector. Select every sample on one selector, or "
                f"wire the other selector into its own method node."
            )
            continue
        total = upstream_link_counts.get(node_id, len(selectors))
        if total > 1:
            errors.append(
                f"Fan-in mode on '{selectors[0]}' is only supported when "
                f"it is the sole upstream of its consumer. Consumer "
                f"'{node_id}' has {total} upstreams."
            )
    return errors


@task(purpose="Validate a sparse pipeline document against a contract map: "
              "unknown methods, unconnected required slots, one edge per "
              "slot (reference fan-in exempt), fan-in shapes, one per-sample "
              "selector per method node, method roots")
def validate_structure(document: dict, contract_map: dict) -> dict[str, Any]:
    """Validate a pipeline graph's structure against a contract map.

    Args:
        document: The sparse canvas document -- ``nodes`` (``id``, ``type``,
            ``method``, ``module``, ``samples``, ``fan_mode``) and ``links``
            (``source``, ``target``, ``sourceHandle``, ``targetHandle``).
        contract_map: ``{"<module>.<method>": {"input_slots": ..., ...}}``
            for every registered method (``wfc.registration.load_contract_map``).

    Returns:
        ``{"valid": bool, "errors": [str], "warnings": [str]}``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    nodes: list[dict] = list(document.get("nodes") or [])
    links: list[dict] = list(document.get("links") or [])

    if not nodes:
        errors.append("Pipeline has no nodes")
        return {"valid": False, "errors": errors, "warnings": warnings}

    for node in nodes:
        # System nodes (input_selector, run_reference) are not registered
        # methods — skip method validation for them.
        node_type = _node_type(node)
        if node_type in ("input_selector", "run_reference"):
            continue
        mod_name = node.get("module") or ""
        method = node.get("method", "")
        key = f"{mod_name}.{method}"
        info = contract_map.get(key)
        if info is None:
            errors.append(f"Unknown method: {mod_name}.{method!r}")
            continue
        input_slots = info.get("input_slots") or {}
        for slot_name, slot_spec in input_slots.items():
            if slot_spec.get("required", True):
                connected = any(
                    lnk.get("target") == node.get("id")
                    and lnk.get("targetHandle") == slot_name
                    for lnk in links
                )
                if not connected:
                    warnings.append(
                        f"{method}: required input slot {slot_name!r} is not connected"
                    )

    # Input-slot uniqueness: a single input slot on a method node must not
    # have more than one incoming edge. Multi-edge-per-slot does not work
    # end-to-end (the engine hard-wires the sample axis per upstream), so
    # reject it at validate time instead of letting Snakemake fail silently.
    #
    # Narrow exemption: a slot every one of whose incoming edges comes from a
    # ``run_reference`` is a reference fan-in, which the engine delivers as N
    # artifacts under that one slot name. The predicate is ALL, not ANY — a
    # second method-node edge, or a method edge joined by a reference, stays
    # refused with the one-edge-per-slot message.
    reference_node_ids = {
        str(n.get("id")) for n in nodes if _node_type(n) == "run_reference"
    }
    slot_edges: dict[tuple, list[str]] = {}
    for lnk in links:
        slot_key = (str(lnk.get("target")), str(lnk.get("targetHandle") or ""))
        slot_edges.setdefault(slot_key, []).append(str(lnk.get("source")))
    for (tgt, slot), sources in slot_edges.items():
        if all(s in reference_node_ids for s in sources):
            continue
        if len(sources) > 1:
            slot_label = slot or "(default)"
            errors.append(
                f"Input slot '{slot_label}' on node '{tgt}' has "
                f"{len(sources)} incoming edges; only one edge per slot is "
                f"supported. Sources: {', '.join(sources)}."
            )

    # Fan-in shape checks (single-selector fan-in): reject shapes the
    # engine does not yet support so the user sees a clear message instead
    # of a cryptic Snakemake failure.
    #
    # 1. Any input_selector with fan_mode="in" must have at least one sample.
    # 2. A method node whose upstreams include a fan-in selector must have
    #    exactly one upstream (the selector) -- multi-selector or
    #    mixed-kind upstreams on a fan-in consumer are out of scope.
    fan_in_selectors: dict[str, dict] = {}
    for node in nodes:
        if _node_type(node) == "input_selector" and (node.get("fan_mode") or "out") == "in":
            fan_in_selectors[str(node.get("id"))] = node
            if not (node.get("samples") or []):
                errors.append(
                    f"Input selector '{node.get('id')}' has fan_mode='in' but no "
                    f"samples selected. Fan-in requires at least one sample."
                )

    if fan_in_selectors:
        # Group links by target for multi-upstream detection.
        upstreams_by_target: dict[str, list[str]] = {}
        for lnk in links:
            upstreams_by_target.setdefault(
                str(lnk.get("target")), []
            ).append(str(lnk.get("source")))

        fan_in_upstreams: dict[str, list[str]] = {}
        upstream_link_counts: dict[str, int] = {}
        for node in nodes:
            if _node_type(node) != "method":
                continue
            tgt_id = str(node.get("id"))
            ups = upstreams_by_target.get(tgt_id, [])
            fan_in_ups = [u for u in dict.fromkeys(ups) if u in fan_in_selectors]
            if fan_in_ups:
                fan_in_upstreams[tgt_id] = fan_in_ups
                upstream_link_counts[tgt_id] = len(ups)
        errors.extend(fan_in_upstream_errors(fan_in_upstreams, upstream_link_counts))

    # One per-sample selector per method node (the second-selector rule).
    per_sample_selectors = {
        str(n.get("id")) for n in nodes
        if _node_type(n) == "input_selector" and (n.get("fan_mode") or "out") != "in"
    }
    method_selectors: dict[str, list[str]] = {}
    for node in nodes:
        if _node_type(node) != "method":
            continue
        tgt_id = str(node.get("id"))
        sources = [str(lnk.get("source")) for lnk in links if str(lnk.get("target")) == tgt_id]
        method_selectors[tgt_id] = [
            s for s in dict.fromkeys(sources) if s in per_sample_selectors
        ]
    errors.extend(second_selector_errors(method_selectors))

    # Structural check: method nodes cannot be DAG roots (no incoming edges).
    # Only system nodes (input_selector, run_reference) are valid roots.
    incoming = {str(lnk.get("target")) for lnk in links}
    for node in nodes:
        if _node_type(node) in ("input_selector", "run_reference"):
            continue  # system nodes are valid roots
        node_id = str(node.get("id"))
        if node_id not in incoming:
            errors.append(
                f"Method node '{node_id}' cannot be a pipeline root. "
                f"Use an input_selector or run_reference system node as the root "
                f"and connect it to this method node."
            )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
