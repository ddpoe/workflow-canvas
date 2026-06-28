"""
Method contract parser for ``method.yaml`` files.

``parse_method_yaml(method_dir)`` reads the YAML contract from a method
directory and returns a normalised dict with ``inputs``, ``outputs``,
``params``, and ``executor`` keys.  Returns ``None`` if no ``method.yaml``
is present (methods without explicit contracts are still valid -- they just
won't have slot-level metadata in the DB).

ADR-005 extensions:
  - ``columns`` key on input/output CSV/Parquet slots (strict, from_params, patterns)
  - ``contents`` key on directory output slots (glob patterns + min_count + columns)
  - Column resolution engine: ``resolve_columns()``, ``read_file_columns()``
  - Input validation: ``validate_input_columns()`` (hard gate)
  - Output validation: ``validate_output_columns()`` (soft warning)
  - Directory validation: ``validate_directory_contents()`` (soft warning)
  - Static cross-step check: ``cross_check_columns()``
"""

from __future__ import annotations

import fnmatch
import itertools
import logging
from pathlib import Path
from typing import Optional

from axiom_annotations import task

logger = logging.getLogger(__name__)


# =============================================================================
# Output-slot type vocabulary
# =============================================================================
#
# An output slot's ``type`` IS the file extension, declared verbatim (dotted,
# e.g. ``.h5ad`` / ``.tar.gz``) and concatenated directly onto the slot name,
# OR the directory marker ``dir`` / ``directory`` (normalised to the canonical
# ``directory``).  There is no hidden semantic-type -> extension translation
# and no silent default: a missing, blank, bare-undotted, or stale-semantic
# value is rejected.

# Canonical directory marker; ``dir`` is an accepted alias.
_DIRECTORY_TYPE_MARKERS = {"dir", "directory"}
_CANONICAL_DIRECTORY = "directory"


def validate_output_slot_type(
    slot_name: str,
    slot_type: object,
    *,
    source: object = None,
) -> str:
    """Validate and normalise an output slot's declared ``type``.

    A valid output ``type`` is either a leading-dot file extension (taken
    verbatim, so compound extensions like ``.tar.gz`` work by concatenation)
    or the directory marker ``dir`` / ``directory`` (case-insensitive,
    normalised to the canonical ``directory``).

    Args:
        slot_name: Name of the output slot (used in the error message).
        slot_type: The declared ``type`` value from the contract.
        source: Optional context (e.g. the ``method.yaml`` path) prepended to
            the error message so a rejected method is easy to locate.

    Returns:
        The canonical type string: a dotted extension verbatim, or
        ``"directory"`` for a directory slot.

    Raises:
        ValueError: If ``slot_type`` is missing, blank, a bare un-dotted name,
            or a stale semantic name.
    """
    if isinstance(slot_type, str):
        candidate = slot_type.strip()
        if candidate.lower() in _DIRECTORY_TYPE_MARKERS:
            return _CANONICAL_DIRECTORY
        if candidate.startswith(".") and len(candidate) > 1:
            return candidate

    prefix = f"{source}: " if source else ""
    raise ValueError(
        f"{prefix}output slot '{slot_name}' has an unusable type "
        f"{slot_type!r}. An output slot's `type` is the file extension, "
        f"declared verbatim with a leading dot (e.g. `type: .csv`, "
        f"`type: .h5ad`, `type: .parquet`), OR the directory marker "
        f"`type: dir` / `type: directory`. Bare names without a dot "
        f"(`csv`), stale semantic names (`anndata`), and empty values are "
        f"rejected — there is no type->extension translation and no silent "
        f"`.csv` default."
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
# YAML parser
# =============================================================================

@task(
    purpose="Parse method.yaml contract file into a normalised slot definition dict",
    inputs="Path to a method directory (may or may not contain method.yaml)",
    outputs="Normalised contract dict with inputs/outputs/params/executor, or None",
)
def parse_method_yaml(method_dir: Path) -> Optional[dict]:
    """Parse ``method.yaml`` from a method directory.

    If the file does not exist, returns ``None`` so callers can skip
    contract registration without failing.

    The returned dict always has all four top-level keys; missing sections
    default to empty dicts/the string ``"python"``.  The ``columns`` key on
    input/output slots and ``contents`` key on directory output slots are
    preserved as-is from the YAML.

    Args:
        method_dir: Directory that may contain ``method.yaml``.

    Returns:
        Parsed contract dict, or ``None`` if ``method.yaml`` is absent.

    Raises:
        ValueError: If the YAML is malformed or missing required top-level keys.
    """
    import yaml  # PyYAML -- available in all envs via pixi/conda/poetry

    yaml_path = Path(method_dir) / "method.yaml"
    if not yaml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {yaml_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{yaml_path} must be a YAML mapping at the top level")

    # ADR-019 Cycle H: execution is container-only. Every method must name a
    # built container env -- there is no host-Python fallback and no
    # ``inherit`` default. Reject a missing ``env`` and the removed
    # ``env: inherit`` keyword at parse time so a non-runnable method is
    # caught at registration rather than failing mid-pipeline.
    env_value = raw.get("env")
    if not env_value or (isinstance(env_value, str) and env_value.strip() == "inherit"):
        raise ValueError(
            f"{yaml_path}: method.yaml must name a built container env via "
            f"`env: <name>` (build it with `wfc register-env <name>`) or a "
            f"direct ref `env: container:docker://...@sha256:...`. "
            f"Host execution and the `env: inherit` keyword were removed in "
            f"ADR-019 Cycle H (execution is container-only)."
        )

    inputs = raw.get("inputs", {})
    outputs = raw.get("outputs", {})

    # Fail loud on an unusable OUTPUT slot type at registration. The `type`
    # is the file extension (dotted, verbatim) or a `dir`/`directory` marker;
    # a stale semantic name, bare un-dotted name, or empty value is rejected
    # here rather than silently misnaming the produced file.
    if isinstance(outputs, dict):
        for slot_name, slot_spec in outputs.items():
            slot_type = slot_spec.get("type") if isinstance(slot_spec, dict) else slot_spec
            validate_output_slot_type(slot_name, slot_type, source=yaml_path)

    # INPUT slot `type` is optional/advisory: validate to the same convention
    # when present, but NON-FATALLY (warn, do not raise) so input wiring is
    # not blocked by an advisory annotation.
    if isinstance(inputs, dict):
        for slot_name, slot_spec in inputs.items():
            if isinstance(slot_spec, dict) and "type" in slot_spec:
                try:
                    validate_output_slot_type(slot_name, slot_spec.get("type"))
                except ValueError as exc:
                    logger.warning(
                        "%s: input slot '%s' type is advisory but does not "
                        "follow the extension/dir convention: %s",
                        yaml_path, slot_name, exc,
                    )

    return {
        "inputs":   inputs,
        "outputs":  outputs,
        "params":   raw.get("params", {}),
        "executor": raw.get("executor", "python"),
        "env":      env_value,
        # ADR-019 Cycle D: GPU plumbing for containerized methods. The
        # boolean is read by wfc.cli.run_step and forwarded to
        # wfc.container_runner.build_docker_command as ``--gpus all`` when
        # true. Default false: methods opt in explicitly.
        "gpus":     bool(raw.get("gpus", False)),
    }


# =============================================================================
# Module YAML parser
# =============================================================================

@task(
    purpose="Parse module.yaml contract file into a normalised module definition dict",
    inputs="Path to a module directory (may or may not contain module.yaml)",
    outputs="Normalised module dict with description and contracts, or None",
)
def parse_module_yaml(module_dir: Path) -> Optional[dict]:
    """Parse ``module.yaml`` from a module directory.

    If the file does not exist, returns ``None`` so callers can skip
    file-based contract loading without failing.

    The returned dict has ``description`` (str or None) and ``contracts``
    (list of contract dicts, each with type/name/value_type/required keys).

    Example ``module.yaml``::

        description: Train and apply binary classifiers on labeled cell data
        contracts:
          - type: output
            name: model
            value_type: model
            required: true
          - type: metric
            name: mcc
            value_type: float
            required: true

    Args:
        module_dir: Directory that may contain ``module.yaml``.

    Returns:
        Parsed module dict, or ``None`` if ``module.yaml`` is absent.

    Raises:
        ValueError: If the YAML is malformed or contains invalid contract entries.
    """
    import yaml

    yaml_path = Path(module_dir) / "module.yaml"
    if not yaml_path.exists():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {yaml_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{yaml_path} must be a YAML mapping at the top level")

    description = raw.get("description", None)
    raw_contracts = raw.get("contracts", [])

    if not isinstance(raw_contracts, list):
        raise ValueError(
            f"{yaml_path}: 'contracts' must be a list, got {type(raw_contracts).__name__}"
        )

    contracts = []
    for i, c in enumerate(raw_contracts):
        if not isinstance(c, dict):
            raise ValueError(
                f"{yaml_path}: contract[{i}] must be a mapping, got {type(c).__name__}"
            )
        if "type" not in c or "name" not in c:
            raise ValueError(
                f"{yaml_path}: contract[{i}] must have 'type' and 'name' keys"
            )
        contracts.append({
            "type": c["type"],
            "name": c["name"],
            "value_type": c.get("value_type"),
            "required": c.get("required", True),
        })

    return {
        "description": description,
        "contracts": contracts,
    }


# =============================================================================
# Column resolution engine (ADR-005)
# =============================================================================

def resolve_columns(column_spec: dict | None, params: dict) -> set[str]:
    """Expand a column spec into a set of required column names.

    Supports three resolution strategies that can be combined:
      - ``strict``: literal column names
      - ``from_params``: column names derived from runtime parameter values
        via cartesian product expansion
      - ``patterns``: not expanded here (checked separately via check_patterns)

    Args:
        column_spec: The ``columns`` dict from a slot definition.  May contain
            ``strict``, ``from_params``, and/or ``patterns`` keys.
        params: The run's parameter dict for resolving ``from_params``.

    Returns:
        Set of required column names (from strict + expanded from_params).
        Patterns are NOT included -- use ``check_patterns()`` separately.
    """
    if not column_spec:
        return set()

    columns: set[str] = set()

    # --- strict: literal column names ---
    strict = column_spec.get("strict", [])
    columns.update(strict)

    # --- from_params: param-driven expansion ---
    for entry in column_spec.get("from_params", []):
        param_names = entry.get("params", [])
        pattern = entry.get("pattern", "{}")

        # Collect param values; skip if any param is missing
        param_values: list[list[str]] = []
        skip = False
        for pname in param_names:
            if pname not in params:
                skip = True
                break
            val = params[pname]
            if isinstance(val, list):
                param_values.append([str(v) for v in val])
            else:
                param_values.append([str(val)])

        if skip:
            continue

        # Cartesian product expansion
        for combo in itertools.product(*param_values):
            columns.add(pattern.format(*combo))

    return columns


def read_file_columns(file_path: Path) -> list[str]:
    """Read column names from a CSV or Parquet file without loading data.

    For CSV files, reads only the first line (header row) and strips
    whitespace from each column name.  For Parquet files, reads column
    names from file metadata.

    Args:
        file_path: Path to a CSV or Parquet file.

    Returns:
        List of column names.  Empty list if the file is empty.
    """
    file_path = Path(file_path)
    ext = file_path.suffix.lower()

    if ext in (".parquet", ".pq"):
        try:
            import pyarrow.parquet as pq
            schema = pq.read_schema(file_path)
            return schema.names
        except Exception:
            return []

    # Default: CSV
    try:
        text = file_path.read_text(encoding="utf-8-sig")
        if not text.strip():
            return []
        first_line = text.split("\n", 1)[0]
        return [col.strip() for col in first_line.split(",")]
    except Exception:
        return []


def check_patterns(patterns: list[str], available_columns: list[str]) -> list[str]:
    """Check that at least one column matches each glob pattern.

    Args:
        patterns: List of fnmatch-style patterns (e.g. ``"*_nuc_mean"``).
        available_columns: Actual column names in the file.

    Returns:
        List of patterns that had zero matches (the "missing" patterns).
    """
    missing = []
    for pattern in patterns:
        if not any(fnmatch.fnmatch(col, pattern) for col in available_columns):
            missing.append(pattern)
    return missing


# =============================================================================
# Input validation (hard gate -- ADR-005)
# =============================================================================

def validate_input_columns(
    file_path: Path,
    column_spec: dict | None,
    params: dict,
    slot_name: str = "",
    method_name: str = "",
) -> None:
    """Validate that a CSV/Parquet file contains all required columns.

    This is the hard gate: raises ``ContractViolation`` if any required
    column (from strict, expanded from_params, or patterns) is missing.

    Args:
        file_path: Path to the input CSV or Parquet file.
        column_spec: The ``columns`` dict from the input slot definition.
        params: The run's parameter dict for resolving from_params.
        slot_name: Name of the input slot (for error messages).
        method_name: Name of the method (for error messages).

    Raises:
        ContractViolation: If required columns are missing.
    """
    if not column_spec:
        return

    available = read_file_columns(file_path)
    available_set = set(available)

    # Check strict + from_params columns
    required = resolve_columns(column_spec, params)
    missing = sorted(required - available_set)

    # Check patterns
    patterns = column_spec.get("patterns", [])
    missing_patterns = check_patterns(patterns, available)

    if missing or missing_patterns:
        from wfc_client import ContractViolation

        raise ContractViolation(
            method=method_name,
            missing_columns=missing + missing_patterns,
            available_columns=sorted(available_set),
            slot_name=slot_name,
        )


# =============================================================================
# Output validation (soft warning -- ADR-005)
# =============================================================================

def validate_output_columns(
    file_path: Path,
    column_spec: dict | None,
    params: dict,
    slot_name: str = "",
    method_name: str = "",
) -> None:
    """Check output CSV/Parquet columns and warn on mismatches.

    This is the soft check: logs warnings but does NOT raise exceptions.
    The run is still marked as completed.

    Args:
        file_path: Path to the output CSV or Parquet file.
        column_spec: The ``columns`` dict from the output slot definition.
        params: The run's parameter dict for resolving from_params.
        slot_name: Name of the output slot (for warning messages).
        method_name: Name of the method (for warning messages).
    """
    if not column_spec:
        return

    available = read_file_columns(file_path)
    available_set = set(available)

    # Check strict + from_params columns
    required = resolve_columns(column_spec, params)
    missing = sorted(required - available_set)

    # Check patterns
    patterns = column_spec.get("patterns", [])
    missing_patterns = check_patterns(patterns, available)

    if missing or missing_patterns:
        all_missing = missing + missing_patterns
        logger.warning(
            "Output contract drift for slot '%s' (method '%s'): "
            "missing columns %s. Available: %s",
            slot_name, method_name, all_missing, sorted(available_set),
        )


# =============================================================================
# Directory content validation (soft warning -- ADR-005)
# =============================================================================

def validate_directory_contents(
    directory: Path,
    contents_spec: list[dict] | None,
    params: dict,
    method_name: str = "",
) -> None:
    """Check directory contents against declared patterns and column specs.

    For each entry in ``contents_spec``:
      - Glob the pattern against actual files in the directory.
      - Check ``min_count`` (default 0) -- warn if fewer files match.
      - For matched CSV files with a ``columns`` spec, check column presence.

    All checks are soft (warnings only, no exceptions).

    Args:
        directory: Path to the output directory.
        contents_spec: List of content assertion dicts from method.yaml.
        params: The run's parameter dict for resolving from_params.
        method_name: Name of the method (for warning messages).
    """
    if not contents_spec:
        return

    directory = Path(directory)
    for entry in contents_spec:
        pattern = entry.get("pattern", "")
        min_count = entry.get("min_count", 0)
        column_spec = entry.get("columns", None)

        # Glob match
        matched_files = sorted(directory.glob(pattern))

        if len(matched_files) < min_count:
            logger.warning(
                "Directory content assertion failed (method '%s'): "
                "pattern '%s' matched %d file(s), min_count is %d",
                method_name, pattern, len(matched_files), min_count,
            )

        # Column check on matched CSV/Parquet files
        if column_spec and matched_files:
            for f in matched_files:
                ext = f.suffix.lower()
                if ext in (".csv", ".parquet", ".pq"):
                    validate_output_columns(
                        f, column_spec, params,
                        slot_name=str(f.relative_to(directory)),
                        method_name=method_name,
                    )


# =============================================================================
# Static cross-step column validation (ADR-005)
# =============================================================================

def cross_check_columns(
    upstream_output_spec: dict | None,
    downstream_input_spec: dict | None,
) -> list[str]:
    """Statically check column compatibility between connected steps.

    Only checks ``strict`` columns -- ``from_params`` columns are deferred
    to runtime because the param values may not be known at pipeline-load
    time.  ``patterns`` are also skipped (they require actual data).

    Args:
        upstream_output_spec: The ``columns`` dict from the upstream output slot.
        downstream_input_spec: The ``columns`` dict from the downstream input slot.

    Returns:
        List of warning messages.  Empty list if compatible.
    """
    if not upstream_output_spec or not downstream_input_spec:
        return []

    upstream_strict = set(upstream_output_spec.get("strict", []))
    downstream_strict = set(downstream_input_spec.get("strict", []))

    if not upstream_strict or not downstream_strict:
        return []

    missing = sorted(downstream_strict - upstream_strict)
    if missing:
        return [
            f"Downstream requires strict columns {missing} "
            f"not declared in upstream output (has {sorted(upstream_strict)})"
        ]

    return []
