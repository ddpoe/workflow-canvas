"""Lineage synthesis: a run's ancestry as a literal-only pipeline document.

Given the loaded run records and a clicked run, synthesis follows input edges
back to the roots, across pipeline boundaries, and returns a flat pipeline
document that the canvas's ``loadPipeline()`` consumes without transformation.
It reads only the records it is handed: no database, and no ``pipeline.json``.

:func:`synthesize_lineage_pipeline` runs four phases:

1. **Collect** the clicked run and every run reachable over input edges,
   breadth-first, refusing past 1000 hops.
2. **Mint** one method node per collected run, carrying its literal method,
   module, params and NID label.
3. **Wire** one link per input edge inside the collected set. A per-sample to
   ``__all__`` boundary collapses into one fan-in selector carrying the bundled
   samples. Each run that recorded sample reads is fed from an input-selector
   head into each slot it recorded, one shared head per sample (a collapsed
   run's own fan-in head for its bundle). A run that recorded no reads is fed
   only if it is a root, into its first input slot.
4. **Emit** the document, with the clicked run's samples.

Synthesis follows input edges only. It does not wire a reuse edge, which would
place each reused step in the pipeline twice.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Mapping, Protocol, Sequence, Tuple

from axiom_annotations import AutoStep, task, workflow

from wfc.contracts import COLLAPSED_SAMPLE
from wfc.lineage.relation import RunRecord

__all__ = [
    "LineageSynthesisError",
    "SynthesisRecord",
    "synthesize_lineage_pipeline",
]


_HOP_CAP = 1000


class SynthesisRecord(RunRecord, Protocol):
    """A run record with the fields synthesis copies into the document.

    Attributes:
        parents: One ``{"slot": ..., "sourceRunId": ..., "sourceSlot": ...}``
            mapping per input edge, in slot order. ``sourceSlot`` is the
            source run's output slot the input consumed, or None when the
            input record names none.
        method: The method's name.
        module: The method's module.
        inputs: The run's literal params.
        nid: The node's NID label, or an empty string.
        dataSource: The run's sample, or ``__all__`` for a collapsed run.
        bundledSamples: The samples a collapsed run bundled.
        sampleInputs: One ``{"slot": ..., "sample": ...}`` mapping per sample
            the run recorded reading, or empty for a run that recorded none.
    """

    parents: Sequence[Mapping[str, Any]]
    method: str
    module: str
    inputs: Mapping[str, Any]
    nid: str
    dataSource: str
    bundledSamples: Sequence[str]
    sampleInputs: Sequence[Mapping[str, str]]


class LineageSynthesisError(Exception):
    """Raised when synthesis cannot produce a coherent lineage pipeline.

    Cases:
      - Unknown ``run_id`` (the caller should already have answered 404).
      - The 1000-hop cap exceeded, typically a malformed cycle.
    """


def _new_node_id() -> str:
    """Return a synthetic ``node_<hex>`` id for a freshly-minted canvas node."""
    return f"node_{secrets.token_hex(4)}"


@task(purpose="Collect the clicked run and every run reachable over its input "
              "edges, breadth-first, under a 1000-hop cap",
      inputs="records: the loaded run records by id; start_run_id: the clicked run",
      outputs="the collected records, the clicked run first, then its ancestors")
def _collect_ancestor_runs(
    records: Mapping[str, SynthesisRecord], start_run_id: str
) -> List[SynthesisRecord]:
    """Walk input edges up from ``start_run_id``, breadth-first.

    Cycle defense: a visited set and a hard 1000-hop cap. The cap fires for
    pathological data (degenerate cycles, runaway chains) and surfaces as
    ``LineageSynthesisError`` so the endpoint can return 422.

    Args:
        records: The loaded run records, by id.
        start_run_id: The clicked run.

    Returns:
        The collected records, the clicked run first, then its ancestors.

    Raises:
        LineageSynthesisError: An unknown ``start_run_id``, or the hop cap
            exceeded.
    """
    if start_run_id not in records:
        raise LineageSynthesisError(f"Run not found: {start_run_id}")
    visited: Dict[str, Any] = {}
    order: List[str] = []
    queue: List[str] = [start_run_id]
    hops = 0
    while queue:
        if hops > _HOP_CAP:
            raise LineageSynthesisError(
                f"Lineage walk exceeded {_HOP_CAP} hops; ancestor chain malformed"
            )
        rid = queue.pop(0)
        if rid in visited:
            continue
        run = records.get(rid)
        if run is None:
            # Parent missing from the loaded run set — skip silently. Common
            # when ancestors were archived; the chain ends there rather than
            # raising.
            continue
        visited[rid] = run
        order.append(rid)
        for parent_id in run.parentRunIds:
            if parent_id and parent_id not in visited:
                queue.append(parent_id)
        hops += 1
    return [visited[rid] for rid in order]


@task(purpose="Mint one method node per collected run, carrying its literal "
              "method, module, params and NID label",
      inputs="the collected records",
      outputs="each run's synthetic node id, and the method nodes in "
              "collection order")
def _mint_nodes(
    collected: Sequence[SynthesisRecord],
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Mint one method node per collected run.

    Position is left to the canvas: ``loadPipeline`` places a node that has no
    ``position``, as it does for an authored canvas.

    Args:
        collected: The collected records.

    Returns:
        The synthetic node id of each run, by run id, and the method nodes in
        collection order.
    """
    node_id_for_run: Dict[str, str] = {r.id: _new_node_id() for r in collected}
    nodes: List[Dict[str, Any]] = []
    for run in collected:
        method_node: Dict[str, Any] = {
            "id": node_id_for_run[run.id],
            "type": "method",
            "method": run.method,
            "module": run.module,
            "params": dict(run.inputs or {}),
        }
        if run.nid:
            method_node["label"] = run.nid
        nodes.append(method_node)
    return node_id_for_run, nodes


@task(purpose="Wire one link per input edge inside the collected set, collapse "
              "a per-sample to __all__ boundary into one fan-in selector, feed "
              "each recorded sample read from its sample's shared head into the "
              "recorded slot, and feed a root with no recorded reads from a "
              "head into its first input slot",
      inputs="the records, the collected records, and each run's synthetic "
             "node id",
      outputs="the selector nodes, and the links")
def _wire_links(
    records: Mapping[str, SynthesisRecord],
    collected: Sequence[SynthesisRecord],
    node_id_for_run: Mapping[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Wire the collected runs into links, adding the selector nodes they need.

    Each input-edge row gives a slot, a source run and the source run's
    output slot the input consumed. The source run maps to its synthetic node
    id, the slot becomes ``targetHandle``, and the recorded source slot
    becomes ``sourceHandle`` (left unset when the input record names none).

    Aggregator collapse: a run whose sample is ``__all__``, with bundled samples
    and at least one per-sample parent inside the collected set, gets an
    ``input_selector(fan_mode="in")`` between those parents and itself. The
    selector carries the bundled samples, and the per-sample parents wire into
    it instead of into the aggregator.

    Recorded sample reads: a collected run with ``sampleInputs`` gets one
    link per distinct recorded slot, from an ``input_selector`` head into
    that slot, whether it is a root or reads its sample beside an in-set
    parent. Every run that reads one sample shares that sample's head, so
    the document has one input per sample. A collapsed run's bundle rows
    feed its own fan-in head, carrying ``bundledSamples``, into the bundle
    slot.

    No recorded reads: a run recorded before sample rows existed is fed only
    if it is a root (no parent inside the collected set), from its sample's
    shared head, or its own fan-in head when collapsed, into its first input
    slot, or into no named slot when it has no parents. A run with recorded
    reads never also gets this link.

    Args:
        records: The loaded run records, by id.
        collected: The collected records.
        node_id_for_run: The synthetic node id of each collected run.

    Returns:
        The selector nodes, in the order they were minted, and the links.
    """
    selectors: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

    for run in collected:
        in_set_parents = [
            p for p in (run.parents or []) if p.get("sourceRunId") in node_id_for_run
        ]
        # Aggregator collapse is the per-sample → ``__all__`` boundary: it
        # bundles many per-sample parent runs into one fan-in selector that
        # feeds the ``__all__`` aggregator. When parents are themselves
        # ``__all__`` (a linear chain of bundled runs), the fan-in already
        # happened upstream — emit normal pass-through edges so we don't
        # insert a redundant selector at every link, which leaves the
        # canvas with edges targeting input_selector nodes that have no
        # input handle (SvelteFlow silently drops them).
        has_per_sample_parent = any(
            (records.get(p["sourceRunId"]) is not None
             and records[p["sourceRunId"]].dataSource != COLLAPSED_SAMPLE)
            for p in in_set_parents
        )
        is_collapsed_aggregator = (
            run.dataSource == COLLAPSED_SAMPLE
            and bool(run.bundledSamples)
            and bool(in_set_parents)
            and has_per_sample_parent
        )
        if is_collapsed_aggregator:
            sel_id = _new_node_id()
            selectors.append(
                {
                    "id": sel_id,
                    "type": "input_selector",
                    "method": "",
                    "params": {},
                    "fan_mode": "in",
                    "samples": list(run.bundledSamples),
                }
            )
            # Per-sample parents → fan-in selector
            for parent_row in in_set_parents:
                src_run_id = parent_row["sourceRunId"]
                links.append(
                    {
                        "source": node_id_for_run[src_run_id],
                        "target": sel_id,
                        "sourceHandle": parent_row.get("sourceSlot"),
                        "targetHandle": None,
                    }
                )
            # fan-in selector → aggregator
            target_slot = in_set_parents[0].get("slot")
            links.append(
                {
                    "source": sel_id,
                    "target": node_id_for_run[run.id],
                    "sourceHandle": "output",
                    "targetHandle": target_slot,
                }
            )
            continue

        for parent_row in run.parents or []:
            src_run_id = parent_row.get("sourceRunId")
            slot = parent_row.get("slot")
            if not src_run_id or src_run_id not in node_id_for_run:
                # Parent outside the collected set (cap hit, archived,
                # or missing). Skip the edge — the downstream node still
                # renders, just with one fewer wire.
                continue
            links.append(
                {
                    "source": node_id_for_run[src_run_id],
                    "target": node_id_for_run[run.id],
                    "sourceHandle": parent_row.get("sourceSlot"),
                    "targetHandle": slot,
                }
            )

    in_set = set(node_id_for_run.keys())
    # One head per sample, shared by every run that reads it in any role:
    # one input per sample on the canvas, as the pipeline was authored.
    selector_for_sample: Dict[str, str] = {}

    def sample_head(sample: str) -> str:
        """Return the sample's shared head, minting it on first use."""
        if sample not in selector_for_sample:
            selector_for_sample[sample] = _new_node_id()
            selectors.append({
                "id": selector_for_sample[sample],
                "type": "input_selector",
                "method": "",
                "params": {},
                "samples": [sample] if sample else [],
            })
        return selector_for_sample[sample]

    def fan_in_head(run: SynthesisRecord) -> str:
        """Mint a collapsed run's own fan-in head, carrying its bundle."""
        selector_id = _new_node_id()
        selectors.append({
            "id": selector_id,
            "type": "input_selector",
            "method": "",
            "params": {},
            "fan_mode": "in",
            "samples": list(run.bundledSamples),
        })
        return selector_id

    for run in collected:
        is_aggregator = run.dataSource == COLLAPSED_SAMPLE and bool(run.bundledSamples)
        recorded = list(run.sampleInputs or [])
        if recorded:
            # Wired only from what the run recorded reading, one link per
            # distinct slot. A bundle's rows share one slot, so they make
            # one link, from the run's fan-in head.
            sample_on_slot: Dict[Any, str] = {}
            for row in recorded:
                sample_on_slot.setdefault(row.get("slot"), row.get("sample") or "")
            bundle_head = fan_in_head(run) if is_aggregator else None
            for slot, sample in sample_on_slot.items():
                links.append(
                    {
                        "source": bundle_head or sample_head(sample),
                        "target": node_id_for_run[run.id],
                        "sourceHandle": "output",
                        "targetHandle": slot,
                    }
                )
            continue

        # No recorded reads (a run recorded before sample rows existed):
        # only a root is fed, from a head into its first input slot.
        if any(p.get("sourceRunId") in in_set for p in (run.parents or [])):
            continue
        selector_id = (
            fan_in_head(run) if is_aggregator else sample_head(run.dataSource or "")
        )
        # ``None`` (when run.parents is empty) lets SvelteFlow attach to the
        # node's first input handle, the primary slot, rather than a guessed
        # name like "data" that may not match the method's contract (e.g.
        # ``merge_dec`` has slot ``sources``).
        target_slot = run.parents[0].get("slot") if run.parents else None
        links.append(
            {
                "source": selector_id,
                "target": node_id_for_run[run.id],
                "sourceHandle": "output",
                "targetHandle": target_slot,
            }
        )

    return selectors, links


@task(purpose="Emit the pipeline document, with the clicked run's sample or "
              "its bundled samples",
      inputs="the clicked record, its id, the nodes and the links",
      outputs="a pipeline document with name, nodes, links and samples")
def _emit_document(
    clicked: SynthesisRecord,
    run_id: str,
    nodes: List[Dict[str, Any]],
    links: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the pipeline document.

    The document's samples are the clicked run's bundled samples when it is an
    aggregator, else its sample.

    Args:
        clicked: The clicked run's record.
        run_id: The clicked run's id.
        nodes: The method nodes, then the selector nodes.
        links: The links.

    Returns:
        A pipeline document with keys ``name``, ``nodes``, ``links`` and
        ``samples``.
    """
    if clicked.dataSource == COLLAPSED_SAMPLE and clicked.bundledSamples:
        samples = list(clicked.bundledSamples)
    elif clicked.dataSource:
        samples = [clicked.dataSource]
    else:
        samples = []

    return {
        "name": f"lineage_{run_id}",
        "nodes": nodes,
        "links": links,
        "samples": samples,
    }


@workflow(
    purpose="Synthesize a literal-only pipeline document from a run's ancestry "
            "over input edges",
    inputs="records: the loaded run records by id; run_id: the clicked run",
    outputs="a pipeline document with name, nodes, links and samples",
)
def synthesize_lineage_pipeline(
    records: Mapping[str, SynthesisRecord], run_id: str
) -> Dict[str, Any]:
    """Synthesize the lineage pipeline that ends at ``run_id``.

    Args:
        records: The loaded run records, by id.
        run_id: The clicked run, the lineage's terminal.

    Returns:
        A pipeline document with keys ``name``, ``nodes``, ``links`` and
        ``samples``, shaped as ``PipelineJSON`` so ``loadPipeline()`` consumes
        it without transformation.

    Raises:
        LineageSynthesisError: An unknown ``run_id``, or the hop cap exceeded.
    """
    口 = AutoStep(step_num=1, name="Collect ancestors")
    collected = _collect_ancestor_runs(records, run_id)

    口 = AutoStep(step_num=2, name="Mint nodes")
    node_id_for_run, method_nodes = _mint_nodes(collected)

    口 = AutoStep(step_num=3, name="Wire links")
    selector_nodes, links = _wire_links(records, collected, node_id_for_run)

    口 = AutoStep(step_num=4, name="Emit the document")
    document = _emit_document(
        records[run_id], run_id, method_nodes + selector_nodes, links
    )
    return document
