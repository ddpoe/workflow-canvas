"""Graph — the Tier 0 declared wiring of a pipeline document.

It covers what a pipeline document says and everything computable from it
alone: the load (parse, reference binding, order and legality, collapse),
the order questions, sample x variant expansion over the one variant axis,
structural validation of the sparse canvas document, and ``{$var}``
substitution. Documented by ``docs/system/graph.json``.

The package is pure: the standard library and ``wfc.contracts`` only -- no
database, no Docker, no subprocess, no network, no writes, no file reads.
Its inputs are values; the consumers keep the I/O (the document is read by
the caller, the referenced runs are resolved by Orchestration generation,
and the contract map that validation and the load's single-input rule
read comes from Registration).

Family modules:

- ``model`` — ``StepDef`` and ``PipelineDef``.
- ``load`` — the stage callables and ``load_pipeline`` composing them.
- ``order`` — the topological sort and the leaves.
- ``drafts`` — the draft-mode closure and which draft owns each step.
- ``expansion`` — the variant axis and per-step sample x variant rows.
- ``validation`` — the structural core over the sparse document.
- ``variables`` — ``{$var}`` substitution.
"""

from __future__ import annotations

from .drafts import draft_owners, is_draft_node
from .expansion import (
    VariantModel,
    expand_step_combos,
    mark_reused,
    resolve_variant_model,
    variant_axis,
)
from .keys import NODE_KEYS, TOP_LEVEL_KEYS, document_key_refusals
from .load import (
    InboundLink,
    ParsedDocument,
    bind_references,
    check_legality,
    document_node,
    inbound_wiring,
    load_pipeline,
    parse_document,
    propagate_collapse,
    reference_link_error,
    reference_nodes,
    selector_slot,
)
from .model import PipelineDef, StepDef, carries_sample_bundle, reads_per_sample
from .order import find_leaf_nodes, topo_sort_steps
from .validation import link_slot_errors, unwired_input_errors, validate_structure
from .variables import UnknownVariableError, resolve_variables

__all__ = [
    "link_slot_errors",
    "unwired_input_errors",
    "InboundLink",
    "ParsedDocument",
    "PipelineDef",
    "StepDef",
    "UnknownVariableError",
    "VariantModel",
    "bind_references",
    "carries_sample_bundle",
    "draft_owners",
    "is_draft_node",
    "check_legality",
    "document_node",
    "expand_step_combos",
    "find_leaf_nodes",
    "inbound_wiring",
    "reads_per_sample",
    "reference_link_error",
    "selector_slot",
    "load_pipeline",
    "mark_reused",
    "parse_document",
    "propagate_collapse",
    "reference_nodes",
    "resolve_variables",
    "resolve_variant_model",
    "topo_sort_steps",
    "validate_structure",
    "NODE_KEYS",
    "TOP_LEVEL_KEYS",
    "document_key_refusals",
    "variant_axis",
]
