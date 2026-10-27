"""Claim phase: decide whether work happens at all.

Resolves the node's config from the pipeline document or inline args, walks
the pipeline links to resolve this node's parents, registers the run via
``pre_run``, and carries its cache answer as-is. Early exits here happen
before any run exists to record against (or, for a ``pre_run`` failure,
before the record surfaces exist), so they never touch the record tail.

The link walk has two parent sources and keeps them on separate channels. A
wired upstream contributes the run id in its ``run_id.txt`` sidecar; a
``run_reference`` never executes and so has no sidecar, and contributes the
run id it names in the pipeline document instead. Both reach registration —
lineage rows and the cache key — but only the sidecar channel is returned to
the orchestrator, because that is the one materialize resolves into paths.

The claim's bodies live here: ``pre_run`` (git check, the cache answer,
registration), ``compose_cache_key`` (the one recipe for a cache key, shared
by ``pre_run`` and the ``check_cache`` verb), ``input_fingerprint_from_rows``
(the one reader of Run/Sample rows for the input fingerprint),
``lookup_cache_hit`` (the one definition of a cache hit, shared with the
verb), ``candidate_cache_key`` (the verb's method resolution over the
recipe), and the legacy ``register_run`` / ``lookup_run`` verbs.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from axiom_annotations import AutoStep, Step, task, workflow
from sqlalchemy import CursorResult, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from .. import layout
from ..graph import document_node, inbound_wiring
from ..identity import KEY_RECIPE
from ..persistence import (
    BUSY_TIMEOUT_SECONDS,
    Method,
    Module,
    Run,
    RunInput,
    RunOutput,
    Sample,
    get_engine,
    get_session,
    write_transaction,
)
from ..persistence import project_root as get_project_root
from ..storage import (
    OUTPUT_MISSING,
    OUTPUT_REMOTE,
    bind_claim,
    has_malformed_output_records,
    hold_code_copy,
    inputs_setting,
    new_claim_token,
    output_location,
    release_claim,
)
from .parents import ParentEntry, parse_parent_entries, spell_parent_entry
from .record import write_claim_refusal


def _run_exists(run_id: str) -> bool:
    """Report whether a run id names a row in the run table.

    The cache-key fingerprint builder skips upstream runs it cannot find, so
    an unresolvable reference would weaken the consumer's cache key without
    saying so. Checking here is what turns that into a loud failure.

    Args:
        run_id: The run id a ``run_reference`` node names.

    Returns:
        True when the id parses as an integer and a matching run row exists.
    """
    try:
        run_id_int = int(run_id)
    except (TypeError, ValueError):
        return False
    with get_session() as session:
        return session.get(Run, run_id_int) is not None


class SidecarCheck(NamedTuple):
    """The sidecar walk's answer for one wired upstream.

    Attributes:
        run_id: The run the sidecar names, as its decimal id, when it is the
            wired node's completed run for this sample and pipeline.
        refusal: Otherwise, the claim's refusal message.
    """

    run_id: str | None
    refusal: str | None


@task(purpose="Check that the run a wired upstream's run-id sidecar names is "
              "the wired node's completed run for this claim's sample and "
              "pipeline; a missing sidecar, content that is not a run id, a "
              "run that does not exist, a run recording no node, and any "
              "other node, sample, pipeline or status are refused",
      inputs="the sidecar path, the consumer node and slot, the id a run "
             "records for the wired source node, this claim's sample and "
             "pipeline id",
      outputs="the run id the sidecar names, or the refusal naming the "
              "sidecar, the run it names and what that run is")
def check_sidecar_run(
    sidecar: Path,
    *,
    node_id: str,
    slot: str,
    source_node_id: str,
    sample: str,
    pipeline_id: str,
) -> SidecarCheck:
    """Check the run a run-id sidecar names against the pipeline's wiring.

    The sidecar is a record wfc owns: run_record writes it beside the wired
    node's sentinel, naming the run that ended the target. The run row
    carries every field the wiring implies, so the named run must be a run
    of the wired node (the raw document id a run records, not the
    canonical id the sidecar path uses), for this claim's sample
    (``COLLAPSED_SAMPLE`` on both sides of a collapsed chain), under this
    claim's pipeline, and completed. A cache hit's audit row carries the
    claim's own node, sample and pipeline, so it passes. Nothing here
    compares pipeline documents.

    Args:
        sidecar: The wired upstream's ``run_id.txt`` path.
        node_id: The consumer node, for the message.
        slot: The consumer's input slot the link feeds, for the message.
        source_node_id: The id a run records for the wired source node.
        sample: This claim's sample.
        pipeline_id: This claim's pipeline id.

    Returns:
        A ``SidecarCheck`` carrying the run id, or the refusal.
    """
    expected = (f"a completed run of node '{source_node_id}' for sample "
                f"'{sample}' in pipeline '{pipeline_id}'")

    def _refusal(what: str) -> SidecarCheck:
        return SidecarCheck(None, (
            f"node '{node_id}' input slot '{slot}' is wired from node "
            f"'{source_node_id}', but {what}: a record wfc owns disagrees "
            f"with the pipeline, which expects {expected}. wfc will not "
            f"read an input it cannot identify."
        ))

    if not sidecar.is_file():
        return _refusal(f"there is no run-id sidecar at {sidecar}")
    text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
    try:
        run_id = int(text)
    except ValueError:
        return _refusal(f"the run-id sidecar {sidecar} holds {text!r}, "
                        f"which is not a run id")
    with get_session() as session:
        run = session.get(Run, run_id)
        found = None if run is None else (
            run.node_id, run.sample, run.pipeline_id, run.status)
    if found is None:
        return _refusal(f"the run-id sidecar {sidecar} names run {run_id}, "
                        f"which does not exist")
    run_node, run_sample, run_pipeline, run_status = found
    if run_node is None:
        return _refusal(f"the run-id sidecar {sidecar} names run {run_id}, "
                        f"which records no node and so cannot be verified")
    if (str(run_node), run_sample, run_pipeline, run_status) != (
            source_node_id, sample, pipeline_id, "completed"):
        return _refusal(
            f"the run-id sidecar {sidecar} names run {run_id}, a "
            f"{run_status} run of node '{run_node}' for sample "
            f"'{run_sample}' in pipeline '{run_pipeline}'"
        )
    return SidecarCheck(str(run_id), None)


def _unresolved_reference(wire, ref_run_id: str) -> str | None:
    """Why a reference link's artifact does not resolve, or ``None`` when it does.

    The load's own resolution: Storage resolves the referenced run's
    outputs and Graph's reference-link rule decides whether the output the
    link names is among them. The launch path already refuses such a
    document at load; this is the same refusal for a claim the load did not
    precede -- a direct ``run-step`` drive, or a run whose records changed
    after the load.

    Args:
        wire: The reference's inbound link (Graph's ``InboundLink``).
        ref_run_id: The run id the reference node names.

    Returns:
        The refusal message, or ``None`` when the artifact resolves.
    """
    from ..graph import reference_link_error
    from ..storage import resolve_run_reference_outputs

    declared = {"run_id": ref_run_id,
                "label": (wire.source_node or {}).get("label", "")}
    with get_session() as session:
        resolved = resolve_run_reference_outputs(
            {wire.raw_source_id: dict(declared)}, session)
    info = {**declared, **(resolved.get(wire.raw_source_id) or {})}
    error = reference_link_error(wire.raw_source_id, info, wire.source_slot)
    return None if error is None else str(error)


def input_fingerprint_from_rows(
    parent_entries: Sequence[ParentEntry],
    samples: Sequence[tuple[str, str]] = (),
    *,
    step: str,
) -> str:
    """Resolve a step's inputs to their identities and fingerprint them.

    ``resolve_input_identities`` followed by ``wfc.identity``'s
    ``build_input_fingerprint``; see the resolver for the rules.

    Args:
        parent_entries: The step's parsed parent entries. Empty for root
            nodes.
        samples: ``(input_slot, sample_name)`` pairs.
        step: The node or method the inputs belong to, for error messages.

    Returns:
        64-char hex SHA256 string.

    Raises:
        ValueError: As ``resolve_input_identities``.
    """
    from ..identity import build_input_fingerprint

    upstream_runs, sample_identities = resolve_input_identities(
        parent_entries, samples, step=step,
    )
    return build_input_fingerprint(upstream_runs, sample_identities)


@task(purpose="Read the Run and Sample rows an input fingerprint is built from, refuse "
              "an input that resolves to no row, resolve an entry that names no output "
              "to the upstream's one recorded output, and return the slot-qualified "
              "identities wfc.identity renders")
def resolve_input_identities(
    parent_entries: Sequence[ParentEntry],
    samples: Sequence[tuple[str, str]] = (),
    *,
    step: str,
) -> tuple[list, list]:
    """Resolve a step's inputs to the identities its input fingerprint is over.

    The one place that reads ``Run`` or ``Sample`` rows for the input
    fingerprint. Each upstream run contributes its id and cache key (absent
    on a row that predates cache keys); each sample its content hash. Both
    carry the input slot they feed, because a value alone does not say what
    was computed from it.

    A sample row carrying no content hash is a malformed record, and this
    is not where it is caught: this runs per target inside ``pre_run``,
    after the engine has started. ``wfc.registration.load_sample_hashes``
    refuses it at pipeline start, where the whole sample set is visible;
    ``wfc.identity.render_sample_part`` raises as a backstop if one ever
    reaches here.

    An input that resolves to no row is refused, naming the step and the
    input: a skipped input contributes no part, so a step whose inputs all
    resolve to nothing would register under the digest of the empty string
    and share that key with every other such step.

    An entry naming no output is resolved to the upstream's one recorded
    output through Storage, so the two spellings of one wiring
    (``data:412`` and ``data:merged:412`` on a single-output upstream) give
    one key. An upstream with several recorded outputs, or none, is refused
    -- an unqualified part would let two different wirings share a key.

    How an identity is spelled as a part, and the sort, join and hash over
    the parts, are ``wfc.identity``'s and are not repeated here.

    Args:
        parent_entries: The step's parsed parent entries. Empty for root
            nodes.
        samples: ``(input_slot, sample_name)`` pairs -- the root sample, or
            each member of a collapsed fan-in bundle.
        step: The node or method the inputs belong to, for error messages.

    Returns:
        ``(upstream_runs, sample_identities)``: the ``UpstreamRunIdentity``
        and ``SampleIdentity`` lists ``cache_key_from_identities`` takes.

    Raises:
        ValueError: An entry names a run with no row, a sample name has no
            row, or an entry naming no output is wired to a run whose
            recorded outputs are not exactly one.
    """
    from ..identity import SampleIdentity, UpstreamRunIdentity
    from ..storage import recorded_output_slots

    upstream_runs: list[UpstreamRunIdentity] = []
    sample_identities: list[SampleIdentity] = []
    with get_session() as session:
        for entry in parent_entries:
            run = session.get(Run, entry.run_id)
            if run is None:
                raise ValueError(
                    f"Step '{step}': input slot '{entry.input_slot}' is fed by "
                    f"run {entry.run_id}, which is not a registered run. An "
                    f"input that resolves to no row would leave the step's "
                    f"cache key blind to it; fix or remove the wiring."
                )
            source_slot = entry.source_slot
            if source_slot is None:
                slots = recorded_output_slots(entry.run_id, session=session)
                if len(slots) != 1:
                    listing = ", ".join(slots) or "(none)"
                    raise ValueError(
                        f"Step '{step}': input slot '{entry.input_slot}' names "
                        f"no output of run {entry.run_id}, and that run does "
                        f"not have exactly one recorded output to fall back on "
                        f"(its outputs: {listing}). Name the output in the "
                        f"entry: '{entry.input_slot}:<output>:{entry.run_id}'."
                    )
                source_slot = slots[0]
            upstream_runs.append(UpstreamRunIdentity(
                input_slot=entry.input_slot,
                source_slot=source_slot,
                run_id=entry.run_id,
                cache_key=run.cache_key,
            ))
        for input_slot, sample_name in samples:
            sample = session.exec(
                select(Sample).where(Sample.name == sample_name)
            ).first()
            if sample is None:
                raise ValueError(
                    f"Step '{step}': input slot '{input_slot}' reads sample "
                    f"'{sample_name}', which is not registered. Register it "
                    f"with `wfc register-sample` so the step's cache key "
                    f"carries its content."
                )
            sample_identities.append(SampleIdentity(
                input_slot=input_slot,
                content_hash=sample.content_hash,
            ))
    return upstream_runs, sample_identities


HIT_LOCAL = "local"
HIT_REMOTE = "remote"
OUTPUTS_MISSING = "outputs_missing"
MISS = "miss"


class OutputLocation(NamedTuple):
    """Where one output of a completed run can be read from.

    Attributes:
        slot: The contract output slot the record fills.
        location: ``local`` (its bytes are on this machine: the DVC cache
            entry of an archived row, or the run-archive path of a row the
            archive pass has not reached -- the two are one label on
            purpose), ``remote`` (archived, not local, recorded pushed) or
            ``missing`` (in neither place).
    """

    slot: str
    location: str


class CacheVerdict(NamedTuple):
    """The hit rule's answer for one cache key, with each output's location.

    Attributes:
        status: ``local`` (a hit, every output local), ``remote`` (a hit, at
            least one output only on the remote, none missing),
            ``outputs_missing`` (a completed run with this key exists but an
            output is in neither place -- the step re-runs under the same
            key) or ``miss`` (no well-formed completed run has this key).
        run_id: The completed run the verdict is about; ``None`` on a miss.
        outputs: Per-output locations of that run, from the same probe that
            decided ``status``; empty on a miss.
    """

    status: str
    run_id: int | None
    outputs: tuple[OutputLocation, ...]

    @property
    def hit_run_id(self) -> int | None:
        """The run to reuse: ``run_id`` on a hit, ``None`` otherwise."""
        return self.run_id if self.status in (HIT_LOCAL, HIT_REMOTE) else None


def _verdict_for_run(run_id: int, rows, project_dir: Path,
                     setting: str | None = None) -> CacheVerdict:
    """The verdict for one candidate run from its output records.

    Each output's location comes from Storage's ``output_location``, which
    runs the integrity check: a changed or marked-changed output is
    ``missing``, so one changed output makes the run ``outputs_missing``.
    ``setting`` is ``[integrity] inputs``, read once per hit-rule pass.
    """
    outputs = tuple(
        OutputLocation(row.slot, output_location(row, project_dir, setting))
        for row in rows
    )
    locations = {o.location for o in outputs}
    if OUTPUT_MISSING in locations:
        status = OUTPUTS_MISSING
    elif OUTPUT_REMOTE in locations:
        status = HIT_REMOTE
    else:
        status = HIT_LOCAL
    return CacheVerdict(status, run_id, outputs)


def classify_cache_key(
    method_name: str, module_name: str, sample: str, cache_key: str
) -> CacheVerdict:
    """The hit rule: decide whether a cache key is reused, and from where.

    The one definition of a cache hit: a completed, non-audit run of the
    method on the sample whose ``cache_key`` is identical to the candidate's,
    whose output records are well formed, and every one of whose outputs can
    be read -- from this machine (the DVC cache, or the run archive for a
    row the archive pass has not reached) or by a pull from the remote. The
    answer is exactly "the consumer's resolver will succeed without
    recomputing". A run with a malformed record (Storage's record-validity
    rule) is never reused, so the step recomputes rather than handing
    downstream steps outputs no reader accepts.

    Readability is Storage's ``output_location``, which checks each output
    against what wfc recorded: an output not yet archived that changed after
    its run wrote it (or whose row is already marked changed), and an
    archived cache object that changed and was never pushed, count as
    missing, so the step re-runs rather than reusing them.

    Among well-formed candidates, the newest whose outputs are all readable
    wins. When none is, the newest well-formed candidate is reported as
    ``outputs_missing``: the step re-runs under the same key, so its
    downstream steps keep theirs.

    ``pre_run`` step 6 and the ``check_cache`` verb (through
    ``lookup_cache_hit``) and the cache-status preview all answer through
    this function, so no caller serves a hit under a second, weaker rule.

    The filter matches the method WITHIN ITS MODULE: the row is reached
    through the module join, so two modules registering a method of the same
    name never share candidates. The cache key is over ``<module>.<method>``
    as well, so the two halves of the rule agree.

    Args:
        method_name: The method's name.
        module_name: The name of the module that registered the method.
        sample: The sample identifier the candidate runs on.
        cache_key: The candidate's cache key (``build_cache_key``).

    Returns:
        The verdict, with the per-output locations that decided it.
    """
    project_dir = get_project_root()
    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, col(Run.method_id) == col(Method.id))
            .join(Module, col(Method.module_id) == col(Module.id))
            .where(Method.name == method_name)
            .where(Module.name == module_name)
            .where(Run.sample == sample)
            .where(Run.status == "completed")
            .where(Run.cache_key == cache_key)
            .where(Run.cache_source_run_id == None)  # noqa: E711  exclude audit rows
        )
        stmt = stmt.order_by(col(Run.finished_at).desc())
        first_incomplete: CacheVerdict | None = None
        setting: str | None = None  # read once, at the first candidate
        for run in session.exec(stmt).all():
            if has_malformed_output_records(run.id, session=session):
                continue
            rows = session.exec(
                select(RunOutput).where(RunOutput.run_id == run.id)
                .order_by(col(RunOutput.id))
            ).all()
            if setting is None:
                setting = inputs_setting(project_dir)
            verdict = _verdict_for_run(run.id, rows, project_dir, setting)
            if verdict.status != OUTPUTS_MISSING:
                return verdict
            if first_incomplete is None:
                first_incomplete = verdict
    return first_incomplete or CacheVerdict(MISS, None, ())


def lookup_cache_hit(
    method_name: str, module_name: str, sample: str, cache_key: str
) -> int | None:
    """Find the completed run the claim phase would reuse for a cache key.

    ``classify_cache_key``'s hit, as the run id ``pre_run`` and the
    ``check_cache`` verb reuse.

    Args:
        method_name: The method's name.
        module_name: The name of the module that registered the method.
        sample: The sample identifier the candidate runs on.
        cache_key: The candidate's cache key (``build_cache_key``).

    Returns:
        The run id to reuse, or ``None`` when the verdict is not a hit.
    """
    return classify_cache_key(
        method_name, module_name, sample, cache_key
    ).hit_run_id


def step_sample_inputs(
    sample: str,
    parent_entries: Sequence[ParentEntry],
    collapsed_samples: Sequence[str] | None = None,
    selector_slot: str | None = None,
) -> list[tuple[str, str]]:
    """Name the sample(s) a step reads and the input slot each is read into.

    Part of the key recipe, shared by ``compose_cache_key`` and the cache
    preview so both name a step's sample inputs one way.

    Which sample(s) this step reads, and into which input slot, is a
    question about the DOCUMENT, not about whether the step has parents.
    ``selector_slot`` is the claim phase's answer, from the same pure
    classifier materialize asks (``classify_input_sources`` over the
    sidecar-only parent list), so the phase that fills the slot and the
    phase that keys on it can never disagree. A node fed by BOTH an
    ``input_selector`` and a ``run_reference`` has a selector slot and
    non-empty parents at once: gating the sample on the absence of parents
    (as this branch once did) meant such a node read a sample it keyed
    nothing from, and a content change left its key byte-identical.

    ``None`` means the caller did not classify -- a direct verb drive with
    no pipeline document. Such a drive reads the sample when it has no
    parents, into ``data``, which is the slot materialize delivers into for
    exactly that case.

    A collapsed fan-in root carries the ``__all__`` sentinel as its sample,
    so the single-sample lookup below finds nothing and the bundle would
    contribute no parts at all: every collapsed root with the same code,
    params and env would then share one key regardless of WHICH samples it
    bundled, and the hit lookup's ``Run.sample`` filter cannot separate them either
    (it is ``__all__`` on both sides). Edit a fan-in selector's sample list
    and the root would hand back the previous bundle's output. The bundle is
    the root's input identity, so each bundled sample contributes a part
    through the same composition an upstream run or a plain root sample uses.

    This is a SIBLING of the no-upstream branch, not a tenant of it. A
    collapsed root has no method parents, but a ``run_reference`` wired into
    it does reach here through ``upstream_run_ids`` (claim.py merges the
    reference channel into the registration parents). Nesting the bundle
    inside ``if not upstream_run_ids`` would silently drop it for
    exactly that shape; both contribute, and ``build_input_fingerprint``
    sorts the combined parts, so bundle ORDER stays irrelevant to the key.

    An unregistered bundled sample is refused by the reader below, naming
    the step and the sample, before any run row exists — a skipped input
    contributes no part at all, and Run.sample is ``__all__`` on both sides
    of a collapsed comparison, so the hit lookup cannot separate two bundles
    the way it separates two samples.

    Args:
        sample: The sample identifier (``COLLAPSED_SAMPLE`` for a bundle).
        parent_entries: The step's parsed parent entries.
        collapsed_samples: The bundled sample identities of a collapsed
            fan-in root.
        selector_slot: The input slot the document's selector feeds, or
            ``None`` when the caller has no document to classify from.

    Returns:
        ``(input_slot, sample_name)`` pairs; empty for a step that reads no
        sample.
    """
    if collapsed_samples:
        bundle_slot = selector_slot or "data"
        return [(bundle_slot, bundled) for bundled in collapsed_samples]
    if selector_slot is not None:
        return [(selector_slot, sample)]
    if not parent_entries:
        return [("data", sample)]
    return []


@task(purpose="Resolve a method version's code fingerprint (its extracted copy, "
              "verified against the version, and the contract declared there) and "
              "its env fingerprint (the one the version recorded at registration, "
              "not the live env manifest) -- the non-pure half of the key recipe")
def method_fingerprints(module_name: str, method_name: str,
                        method_env: str, *,
                        version: str | None = None) -> tuple[str, str]:
    """The method-level fingerprints a cache key is over.

    Both come from the version's list entry: the code fingerprint its
    extracted copy was verified against, and the env fingerprint it
    recorded when it was registered. Registering the env again does not
    move an existing version's key.

    Args:
        module_name: The method's module.
        method_name: The method's name.
        method_env: The method's stored env spec; refused when empty.
        version: The version's content identity; ``None`` is the newest.

    Returns:
        ``(code_fingerprint, env_fingerprint)``.

    Raises:
        ValueError: The method declares no env, the resolver refuses the
            version (no list, an unknown version, a tree git no longer
            holds), or the version records no env fingerprint.
    """
    from ..registration import resolve_method_code

    if not method_env:
        raise ValueError(
            f"env required (pixi/conda/byo): method '{method_name}' "
            f"declares no env"
        )

    口 = Step(step_num=1, name="Build code fingerprint",
             purpose="Compute the code fingerprint from the method's registered "
                     "source copy: its scripts AND the contract it declares there, "
                     "since an output slot's name and type decide the filename a "
                     "run writes")
    # The version's extracted copy (the resolver verified it fingerprints
    # as the version records, method.yaml's contract included), never the
    # staging snapshot or the MethodContract row.
    code = resolve_method_code(module_name, method_name, get_project_root(),
                               version=version)
    code_fingerprint = code.entry.code_fingerprint

    口 = Step(step_num=2, name="Read the version's env fingerprint",
             purpose="The env fingerprint the version's list entry recorded "
                     "when it was registered, persisted on the Run row; the "
                     "live env manifest is not read, so a version keeps its "
                     "key when its env is registered again")
    env_fingerprint = code.entry.env_fingerprint
    if not env_fingerprint:
        raise ValueError(
            f"Version v{code.entry.label} of method '{module_name}/{method_name}' "
            f"records no env fingerprint. Register the method again with "
            f"`wfc register-method`."
        )
    return code_fingerprint, env_fingerprint


@task(purpose="The pure key core: combine the code fingerprint, params, the input "
              "fingerprint over resolved identities, the env fingerprint and "
              "<module>.<method> into the cache key, reading nothing")
def cache_key_from_identities(
    code_fingerprint: str,
    env_fingerprint: str,
    qualified_method: str,
    params: dict | None,
    upstream_runs: Sequence,
    sample_identities: Sequence,
) -> str:
    """The pure key core: a cache key over already-resolved identities.

    Reads nothing. ``compose_cache_key`` feeds it the identities it resolved
    from rows; the cache preview feeds it predicted upstream keys for steps
    that have not run. One core, so the two never compute a key two ways.

    Args:
        code_fingerprint: The method's code fingerprint.
        env_fingerprint: The method's env fingerprint.
        qualified_method: ``"<module>.<method>"``.
        params: The step's parameters.
        upstream_runs: ``UpstreamRunIdentity`` per upstream wiring.
        sample_identities: ``SampleIdentity`` per sample input.

    Returns:
        The 64-char hex cache key.
    """
    from ..identity import build_cache_key, build_input_fingerprint

    input_fingerprint = build_input_fingerprint(upstream_runs, sample_identities)
    return build_cache_key(
        code_fingerprint, params or {}, input_fingerprint, env_fingerprint,
        qualified_method,
    )


class SampleRead(NamedTuple):
    """One sample a step reads: the slot, the sample, and its content hash.

    The three travel together from the moment the reader resolves the hash,
    so the row the claim phase records can never pair a sample with another
    sample's slot or hash.

    Attributes:
        input_slot: The input slot the sample is read into.
        sample_name: The registered sample's name.
        content_hash: The sample's content hash, as the cache key is over it.
    """

    input_slot: str
    sample_name: str
    content_hash: str | None


def _pair_sample_reads(
    sample_inputs: Sequence[tuple[str, str]],
    sample_identities: Sequence,
) -> list[SampleRead]:
    """Pair each ``(slot, sample)`` input with the identity resolved for it.

    ``resolve_input_identities`` returns one identity per sample input, in
    order, or raises. The pairing is checked rather than trusted: a count or
    slot mismatch is a defect in the reader, and a silent mispairing would
    record one sample's hash against another.

    Args:
        sample_inputs: The ``(input_slot, sample_name)`` pairs handed to the
            reader.
        sample_identities: The ``SampleIdentity`` list it returned.

    Returns:
        One ``SampleRead`` per sample input.

    Raises:
        RuntimeError: The two lists differ in length or in a slot.
    """
    if len(sample_inputs) != len(sample_identities):
        raise RuntimeError(
            f"sample reader returned {len(sample_identities)} identities for "
            f"{len(sample_inputs)} sample inputs"
        )
    reads: list[SampleRead] = []
    for (input_slot, sample_name), identity in zip(sample_inputs, sample_identities):
        if identity.input_slot != input_slot:
            raise RuntimeError(
                f"sample reader paired sample '{sample_name}' in slot "
                f"'{input_slot}' with an identity in slot '{identity.input_slot}'"
            )
        reads.append(SampleRead(input_slot, sample_name, identity.content_hash))
    return reads


class ComposedKey(NamedTuple):
    """A candidate step's cache key and the parts the claim phase keeps.

    Attributes:
        cache_key: The 64-char hex cache key.
        code_fingerprint: The fingerprint of the registered source copy; the
            claim phase records it as the run's method version.
        env_fingerprint: The method's env fingerprint; the claim phase
            persists it on the run row.
        parent_entries: The parsed parent entries (input slot, source slot,
            run id); the claim phase records them as lineage rows.
        sample_reads: The samples the recipe resolved the step to read, each
            with its slot and content hash -- one for a plain root, one per
            bundled sample for a collapsed fan-in root, none for a step that
            reads no sample. The claim phase records them as sample rows and
            reads their slots to answer which of the method's declared input
            slots this step feeds.
    """

    cache_key: str
    code_fingerprint: str
    env_fingerprint: str
    parent_entries: list[ParentEntry]
    sample_reads: list[SampleRead]

    @property
    def sample_inputs(self) -> list[tuple[str, str]]:
        """The ``(input_slot, sample_name)`` pairs of ``sample_reads``."""
        return [(read.input_slot, read.sample_name) for read in self.sample_reads]


@task(purpose="The one recipe for a candidate step's cache key: the code fingerprint "
              "of the registered source copy and the contract it declares there, the "
              "params, the input fingerprint over the upstream runs or the root "
              "sample(s), the method's env fingerprint, and which module's method "
              "this is")
def compose_cache_key(
    method_name: str,
    method_env: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    collapsed_samples: list[str] | None = None,
    selector_slot: str | None = None,
    version: str | None = None,
) -> ComposedKey:
    """Compose the cache key for a candidate step of a resolved method.

    The one owner of the key's recipe: ``pre_run`` builds every run's key
    here, and the ``check_cache`` verb builds its candidate's key here
    through ``candidate_cache_key``. Each caller resolves the method row
    itself and hands over the row's name, module and env.

    Args:
        method_name: The resolved method's name; its registered source copy
            is the method dir of that name.
        method_env: The resolved method's stored env spec.
        module_name: The name of the module the resolved method belongs to.
            ``"<module>.<method>"`` is a key component, so two modules'
            same-named methods never share a candidate.
        sample: The sample identifier. A root step's input identity when it
            has no upstream runs and no collapsed bundle.
        params: The step's parameters.
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"``; empty for a root step.
        collapsed_samples: For a collapsed fan-in root (sample is
            ``COLLAPSED_SAMPLE``), the bundled sample identities. They are the
            root's input identity and enter the key as a set.
        selector_slot: The input slot the step reads its sample into, as the
            claim phase classified it from the pipeline document. ``None``
            when the caller has no document to classify from -- a direct verb
            drive -- in which case a parentless step reads its sample into
            ``data``.
        version: The method version's content identity the step runs;
            ``None`` is the newest. Its code and recorded env fingerprints
            are what the key is over.

    Returns:
        The key, with the code fingerprint, env fingerprint, parsed parent
        entries and sample reads the claim phase records beside it.

    Raises:
        ValueError: If the method declares no env, its registered source copy
            cannot be fingerprinted or holds no ``method.yaml``, a parent
            entry is malformed, two entries wire different outputs of one run
            into one input slot, an input resolves to no row, or an entry
            names no output on an upstream whose recorded outputs are not
            exactly one.
    """
    params = params or {}
    if not method_env:
        raise ValueError(
            f"env required (pixi/conda/byo): method '{method_name}' "
            f"declares no env"
        )

    口 = AutoStep(step_num=1, name="Resolve the method's fingerprints")
    code_fingerprint, env_fingerprint = method_fingerprints(
        module_name, method_name, method_env, version=version,
    )

    口 = Step(step_num=2, name="Parse parent run references",
             purpose="Parse each parent entry into its input slot, source slot and "
                     "run id through the shared parser, rejecting a bare run id and "
                     "two outputs of one run wired into one input. The whole entry "
                     "feeds both the lineage rows and the input fingerprint -- the "
                     "slots are part of what the key is over")
    parent_entries = parse_parent_entries(
        parent_run_ids or [], step=method_name,
    )

    口 = Step(step_num=3, name="Resolve input identities",
             purpose="Name the step's sample inputs and their input slot, then hand "
                     "the parent entries and the (slot, sample) pairs to the one "
                     "reader, which resolves them to slot-qualified identities for "
                     "Identity -- the key chains through upstream Run.cache_key and "
                     "records the wiring each value arrives through. Each sample "
                     "stays paired with its slot and resolved content hash, so the "
                     "claim records what it read without a second Sample read")
    sample_inputs = step_sample_inputs(
        sample, parent_entries, collapsed_samples, selector_slot,
    )
    upstream_runs, sample_identities = resolve_input_identities(
        parent_entries, sample_inputs, step=method_name,
    )
    sample_reads = _pair_sample_reads(sample_inputs, sample_identities)

    口 = AutoStep(step_num=4, name="Build cache key")
    cache_key = cache_key_from_identities(
        code_fingerprint, env_fingerprint, f"{module_name}.{method_name}",
        params, upstream_runs, sample_identities,
    )
    return ComposedKey(
        cache_key, code_fingerprint, env_fingerprint, parent_entries, sample_reads
    )


@task(purpose="The claim's door for params: Contracts' parameter check against "
              "the version's declared params, then each declared value in its "
              "declared type's form, then each declared default the params "
              "leave out filled -- the one place that order is written",
      inputs="module and method names, the params as given, the version "
             "identity (the newest when none) and the node id, if any",
      outputs="The params the claim keys and records and dispatch hands the "
              "method; a ValueError naming node (or method), version and "
              "parameter when the check refuses them",
      critical="On a prepared document's params it changes nothing: they were "
               "checked, normalized and filled by the same Contracts rules. A "
               "version with no contract declares no params and is passed "
               "through unchecked, as the validator does")
def resolve_claim_params(module_name: str, method_name: str,
                         params: dict | None, version: str | None = None,
                         node_id: str | None = None) -> dict:
    """Check, normalize and fill a claim's params against a version's contract.

    ``run_claim`` (once per claim, so dispatch gets the dict the run row
    records; its ``pre_run`` call does not resolve again), a direct
    ``pre_run``, ``candidate_cache_key`` and ``register_run`` all come
    through here, so a hand-run verb gets the pipeline's verdict, its
    ``int``/``float`` spelling and its declared defaults.

    Args:
        module_name: The method's module.
        method_name: The method's name.
        params: The params as the caller gave them; ``None`` means none.
        version: The version's content identity; ``None`` checks against the
            newest version.
        node_id: The pipeline node the params are for, named in a refusal;
            ``None`` (a verb with no node) names the method instead.

    Returns:
        A new dict: the params with each declared value normalized and each
        usable declared default the params leave out added.

    Raises:
        ValueError: The parameter check refuses the params (one line per
            refusal, each naming the node or method, the version label and
            the parameter); or the method has no version list, or no version
            carries ``version`` (Registration's errors).
    """
    from ..contracts import normalize_and_fill_params, param_refusals
    from ..registration import UnknownVersionError, chosen_version, version_contracts

    params = dict(params or {})
    versions = version_contracts(module_name, method_name)
    if not versions:
        return params
    found = chosen_version(versions, version, entry_of=lambda vc: vc[0].entry)
    if found is None:
        labels = ", ".join(f"v{v.entry.label}" for v, _ in versions)
        raise UnknownVersionError(
            f"{module_name}.{method_name} has no version {version} "
            f"(its versions: {labels})")
    chosen, contract = found
    if contract is None:
        return params
    schema = contract.params_schema or {}
    label = f"v{chosen.entry.label}"
    method_ref = f"{module_name}.{method_name}"

    口 = Step(step_num=1, name="Check the params as given",
             purpose="Contracts' param_refusals: undeclared, wrong type, outside "
                     "its limits, or a required parameter with no usable default")
    refusals = param_refusals(node_id or method_name, method_ref, label,
                              params, schema)
    if refusals:
        if node_id is None:
            # A verb with no node: the refusal names the method it was for.
            prefix = f"Node '{method_name}' "
            refusals = [f"Method '{method_name}' " + r[len(prefix):]
                        if r.startswith(prefix) else r for r in refusals]
        raise ValueError("\n".join(refusals))

    口 = Step(step_num=2, name="Normalize, then fill",
             purpose="Contracts' normalize_and_fill_params: each declared value "
                     "in its declared type's form, then each usable declared "
                     "default the params leave out")
    resolved = normalize_and_fill_params(params, schema)
    return resolved


def candidate_cache_key(
    method_name: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_ids: list | None = None,
) -> str:
    """Compute the cache key the ``check_cache`` verb looks up for a candidate.

    Resolves the method row within its module, then builds the key through
    ``compose_cache_key``, the recipe ``pre_run`` uses. One divergence from
    the claim phase's answer remains:

    - A collapsed fan-in root. The claim phase folds the bundled sample
      identities into the root's key; the verb's argv cannot name a bundle,
      so its key for a collapsed root carries none and does not match the
      key the claim phase built.

    Args:
        method_name: The method's name.
        module_name: The name of the module that registered it. The verb
            requires it, so the row this resolves is the row ``pre_run``
            would resolve for the same step.
        sample: The sample identifier.
        params: The step's parameters, as given: checked, normalized and
            filled with the newest version's declared defaults
            (``resolve_claim_params``) before they are keyed.
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"`` strings; empty for a root step. A bare run id
            is refused -- every entry names the input slot it feeds.

    Returns:
        The 64-char hex cache key.

    Raises:
        ValueError: If the module is not registered, the method is not
            registered in it, the newest version's parameter check refuses
            the params, the method declares no env, its registered source
            copy cannot be fingerprinted, or a parent reference is not an
            integer run id.
    """
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            raise ValueError(f"Module '{module_name}' not found in DB")
        method = session.exec(
            select(Method).where(
                Method.name == method_name,
                Method.module_id == mod.id,
            )
        ).first()
        if method is None:
            raise ValueError(
                f"Method '{method_name}' not found in module '{module_name}'"
            )
        method_env: str = method.env

    # The params the claim would key: checked, normalized and filled against
    # the newest version, as pre_run does for a claim with no pin.
    params = resolve_claim_params(module_name, method_name, params)
    return compose_cache_key(
        method_name=method_name,
        method_env=method_env,
        module_name=module_name,
        sample=sample,
        params=params,
        parent_run_ids=parent_run_ids,
    ).cache_key


def check_required_inputs(
    declared_inputs: dict,
    parent_entries: Sequence[ParentEntry],
    sample_inputs: Sequence[tuple[str, str]],
    wired_slots: dict[str, str] | None,
    *,
    step_label: str,
    method_name: str,
) -> None:
    """Refuse a step whose method declares a required input slot nothing feeds.

    An unfed required slot means the method will read a file nothing in the
    key accounts for, so two runs over different data share a key and the
    second is served the first's outputs. ``pre_run`` and the cache-status
    preview both ask here, so the preview reports the same refusal the claim
    raises.

    ``required: false`` is the codebase's existing "declared but
    deliberately unfed" convention (a method must declare at least one
    input slot, so a trigger-only method declares one and leaves it
    unfed); those slots are not checked.

    A caller with no pipeline document does not call this: the
    ``--ref-input`` channel feeds slots that never reach the claim, so the
    fed set is incomplete and the refusal would be false.

    An unfed slot the document DOES wire is not the author's mistake: the
    wiring is there and wfc failed to hand the claim its input. That gets
    its own message, naming the slot and the node wiring it.

    Args:
        declared_inputs: The method contract's ``input_slots``.
        parent_entries: The step's parent entries (both channels).
        sample_inputs: The ``(input_slot, sample_name)`` pairs it reads.
        wired_slots: Each input slot the document wires into the node,
            mapped to the node feeding it; ``None`` treats every unfed slot
            as unwired.
        step_label: The step's NID or method name, for the message.
        method_name: The method's name, for the message.

    Raises:
        ValueError: A required slot is unfed.
    """
    fed = {entry.input_slot for entry in parent_entries}
    fed |= {input_slot for input_slot, _ in sample_inputs}
    unfed = sorted(
        slot for slot, spec in (declared_inputs or {}).items()
        if isinstance(spec, dict) and spec.get("required") is True
        and slot not in fed
    )
    wired = wired_slots or {}
    fed_listing = ", ".join(f"'{slot}'" for slot in sorted(fed)) or "(none)"
    undelivered = [slot for slot in unfed if slot in wired]
    if undelivered:
        listing = ", ".join(
            f"'{slot}' (wired from node '{wired[slot]}')" for slot in undelivered
        )
        raise ValueError(
            f"Step '{step_label}': the pipeline document wires "
            f"input slot(s) {listing} into method '{method_name}', but wfc "
            f"did not deliver that input to the step (fed: {fed_listing}). "
            f"This is a wfc defect, not a wiring to fix: please report it."
        )
    if unfed:
        listing = ", ".join(f"'{slot}'" for slot in unfed)
        raise ValueError(
            f"Step '{step_label}': method '{method_name}' declares "
            f"required input slot(s) {listing}, which nothing feeds (fed: "
            f"{fed_listing}). A slot the cache key is not over lets two "
            f"runs over different data share a key. Wire the slot in the "
            f"pipeline document, or declare it `required: false` in "
            f"method.yaml."
        )


@task(purpose="The one writer of a run's input rows: one row per parent entry, "
              "carrying its input slot and the source slot it names, and one row "
              "per sample the step read, carrying its slot, name and content hash")
def record_run_inputs(
    session,
    run_id: int,
    parent_entries: Sequence[ParentEntry],
    sample_reads: Sequence[SampleRead] = (),
) -> None:
    """Record what a run read, against a run row the session already holds.

    The rows are added to the caller's session and never committed here:
    the claim writes them in the same transaction as their run row, so a
    claim refused or failed before its commit leaves no row of either kind.
    A sample row never names a source run, which is what keeps it out of
    every parent walk.

    Args:
        session: An open session holding the run row (flushed, so it has
            its id); the caller commits.
        run_id: The registered run the rows belong to.
        parent_entries: The run's parsed parent entries.
        sample_reads: The samples the run read, each paired with its slot
            and content hash. Empty for a step that reads no sample.
    """
    rows = [
        {"source_run_id": entry.run_id, "input_name": entry.input_slot,
         "source_slot": entry.source_slot}
        for entry in parent_entries
    ] + [
        {"input_name": read.input_slot, "sample_name": read.sample_name,
         "content_hash": read.content_hash}
        for read in sample_reads
    ]
    for row in rows:
        session.add(RunInput(run_id=run_id, **row))


@task(purpose="Keep a pipeline document by its content: hash its canonical JSON "
              "through Identity and store those bytes, if absent, through "
              "Storage's store path with kind pipeline-document, which also "
              "enters its blob row (insert if absent)",
      inputs="the parsed pipeline document, the project root",
      outputs="the document's canonical-JSON hash, its bytes in the cache",
      critical="the bytes are in the cache before any run row names the hash; "
               "a document stored before is left as it is")
def store_pipeline_document(document: dict, project_dir: Path | str) -> str:
    """Store a pipeline document's canonical JSON and return its hash.

    The launch and the claim both call this on the document they read, so
    two spellings of one document (other whitespace, other key order) are
    one entry. Idempotent: the cache keeps an entry that is already there,
    and the blob row is entered only if absent.

    Args:
        document: The parsed pipeline document.
        project_dir: Root directory of the wfc project.

    Returns:
        The md5 of the document's canonical JSON bytes.

    Raises:
        ValueError: The document holds NaN or an infinity.
    """
    from ..identity import canonical_json_bytes, canonical_json_hash
    from ..storage import KIND_PIPELINE_DOCUMENT, store_env_content

    digest = canonical_json_hash(document)
    stored = store_env_content(canonical_json_bytes(document).decode("utf-8"),
                               project_dir, kind=KIND_PIPELINE_DOCUMENT)
    if stored != digest:  # pragma: no cover - the same bytes, the same md5
        raise RuntimeError(f"stored document hash {stored} != canonical hash {digest}")
    return digest


#: Seconds between two advances of a held claim's heartbeat. A module
#: constant, injectable by tests; there is no configuration key.
HEARTBEAT_INTERVAL_SECONDS = 5.0

#: Seconds a waiting claim watches a running claim's heartbeat stay still
#: before it closes that claim as failed: twelve intervals, timed by the
#: waiter's own monotonic clock and never compared with a stored time. A
#: module constant, injectable by tests.
STALL_WINDOW_SECONDS = 60.0

#: The error a stalled claim is closed with.
STALLED_CLAIM_ERROR = (
    "Its claim stopped responding: the heartbeat of run {run_id} did not "
    "advance for {window:g} s (the process running it was likely killed), so "
    "a claim of the same result closed it as failed and ran the step again."
)

#: The held claims this process is keeping alive, by run id.
_heartbeats: dict[int, _ClaimHeartbeat] = {}
_heartbeats_lock = threading.Lock()


class _ClaimHeartbeat:
    """A daemon thread advancing one held claim's heartbeat at the interval.

    The counter moves only while the row is ``running``; the thread ends on
    :meth:`stop` or when an advance finds the row no longer running. A
    failed advance (the database locked past the busy timeout) is retried at
    the next interval. The engine is the one the claim was written through,
    so a process that binds another project meanwhile never beats into it.
    """

    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self._engine = get_engine()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._beat, name=f"wfc-claim-heartbeat-{run_id}", daemon=True)

    def start(self) -> None:
        """Start advancing the heartbeat."""
        self._thread.start()

    def _beat(self) -> None:
        while not self._stopped.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                with Session(self._engine) as session:
                    result = cast("CursorResult[Any]", session.execute(
                        update(Run)
                        .where(col(Run.id) == self.run_id)
                        .where(col(Run.status) == "running")
                        .values(heartbeat=col(Run.heartbeat) + 1)
                    ))
                    session.commit()
            except Exception:  # noqa: BLE001 - the next interval tries again
                continue
            if not result.rowcount:
                return

    def stop(self) -> None:
        """Stop advancing, and wait for an advance in progress to finish."""
        self._stopped.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=BUSY_TIMEOUT_SECONDS + HEARTBEAT_INTERVAL_SECONDS)


def _start_heartbeat(run_id: int) -> None:
    """Start keeping a held claim alive in this process."""
    beat = _ClaimHeartbeat(run_id)
    with _heartbeats_lock:
        _heartbeats[run_id] = beat
    beat.start()


def stop_heartbeat(run_id: int) -> None:
    """Stop advancing a held claim's heartbeat; a no-op for any other run.

    ``run_step`` calls it when the claim reaches its record phase and on
    every other ending (a failure, an exception, the cache-hit path, which
    never held a claim). Once it returns, nothing writes the run's heartbeat
    again.

    Args:
        run_id: The run whose claim this process held.
    """
    with _heartbeats_lock:
        beat = _heartbeats.pop(run_id, None)
    if beat is not None:
        beat.stop()


def _running_claim(method_id: int, sample: str, cache_key: str) -> int | None:
    """The run holding the running claim of a key, or ``None``.

    The row the running-claim index admits: not a cache-hit audit row,
    ``running`` and carrying the heartbeat.
    """
    with get_session() as session:
        return session.exec(
            select(Run.id)
            .where(Run.method_id == method_id)
            .where(Run.sample == sample)
            .where(Run.cache_key == cache_key)
            .where(Run.cache_source_run_id == None)  # noqa: E711
            .where(Run.status == "running")
            .where(Run.heartbeat != None)  # noqa: E711
        ).first()


def _newest_completed_run(session, method_id: int, sample: str,
                          cache_key: str) -> int | None:
    """The newest completed, non-audit run id under a key (the hit rule's pool)."""
    return session.exec(
        select(func.max(Run.id))
        .where(Run.method_id == method_id)
        .where(Run.sample == sample)
        .where(Run.cache_key == cache_key)
        .where(Run.cache_source_run_id == None)  # noqa: E711
        .where(Run.status == "completed")
    ).one()


def _close_stalled_claim(run_id: int, seen: int | None) -> bool:
    """Close a running claim as failed if its heartbeat is still ``seen``.

    A compare-and-set on the counter value the waiter saw: a claim whose
    heartbeat moved, or that left ``running``, is left alone.

    Returns:
        True when this call closed the claim.
    """
    with write_transaction() as session:
        result = session.execute(
            update(Run)
            .where(col(Run.id) == run_id)
            .where(col(Run.status) == "running")
            .where(col(Run.heartbeat) == seen)
            .values(
                status="failed",
                finished_at=datetime.now(UTC),
                error_message=STALLED_CLAIM_ERROR.format(
                    run_id=run_id, window=STALL_WINDOW_SECONDS),
            )
        )
        closed = bool(result.rowcount)
    return closed


@task(purpose="Wait on the run holding a key's running claim: return when it "
              "leaves running, or close it as failed by a compare-and-set on "
              "the heartbeat value seen when the heartbeat stays still for the "
              "stall window, timed by this process's own monotonic clock")
def wait_on_running_claim(run_id: int, label: str) -> bool:
    """Wait while a running claim shows progress, and take over a stalled one.

    Prints one line naming the run it waits on. The heartbeat is read only
    for movement: each change restarts the waiter's own stall timer, and
    the window is :data:`STALL_WINDOW_SECONDS` of this process's monotonic
    time, so no clock is compared between writers.

    Args:
        run_id: The run holding the claim.
        label: What the claim computes, for the waiting line.

    Returns:
        True when this call closed the claim as failed; False when the claim
        left ``running`` by itself.
    """
    print(f"Waiting on run {run_id}, which is computing the same result "
          f"({label}); this step goes on when it ends.", flush=True)
    seen: int | None = None
    still_since = time.monotonic()
    while True:
        with get_session() as session:
            row = session.get(Run, run_id)
            status = row.status if row is not None else None
            beat = row.heartbeat if row is not None else None
        if status != "running":
            return False
        now = time.monotonic()
        if beat != seen:
            seen, still_since = beat, now
        elif now - still_since >= STALL_WINDOW_SECONDS:
            if _close_stalled_claim(run_id, seen):
                print(f"Run {run_id} stopped responding: closed it as failed; "
                      f"claiming its result.", flush=True)
                return True
            seen = None
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


@task(purpose="Record a claim's code copy as in use, then resolve the method "
              "version's code: the record commits before the claim first "
              "extracts, checks or reuses the copy",
      inputs="module name, method name, version identity (None: the newest), "
             "the claim's token",
      outputs="MethodCode of a copy the claim holds",
      critical="Two entries of one identity can have different trees (line "
               "endings fingerprint alike), so a resolved tree the claim "
               "does not hold yet is recorded and resolved again; nothing "
               "looked at before its record committed is trusted")
def resolve_held_code(module_name: str, method_name: str, version: str | None,
                      claim_token: str):
    """Hold a method version's code copy for a claim, then resolve it.

    The version's list entry is read without extracting anything, its tree
    recorded as in use under the claim's token (Storage), and only then is
    the code resolved, pinned to the entry's identity. A prune that ran
    before the record committed may have removed the copy; the resolver then
    extracts it again. One that runs after finds the record and keeps it.

    Args:
        module_name: The method's module.
        method_name: The method's name.
        version: The version's content identity; ``None`` is the newest.
        claim_token: The claim's token (``storage.new_claim_token``).

    Returns:
        The resolved ``MethodCode``; its copy's tree is held by the token.

    Raises:
        MalformedMethodRecordError: The resolver refused the record.
        UnknownVersionError: No entry carries *version*.
        UnregisteredSnapshotError: The resolved tree kept changing under
            new registrations (the message says to run the step again).
    """
    from ..registration.registered_code import (
        UnregisteredSnapshotError,
        resolve_method_code,
        version_entry,
    )

    held: set[str] = set()
    identity = version
    entry = version_entry(module_name, method_name, version)
    if entry is not None:
        hold_code_copy(entry.tree_hash, claim_token)
        held.add(entry.tree_hash)
        identity = entry.identity
    for _attempt in range(3):
        code = resolve_method_code(module_name, method_name, version=identity)
        if code.entry.tree_hash in held:
            return code
        # Resolved a tree whose record was not written before it was looked
        # at: record it, then resolve again so the copy is checked anew.
        hold_code_copy(code.entry.tree_hash, claim_token)
        held.add(code.entry.tree_hash)
        identity = code.entry.identity
    raise UnregisteredSnapshotError(
        f"Method '{module_name}/{method_name}' kept being registered again while "
        f"this step was being claimed. Run the step again."
    )


class _ResultLanded(Exception):
    """A completed run landed under the key after the hit rule answered."""


def _claim_key(
    method_name: str,
    module_name: str,
    row_fields: dict,
    parent_entries: Sequence[ParentEntry],
    sample_reads: Sequence[SampleRead],
    *,
    hold_claim: bool,
    code_claim: str | None = None,
) -> tuple[str, int]:
    """Write a claim's run row and its input rows, one transaction per attempt.

    The hit rule answers first, outside any transaction (its readers open
    their own sessions). A hit writes the audit row and its input rows
    together, and releases the claim's code-copy records in the same
    transaction (a hit runs no code). A miss writes the running row and its
    input rows together and binds the claim's code-copy records to the run,
    inside a transaction that first checks no completed run landed under the
    key since the hit rule read it (the write lock is held from the start,
    so none can land after). An insert the running-claim index refuses waits
    on the running claim, then asks the hit rule again.

    Args:
        method_name: The method's name.
        module_name: The method's module name.
        row_fields: The run row's columns both kinds of row share.
        parent_entries: The run's parent entries, for its lineage rows.
        sample_reads: The samples the run read, for its sample rows.
        hold_claim: Whether a new row carries the heartbeat and this process
            keeps it alive.
        code_claim: The claim's token, under which its code copy is
            recorded in use; ``None`` when it recorded none.

    Returns:
        ``("CACHED", audit_run_id)`` or ``("NEW", run_id)``.
    """
    method_id = row_fields["method_id"]
    sample = row_fields["sample"]
    cache_key = row_fields["cache_key"]
    label = f"{module_name}/{method_name} on sample '{sample}'"
    refusals_without_a_claim = 0
    while True:
        with get_session() as session:
            known = _newest_completed_run(session, method_id, sample, cache_key)
        hit_id = lookup_cache_hit(method_name, module_name, sample, cache_key)
        if hit_id is not None:
            now = datetime.now(UTC)
            with write_transaction() as session:
                audit_run = Run(**row_fields, status="completed", started_at=now,
                                finished_at=now, cache_source_run_id=hit_id)
                session.add(audit_run)
                session.flush()
                # Lineage for the audit row: without it, PathsView and the
                # Descendants view treat the audit run as disconnected.
                record_run_inputs(session, audit_run.id, parent_entries,
                                  sample_reads)
                if code_claim is not None:
                    release_claim(code_claim, session)
            return ("CACHED", audit_run.id)  # type: ignore[return-value]
        try:
            with write_transaction() as session:
                if _newest_completed_run(session, method_id, sample, cache_key) != known:
                    raise _ResultLanded
                run = Run(**row_fields, status="running",
                          started_at=datetime.now(UTC),
                          heartbeat=0 if hold_claim else None)
                session.add(run)
                session.flush()
                record_run_inputs(session, run.id, parent_entries, sample_reads)
                if code_claim is not None:
                    bind_claim(session, code_claim, run.id)  # type: ignore[arg-type]
        except _ResultLanded:
            continue
        except IntegrityError:
            running = _running_claim(method_id, sample, cache_key)
            if running is None:
                # The claim left running between the refusal and the read;
                # a refusal that no running claim explains is not the index's.
                refusals_without_a_claim += 1
                if refusals_without_a_claim >= 3:
                    raise
                continue
            wait_on_running_claim(running, label)
            continue
        if hold_claim:
            _start_heartbeat(run.id)  # type: ignore[arg-type]
        return ("NEW", run.id)  # type: ignore[return-value]


@workflow(purpose="Version-aware pre-run hook: git commit check, the params "
                  "door (resolve_claim_params: check, normalize, fill declared "
                  "defaults; skipped when run_claim already did), cache "
                  "lookup, run registration")
def pre_run(
    method_name: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    pipeline_id: str | None = None,
    nf_process_name: str | None = None,
    repo_path: str | None = None,
    git_commit: str | None = None,
    nid: str | None = None,
    node_id: str | None = None,
    collapsed_samples: list[str] | None = None,
    selector_slot: str | None = None,
    has_pipeline_document: bool = True,
    wired_slots: dict[str, str] | None = None,
    version: str | None = None,
    pipeline_doc_hash: str | None = None,
    hold_claim: bool = False,
    code_claim: str | None = None,
    params_resolved: bool = False,
) -> tuple[str, int]:
    """Git-commit check, input fingerprinting, cache lookup, and run registration.

    One call covers what the separate ``check_cache`` and ``register_run``
    verbs do, and also enforces version discipline.

    A claim held by its process (``hold_claim``, how ``run_step`` claims)
    computes alone: its run row carries the claim heartbeat, which puts it
    under the running-claim index, so a second claim of the same method,
    sample and cache key is refused the insert. That claim waits on the
    running one, printing one line naming it, and asks the hit rule again
    when it leaves ``running``: a hit becomes its cache-hit audit row, a
    failed run or one whose outputs are missing lets it run. A running claim
    whose heartbeat stays still for :data:`STALL_WINDOW_SECONDS` is closed as
    failed (its claim stopped responding) and the waiter claims the key.

    Args:
        method_name: The registered method to run.
        module_name: The module the method belongs to.
        sample: The sample identity the run is for (``COLLAPSED_SAMPLE`` for
            a collapsed fan-in root).
        params: The run's parameters, as given. Step 3 checks them against
            the version's declared params, normalizes them and fills each
            declared default they leave out (``resolve_claim_params``) unless
            ``params_resolved`` says the caller did; the result is what the
            cache key is over and the run row records. ``None`` means no
            parameters.
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"``; they enter the cache key and become the run's
            lineage rows.
        pipeline_id: The pipeline this run belongs to, stored on the run row.
        nf_process_name: The Nextflow process name, stored on the run row.
        repo_path: Directory within the git repository the commit is read
            from when ``git_commit`` is not supplied.
        git_commit: A pre-resolved commit SHA, recorded on the run row as
            given; when ``None`` the commit is read from ``repo_path`` (a
            dirty tree is refused) and recorded. Not part of the cache key;
            the version's commit is the one registration recorded.
        nid: The node's display label, stored on the run row and used to
            name the step in a missing-input refusal.
        node_id: The pipeline node this run is for, as the document spells
            its id; stamped on the run row (the new run and a cache-hit
            audit row alike). ``None`` leaves the row's node unrecorded.
        collapsed_samples: For a collapsed fan-in root (sample is
            ``COLLAPSED_SAMPLE``),
            the bundled sample identities. They are the root's input identity
            and enter the cache key as a set; see ``compose_cache_key``.
        selector_slot: The input slot this step reads its sample into, as
            ``run_claim`` classified it from the pipeline document; ``None``
            on a direct verb drive, where a parentless step reads into
            ``data``.
        has_pipeline_document: Whether the caller resolved a pipeline
            document for this node. ``False`` says the claim cannot see
            every channel feeding the step -- a ``wfc run-step
            --ref-input`` artifact never reaches here -- so the
            declared-input check is skipped rather than refusing a
            legitimate invocation.
        wired_slots: Each input slot the pipeline document wires into this
            node, mapped to the node that feeds it (any kind: selector,
            method, reference). It separates a required slot the document
            leaves unwired -- the author's to fix -- from one it wires but
            the claim was not handed -- wfc's defect. ``None`` treats every
            unfed slot as unwired.
        version: The method version's content identity the step runs (the
            node's pin, or the newest when ``None``). Its contract is the
            declared-input check's, its fingerprints the key's, and the run
            row records it.
        pipeline_doc_hash: The canonical-JSON hash of the pipeline document
            the caller read, already in the cache under it; recorded on the
            run row, never part of the cache key. ``None`` (no document)
            records none.
        hold_claim: Whether this process runs the claimed step itself and
            keeps the claim alive until its record phase. ``True`` (the
            ``run_step`` claim): a new row carries the heartbeat and this
            process advances it until :func:`stop_heartbeat` is called with
            the run id. ``False`` (the hand-run ``pre_run`` verb, whose
            process exits before the run executes): the row carries no
            heartbeat and stays outside the index, like ``register_run``'s.
        code_claim: The claim's token (``run_claim``'s), under which the
            version's code copy is already recorded in use. ``None``: the
            claim draws its own token and records the copy before step 4
            first reads it (*version* stays as given, so step 5 still
            refuses a registration landing between the key and the
            verify). The
            claim transaction binds the token's records to the new run (a
            hit releases them); a claim that raises releases a token it
            drew.
        params_resolved: Whether ``params`` already came out of
            ``resolve_claim_params`` for this version and node. ``True`` (the
            ``run_claim`` claim, which resolved them for dispatch): step 3 is
            skipped, so a claim reads the version list once. ``False`` (a
            direct ``pre_run``): step 3 checks, normalizes and fills them.

    Returns:
        ``("CACHED", source_run_id)`` — cache hit; source_run_id is the
        original completed run whose archive should be reused.  A new Run row
        with ``cache_source_run_id`` set is inserted for lineage.

        ``("NEW", new_run_id)`` — cache miss; a fresh Run row with
        ``version_id`` and ``cache_key`` is inserted with status='running'.

    Raises:
        DirtyRepositoryError: If the working tree has uncommitted changes and
            ``git_commit`` was not pre-supplied.
        UnregisteredSnapshotError: If the method's registered snapshot is
            not the code of any registered version or holds an untracked
            file (the message names ``wfc register-method``), or if the
            method was registered again between composing the cache key and
            verifying the snapshot (the message says to run the step
            again). No Run or MethodVersion row is written.
        ValueError: If the method or module is not found in the DB, the
            version's parameter check refuses the params (naming the node,
            or the method when no node is given, the version and the
            parameter), or a required input slot the method declares is fed
            by nothing --
            either because the document does not wire it, or because it
            wires it and the input was not delivered.
    """
    from ..registration.registered_code import (
        UnregisteredSnapshotError,
        verify_registered_snapshot,  # from its defining module, so the AutoStep edge resolves
    )
    from ..version import get_git_commit, installed_wfc_version

    params = params or {}

    口 = Step(step_num=1, name="Resolve git commit",
             purpose="Take the caller's commit as given, or read HEAD after "
                     "checking the working tree is clean (raises "
                     "DirtyRepositoryError if dirty); the commit is recorded "
                     "on the run row, never part of the cache key")
    # Provenance, not identity: the commit is where this run came from and
    # step 6 stores it on the row; the code the key is over is the verified
    # version's, whose commit is the one registration recorded.
    if git_commit is None:
        git_commit = get_git_commit(repo_path)

    口 = Step(step_num=2, name="Look up method",
             purpose="Query database for the module and method rows, and read the "
                     "method's registered contract: its declared input slots are "
                     "what step 4 checks the step's wiring against")
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            raise ValueError(f"Module '{module_name}' not found in DB")

        method = session.exec(
            select(Method).where(
                Method.name == method_name,
                Method.module_id == mod.id,
            )
        ).first()
        if method is None:
            raise ValueError(
                f"Method '{method_name}' not found in module '{module_name}'"
            )
        method_id: int = method.id
        method_env: str = method.env
        # The registered contract's input declaration, read off the row
        # already in hand rather than through the whole-registry contract
        # map: this is one method's question.
        from ..registration import method_contract
        version_contract = method_contract(module_name, method_name, version)
        declared_inputs: dict = (
            version_contract.input_slots if version_contract else {}
        ) or {}

    # Every claim goes through the door, not only a document-less one: a
    # `run-step --pipeline-json` on a file never prepared has a document but
    # no fill. On a prepared document's params it changes nothing. The run
    # row and the key below take its output. run_claim passed its claim
    # through already (``params_resolved``), so its params are not resolved
    # twice.
    if not params_resolved:
        口 = AutoStep(step_num=3, name="Check, normalize and fill the params")
        params = resolve_claim_params(module_name, method_name, params,
                                      version=version, node_id=node_id)

    # The code copy the claim runs is recorded in use before the claim first
    # reads it (step 4 composes the key over it). run_claim holds it already
    # and hands its token; any other caller's claim holds it here.
    own_claim = code_claim is None
    if own_claim:
        code_claim = new_claim_token()
        try:
            resolve_held_code(module_name, method_name, version, code_claim)
        except BaseException:
            release_claim(code_claim)
            raise

    try:
        口 = AutoStep(step_num=4, name="Compose cache key")
        composed = compose_cache_key(
            method_name=method_name,
            method_env=method_env,
            module_name=module_name,
            sample=sample,
            params=params,
            parent_run_ids=parent_run_ids,
            collapsed_samples=collapsed_samples,
            selector_slot=selector_slot,
            version=version,
        )
        # Every input slot the method declares REQUIRED has to be fed by
        # something the key is over; refused after the recipe resolved which
        # slots the step feeds, and before step 5 verifies the snapshot or step
        # 6 writes a run row. A caller with no pipeline document is exempt (see
        # ``check_required_inputs``).
        if has_pipeline_document:
            check_required_inputs(
                declared_inputs, composed.parent_entries, composed.sample_inputs,
                wired_slots, step_label=nid or method_name, method_name=method_name,
            )
        cache_key = composed.cache_key
        env_fingerprint = composed.env_fingerprint
        parent_entries = composed.parent_entries

        # The claim never mints a version: registration is the only writer. A
        # snapshot that is not the code of a registered version (edited after
        # registration, holding a stray file) is refused here, before step 6
        # writes any Run row. The verified snapshot must also be the one step 4
        # composed the key over: a registration landing between the two reads
        # would otherwise key the run on one version's code and record another.
        口 = AutoStep(step_num=5, name="Verify the registered snapshot")
        registered = verify_registered_snapshot(module_name, method_name,
                                                version=version)
        if (registered.code_fingerprint != composed.code_fingerprint
                or registered.code.entry.env_fingerprint != composed.env_fingerprint):
            raise UnregisteredSnapshotError(
                f"Method '{module_name}/{method_name}' was registered again while "
                f"this step was being claimed, so the code its cache key was "
                f"composed over is no longer the registered copy. Run the step "
                f"again."
            )
        version_id = registered.version_id
        # The claimed version's env, the entry the env fingerprint came from --
        # never ``Method.env``, which names the newest version's env while a
        # node may be pinned to an older one.
        env_name = registered.code.entry.env_name
        wfc_version = installed_wfc_version()

        口 = Step(step_num=6, name="Cache lookup and registration",
                 purpose="Ask the hit rule (classify_cache_key) for the newest "
                         "completed run of THIS module's method on the sample under "
                         "cache_key whose every output is readable: local (DVC "
                         "cache or run archive) or remote (pushed, pulled on read); "
                         "a run with an output missing is never reused. On a hit, "
                         "write the cache-hit audit row and its input rows in one "
                         "write transaction; on a miss or outputs_missing, write "
                         "the new running row (with its heartbeat, for a held "
                         "claim) and its input rows in one. Either row carries, in "
                         "its own INSERT, this claim's commit, the claimed "
                         "version's env name, the installed wfc version and the "
                         "pipeline document's hash; the input rows are one lineage "
                         "row per parent entry, carrying its input slot and the "
                         "source slot it names, and one sample row per sample the "
                         "step read, carrying its slot, name and content hash. An "
                         "insert the running-claim index refuses waits on the "
                         "running claim and asks the hit rule again",
                 critical="The hit rule runs outside the write transaction: its "
                          "readers open their own sessions (mark_changed). A "
                          "completed run landing between the hit rule's answer "
                          "and the insert sends the claim back to the hit rule, "
                          "so a waiter never computes a result that just landed")
        row_fields = dict(
            method_id=method_id,
            params=params,
            sample=sample,
            pipeline_id=pipeline_id,
            nf_process_name=nf_process_name,
            version_id=version_id,
            cache_key=cache_key,
            key_recipe=KEY_RECIPE,
            env_fingerprint=env_fingerprint,
            nid=nid,
            node_id=node_id,
            git_commit=git_commit,
            env_name=env_name,
            wfc_version=wfc_version,
            pipeline_doc_hash=pipeline_doc_hash,
        )
        flag, run_id = _claim_key(
            method_name, module_name, row_fields, parent_entries,
            composed.sample_reads, hold_claim=hold_claim,
            code_claim=code_claim,
        )
        if flag == "CACHED":
            # The return value is the newly-inserted audit row, not the cached
            # source. Callers (run_step, sidecar writer) then record lineage and
            # write the run-id sidecars with the audit ID, so downstream nodes
            # wire their ``parent_run_ids`` to this pipeline's sibling audits
            # instead of the old pipeline's source runs. The cached source is
            # still reachable via ``Run.cache_source_run_id`` when a caller
            # actually needs it (e.g. to find the RunOutput rows for restore).
            return ("CACHED", run_id)

        # The run-archive directory is made after the rows are committed. A
        # held claim whose directory cannot be made stops its heartbeat, so a
        # later claim of the key is not kept waiting on a claim nobody runs.
        try:
            layout.run_archive_dir(get_project_root(), run_id).mkdir(
                parents=True, exist_ok=True)
        except BaseException:
            stop_heartbeat(run_id)
            raise
        return ("NEW", run_id)
    except BaseException:
        if own_claim and code_claim is not None:
            release_claim(code_claim)
        raise


@task(purpose="Check, normalize and fill a run's params against the newest "
              "version (resolve_claim_params), then register the run in the "
              "database with status='running'")
def register_run(
    method_name: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    nf_process_name: str | None = None,
    pipeline_id: str | None = None,
) -> int:
    """Insert a new run (status='running') and return the run ID.

    ``parent_run_ids`` holds parent entries as ``"input:output:run"`` or
    ``"input:run"``; fan-in is several entries sharing an input (e.g.
    ``["sources:5", "sources:8"]``).  Each entry becomes a ``RunInput`` row
    carrying its input slot and the source slot it names.

    ``params`` go through ``resolve_claim_params`` against the newest
    version before the row is written: the row records them normalized and
    filled with the declared defaults, and a refusal prints ``ERROR:`` and
    exits 1, as the verb's other errors do.
    """
    parent_entries: list[ParentEntry] = []
    if parent_run_ids:
        parent_entries = parse_parent_entries(parent_run_ids, step=method_name)

    with get_session() as session:
        from ..persistence import Module
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            print(f"ERROR: module '{module_name}' not found in DB", file=sys.stderr)
            sys.exit(1)
        stmt = select(Method).where(
            Method.name == method_name,
            Method.module_id == mod.id,
        )
        method = session.exec(stmt).first()
        if method is None:
            print(f"ERROR: method '{method_name}' not found in module '{module_name}'", file=sys.stderr)
            sys.exit(1)

        # The row records the params a claim would: checked, normalized and
        # filled against the newest version.
        try:
            口 = AutoStep(step_num=1, name="Check, normalize and fill the params")
            params = resolve_claim_params(module_name, method_name, params)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        run = Run(
            method_id=method.id,
            params=params,
            sample=sample,
            status="running",
            pipeline_id=pipeline_id,
            nf_process_name=nf_process_name,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id_val: int = run.id  # type: ignore[assignment]

        record_run_inputs(session, run_id_val, parent_entries)
        session.commit()

        # Create the archive directory so the method can write to it
        layout.run_archive_dir(get_project_root(), run_id_val).mkdir(parents=True, exist_ok=True)

    return run_id_val


def lookup_run(method_name: str, module_name: str, sample: str,
               nf_process_name: str | None = None) -> int | None:
    """Find the most-recent completed run for a module's method + sample.

    Legacy verb — pipelines resolve their parents through the run_id.txt
    sidecars and take the claim phase's cache answer instead.

    Args:
        method_name: The method's name.
        module_name: The name of the module that registered the method.
        sample: The sample identifier.
        nf_process_name: Optional Nextflow process name to match.

    Returns:
        The run id, or ``None`` when no completed run matches.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, col(Run.method_id) == col(Method.id))
            .join(Module, col(Method.module_id) == col(Module.id))
            .where(Method.name == method_name)
            .where(Module.name == module_name)
            .where(Run.sample == sample)
            .where(Run.status == "completed")
        )
        if nf_process_name is not None:
            stmt = stmt.where(Run.nf_process_name == nf_process_name)
        stmt = stmt.order_by(col(Run.finished_at).desc())
        run = session.exec(stmt).first()
        return run.id if run else None


@task(purpose="Claim phase: resolve node config and parent runs, register the "
              "run, and carry the cache decision",
      inputs="node identity, inline args or pipeline JSON, optional CLI parent entries",
      outputs="cache flag + run id + resolved config and parent wiring, "
              "or an early-exit rc")
def run_claim(
    node_id: str,
    sample: str,
    variant: str,
    method_name: str | None,
    module_name: str | None,
    params: dict | None,
    parent_run_ids: list | None,
    pipeline_id: str | None,
    pipeline_json: str | None,
    git_commit: str | None,
    collapsed_samples: list[str] | None = None,
) -> dict:
    """Resolve config and parents, then register the run.

    The executed script is never taken from the caller or the document: it
    is Registration's resolver's answer for the module and method, and the
    claim hands it (with the snapshot directory) to dispatch.

    Args:
        node_id: Unique node identity within the pipeline.
        sample: Sample identifier.
        variant: Parameter variant name.
        method_name: Method name (inline fallback).
        module_name: Module name (inline fallback).
        params: Parameter dict (inline fallback).
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"`` strings; a bare run id is refused. ``None``
            triggers the sidecar walk.
        pipeline_id: Pipeline execution ID (env fallback applies).
        pipeline_json: Path to the pipeline JSON file (env fallback applies).
        git_commit: Pre-computed git commit SHA.
        collapsed_samples: For a collapsed fan-in root, the bundled sample
            identities. Registration needs them because they are the root's
            input identity and so belong in its cache key; materialize
            receives the same value separately to resolve the data files.

    Returns:
        ``{"ok": False, "rc": 1}`` on an early exit (error already printed;
        no run row written). Inside a pipeline -- a document is present
        and a pipeline id was given -- a refusal after the node resolves
        also writes the target's failed claim-refusal outcome sidecar, which
        the pipeline-end walk turns into its failed row. Otherwise ``{"ok": True, ...}`` with:
        ``flag`` (``"NEW"``/``"CACHED"``), ``run_id``, the resolved
        ``method_name``/``module_name``, the ``params`` the run row records
        (checked, normalized and filled with the version's declared
        defaults; a parameter refusal is a ``pre_run failed:`` refusal), the resolved
        ``script_path`` inside the version's extracted copy ``code_dir``,
        the ``version`` identity it ran,
        ``pipeline_id``/``pipeline_json``, the parsed ``pipeline_data``
        document (or ``None``), the node's ``node_cfg``/``slot_outputs``/
        ``slot_types`` output declarations, and the sidecar-derived
        ``parent_run_ids`` entries (``input:output:run``, or ``input:run``
        for a link naming no output; CLI-provided entries as typed).
        ``parent_run_ids`` is sidecar-derived only: reference-derived
        parents are registered but never returned, because materialize
        resolves every entry it is handed and branches on the list being
        empty.
    """
    口 = Step(step_num=1, name="Resolve step config",
             purpose="Load node config (method, module, params) from pipeline "
                     "JSON or inline args; the script is not part of it -- a "
                     "document's legacy `script` key is ignored and the "
                     "executed script comes from the registered-code resolver")
    if pipeline_json is None:
        pipeline_json = os.environ.get("WFC_PIPELINE_JSON")

    if pipeline_id is None:
        pipeline_id = os.environ.get("WFC_PIPELINE_ID")
    # A claim inside a pipeline leaves its refusal where the pipeline-end
    # walk finds it; a standalone drive has no walk to read one.
    in_pipeline = pipeline_id is not None
    if pipeline_id is None:
        pipeline_id = "standalone"

    if pipeline_json and Path(pipeline_json).exists():
        # Load from pipeline JSON
        raw = json.loads(Path(pipeline_json).read_text(encoding="utf-8"))
        # A node's identity is its document id.
        node = document_node(raw, node_id)
        if node is None:
            print(f"ERROR: node '{node_id}' not found in pipeline JSON", file=sys.stderr)
            return {"ok": False, "rc": 1}
        method_name = method_name or node["method"]
        module_name = module_name or node["module"]
        if params is None:
            # Look up variant params from the node's param_sets entry or its
            # own params
            ps = raw.get("param_sets", {})
            node_ps = ps.get(node_id, {})
            params = node_ps.get(variant, node.get("params", {}))

    # Validate required inline args
    if not method_name or not module_name:
        print("ERROR: --method and --module are required "
              "when --pipeline-json is not provided", file=sys.stderr)
        return {"ok": False, "rc": 1}
    params = params or {}

    # Extract custom NID label from pipeline JSON node (if present).
    # Parse pipeline JSON once and reuse for both NID extraction and
    # parent run resolution below.
    # The run records the node it ran as: the resolved node's raw document
    # id, or, with no document, the node id as the caller gave it.
    nid_label: str | None = None
    run_node_id: str = node_id
    node_version: str | None = None
    pipeline_data: dict | None = None
    if pipeline_json and Path(pipeline_json).exists():
        pipeline_data = json.loads(Path(pipeline_json).read_text(encoding="utf-8"))
        current_node = document_node(pipeline_data, node_id) or {}
        nid_label = current_node.get("label") or None
        if "id" in current_node:
            run_node_id = str(current_node["id"])
        # The version the node runs: the frozen document's pin (every
        # prepared document carries one on each method node); with no
        # document, a direct drive, the newest.
        node_version = current_node.get("version") or None

    def _refuse(message: str) -> dict:
        """Print a refusal and, inside a pipeline, leave its outcome sidecar."""
        print(f"ERROR: {message}", file=sys.stderr)
        if in_pipeline and pipeline_data is not None:
            write_claim_refusal(pipeline_id, node_id, sample, variant, message)
        return {"ok": False, "rc": 1}

    口 = Step(step_num=2, name="Resolve parent runs from sidecars and references",
             purpose="Ask Graph for the links into this node and build one "
                     "input:output:run entry per link; a wired upstream contributes "
                     "the run its run_id.txt sidecar names, which must be that "
                     "node's completed run for this sample and pipeline, and must "
                     "declare the output the link names; a selector contributes "
                     "no entry; a run_reference contributes the run id it names. The "
                     "two arrive on separate channels because only one of them is "
                     "a path materialize must resolve")
    # Resolve parent run IDs from sentinel sidecars if not provided.
    # Sidecars live next to the sentinel files (layout.run_id_sidecar_path).
    #
    # Each entry carries the link's source slot (input:output:run), or names
    # no output (input:run) when the link names none. CLI-provided parents
    # skip the walk and arrive as typed.
    #
    # reference_parents is the SECOND channel and it is deliberately not
    # merged into parent_run_ids. A run_reference never executes, so its
    # artifact reaches the method through --ref-input, which the orchestrator
    # already resolved; the run id it names is lineage and cache-key input
    # only. Leaking it into the list this function RETURNS would do two wrong
    # things at once: materialize's classifier branches on whether that list
    # is empty (so a reference-rooted node would stop suppressing the sample
    # fallback), and materialize resolves every entry in it through the input
    # resolver (so the artifact would be filled into a slot the reference
    # merge is about to fill again). Registration gets both channels; the
    # return value carries the sidecar channel alone.
    #
    # ``wired`` is the document's side of the same question: every slot some
    # link feeds, whatever its kind, and the node feeding it. pre_run reads
    # it to tell an unwired slot from one wired but not delivered.
    reference_parents: list[str] = []
    wired: dict[str, str] = {}
    if pipeline_data is not None:
        for wire in inbound_wiring(pipeline_data, node_id):
            wired.setdefault(wire.target_slot, wire.raw_source_id)
    if parent_run_ids is None:
        parent_run_ids = []
        sentinel_root = get_project_root()
        if pipeline_data is not None:
            for wire in inbound_wiring(pipeline_data, node_id):
                slot = wire.target_slot

                # Graph answered the wiring (kind, canonical id, slots, the
                # referenced run id); the executor's part starts here.
                if wire.source_kind == "run_reference":
                    ref_run_id = wire.run_id
                    if not ref_run_id:
                        # The node names no run at all — there is nothing to
                        # record and nothing to invalidate against. The load
                        # refuses such a reference before any step claims, so
                        # a launched pipeline never reaches this line.
                        continue
                    if not _run_exists(ref_run_id):
                        return _refuse(
                            f"node '{node_id}' input slot '{slot}' is "
                            f"fed by run_reference node '{wire.raw_source_id}', "
                            f"which names run '{ref_run_id}' — no such run. A "
                            f"reference that cannot be resolved would leave the "
                            f"consumer's provenance and cache key silently "
                            f"incomplete; fix or remove the reference."
                        )
                    # The run exists; its artifact must resolve for the
                    # output the link names, by the load's own rule. A run
                    # recorded as a parent and keyed on for an artifact the
                    # method never receives would be false provenance.
                    unresolved = _unresolved_reference(wire, ref_run_id)
                    if unresolved is not None:
                        return _refuse(
                            f"node '{node_id}' input slot '{slot}' is fed by "
                            f"run_reference node '{wire.raw_source_id}': "
                            f"{unresolved}"
                        )
                    reference_parents.append(
                        spell_parent_entry(slot, wire.source_slot, ref_run_id)
                    )
                    continue

                if wire.source_kind == "input_selector":
                    # A selector never runs and writes no sidecar; its slot
                    # is the sample the classifier below hands materialize.
                    continue

                src_nid = wire.source_id
                # Sentinel outputs are scoped by pipeline_id, so
                # parent sidecar lookups include pipeline_id too. An
                # un-scoped lookup finds nothing or a stale sidecar
                # from a prior session, leaving WFC_INPUT_PATHS empty.
                sidecar = layout.run_id_sidecar_path(
                    sentinel_root, pipeline_id, src_nid, sample, variant
                )
                # The sidecar must name the wired node's completed run for
                # this sample and pipeline; a missing one is refused for an
                # optional slot as for a required one. The run records the
                # node's raw document id, the path its canonical id.
                口 = AutoStep(step_num=2.1, name="Check the run the sidecar names")
                named = check_sidecar_run(
                    sidecar, node_id=node_id, slot=slot,
                    source_node_id=wire.raw_source_id, sample=sample,
                    pipeline_id=pipeline_id,
                )
                if named.refusal is not None:
                    return _refuse(named.refusal)
                pid = named.run_id
                source_slot = wire.source_slot
                declared = (wire.source_node or {}).get("slot_outputs") or {}
                if source_slot and declared and source_slot not in declared:
                    return _refuse(
                        f"node '{node_id}' input slot '{slot}' is "
                        f"wired to output slot '{source_slot}' of parent "
                        f"node '{src_nid}', but that node declares no such "
                        f"output slot. Declared output slots: "
                        f"{', '.join(declared)}."
                    )
                parent_run_ids.append(
                    spell_parent_entry(slot, source_slot, pid)
                )

    # Registration sees both channels, in document order within each.
    registration_parents = list(parent_run_ids) + reference_parents

    # Which input slot this node reads its sample into, asked of the same pure
    # classifier materialize asks, with the same argument: the SIDECAR-ONLY
    # parent list. Fed the merged list instead, a reference-rooted node would
    # classify one way here and another in materialize, and the sample it
    # actually reads would be a sample it keyed nothing from.
    from .materialize import classify_input_sources

    try:
        selector_slot = classify_input_sources(
            pipeline_data, node_id, parent_run_ids, None,
        )["selector_slot"]
    except Exception as exc:
        return _refuse(f"pre_run failed: {exc}")

    # What dispatch runs: the resolver's script inside the method's snapshot
    # -- never a path from the caller or the document. Resolved before
    # pre_run, so a resolver refusal leaves no Run row behind; pre_run's
    # verify rule then checks the same snapshot.
    # The copy the run executes is recorded in use under this claim's token
    # before the claim first looks at it, so a prune keeps it; every refusal
    # from here releases the record, and the claim transaction binds it to
    # the run.
    code_claim = new_claim_token()

    def _refuse_held(message: str) -> dict:
        """Release the claim's code-copy record, then refuse."""
        release_claim(code_claim)
        return _refuse(message)

    try:
        口 = AutoStep(step_num=3, name="Hold and resolve the executed script")
        code = resolve_held_code(module_name, method_name, node_version, code_claim)
    except Exception as exc:
        return _refuse_held(str(exc))

    # The document this claim read is in the cache before its row names it:
    # a launch stored it already (the store is idempotent), a standalone
    # `wfc run-step --pipeline-json` stores it here.
    pipeline_doc_hash: str | None = None
    if pipeline_data is not None:
        try:
            口 = AutoStep(step_num=4, name="Keep the pipeline document")
            pipeline_doc_hash = store_pipeline_document(pipeline_data,
                                                        get_project_root())
        except Exception as exc:
            return _refuse_held(f"could not store the pipeline document: {exc}")

    try:
        # Dispatch hands the method (as WFC_PARAMS) the dict returned below,
        # so it is the door's output, the dict pre_run keys and records;
        # pre_run is told so and does not pass it through the door again.
        口 = AutoStep(step_num=5, name="Check, normalize and fill the params")
        params = resolve_claim_params(module_name, method_name, params,
                                      version=code.entry.identity,
                                      node_id=run_node_id)
        口 = AutoStep(step_num=6, name="Pre-run")
        flag, run_id = pre_run(
            method_name=method_name,
            module_name=module_name,
            sample=sample,
            params=params,
            parent_run_ids=registration_parents if registration_parents else None,
            pipeline_id=pipeline_id,
            git_commit=git_commit,
            nid=nid_label,
            node_id=run_node_id,
            collapsed_samples=collapsed_samples,
            selector_slot=selector_slot,
            # With no document the claim cannot see every channel feeding
            # the node -- a `wfc run-step --ref-input label=path` artifact
            # goes straight to materialize -- so the declared-input check
            # would refuse a legitimate invocation.
            has_pipeline_document=pipeline_data is not None,
            wired_slots=wired,
            # The version the resolver answered, so the key, the verify rule
            # and the run row are that version's even if the method is
            # registered again meanwhile.
            version=code.entry.identity,
            pipeline_doc_hash=pipeline_doc_hash,
            # This process runs the step: the claim is held, and run_step
            # stops its heartbeat at the record phase.
            hold_claim=True,
            code_claim=code_claim,
            # Resolved at step 5 against this version and node.
            params_resolved=True,
        )
    except Exception as exc:
        return _refuse_held(f"pre_run failed: {exc}")

    # Output-slot declarations from the pipeline JSON (consumed by dispatch's
    # run context and collect's slot scan). slot_outputs (filename per slot)
    # and slot_types (type per slot) come from the pipeline JSON emitted by
    # _enrich_pipeline. A node with no slot_outputs has one output, under the
    # slot name "output", which resolve_node_outputs names at collect time.
    slot_outputs: dict[str, str] = {}
    slot_types: dict[str, str] = {}
    node_cfg: dict = {}
    try:
        if pipeline_json and Path(pipeline_json).exists():
            raw = json.loads(Path(pipeline_json).read_text(encoding="utf-8"))
            node_cfg = document_node(raw, node_id) or {}
            slot_outputs = node_cfg.get("slot_outputs", {}) or {}
            slot_types = node_cfg.get("slot_types", {}) or {}
    except BaseException:
        # The claim never reaches run_step's phases: its heartbeat stops
        # and its code copy is released.
        stop_heartbeat(run_id)
        release_claim(code_claim)
        raise

    return {
        "ok": True,
        "flag": flag,
        "run_id": run_id,
        "method_name": method_name,
        "module_name": module_name,
        "script_path": str(code.script),
        # The version's extracted copy: dispatch reads its method.yaml and
        # binds it read-only; ``version`` is the identity it re-checks.
        "code_dir": str(code.code_dir),
        "version": code.entry.identity,
        "params": params,
        "pipeline_id": pipeline_id,
        "pipeline_json": pipeline_json,
        "pipeline_data": pipeline_data,
        "node_cfg": node_cfg,
        "slot_outputs": slot_outputs,
        "slot_types": slot_types,
        "parent_run_ids": parent_run_ids,
    }
