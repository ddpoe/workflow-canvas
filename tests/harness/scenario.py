"""Scenario declaration: plain dataclasses plus preset constructors.

A test states topology, samples, variants, per-method behavior and starting
state; nothing here touches the filesystem or the database. Defaults carry
the small cases — ``Scenario()`` with no arguments is one selector-rooted
method node, one sample, one variant, a method exiting zero, and no prior
state — so a declaration is only as large as the thing being varied.

The presets return ordinary data the caller keeps modifying: :func:`chain`,
:func:`fan_in_nodes`, :func:`selector_beside_method` and
:func:`selector_beside_method_and_reference` return node lists, :func:`fan_in`
returns input wiring, :func:`two_node_chain` returns a whole :class:`Scenario`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

#: Name of the container env every harness node binds to by default.
DEFAULT_ENV_NAME = "harness-env"
#: Module every harness method is registered under by default.
DEFAULT_MODULE = "harness_mod"
DEFAULT_SAMPLE = "s1"
DEFAULT_VARIANT = "default"
#: Node id of the selector root the presets install.
SELECTOR_ID = "sel"


@dataclass(frozen=True)
class Wire:
    """One link from an upstream node into one of this node's input slots.

    Attributes:
        source: Upstream node id.
        source_slot: Which of the upstream node's declared output slots
            feeds this link. ``None`` names no output, and a link that
            names none takes the referenced run's only output — and fails
            the load when that run has several.
        target_slot: The input slot this node receives the data on.
        bundle: When True the upstream selector collapses the sample axis
            (``fan_mode="in"``), so this node runs once over all samples
            with ``sample="__all__"``.
    """

    source: str
    source_slot: str | None = "data"
    target_slot: str = "data"
    bundle: bool = False


@dataclass(frozen=True)
class Behavior:
    """What the node's method process does when it runs.

    Attributes:
        exit_code: Process exit status.
        outputs: Slot name -> file content. A directory slot may instead
            map to ``{filename: content}`` for its children. Slots not
            named here are written with generated content; use
            ``skip_slots`` to declare a slot the method deliberately fails
            to produce.
        echo_input: Input slot the method reads. Its first resolved path's
            content becomes the content of every output slot with no
            declared content, and an empty slot exits 2 — the method
            actually consuming its input, which is what a wiring test
            needs to observe.
        concat_input: Input slot the method consumes *in full*: every
            resolved path in the slot is read and their CSV bodies are
            concatenated under the first file's header. Distinct from
            ``echo_input``, which reads only the first path — a fan-in
            bundle delivering all of its members can only be proven by a
            method that actually reads all of them, since reading the
            first one succeeds whether the bundle carried one path or
            twenty. An empty slot exits 2.
        read_inputs: Input slots the method requires to have arrived: for
            each, every resolved path must exist where the method runs,
            and an empty slot or a missing path exits 2 naming the slot.
            A node reading several slots can only be shown to have
            received them all by a method that looks at each one; on the
            engine rung, where the harness observes no dispatch, the
            method's own exit status is that observation.
        skip_slots: Declared output slots the method does NOT write.
        metrics: Metrics recorded into ``_wfc_results.json``. An empty
            mapping still writes the manifest.
        write_manifest: When False no ``_wfc_results.json`` is written at
            all (the pure outputs-only Tier-2 shape).
        stdout: Text the method prints to stdout.
        stderr: Text the method prints to stderr (lifted into the run row's
            error fields on a non-zero exit).
        nested_outputs: Slots written under ``_workdir/`` and recorded in
            the manifest by their run-dir-relative path rather than at the
            top of the run dir.
        saved_files: Slot name -> the ``_workdir``-relative path the method
            saves that slot under, recorded in the manifest the way
            ``ctx.save_artifact(slot, path)`` records it. The file name is
            the method's own and need not match the document's declared
            file name for the slot, which is the shape that proves an
            output is found by its slot rather than by a file name.
        delete_paths: Project-relative paths the method deletes before
            exiting — fault injection at a real boundary.
        raises: When set, the method ends by raising
            ``RuntimeError(<text>)`` uncaught, so the interpreter writes a
            real traceback to stderr and the process exits non-zero. A
            crash is not the same event as "wrote to stderr and exited",
            and a test about capturing a crash needs the real thing.
        launch_error: When set, the process never starts: the stub rung's
            substituted process boundary raises ``OSError(<text>)`` in
            place of launching, which is the dispatch phase's
            ``launch-failure`` ending. Every other behavior field describes
            what the method does once running; this one describes the
            launch refusing, an event a script cannot play. Stub rung only
            -- the engine rung installs nothing at the boundary.
    """

    exit_code: int = 0
    outputs: Mapping[str, Any] | None = None
    echo_input: str | None = None
    concat_input: str | None = None
    read_inputs: tuple[str, ...] = ()
    skip_slots: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    write_manifest: bool = True
    stdout: str = ""
    stderr: str = ""
    nested_outputs: tuple[str, ...] = ()
    saved_files: Mapping[str, str] | None = None
    delete_paths: tuple[str, ...] = ()
    raises: str | None = None
    launch_error: str | None = None


@dataclass
class NodeSpec:
    """One node in the declared pipeline.

    Attributes:
        id: Node identity within the pipeline.
        method: Method name. Defaults to ``id``.
        type: ``"method"``, ``"input_selector"`` or ``"run_reference"``.
        module: Owning module name.
        env: Env the node binds to — the bare name of a manifest-backed
            env, written to ``method.yaml`` as-is. Defaults to the
            scenario's env.
        inputs: Incoming wiring.
        outputs: Declared output slot -> type string (``".csv"``, and
            ``"directory"`` for a directory slot).
        output_files: Declared output slot -> the published filename.
            Defaults to ``<slot>.csv``. Naming a file that differs from
            its slot is what proves the *declared filename* — not the slot
            name — is the source of truth for the published artifact.
        input_columns: Input slot -> its ``columns`` block (``strict``,
            ``from_params``, ``patterns``), written verbatim beneath the
            slot's declaration in ``method.yaml``. Registration stores the
            block and the load-time cross-check reads it; the stub script
            never consults it, so it is a declaration the harness carries
            through and nothing the harness interprets.
        output_columns: Output slot -> its ``columns`` block, written
            verbatim beneath the slot's declaration in ``method.yaml`` the
            same way. Registration stores it on the contract and the
            canvas's columns route resolves it; the harness interprets
            nothing.
        params: Node-level default params.
        behavior: What the method process does.
        behavior_by_sample: Per-sample override of ``behavior``, applied
            wholesale for the named sample. The sample axis is a real axis
            of a pipeline and a partial-prune scenario is exactly one
            sample's method failing where another sample's succeeds, so a
            declaration that cannot vary behavior along it cannot state
            the scenario at all.
        behavior_by_variant: Per-variant override of ``behavior``, applied
            wholesale for the named variant -- the variant axis, declared
            the way the sample axis is. A node declares at most one of the
            two override maps; ``build_project`` refuses both, since no
            scenario has yet needed a precedence rule between them.
        samples: For ``input_selector`` nodes, the sample list the selector
            supplies.
        fan_mode: For ``input_selector`` nodes, ``"in"`` collapses the
            sample axis for everything downstream.
        label: Canvas label; ``Run.nid`` is stamped from it.
        executor: Engine the method declares in ``method.yaml``.
            ``"slurm"`` is the carve-out dispatch rejects.
        gpus: Whether the method declares a GPU requirement, which dispatch
            plumbs through to the container runtime.
        script_name: The script filename the pipeline document names for
            this node. Defaults to ``<method>.py``. Naming a file that is
            not there is what reaches the dispatch phase's host-side
            script pre-flight: the method directory still holds a real
            script, so the claim phase's source-directory check passes and
            the node gets as far as dispatch.
        document_env: The env the pipeline *document* names for this node
            when it should differ from the one ``method.yaml`` declares --
            the ``script_name`` pattern applied to the env. Registration
            validates ``method.yaml``'s env against the manifest, so a
            document that names an env no manifest entry backs is how the
            dispatch phase's ``no-container`` ending is reached: the
            method registers, the claim passes, and the manifest lookup
            dispatch makes off the document misses. ``None`` (the default)
            writes ``env`` to the document unchanged.
        run_id: For ``run_reference`` nodes, the run the reference names.
            Written to the document verbatim, so a run id that resolves to
            nothing is how the unresolvable-reference case is declared.
        run_of: For ``run_reference`` nodes, a ``(node, sample, variant)``
            locator — or a bare node id — naming the run the reference
            resolves to: a seeded prior run of this scenario, or, with
            ``run_of_pipeline``, a run another scenario over the same root
            produced under that pipeline id. The binding happens after the
            prior runs have executed and before the document is read, so
            the run id in the document is one a real run produced rather
            than a fabricated number.
        run_of_pipeline: The pipeline id the ``run_of`` locator is read
            under; ``None`` means this scenario's own pipeline. Names the
            sentinel tree production wrote for that pipeline, so a
            reference across pipelines binds to the run the other pipeline
            recorded rather than to an id a test handed it.
    """

    id: str
    method: str | None = None
    type: str = "method"
    module: str = DEFAULT_MODULE
    env: str | None = None
    inputs: list[Wire] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=lambda: {"data": ".csv"})
    output_files: dict[str, str] = field(default_factory=dict)
    input_columns: dict[str, dict] = field(default_factory=dict)
    output_columns: dict[str, dict] = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    behavior: Behavior = field(default_factory=Behavior)
    behavior_by_sample: dict[str, Behavior] = field(default_factory=dict)
    behavior_by_variant: dict[str, Behavior] = field(default_factory=dict)
    samples: list[str] | None = None
    fan_mode: str | None = None
    label: str | None = None
    executor: str = "local"
    gpus: bool = False
    script_name: str | None = None
    document_env: str | None = None
    run_id: str | None = None
    run_of: str | tuple[str, str, str] | None = None
    run_of_pipeline: str | None = None

    @property
    def run_of_target(self) -> tuple[str, str, str] | None:
        """The ``run_of`` locator as a full ``(node, sample, variant)``."""
        if self.run_of is None:
            return None
        if isinstance(self.run_of, str):
            return (self.run_of, DEFAULT_SAMPLE, DEFAULT_VARIANT)
        return tuple(self.run_of)  # type: ignore[return-value]

    @property
    def method_name(self) -> str:
        """The method name this node executes (``id`` when unset)."""
        return self.method or self.id

    def behavior_for(self, sample: str, variant: str) -> Behavior:
        """Return the behavior this node's process plays for one target.

        The one resolution rule, shared with the generated script: the
        sample override when the sample is named, else the variant
        override when the variant is named, else the node's default.

        Args:
            sample: The target's sample identifier.
            variant: The target's variant name.

        Returns:
            The behavior to play.
        """
        if sample in self.behavior_by_sample:
            return self.behavior_by_sample[sample]
        if variant in self.behavior_by_variant:
            return self.behavior_by_variant[variant]
        return self.behavior


@dataclass(frozen=True)
class PriorRun:
    """A completed run to seed before the scenario executes.

    The seeded run is produced by actually running the target, so the
    preconditions a later phase sees are ones a run really produced.

    Attributes:
        node: Node id to run.
        sample: Sample identifier.
        variant: Variant name.
        archive_deleted: Delete the seeded run's archive directory
            afterwards — the deleted-archive starting state.
        sentinel_deleted: Delete the seeded run's sentinel (and its
            ``run_id.txt`` sidecar) afterwards, leaving the row behind.
    """

    node: str
    sample: str = DEFAULT_SAMPLE
    variant: str = DEFAULT_VARIANT
    archive_deleted: bool = False
    sentinel_deleted: bool = False


@dataclass(frozen=True)
class ModuleSpec:
    """What a module row carries beyond the methods that name it.

    Registration upserts the module on every method registration, replacing
    its contracts with the list given, so a scenario that needs module-level
    contracts or a description states them here, keyed by module name in
    :attr:`Scenario.modules`, and ``build_project`` hands them to
    production's ``register_module`` on each registration.

    Attributes:
        description: The module's human-readable description. ``None``
            leaves whatever registration wrote (an existing row keeps its
            description; a new one has none).
        contracts: Module-level contract dicts in the shape
            ``register_module`` accepts (``type``, ``name``, ``value_type``,
            ``required``).
        demo_owned: Register the module and its methods the way ``wfc
            demo`` registers its own, opting in to the reserved
            ``__demo__`` prefix. Only such a module can carry that prefix,
            and only its runs are what ``wfc demo --remove`` deletes.
    """

    description: str | None = None
    contracts: tuple[dict, ...] = ()
    demo_owned: bool = False


@dataclass
class Scenario:
    """The whole declaration: topology, samples, variants, starting state.

    Attributes:
        nodes: The pipeline's nodes. Defaults to a selector root feeding
            one method node.
        samples: Sample identifiers.
        variants: Variant name -> params, applied to every method node.
            ``None`` means the single implicit ``"default"`` variant.
        prior_runs: Runs to seed before the scenario proper executes.
        pipeline_id: Pipeline execution id.
        name: Pipeline name (drives the pipeline JSON filename).
        env_name: Container env name every node binds to by default.
        env_backend: Backend the env record declares (``"byo"``,
            ``"pixi"`` or ``"conda"``). The backend is what decides the
            container-side interpreter when the record does not name one.
        env_legacy_no_python: Write the env record without a ``python``
            key at all — the shape of records written before the field
            existed, which dispatch must resolve via the per-backend
            default.
        dirty_repo: Leave an uncommitted file in the working tree so
            ``pre_run``'s clean-tree check fires.
        sample_content: Content written into each sample's data file, and
            the bytes its ``samples`` row is content-hashed from. Three
            forms. ``None`` (the default) derives content from the sample
            name, so every sample holds distinct bytes and a
            content-addressed row per sample carries a distinct
            ``hash:`` part — without that, a same-size membership swap
            between two bundles computes one key and the cache-key axis
            stays invisible. A single ``str`` writes those exact bytes
            into every sample, which is what a test asserting on merged
            row counts needs. A mapping states content per sample name;
            names it does not list fall back to the derived default, and
            a name that is not a declared sample raises rather than
            silently writing nothing.
        empty_samples: Samples whose data directory is created but left
            empty — the starting state a collapsed fan-in's per-sample
            error is asked about.
        missing_samples: Samples whose data directory is not created at
            all. The sibling starting state of ``empty_samples``: a
            collapsed fan-in names both kinds of offender, and only
            declaring both proves it names each for its own reason.
        sample_ready_sentinel: Also drop the ``.sample_ready`` dotfile the
            DVC restore rule leaves in each sample directory. The runtime
            collapsed-fan-in resolver walks the directory and must skip
            dotfiles; without the sentinel present there is nothing for it
            to skip, so a resolver that stopped skipping them would go
            unnoticed.
        image_digest: Bare sha256 hex the env record's container ref is
            pinned to. ``None`` uses the stub placeholder, whose shape is
            valid but which no runtime can pull. A test that builds the
            project and then drives real containers itself declares the
            session-built image's digest here so the record is written
            before the project is committed and the clean-tree check
            still passes.
        modules: Module name -> :class:`ModuleSpec` for any module whose
            row needs a description or module-level contracts. A module a
            node names but this mapping does not is registered with no
            description and an empty contract list, as before.
    """

    nodes: list[NodeSpec] = field(default_factory=lambda: list(_default_nodes()))
    samples: list[str] = field(default_factory=lambda: [DEFAULT_SAMPLE])
    variants: dict[str, dict] | None = None
    prior_runs: list[PriorRun] = field(default_factory=list)
    pipeline_id: str = "harness-pipeline"
    name: str = "harness"
    env_name: str = DEFAULT_ENV_NAME
    env_backend: str = "byo"
    env_legacy_no_python: bool = False
    dirty_repo: bool = False
    sample_content: str | Mapping[str, str] | None = None
    empty_samples: tuple[str, ...] = ()
    missing_samples: tuple[str, ...] = ()
    sample_ready_sentinel: bool = False
    image_digest: str | None = None
    modules: Mapping[str, ModuleSpec] = field(default_factory=dict)

    def method_nodes(self) -> list[NodeSpec]:
        """Return the scenario's method nodes in declaration order."""
        return [n for n in self.nodes if n.type == "method"]

    def content_for(self, sample: str) -> str:
        """Return the content declared for one sample.

        The single rule both staging and row-recording read, so the
        bytes written to ``data/samples/`` and the bytes the row is
        hashed from cannot drift apart.

        Args:
            sample: The sample name.

        Returns:
            The content declared for that sample.

        Raises:
            KeyError: When ``sample_content`` is a mapping naming a
                sample this scenario does not declare — a typo'd name
                would otherwise fall back to derived content and the
                test would quietly stop testing what it says.
        """
        declared = self.sample_content
        if isinstance(declared, str):
            return declared
        if declared is not None:
            unknown = set(declared) - set(self.samples)
            if unknown:
                raise KeyError(
                    f"sample_content names {sorted(unknown)}, which "
                    f"{'is' if len(unknown) == 1 else 'are'} not among the "
                    f"scenario's samples {list(self.samples)}"
                )
            if sample in declared:
                return declared[sample]
        # Derived default: same shape as a one-row CSV, distinct per
        # sample so content-addressed rows carry distinct hashes.
        return f"id,value\n1,{sample}\n"

    def node_by_id(self, node_id: str) -> NodeSpec:
        """Look up one node by id.

        Honours the executor's identity rule: the node's own id first,
        else the method a node names -- the identity a legacy numeric-id
        document's steps are scheduled under.

        Args:
            node_id: The node identity to find: an id, or a method name.

        Returns:
            The matching :class:`NodeSpec`.

        Raises:
            KeyError: When no node carries that id and no one node names
                that method.
        """
        for n in self.nodes:
            if n.id == node_id:
                return n
        by_method = [n for n in self.nodes if n.method == node_id]
        if len(by_method) == 1:
            return by_method[0]
        raise KeyError(f"no node {node_id!r} in scenario (have "
                       f"{[n.id for n in self.nodes]})")


# =============================================================================
# Declaration helpers
# =============================================================================

def node(node_id: str, **kwargs) -> NodeSpec:
    """Construct one method node.

    Args:
        node_id: Node identity.
        **kwargs: Any :class:`NodeSpec` field.

    Returns:
        The node spec.
    """
    return NodeSpec(id=node_id, **kwargs)


def selector(node_id: str = SELECTOR_ID, *, fan_mode: str | None = None,
             samples: list[str] | None = None) -> NodeSpec:
    """Construct an ``input_selector`` root node.

    Args:
        node_id: Node identity.
        fan_mode: ``"in"`` collapses the sample axis downstream.
        samples: Explicit sample list carried on the selector.

    Returns:
        The selector node spec.
    """
    return NodeSpec(id=node_id, type="input_selector", outputs={},
                    fan_mode=fan_mode, samples=samples)


def reference(node_id: str, *,
              run_id: str | None = None,
              run_of: str | tuple[str, str, str] | None = None,
              pipeline: str | None = None) -> NodeSpec:
    """Construct a ``run_reference`` root node.

    A reference is the run it names, and its outgoing links name which of
    that run's outputs they draw. A reference root is what suppresses the
    sample fallback: a method node wired only to one of these has no
    incoming selector edge, so its slot is filled from the referenced run's
    artifact rather than from ``data/samples/``.

    Args:
        node_id: Node identity.
        run_id: The run the reference names, written verbatim.
        run_of: A run to bind to — ``(node, sample, variant)`` or a bare
            node id — read under this scenario's pipeline, or under
            ``pipeline`` when given. Supplies the run id from the run that
            actually executed.
        pipeline: The pipeline id the locator is read under, for a run
            another scenario over the same root produced. ``None`` means
            this scenario's own pipeline.

    Returns:
        The reference node spec.
    """
    return NodeSpec(id=node_id, type="run_reference", outputs={},
                    run_id=run_id, run_of=run_of, run_of_pipeline=pipeline)


def wire(source: str, *, source_slot: str | None = "data",
         target_slot: str = "data", bundle: bool = False) -> Wire:
    """Construct one input wiring entry.

    Args:
        source: Upstream node id.
        source_slot: Upstream output slot feeding the link.
        target_slot: This node's receiving input slot.
        bundle: Collapse the sample axis over this link.

    Returns:
        The wiring entry.
    """
    return Wire(source=source, source_slot=source_slot,
                target_slot=target_slot, bundle=bundle)


def fan_in(*sources: str, source_slot: str | None = "data",
           target_slot: str = "data", bundle: bool = False) -> list[Wire]:
    """Wire several upstream nodes into one input slot.

    Args:
        *sources: Upstream node ids, in the order they should arrive.
        source_slot: Upstream output slot feeding each link.
        target_slot: The receiving input slot.
        bundle: Collapse the sample axis (a bundled fan-in).

    Returns:
        One wiring entry per source, in the supplied order.
    """
    return [wire(s, source_slot=source_slot, target_slot=target_slot,
                 bundle=bundle) for s in sources]


def exits(code: int, **kwargs) -> Behavior:
    """Declare a method that exits with a given status.

    Args:
        code: Process exit status.
        **kwargs: Any other :class:`Behavior` field.

    Returns:
        The behavior spec.
    """
    return Behavior(exit_code=code, **kwargs)


def completed(node_id: str, *, sample: str = DEFAULT_SAMPLE,
              variant: str = DEFAULT_VARIANT, **kwargs) -> PriorRun:
    """Declare a prior completed run to seed.

    Args:
        node_id: Node id to run ahead of the scenario.
        sample: Sample identifier.
        variant: Variant name.
        **kwargs: Any other :class:`PriorRun` field.

    Returns:
        The prior-run spec.
    """
    return PriorRun(node=node_id, sample=sample, variant=variant, **kwargs)


# =============================================================================
# Presets
# =============================================================================

def chain(*node_ids: str, selector_id: str = SELECTOR_ID,
          fan_mode: str | None = None) -> list[NodeSpec]:
    """Build a selector root plus a head-to-tail node sequence.

    Args:
        *node_ids: Node ids in execution order. The first is wired to the
            selector; each subsequent node is wired to its predecessor.
        selector_id: Node id for the selector root.
        fan_mode: ``"in"`` on the selector collapses the sample axis.

    Returns:
        The selector node followed by the chained method nodes.
    """
    nodes: list[NodeSpec] = [selector(selector_id, fan_mode=fan_mode)]
    upstream = selector_id
    for nid in node_ids:
        nodes.append(node(nid, inputs=[wire(upstream)]))
        upstream = nid
    return nodes


def fan_in_nodes(*sources: str, sink: str = "merge",
                 selector_id: str = SELECTOR_ID,
                 bundle: bool = False) -> list[NodeSpec]:
    """Build several selector-rooted producers feeding one sink node.

    Args:
        *sources: Producer node ids.
        sink: The node id every producer feeds.
        selector_id: Node id for the shared selector root.
        bundle: Collapse the sample axis over the fan-in.

    Returns:
        The selector, the producers, and the sink.
    """
    nodes: list[NodeSpec] = [
        selector(selector_id, fan_mode="in" if bundle else None)
    ]
    for src in sources:
        nodes.append(node(src, inputs=[wire(selector_id)]))
    nodes.append(node(sink, inputs=fan_in(*sources, bundle=bundle)))
    return nodes


def selector_beside_method(consumer: str = "quantify", upstream: str = "segment",
                           *, selector_id: str = SELECTOR_ID,
                           sample_slot: str = "raw",
                           upstream_slot: str = "mask") -> list[NodeSpec]:
    """Build a node that reads its sample beside an upstream method's output.

    The raw image plus a mask from an earlier step: the selector feeds both
    the upstream and, on its own slot, the consumer, which also takes the
    upstream's output on a second slot.

    Args:
        consumer: The node reading both inputs.
        upstream: The method node whose output the consumer also reads.
        selector_id: Node id for the per-sample selector.
        sample_slot: The consumer's slot the sample arrives on.
        upstream_slot: The consumer's slot the upstream's output arrives on.

    Returns:
        The selector, the upstream and the consumer.
    """
    return [
        selector(selector_id),
        node(upstream, inputs=[wire(selector_id)]),
        node(consumer, inputs=[wire(selector_id, target_slot=sample_slot),
                               wire(upstream, target_slot=upstream_slot)]),
    ]


def selector_beside_method_and_reference(
        consumer: str = "quantify", upstream: str = "segment", *,
        source: str = "train", reference_id: str = "model",
        selector_id: str = SELECTOR_ID, sample_slot: str = "raw",
        upstream_slot: str = "mask",
        reference_slot: str = "model") -> list[NodeSpec]:
    """Build a node that reads its sample, an upstream output and a reference.

    :func:`selector_beside_method` plus a ``run_reference`` onto a third
    slot. The reference names a run of ``source``, a selector-fed node of
    the same pipeline, so the scenario seeds that run with
    ``prior_runs=[completed(source)]`` and the referenced run's sample is
    one of the selector's samples.

    Args:
        consumer: The node reading all three inputs.
        upstream: The method node whose output the consumer reads.
        source: The node whose seeded run the reference names.
        reference_id: Node id for the reference.
        selector_id: Node id for the per-sample selector.
        sample_slot: The consumer's slot the sample arrives on.
        upstream_slot: The consumer's slot the upstream's output arrives on.
        reference_slot: The consumer's slot the referenced artifact arrives on.

    Returns:
        The selector, the upstream, the source, the reference and the
        consumer.
    """
    nodes = selector_beside_method(consumer, upstream, selector_id=selector_id,
                                   sample_slot=sample_slot,
                                   upstream_slot=upstream_slot)
    consumer_spec = nodes.pop()
    consumer_spec.inputs.append(
        wire(reference_id, source_slot=None, target_slot=reference_slot))
    return nodes + [
        node(source, inputs=[wire(selector_id)]),
        reference(reference_id, run_of=source),
        consumer_spec,
    ]


def two_node_chain(**kwargs) -> Scenario:
    """A selector root feeding a two-node chain.

    Args:
        **kwargs: Any :class:`Scenario` field to override.

    Returns:
        An ordinary scenario the caller can keep modifying.
    """
    return Scenario(nodes=chain("head", "tail"), **kwargs)


def _default_nodes() -> list[NodeSpec]:
    """The no-argument scenario: a selector root and one method node."""
    return chain("n1")


__all__ = [
    "DEFAULT_ENV_NAME", "DEFAULT_MODULE", "DEFAULT_SAMPLE", "DEFAULT_VARIANT",
    "SELECTOR_ID", "Behavior", "NodeSpec", "PriorRun", "Scenario", "Wire",
    "chain", "completed", "exits", "fan_in", "fan_in_nodes", "node", "reference",
    "replace", "selector", "selector_beside_method",
    "selector_beside_method_and_reference", "two_node_chain", "wire",
]
