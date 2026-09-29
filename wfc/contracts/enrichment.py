"""The enrichment pass: a sparse pipeline document becomes the canonical one.

The canvas (and any other composer) sends minimal node data — id, type,
method, module, params — and the engine loads a document whose method nodes
carry a script path, per-slot output filenames, per-slot canonical types and
an env. This module is the one place slot filenames are derived for a
pipeline document. It is a plain function over plain dicts: the contract
map is an argument (Registration reads it from the database; see
``wfc.registration.load_contract_map``), so the pass runs without a server, a
session or an import from ``wfc``.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from axiom_annotations import Step, task

from .slots import output_slot_filename, validate_output_slot_type

#: Node types that carry no method and pass through without enrichment.
_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


@task(
    purpose="Enrich a sparse pipeline document into the canonical document the "
            "engine loads: validate each method node's declared output slot "
            "types, derive its slot filenames, and attach its script path and "
            "env from the contract map",
    inputs="A sparse document (plain dicts: nodes with id/type/method/module/"
           "params and the system-node fields, links with source/target and "
           "optional handles, samples, and optional name/param_sets/"
           "explicit_combos) and a contract map keyed '<module>.<method>' -> "
           "{output_slots, script_path, env}",
    outputs="The canonical document dict: nodes, links, samples, plus name/"
            "param_sets/explicit_combos when the sparse document carried them",
)
def enrich_pipeline(
    sparse_doc: Mapping[str, Any],
    contract_map: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Enrich a sparse pipeline document against a contract map.

    A method node is identified by its module and method together
    (``"<module>.<method>"``); a node whose key is absent from the map is
    emitted with an empty slot map, the fallback script path
    ``methods/<method>/<method>.py`` and no env. System nodes
    (``input_selector``, ``run_reference``) pass through with their own
    fields and no enrichment.

    Args:
        sparse_doc: The document as the canvas sends it, as plain dicts.
        contract_map: ``{"<module>.<method>": {"output_slots": {slot: spec},
            "script_path": str | None, "env": str | None}}`` for every
            registered method.

    Returns:
        The canonical pipeline document the engine's loader expects.

    Raises:
        ValueError: If a mapped method's declared output slot type is
            unusable (a stored contract that predates registration-time
            validation still fails loud here rather than misnaming its file).
    """
    nodes: list[Dict[str, Any]] = []

    口 = Step(step_num=1, name="Walk the document's nodes",
             purpose="Pass system nodes through with their own fields; enrich "
                     "each method node from its module-qualified contract")
    for node in sparse_doc.get("nodes", []):
        node_type = node.get("type") or "method"

        if node_type in _SYSTEM_NODE_TYPES:
            node_dict: Dict[str, Any] = {
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
        info = contract_map.get(f"{module}.{method}", {})

        口 = Step(step_num=1.1, name="Validate the declared output slot types",
                 purpose="Each declared slot type is the file extension or the "
                         "directory marker, normalised to its canonical form; "
                         "an unusable stored type fails loud here")
        canonical_types: Dict[str, str] = {}
        for slot_name, slot_spec in info.get("output_slots", {}).items():
            raw_type = slot_spec.get("type") if isinstance(slot_spec, dict) else slot_spec
            canonical_types[slot_name] = validate_output_slot_type(slot_name, raw_type)

        口 = Step(step_num=1.2, name="Derive the slot filenames",
                 purpose="One filename per slot from its canonical type: the "
                         "bare slot name for a directory, the extension "
                         "concatenated verbatim for a file")
        slot_outputs = {
            slot_name: output_slot_filename(slot_name, canonical_type)
            for slot_name, canonical_type in canonical_types.items()
        }

        口 = Step(step_num=1.3, name="Attach the script path and env",
                 purpose="The registered script path (falling back to "
                         "methods/<method>/<method>.py) and the registered env "
                         "join the node, with the parallel slot_types map "
                         "generation and dispatch consult for directory slots")
        script_path = info.get("script_path", f"methods/{method}/{method}.py")
        node_dict = {
            "id": node["id"],
            "method": method,
            "module": module,
            "script": script_path or f"methods/{method}/{method}.py",
            "params": node.get("params", {}),
            "slot_outputs": slot_outputs,
            "slot_types": canonical_types,
            "env": info.get("env"),
        }
        if node.get("label"):
            node_dict["label"] = node["label"]
        nodes.append(node_dict)

    口 = Step(step_num=2, name="Carry the links and the document-level fields",
             purpose="Links keep their optional slot handles under the engine's "
                     "names; name, param_sets and explicit_combos pass through "
                     "only when the sparse document carried them")
    links: list[Dict[str, str]] = []
    for link in sparse_doc.get("links", []):
        entry: Dict[str, str] = {"source": link["source"], "target": link["target"]}
        if link.get("sourceHandle"):
            entry["source_slot"] = link["sourceHandle"]
        if link.get("targetHandle"):
            entry["target_slot"] = link["targetHandle"]
        links.append(entry)

    result: Dict[str, Any] = {
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
