"""The pipeline model: one step, one pipeline definition.

``StepDef`` is one method node as the engine sees it after the load;
``PipelineDef`` is the whole document -- steps, samples, named param
variants and the optional explicit combinations. Both are plain data;
every derivation over them lives beside them in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepDef:
    """One step in the pipeline DAG.

    ``node_id`` is the unique identity within a pipeline.  For legacy
    pipelines (integer node IDs, one method per node) it defaults to
    ``method_name``.  Pipelines with string IDs, or with a method reused
    across nodes, set it explicitly.

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
    env: str = ""  # required typed env spec / named shared env; "" is an
    # unset sentinel — load_pipeline always sets this explicitly and raises
    # when a node declares no env.
    slot_outputs: dict[str, str] = field(default_factory=dict)
    # named output slots → filename, e.g. {"predictions": "predictions.csv", "model": "model.pkl"}
    # empty dict means single-output method (uses output{ext} path)
    slot_types: dict[str, str] = field(default_factory=dict)
    # named output slots → type string (e.g. "CSV", "JSON", "directory"):
    # the single source of truth for directory-slot detection.  Populated by
    # enrichment from MethodContract.output_slots and parsed through to
    # run_step via the pipeline JSON.
    input_source_slots: dict[str, list[str | None]] = field(default_factory=dict)
    # target_slot → [source_slot per upstream_id]; None = use upstream's default/primary output
    # e.g. for plot_decision_boundary: {"data": ["predictions"], "model": ["model"]}
    run_ref_inputs: dict[str, list[str]] = field(default_factory=dict)
    # Named static input paths from run_reference nodes.
    # Maps a slot label to the LIST of artifact paths wired into it, in
    # document order. The label is the canvas link's ``target_slot`` — so the
    # downstream method reads the artifacts under the same slot name its
    # contract declares — with a ``run_ref_{i}`` fallback for untyped legacy
    # edges (one synthetic label per untyped link). Several references may
    # target one slot: each contributes another path to that label's list,
    # which is emitted as one ``--ref-input <slot>=<path>`` occurrence each
    # and accumulated back into a single slot by the materialize phase. Same
    # shape as ``inputs`` (slot → many upstreams), so method fan-in and
    # reference fan-in agree. These are concrete paths (no wildcards)
    # injected as additional Snakemake rule inputs alongside wildcard-based
    # upstream deps.
    sample_collapsed: bool = False
    # True when this step's sample axis has been collapsed to a single
    # bundled run (driven by an upstream input_selector with fan_mode="in").
    # Collapsed steps run once per variant -- not once per (sample, variant) --
    # and use COLLAPSED_SAMPLE (``__all__``) as their sample segment in paths
    # and runs.
    # Collapse is contagious: any step with a collapsed upstream is itself
    # collapsed (re-fan-out is not supported).
    collapsed_samples: list[str] = field(default_factory=list)
    # The sample list bundled into this collapsed step. Populated from the
    # originating input_selector's samples. Only meaningful when
    # sample_collapsed is True; empty otherwise.
    selector_slot: str | None = None
    # The input slot this step reads its sample(s) into, stamped by the load
    # from the document's selector wiring (``wfc.graph.selector_slot``) -- the
    # same answer the claim and materialize classify from. ``None`` when no
    # input_selector feeds the step, whatever else does.

    def __post_init__(self):
        if not self.node_id:
            self.node_id = self.method_name
        # Derive inputs from depends_on when not explicitly set
        if not self.inputs and self.depends_on:
            self.inputs = {"data": list(self.depends_on)}


def reads_per_sample(step: StepDef) -> bool:
    """Whether a step reads its own target's sample from ``data/samples/``.

    A step a per-sample selector feeds reads the sample the target runs
    over, beside whatever upstream outputs and references it also takes, so
    the sample has to be restored before the step runs. A step that carries
    a fan-in bundle reads every bundled sample instead (see
    :func:`carries_sample_bundle`).

    Args:
        step: The loaded step definition.

    Returns:
        True when the load stamped a selector slot and the step does not
        carry a bundle.
    """
    return step.selector_slot is not None and not carries_sample_bundle(step)


def carries_sample_bundle(step: StepDef) -> bool:
    """Whether a step is handed the fan-in bundle as run-step arguments.

    Only the step wired *directly* to a fan-in ``input_selector`` carries
    the bundle. It has no method upstream, so the selector's sample list is
    the only way it can learn which samples it runs over, and the generator
    hands that list to ``wfc run-step`` as one ``--collapsed-sample`` flag
    per member.

    A step further down the chain is collapsed too (collapse is contagious)
    and inherits ``collapsed_samples`` for path and variant purposes, but it
    reads its inputs from its upstream's collapsed outputs, and the bundle
    reaches its cache key through that upstream's ``Run.cache_key``. Handing
    it the sample list again would put the same information in the key
    twice, on one side of a fence only.

    Every place that decides who gets the bundle reads this predicate: the
    generator's sentinel ``input:`` block, the generator's shell flags, and
    the test harness's direct ``run_step`` driver. A harness scenario is
    only evidence about production's cache key while all three agree.

    Args:
        step: The loaded step definition.

    Returns:
        True when the step takes the bundle directly off a fan-in selector.
    """
    return bool(
        step.sample_collapsed and not step.depends_on and step.collapsed_samples
    )


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
