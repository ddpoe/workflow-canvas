"""Document preparation: the one path from a submitted document to a loadable one.

A pipeline document reaches the engine through three doors -- the canvas's
submission, ``wfc run-pipeline`` and the cache-status preview -- and all
three prepare it here, so a document the canvas runs is a document the CLI
runs and the preview predicts:

1. substitute the document's ``{$var}`` references through Graph's
   ``resolve_variables``, so everything after sees literals;
2. enrich it against the registered contract map (script path, slot
   filenames and slot types per method node) through the Contracts unit's
   pure ``enrich_pipeline``, unless it is already enriched.

A document is already enriched when every method node carries its
``slot_outputs`` -- the frozen ``pipeline.json`` the canvas writes is, and
enriching it again would drop nothing but cost a read.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from axiom_annotations import Step, task

from ..contracts import enrich_pipeline
from ..graph import resolve_variables
from ..registration import load_contract_map
from .composer import _composer_session

_SYSTEM_NODE_TYPES = ("input_selector", "run_reference")


def is_enriched(document: Mapping[str, Any]) -> bool:
    """Whether every method node of a document already carries its slot map.

    Args:
        document: A pipeline document.

    Returns:
        True when the document has method nodes and each has
        ``slot_outputs``; False for a sparse (canvas-form) document.
    """
    method_nodes = [
        n for n in document.get("nodes", [])
        if (n.get("type") or "method") not in _SYSTEM_NODE_TYPES
    ]
    return bool(method_nodes) and all("slot_outputs" in n for n in method_nodes)


_ENRICHED_KEYS = ("script", "slot_outputs", "slot_types", "env")
_CANVAS_LINK_KEYS = ("sourceHandle", "targetHandle")


def enrich_document(document: Mapping[str, Any]) -> Dict[str, Any]:
    """Enrich a document against the registered contract map.

    The contract fills what the document does not say: a node's own
    ``script``, ``slot_outputs``, ``slot_types`` or ``env`` wins over the
    contract's, and a node's or link's other fields are kept, so a
    hand-written document that already names them loads as it did.

    Args:
        document: The substituted document, as plain dicts.

    Returns:
        The canonical pipeline document the engine's loader expects.

    Raises:
        DatabaseUnreachableError: The database cannot be reached; the
            composer's own connection probe raises it before the read, so
            preparation reports it exactly as the load does.
        ValueError: A registered method's stored output slot type is unusable.
    """
    with _composer_session() as session:
        contract_map = load_contract_map(session)
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
              "then enrich a sparse document against the registered contract map -- "
              "the one preparation the canvas, run-pipeline and the cache preview share",
      inputs="A pipeline document, sparse (canvas form) or already enriched",
      outputs="The substituted, enriched document the composer loads")
def prepare_document(document: Mapping[str, Any]) -> Dict[str, Any]:
    """Substitute and enrich a pipeline document.

    Args:
        document: The document as submitted.

    Returns:
        The prepared document.

    Raises:
        UnknownVariableError: The document names a variable its
            ``variables`` block does not declare.
        DatabaseUnreachableError: A sparse document needs the contract map
            and the database cannot be reached; nothing was launched.
        ValueError: Its variables fail to resolve, or a stored output slot
            type is unusable.
    """
    口 = Step(step_num=1, name="Substitute variables",
             purpose="Resolve the document's {$var} refs to literals so "
                     "enrichment and the load see only literals")
    substituted = resolve_variables(dict(document))

    口 = Step(step_num=2, name="Enrich a sparse document",
             purpose="Add each method node's script path, slot filenames and "
                     "slot types from the registered contract map; an already "
                     "enriched document passes through")
    if is_enriched(substituted):
        return substituted
    return enrich_document(substituted)
