"""Materialize phase: classify input sources and resolve them to local paths.

The classification is pure (pipeline document in, per-slot source kind out);
``run_materialize`` acts only on the classification. Every failure returns
the ``materialize-failed`` ending with its message, and the record tail
marks the run row failed with that message.

A selector slot reads the sample's data where Storage's
``sample_data_path`` says restore put it (the path its registration
recorded), never by picking an entry from a listing.

The node's inbound wiring is Graph's answer (``wfc.graph.inbound_wiring``).
Each parent-slot source resolves through Storage's ``resolve_input``.
"""
from __future__ import annotations

from axiom_annotations import task, Step, AutoStep
from ..graph import selector_slot as graph_selector_slot

from ..contracts import COLLAPSED_SAMPLE
from ..persistence import project_root as get_project_root
from .parents import parse_parent_entries

#: The step ending every materialize failure returns to the record tail.
MATERIALIZE_FAILED = "materialize-failed"


def _failed(message: str) -> dict:
    """Return a materialize failure carrying its message for the run row.

    Args:
        message: What failed, as the run row and the console will show it.

    Returns:
        The failure result ``run_step`` hands to the record tail.
    """
    return {"ok": False, "rc": 1, "ending": MATERIALIZE_FAILED,
            "error_message": message}


def classify_input_sources(
    pipeline_data: dict | None,
    node_id: str,
    parent_run_ids: list | None,
    ref_inputs: list[str] | None,
) -> dict:
    """Classify where each of a node's input slots gets its data from.

    Pure function: pipeline document in, per-slot classification out — no
    I/O. Each input slot is fed by exactly one of three source kinds:

    - **parent-slot** — a wired parent run's output; one per parent entry
      (``input:output:run`` or ``input:run``).
    - **sample-via-selector** — the registered sample directory, fired when
      an incoming ``input_selector`` edge exists, whatever parents or
      references also feed the node; or, with no pipeline document at all,
      when the node has no parent runs (a legacy drive keeps the root sample
      behavior).
    - **reference** — an orchestrator-resolved ``--ref-input`` artifact;
      one entry per ``label=path`` flag. References coexist with parents
      (classification is per-slot, not per-node).

    Args:
        pipeline_data: The parsed pipeline JSON document, or ``None`` when
            no pipeline document is available.
        node_id: The node whose input slots are being classified.
        parent_run_ids: Parent entries (the sidecar walk's output, or the
            entries typed on the command line).
        ref_inputs: Raw ``label=path`` ref-input flags.

    Returns:
        A dict with three keys:

        - ``"parents"``: ordered ``ParentEntry`` values parsed from
          ``parent_run_ids`` by the shared parser.
        - ``"selector_slot"``: the slot name to fill from the sample
          directory, or ``None`` when no selector edge feeds the node (or,
          with no document, when parents are present).
        - ``"references"``: ordered ``[(label, path), ...]`` parsed from
          ``ref_inputs`` (entries without ``=`` are skipped).

    Raises:
        ValueError: A parent entry is malformed, or two entries wire
            different outputs of one run into one input slot.
    """
    parents = parse_parent_entries(parent_run_ids or [], step=node_id)

    # Sample-via-selector: the document's selector edge alone decides,
    # whatever parents or references also feed the node -- Graph's one
    # answer, which the load also stamps on the step for the emitter. A
    # reference-rooted node has no selector edge, so it reads no sample.
    # With no document there is no edge to read: a legacy drive treats a
    # parentless node as a root reading its sample into ``data``.
    if pipeline_data is None:
        selector_slot: str | None = "data" if not parents else None
    else:
        selector_slot = graph_selector_slot(pipeline_data, node_id)

    references: list[tuple[str, str]] = []
    for entry in ref_inputs or []:
        if "=" not in entry:
            continue
        label, ref_path = entry.split("=", 1)
        references.append((label, ref_path))

    return {
        "parents": parents,
        "selector_slot": selector_slot,
        "references": references,
    }


@task(purpose="Materialize phase: turn the per-slot input-source classification "
              "into resolved local paths",
      inputs="pipeline document, parent entries (input:output:run), "
             "ref-input flags, collapsed fan-in samples",
      outputs="slot→paths map, or the materialize-failed ending with its "
              "message for the record tail")
def run_materialize(
    node_id: str,
    sample: str,
    pipeline_data: dict | None,
    parent_run_ids: list,
    ref_inputs: list[str] | None,
    collapsed_samples: list[str] | None,
) -> dict:
    """Resolve every input slot's data to local paths.

    Args:
        node_id: The node being executed.
        sample: Sample identifier (``COLLAPSED_SAMPLE`` for collapsed
            fan-in roots).
        pipeline_data: Parsed pipeline JSON document, or ``None``.
        parent_run_ids: Parent entries (``"input:output:run"`` or
            ``"input:run"``). A bare run id is refused -- every entry
            names the input slot it feeds.
        ref_inputs: Raw ``label=path`` ref-input flags.
        collapsed_samples: Bundled sample identities for collapsed fan-in.

    Returns:
        ``{"ok": True, "slot_paths": {...}}`` on success, or
        ``{"ok": False, "rc": 1, "ending": "materialize-failed",
        "error_message": ...}``; ``run_step`` hands the failure to the
        record tail, which marks the run row failed with the message and
        prints it.
    """
    from ..storage.resolve import resolve_input  # from its defining module, so the AutoStep edge resolves
    from ..storage import (
        InputUnavailableError,
        MalformedEntryError,
        MalformedSampleRecordError,
        sample_data_path,
    )

    口 = Step(step_num=1, name="Classify input sources",
             purpose="Derive each input slot's source kind (parent-slot / "
                     "sample-via-selector / reference) from the pipeline document "
                     "and the parent entries, each carrying the input slot, the "
                     "source slot it names and the run id")
    try:
        input_sources = classify_input_sources(
            pipeline_data, node_id, parent_run_ids, ref_inputs
        )
    except ValueError as exc:
        return _failed(str(exc))
    # For fan-in nodes with multiple parents we build a slot→paths dict so
    # method.py can dispatch via WFC_INPUT_PATHS.
    slot_paths: dict[str, list[str]] = {}  # slot → [resolved_path, ...]

    口 = Step(step_num=2, name="Resolve parent slots",
             purpose="Resolve each parent entry through resolve_input by the source "
                     "slot the entry names (no slot: the run's only output); an "
                     "unresolvable parent slot is a failure naming the slot and "
                     "the parent run, and an output that is in neither place "
                     "(a gone pre-archive file, or an archived entry neither "
                     "local nor pushed, or a pull that failed) or a malformed "
                     "cache entry also carries the resolver's reason (the path "
                     "or content hash, and the re-run) onto the run row")
    entry = None
    try:
        for entry in input_sources["parents"]:
            口 = AutoStep(step_num=2.1, name="Resolve the parent's output")
            resolved = resolve_input(run_id=entry.run_id, slot=entry.source_slot)
            if not resolved:
                hint = (
                    f" If run {entry.run_id} has several outputs, name the one "
                    f"to use in the entry "
                    f"{entry.input_slot}:<output>:{entry.run_id}."
                    if entry.source_slot is None else ""
                )
                return _failed(
                    f"node '{node_id}' could not resolve input slot "
                    f"'{entry.input_slot}' from parent run {entry.run_id} (the "
                    f"resolver's reason is in the step log). Refusing to run "
                    f"with a missing input.{hint}"
                )
            slot_paths.setdefault(entry.input_slot, []).append(str(resolved))
    except (InputUnavailableError, MalformedEntryError) as exc:
        return _failed(
            f"node '{node_id}' could not resolve input slot "
            f"'{entry.input_slot}' from parent run {entry.run_id}: {exc} "
            f"Refusing to run with a missing input."
        )

    口 = Step(step_num=3, name="Resolve selector sample data",
             purpose="Fill a sample-via-selector slot with the sample's data by "
                     "the name its registration recorded, for the target's own "
                     "sample or each bundled sample of a collapsed fan-in, at "
                     "the location Storage's sample_data_path owns; a sample "
                     "whose recorded data is not there, or whose row is "
                     "malformed, is a failure naming it")
    if input_sources["selector_slot"] is not None:
        # Sample-via-selector: a node an input_selector feeds, whatever else
        # feeds it (or a legacy drive with no pipeline document). The data is
        # the file or directory registration recorded, under
        # data/samples/{sample}/ once restore_sample has run; a sibling that
        # sorts first is never read instead.
        slot = input_sources["selector_slot"]
        project_root_for_samples = get_project_root()
        # Collapsed-fan-in root branch: sample is COLLAPSED_SAMPLE and one
        # --collapsed-sample flag was emitted per bundled sample by
        # _generate_rule; restore_sample has populated each one by now (the
        # .sample_ready sentinels in the rule's input: block gate it).
        if sample == COLLAPSED_SAMPLE and collapsed_samples:
            missing: list[str] = []
            for s in collapsed_samples:
                try:
                    data = sample_data_path(project_root_for_samples, s)
                except MalformedSampleRecordError as exc:
                    return _failed(
                        f"collapsed-fan-in root '{node_id}' cannot read "
                        f"sample '{s}': {exc}"
                    )
                if data is None or not data.exists():
                    missing.append(s)
                    continue
                slot_paths.setdefault(slot, []).append(str(data.resolve()))
            if missing:
                return _failed(
                    f"collapsed-fan-in root '{node_id}' could not resolve the "
                    f"recorded data for sample(s) {missing}: each must be "
                    f"registered and its recorded file or directory present "
                    f"in its sample directory after restore_sample runs. Check the "
                    f"upstream restore_sample rule output."
                )
        else:
            # A per-sample reader: one sample, resolved by the same rule as
            # each bundled sample above, and refused the same way when its
            # recorded data is absent -- the restore did not deliver what the
            # step reads.
            try:
                data = sample_data_path(project_root_for_samples, sample)
            except MalformedSampleRecordError as exc:
                return _failed(
                    f"node '{node_id}' cannot read sample '{sample}' into "
                    f"input slot '{slot}': {exc}"
                )
            present = data is not None and data.exists()
            if not present and pipeline_data is None:
                # A legacy drive with no document reads its sample only when
                # one is there; a --ref-input may be what feeds it instead,
                # and the root check below names that flag when neither is.
                pass
            elif not present:
                reason = (
                    "no sample of that name is registered." if data is None else
                    f"its recorded data '{data.name}' is not in {data.parent} "
                    f"after restore_sample runs. Check "
                    f"the upstream restore_sample rule output."
                )
                return _failed(
                    f"node '{node_id}' could not resolve the data for sample "
                    f"'{sample}' into input slot '{slot}': {reason}"
                )
            else:
                slot_paths.setdefault(slot, []).append(str(data.resolve()))

    口 = Step(step_num=4, name="Merge references and enforce root input",
             purpose="Merge orchestrator-resolved --ref-input artifacts into their "
                     "declared slots; fail a root node that resolved no input at all")
    # Boundary rule: run_reference paths are resolved by the
    # orchestrator and passed via --ref-input.  Merge them into
    # slot_paths so the method receives them as normal
    # inputs without run_step needing to understand system node types.
    for label, ref_path in input_sources["references"]:
        slot_paths.setdefault(label, []).append(ref_path)

    # Enforce --ref-input for root nodes.  If we reach this point with
    # no parent-slot sources AND no ref_inputs resolved, the node is a root
    # node invoked without the required --ref-input flag.
    if not input_sources["parents"] and not slot_paths:
        return _failed(
            f"root node '{node_id}' has no input data.  "
            f"Provide --ref-input <slot>=<path> to supply input for root nodes."
        )

    return {"ok": True, "slot_paths": slot_paths}
