"""Contracts — the Tier 0 method promise and the vocabulary the system shares with method code.

What a method promises — its declared input and output slots, their types
and filenames, its parameters, its columns, its env — and the vocabulary the
system uses to talk to method code. Documented by ``docs/system/contracts.json``.

The package is pure: stdlib plus the YAML parser, no import from ``wfc``,
no database, no Docker, no subprocess, no network, no file writes. Its
inputs are values and declaration files; the consumers keep the I/O.

Family modules:

- ``declarations`` — the ``method.yaml`` and ``module.yaml`` parsers.
- ``slots`` — the output-slot type vocabulary, slot filename derivation and
  node-config slot resolution.
- ``columns`` — the column family.
- ``enrichment`` — the pure enrichment pass over a sparse pipeline document.
- ``fingerprint`` — the computation-bearing projection of a parsed
  ``method.yaml``, rendered for the code fingerprint.
- ``envspec`` — the env-spec grammar (an env spec is the name of a registered
  env; two entry points over one name rule) and the image-reference grammar
  (what may be stored, what may be typed at ``register-env``).
- ``vocabulary`` — the declared values the backend shares.
"""

from __future__ import annotations

from .columns import (
    cross_check_columns,
    resolve_columns,
    validate_columns_block,
)
from .declarations import parse_method_yaml, parse_module_yaml
from .enrichment import enrich_pipeline
from .envspec import (
    ENV_NAME_RULE,
    ENV_SPEC_FORM,
    is_env_name,
    parse_byo_ref,
    parse_env_spec,
    validate_container_ref,
    validate_env_name,
)
from .fingerprint import render_contract_projection
from .slots import (
    is_directory_slot,
    output_slot_filename,
    resolve_node_outputs,
    validate_output_slot_type,
    validate_slot_name,
)
from .vocabulary import (
    COLLAPSED_SAMPLE,
    PARAM_TYPE_ALIASES,
    PARAM_TYPES,
    WFC_ENV_VARS,
)

__all__ = [
    "COLLAPSED_SAMPLE",
    "WFC_ENV_VARS",
    "PARAM_TYPES",
    "PARAM_TYPE_ALIASES",
    "ENV_NAME_RULE",
    "ENV_SPEC_FORM",
    "is_env_name",
    "parse_env_spec",
    "validate_env_name",
    "cross_check_columns",
    "enrich_pipeline",
    "is_directory_slot",
    "output_slot_filename",
    "parse_byo_ref",
    "parse_method_yaml",
    "parse_module_yaml",
    "render_contract_projection",
    "resolve_columns",
    "resolve_node_outputs",
    "validate_columns_block",
    "validate_container_ref",
    "validate_output_slot_type",
    "validate_slot_name",
]
