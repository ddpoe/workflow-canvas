"""The load: a pipeline document to a ``PipelineDef``, on values alone.

``load_pipeline(document, *, contract_map, reference_outputs)`` composes
the stages in order -- parse (the document's
nodes, links and samples into steps, remembering the system-node facts),
bind references (the referenced runs' outputs and sample, handed in), order
and legality, collapse propagation, the column cross-check moment (the
contract map, handed in) -- and refuses a definition with steps but no
samples. Nothing here opens a database or reads a file: the caller reads
the document, resolves the referenced runs and fetches the map
(``wfc.execution.load_pipeline_from_path`` is the one composer on the
execution path).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePath

from axiom_annotations import AutoStep, Step, task

from ..contracts import COLLAPSED_SAMPLE, cross_check_columns
from .model import PipelineDef, StepDef
from .order import topo_sort_steps
from .validation import fan_in_upstream_errors, second_selector_errors

logger = logging.getLogger(__name__)


@dataclass
class ParsedDocument:
    """What the parse stage remembers for the stages after it.

    ``steps`` and ``samples`` are the definition in progress (binding
    appends the referenced runs' samples and injects their artifacts; the
    collapse stage marks steps). The rest are the system-node facts the
    later stages need: which raw ids are system nodes, the references the
    document declares, the links out of them, the fan-in selectors and
    which method node each one feeds directly.
    """
    steps: list[StepDef]
    samples: list[str]
    param_sets: dict[str, dict[str, dict]]
    explicit_combos: list[dict[str, str]] | None
    nodes: list[dict]  # method nodes, document order (parallel to ``steps``)
    has_duplicate_methods: bool
    system_node_ids: set[str] = field(default_factory=set)
    reference_nodes: dict[str, dict] = field(default_factory=dict)
    # ref raw id -> {run_id, label} as the document says
    reference_links: list[dict] = field(default_factory=list)
    # links whose source is a run_reference node, document order
    fan_in_selectors: dict[str, list[str]] = field(default_factory=dict)
    # selector raw id -> bundled samples, for selectors with fan_mode="in"
    fan_in_direct_upstream: dict[str, tuple[str, str]] = field(default_factory=dict)
    # method raw id -> (selector raw id, target slot) for direct fan-in consumers
    per_sample_selector_upstream: dict[str, list[str]] = field(default_factory=dict)
    # method raw id -> distinct per-sample selector raw ids wired into it, link order
    fan_in_selector_upstream: dict[str, list[str]] = field(default_factory=dict)
    # method raw id -> distinct fan-in selector raw ids wired into it, link order
    upstream_link_counts: dict[str, int] = field(default_factory=dict)
    # method raw id -> incoming link count, every upstream kind, before filtering


def reference_nodes(document: dict) -> dict[str, dict]:
    """The references a document declares, keyed by node id.

    A reference is the run it names: ``run_id``, plus the node's canvas
    ``label`` for messages. Which output of that run feeds a consumer is
    each link's own ``source_slot``, resolved in ``bind_references``.

    Args:
        document: The pipeline document.

    Returns:
        ``{node id: {"run_id", "label"}}`` in document order.
    """
    out: dict[str, dict] = {}
    for n in document["nodes"]:
        if n.get("type", "method") == "run_reference":
            out[str(n["id"])] = {
                "run_id": n.get("run_id", ""),
                "label": n.get("label", ""),
            }
    return out


@dataclass(frozen=True)
class InboundLink:
    """One link feeding a node's input slot, as the document declares it.

    Attributes:
        target_slot: The fed input slot (``"data"`` when the link names none).
        source_id: The source node's canonical id -- a legacy numeric id
            resolves to its method name, as the executor keys sidecars.
        raw_source_id: The source node's id as written in the document.
        source_kind: The source node's ``type`` (``"method"`` when absent).
        source_slot: The source output slot the link names, or ``None``.
        run_id: For a ``run_reference`` source, the run id the node names
            (``""`` when it names none -- reported, not decided); ``""``
            for every other kind.
        source_node: The source node's document entry.
    """

    target_slot: str
    source_id: str
    raw_source_id: str
    source_kind: str
    source_slot: str | None
    run_id: str
    source_node: dict


def document_node(document: dict, node_id: str) -> dict | None:
    """The document node a node id names, under the executor's identity rule.

    ``node_id`` is matched against each node's own id first (as a string);
    failing that, against the method names of the method nodes, which is how
    a legacy numeric-id document names its nodes. A node's raw document id
    is the returned entry's ``id``.

    Args:
        document: The pipeline document.
        node_id: A node's own id or, for a legacy document, its method name.

    Returns:
        The node's document entry, or ``None`` when no node answers to it.
    """
    nodes = document.get("nodes", [])
    nodes_by_id = {str(n["id"]): n for n in nodes}
    nodes_by_method = {n["method"]: n for n in nodes if n.get("method")}
    return nodes_by_id.get(node_id) or nodes_by_method.get(node_id)


def inbound_wiring(document: dict, node_id: str) -> list[InboundLink]:
    """The links feeding a node's input slots, in document order.

    Pure over the document: no run, no database. Node identity honours both
    spellings the executor uses -- ``node_id`` may be the node's own id
    (canonical or raw) or, for a legacy numeric-id document, its method
    name. A link whose source or target is not a node of the document is
    ignored.

    Args:
        document: The pipeline document.
        node_id: The fed node, by id or (legacy) by method name.

    Returns:
        One ``InboundLink`` per link into the node.
    """
    nodes_by_id = {str(n["id"]): n for n in document.get("nodes", [])}
    current = document_node(document, node_id) or {}
    resolved_raw_id = str(current.get("id", node_id))

    def _canonical(raw: str, node: dict) -> str:
        if raw.isdigit() and node.get("method"):
            return str(node["method"])
        return raw

    out: list[InboundLink] = []
    for link in document.get("links", []):
        tgt = str(link.get("target", ""))
        src = str(link.get("source", ""))
        tgt_node = nodes_by_id.get(tgt)
        src_node = nodes_by_id.get(src)
        if tgt_node is None or src_node is None:
            continue
        if node_id not in (tgt, _canonical(tgt, tgt_node)) and tgt != resolved_raw_id:
            continue
        kind = src_node.get("type", "method")
        run_id = str(src_node.get("run_id") or "").strip() if kind == "run_reference" else ""
        out.append(InboundLink(
            target_slot=link.get("target_slot", "data"),
            source_id=_canonical(src, src_node),
            raw_source_id=src,
            source_kind=kind,
            source_slot=link.get("source_slot"),
            run_id=run_id,
            source_node=src_node,
        ))
    return out


def selector_slot(document: dict, node_id: str) -> str | None:
    """The input slot a node reads its sample(s) into, if any.

    The one answer to "does this node read a sample": an ``input_selector``
    wired into the node feeds the slot the link names, whatever else feeds
    the node -- method parents and references do not suppress it. For a
    per-sample selector the slot receives the target's own sample; for a
    fan-in selector it receives the bundle. Pure over the document, so the
    classifier the claim and materialize ask and the value the load stamps
    on the step for the emitter cannot disagree.

    Args:
        document: The pipeline document.
        node_id: The fed node, by id or (legacy) by method name.

    Returns:
        The target slot of the first selector link into the node (``"data"``
        when the link names none), or ``None`` when no selector feeds it.
    """
    for wire in inbound_wiring(document, node_id):
        if wire.source_kind == "input_selector":
            return wire.target_slot or "data"
    return None


# =============================================================================
# Stage: parse
# =============================================================================

@task(purpose="Parse a pipeline document's nodes, links and samples into step "
              "definitions, remembering the system-node facts the later "
              "stages need")
def parse_document(document: dict) -> ParsedDocument:
    """Parse the document into steps plus the remembered system-node facts.

    The JSON uses a graph-native format with ``nodes`` and ``links``
    (compatible with LiteGraph.js exports).  ``depends_on`` is derived
    from the links rather than being stored redundantly.

    Links may carry an optional ``target_slot`` field (default ``"data"``)
    that specifies which named input slot the upstream feeds into.
    ``StepDef.inputs`` is populated from these slots.

    Minimal example::

        {
          "nodes": [
            {"id": 1, "method": "preprocess",
             "script": "methods/preprocess/preprocess.py",
             "params": {"normalize": true}},
            {"id": 2, "method": "filter_cells",
             "script": "methods/filter_cells/filter_cells.py",
             "params": {"min_quality": 0.5}}
          ],
          "links": [
            {"source": 1, "target": 2}
          ],
          "samples": ["Pa16c"]
        }

    Optional keys: ``param_sets``, ``explicit_combos``, ``module``
    (per-node), ``position`` (per-node, preserved for canvas roundtrip).

    Args:
        document: The pipeline document.

    Returns:
        The parsed document: steps, samples and the system-node facts.

    Raises:
        KeyError: If required fields are missing.
        ValueError: A method node with no incoming edge in a document that
            has system nodes; a link naming an unknown node; a method node
            declaring no env.
    """
    口 = Step(step_num=1, name="Parse the document",
             purpose="Read nodes, links, samples, and param_sets; separate "
                     "system nodes from method nodes")
    raw = document

    all_nodes = raw["nodes"]
    links = raw.get("links", [])
    samples = list(raw.get("samples", []))
    param_sets = raw.get("param_sets", {})
    explicit_combos = raw.get("explicit_combos", None)

    # ── System node extraction ──────────────────────────────────────────────
    # Separate system nodes (input_selector, run_reference) from method nodes.
    # System nodes do not become StepDefs — they are data sources resolved
    # before Snakemake execution.
    system_node_ids: set[str] = set()
    run_reference_nodes: dict[str, dict] = {}  # node_id → {run_id, label}
    # Map of input_selector raw_id → bundled sample list, for selectors with
    # fan_mode="in". Used below to mark downstream steps as sample_collapsed.
    fan_in_selectors: dict[str, list[str]] = {}
    for n in all_nodes:
        node_type = n.get("type", "method")
        nid = str(n["id"])
        if node_type == "input_selector":
            system_node_ids.add(nid)
            sel_samples = list(n.get("samples", []))
            # Merge selected samples into the pipeline sample list
            for s in sel_samples:
                if s not in samples:
                    samples.append(s)
            if n.get("fan_mode", "out") == "in":
                fan_in_selectors[nid] = sel_samples
        elif node_type == "run_reference":
            system_node_ids.add(nid)
            run_reference_nodes[nid] = {
                "run_id": n.get("run_id", ""),
                "label": n.get("label", ""),
            }

    # Capture links from run_reference nodes → downstream method nodes
    # before filtering them out of the main link list.
    run_ref_links: list[dict] = []  # [{source: ref_id, target: method_id, ...}]
    for lnk in links:
        src_id = str(lnk["source"])
        if src_id in run_reference_nodes:
            run_ref_links.append(lnk)

    # Filter to method nodes only for step construction
    nodes = [n for n in all_nodes if str(n["id"]) not in system_node_ids]

    # Enforce: when the pipeline contains system nodes, every method node must
    # have at least one incoming edge in the original links (from any source,
    # including system nodes).  A method node with no incoming edges is an
    # invalid root -- the pipeline should use an input_selector or
    # run_reference system node as the root.  Pipelines without system nodes
    # are legacy/standalone and skip this check.
    if system_node_ids:
        original_targets = {str(lnk["target"]) for lnk in links}
        for n in nodes:
            nid = str(n["id"])
            if nid not in original_targets:
                raise ValueError(
                    f"Method node '{nid}' (method={n['method']}) has no incoming "
                    f"edges and cannot be a pipeline root. Add an input_selector "
                    f"or run_reference system node upstream of this method node."
                )

    # Capture direct fan-in upstreams (method_raw_id → selector_raw_id) before
    # filtering selector→method links. Each method that consumes a fan-in
    # selector directly gets marked sample_collapsed below; downstream steps
    # inherit collapse transitively.
    # target_raw → (selector_raw, target_slot). Carrying the slot here is
    # essential: the selector→method link is filtered out of slot_map below
    # (source is a system node), so without capturing it now we'd lose the
    # fan-in slot name and default to "data" downstream.
    #
    # ``fan_in_selector_upstream`` and ``upstream_link_counts`` are the
    # legality stage's view of the same wiring: every fan-in selector a node
    # consumes (not only the last one to arrive) and how many incoming links
    # it has in total. ``check_legality`` refuses a second fan-in selector
    # before anything downstream reads ``fan_in_direct_upstream``, which is
    # why that dict can stay single-valued.
    fan_in_direct_upstream: dict[str, tuple[str, str]] = {}
    fan_in_selector_upstream: dict[str, list[str]] = {}
    upstream_link_counts: dict[str, int] = {}
    for lnk in links:
        tgt_raw = str(lnk["target"])
        upstream_link_counts[tgt_raw] = upstream_link_counts.get(tgt_raw, 0) + 1
    if fan_in_selectors:
        for lnk in links:
            src_raw = str(lnk["source"])
            tgt_raw = str(lnk["target"])
            if src_raw in fan_in_selectors:
                target_slot = lnk.get("target_slot", "data")
                fan_in_direct_upstream[tgt_raw] = (src_raw, target_slot)
                feeds = fan_in_selector_upstream.setdefault(tgt_raw, [])
                if src_raw not in feeds:
                    feeds.append(src_raw)

    # Remember which per-sample selectors feed each method node (distinct,
    # link order) for the legality stage's second-selector rule, before
    # the selector links are filtered out below.
    per_sample_selector_upstream: dict[str, list[str]] = {}
    for lnk in links:
        src_raw = str(lnk["source"])
        if src_raw in system_node_ids and src_raw not in run_reference_nodes \
                and src_raw not in fan_in_selectors:
            feeds = per_sample_selector_upstream.setdefault(str(lnk["target"]), [])
            if src_raw not in feeds:
                feeds.append(src_raw)

    # Filter links: remove links where source is a system node
    # (run_reference links are handled separately via run_ref_inputs)
    links = [
        lnk for lnk in links
        if str(lnk["source"]) not in system_node_ids
    ]

    口 = Step(step_num=2, name="Resolve link dependencies",
             purpose="Loop through each link to map which nodes must run before which")
    # Build node lookup and adjacency (target_node_id → list of source_node_ids)
    node_map: dict[str, dict] = {str(n["id"]): n for n in nodes}
    deps: dict[str, list[str]] = defaultdict(list)
    # slot_map: target_raw_id → {slot → [source_raw_ids]}
    slot_map: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    source_slot_map: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for link in links:
        口 = Step(step_num=2.1, name="Validate node references",
                 purpose="Raise if source or target node ID doesn't exist")
        src_id = str(link["source"])
        tgt_id = str(link["target"])
        if src_id not in node_map:
            raise ValueError(f"Link references unknown source node {src_id}")
        if tgt_id not in node_map:
            raise ValueError(f"Link references unknown target node {tgt_id}")

        口 = Step(step_num=2.2, name="Record dependency",
                 purpose="Add source node as an upstream requirement of the target node")
        if src_id not in deps[tgt_id]:
            deps[tgt_id].append(src_id)

        # Parse target_slot (default "data") and optional source_slot
        slot = link.get("target_slot", "data")
        source_slot = link.get("source_slot", None)
        if src_id not in slot_map[tgt_id][slot]:
            slot_map[tgt_id][slot].append(src_id)
            source_slot_map[tgt_id][slot].append(source_slot)

    口 = Step(step_num=3, name="Build step definitions",
             purpose="Convert each node into a runnable step with its upstream node requirements")
    # Build StepDefs in node order
    # For legacy pipelines (int IDs where each method is unique),
    # node_id defaults to method_name.
    seen_methods: set[str] = set()
    has_duplicate_methods = False
    for n in nodes:
        m = n["method"]
        if m in seen_methods:
            has_duplicate_methods = True
            break
        seen_methods.add(m)

    steps = []
    for n in nodes:
        method = n["method"]
        module = n["module"]
        raw_id = str(n["id"])

        # Choose node_id: use raw_id if methods repeat or ID is a
        # human-readable string; otherwise fall back to method_name.
        if has_duplicate_methods or not raw_id.isdigit():
            nid = raw_id
        else:
            nid = method  # legacy compat: node_id == method_name

        # Resolve depends_on for this node_id
        raw_deps = deps.get(raw_id, [])
        # Map raw source IDs to their resolved node_ids
        resolved_deps: list[str] = []
        for src_raw in raw_deps:
            src_node = node_map[src_raw]
            src_method = src_node["method"]
            if has_duplicate_methods or not src_raw.isdigit():
                resolved_deps.append(src_raw)
            else:
                resolved_deps.append(src_method)

        # Build inputs dict from slot_map (slot → [resolved upstream node_ids])
        raw_slots = slot_map.get(raw_id, {})
        inputs: dict[str, list[str]] = {}
        input_source_slots_for_step: dict[str, list] = {}
        for slot, src_raw_ids in raw_slots.items():
            resolved_slot_deps: list[str] = []
            for sr in src_raw_ids:
                src_node = node_map[sr]
                src_method = src_node["method"]
                if has_duplicate_methods or not sr.isdigit():
                    resolved_slot_deps.append(sr)
                else:
                    resolved_slot_deps.append(src_method)
            inputs[slot] = resolved_slot_deps
            # Mirror source_slot list (same length as upstream_id list)
            input_source_slots_for_step[slot] = list(
                source_slot_map.get(raw_id, {}).get(slot, [None] * len(src_raw_ids))
            )

        # env is required (missing-value guard, not a backend allow-list):
        # prefer an explicit ``env``, fall back to the legacy ``env_strategy``
        # alias, and raise when NEITHER is present/non-empty. A truthy
        # ``container:<name>`` / bare manifest name passes through untouched.
        step_env = n.get("env") or n.get("env_strategy")
        if not step_env:
            raise ValueError(
                f"env required (pixi/conda/byo): node '{raw_id}' "
                f"(method={method}) declares no env"
            )

        steps.append(StepDef(
            method_name=method,
            module_name=module,
            script_path=n.get("script", f"methods/{method}/{method}.py"),
            params=n.get("params", {}),
            depends_on=resolved_deps,
            output_ext=n.get("output_ext", ".parquet"),
            node_id=nid,
            inputs=inputs,
            env=step_env,
            slot_outputs=n.get("slot_outputs", {}),
            slot_types=n.get("slot_types", {}),
            input_source_slots=input_source_slots_for_step,
            selector_slot=selector_slot(document, raw_id),
        ))

    return ParsedDocument(
        steps=steps,
        samples=samples,
        param_sets=param_sets,
        explicit_combos=explicit_combos,
        nodes=nodes,
        has_duplicate_methods=has_duplicate_methods,
        system_node_ids=system_node_ids,
        reference_nodes=run_reference_nodes,
        reference_links=run_ref_links,
        fan_in_selectors=fan_in_selectors,
        fan_in_direct_upstream=fan_in_direct_upstream,
        per_sample_selector_upstream=per_sample_selector_upstream,
        fan_in_selector_upstream=fan_in_selector_upstream,
        upstream_link_counts=upstream_link_counts,
    )


# =============================================================================
# Stage: bind references
# =============================================================================

def _reference_name(ref_id: str, info: dict) -> str:
    """The reference as the user named it: its canvas label, else its id."""
    return info.get("label") or ref_id


def _run_phrase(info: dict) -> str:
    """``run <id> (<method>)`` for a message, the method dropped when unknown."""
    run_id = info.get("run_id", "")
    method = info.get("method") or ""
    return f"run {run_id} ({method})" if method else f"run {run_id}"


def _output_listing(output_paths: dict) -> str:
    """``slot (file name)`` pairs for the run's outputs, in record order."""
    return ", ".join(
        f"{slot} ({PurePath(path).name})" for slot, path in output_paths.items()
    )


def _no_run_reference(ref_id: str, info: dict) -> ValueError:
    """The error for a reference link whose node names no run.

    Args:
        ref_id: The reference node's id.
        info: The reference's resolved entry (``label``).

    Returns:
        The error for the caller to raise.
    """
    logger.warning(
        "Run reference node %s names no run, so its links cannot be served",
        ref_id,
    )
    return ValueError(
        f"Reference '{_reference_name(ref_id, info)}' names no run; select a "
        f"run for it."
    )


def _invalid_reference(ref_id: str, info: dict, source_slot: str | None) -> ValueError:
    """The invalid-reference error for a link its referenced run cannot serve.

    Logs the detail (the output the link named and the run's outputs, or
    that the run's records are malformed) and returns the one sentence the
    user sees, naming the reference node (its label, else its id) and the
    run.

    Args:
        ref_id: The reference node's id.
        info: The reference's resolved entry (``run_id``, ``label``,
            ``method``, ``malformed``, ``output_paths``).
        source_slot: The output the link names, or ``None``.

    Returns:
        The error for the caller to raise.
    """
    run_id = info.get("run_id", "")
    if info.get("malformed"):
        logger.warning(
            "Run reference node %s: run %s has a malformed record, so output "
            "%r cannot be served", ref_id, run_id, source_slot,
        )
    elif source_slot:
        logger.warning(
            "Run reference node %s: output %r is not one of run %s's outputs "
            "(%s)", ref_id, source_slot, run_id,
            sorted(info.get("output_paths") or {}),
        )
    else:
        logger.warning(
            "Run reference node %s: run %s has no outputs to serve its link",
            ref_id, run_id,
        )
    return ValueError(
        f"Reference '{_reference_name(ref_id, info)}' is invalid for "
        f"{_run_phrase(info)}; re-run that step and select the new run."
    )


def _unnamed_output_reference(ref_id: str, info: dict) -> ValueError:
    """The error for a link naming no output on a run with several.

    Unlike an invalid reference, this one has a fix other than re-running:
    name one of the run's outputs on the link. The message lists them.

    Args:
        ref_id: The reference node's id.
        info: The reference's resolved entry (``run_id``, ``label``,
            ``method``, ``output_paths``).

    Returns:
        The error for the caller to raise.
    """
    output_paths = info.get("output_paths") or {}
    logger.warning(
        "Run reference node %s: a link names no output and run %s has several "
        "(%s)", ref_id, info.get("run_id", ""), sorted(output_paths),
    )
    return ValueError(
        f"Reference '{_reference_name(ref_id, info)}' draws on "
        f"{_run_phrase(info)}, which has several outputs: "
        f"{_output_listing(output_paths)}. Name the one to use on the link."
    )


def reference_link_error(ref_id: str, info: dict,
                         source_slot: str | None) -> ValueError | None:
    """The refusal for a reference link its referenced run cannot serve, if any.

    The one rule for whether a reference link resolves to an artifact: the
    node names a run, the run's records are well formed, and the output the
    link names is one of the run's -- or, naming none, the run has exactly
    one. The load's binding and the claim's reference branch both ask it, so
    a link is refused for the same reason, with the same message, at either
    moment.

    Args:
        ref_id: The reference node's id.
        info: The reference's resolved entry (``run_id``, ``label``,
            ``method``, ``malformed``, ``output_paths``).
        source_slot: The output the link names, or ``None``.

    Returns:
        The error to raise, or ``None`` when the link resolves.
    """
    output_paths = info.get("output_paths") or {}
    if not info.get("run_id"):
        return _no_run_reference(ref_id, info)
    if info.get("malformed"):
        return _invalid_reference(ref_id, info, source_slot)
    if source_slot:
        if source_slot not in output_paths:
            return _invalid_reference(ref_id, info, source_slot)
        return None
    if len(output_paths) == 1:
        return None
    if not output_paths:
        return _invalid_reference(ref_id, info, source_slot)
    return _unnamed_output_reference(ref_id, info)


@task(purpose="Bind the referenced runs' resolved outputs and sample into the "
              "parsed document: merge each reference's sample, and inject the "
              "output each reference link names -- or the run's only output "
              "when it names none -- onto its consumer's slot")
def bind_references(parsed: ParsedDocument, reference_outputs: dict[str, dict]) -> None:
    """Bind resolved reference outputs into the parsed document, in place.

    ``reference_outputs`` is what the caller's resolver returns for the
    document's references: ``{node id: {output_paths, sample, method,
    malformed}}``. A reference with no entry, or one whose entry carries no
    ``output_paths``, has no output to serve its links.

    A reference node is the run it names, and each link names the output it
    draws from that run. Every link from a node that names no run fails the
    load. An output the link names must be one of the run's; a link that
    names none takes the run's only output, and fails when the run has
    several or none.

    Args:
        parsed: The parse stage's result; ``samples`` and the steps'
            ``run_ref_inputs`` are mutated.
        reference_outputs: Resolved outputs keyed by reference node id.

    Raises:
        ValueError: A reference link is invalid for its referenced run, or
            names no output on a run with several.
    """
    resolved: dict[str, dict] = {
        ref_id: {**declared, **(reference_outputs.get(ref_id) or {})}
        for ref_id, declared in parsed.reference_nodes.items()
    }

    # Merge each run_reference's referenced Run.sample into the pipeline
    # sample list. Same dedup-preserving pattern as input_selector above.
    # Without this, a pipeline rooted solely at a run_reference ends up with
    # samples=[] and Snakemake generates a zero-job DAG that silently exits 0.
    for info in resolved.values():
        ref_sample = info.get("sample", "")
        if ref_sample and ref_sample not in parsed.samples:
            parsed.samples.append(ref_sample)

    # ── Inject run_reference inputs ──────────────────────────────────────────
    # For each link from a run_reference node to a method node, add the
    # resolved artifact path as a static input on the downstream StepDef.
    # Each link carries its own ``source_slot`` (which output of the prior
    # run it draws from); that output selects from ``output_paths``. A link
    # naming none takes the run's only output, the rule an unnamed link
    # between two methods already follows.
    if parsed.reference_links:
        step_by_raw_id: dict[str, StepDef] = {}
        for n_raw, s in zip(parsed.nodes, parsed.steps):
            step_by_raw_id[str(n_raw["id"])] = s

        for rl in parsed.reference_links:
            ref_id = str(rl["source"])
            tgt_raw_id = str(rl["target"])
            ref_info = resolved.get(ref_id, {})
            source_slot = rl.get("source_slot")
            output_paths = ref_info.get("output_paths") or {}
            error = reference_link_error(ref_id, ref_info, source_slot)
            if error is not None:
                raise error
            output_path = (output_paths[source_slot] if source_slot
                           else next(iter(output_paths.values())))
            if output_path and tgt_raw_id in step_by_raw_id:
                tgt_step = step_by_raw_id[tgt_raw_id]
                # Label the ref-input with the canvas link's ``target_slot``
                # so ``--ref-input <slot>=<path>`` lands the artifact in the
                # slot the downstream method actually reads. A second link
                # onto an already-present slot APPENDS: the slot is the
                # method's contract and N references wired into it are N
                # artifacts under that one name, not N invented slots the
                # method never declared. A link that names no target_slot
                # gets a synthetic ``run_ref_{i}`` label instead — one per
                # such link.
                target_slot = rl.get("target_slot") or ""
                if target_slot:
                    label = target_slot
                else:
                    label = f"run_ref_{len(tgt_step.run_ref_inputs)}"
                tgt_step.run_ref_inputs.setdefault(label, []).append(output_path)


# =============================================================================
# Stage: order and legality
# =============================================================================

@task(purpose="Refuse a document the engine cannot give a meaning to: a cycle "
              "in the step DAG, a method node fed by more than one per-sample "
              "selector, a method node fed by more than one fan-in selector, "
              "or a fan-in selector beside another upstream on one consumer "
              "(all three rules shared, message for message, with structural "
              "validation)")
def check_legality(parsed: ParsedDocument) -> None:
    """Refuse illegal wirings over the parsed steps.

    Args:
        parsed: The parse stage's result, references bound.

    Raises:
        ValueError: The DAG contains a cycle, a method node has more than
            one per-sample selector upstream, a method node has more than
            one fan-in selector upstream, or a fan-in selector is not the
            sole upstream of its consumer.
    """
    # Cycle detection (topological sort via Kahn's algorithm)
    topo_sort_steps(parsed.steps)

    # One per-sample selector per method node -- the same rule, and the
    # same message, structural validation gives.
    errors = second_selector_errors(parsed.per_sample_selector_upstream)

    # The fan-in shape rules, from the same module, so the launch path and
    # the canvas's preview refuse the same documents for the same reasons.
    errors.extend(
        fan_in_upstream_errors(
            parsed.fan_in_selector_upstream, parsed.upstream_link_counts
        )
    )
    if errors:
        raise ValueError(errors[0])


# =============================================================================
# Stage: collapse
# =============================================================================

@task(purpose="Mark every step downstream of a fan-in selector as "
              "sample-collapsed, then reject a collapsed step that also "
              "consumes a per-sample method step's outputs -- the one "
              "collapse shape the engine cannot schedule")
def propagate_collapse(parsed: ParsedDocument) -> None:
    """Propagate sample collapse from fan-in selectors, in place.

    Args:
        parsed: The parse stage's result; the steps' ``sample_collapsed``,
            ``collapsed_samples`` and fan-in slot are mutated.

    Raises:
        ValueError: A collapsed step consumes a per-sample method step's
            outputs.
    """
    # ── Sample-collapse propagation ─────────────────────────────────────────
    # A step is sample_collapsed when:
    #   (a) it directly consumes a fan-in input_selector upstream, or
    #   (b) any of its resolved upstream steps is already sample_collapsed.
    # Collapse is contagious: no re-fan-out is supported.
    steps = parsed.steps
    fan_in_selectors = parsed.fan_in_selectors
    if fan_in_selectors:
        sorted_steps = topo_sort_steps(steps)
        step_by_nid: dict[str, StepDef] = {s.node_id: s for s in steps}
        # raw_id → node_id mapping (built during step construction)
        raw_id_to_nid: dict[str, str] = {}
        for n in parsed.nodes:
            raw = str(n["id"])
            method_name = n["method"]
            if parsed.has_duplicate_methods or not raw.isdigit():
                raw_id_to_nid[raw] = raw
            else:
                raw_id_to_nid[raw] = method_name

        # Precompute direct-selector upstreams keyed by step node_id.
        direct_fan_in_nid: dict[str, tuple[str, str]] = {}
        for method_raw, (selector_raw, target_slot) in parsed.fan_in_direct_upstream.items():
            if method_raw in raw_id_to_nid:
                direct_fan_in_nid[raw_id_to_nid[method_raw]] = (selector_raw, target_slot)

        for step in sorted_steps:
            if step.node_id in direct_fan_in_nid:
                sel_raw, target_slot = direct_fan_in_nid[step.node_id]
                step.sample_collapsed = True
                step.collapsed_samples = list(fan_in_selectors[sel_raw])
                # Record the fan-in slot on step.inputs so _input_path emits
                # sentinels under this key and _generate_rule passes
                # --ref-input <slot>=<path> in the shell command.
                step.inputs.setdefault(target_slot, [])
                continue
            # Inherit from any upstream step that is already collapsed
            for up_nid in step.depends_on:
                up = step_by_nid.get(up_nid)
                if up is not None and up.sample_collapsed:
                    step.sample_collapsed = True
                    step.collapsed_samples = list(up.collapsed_samples)
                    break

        # A collapsed step consuming a per-sample method step's outputs is
        # a shape the engine cannot schedule: collapse is decided per node,
        # so the consumer would run once at COLLAPSED_SAMPLE while its
        # upstream runs once per sample, and the generated rule's input
        # would carry a {sample} wildcard its output cannot bind. Bundling
        # an upstream method's per-sample outputs is not supported, so the
        # document is rejected here -- the one place every consumer
        # (generator, cancelled-rows walk, harness) loads it.
        for step in sorted_steps:
            if not step.sample_collapsed:
                continue
            per_sample_upstreams = [
                up for up in step.depends_on
                if up in step_by_nid and not step_by_nid[up].sample_collapsed
            ]
            if per_sample_upstreams:
                raise ValueError(
                    f"Method node '{step.node_id}' (method={step.method_name}) "
                    f"is sample-collapsed (it consumes a fan-in input_selector, "
                    f"directly or through a collapsed upstream) but also "
                    f"consumes the per-sample outputs of upstream method "
                    f"step(s) {per_sample_upstreams}: a collapsed step cannot "
                    f"consume per-sample outputs of an upstream method step. "
                    f"Collapse is decided per node, so this node would run "
                    f"once at sample '{COLLAPSED_SAMPLE}' while its upstream "
                    f"runs once per sample. Bundling an upstream method's "
                    f"per-sample outputs is not supported; feed the fan-in "
                    f"selector and the per-sample chain into separate nodes."
                )


# =============================================================================
# Stage: the column cross-check moment
# =============================================================================

@task(purpose="Pair each input slot with the upstream output slot that feeds "
              "it and cross-check their declared columns (warnings only)")
def cross_check_columns_moment(steps: list[StepDef], contract_map: dict) -> None:
    """Cross-check strict columns between connected steps at load time.

    For each step, reads its contract from the map (keyed
    ``module.method``) to get input/output column specs.  For each input
    slot, finds the upstream step and checks that the upstream's declared
    output columns are a superset of the downstream's required input
    columns (strict only).  A step whose entry has no input-slot half is
    skipped, as is an upstream with no output-slot half.

    Warnings are logged for mismatches.  from_params and patterns are
    silently deferred to runtime.

    Args:
        steps: The parsed steps.
        contract_map: ``{"<module>.<method>": {"input_slots": ...,
            "output_slots": ...}}`` -- Registration's contract map.
    """
    # Build node_id -> StepDef lookup
    step_map: dict[str, StepDef] = {s.node_id: s for s in steps}

    def _slots(step: StepDef) -> tuple[dict | None, dict | None]:
        entry = contract_map.get(f"{step.module_name}.{step.method_name}")
        if not entry:
            return (None, None)
        return (entry.get("input_slots"), entry.get("output_slots"))

    # Cross-check each step's input slots against upstream output slots
    for step in steps:
        input_slots, _ = _slots(step)
        if not input_slots:
            continue

        for slot_name, upstream_ids in step.inputs.items():
            slot_def = input_slots.get(slot_name, {})
            input_column_spec = slot_def.get("columns")
            if not input_column_spec:
                continue

            for upstream_id in upstream_ids:
                upstream_step = step_map.get(upstream_id)
                if upstream_step is None:
                    continue

                _, upstream_output_slots = _slots(upstream_step)
                if not upstream_output_slots:
                    continue

                # Determine which upstream output slot feeds this input
                # Use the source_slot mapping if available, otherwise try
                # the first output slot or the slot with the same name
                source_slots = step.input_source_slots.get(slot_name, [])
                idx = upstream_ids.index(upstream_id)
                source_slot = source_slots[idx] if idx < len(source_slots) else None

                if source_slot and source_slot in upstream_output_slots:
                    upstream_slot_def = upstream_output_slots[source_slot]
                elif len(upstream_output_slots) == 1:
                    upstream_slot_def = next(iter(upstream_output_slots.values()))
                else:
                    continue

                upstream_column_spec = upstream_slot_def.get("columns")
                warnings = cross_check_columns(upstream_column_spec, input_column_spec)
                for w in warnings:
                    logger.warning(
                        "Static column cross-check: step '%s' slot '%s' <- "
                        "upstream '%s': %s",
                        step.node_id, slot_name, upstream_id, w,
                    )


# =============================================================================
# The load
# =============================================================================

@task(purpose="Parse a pipeline document into a PipelineDef for execution, on "
              "values alone: the document, the contract map and the "
              "referenced runs' outputs are handed in")
def load_pipeline(
    document: dict,
    *,
    contract_map: dict,
    reference_outputs: dict[str, dict],
) -> PipelineDef:
    """Parse a pipeline document into a PipelineDef.

    Args:
        document: The pipeline document (the caller reads the file).
        contract_map: Registration's contract map, ``{"<module>.<method>":
            {"input_slots", "output_slots", ...}}``; ``{}`` skips the
            column cross-check for every step.
        reference_outputs: The referenced runs' resolved outputs,
            ``{node id: {"output_paths", "sample", "method", "malformed"}}``
            (what ``wfc.storage.resolve_run_reference_outputs``
            returns); a reference with no resolved outputs cannot serve
            its links.

    Returns:
        A ``PipelineDef`` ready for ``generate_snakefile()``.

    Raises:
        KeyError: If required fields are missing.
        ValueError: If the graph contains cycles or dangling references,
            a method node is an invalid root, a node declares no env, a
            collapsed step consumes a per-sample method step's outputs, or
            the document resolves to zero samples.
    """
    口 = AutoStep(step_num=1, name="Parse the document")
    parsed = parse_document(document)

    口 = AutoStep(step_num=2, name="Bind references")
    bind_references(parsed, reference_outputs)

    口 = AutoStep(step_num=3, name="Order and legality")
    check_legality(parsed)

    口 = AutoStep(step_num=4, name="Propagate sample collapse; reject a collapsed step over per-sample outputs")
    propagate_collapse(parsed)

    口 = AutoStep(step_num=5, name="Static column cross-check")
    cross_check_columns_moment(parsed.steps, contract_map)

    口 = Step(step_num=6, name="Refuse zero samples; assemble the definition",
             purpose="A pipeline with method steps but no samples would be a "
                     "zero-job DAG that silently exits 0 -- fail loudly")
    # Defensive: a pipeline with method steps but no samples produces a
    # zero-job Snakemake DAG that silently exits 0. That happens when a
    # run_reference is the sole root and its Run.sample can't be
    # resolved. Fail loudly instead of letting the pipeline "succeed"
    # with zero runs and no outputs.
    if parsed.steps and not parsed.samples:
        raise ValueError(
            "Pipeline has method nodes but resolves to zero samples. "
            "Add an input_selector, or ensure every run_reference's "
            "referenced Run has a sample in the database."
        )

    return PipelineDef(
        steps=parsed.steps,
        samples=parsed.samples,
        param_sets=parsed.param_sets,
        explicit_combos=parsed.explicit_combos,
    )
