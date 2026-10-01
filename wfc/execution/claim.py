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
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import col, select

from .. import layout
from ..graph import document_node, inbound_wiring
from ..persistence import Method, Module, Run, RunInput, RunOutput, Sample, get_session
from ..persistence import project_root as get_project_root
from ..storage import (
    OUTPUT_MISSING,
    OUTPUT_REMOTE,
    has_malformed_output_records,
    output_location,
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


def _verdict_for_run(run_id: int, rows, project_dir: Path) -> CacheVerdict:
    """The verdict for one candidate run from its output records."""
    outputs = tuple(
        OutputLocation(row.slot, output_location(row, project_dir))
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

    Among well-formed candidates, the newest whose outputs are all readable
    wins. When none is, the newest well-formed candidate is reported as
    ``outputs_missing``: the step re-runs under the same key, so its
    downstream steps keep theirs.

    ``pre_run`` step 5 and the ``check_cache`` verb (through
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
        for run in session.exec(stmt).all():
            if has_malformed_output_records(run.id, session=session):
                continue
            rows = session.exec(
                select(RunOutput).where(RunOutput.run_id == run.id)
                .order_by(col(RunOutput.id))
            ).all()
            verdict = _verdict_for_run(run.id, rows, project_dir)
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


@task(purpose="Resolve a method's code fingerprint (its registered source copy and "
              "the contract declared there) and its env fingerprint (a manifest read, "
              "no capture) -- the non-pure half of the key recipe")
def method_fingerprints(method_name: str, method_env: str) -> tuple[str, str]:
    """The method-level fingerprints a cache key is over.

    Args:
        method_name: The method's name; its registered source copy is the
            method dir of that name.
        method_env: The method's stored env spec.

    Returns:
        ``(code_fingerprint, env_fingerprint)``.

    Raises:
        ValueError: The method declares no env, or its registered source
            copy cannot be fingerprinted or holds no ``method.yaml``.
    """
    from ..contracts import (
        parse_env_spec,
        parse_method_yaml,
        render_contract_projection,
    )
    from ..environments import resolve_env_fingerprint
    from ..identity import build_code_fingerprint

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
    # The contract that enters the key is the SNAPSHOT's method.yaml, never
    # the MethodContract row: register_method's own docstring records that a
    # refusal at its later steps leaves the earlier rows committed, so row
    # and snapshot can desync. The snapshot is already the authority for a
    # method's code identity; it is the authority for its contract identity
    # too, so the two can never drift apart under one lookup rule.
    method_source_dir = layout.method_dir(get_project_root(), method_name)
    contract_projection = render_contract_projection(
        parse_method_yaml(method_source_dir)
    )
    code_fingerprint = build_code_fingerprint(
        method_source_dir, contract_projection
    )

    口 = Step(step_num=2, name="Resolve env fingerprint",
             purpose="Resolve the method's env to the env_fingerprint persisted on "
                     "the Run row: the stored value goes through the env-spec "
                     "grammar's read side (a legacy container: prefix stripped "
                     "once), and a registered container env returns the manifest's "
                     "precomputed fingerprint directly (no cache write)")
    env_fingerprint = resolve_env_fingerprint(
        parse_env_spec(method_env), get_project_root()
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
    code_fingerprint, env_fingerprint = method_fingerprints(method_name, method_env)

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
        params: The step's parameters.
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"`` strings; empty for a root step. A bare run id
            is refused -- every entry names the input slot it feeds.

    Returns:
        The 64-char hex cache key.

    Raises:
        ValueError: If the module is not registered, the method is not
            registered in it, the method declares no env, its registered
            source copy cannot be fingerprinted, or a parent reference is not
            an integer run id.
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
    """Record what a run read, against a run row that already exists.

    Called only after the run row is committed, so a claim refused before
    registration leaves no input row of either kind. A sample row never
    names a source run, which is what keeps it out of every parent walk.

    Args:
        session: An open session; committed when any row was added.
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
    if rows:
        session.commit()


@workflow(purpose="Version-aware pre-run hook: git commit check, cache lookup, run registration")
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
) -> tuple[str, int]:
    """Git-commit check, input fingerprinting, cache lookup, and run registration.

    One call covers what the separate ``check_cache`` and ``register_run``
    verbs do, and also enforces version discipline.

    Args:
        method_name: The registered method to run.
        module_name: The module the method belongs to.
        sample: The sample identity the run is for (``COLLAPSED_SAMPLE`` for
            a collapsed fan-in root).
        params: The run's parameters; part of the cache key and stored on the
            run row. ``None`` means no parameters.
        parent_run_ids: Parent entries as ``"input:output:run"`` or
            ``"input:run"``; they enter the cache key and become the run's
            lineage rows.
        pipeline_id: The pipeline this run belongs to, stored on the run row.
        nf_process_name: The Nextflow process name, stored on the run row.
        repo_path: Directory within the git repository the commit is read
            from when ``git_commit`` is not supplied.
        git_commit: A pre-resolved commit SHA; when ``None`` the commit is
            read from ``repo_path`` and a dirty tree is refused. Recorded as
            audit metadata on the method version, not part of the cache key.
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

    Returns:
        ``("CACHED", source_run_id)`` — cache hit; source_run_id is the
        original completed run whose archive should be reused.  A new Run row
        with ``cache_source_run_id`` set is inserted for lineage.

        ``("NEW", new_run_id)`` — cache miss; a fresh Run row with
        ``version_id`` and ``cache_key`` is inserted with status='running'.

    Raises:
        DirtyRepositoryError: If the working tree has uncommitted changes and
            ``git_commit`` was not pre-supplied.
        ValueError: If the method or module is not found in the DB, or a
            required input slot the method declares is fed by nothing --
            either because the document does not wire it, or because it
            wires it and the input was not delivered.
    """
    from ..registration.method_version import (
        get_or_create_version,  # from its defining module, so the AutoStep edge resolves
    )
    from ..version import get_git_commit

    params = params or {}

    口 = Step(step_num=1, name="Resolve git commit",
             purpose="Check working tree is clean; raises DirtyRepositoryError if dirty")
    # git_commit is kept as audit metadata only — not part of cache key.
    if git_commit is None:
        git_commit = get_git_commit(repo_path)

    口 = Step(step_num=2, name="Look up method",
             purpose="Query database for the module and method rows, and read the "
                     "method's registered contract: its declared input slots are "
                     "what step 3 checks the step's wiring against")
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
        declared_inputs: dict = (
            method.contract.input_slots if method.contract else {}
        ) or {}

    口 = AutoStep(step_num=3, name="Compose cache key")
    composed = compose_cache_key(
        method_name=method_name,
        method_env=method_env,
        module_name=module_name,
        sample=sample,
        params=params,
        parent_run_ids=parent_run_ids,
        collapsed_samples=collapsed_samples,
        selector_slot=selector_slot,
    )
    # Every input slot the method declares REQUIRED has to be fed by
    # something the key is over; refused after the recipe resolved which
    # slots the step feeds, and before step 4 creates a version row or step
    # 5 writes a run row. A caller with no pipeline document is exempt (see
    # ``check_required_inputs``).
    if has_pipeline_document:
        check_required_inputs(
            declared_inputs, composed.parent_entries, composed.sample_inputs,
            wired_slots, step_label=nid or method_name, method_name=method_name,
        )
    cache_key = composed.cache_key
    env_fingerprint = composed.env_fingerprint
    parent_entries = composed.parent_entries

    口 = AutoStep(step_num=4, name="Get or create method version")
    version_id = get_or_create_version(
        method_id, composed.code_fingerprint, git_commit=git_commit
    )

    口 = Step(step_num=5, name="Cache lookup and registration",
             purpose="Ask the hit rule (classify_cache_key) for the newest "
                     "completed run of THIS module's method on the sample under "
                     "cache_key whose every output is readable: local (DVC "
                     "cache or run archive) or remote (pushed, pulled on read); "
                     "a run with an output missing is never reused. Return "
                     "CACHED:{id} on hit or register a new run on a miss or "
                     "outputs_missing. Either row records, through the one writer, "
                     "one lineage row per parent entry, carrying its input slot and "
                     "the source slot it names, and one sample row per sample the "
                     "step read, carrying its slot, name and content hash")
    hit_id = lookup_cache_hit(method_name, module_name, sample, cache_key)

    if hit_id is not None:
        # -- Cache HIT: insert an audit Run row, return the AUDIT row's ID --
        # The return value is the newly-inserted audit row, not the cached
        # source. Callers (run_step, sidecar writer) then record lineage and
        # write the run-id sidecars with the audit ID, so downstream nodes
        # wire their ``parent_run_ids`` to this pipeline's sibling audits
        # instead of the old pipeline's source runs. The cached source is
        # still reachable via ``Run.cache_source_run_id`` when a caller
        # actually needs it (e.g. to find the RunOutput rows for restore).
        source_run_id: int = hit_id
        with get_session() as session:
            audit_run = Run(
                method_id=method_id,
                params=params,
                sample=sample,
                status="completed",
                pipeline_id=pipeline_id,
                nf_process_name=nf_process_name,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                version_id=version_id,
                cache_key=cache_key,
                cache_source_run_id=source_run_id,
                env_fingerprint=env_fingerprint,
                nid=nid,
                node_id=node_id,
            )
            session.add(audit_run)
            session.commit()
            session.refresh(audit_run)
            audit_run_id: int = audit_run.id  # type: ignore[assignment]

            # Record lineage for the audit row. Without these rows, PathsView
            # and the Descendants view treat the audit run as disconnected —
            # every cache-hit branch of a fan-out pipeline shows up as an
            # orphan instead of as a terminal in its sample's sub-DAG.
            record_run_inputs(
                session, audit_run_id, parent_entries, composed.sample_reads,
            )
        return ("CACHED", audit_run_id)

    # -- Cache MISS: register fresh run --
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        run = Run(
            method_id=method_id,
            params=params,
            sample=sample,
            status="running",
            pipeline_id=pipeline_id,
            nf_process_name=nf_process_name,
            started_at=datetime.now(UTC),
            version_id=version_id,
            cache_key=cache_key,
            env_fingerprint=env_fingerprint,
            nid=nid,
            node_id=node_id,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        new_run_id: int = run.id  # type: ignore[assignment]

        record_run_inputs(
            session, new_run_id, parent_entries, composed.sample_reads,
        )

        layout.run_archive_dir(get_project_root(), new_run_id).mkdir(parents=True, exist_ok=True)

    return ("NEW", new_run_id)


@task(purpose="Register a new run in the database with status='running'")
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

        # Create the archive directory so the method can write to it
        layout.run_archive_dir(get_project_root(), run_id_val).mkdir(parents=True, exist_ok=True)

    return run_id_val


def lookup_run(method_name: str, sample: str, nf_process_name: str | None = None) -> int | None:
    """Find the most-recent completed run for a method + sample.

    Legacy verb — pipelines resolve their parents through the run_id.txt
    sidecars and take the claim phase's cache answer instead.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, col(Run.method_id) == col(Method.id))
            .where(Method.name == method_name)
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
    script_path: str | None,
    params: dict | None,
    parent_run_ids: list | None,
    pipeline_id: str | None,
    pipeline_json: str | None,
    git_commit: str | None,
    collapsed_samples: list[str] | None = None,
) -> dict:
    """Resolve config and parents, then register the run.

    Args:
        node_id: Unique node identity within the pipeline.
        sample: Sample identifier.
        variant: Parameter variant name.
        method_name: Method name (inline fallback).
        module_name: Module name (inline fallback).
        script_path: Path to method script (inline fallback).
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
        ``method_name``/``module_name``/``script_path``/``params``,
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
             purpose="Load node config from pipeline JSON or inline args")
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
        raw = json.loads(Path(pipeline_json).read_text())
        # Graph's identity rule: the node's own id, else (legacy numeric-id
        # documents) its method name.
        node = document_node(raw, node_id)
        if node is None:
            print(f"ERROR: node '{node_id}' not found in pipeline JSON", file=sys.stderr)
            return {"ok": False, "rc": 1}
        method_name = method_name or node["method"]
        module_name = module_name or node["module"]
        script_path = script_path or node.get("script", f"methods/{method_name}/{method_name}.py")
        if params is None:
            # Look up variant params from param_sets or node params
            ps = raw.get("param_sets", {})
            node_ps = ps.get(node_id, ps.get(method_name, {}))
            params = node_ps.get(variant, node.get("params", {}))

    # Validate required inline args
    if not method_name or not module_name or not script_path:
        print("ERROR: --method, --module, and --script are required "
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
    pipeline_data: dict | None = None
    if pipeline_json and Path(pipeline_json).exists():
        pipeline_data = json.loads(Path(pipeline_json).read_text())
        current_node = document_node(pipeline_data, node_id) or {}
        nid_label = current_node.get("label") or None
        if "id" in current_node:
            run_node_id = str(current_node["id"])

    def _refuse(message: str) -> dict:
        """Print a refusal and, inside a pipeline, leave its outcome sidecar."""
        print(f"ERROR: {message}", file=sys.stderr)
        if in_pipeline and pipeline_data is not None:
            write_claim_refusal(pipeline_id, node_id, sample, variant, message)
        return {"ok": False, "rc": 1}

    口 = Step(step_num=2, name="Resolve parent runs from sidecars and references",
             purpose="Ask Graph for the links into this node and build one "
                     "input:output:run entry per link; a wired upstream contributes "
                     "its run_id.txt sidecar and must declare the output the link "
                     "names, a run_reference contributes the run id it names. The "
                     "two arrive on separate channels because only one of them is "
                     "a path materialize must resolve")
    # Resolve parent run IDs from sentinel sidecars if not provided
    # Sidecars live next to the sentinel files at
    # .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/run_id.txt.
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

                src_nid = wire.source_id
                # Sentinel outputs are scoped by pipeline_id, so
                # parent sidecar lookups include pipeline_id too. An
                # un-scoped lookup finds nothing or a stale sidecar
                # from a prior session, leaving WFC_INPUT_PATHS empty.
                sidecar = layout.run_id_sidecar_path(
                    sentinel_root, pipeline_id, src_nid, sample, variant
                )
                if sidecar.exists():
                    pid = sidecar.read_text().strip()
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

    try:
        口 = AutoStep(step_num=3, name="Pre-run")
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
        )
    except Exception as exc:
        return _refuse(f"pre_run failed: {exc}")

    # Output-slot declarations from the pipeline JSON (consumed by dispatch's
    # run context and collect's slot scan). slot_outputs (filename per slot)
    # and slot_types (type per slot) come from the pipeline JSON emitted by
    # _enrich_pipeline. A node with no slot_outputs has one output, under the
    # slot name "output", which resolve_node_outputs names at collect time.
    slot_outputs: dict[str, str] = {}
    slot_types: dict[str, str] = {}
    node_cfg: dict = {}
    if pipeline_json and Path(pipeline_json).exists():
        raw = json.loads(Path(pipeline_json).read_text())
        node_cfg = document_node(raw, node_id) or {}
        slot_outputs = node_cfg.get("slot_outputs", {}) or {}
        slot_types = node_cfg.get("slot_types", {}) or {}

    return {
        "ok": True,
        "flag": flag,
        "run_id": run_id,
        "method_name": method_name,
        "module_name": module_name,
        "script_path": script_path,
        "params": params,
        "pipeline_id": pipeline_id,
        "pipeline_json": pipeline_json,
        "pipeline_data": pipeline_data,
        "node_cfg": node_cfg,
        "slot_outputs": slot_outputs,
        "slot_types": slot_types,
        "parent_run_ids": parent_run_ids,
    }
