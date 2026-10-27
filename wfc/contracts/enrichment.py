"""The enrichment pass: a sparse pipeline document becomes the canonical one.

The canvas (and any other composer) sends minimal node data — id, type,
method, module, params and an optional version pin — and the engine loads a
document whose method nodes carry the version they run, per-slot output
filenames, per-slot canonical types and an env. This module is the one place
slot filenames are derived and versions resolved for a pipeline document. It
is plain functions over plain dicts: the contract map is an argument
(Registration reads it from the database; see
``wfc.registration.load_contract_map``), so the pass runs without a server, a
session or an import from ``wfc``.

A method node whose ``<module>.<method>`` the map does not hold is refused by
name, whatever else the node carries: wfc cannot say what it would run.

Once every node's version is known, ``normalize_document_params`` gives each
``int`` and ``float`` parameter value its declared type's form by that
version's contract, so one value has one spelling downstream, and
``fill_document_defaults`` adds each declared default a node or variant
leaves out, so the params a step runs with are written in the document.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from axiom_annotations import Step, task

from .param_check import filled_params, normalized_params
from .slots import output_slot_filename, validate_output_slot_type

#: Node types that carry no method and pass through without enrichment.
_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


class MethodNotFoundError(ValueError):
    """A method node names a method the contract map does not hold.

    Attributes:
        node_id: The node's document id.
        qualified: The ``<module>.<method>`` the node names.
    """

    def __init__(self, node_id: str, qualified: str):
        """Build the refusal naming the node and its ``module.method``.

        Args:
            node_id: The node's document id.
            qualified: The ``<module>.<method>`` the node names.
        """
        self.node_id = node_id
        self.qualified = qualified
        self.module, _, self.method = qualified.partition(".")
        super().__init__(
            f"Node '{node_id}': method '{qualified}' is not registered, so wfc "
            f"cannot say what the node runs. Register it with `wfc "
            f"register-method`, or name a registered module and method."
        )


def version_entry(entry: Mapping[str, Any], pin: str | None) -> Mapping[str, Any] | None:
    """The contract a contract-map entry holds for one version.

    Args:
        entry: One method's contract-map entry (its top level is the newest
            version's contract; ``versions`` holds one per version identity).
        pin: The node's version pin, or ``None``/empty for the newest.

    Returns:
        The pinned version's contract, the entry itself when there is no
        pin, or ``None`` when the pin names no version of the method.
    """
    if not pin:
        return entry
    return (entry.get("versions") or {}).get(pin)


def resolve_node_version(
    node: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, Mapping[str, Any]]:
    """Resolve one method node's version and that version's contract.

    Args:
        node: A method node (``id``, ``module``, ``method``, optional
            ``version``).
        contract_map: Registration's contract map.

    Returns:
        ``(identity, contract)``: the node's pin, or the newest version's
        identity when it has none (``None`` when the entry names no
        version), and that version's contract.

    Raises:
        MethodNotFoundError: The map holds no ``<module>.<method>`` for the
            node.
        ValueError: The node's pin names no version of its method.
    """
    module = node.get("module") or ""
    method = node.get("method") or ""
    qualified = f"{module}.{method}"
    entry = contract_map.get(qualified)
    if entry is None:
        raise MethodNotFoundError(str(node.get("id")), qualified)
    pin = node.get("version") or None
    chosen = version_entry(entry, pin)
    if chosen is None:
        raise ValueError(
            f"Node '{node.get('id')}' ({qualified}): pinned version {pin} is "
            f"not a version of the method"
        )
    return (pin or entry.get("version")), chosen


@task(
    purpose="Write the version each method node runs into a pipeline document: "
            "a pin is kept, an unpinned node gains its method's newest version; "
            "a node whose method the contract map does not hold is refused",
    inputs="A pipeline document (sparse or enriched) and the contract map",
    outputs="The document with a version on every method node",
)
def resolve_versions(
    document: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Pin every method node of a document to the version it runs.

    Nothing else about a node changes: an already enriched document keeps
    its slot maps and env.

    Args:
        document: The pipeline document, as plain dicts.
        contract_map: Registration's contract map.

    Returns:
        A copy of the document whose method nodes each carry ``version``.

    Raises:
        MethodNotFoundError: A method node names a method the map does not
            hold (even when the node carries its own ``env``).
        ValueError: A node's pin names no version of its method.
    """
    nodes: list[Any] = []
    for node in document.get("nodes", []):
        if (node.get("type") or "method") in _SYSTEM_NODE_TYPES:
            nodes.append(node)
            continue
        identity, _contract = resolve_node_version(node, contract_map)
        nodes.append({**node, "version": identity} if identity else dict(node))
    return {**document, "nodes": nodes}


def _node_params_schema(
    node: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """The declared params of the version a method node runs.

    Args:
        node: A method node (``module``, ``method``, optional ``version``).
        contract_map: Registration's contract map.

    Returns:
        The version's non-empty params schema, or ``None`` when the map
        holds no such method or version, or the version declares no params.
    """
    entry = contract_map.get(f"{node.get('module') or ''}.{node.get('method') or ''}")
    contract = version_entry(entry, node.get("version") or None) if entry else None
    schema = (contract or {}).get("params")
    return schema if isinstance(schema, Mapping) and schema else None


def _map_document_params(
    document: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
    per_params: Callable[[Any, Mapping[str, Any]], Any],
    *,
    absent: bool,
) -> dict[str, Any]:
    """Apply one params rule to each method node's params and variants.

    Each method node's ``params``, and each of its variants under the
    document's ``param_sets``, go through ``per_params`` with the declared
    params of the version the node runs. System nodes and nodes whose
    version declares no params pass through.

    Args:
        document: The pipeline document, its versions already resolved.
        contract_map: Registration's contract map.
        per_params: The rule, ``(params, schema) -> params``.
        absent: Whether a node with no ``params`` key goes through the rule
            too (as ``None``); its key is written only when the rule
            returns a non-empty dict.

    Returns:
        A copy of the document; the input is not changed.
    """
    schemas: dict[str, Mapping[str, Any]] = {}
    nodes: list[Any] = []
    for node in document.get("nodes", []):
        if (node.get("type") or "method") in _SYSTEM_NODE_TYPES:
            nodes.append(node)
            continue
        schema = _node_params_schema(node, contract_map)
        if schema is None:
            nodes.append(node)
            continue
        schemas[str(node.get("id"))] = schema
        if "params" in node:
            node = {**node, "params": per_params(node["params"], schema)}
        elif absent:
            params = per_params(None, schema)
            if params:
                node = {**node, "params": params}
        nodes.append(node)
    mapped = {**document, "nodes": nodes}
    param_sets = document.get("param_sets")
    if isinstance(param_sets, Mapping) and schemas:
        mapped["param_sets"] = {
            node_id: ({variant: per_params(values, schemas[node_id])
                       for variant, values in variants.items()}
                      if node_id in schemas and isinstance(variants, Mapping)
                      else variants)
            for node_id, variants in param_sets.items()
        }
    return mapped


@task(
    purpose="Give every method node's params their declared type's form "
            "(an int param's 1.0 becomes 1, a float param's 1 becomes 1.0), "
            "by the contract of the version each node runs, so one value has "
            "one spelling in the frozen document, the cache key and the script",
    inputs="A pipeline document whose method nodes carry their version, and "
           "the contract map",
    outputs="A new document with normalized node params and param_sets "
            "variants",
    critical="Pure and idempotent: a node whose version has no contract, a "
             "param its contract does not declare, a value the rule refuses "
             "and values nested in list/dict params are left as they are",
)
def normalize_document_params(
    document: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Normalize a resolved document's ``int`` and ``float`` parameter values.

    Each method node's ``params``, and each of its variants under the
    document's ``param_sets``, is normalized by the declared params of the
    version the node runs (its ``version``, else the newest), through
    ``normalize_param_value``.

    Args:
        document: The pipeline document, its versions already resolved.
        contract_map: Registration's contract map; each version's contract
            carries its declared ``params``.

    Returns:
        A copy of the document with normalized params; the input is not
        changed.
    """
    normalized = _map_document_params(document, contract_map,
                                      normalized_params, absent=False)
    return normalized


@task(
    purpose="Fill each method node's params, and each of its param_sets "
            "variants, with the declared defaults of the version the node "
            "runs for every parameter it leaves out, so a step that leaves a "
            "parameter out and one that writes its default are the same work",
    inputs="A pipeline document whose method nodes carry their version, and "
           "the contract map",
    outputs="A new document whose node params and variants carry every "
            "usable declared default",
    critical="Pure and idempotent: a key the document writes is kept "
             "whatever its value (null included); a declared null default is "
             "not filled; each variant is filled on its own; system nodes and "
             "nodes whose version declares no params are left as they are",
)
def fill_document_defaults(
    document: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Fill a resolved document's params with their versions' declared defaults.

    Each method node's ``params`` (a node with no ``params`` key gains one
    when its version declares a usable default), and each of its variants
    under the document's ``param_sets``, is filled through
    ``fill_declared_defaults`` by the declared params of the version the node
    runs (its ``version``, else the newest). A variant's table replaces the
    node's params when it runs, so it is filled on its own.

    Args:
        document: The pipeline document, its versions already resolved.
        contract_map: Registration's contract map; each version's contract
            carries its declared ``params``.

    Returns:
        A copy of the document with filled params; the input is not changed.
    """
    filled = _map_document_params(document, contract_map, filled_params,
                                  absent=True)
    return filled


@task(
    purpose="Enrich a sparse pipeline document into the canonical document the "
            "engine loads: resolve each method node's version, validate that "
            "version's declared output slot types, derive its slot filenames, "
            "and attach its env from the contract map",
    inputs="A sparse document (plain dicts: nodes with id/type/method/module/"
           "params/optional version and the system-node fields, links with "
           "source/target and optional handles, samples, and optional name/"
           "param_sets/explicit_combos) and a contract map keyed "
           "'<module>.<method>' -> {output_slots, env, version, versions}",
    outputs="The canonical document dict: nodes, links, samples, plus name/"
            "param_sets/explicit_combos when the sparse document carried them",
)
def enrich_pipeline(
    sparse_doc: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Enrich a sparse pipeline document against a contract map.

    A method node is identified by its module and method together
    (``"<module>.<method>"``); a node whose key is absent from the map is
    refused naming the node and its ``module.method``. Each method node
    runs a version: its pin, or the newest version when it has none, and
    the node is written with that version's identity, slot maps and env.
    No node carries a script path: the executed script is resolved from
    the version's committed tree at run time. System nodes
    (``input_selector``, ``run_reference``) pass through with their own
    fields and no enrichment.

    Args:
        sparse_doc: The document as the canvas sends it, as plain dicts.
        contract_map: ``{"<module>.<method>": {"output_slots": {slot: spec},
            "env": str | None, "version": <newest identity>, "versions":
            {<identity>: {"output_slots", "env", ...}}}}`` for every
            registered method.

    Returns:
        The canonical pipeline document the engine's loader expects.

    Raises:
        MethodNotFoundError: A method node names a method the map does not
            hold.
        ValueError: A node's pin names no version of its method, or a
            mapped version's declared output slot type is unusable (a stored
            contract that predates registration-time validation still fails
            loud here rather than misnaming its file).
    """
    nodes: list[dict[str, Any]] = []

    口 = Step(step_num=1, name="Walk the document's nodes",
             purpose="Pass system nodes through with their own fields; enrich "
                     "each method node from its version's module-qualified "
                     "contract")
    for node in sparse_doc.get("nodes", []):
        node_type = node.get("type") or "method"

        if node_type in _SYSTEM_NODE_TYPES:
            node_dict: dict[str, Any] = {
                "id": node["id"],
                "type": node_type,
                "method": "",
                "module": "",
                "params": node.get("params", {}),
            }
            if node_type == "input_selector":
                node_dict["samples"] = node.get("samples") or []
                node_dict["source"] = node.get("source") or "registered"
                # Preserve fan_mode through to the engine; default "out"
                # keeps per-sample semantics intact for legacy pipelines.
                node_dict["fan_mode"] = node.get("fan_mode") or "out"
            elif node_type == "run_reference":
                # A reference is the run it names; which output feeds each
                # consumer is the link's own source slot.
                node_dict["run_id"] = node.get("run_id")
            if node.get("label"):
                node_dict["label"] = node["label"]
            nodes.append(node_dict)
            continue

        method = node.get("method", "")
        module = node.get("module") or ""

        口 = Step(step_num=1.1, name="Resolve the node's version",
                 purpose="The pin, or the newest version when the node has "
                         "none; a module.method the map does not hold is "
                         "refused naming the node, whatever env it carries",
                 critical="No silent fallback: an unenriched node would fail "
                          "much later as a missing env or slot")
        version, info = resolve_node_version(node, contract_map)

        口 = Step(step_num=1.2, name="Validate the declared output slot types",
                 purpose="Each declared slot type is the file extension or the "
                         "directory marker, normalised to its canonical form; "
                         "an unusable stored type fails loud here")
        canonical_types: dict[str, str] = {}
        for slot_name, slot_spec in (info.get("output_slots") or {}).items():
            raw_type = slot_spec.get("type") if isinstance(slot_spec, dict) else slot_spec
            canonical_types[slot_name] = validate_output_slot_type(slot_name, raw_type)

        口 = Step(step_num=1.3, name="Derive the slot filenames",
                 purpose="One filename per slot from its canonical type: the "
                         "bare slot name for a directory, the extension "
                         "concatenated verbatim for a file")
        slot_outputs = {
            slot_name: output_slot_filename(slot_name, canonical_type)
            for slot_name, canonical_type in canonical_types.items()
        }

        口 = Step(step_num=1.4, name="Attach the version and its env",
                 purpose="The version's identity and env join the node, with the "
                         "parallel slot_types map generation and dispatch "
                         "consult for directory slots. No script path is "
                         "written: what runs is resolved from the version's "
                         "committed tree")
        node_dict = {
            "id": node["id"],
            "method": method,
            "module": module,
            "params": node.get("params", {}),
            "slot_outputs": slot_outputs,
            "slot_types": canonical_types,
            "env": info.get("env"),
        }
        if version:
            node_dict["version"] = version
        if node.get("label"):
            node_dict["label"] = node["label"]
        nodes.append(node_dict)

    口 = Step(step_num=2, name="Carry the links and the document-level fields",
             purpose="Links keep their optional slot handles under the engine's "
                     "names; name, param_sets and explicit_combos pass through "
                     "only when the sparse document carried them")
    links: list[dict[str, str]] = []
    for link in sparse_doc.get("links", []):
        entry: dict[str, str] = {"source": link["source"], "target": link["target"]}
        if link.get("sourceHandle"):
            entry["source_slot"] = link["sourceHandle"]
        if link.get("targetHandle"):
            entry["target_slot"] = link["targetHandle"]
        links.append(entry)

    result: dict[str, Any] = {
        "nodes": nodes,
        "links": links,
        "samples": sparse_doc.get("samples", []),
    }
    # Keep the user-given pipeline name on the persisted record — the
    # history provider reads it back for the Pipelines-view card title.
    # load_pipeline() ignores unknown top-level keys.
    if sparse_doc.get("name"):
        result["name"] = sparse_doc["name"]
    # Pass through parameter-sweep fields when the composer has authored any.
    # Both map 1:1 onto the engine's pipeline JSON schema — no transformation.
    if sparse_doc.get("param_sets"):
        result["param_sets"] = sparse_doc["param_sets"]
    if sparse_doc.get("explicit_combos"):
        result["explicit_combos"] = sparse_doc["explicit_combos"]
    return result
