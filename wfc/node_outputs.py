"""
Shared helper for resolving a pipeline-JSON node config to workspace output
paths (ADR-010).

This module owns the single source of truth for the mapping
``node_cfg -> {slot_name: workspace_path}``.  Both ``wfc.snakemake_gen``
(rule generation) and ``wfc.cli.run_step`` (execution) depend on it so that
the filenames each side expects cannot drift.

Design contract:
    - Pure module.  No DB lookups, no filesystem side effects, no imports
      from ``wfc.snakemake_gen`` or ``wfc.cli``.
    - Directory-slot detection consults the parallel ``slot_types`` field
      on the node config.  Filename shape heuristics (trailing slash,
      absent extension) are explicitly rejected.
    - Legacy fallback: when ``slot_outputs`` is empty or missing, the
      helper returns a single ``{"output": ws_base / "output{ext}"}``
      mapping so legacy single-output pipelines keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping


_DIRECTORY_TYPE_MARKERS = {"directory"}


def resolve_node_outputs(
    node_cfg: Mapping[str, object],
    ws_base: Path,
) -> Dict[str, Path]:
    """Resolve a node's declared output slots to workspace paths.

    For each entry in the node's ``slot_outputs``, build an absolute
    workspace path by joining ``ws_base`` with the slot's filename.  The
    filename is taken verbatim from ``slot_outputs`` (including its
    extension or lack thereof).

    When ``slot_outputs`` is empty or missing, falls back to a single
    generic ``output{output_ext}`` entry under the slot name ``"output"``
    so that legacy single-output pipelines (no contract, no slots) keep
    working.  ``output_ext`` defaults to ``.parquet``.

    Args:
        node_cfg: A dict-like node configuration from the pipeline JSON.
            Recognized keys: ``slot_outputs`` (dict[str, str]),
            ``output_ext`` (str, legacy fallback only).
        ws_base: The workspace base path for this node (already resolved
            to include pipeline_id / node_id / sample / variant).

    Returns:
        An ordered mapping ``{slot_name: workspace_path}``.  Insertion
        order mirrors the order of ``slot_outputs``.  For the legacy
        fallback, the mapping has exactly one entry with key ``"output"``.
    """
    slot_outputs = node_cfg.get("slot_outputs") or {}
    if not slot_outputs:
        output_ext = node_cfg.get("output_ext", ".parquet") or ".parquet"
        return {"output": ws_base / f"output{output_ext}"}

    result: Dict[str, Path] = {}
    for slot_name, filename in slot_outputs.items():
        result[slot_name] = ws_base / filename
    return result


def is_directory_slot(
    node_cfg: Mapping[str, object],
    slot_name: str,
) -> bool:
    """Answer whether the given slot on the node is a directory slot.

    The single source of truth is the parallel ``slot_types`` field on
    the node config, which ``_enrich_pipeline`` populates verbatim from
    ``MethodContract.output_slots``.  A slot is a directory slot iff its
    type (case-insensitive) is in ``_DIRECTORY_TYPE_MARKERS``.

    When ``slot_types`` is absent or the slot is not listed, the slot
    is treated as a file slot (strict additivity — legacy pipeline JSON
    without ``slot_types`` keeps working unchanged).

    Args:
        node_cfg: A dict-like node configuration from the pipeline JSON.
        slot_name: The name of the slot to check.

    Returns:
        True if the slot is declared as a directory slot, False
        otherwise.
    """
    slot_types = node_cfg.get("slot_types") or {}
    slot_type = slot_types.get(slot_name)
    if not isinstance(slot_type, str):
        return False
    return slot_type.lower() in _DIRECTORY_TYPE_MARKERS
