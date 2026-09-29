"""Expansion: the variant model, the sample x variant rows per step, the reuse mark.

One derivation of the variant model over the pipeline definition --
each node's variant table resolved by node id with a method-name fallback,
the global axis, every table padded to the axis -- consumed by the Snakefile
emitter and the cancelled-rows walk alike. Every consumer that enumerates a
pipeline's runs takes its rows from ``expand_step_combos`` over that model;
``mark_reused`` says which of those rows reuse another row's output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from axiom_annotations import task

from ..contracts import COLLAPSED_SAMPLE
from .model import PipelineDef, StepDef


@dataclass
class VariantModel:
    """The resolved variant model of a pipeline definition.

    ``tables`` is ``{node_id: {variant_name: params}}`` with every node
    padded to every name on ``axis``; ``axis`` is the sorted union of every
    node's declared variant names, ``default`` included when some node
    declares none (or none does).
    """
    tables: dict[str, dict[str, dict]]
    axis: list[str]


def variant_axis(resolved_params: dict[str, dict[str, dict]]) -> list[str]:
    """Return the pipeline's global variant axis.

    The sorted union of every node's variant names, or ``["default"]``
    when no node declares any -- the one axis expansion and the generated
    Snakefile's ``VARIANT_NAMES`` share.

    Args:
        resolved_params: ``{node_id: {variant_name: params_dict}}``.

    Returns:
        The variant names, sorted.
    """
    all_variant_names: set[str] = set()
    for variants in resolved_params.values():
        all_variant_names.update(variants.keys())
    return sorted(all_variant_names) if all_variant_names else ["default"]


@task(purpose="Derive the variant model of a pipeline definition: each node's "
              "variant table by node id with a method-name fallback, the "
              "global axis, every node padded to cover every variant name")
def resolve_variant_model(pipeline: PipelineDef) -> VariantModel:
    """Resolve the param/variant model the engine schedules from.

    Each node's table is its ``param_sets`` entry by node id, else by method
    name, else ``{"default": step.params}``; the axis is the union of every
    table's names; every table is padded so each name maps to the node's
    base params where the node declares no variant of that name. The
    definition's own ``param_sets`` are not mutated -- the tables are copies.

    Args:
        pipeline: The pipeline definition.

    Returns:
        The resolved tables and the axis.
    """
    tables: dict[str, dict[str, dict]] = {}
    for step in pipeline.steps:
        tables[step.node_id] = dict(pipeline.param_sets.get(
            step.node_id, pipeline.param_sets.get(
                step.method_name, {"default": step.params}
            )
        ))

    axis = variant_axis(tables)

    for step in pipeline.steps:
        for vname in axis:
            if vname not in tables[step.node_id]:
                tables[step.node_id][vname] = step.params

    return VariantModel(tables=tables, axis=axis)


@task(purpose="Expand sample x variant combinations for every step, per step: "
              "a collapsed step's sample axis is the collapsed-sample sentinel, "
              "a per-sample step's is the sample list; in selective mode every "
              "step runs the explicit combinations, refused for a pipeline with "
              "a collapsed step")
def expand_step_combos(
    steps: list[StepDef],
    samples: list[str],
    resolved_params: dict[str, dict[str, dict]],
    explicit_combos: list[dict[str, str]] | None,
) -> list[tuple[StepDef, dict[str, str]]]:
    """Expand sample x variant combinations for every step, per step.

    A pipeline that mixes a sample-collapsed branch with a per-sample
    branch has two sample axes: the collapsed steps run once per variant
    at ``COLLAPSED_SAMPLE``, the per-sample steps once per (sample,
    variant). Every consumer that enumerates the pipeline's targets -- the
    emitter's target list, the pipeline-end cancelled-rows walk, the
    scenario harness's scheduler -- reads this function so both axes come
    from one place.

    Selective mode (``explicit_combos`` given) has one answer for every
    step: the explicit combinations, as given, for each step.

    Args:
        steps: Ordered list of StepDefs.
        samples: List of sample identifiers.
        resolved_params: ``{node_id: {variant_name: params_dict}}`` (the
            ``tables`` of ``resolve_variant_model``).
        explicit_combos: If provided, used directly for every step
            (selective mode); rejected for a pipeline containing a
            collapsed step.

    Returns:
        ``[(step, {"sample": ..., "variant": ...}), ...]`` in step order,
        each step's combos contiguous.

    Raises:
        ValueError: When ``explicit_combos`` is given for a pipeline that
            contains a sample-collapsed step.
    """
    if explicit_combos is not None:
        if any(s.sample_collapsed for s in steps):
            raise ValueError(
                "explicit_combos is not supported for pipelines containing "
                "fan-in (sample-collapsed) steps"
            )
        return [(step, dict(combo)) for step in steps for combo in explicit_combos]

    variant_names = variant_axis(resolved_params)
    out: list[tuple[StepDef, dict[str, str]]] = []
    for step in steps:
        if step.sample_collapsed:
            out.extend(
                (step, {"sample": COLLAPSED_SAMPLE, "variant": v})
                for v in variant_names
            )
        else:
            out.extend(
                (step, {"sample": s, "variant": v})
                for s in samples for v in variant_names
            )
    return out


def _chain_signature(
    step: StepDef,
    variant: str,
    step_map: dict[str, StepDef],
    resolved_params: dict[str, dict[str, dict]],
) -> str:
    """The resolved params of ``step`` and every transitive upstream under ``variant``."""
    seen: set[str] = set()
    frontier = [step.node_id]
    while frontier:
        nid = frontier.pop()
        if nid in seen or nid not in step_map:
            continue
        seen.add(nid)
        frontier.extend(step_map[nid].depends_on)
    parts = []
    for nid in sorted(seen):
        table = resolved_params.get(nid, {})
        params = table.get(variant, step_map[nid].params)
        parts.append((nid, json.dumps(params, sort_keys=True, default=str)))
    return json.dumps(parts)


@task(purpose="Mark the scheduled rows that reuse another row's output: a "
              "step's row under a variant name is reused when its resolved "
              "params and every upstream's, transitively, under that name "
              "are identical to those under an earlier scheduled name")
def mark_reused(
    steps: list[StepDef],
    rows: list[tuple[StepDef, dict[str, str]]],
    resolved_params: dict[str, dict[str, dict]],
) -> list[bool]:
    """Say which scheduled rows reuse another row's output.

    The rule is value identity, not name declaration: two names that
    resolve to the same params on the whole upstream chain are one unique
    output, whether the second is padding (the common case) or a declared
    variant that duplicates the values. The earlier name in the step's
    scheduled order is the real run. Reuse is a fact about (step, variant
    name); every sample's row under that name carries it. Names are
    compared only among the names actually scheduled for the step, so a
    selective-mode schedule never marks a row against a run it does not
    contain.

    Args:
        steps: The pipeline's steps (for the dependency closure).
        rows: The scheduled rows, as ``expand_step_combos`` returns them.
        resolved_params: The resolved tables (``resolve_variant_model``).

    Returns:
        One flag per row, parallel to ``rows``.
    """
    step_map = {s.node_id: s for s in steps}
    scheduled_names: dict[str, list[str]] = {}
    for step, combo in rows:
        names = scheduled_names.setdefault(step.node_id, [])
        if combo["variant"] not in names:
            names.append(combo["variant"])

    reused_by_step_name: dict[tuple[str, str], bool] = {}
    for nid, names in scheduled_names.items():
        first_by_signature: dict[str, str] = {}
        for name in names:
            sig = _chain_signature(step_map[nid], name, step_map, resolved_params)
            reused_by_step_name[(nid, name)] = sig in first_by_signature
            first_by_signature.setdefault(sig, name)

    return [reused_by_step_name[(step.node_id, combo["variant"])] for step, combo in rows]
