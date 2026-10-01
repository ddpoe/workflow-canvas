"""Cache status: what a pipeline document would do if it ran now.

A read-only walk over the engine's own answers. The document is prepared
and loaded exactly as a run prepares and loads it; the targets are the
engine's target expansion in topological order; each target's key comes
from the claim's pure key core, fed the upstreams' *predicted* keys instead
of rows that do not exist yet; and the hit rule that decides the row is the
claim's own ``classify_cache_key``. Nothing is written, no env is captured,
nothing is restored or pulled.

Each row gets one of six statuses:

- ``cached_local`` -- a hit whose outputs are all on this machine;
- ``cached_remote`` -- a hit with at least one output only on the remote;
- ``outputs_missing`` -- a completed run has this exact key but an output is
  in neither place; the step re-runs under the same key, so the steps below
  it keep theirs;
- ``new_step_changed`` -- a miss whose upstreams all keep their keys;
- ``new_upstream_reruns`` -- a miss under an upstream whose key is new;
- ``blocked`` -- the run path would refuse the step, with that refusal's
  message; a step under a blocked upstream is blocked, naming it.

A document the load refuses is reported as one pipeline-level reason and no
rows: every row the author sees is blocked by it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from axiom_annotations import Step, workflow
from sqlmodel import select

from ..persistence import Method, Module, Run, Sample, get_session
from ..persistence import project_root as get_project_root

CACHED_LOCAL = "cached_local"
CACHED_REMOTE = "cached_remote"
OUTPUTS_MISSING = "outputs_missing"
NEW_STEP_CHANGED = "new_step_changed"
NEW_UPSTREAM_RERUNS = "new_upstream_reruns"
BLOCKED = "blocked"

_NEW = (NEW_STEP_CHANGED, NEW_UPSTREAM_RERUNS)


@dataclass
class CacheStatusRow:
    """One target's predicted status.

    Attributes:
        node_id: The step's node id.
        sample: The target's sample (the collapsed sentinel for a bundle).
        variant: The target's variant name.
        status: One of the six statuses.
        reason: The refusal message on a ``blocked`` row; ``None`` otherwise.
        cache_key: The predicted key; ``None`` on a ``blocked`` row.
        source_run_id: The completed run a cached or outputs-missing row is
            about (the run History links to).
        source_nid: That run's NID, when it has one.
        outputs: ``(slot, location)`` per output of that run, from the probe
            that decided the status; empty on new and blocked rows.
    """

    node_id: str
    sample: str
    variant: str
    status: str
    reason: str | None = None
    cache_key: str | None = None
    source_run_id: int | None = None
    source_nid: str | None = None
    outputs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The row key the canvas projection uses: ``node::sample::variant``."""
        return f"{self.node_id}::{self.sample}::{self.variant}"


@dataclass
class CacheStatusReport:
    """The whole preview.

    Attributes:
        rows: One row per target, in execution order.
        blocked_reason: The load's refusal when the document cannot be
            prepared or loaded; ``rows`` is then empty.
    """

    rows: list[CacheStatusRow] = field(default_factory=list)
    blocked_reason: str | None = None


def _method_env(method_name: str, module_name: str) -> tuple[str, dict]:
    """Read a method's env and declared inputs the way ``pre_run`` step 2 does.

    Raises:
        ValueError: The module or the method in it is not registered.
    """
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            raise ValueError(f"Module '{module_name}' not found in DB")
        method = session.exec(
            select(Method).where(
                Method.name == method_name, Method.module_id == mod.id,
            )
        ).first()
        if method is None:
            raise ValueError(
                f"Method '{method_name}' not found in module '{module_name}'"
            )
        declared = (method.contract.input_slots if method.contract else {}) or {}
        return method.env, dict(declared)


def _unreachable_sample(sample_inputs) -> str | None:
    """The refusal for a read sample whose bytes are in neither place, if any."""
    from ..registration.sample_health import classify_sample

    project_dir = get_project_root()
    with get_session() as session:
        for _slot, name in sample_inputs:
            row = session.exec(select(Sample).where(Sample.name == name)).first()
            if row is None:
                continue  # the key core refuses an unregistered sample
            if classify_sample(row, project_dir).state == "unreachable":
                return (
                    f"sample '{name}' is unreachable: its content is neither in "
                    f"the local cache nor on the remote. Re-register it with "
                    f"`wfc register-sample --name {name} --source <path>`."
                )
    return None


def _run_nid(run_id: int | None) -> str | None:
    """The NID a run row carries, if any."""
    if run_id is None:
        return None
    with get_session() as session:
        run = session.get(Run, run_id)
        return run.nid if run is not None else None


def _source_slot(wire) -> str:
    """The upstream output slot a link reads, resolved as the claim resolves it.

    A link that names an output must name a declared one. A link that names
    none reads the upstream's one output: its one declared slot, or
    ``output`` for an upstream that declares none -- the slot the
    recorded-output fallback finds on the upstream's run.

    Raises:
        ValueError: The named slot is not declared, or the link names none
            on an upstream with several outputs.
    """
    declared = dict((wire.source_node or {}).get("slot_outputs") or {})
    if wire.source_slot:
        if declared and wire.source_slot not in declared:
            raise ValueError(
                f"input slot '{wire.target_slot}' is wired to output slot "
                f"'{wire.source_slot}' of parent node '{wire.source_id}', but "
                f"that node declares no such output slot. Declared output "
                f"slots: {', '.join(declared)}."
            )
        return wire.source_slot
    if not declared:
        return "output"
    if len(declared) == 1:
        return next(iter(declared))
    raise ValueError(
        f"input slot '{wire.target_slot}' names no output of node "
        f"'{wire.source_id}', which declares several "
        f"({', '.join(declared)}). Name the output on the link."
    )


def _status_from_verdict(verdict_status: str) -> str | None:
    """Map a hit-rule verdict to a row status; ``None`` for a miss."""
    from .claim import HIT_LOCAL, HIT_REMOTE
    from .claim import OUTPUTS_MISSING as VERDICT_OUTPUTS_MISSING

    return {
        HIT_LOCAL: CACHED_LOCAL,
        HIT_REMOTE: CACHED_REMOTE,
        VERDICT_OUTPUTS_MISSING: OUTPUTS_MISSING,
    }.get(verdict_status)


def _predict_row(document, step, sample, variant, params, predicted) -> CacheStatusRow:
    """Predict one target's key and classify it.

    ``predicted`` maps ``(node_id, sample, variant)`` of every earlier
    target to its row. Mirrors ``run_claim``: a wired upstream is the
    upstream target at the same sample and variant (where the sidecar would
    be); a ``run_reference`` is the run it names.
    """
    from ..graph import carries_sample_bundle, inbound_wiring
    from ..identity import UpstreamRunIdentity
    from .claim import (
        cache_key_from_identities,
        check_required_inputs,
        classify_cache_key,
        method_fingerprints,
        resolve_input_identities,
        step_sample_inputs,
    )
    from .parents import ParentEntry

    node_id = step.node_id
    row = CacheStatusRow(node_id=node_id, sample=sample, variant=variant,
                         status=BLOCKED)
    try:
        method_env, declared_inputs = _method_env(step.method_name,
                                                  step.module_name)
        code_fp, env_fp = method_fingerprints(step.method_name, method_env)

        wired: dict[str, str] = {}
        predicted_upstreams: list[UpstreamRunIdentity] = []
        upstream_rows: list[CacheStatusRow] = []
        entries: list[ParentEntry] = []       # both channels, for the slot rules
        reference_entries: list[ParentEntry] = []
        for wire in inbound_wiring(document, node_id):
            wired.setdefault(wire.target_slot, wire.raw_source_id)
            if wire.source_kind == "run_reference":
                if not wire.run_id:
                    continue
                entry = ParentEntry(wire.target_slot, wire.source_slot,
                                    int(wire.run_id))
                reference_entries.append(entry)
                entries.append(entry)
                continue
            upstream = predicted.get((wire.source_id, sample, variant))
            if upstream is None:
                continue  # no upstream target here: the claim finds no sidecar
            if upstream.status == BLOCKED:
                row.reason = (f"upstream step '{wire.source_id}' is blocked: "
                              f"{upstream.reason}")
                return row
            source_slot = _source_slot(wire)
            predicted_upstreams.append(UpstreamRunIdentity(
                input_slot=wire.target_slot, source_slot=source_slot,
                run_id=0, cache_key=upstream.cache_key,
            ))
            upstream_rows.append(upstream)
            entries.append(ParentEntry(wire.target_slot, source_slot, 0))

        collapsed = (list(step.collapsed_samples)
                     if carries_sample_bundle(step) else None)
        sample_inputs = step_sample_inputs(sample, entries, collapsed,
                                           step.selector_slot)
        unreachable = _unreachable_sample(sample_inputs)
        if unreachable is not None:
            row.reason = unreachable
            return row
        reference_runs, sample_identities = resolve_input_identities(
            reference_entries, sample_inputs, step=step.method_name,
        )
        check_required_inputs(
            declared_inputs, entries, sample_inputs, wired,
            step_label=node_id, method_name=step.method_name,
        )
        cache_key = cache_key_from_identities(
            code_fp, env_fp, f"{step.module_name}.{step.method_name}", params,
            predicted_upstreams + list(reference_runs), sample_identities,
        )
    except ValueError as exc:
        row.reason = str(exc)
        return row

    verdict = classify_cache_key(step.method_name, step.module_name, sample,
                                 cache_key)
    row.cache_key = cache_key
    status = _status_from_verdict(verdict.status)
    if status is None:
        upstream_new = any(u.status in _NEW for u in upstream_rows)
        row.status = NEW_UPSTREAM_RERUNS if upstream_new else NEW_STEP_CHANGED
        return row
    row.status = status
    row.source_run_id = verdict.run_id
    row.source_nid = _run_nid(verdict.run_id)
    row.outputs = [(o.slot, o.location) for o in verdict.outputs]
    return row


@workflow(purpose="Predict what a pipeline document would do if it ran now: prepare "
                  "and load it as a run does, walk the engine's target expansion in "
                  "order, predict each key with the claim's key core over the "
                  "upstreams' predicted keys, and classify it with the claim's hit "
                  "rule -- reading only",
          inputs="a pipeline document as the canvas posts it",
          outputs="a CacheStatusReport: one row per target, or the load's refusal")
def cache_status(document: Mapping[str, Any]) -> CacheStatusReport:
    """Predict every target's cache status for a pipeline document.

    Args:
        document: The pipeline document, as the run route accepts it.

    Returns:
        The report. A document the preparation or the load refuses gives
        ``blocked_reason`` and no rows; a refusal is data, never raised.
    """
    from ..graph import expand_step_combos, resolve_variant_model, topo_sort_steps
    from .composer import DatabaseUnreachableError, load_pipeline_from_document
    from .prepare import prepare_document

    口 = Step(step_num=1, name="Prepare and load",
             purpose="The run path's own preparation and load; its refusal is the "
                     "one reason every row is blocked by")
    try:
        prepared = prepare_document(document)
        pipeline = load_pipeline_from_document(prepared).pipeline
        steps = topo_sort_steps(pipeline.steps)
        tables = resolve_variant_model(pipeline).tables
        targets = expand_step_combos(steps, pipeline.samples, tables,
                                     pipeline.explicit_combos)
    except (ValueError, DatabaseUnreachableError) as exc:
        return CacheStatusReport(rows=[], blocked_reason=str(exc))

    口 = Step(step_num=2, name="Predict and classify each target in order",
             purpose="Upstream targets come first, so each target's upstream keys "
                     "are already predicted when its own key is composed")
    report = CacheStatusReport()
    predicted: dict[tuple[str, str, str], CacheStatusRow] = {}
    for step, combo in targets:
        sample, variant = combo["sample"], combo["variant"]
        params = tables.get(step.node_id, {}).get(variant, step.params)
        口 = Step(step_num=2.1, name="Predict one target",
                 purpose="Key core over predicted upstream keys, then the hit rule")
        row = _predict_row(prepared, step, sample, variant, params, predicted)
        predicted[(step.node_id, sample, variant)] = row
        report.rows.append(row)
    return report
