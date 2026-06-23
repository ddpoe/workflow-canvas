"""Synthesize literal-only lineage pipelines from a Run's ancestor chain.

This is Action 2 of the load-in-canvas cycle. Given a clicked run id, walk
``parentRunIds`` back to roots — through pipeline boundaries (D-2) — and
emit a flat literal-only pipeline JSON suitable for the canvas's
``loadPipeline()``.

Per D-8 the synthesizer is **DB-driven only**: it consumes the already-loaded
``WfcProvider._runs`` map (and its already-resolved ``bundledSamples`` field).
It does NOT read ``pipeline.json`` files from disk.

The 7-step algorithm follows SPEC §"Lineage synthesizer algorithm":

  1. Collect every Run reachable via ``parentRunIds`` (BFS, 1000-hop cap).
  2. Reconstruct each Run's literal params/method/sample/nid.
  3. Synthesize one canvas method node per Run.
  4. Synthesize one link per parent->child slot edge.
  5. For each root Run, prepend an ``input_selector`` head sourcing the sample.
  6. For each ``__all__`` Run, insert an ``input_selector(fan_mode='in')``
     above with the Run's ``bundledSamples`` list.
  7. Return a single-sample (or bundled-sample) pipeline JSON document.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional, Tuple


__all__ = ["LineageSynthesisError", "synthesize_lineage_pipeline"]


_HOP_CAP = 1000


class LineageSynthesisError(Exception):
    """Raised when the synthesizer cannot produce a coherent lineage pipeline.

    Cases:
      - Unknown ``run_id`` (caller should already have 404'd).
      - 1000-hop cap exceeded — typically a malformed cycle.
    """


def _new_node_id() -> str:
    """Return a synthetic ``node_<hex>`` id for a freshly-minted canvas node."""
    return f"node_{secrets.token_hex(4)}"


def _collect_ancestor_runs(
    runs_by_id: Dict[str, Any], start_run_id: str
) -> List[Any]:
    """BFS up ``parentRunIds`` from ``start_run_id``, returning runs in
    topological-ish order (clicked run first, then ancestors).

    Cycle defense: a visited-set + a hard 1000-hop cap. The cap fires for
    pathological data (degenerate cycles, runaway chains) and surfaces as
    ``LineageSynthesisError`` so the endpoint can return 422.
    """
    if start_run_id not in runs_by_id:
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
        run = runs_by_id.get(rid)
        if run is None:
            # Parent missing from the loaded run set — skip silently. Common
            # when ancestors were archived; the synthesizer treats the chain
            # as ending there rather than raising.
            continue
        visited[rid] = run
        order.append(rid)
        for parent_id in run.parentRunIds:
            if parent_id and parent_id not in visited:
                queue.append(parent_id)
        hops += 1
    return [visited[rid] for rid in order]


def _primary_output_slot(run: Any) -> Optional[str]:
    """Return the upstream Run's primary output slot name, or ``None`` if it
    cannot be authoritatively determined.

    ``run.outputs`` is keyed by ``run_outputs.output_name`` which is the
    artifact filename (``"merged.csv"``, ``"output.csv"``, …) — NOT the
    contract slot name (``"merged"``, ``"output"``). Returning the filename
    as a SvelteFlow ``sourceHandle`` produces handles that the canvas's
    ``CustomNode`` does not render (its source handles use contract slot
    names from ``methodDef.outputs``), so the edge silently drops.

    Returning ``None`` lets the canvas's ``loadPipeline`` pass a null
    handle through to SvelteFlow, which attaches the edge to the node's
    first source handle — the primary contract output. That matches the
    synthesizer's intent (most contracts have a single primary output)
    without requiring contract-level lookup here.
    """
    return None


def synthesize_lineage_pipeline(provider: Any, run_id: str) -> Dict[str, Any]:
    """Walk ``parentRunIds`` from ``run_id`` back to roots; return a flat
    literal-only pipeline JSON document for the canvas.

    Args:
        provider: A ``WfcProvider`` (or duck-typed object exposing
            ``_runs: Dict[str, WfcRun]``).
        run_id: The clicked run id — the lineage's terminal.

    Returns:
        A pipeline JSON dict with keys ``name``, ``nodes``, ``links``,
        ``samples``. Shape matches ``PipelineJSON`` so ``loadPipeline()``
        consumes it without transformation.

    Raises:
        LineageSynthesisError: Unknown ``run_id`` or hop-cap exceeded.
    """
    runs_by_id: Dict[str, Any] = getattr(provider, "_runs", {}) or {}
    if run_id not in runs_by_id:
        raise LineageSynthesisError(f"Run not found: {run_id}")

    collected = _collect_ancestor_runs(runs_by_id, run_id)
    clicked = runs_by_id[run_id]

    # Each Run gets one synthetic method node. Track id -> synthetic node id
    # so we can rewrite ``parents[*].sourceRunId`` into edge sources.
    node_id_for_run: Dict[str, str] = {r.id: _new_node_id() for r in collected}

    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

    # Emit one method node per Run with its literal params/method/module/nid.
    # Position is left to the canvas (loadPipeline assigns random positions
    # when ``position`` is absent — same as authored canvases).
    for run in collected:
        node_id = node_id_for_run[run.id]
        method_node: Dict[str, Any] = {
            "id": node_id,
            "type": "method",
            "method": run.method,
            "module": run.module,
            "params": dict(run.inputs or {}),
        }
        if run.nid:
            method_node["label"] = run.nid
        nodes.append(method_node)

    # Edges from ``parents`` rows. Each parent row gives (slot, sourceRunId);
    # we map the source run to its synthetic node id and the slot becomes
    # ``targetHandle``. ``sourceHandle`` defaults to the upstream's primary
    # output slot (rare contracts disambiguate further).
    #
    # Aggregator collapse (SPEC step 6): for runs with sample == "__all__"
    # AND bundledSamples populated AND at least one in-set parent, insert
    # an ``input_selector(fan_mode="in")`` between the per-sample parents
    # and the aggregator. The selector carries bundledSamples; the per-sample
    # parents wire into it instead of directly into the aggregator.
    aggregator_selector_for: Dict[str, str] = {}
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
            (runs_by_id.get(p["sourceRunId"]) is not None
             and runs_by_id[p["sourceRunId"]].dataSource != "__all__")
            for p in in_set_parents
        )
        is_collapsed_aggregator = (
            run.dataSource == "__all__"
            and bool(run.bundledSamples)
            and bool(in_set_parents)
            and has_per_sample_parent
        )
        if is_collapsed_aggregator:
            sel_id = _new_node_id()
            aggregator_selector_for[run.id] = sel_id
            nodes.append(
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
                src_run = runs_by_id.get(src_run_id)
                links.append(
                    {
                        "source": node_id_for_run[src_run_id],
                        "target": sel_id,
                        "sourceHandle": _primary_output_slot(src_run),
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
            src_run = runs_by_id.get(src_run_id)
            links.append(
                {
                    "source": node_id_for_run[src_run_id],
                    "target": node_id_for_run[run.id],
                    "sourceHandle": _primary_output_slot(src_run),
                    "targetHandle": slot,
                }
            )

    # Roots: any collected Run with no in-set parents needs an
    # ``input_selector`` head feeding its first input slot.
    in_set = set(node_id_for_run.keys())
    for run in collected:
        in_set_parents = [
            p for p in (run.parents or []) if p.get("sourceRunId") in in_set
        ]
        if in_set_parents:
            continue
        # Aggregator collapse: a __all__ run becomes the head AND emits a
        # fan_mode='in' selector with bundledSamples. Per-sample roots
        # emit a fan_mode='out' (default) selector with the run's sample.
        is_aggregator = run.dataSource == "__all__" and bool(run.bundledSamples)
        selector_id = _new_node_id()
        if is_aggregator:
            selector_node = {
                "id": selector_id,
                "type": "input_selector",
                "method": "",
                "params": {},
                "fan_mode": "in",
                "samples": list(run.bundledSamples),
            }
        else:
            sample = run.dataSource or ""
            selector_node = {
                "id": selector_id,
                "type": "input_selector",
                "method": "",
                "params": {},
                "samples": [sample] if sample else [],
            }
        nodes.append(selector_node)

        # Wire the selector's output into the root Run's first declared
        # input slot. ``None`` (when run.parents is empty) lets SvelteFlow
        # attach to the node's first input handle — the primary slot —
        # rather than picking a guessed name like "data" that may not match
        # the method's contract (e.g. ``merge_dec`` has slot ``sources``).
        target_slot = run.parents[0].get("slot") if run.parents else None
        links.append(
            {
                "source": selector_id,
                "target": node_id_for_run[run.id],
                "sourceHandle": "output",
                "targetHandle": target_slot,
            }
        )

    # Top-level samples: clicked run's sample, or its bundledSamples list
    # if the clicked run is an aggregator.
    if clicked.dataSource == "__all__" and clicked.bundledSamples:
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
