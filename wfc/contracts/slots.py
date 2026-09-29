"""Slot names, the output-slot type vocabulary and slot filename derivation.

A slot NAME is carried through grammars that use punctuation of their own --
parent entries (``input:output:run``), ``--ref-input label=path``, argv -- so
:func:`validate_slot_name` refuses the characters those grammars claim.  The
rule is a denylist, not an allowlist: a name that has always registered keeps
registering.

An output slot's ``type`` IS the file extension.  The dot is optional in
``method.yaml`` (``.csv`` and ``csv`` are both accepted); the canonical form
is dotted (``.h5ad`` / ``.tar.gz``) and is concatenated directly onto the
slot name.  The directory marker ``dir`` / ``directory`` normalises to the
canonical ``directory``.  There is no hidden semantic-type -> extension
translation and no silent default: a missing or blank value is rejected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping


# Canonical directory marker; ``dir`` is an accepted alias. The one marker
# set: registration-time validation, enrichment and node-config directory
# detection all read it.
_DIRECTORY_TYPE_MARKERS = {"dir", "directory"}
_CANONICAL_DIRECTORY = "directory"

# The characters a slot name may not carry, with the grammar each one would
# break. A denylist: every other character is a legal slot name.
_SLOT_NAME_DENIED = {
    ":": "the parent-entry delimiter (input:output:run)",
    "=": "the --ref-input delimiter (label=path)",
}


def validate_slot_name(slot_name: object, *, kind: str, source: object = None) -> str:
    """Validate a declared input or output slot name.

    A slot name travels through the parent-entry grammar, the ``--ref-input``
    flag and process argv.  A name carrying one of those delimiters round-trips
    as a DIFFERENT wiring rather than failing -- ``a:b`` declared as an input
    slot spells the entry ``a:b:412``, which parses back as input ``a``, output
    ``b``.  The refusal lands at registration, where the name is written down,
    so no run is ever keyed under the misread wiring.

    Args:
        slot_name: The declared slot name from the contract.
        kind: ``"input"`` or ``"output"`` -- names the half in the message.
        source: Optional context (e.g. the ``method.yaml`` path) prepended to
            the error message so a rejected method is easy to locate.

    Returns:
        The slot name, unchanged.

    Raises:
        ValueError: The name is not a string, is empty or blank, or carries a
            denied character or whitespace.
    """
    prefix = f"{source}: " if source else ""

    if not isinstance(slot_name, str) or not slot_name.strip():
        raise ValueError(
            f"{prefix}{kind} slot name {slot_name!r} is empty. Every {kind} "
            f"slot needs a name -- it is how the pipeline wires the slot and "
            f"how the cache key records it."
        )
    for char, grammar in _SLOT_NAME_DENIED.items():
        if char in slot_name:
            raise ValueError(
                f"{prefix}{kind} slot name '{slot_name}' contains '{char}', "
                f"which is {grammar}. Rename the slot without it."
            )
    if any(c.isspace() for c in slot_name):
        raise ValueError(
            f"{prefix}{kind} slot name '{slot_name}' contains whitespace, "
            f"which does not survive the command line. Rename the slot "
            f"without it (an underscore or a hyphen reads the same)."
        )
    return slot_name


def validate_output_slot_type(
    slot_name: str,
    slot_type: object,
    *,
    source: object = None,
) -> str:
    """Validate and normalise an output slot's declared ``type``.

    A valid output ``type`` is a file extension — with or without the
    leading dot (``.csv`` and ``csv`` both normalise to the canonical
    dotted ``.csv``; compound extensions like ``.tar.gz`` / ``tar.gz``
    work by concatenation) — or the directory marker ``dir`` /
    ``directory`` (case-insensitive, normalised to the canonical
    ``directory``).

    Args:
        slot_name: Name of the output slot (used in the error message).
        slot_type: The declared ``type`` value from the contract.
        source: Optional context (e.g. the ``method.yaml`` path) prepended to
            the error message so a rejected method is easy to locate.

    Returns:
        The canonical type string: a dotted extension, or ``"directory"``
        for a directory slot.

    Raises:
        ValueError: If ``slot_type`` is missing, blank, or not a string.
    """
    if isinstance(slot_type, str):
        candidate = slot_type.strip()
        if candidate.lower() in _DIRECTORY_TYPE_MARKERS:
            return _CANONICAL_DIRECTORY
        if candidate.startswith(".") and len(candidate) > 1:
            return candidate
        if candidate and not candidate.startswith("."):
            return f".{candidate}"

    prefix = f"{source}: " if source else ""
    raise ValueError(
        f"{prefix}output slot '{slot_name}' has an unusable type "
        f"{slot_type!r}. An output slot's `type` is the file extension, "
        f"with or without the leading dot (e.g. `type: .csv`, `type: csv`, "
        f"`type: .h5ad`), OR the directory marker `type: dir` / "
        f"`type: directory`. Empty values are rejected — there is no "
        f"type->extension translation and no silent `.csv` default."
    )


def output_slot_filename(slot_name: str, canonical_type: str) -> str:
    """Build an output slot's filename from its canonical (validated) type.

    A directory slot gets no extension (the bare slot name); a file slot gets
    the dotted extension concatenated verbatim.

    Args:
        slot_name: The output slot name.
        canonical_type: The value returned by :func:`validate_output_slot_type`.

    Returns:
        ``slot_name`` for a directory slot, else ``slot_name + canonical_type``.
    """
    if canonical_type == _CANONICAL_DIRECTORY:
        return slot_name
    return f"{slot_name}{canonical_type}"


# =============================================================================
# Node-config slot resolution
# =============================================================================
#
# The read side of the slot vocabulary: a pipeline-JSON node config to the
# workspace paths it will produce, and directory-slot detection.  Both
# ``wfc.orchestration`` (rule generation) and the collect phase depend on
# this so the filenames each side expects cannot drift.  Directory-slot
# detection consults the parallel ``slot_types`` field on the node config;
# filename shape heuristics (trailing slash, absent extension) are
# explicitly rejected.


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
    the node config, which enrichment populates verbatim from the
    registered contract's output slots.  A slot is a directory slot iff
    its type (case-insensitive) is one of the directory markers — the
    canonical ``directory`` enrichment writes, or ``dir`` should either
    spelling reach this helper.

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
