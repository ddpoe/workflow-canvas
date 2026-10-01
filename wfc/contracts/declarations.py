"""The two contract parsers: ``method.yaml`` and ``module.yaml``.

``parse_method_yaml(method_dir)`` reads the YAML contract from a method
directory and returns a normalised dict with ``inputs``, ``outputs``,
``params``, ``executor``, ``env``, ``script``, ``helpers``, and ``gpus``
keys.  Returns ``None`` if no ``method.yaml`` is present (methods without
explicit contracts are still valid -- they just won't have slot-level
metadata in the DB).

``parse_module_yaml(module_dir)`` does the same for a module's
``module.yaml`` and its ``contracts`` list.
"""

from __future__ import annotations

import logging
from pathlib import Path

from axiom_annotations import task

from .columns import validate_columns_block
from .slots import validate_output_slot_type, validate_slot_name
from .vocabulary import PARAM_TYPE_ALIASES, PARAM_TYPES

logger = logging.getLogger(__name__)


def _canonical_param_type(param_name: str, value, source: Path) -> str:
    """Return the canonical spelling of a declared param ``type``.

    A canonical name or an accepted spelling from the one alias table
    resolves case-insensitively; anything else is refused naming the
    parameter and listing the canonical vocabulary.

    Args:
        param_name: The parameter being declared (for the message).
        value: The ``type`` value as read from ``method.yaml``.
        source: Path of the declaration file (for the message).

    Returns:
        One of ``PARAM_TYPES``.

    Raises:
        ValueError: If ``value`` is not a canonical name or accepted spelling.
    """
    if isinstance(value, str):
        key = value.lower()
        if key in PARAM_TYPES:
            return key
        if key in PARAM_TYPE_ALIASES:
            return PARAM_TYPE_ALIASES[key]
    raise ValueError(
        f"{source}: param '{param_name}' type {value!r} is not a known type. "
        f"A param declares `type: <type>` where <type> is one of "
        f"{', '.join(PARAM_TYPES)}."
    )


# =============================================================================
# YAML parser
# =============================================================================

@task(
    purpose="Parse method.yaml contract file into a normalised slot definition dict",
    inputs="Path to a method directory (may or may not contain method.yaml)",
    outputs="Normalised contract dict with inputs/outputs/params/executor/env/script/helpers/gpus, or None",
)
def parse_method_yaml(method_dir: Path) -> dict | None:
    """Parse ``method.yaml`` from a method directory.

    If the file does not exist, returns ``None`` so callers can skip
    contract registration without failing.

    The returned dict always has all eight top-level keys: ``inputs``,
    ``outputs``, and ``params`` (default to empty dicts), ``executor``
    (default ``"python"``), ``env`` (required -- a missing or ``inherit``
    value is rejected), ``script`` and ``helpers`` (optional, default
    ``None``), and ``gpus`` (default ``False``).  The ``columns`` key on
    input/output slots and ``contents`` key on directory output slots are
    preserved as-is from the YAML.  A param's ``type`` is written back in
    its canonical spelling (``PARAM_TYPES``); an accepted alias resolves,
    anything else is refused.

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

    # Execution is container-only. Every method must name a built container
    # env -- there is no host-Python fallback and no ``inherit`` default.
    # Reject a missing ``env`` and the ``env: inherit`` keyword at parse
    # time so a non-runnable method is caught at registration rather than
    # failing mid-pipeline.
    env_value = raw.get("env")
    if not env_value or (isinstance(env_value, str) and env_value.strip() == "inherit"):
        raise ValueError(
            f"{yaml_path}: method.yaml must name a built container env: "
            f"declare `env: <name>` where <name> is a registered env "
            f"(build one with `wfc register-env <name>`)."
        )

    inputs = raw.get("inputs", {})
    outputs = raw.get("outputs", {})

    # A slot NAME is carried through grammars with punctuation of their own --
    # the parent entry (input:output:run), --ref-input (label=path), argv. A
    # name carrying one of those delimiters does not fail there; it reads as a
    # DIFFERENT wiring, and the run is keyed under it. Refuse the name where it
    # is declared, for inputs and outputs alike.
    for slots, kind in ((inputs, "input"), (outputs, "output")):
        if isinstance(slots, dict):
            for slot_name in slots:
                validate_slot_name(slot_name, kind=kind, source=yaml_path)

    # Fail loud on an unusable OUTPUT slot type at registration. The `type`
    # is the file extension (dot optional in the YAML; canonical form is
    # dotted) or a `dir`/`directory` marker; an empty or missing value is
    # rejected here rather than silently misnaming the produced file. The
    # canonical form is written back so the stored contract is uniform
    # regardless of which spelling the author used.
    if isinstance(outputs, dict):
        for slot_name, slot_spec in outputs.items():
            slot_type = slot_spec.get("type") if isinstance(slot_spec, dict) else slot_spec
            canonical = validate_output_slot_type(slot_name, slot_type, source=yaml_path)
            if isinstance(slot_spec, dict):
                slot_spec["type"] = canonical
            else:
                outputs[slot_name] = canonical

    # INPUT slot `type` is optional/advisory: validate to the same convention
    # when present, but NON-FATALLY (warn, do not raise) so input wiring is
    # not blocked by an advisory annotation. Valid values are normalised to
    # the canonical form like outputs.
    if isinstance(inputs, dict):
        for slot_name, slot_spec in inputs.items():
            if isinstance(slot_spec, dict) and "type" in slot_spec:
                try:
                    slot_spec["type"] = validate_output_slot_type(
                        slot_name, slot_spec.get("type")
                    )
                except ValueError as exc:
                    logger.warning(
                        "%s: input slot '%s' type is advisory but does not "
                        "follow the extension/dir convention: %s",
                        yaml_path, slot_name, exc,
                    )

    # A `columns` block on an input or output slot is a declaration the
    # canvas dropdown and the load-time cross-check read; a malformed one
    # would parse clean and resolve to nothing, so its shape is refused
    # here naming the slot. Well-formed blocks pass through untouched.
    for slots in (inputs, outputs):
        if isinstance(slots, dict):
            for slot_name, slot_spec in slots.items():
                if isinstance(slot_spec, dict) and slot_spec.get("columns") is not None:
                    validate_columns_block(slot_name, slot_spec["columns"], source=yaml_path)

    # Optional explicit script selector. Passed through verbatim; extension
    # recognition and file existence are validated at registration
    # (wfc.registration.register_method), which is the one place that knows the
    # recognized-extension set and the resolved method dir.
    script_value = raw.get("script")
    if script_value is not None and not isinstance(script_value, str):
        raise ValueError(
            f"{yaml_path}: `script:` must be a filename string, "
            f"got {type(script_value).__name__}"
        )

    # Optional strict-helpers declaration. ABSENT -> permissive mode.
    # PRESENT (even as an empty list)
    # -> strict mode: snapshot + fingerprint cover exactly the main script
    # plus these declared helpers, and any other recognized-extension file in
    # the method dir is a registration error. Path rules are enforced at
    # registration (wfc.registration.register_method).
    helpers_value = raw.get("helpers")
    if helpers_value is not None:
        if not isinstance(helpers_value, list) or not all(
            isinstance(h, str) for h in helpers_value
        ):
            raise ValueError(
                f"{yaml_path}: `helpers:` must be a list of "
                f"method-dir-relative path strings"
            )

    # A param's declared `type` comes from the closed vocabulary. An accepted
    # spelling (case-insensitive; the one table in `vocabulary`) is
    # normalised to the canonical name and written back, so the stored
    # contract and everything downstream see only canonical names; anything
    # else is refused here, naming the parameter.
    params = raw.get("params", {})
    if isinstance(params, dict):
        for param_name, param_spec in params.items():
            if isinstance(param_spec, dict) and "type" in param_spec:
                param_spec["type"] = _canonical_param_type(
                    param_name, param_spec["type"], source=yaml_path
                )

    return {
        "inputs":   inputs,
        "outputs":  outputs,
        "params":   params,
        "executor": raw.get("executor", "python"),
        "env":      env_value,
        "script":   script_value,
        "helpers":  helpers_value,
        # GPU plumbing for containerized methods. The boolean is read by
        # wfc.execution.dispatch and forwarded to
        # wfc.environments.argv.build_docker_command as ``--gpus all`` when
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
def parse_module_yaml(module_dir: Path) -> dict | None:
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
