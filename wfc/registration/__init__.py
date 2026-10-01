"""Registration: bring modules, methods and samples into the project's registry.

Consumers import this package: the CLI's ``register-*`` verbs, the canvas's
registry routes, the demo scaffold, Execution's claim phase and Execution's
composer.

- ``register_module`` and ``register_method``: module and method
  registration. Method registration locates and scans the script
  (``discovery``, ``ast_scanner``), checks its saves against the contract
  (``method_ast``), commits the method directory to git (``git_commit``) and
  writes the source snapshot (``snapshot``).
- ``register_sample``: refuse, hand the bytes to Storage's sample store, and
  write the sample row.
- ``load_contract_map``: every registered method's contract, for the
  enrichment pass.
- ``load_sample_hashes``: the content hash of each named registered sample,
  for the composer (the emitter's restore rule). It refuses a hashless row
  with ``MalformedSampleError``, which ``wfc run-pipeline`` catches and
  prints beside ``UnreachableSampleError``.
- ``preflight_sample_content`` and the ``classify_sample`` table beside it:
  whether a registered sample's bytes are still reachable (local cache x
  ``push_status``, no network I/O). The preflight refuses a pipeline whose
  samples include unreachable content, at pipeline start and before any run
  row; ``wfc doctor``'s ``samples`` check reports the same table's counts.
- ``get_or_create_version``: the MethodVersion row for a method's code
  fingerprint.
- ``methods_referencing_env``: every registered method whose env names a
  given env, for ``wfc delete-env``'s warning.

Method registration validates a method's env with ``check_method_env``
from ``wfc.environments``, Environments & containers' package surface.
"""

from .ast_scanner import FunctionInfo, ParamInfo, ScriptInfo, scan_script
from .contract_map import load_contract_map
from .env_references import methods_referencing_env
from .method import register_method
from .method_ast import validate_save_artifacts
from .method_version import get_or_create_version
from .module import register_module
from .sample import register_sample
from .sample_hashes import MalformedSampleError, load_sample_hashes
from .sample_health import (
    SampleHealth,
    SampleState,
    UnreachableSampleError,
    classify_sample,
    classify_samples,
    load_sample_health,
    preflight_sample_content,
)
from .sample_manifest import SampleManifestError, read_sample_manifest
from .snapshot import HELPER_SNAPSHOT_DIR

__all__ = [
    "FunctionInfo",
    "HELPER_SNAPSHOT_DIR",
    "MalformedSampleError",
    "ParamInfo",
    "SampleHealth",
    "SampleManifestError",
    "SampleState",
    "ScriptInfo",
    "UnreachableSampleError",
    "classify_sample",
    "classify_samples",
    "get_or_create_version",
    "load_contract_map",
    "load_sample_hashes",
    "load_sample_health",
    "methods_referencing_env",
    "preflight_sample_content",
    "read_sample_manifest",
    "register_method",
    "register_module",
    "register_sample",
    "scan_script",
    "validate_save_artifacts",
]
