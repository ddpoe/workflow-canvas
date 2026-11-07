"""Structural validation: the sparse canvas document against a contract map.

The core of the canvas's validate endpoint, as a function of two values:
the document the canvas exports (``sourceHandle`` / ``targetHandle`` on its
links) and Registration's contract map keyed ``module.method``. The
endpoint fetches the map and calls this; tests and in-process callers call
it directly. It answers ``{valid, errors, warnings}`` and never raises on
a shape question.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from axiom_annotations import task

from ..contracts import (
    DRAFT_PIN,
    input_is_optional,
    input_takes_many,
    version_entry,
    version_refusal,
)
from .variables import param_sets_refusal


def _node_type(node: dict) -> str:
    return node.get("type") or "method"


def input_file_counts(nodes: list[dict], links: list[dict], *, slot_key: str,
                      default_slot: str) -> dict[tuple[str, str], int]:
    """How many files reach each fed input slot, by the document's links.

    A link carries one file, except a link out of a fan-in selector
    (``fan_mode="in"``), which bundles every sample it selects into the slot.

    Args:
        nodes: The document's nodes, system nodes included.
        links: The document's links.
        slot_key: The link key naming the fed slot (``targetHandle`` in the
            sparse canvas document, ``target_slot`` in the enriched one).
        default_slot: The slot a link naming none feeds.

    Returns:
        ``{(target node id, slot): file count}``.
    """
    bundles = {
        str(n.get("id")): len(n.get("samples") or [])
        for n in nodes
        if _node_type(n) == "input_selector" and (n.get("fan_mode") or "out") == "in"
    }
    counts: dict[tuple[str, str], int] = {}
    for lnk in links:
        key = (str(lnk.get("target")), str(lnk.get(slot_key) or default_slot))
        counts[key] = counts.get(key, 0) + bundles.get(str(lnk.get("source")), 1)
    return counts


def declared_input_slots(nodes: list[dict], contract_map: dict) -> dict[str, dict]:
    """Each method node's declared inputs, from its version's contract.

    Args:
        nodes: Document nodes; system nodes and nodes whose method the map
            does not hold are left out.
        contract_map: Registration's contract map keyed ``module.method``.

    Returns:
        ``{method node id: {input name: declaration}}``.
    """
    out: dict[str, dict] = {}
    for node in nodes:
        if _node_type(node) != "method":
            continue
        entry = contract_map.get(f"{node.get('module') or ''}.{node.get('method', '')}")
        if not entry:
            continue
        entry = version_entry(entry, node.get("version")) or entry
        out[str(node.get("id"))] = entry.get("input_slots") or {}
    return out


def _slot_list(slots: dict) -> str:
    """The declared slot names for a message, or ``none``."""
    return ", ".join(f"'{name}'" for name in slots) or "none"


def _node_contracts(nodes: list[dict], contract_map: dict,
                    ) -> dict[str, tuple[str, Mapping[str, Any]]]:
    """Each method node's label and version contract, where the map resolves it.

    A node whose method the map does not hold, or whose version it cannot
    run (``version_refusal``), is left out: other rules refuse those. A
    draft node reads its draft's contract (``versions["draft"]``).

    Args:
        nodes: The document's nodes; only method nodes are read.
        contract_map: Registration's contract map keyed ``module.method``.

    Returns:
        ``{node id: (label, contract)}``; the label is ``module.method``, or
        ``<module.method>'s draft`` for a draft node.
    """
    contracts: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for node in nodes:
        if _node_type(node) != "method":
            continue
        qualified = f"{node.get('module') or ''}.{node.get('method', '')}"
        entry = contract_map.get(qualified)
        if not entry or version_refusal(node, entry) is not None:
            continue
        contract = version_entry(entry, node.get("version"))
        if contract is None:
            continue
        # A registered pin is a version identity (a hash on the run path),
        # so only the draft is named; the node id says which node it is.
        label = (f"{qualified}'s draft" if node.get("version") == DRAFT_PIN
                 else qualified)
        contracts[str(node.get("id"))] = (label, contract)
    return contracts


def unwired_input_errors(nodes: list[dict], links: list[dict], contract_map: dict,
                         *, fed_outside: Mapping[str, Iterable[str]] | None = None,
                         ) -> list[str]:
    """The unwired-required-input rule, written once for both moments.

    Every input a method node's version declares must be wired by a link
    into that node, unless it says ``required: false`` (Contracts'
    ``input_is_optional``: an input with no ``required`` key is required).
    A link wires the input its ``target_slot`` / ``targetHandle`` names; a
    link naming none wires ``data``, as the run path reads it. Links from
    selectors, methods and run references all count. A draft node is read
    against its draft's contract. A node whose method the map does not
    hold, whose version it cannot run, or whose contract has no
    ``input_slots`` key is left to the rules that refuse those. Structural
    validation and the load's legality stage both call this, so either
    moment refuses the same input with the same message.

    Args:
        nodes: The document's nodes; only method nodes are read.
        links: The document's links, in either form (the sparse canvas
            handles or the enriched slot keys).
        contract_map: Registration's contract map keyed ``module.method``.
        fed_outside: Per node id, the inputs fed from outside the document
            (``wfc try``'s parent entries); only the try passes any.

    Returns:
        One message per unwired required input, naming the node, its
        method, the input, the declared inputs and the fix.
    """
    wired: dict[str, set[str]] = {}
    for lnk in links:
        slot = lnk.get("target_slot") or lnk.get("targetHandle") or "data"
        wired.setdefault(str(lnk.get("target")), set()).add(str(slot))
    outside = fed_outside or {}

    errors: list[str] = []
    for node_id, (label, contract) in _node_contracts(nodes, contract_map).items():
        inputs = contract.get("input_slots")
        if not isinstance(inputs, dict):
            continue
        fed = wired.get(node_id, set()) | set(outside.get(node_id) or ())
        for name, spec in inputs.items():
            if name in fed or input_is_optional(spec):
                continue
            errors.append(
                f"Node '{node_id}': {label} declares input '{name}', which "
                f"nothing is connected to (its inputs: {_slot_list(inputs)}). "
                f"Connect a link to '{name}', or declare it `required: false` "
                f"in the method's method.yaml."
            )
    return errors


def link_slot_errors(nodes: list[dict], links: list[dict],
                     contract_map: dict) -> list[str]:
    """The link-slot rule, written once for both moments.

    A link that names a slot -- ``source_slot`` / ``sourceHandle`` on its
    source end, ``target_slot`` / ``targetHandle`` on its target end -- must
    name one the node's version declares: an output of the source method,
    an input of the target method. A draft node is read against its draft's
    contract (``versions["draft"]``). A link end naming no slot is not
    checked, nor is the source end of a selector or a reference (neither
    has a method contract). A node whose method the map does not hold, or
    whose version it cannot run, is left to the rules that refuse those.
    Structural validation and the load's legality stage both call this, so
    either moment refuses the same link with the same message.

    Args:
        nodes: The document's nodes; only method nodes are read.
        links: The document's links, in either form (the sparse canvas
            handles or the enriched slot keys).
        contract_map: Registration's contract map keyed ``module.method``.

    Returns:
        One message per offending link end, naming the node and the slot.
    """
    contracts = _node_contracts(nodes, contract_map)
    errors: list[str] = []
    for lnk in links:
        source, target = str(lnk.get("source")), str(lnk.get("target"))
        source_slot = lnk.get("source_slot") or lnk.get("sourceHandle")
        target_slot = lnk.get("target_slot") or lnk.get("targetHandle")
        if source_slot and source in contracts:
            label, contract = contracts[source]
            outputs = contract.get("output_slots")
            if isinstance(outputs, dict) and source_slot not in outputs:
                errors.append(
                    f"Node '{source}': the link to '{target}' names output "
                    f"'{source_slot}', which {label} does not declare (its "
                    f"outputs: {_slot_list(outputs)}). Link a declared output, "
                    f"or declare '{source_slot}' in the method's method.yaml."
                )
        if target_slot and target in contracts:
            label, contract = contracts[target]
            inputs = contract.get("input_slots")
            if isinstance(inputs, dict) and target_slot not in inputs:
                errors.append(
                    f"Node '{target}': the link from '{source}' names input "
                    f"'{target_slot}', which {label} does not declare (its "
                    f"inputs: {_slot_list(inputs)}). Link a declared input, "
                    f"or declare '{target_slot}' in the method's method.yaml."
                )
    return errors


def single_input_errors(file_counts: dict[tuple[str, str], int],
                        input_slots: dict[str, dict]) -> list[str]:
    """The single-input rule, written once for both moments.

    An input without ``multiple: true`` takes one file. More than one file
    into it -- several links, or a selector that bundles its samples -- is
    refused before the run, naming the node and the input. Structural
    validation and the load's legality stage both call this with their own
    view of the wiring.

    Args:
        file_counts: ``{(node id, slot): files reaching it}``
            (``input_file_counts``).
        input_slots: ``{node id: declared inputs}`` (``declared_input_slots``);
            a node or slot it does not declare is left to other rules.

    Returns:
        One message per offending input.
    """
    errors: list[str] = []
    for (node_id, slot), count in file_counts.items():
        if count < 2 or node_id not in input_slots:
            continue
        spec = input_slots[node_id].get(slot)
        if spec is None or input_takes_many(spec):
            continue
        errors.append(
            f"Input '{slot}' on node '{node_id}' takes one file, but {count} "
            f"files reach it (several links, or a selector that bundles its "
            f"samples). Declare `multiple: true` on the input in method.yaml "
            f"if the method collects several files, or wire one file into it."
        )
    return errors


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
      the engine would keep only one, so two documents with different
      first-selector bundles would compute the same cache key;
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
              "unknown methods, links to undeclared slots, a required input "
              "nothing is connected to (refused, as the load refuses it), a "
              "param_sets that is not a mapping, one edge per slot (reference "
              "fan-in exempt), fan-in shapes, one per-sample selector per "
              "method node, method roots")
def validate_structure(document: dict, contract_map: dict, *,
                       fed_outside: Mapping[str, Iterable[str]] | None = None,
                       ) -> dict[str, Any]:
    """Validate a pipeline graph's structure against a contract map.

    A required input nothing is connected to is an error
    (``unwired_input_errors``), in the words the load refuses it with.

    Args:
        document: The sparse canvas document -- ``nodes`` (``id``, ``type``,
            ``method``, ``module``, ``samples``, ``fan_mode``) and ``links``
            (``source``, ``target``, ``sourceHandle``, ``targetHandle``).
        contract_map: ``{"<module>.<method>": {"input_slots": ..., ...}}``
            for every registered method (``wfc.registration.load_contract_map``).
        fed_outside: Per node id, the inputs fed from outside the document
            (``wfc try``'s parent entries); they count as connected.

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

    # A param_sets that is not a mapping, in the words the load and
    # substitution refuse it with.
    refusal = param_sets_refusal(document)
    if refusal is not None:
        errors.append(refusal)

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
        # A node that cannot run a draft (unpinned on a DRAFT method, a
        # draft the method lacks, another folder's draft) is refused here
        # in Contracts' words, never resolved to the draft silently.
        if not node.get("version") or node.get("version") == DRAFT_PIN:
            refusal = version_refusal(node, info)
            if refusal is not None:
                errors.append(refusal)
                continue

    # A link naming a slot its node's version does not declare -- the rule
    # the load refuses it by for `wfc run-pipeline` (one rule, both moments).
    errors.extend(link_slot_errors(nodes, links, contract_map))

    # A required input nothing is connected to -- refused here and at the
    # load in the same words, before anything runs.
    errors.extend(unwired_input_errors(nodes, links, contract_map,
                                       fed_outside=fed_outside))

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
    edge_refused: set[tuple] = set()
    for (tgt, slot), sources in slot_edges.items():
        if all(s in reference_node_ids for s in sources):
            continue
        if len(sources) > 1:
            edge_refused.add((tgt, slot))
            slot_label = slot or "(default)"
            errors.append(
                f"Input slot '{slot_label}' on node '{tgt}' has "
                f"{len(sources)} incoming edges; only one edge per slot is "
                f"supported. Sources: {', '.join(sources)}."
            )

    # Single inputs: an input without `multiple: true` takes one file. A
    # reference fan-in or a fan-in selector into it is refused here, as the
    # load refuses it for `wfc run-pipeline` (the one rule, both moments).
    # A slot the one-edge rule already refused is not named twice.
    counts = {
        key: n for key, n in input_file_counts(
            nodes, links, slot_key="targetHandle", default_slot="").items()
        if key not in edge_refused
    }
    errors.extend(single_input_errors(counts, declared_input_slots(nodes, contract_map)))

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
