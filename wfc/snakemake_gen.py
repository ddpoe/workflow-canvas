"""
Snakemake pipeline generator (wildcard-based).

Generates ONE rule per method using wildcards for samples and parameter variants.
No rule explosion — the number of rules equals the number of pipeline steps,
regardless of how many samples or parameter variants exist.

Supports:
  - Cartesian product (all variant combinations)
  - Selective combos (explicit list of variant combinations)
  - Asymmetric fan-out (different steps with different numbers of variants)

Design choices (aligned with SYSTEM_OVERVIEW_v2.html):
  - Everything hardcoded — no Snakemake config files needed
  - Each Snakefile is a throwaway build artifact; the DB is the record of truth
  - Each method dir is mapped to a single Snakemake rule (with wildcards)
  - wfc CLI handles bookkeeping; Snakemake handles execution + DAG resolution
"""

from __future__ import annotations

import json
import logging
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dflow.core.decorators import workflow, task, Step, AutoStep

logger = logging.getLogger(__name__)


# =============================================================================
# Data structures
# =============================================================================

@dataclass
class StepDef:
    """One step in the pipeline DAG.

    ``node_id`` is the unique identity within a pipeline.  For legacy
    pipelines (integer node IDs, one method per node) it defaults to
    ``method_name`` so existing behaviour is preserved.  New pipelines
    (string IDs, same method reused across nodes) set it explicitly.

    ``inputs`` maps named input slots to the upstream node_ids that
    feed them.  For single-input methods the slot is ``"data"``.
    For fan-in methods (e.g. csv_merge) a slot like ``"sources"``
    may list multiple upstream node_ids.
    """
    method_name: str
    module_name: str              # owning module (qualifies method lookup)
    script_path: str          # relative path to method script ({method_name}.py)
    params: dict              # default params (used if no param_sets entry)
    depends_on: list[str] = field(default_factory=list)  # upstream node_ids
    output_ext: str = ".parquet"  # output file extension (e.g. .csv, .parquet)
    node_id: str = ""             # unique node identity (defaults to method_name)
    inputs: dict[str, list[str]] = field(default_factory=dict)  # slot → [upstream node_ids]
    env: str = "inherit"  # "inherit" | named shared env (e.g. "image-io")
    slot_outputs: dict[str, str] = field(default_factory=dict)
    # named output slots → filename, e.g. {"predictions": "predictions.csv", "model": "model.pkl"}
    # empty dict means single-output method (uses output{ext} path)
    slot_types: dict[str, str] = field(default_factory=dict)
    # named output slots → type string (e.g. "CSV", "JSON", "directory"). ADR-010:
    # the single source of truth for directory-slot detection.  Populated by
    # _enrich_pipeline from MethodContract.output_slots and parsed through to
    # run_step via the pipeline JSON.
    input_source_slots: dict[str, list[str | None]] = field(default_factory=dict)
    # target_slot → [source_slot per upstream_id]; None = use upstream's default/primary output
    # e.g. for plot_decision_boundary: {"data": ["predictions"], "model": ["model"]}
    run_ref_inputs: dict[str, str] = field(default_factory=dict)
    # Named static input paths from run_reference nodes.
    # Maps a slot label to an artifact path string. The label is the canvas
    # link's ``target_slot`` — so the downstream method reads the artifact
    # under the same slot name its contract declares — with a ``run_ref_{i}``
    # fallback for untyped legacy edges. These are concrete paths (no
    # wildcards) injected as additional Snakemake rule inputs alongside
    # wildcard-based upstream deps.
    sample_collapsed: bool = False
    # True when this step's sample axis has been collapsed to a single
    # bundled run (driven by an upstream input_selector with fan_mode="in").
    # Collapsed steps run once per variant -- not once per (sample, variant) --
    # and use the literal "__all__" as their sample segment in paths and runs.
    # Collapse is contagious: any step with a collapsed upstream is itself
    # collapsed (no re-fan-out supported in this cycle).
    collapsed_samples: list[str] = field(default_factory=list)
    # The sample list bundled into this collapsed step. Populated from the
    # originating input_selector's samples. Only meaningful when
    # sample_collapsed is True; empty otherwise.

    def __post_init__(self):
        if not self.node_id:
            self.node_id = self.method_name
        # Derive inputs from depends_on when not explicitly set
        if not self.inputs and self.depends_on:
            self.inputs = {"data": list(self.depends_on)}


@dataclass
class PipelineDef:
    """Complete pipeline definition: steps + samples + named param variants.

    param_sets maps method_name -> {variant_label: {param: value}}.
    If a step has no entry, it gets a single variant called "default"
    using the params from its StepDef.

    explicit_combos (optional): if set, only these specific combinations
    run — not the full cartesian product. Each dict maps method_name
    to variant_label, plus a "sample" key.

    Example::

        [{"sample": "Pa16c", "preprocess": "default", "filter": "strict", "label": "high"}]
    """
    steps: list[StepDef]
    samples: list[str]
    param_sets: dict[str, dict[str, dict]] = field(default_factory=dict)
    explicit_combos: list[dict[str, str]] | None = None


# =============================================================================
# Load pipeline from JSON
# =============================================================================

@task(purpose="Parse a JSON pipeline config into a PipelineDef for execution")
def load_pipeline(path: Path) -> PipelineDef:
    """Parse a JSON pipeline config into a PipelineDef.

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
        path: Path to the JSON config file.

    Returns:
        A ``PipelineDef`` ready for ``generate_snakefile()``.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError: If required fields are missing.
        ValueError: If the graph contains cycles or dangling references.
    """
    口 = Step(step_num=1, name="Parse JSON config",
             purpose="Read nodes, links, samples, and param_sets from JSON file")
    raw = json.loads(Path(path).read_text())

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
    run_reference_outputs: dict[str, dict] = {}  # node_id → {run_id, output_slot, output_path?}
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
            run_reference_outputs[nid] = {
                "run_id": n.get("run_id", ""),
                "output_slot": n.get("output_slot", "output"),
                "output_path": n.get("output_path", ""),
            }

    # Resolve run_reference output paths from DB when not in the node JSON
    run_reference_outputs = _resolve_run_reference_paths(run_reference_outputs)

    # Merge each run_reference's referenced Run.sample into the pipeline
    # sample list. Same dedup-preserving pattern as input_selector above.
    # Without this, a pipeline rooted solely at a run_reference ends up with
    # samples=[] and Snakemake generates a zero-job DAG that silently exits 0.
    for info in run_reference_outputs.values():
        ref_sample = info.get("sample", "")
        if ref_sample and ref_sample not in samples:
            samples.append(ref_sample)

    # Capture links from run_reference nodes → downstream method nodes
    # before filtering them out of the main link list.
    run_ref_links: list[dict] = []  # [{source: ref_id, target: method_id, ...}]
    for lnk in links:
        src_id = str(lnk["source"])
        if src_id in run_reference_outputs:
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
    fan_in_direct_upstream: dict[str, tuple[str, str]] = {}
    if fan_in_selectors:
        for lnk in links:
            src_raw = str(lnk["source"])
            tgt_raw = str(lnk["target"])
            if src_raw in fan_in_selectors:
                target_slot = lnk.get("target_slot", "data")
                fan_in_direct_upstream[tgt_raw] = (src_raw, target_slot)

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
    # node_id defaults to method_name so existing code keeps working.
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

        steps.append(StepDef(
            method_name=method,
            module_name=module,
            script_path=n.get("script", f"methods/{method}/{method}.py"),
            params=n.get("params", {}),
            depends_on=resolved_deps,
            output_ext=n.get("output_ext", ".parquet"),
            node_id=nid,
            inputs=inputs,
            env=n.get("env", n.get("env_strategy", "inherit")),
            slot_outputs=n.get("slot_outputs", {}),
            slot_types=n.get("slot_types", {}),
            input_source_slots=input_source_slots_for_step,
        ))

    # ── Inject run_reference inputs ──────────────────────────────────────────
    # For each link from a run_reference node to a method node, add the
    # resolved artifact path as a static input on the downstream StepDef.
    # Each link carries its own ``source_slot`` (which output of the prior
    # run it draws from); that slot selects from ``output_paths``. Legacy
    # pipelines without ``source_slot`` on the link fall back to the single
    # resolved ``output_path`` on the node.
    if run_ref_links:
        step_by_raw_id: dict[str, StepDef] = {}
        for n_raw, s in zip(nodes, steps):
            step_by_raw_id[str(n_raw["id"])] = s

        for rl in run_ref_links:
            ref_id = str(rl["source"])
            tgt_raw_id = str(rl["target"])
            ref_info = run_reference_outputs.get(ref_id, {})
            source_slot = rl.get("source_slot")
            output_paths = ref_info.get("output_paths", {})
            output_path = ""
            if source_slot and source_slot in output_paths:
                output_path = output_paths[source_slot]
            elif source_slot and source_slot == ref_info.get("output_slot"):
                output_path = ref_info.get("output_path", "")
            else:
                output_path = ref_info.get("output_path", "")
            if output_path and tgt_raw_id in step_by_raw_id:
                tgt_step = step_by_raw_id[tgt_raw_id]
                # Label the ref-input with the canvas link's ``target_slot``
                # so ``--ref-input <slot>=<path>`` lands the artifact in the
                # slot the downstream method actually reads. Fall back to a
                # synthetic ``run_ref_{i}`` label only when the link has no
                # target_slot (untyped legacy edges).
                target_slot = rl.get("target_slot") or ""
                if target_slot and target_slot not in tgt_step.run_ref_inputs:
                    label = target_slot
                else:
                    label = f"run_ref_{len(tgt_step.run_ref_inputs)}"
                tgt_step.run_ref_inputs[label] = output_path

    口 = Step(step_num=4, name="Validate DAG",
             purpose="Cycle detection via topological sort — raises if graph has cycles")
    # Cycle detection (topological sort via Kahn's algorithm)
    topo_sort_steps(steps)

    # ── Sample-collapse propagation ─────────────────────────────────────────
    # A step is sample_collapsed when:
    #   (a) it directly consumes a fan-in input_selector upstream, or
    #   (b) any of its resolved upstream steps is already sample_collapsed.
    # Collapse is contagious: no re-fan-out is supported (architect constraint).
    if fan_in_selectors:
        sorted_steps = topo_sort_steps(steps)
        step_by_nid: dict[str, StepDef] = {s.node_id: s for s in steps}
        # raw_id → node_id mapping (built during step construction)
        raw_id_to_nid: dict[str, str] = {}
        for n in nodes:
            raw = str(n["id"])
            method_name = n["method"]
            if has_duplicate_methods or not raw.isdigit():
                raw_id_to_nid[raw] = raw
            else:
                raw_id_to_nid[raw] = method_name

        # Precompute direct-selector upstreams keyed by step node_id.
        direct_fan_in_nid: dict[str, tuple[str, str]] = {}
        for method_raw, (selector_raw, target_slot) in fan_in_direct_upstream.items():
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

    口 = Step(step_num=5, name="Static column cross-check (ADR-005)",
             purpose="Cross-check strict columns between connected steps at load time")
    _static_column_cross_check(steps)

    # Defensive: a pipeline with method steps but no samples produces a
    # zero-job Snakemake DAG that silently exits 0. That happened when a
    # run_reference was the sole root and its Run.sample couldn't be
    # resolved. Fail loudly instead of letting the pipeline "succeed"
    # with zero runs and no outputs.
    if steps and not samples:
        raise ValueError(
            "Pipeline has method nodes but resolves to zero samples. "
            "Add an input_selector, or ensure every run_reference's "
            "referenced Run has a sample in the database."
        )

    return PipelineDef(
        steps=steps,
        samples=samples,
        param_sets=param_sets,
        explicit_combos=explicit_combos,
    )


# =============================================================================
# Run reference path resolution
# =============================================================================

def _resolve_run_reference_paths(
    run_ref_outputs: dict[str, dict],
) -> dict[str, dict]:
    """Resolve artifact paths and sample for run_reference nodes.

    For each run_reference entry, DB-resolves:
      - ``output_paths``: {slot_name: artifact_path} for every RunOutput of
        the referenced Run. Edges carrying a ``source_slot`` pick from here.
      - ``sample``: the referenced ``Run.sample``. Merged into the pipeline
        samples list so a run_reference-rooted pipeline actually has work to
        do.
      - ``output_path``: legacy single path kept for backcompat — populated
        from ``output_paths[output_slot]`` when a singular ``output_slot`` is
        set on the node (old pipelines authored before multi-output).

    If ``output_path`` was already present on the node JSON it is preserved
    (callers / tests pre-populate it). If the DB is unavailable, the node
    keeps whatever it had and a warning is logged.

    Args:
        run_ref_outputs: Dict mapping node_id to
            ``{"run_id": ..., "output_slot": ..., "output_path": ...}``.

    Returns:
        The same dict, mutated with ``output_paths`` and ``sample`` resolved
        where possible, plus legacy ``output_path`` backfill.
    """
    # Default fields on every entry so callers can rely on them existing.
    for info in run_ref_outputs.values():
        info.setdefault("output_paths", {})
        info.setdefault("sample", "")

    if not run_ref_outputs:
        return run_ref_outputs

    try:
        from wfc.database import get_session
        from wfc.models import Run, RunOutput
        from sqlmodel import select

        with get_session() as session:
            for nid, info in run_ref_outputs.items():
                run_id = info.get("run_id", "")
                if not run_id:
                    continue
                try:
                    run_id_int = int(run_id)
                except (ValueError, TypeError):
                    continue

                # All RunOutputs for the referenced run → {slot: path}
                output_rows = session.exec(
                    select(RunOutput).where(RunOutput.run_id == run_id_int)
                ).all()
                for row in output_rows:
                    if row.output_name and row.artifact_path:
                        info["output_paths"][row.output_name] = row.artifact_path

                # Referenced Run's sample (single value — one run, one sample)
                run_row = session.exec(
                    select(Run).where(Run.id == run_id_int)
                ).first()
                if run_row and run_row.sample:
                    info["sample"] = run_row.sample

                # Legacy singular output_path: populate from output_paths when
                # an output_slot was supplied and a matching RunOutput exists.
                if not info.get("output_path"):
                    legacy_slot = info.get("output_slot", "")
                    if legacy_slot and legacy_slot in info["output_paths"]:
                        info["output_path"] = info["output_paths"][legacy_slot]

                if not info["output_paths"] and not info.get("output_path"):
                    logger.warning(
                        "Run reference node %s: no artifacts found for run_id=%s",
                        nid, run_id,
                    )
    except Exception:
        for nid, info in run_ref_outputs.items():
            if not info["output_paths"] and not info.get("output_path"):
                logger.warning(
                    "Run reference node %s: DB unavailable, cannot resolve "
                    "artifact paths for run_id=%s",
                    nid, info.get("run_id", ""),
                )
    return run_ref_outputs


# =============================================================================
# ADR-005: Static column cross-check
# =============================================================================

def _static_column_cross_check(steps: list[StepDef]) -> None:
    """Cross-check strict columns between connected steps at pipeline-load time.

    For each step, looks up the MethodContract from the database to get
    input/output column specs.  For each input slot, finds the upstream step
    and checks that the upstream's declared output columns are a superset
    of the downstream's required input columns (strict only).

    Warnings are logged for mismatches.  from_params and patterns are
    silently deferred to runtime.

    Args:
        steps: List of StepDef objects from load_pipeline().
    """
    try:
        from wfc.database import get_session
        from wfc.models import Method, MethodContract
        from wfc.contracts import cross_check_columns
        from sqlmodel import select
    except Exception:
        return  # DB not available, skip static checks

    # Build node_id -> StepDef lookup
    step_map: dict[str, StepDef] = {s.node_id: s for s in steps}

    # Load all MethodContracts into a cache: method_name -> (input_slots, output_slots)
    contract_cache: dict[str, tuple[dict | None, dict | None]] = {}
    try:
        with get_session() as session:
            for step in steps:
                if step.method_name in contract_cache:
                    continue
                method = session.exec(
                    select(Method).where(Method.name == step.method_name)
                ).first()
                if method is None:
                    contract_cache[step.method_name] = (None, None)
                    continue
                mc = session.exec(
                    select(MethodContract).where(
                        MethodContract.method_id == method.id
                    )
                ).first()
                if mc is None:
                    contract_cache[step.method_name] = (None, None)
                else:
                    contract_cache[step.method_name] = (mc.input_slots, mc.output_slots)
    except Exception:
        return

    # Cross-check each step's input slots against upstream output slots
    for step in steps:
        input_slots, _ = contract_cache.get(step.method_name, (None, None))
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

                _, upstream_output_slots = contract_cache.get(
                    upstream_step.method_name, (None, None)
                )
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
# Pipeline helpers
# =============================================================================

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


@task(purpose="Expand sample × variant combinations into concrete run specifications")
def expand_variant_combos(
    steps: list[StepDef],
    samples: list[str],
    resolved_params: dict[str, dict[str, dict]],
    explicit_combos: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Expand sample × variant combinations.

    Returns a list of dicts like::

        {"sample": "Pa16c", "variant": "strict"}

    In cartesian mode, generates every sample × variant combination
    where variants are the union of all param_sets variant names.
    In selective mode, returns ``explicit_combos`` as-is.

    Args:
        steps: Ordered list of StepDefs.
        samples: List of sample identifiers.
        resolved_params: ``{node_id: {variant_name: params_dict}}``.
        explicit_combos: If provided, used directly (selective mode).

    Returns:
        List of combo dicts with ``sample`` and ``variant`` keys.
    """
    if explicit_combos is not None:
        if any(s.sample_collapsed for s in steps):
            raise ValueError(
                "explicit_combos is not supported for pipelines containing "
                "fan-in (sample-collapsed) steps"
            )
        return explicit_combos

    # Collect global variant names
    all_variant_names: set[str] = set()
    for variants in resolved_params.values():
        all_variant_names.update(variants.keys())
    variant_names = sorted(all_variant_names) if all_variant_names else ["default"]

    # If any step in the pipeline is sample_collapsed, the terminal (leaf)
    # bundle is a single "__all__" sample per variant. Per the architect
    # constraint, once collapsed the pipeline stays collapsed -- so all
    # leaves share the collapsed sample axis. (Mixed-axis pipelines are
    # rejected at validate time.)
    if any(s.sample_collapsed for s in steps):
        return [{"sample": "__all__", "variant": v} for v in variant_names]

    combos = []
    for sample in samples:
        for variant in variant_names:
            combos.append({"sample": sample, "variant": variant})

    return combos


# =============================================================================
# Internal helpers
# =============================================================================

def _python_repr(obj) -> str:
    """Convert a Python object to a repr string with proper Python booleans."""
    if isinstance(obj, dict):
        items = ", ".join(f"{_python_repr(k)}: {_python_repr(v)}" for k, v in obj.items())
        return "{" + items + "}"
    elif isinstance(obj, list):
        items = ", ".join(_python_repr(v) for v in obj)
        return "[" + items + "]"
    elif isinstance(obj, bool):
        return "True" if obj else "False"
    elif isinstance(obj, str):
        return json.dumps(obj)
    else:
        return repr(obj)


def _find_leaf_nodes(step_map: dict[str, StepDef]) -> list[str]:
    """Return node_ids that have no downstream dependents (leaf nodes)."""
    all_ids = set(step_map.keys())
    has_children = set()
    for s in step_map.values():
        for dep in s.depends_on:
            has_children.add(dep)
    return [nid for nid in step_map if nid not in has_children]


def _output_path(node_id: str, step_map: dict[str, StepDef], pipeline_id: str | None = None) -> str:
    """Build the Snakemake-visible output path -- a zero-byte sentinel (ADR-018).

    Unified path scheme -- every node, regardless of slot count or slot type::

        .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/.complete

    The sentinel is touched by ``wfc run-step`` after ``cache_file`` succeeds,
    signalling to Snakemake that the rule completed.  Actual method outputs
    live in ``.runs/<run_id>/<slot>/`` (staging) and are content-addressed
    via the DVC cache -- Snakemake never sees them.  Slot-agnostic: every
    slot of a node collapses to one sentinel under the unified contract.

    Args:
        node_id: Node identity within the pipeline.
        step_map: Lookup dict of all steps.
        pipeline_id: Pipeline execution ID.

    Returns:
        Sentinel path with ``{sample}`` and ``{variant}`` wildcards.
        Collapsed (fan-in) steps bake ``__all__`` into the sample segment.
    """
    step = step_map[node_id]
    prefix = f".runs/sentinels/{pipeline_id}" if pipeline_id else ".runs/sentinels"
    # Collapsed steps (fan-in) bake the literal "__all__" into the sample
    # segment -- they run once per variant, not once per (sample, variant).
    sample_segment = "__all__" if step.sample_collapsed else "{sample}"
    return f"{prefix}/{node_id}/{sample_segment}/{{variant}}/.complete"


def _input_path(
    node_id: str,
    step_map: dict[str, StepDef],
    pipeline_id: str | None = None,
) -> str | dict[str, str | list[str]] | None:
    """Build the input path (= upstream step's output path).

    Returns None for root steps (no upstream dependency).
    For multi-input (fan-in) steps, returns a dict mapping slot names
    to lists of upstream output paths.
    """
    step = step_map[node_id]
    if not step.depends_on:
        # Collapsed root step: its "upstream" is a fan-in input_selector.
        # Emit a list of per-sample restore sentinels (ADR-009) bound to
        # the consumer's first input slot so Snakemake expands the bundle
        # as a list of input files on a single rule invocation.
        if step.sample_collapsed and step.collapsed_samples:
            slot_name = next(iter(step.inputs), "data") if step.inputs else "data"
            sentinels = [
                f"data/samples/{s}/.sample_ready" for s in step.collapsed_samples
            ]
            return {slot_name: sentinels}
        return None

    # Multi-input: fan-in (>1 dep), multi-slot (>1 input slot), or named source_slots
    if (
        len(step.depends_on) > 1
        or any(len(v) > 1 for v in step.inputs.values())
        or len(step.inputs) > 1
    ):
        result: dict[str, list[str]] = {}
        for slot, upstream_ids in step.inputs.items():
            result[slot] = [
                _output_path(uid, step_map, pipeline_id=pipeline_id)
                for uid in upstream_ids
            ]
        return result

    # Single input: sentinels are slot-agnostic under ADR-018; no need to
    # consult source_slot here.
    upstream_id = step.depends_on[0]
    return _output_path(upstream_id, step_map, pipeline_id=pipeline_id)


# =============================================================================
# Rule body generator
# =============================================================================

@task(
    purpose="Generate Snakemake rule lines for a single pipeline step (shell-only, ADR 008)",
    inputs="StepDef, step lookup map",
    outputs="List of Snakefile lines for this rule",
)
def _generate_rule(
    step: StepDef,
    step_map: dict[str, StepDef],
    pipeline_id: str | None = None,
    sample_restore_sentinel: str | None = None,
) -> list[str]:
    """Generate a minimal Snakemake rule that delegates to ``wfc run-step``.

    All execution logic (cache check, env dispatch, run registration, error
    capture, output archiving) lives in the ``run-step`` CLI command.  The
    generated rule only handles DAG wiring (input/output/shell).

    Args:
        step: The pipeline step to generate a rule for.
        step_map: Lookup dict of all steps keyed by node_id.
        pipeline_id: Pipeline execution ID.
        sample_restore_sentinel: If provided and the step is a root step,
            use this path as the input dependency (restore_sample sentinel).

    Returns:
        List of strings (one per line) for this rule block.
    """
    nid = step.node_id
    out = _output_path(nid, step_map, pipeline_id=pipeline_id)
    inp = _input_path(nid, step_map, pipeline_id=pipeline_id)
    is_fan_in = isinstance(inp, dict)
    lines: list[str] = []

    lines.append(f"rule {nid}:")

    # -- input: declaration --
    has_run_refs = bool(step.run_ref_inputs)
    if is_fan_in or (has_run_refs and inp is not None):
        # Multi-part input: use named input syntax for all parts
        input_parts = []
        if is_fan_in:
            for slot, paths in inp.items():
                for i, p in enumerate(paths):
                    input_parts.append(f"        {slot}_{i}=\"{p}\"")
        elif inp is not None:
            # Single upstream dep promoted to named syntax for consistency
            input_parts.append(f"        data_0=\"{inp}\"")
        # Append run_reference static paths as additional named inputs
        # Use forward slashes so backslashes don't become Python escapes.
        for label, ref_path in step.run_ref_inputs.items():
            input_parts.append(f"        {label}=\"{ref_path.replace(chr(92), '/')}\"")
        lines.append("    input:")
        lines.append(",\n".join(input_parts))
    elif has_run_refs and inp is None:
        # Root step with run_reference inputs (no upstream method deps)
        input_parts = []
        if sample_restore_sentinel is not None:
            input_parts.append(f"        sample_ready=\"{sample_restore_sentinel}\"")
        for label, ref_path in step.run_ref_inputs.items():
            input_parts.append(f"        {label}=\"{ref_path.replace(chr(92), '/')}\"")

        lines.append("    input:")
        lines.append(",\n".join(input_parts))
    elif inp is not None:
        lines.append(f"    input: \"{inp}\"")
    elif sample_restore_sentinel is not None:
        # Root step: depend on restore_sample sentinel (ADR-009)
        lines.append(f"    input: \"{sample_restore_sentinel}\"")

    # -- output: declaration --
    # ADR-018: Snakemake-visible output collapses to a single zero-byte
    # sentinel per (pipeline, node, sample, variant). Real method outputs
    # stay in .runs/<run_id>/<slot>/ (staging) and are content-addressed
    # via the DVC cache. The sentinel is touched by `wfc run-step` after
    # cache_file succeeds. Directory-slot and multi-slot detection are
    # no longer needed in the Snakefile -- the sentinel is uniform.
    lines.append(f"    output: \"{out}\"")

    # -- params: pass variant and node_id through Snakemake params --
    lines.append(f'    params:')
    lines.append(f'        variant="{{variant}}",')
    lines.append(f'        node_id="{nid}"')

    # -- shell: delegate everything to wfc run-step --
    # Use env vars WFC_PIPELINE_JSON and WFC_PIPELINE_ID set in preamble.
    # No {{CONSTANT}} brace patterns in shell strings (D-4).
    # ADR-008 boundary rule: the orchestrator resolves run_reference paths
    # and passes them explicitly via --ref-input so run_step stays
    # topology-agnostic.
    ref_input_args = ""
    for label, ref_path in step.run_ref_inputs.items():
        ref_input_args += f' --ref-input {label}={ref_path.replace(chr(92), "/")}'
    # Collapsed fan-in root: the Snakemake .sample_ready sentinels in the
    # input: block only gate dependency ordering -- they aren't data paths.
    # Emit one --collapsed-sample <s> per bundled sample. The runtime
    # resolver in wfc.cli.run_step iterates each named sample's
    # data/samples/<s>/ directory at execution time (after restore_sample
    # has populated it) and accumulates the per-sample data files into the
    # fan-in slot. Filesystem inspection deliberately stays out of the
    # generator -- restore_sample is itself a Snakemake rule whose outputs
    # don't exist at Snakefile-generation time. (PEV cycle
    # 2026-05-02-snakemake-gen-collapsed-fanin-fix; D-1, D-4.)
    collapsed_sample_args = ""
    if step.sample_collapsed and not step.depends_on and step.collapsed_samples:
        for s in step.collapsed_samples:
            collapsed_sample_args += f' --collapsed-sample {s}'
    # Collapsed steps have no `{sample}` wildcard (it was baked to __all__).
    # Pass the literal so wfc run-step records the run with sample="__all__".
    sample_arg = "__all__" if step.sample_collapsed else "{wildcards.sample}"
    lines.append(f'    shell:')
    lines.append(f'        "{{sys.executable}} -m wfc run-step '
                 f'--node-id {{params.node_id}} '
                 f'--sample {sample_arg} '
                 f'--variant {{params.variant}}'
                 f'{ref_input_args}'
                 f'{collapsed_sample_args}"')

    lines.append("")

    return lines


# =============================================================================
# Generator
# =============================================================================

@workflow(
    purpose="Generate a wildcard-based Snakefile from a pipeline definition",
    inputs="PipelineDef (steps, samples, param variants), project root path",
    outputs="Complete Snakefile string content",
)
def generate_snakefile(
    pipeline: PipelineDef,
    wfc_module_path: str,
    project_root: str | None = None,
    pipeline_id: str | None = None,
    pipeline_json_path: str | None = None,
) -> str:
    """Generate a wildcard-based Snakefile.

    One rule per method. Wildcards handle fan-out across samples
    and parameter variants. Snakemake resolves the DAG from file
    name patterns — no explicit wiring needed.

    Unified path scheme (ADR-018 sentinel-only)::

        .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/.complete

    Every node uses the same sentinel pattern. Actual method outputs
    live in the run-archive directory and are content-addressed via
    the DVC cache; Snakemake never sees them. A single ``{variant}``
    wildcard dimension flows through the entire pipeline. Nodes
    without ``param_sets`` are padded so that every variant name
    maps to their default params.

    Args:
        pipeline: Pipeline definition with steps, samples, named param variants.
        wfc_module_path: Absolute path to wfc framework root (added to PYTHONPATH
            in worker processes so ``import wfc`` works).
        pipeline_id: Pre-generated pipeline ID (UUID string).  When provided,
            the Snakefile embeds this literal value.  When ``None``, falls back
            to reading the ``PIPELINE_LOG_DIR`` env var at Snakemake runtime or
            generating a UUID.
        project_root: Absolute path to the wfc project directory (the git repo
            containing method scripts).  Defaults to ``wfc_module_path``.

    Returns:
        String content of the Snakefile.
    """
    import uuid as _uuid

    # Ensure pipeline_id is always concrete — workspace paths are scoped by it
    _pipeline_id = pipeline_id if pipeline_id is not None else str(_uuid.uuid4())

    step_map = {s.node_id: s for s in pipeline.steps}

    # Resolve param variants keyed by node_id
    # (look up param_sets by node_id first, fall back to method_name)
    resolved_params: dict[str, dict[str, dict]] = {}
    for step in pipeline.steps:
        resolved_params[step.node_id] = pipeline.param_sets.get(
            step.node_id, pipeline.param_sets.get(
                step.method_name, {"default": step.params}
            )
        )

    # Compute global variant names (union of all param_sets variant names)
    all_variant_names: set[str] = set()
    for variants in resolved_params.values():
        all_variant_names.update(variants.keys())
    variant_names = sorted(all_variant_names) if all_variant_names else ["default"]

    # Pad: every node must have an entry for every variant name
    for step in pipeline.steps:
        for vname in variant_names:
            if vname not in resolved_params[step.node_id]:
                resolved_params[step.node_id][vname] = step.params

    lines: list[str] = []
    leaf_ids = _find_leaf_nodes(step_map)

    # ── Header ──────────────────────────────────────────────────────────────
    variant_counts = {m: len(vs) for m, vs in resolved_params.items()}
    if pipeline.explicit_combos:
        total_runs_est = len(pipeline.explicit_combos) * len(pipeline.steps)
        mode = "selective"
    else:
        total_runs_est = len(pipeline.samples) * len(variant_names) * len(pipeline.steps)
        mode = "unified"

    lines.append('"""')
    lines.append(f"Auto-generated Snakefile (wildcard-based, {mode} mode)")
    lines.append(f"Steps: {len(pipeline.steps)} methods")
    lines.append(f"Samples: {pipeline.samples}")
    lines.append(f"Variants: {variant_names}")
    lines.append(f"Estimated total runs: {total_runs_est}")
    lines.append('"""')
    lines.append("")
    lines.append("import subprocess, sys, os, json, uuid, logging, time, tempfile")
    lines.append("")

    # ── Config ──────────────────────────────────────────────────────────────
    lines.append(f"SAMPLES = {repr(pipeline.samples)}")
    lines.append(f"VARIANT_NAMES = {repr(variant_names)}")
    lines.append(f'WFC_ROOT = r"{wfc_module_path}"')
    lines.append(f'PIPELINE_ID = "{_pipeline_id}"')
    # ADR 008: pipeline JSON path for run-step to find node config
    if pipeline_json_path is not None:
        lines.append(f'PIPELINE_JSON = r"{pipeline_json_path}"')
    else:
        lines.append('PIPELINE_JSON = os.environ.get("WFC_PIPELINE_JSON", "")')
    lines.append("")

    # ── Env names (per-node) ─────────────────────────────────────────────────
    lines.append("ENV_NAMES = {")
    for step in pipeline.steps:
        lines.append(f"    {repr(step.node_id)}: {repr(step.env)},")
    lines.append("}")
    lines.append("")

    # ── Env python paths (resolved at generation time) ──────────────────────
    # Resolve env names to python executable paths at generation time
    # so the Snakefile calls python directly (no pixi CLI at runtime).
    _project_root = Path(project_root or wfc_module_path).resolve()
    lines.append(f'PROJECT_ROOT = r"{str(_project_root)}"')
    # Propagate PROJECT_ROOT to every subprocess the Snakefile spawns so that
    # wfc.database.project_root() can resolve it even when Snakemake's shell
    # rules inherit a foreign cwd (notably Windows UNC paths, where cmd.exe
    # silently rewrites cwd to C:\Windows and cwd-based lookup tries to
    # mkdir C:\Windows\.wfc).
    lines.append('os.environ["WFC_PROJECT_ROOT"] = PROJECT_ROOT')
    # Anchor Snakemake itself to the project root so its own relative-path
    # resolution matches the WFC_PROJECT_ROOT contract.
    lines.append('workdir: PROJECT_ROOT')
    env_python_paths: dict[str, str] = {}
    config_path = _project_root / ".wfc" / "wf-canvas.toml"
    if config_path.exists():
        from .init import read_config as _read_config
        _cfg = _read_config(_project_root)
        _pixi_root = _cfg.get("pixi_root", "")
        _conda_root = _cfg.get("conda_root", "")
        from .register import resolve_python_for_env as _resolve_py
        for step in pipeline.steps:
            if step.env != "inherit" and step.env not in env_python_paths:
                try:
                    python_path = _resolve_py(
                        step.env, pixi_root=_pixi_root, conda_root=_conda_root,
                    )
                    env_python_paths[step.env] = str(python_path).replace("\\", "/")
                except ValueError:
                    pass  # env not found — will fall back to sys.executable
    lines.append("ENV_PYTHON_PATHS = {")
    for name, python_path in env_python_paths.items():
        lines.append(f"    {repr(name)}: r{repr(python_path)},")
    lines.append("}")
    lines.append("")

    # ── Params dict (named variants) ───────────────────────────────────────
    lines.append("PARAMS = {")
    for step in pipeline.steps:
        variants = resolved_params[step.node_id]
        lines.append(f"    {repr(step.node_id)}: {{")
        for vname, vparams in variants.items():
            lines.append(f"        {repr(vname)}: {_python_repr(vparams)},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Explicit combos (selective mode only) ──────────────────────────────
    if pipeline.explicit_combos:
        lines.append("RUNS = " + _python_repr(pipeline.explicit_combos))
        lines.append("")

    # ── Logger setup (ADR 004 Tier 1) ───────────────────────────────────────
    lines.append(textwrap.dedent('''\
        # ADR 004: Pipeline logger — writes to pipeline.log + stderr
        PIPELINE_LOG_DIR = os.environ.get(
            "WFC_PIPELINE_LOG_DIR",
            os.path.join(".runs", "pipelines", PIPELINE_ID),
        )
        os.makedirs(PIPELINE_LOG_DIR, exist_ok=True)
        os.makedirs(os.path.join(PIPELINE_LOG_DIR, "runs"), exist_ok=True)

        _pipeline_logger = logging.getLogger(f"wfc.pipeline.{PIPELINE_ID}")
        _pipeline_logger.setLevel(logging.DEBUG)
        _pipeline_log_path = os.path.join(PIPELINE_LOG_DIR, "pipeline.log")
        _file_handler = logging.FileHandler(_pipeline_log_path, encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _stream_handler = logging.StreamHandler(sys.stderr)
        _stream_handler.setLevel(logging.INFO)
        _log_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        _file_handler.setFormatter(_log_fmt)
        _stream_handler.setFormatter(_log_fmt)
        _pipeline_logger.addHandler(_file_handler)
        _pipeline_logger.addHandler(_stream_handler)

        # ADR 008: run outcomes tracked via sidecar JSONs (written by run-step)
    '''))

    # ── Pipeline JSON + env vars for run-step (ADR 008, D-3, D-4) ─────────
    # WFC_PIPELINE_JSON and WFC_PIPELINE_ID are set as env vars so run-step
    # can find the pipeline config.  NOT as {CONSTANT} brace patterns in
    # shell strings (Snakemake interprets those as wildcards).
    lines.append(textwrap.dedent('''\
        # ADR 008: Set env vars for run-step (no brace patterns in shell strings)
        os.environ["WFC_PIPELINE_JSON"] = os.path.abspath(PIPELINE_JSON) if PIPELINE_JSON else ""
        os.environ["WFC_PIPELINE_ID"] = PIPELINE_ID
        # PYTHONPATH is deliberately NOT set here.  The shell rule invokes
        # the run-step verb (``{sys.executable} -m wfc``) against the host
        # venv Python, which already has wfc in its own site-packages — and
        # run-step itself builds a narrow shim (wfc._ensure_wfc_shim) before spawning
        # the pixi-env method subprocess.  Prepending WFC_ROOT to PYTHONPATH
        # here used to leak the host venv's site-packages (pandas/numpy at
        # the wrong ABI) into every Snakemake-spawned child process.
        # Forward DATABASE_URL for test isolation
        if os.environ.get("DATABASE_URL"):
            pass  # already in environment
    '''))

    # ── Rule all (target declaration) ──────────────────────────────────────
    lines.append("rule all:")
    lines.append("    input:")

    if pipeline.explicit_combos:
        # Selective mode: list comprehension over explicit RUNS
        targets = []
        for lid in leaf_ids:
            leaf_output = _output_path(lid, step_map, pipeline_id=_pipeline_id)
            fmt_path = leaf_output.replace("{sample}", "{r['sample']}").replace("{variant}", "{r['variant']}")
            targets.append(f'[f\"{fmt_path}\" for r in RUNS]')
        lines.append("        " + " + ".join(targets))
    else:
        # Unified mode: expand() over leaf nodes × samples × variants.
        # Collapsed leaves have "__all__" already baked into their path, so
        # the sample axis has no remaining wildcard -- emit a single-element
        # list literal to keep expand() well-formed.
        targets = []
        for lid in leaf_ids:
            leaf_step = step_map[lid]
            leaf_output = _output_path(lid, step_map, pipeline_id=_pipeline_id)
            if leaf_step.sample_collapsed:
                targets.append(
                    f'expand("{leaf_output}", variant=VARIANT_NAMES)'
                )
            else:
                targets.append(
                    f'expand("{leaf_output}", sample=SAMPLES, variant=VARIANT_NAMES)'
                )
        lines.append("        " + " + ".join(targets))

    lines.append("")

    # ── ADR-009: restore_sample rules for root steps ─────────────────────
    # Root steps (no depends_on) need samples restored from DVC cache before
    # execution. Generate one restore_sample rule per sample that materializes
    # the sample file and writes a sentinel. Root step rules depend on the
    # sentinel via sample_restore_sentinel.
    root_steps = [s for s in pipeline.steps if not s.depends_on]
    sample_restore_sentinel = None
    if root_steps:
        # Look up sample content_hashes from DB at generation time
        from .database import get_session as _get_session
        from .models import Sample as _Sample
        from sqlmodel import select as _select
        sample_hashes: dict[str, str] = {}
        with _get_session() as _session:
            for sample_name in pipeline.samples:
                row = _session.exec(
                    _select(_Sample).where(_Sample.name == sample_name)
                ).first()
                if row is not None and row.content_hash:
                    sample_hashes[sample_name] = row.content_hash

        if sample_hashes:
            sample_restore_sentinel = "data/samples/{sample}/.sample_ready"

            lines.append("SAMPLE_HASHES = " + repr(sample_hashes))
            lines.append("")

            # Generate restore_sample rule
            lines.append("rule restore_sample:")
            lines.append(f'    output: "{sample_restore_sentinel}"')
            lines.append("    params:")
            lines.append('        hash=lambda wildcards: SAMPLE_HASHES.get(wildcards.sample, "")')
            # wfc restore-sample itself creates the Snakemake sentinel marker
            # at <project_root>/data/samples/<name>/.sample_ready (mkparents,
            # absolute path via get_project_root()), so the shell rule is now
            # a single cwd-independent wfc invocation.
            lines.append("    shell:")
            lines.append(
                '        "{sys.executable} -m wfc restore-sample '
                '--name {wildcards.sample} --hash {params.hash}"'
            )
            lines.append("")

    # ── Rules (one per node) ─────────────────────────────────────────────
    for step in pipeline.steps:
        lines.extend(_generate_rule(
            step, step_map,
            pipeline_id=_pipeline_id,
            sample_restore_sentinel=sample_restore_sentinel if not step.depends_on else None,
        ))

    # ── onsuccess / onerror handlers (ADR 008: delegate to pipeline-summary) ──
    lines.append("onsuccess:")
    lines.append('    _pipeline_logger.info("Pipeline %s completed successfully", PIPELINE_ID)')
    lines.append('    shell(f"wfc pipeline-summary --pipeline-id {PIPELINE_ID}")')
    lines.append("")
    lines.append("onerror:")
    lines.append("    try:")
    lines.append('        shell(f"wfc fail_pipeline --pipeline-id {PIPELINE_ID}")')
    lines.append("    except Exception:")
    lines.append('        _pipeline_logger.exception("fail_pipeline command failed")')
    lines.append('    _pipeline_logger.error("Pipeline %s FAILED", PIPELINE_ID)')
    lines.append("    try:")
    lines.append('        shell(f"wfc pipeline-summary --pipeline-id {PIPELINE_ID}")')
    lines.append("    except Exception:")
    lines.append('        _pipeline_logger.exception("pipeline-summary command failed")')
    lines.append("")

    return "\n".join(lines)
