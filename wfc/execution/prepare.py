"""Document preparation: the one path from a submitted document to a loadable one.

A pipeline document reaches the engine through three doors -- the canvas's
submission, ``wfc run-pipeline`` and the cache-status preview -- and all
three prepare it here, so a document the canvas runs is a document the CLI
runs and the preview predicts:

1. substitute the document's ``{$var}`` references through Graph's
   ``resolve_variables``, so everything after sees literals;
2. validate it through ``validation.refuse_invalid`` (unknown keys, each
   method node's params and each of its ``param_sets`` variants against
   its version's contract), refusing it before anything else reads it;
3. enrich it against the registered contract map (each method node's
   version, slot filenames, slot types and env) through the Contracts
   unit's pure ``enrich_pipeline``; an already enriched document keeps its
   slots and only has its versions resolved (``resolve_versions``);
4. normalize every method node's ``int`` and ``float`` params, and those
   of each of its ``param_sets`` variants, by its version's contract (Contracts' ``normalize_document_params``), so
   ``1.0`` and ``1`` for an ``int`` param, or ``1`` and ``1.0`` for a
   ``float`` one, are one value under one cache key;
5. fill every parameter a method node, or one of its ``param_sets``
   variants, leaves out with the declared default of the version the node
   runs (Contracts' ``fill_document_defaults``), so a step that leaves a
   parameter out and one that writes its default are one params dict and
   one cache key, and the run records every value it ran with. A value the
   document writes, ``null`` included, is kept.

Validation sees the document as written. The fill adds only usable
defaults (non-null, and not ``""`` for a required parameter); a default
that breaks its own type or constraints is refused at registration.

Every prepared document carries a ``version`` on every method node: a pin
is kept, an unpinned node gains its method's newest version. The frozen
``pipeline.json`` both paths write is the prepared document, so it records
what each node ran, and a node whose method is not registered is refused by
name. A document is already enriched when every method node carries its
``slot_outputs`` -- the frozen ``pipeline.json`` the canvas writes is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from axiom_annotations import AutoStep, Step, task

from ..contracts import (
    MethodNotFoundError,
    enrich_pipeline,
    fill_document_defaults,
    normalize_document_params,
    resolve_versions,
)
from ..graph import resolve_variables
from ..registration import (
    MalformedMethodRecordError,
    load_contract_map,
    version_contracts,
)
from .composer import _composer_session
from .validation import refuse_invalid

_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


def _method_nodes(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A document's method nodes, in document order."""
    return [
        n for n in document.get("nodes", [])
        if (n.get("type") or "method") not in _SYSTEM_NODE_TYPES
    ]


def is_enriched(document: Mapping[str, Any]) -> bool:
    """Whether every method node of a document already carries its slot map.

    Args:
        document: A pipeline document.

    Returns:
        True when the document has method nodes and each has
        ``slot_outputs``; False for a sparse (canvas-form) document.
    """
    method_nodes = _method_nodes(document)
    return bool(method_nodes) and all("slot_outputs" in n for n in method_nodes)


_ENRICHED_KEYS = ("slot_outputs", "slot_types", "env")
_CANVAS_LINK_KEYS = ("sourceHandle", "targetHandle")


def _read_contract_map() -> dict:
    """The registered contract map, read in one composer session.

    Raises:
        DatabaseUnreachableError: The database cannot be reached.
    """
    with _composer_session() as session:
        return load_contract_map(session)


def enrich_document(document: Mapping[str, Any],
                    contract_map: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Enrich a document against the registered contract map.

    The contract fills what the document does not say: a node's own
    ``slot_outputs``, ``slot_types`` or ``env`` wins over the
    contract's, and a node's or link's other fields are kept, so a
    hand-written document that already names them loads as it did. Every
    method node gains the version it runs (its pin, else the newest).

    Args:
        document: The substituted document, as plain dicts.
        contract_map: The registered contract map; read from the database
            when omitted.

    Returns:
        The canonical pipeline document the engine's loader expects.

    Raises:
        DatabaseUnreachableError: The database cannot be reached; the
            composer's own connection probe raises it before the read, so
            preparation reports it exactly as the load does.
        MethodNotFoundError: A method node names a method that is not
            registered (a ``ValueError``).
        ValueError: A registered method's stored output slot type is unusable.
    """
    if contract_map is None:
        contract_map = _read_contract_map()
    enriched = enrich_pipeline(document, contract_map)
    nodes = []
    for original, filled in zip(document.get("nodes", []), enriched["nodes"]):
        own = {k: original[k] for k in _ENRICHED_KEYS
               if original.get(k) is not None}
        nodes.append({**original, **filled, **own})
    links = []
    for original, filled in zip(document.get("links", []), enriched["links"]):
        kept = {k: v for k, v in original.items() if k not in _CANVAS_LINK_KEYS}
        links.append({**kept, **filled})
    return {**enriched, "nodes": nodes, "links": links}


@task(purpose="Prepare a pipeline document for the load: substitute its variables, "
              "validate it, then enrich a sparse document against the registered "
              "contract map, resolve every method node's version, normalize "
              "its int and float params and fill every declared default it "
              "leaves out -- the one preparation the canvas, run-pipeline and "
              "the cache preview share",
      inputs="A pipeline document, sparse (canvas form) or already enriched",
      outputs="The substituted, enriched document the composer loads, with a "
              "version on every method node and params that are normalized "
              "and carry every declared default",
      critical="Validation sees the document as written; the fill runs after "
               "it and keeps every value the document writes, null included; "
               "preparing a prepared document changes nothing")
def prepare_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Substitute, validate, enrich, pin, normalize and fill a pipeline document.

    Step 5 gives every ``int`` and ``float`` parameter value its declared
    type's form by the contract of the version its node runs (an ``int``
    param's ``1.0`` becomes ``1``, a ``float`` param's ``1`` becomes
    ``1.0``), on both document forms. Step 6 then adds, for every parameter
    a method node or one of its ``param_sets`` variants leaves out, the
    default its version declares. So the frozen ``pipeline.json``, the cache
    key, the run row and the script see one params dict: one spelling of
    each value, and every value the step runs with.

    Args:
        document: The document as submitted.

    Returns:
        The prepared document: every method node carries ``version`` and
        its params in their declared types' form, with every declared
        default its version has filled in.

    Raises:
        UnknownVariableError: The document names a variable its
            ``variables`` block does not declare.
        DatabaseUnreachableError: The contract map is needed and the
            database cannot be reached; nothing was launched.
        PipelineRefusedError: The validator refuses the substituted
            document (an unknown key, or a parameter its version's contract
            does not accept); carries every refusal.
        MethodNotFoundError: A method node names a method that is not
            registered, whatever ``env`` it carries (a ``ValueError``).
        ValueError: Its variables fail to resolve, or a stored output slot
            type is unusable.
    """
    口 = Step(step_num=1, name="Substitute variables",
             purpose="Resolve the document's {$var} refs to literals so "
                     "enrichment and the load see only literals")
    substituted = resolve_variables(dict(document))

    口 = AutoStep(step_num=2, name="Validate")
    refuse_invalid(substituted)

    口 = Step(step_num=3, name="Read the contract map",
             purpose="One read of every registered method version's contract, "
                     "for the enrichment and the version resolution below")
    contract_map = _read_contract_map()

    口 = Step(step_num=4, name="Enrich a sparse document, or resolve an enriched one's versions",
             purpose="A sparse document gains each method node's version, slot "
                     "filenames, slot types and env; an already enriched one "
                     "keeps its slots and gains only the versions its nodes "
                     "leave unpinned",
             critical="Every returned method node carries a version, so the "
                      "frozen pipeline.json records what each node ran; a node "
                      "whose module.method is not registered is refused")
    try:
        if is_enriched(substituted):
            resolved = resolve_versions(substituted, contract_map)
        else:
            resolved = enrich_document(substituted, contract_map)
    except MethodNotFoundError as missing:
        _raise_malformed_record(missing)
        raise

    口 = AutoStep(step_num=5, name="Normalize int and float params")
    normalized = normalize_document_params(resolved, contract_map)

    口 = AutoStep(step_num=6, name="Fill declared defaults")
    prepared = fill_document_defaults(normalized, contract_map)
    return prepared


def _raise_malformed_record(missing: MethodNotFoundError) -> None:
    """Re-raise a miss as its method's malformed-record refusal, when it is one.

    The contract map leaves out a registered method whose version list is
    missing or malformed; its node is then refused with Registration's own
    message (naming ``wfc register-method``), not called unregistered.

    Raises:
        MalformedMethodRecordError: The method is registered but its record
            names no version.
    """
    if not (missing.module and missing.method):
        return
    from sqlmodel import select

    from ..persistence import Method, Module

    with _composer_session() as session:
        row = session.exec(select(Method).join(Module).where(
            Module.name == missing.module, Method.name == missing.method)).first()
    if row is None:
        return  # not registered at all: the miss's own refusal stands
    try:
        version_contracts(missing.module, missing.method)
    except MalformedMethodRecordError:
        raise
    except Exception:  # an unregistered method: the miss's own refusal stands
        return


class ResolvedVersion(NamedTuple):
    """The version run-pipeline resolved for a node the document left unpinned.

    Attributes:
        node_id: The node's document id.
        module: The node's module.
        method: The node's method.
        label: The resolved version's label (``2`` for v2).
    """

    node_id: str
    module: str
    method: str
    label: int


def unpinned_resolutions(submitted: Mapping[str, Any],
                         prepared: Mapping[str, Any]) -> list[ResolvedVersion]:
    """The versions the preparation chose for the nodes a document left unpinned.

    Args:
        submitted: The document as submitted (before preparation).
        prepared: What ``prepare_document`` returned for it.

    Returns:
        One entry per method node of ``submitted`` with no ``version``, in
        document order, naming the label of the version it resolved. Empty
        (and no read) when every method node is pinned, as the canvas's
        frozen copy is.

    Raises:
        DatabaseUnreachableError: The database cannot be reached.
    """
    unpinned = [str(n.get("id")) for n in _method_nodes(submitted)
                if not n.get("version")]
    if not unpinned:
        return []
    by_id = {str(n.get("id")): n for n in _method_nodes(prepared)}
    contract_map = _read_contract_map()
    out: list[ResolvedVersion] = []
    for node_id in unpinned:
        node = by_id.get(node_id)
        if node is None:
            continue
        module, method = node.get("module") or "", node.get("method") or ""
        entry = contract_map.get(f"{module}.{method}") or {}
        version = (entry.get("versions") or {}).get(node.get("version")) or {}
        if "label" in version:
            out.append(ResolvedVersion(node_id, module, method, version["label"]))
    return out


class FilledDefaults(NamedTuple):
    """The declared defaults one node, or one of its variants, runs.

    Attributes:
        node_id: The node's document id.
        module: The node's module.
        method: The node's method.
        variant: The ``param_sets`` variant's name, or ``None`` for the
            node's own params.
        values: Each parameter the submitted document left out and
            preparation filled, with its value, in the prepared order.
    """

    node_id: str
    module: str
    method: str
    variant: str | None
    values: dict[str, Any]


def _param_sets(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """A document's ``param_sets`` block, or an empty one."""
    param_sets = document.get("param_sets")
    return param_sets if isinstance(param_sets, Mapping) else {}


def _left_out(written: Any, prepared: Any) -> dict[str, Any]:
    """The keys of ``prepared`` that ``written`` lacks, with their values."""
    if not isinstance(prepared, Mapping):
        return {}
    written = written if isinstance(written, Mapping) else {}
    return {name: value for name, value in prepared.items() if name not in written}


def filled_defaults(submitted: Mapping[str, Any],
                    prepared: Mapping[str, Any]) -> list[FilledDefaults]:
    """The declared defaults preparation filled into a document, where they run.

    Compares the submitted document with what ``prepare_document`` returned,
    by key (variable substitution adds none): for each method node, the
    parameters present in the prepared params and absent from the submitted
    ones. Reads no contract.

    A node's own params run unless its ``param_sets`` table names every
    variant the pipeline runs (the engine runs a node's own params under a
    variant name its table lacks, and under ``default`` when it has no
    table), so a node whose own params never run gets no entry for them.
    Each of its variants that runs a default gets its own entry.

    Args:
        submitted: The document as submitted (before preparation).
        prepared: What ``prepare_document`` returned for it.

    Returns:
        One entry per node, and per variant, that runs at least one filled
        default, in document order (a node's own entry before its
        variants'). Empty for a document that was already filled, as the
        canvas's frozen copy is.
    """
    written_nodes = {str(n.get("id")): n for n in _method_nodes(submitted)}
    written_sets = _param_sets(submitted)
    prepared_sets = _param_sets(prepared)
    nodes = _method_nodes(prepared)
    axis: set[str] = set()
    for node in nodes:
        table = prepared_sets.get(str(node.get("id")))
        axis.update(table if isinstance(table, Mapping) else ("default",))
    axis = axis or {"default"}
    out: list[FilledDefaults] = []
    for node in nodes:
        node_id = str(node.get("id"))
        module, method = node.get("module") or "", node.get("method") or ""
        table = prepared_sets.get(node_id)
        if not isinstance(table, Mapping) or not axis <= set(table):
            values = _left_out((written_nodes.get(node_id) or {}).get("params"),
                               node.get("params"))
            if values:
                out.append(FilledDefaults(node_id, module, method, None, values))
        if not isinstance(table, Mapping):
            continue
        written_table = written_sets.get(node_id)
        written_table = written_table if isinstance(written_table, Mapping) else {}
        for variant, params in table.items():
            values = _left_out(written_table.get(variant), params)
            if values:
                out.append(FilledDefaults(node_id, module, method, str(variant), values))
    return out
