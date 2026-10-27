"""Registration: bring modules, methods and samples into the project's registry.

Consumers import this package: the CLI's ``register-*`` verbs, the canvas's
registry routes, the demo scaffold, Execution's claim phase and Execution's
composer.

- ``register_module`` and ``register_method``: module and method
  registration. Method registration is planned before anything is written
  (``plan_method_registration``: the outcome, new, changed or same, and its
  message) and then applied (``apply_method_registration``); a plan that
  adds a version is applied only when its caller confirms it
  (``register_method``'s ``assume_yes``, or the CLI's prompt;
  ``RegistrationConfirmationRequired`` otherwise). It locates and scans the script
  (``discovery``, ``ast_scanner``), checks its saves against the contract
  (``method_ast``), commits the method directory to git (``git_commit``) and
  writes the source snapshot (``snapshot``).
- ``register_sample``: refuse, hand the bytes to Storage's sample store, and
  write the sample row.
- ``load_contract_map``: every registered method version's contract (one
  per version, keyed by its content identity, the newest named), for the
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
- ``read_version_list`` / ``write_version_list``: a method's git-tracked
  version list (``VersionEntry``; the writer is registration's to call).
- ``sync_version_rows``: MethodVersion rows and contracts follow the list;
  ``newest_version``, ``method_contract`` (a version's contract, the
  newest when none is named) and ``version_contracts`` read through it;
  ``chosen_version`` is the one rule that picks a version from such a list
  (the newest, or the newest entry carrying a named identity);
  ``contracts_by_version`` reads stored contracts by version row id with no
  sync, in the caller's session.
- ``registered_env``: the env a method version runs in and is keyed on
  (its list entry's env; the newest version when none is named);
  ``version_entry`` is that entry itself (env fingerprint, image reference).
- ``copy_file_digests`` / ``changed_copy_files`` / ``drop_copy_stamp``: the
  post-run re-check of a copy a backend could not bind read-only.
- ``resolve_method_code``: the one resolver for a method version's code:
  its extracted, verified copy under ``.wfc/runs/code/<tree hash>/`` and the
  script the copy selects (``extract_version_copy``);
  ``verify_registered_snapshot``: the verify rule over that copy.
  Registration itself restores a snapshot changed outside wfc before it
  writes (``registered_code``).
- ``versions_recording_env``: every version of every method that records a
  given env, read from the committed version lists, for ``wfc delete-env``'s
  refusal; ``methods_referencing_env``: every registered method whose env
  names it, for ``wfc demo --remove``'s warning;
  ``methods_whose_newest_version_uses_env``: the methods whose newest
  version records it, for ``wfc register-env --force``;
  ``version_fingerprints_recording_env``: every version recording it with
  the image it recorded, for ``register-env --force``'s interpreter check.

Method registration validates a method's env with ``check_method_env``
from ``wfc.environments``, Environments & containers' package surface.
"""

from .ast_scanner import FunctionInfo, ParamInfo, ScriptInfo, scan_script
from .contract_map import load_contract_map
from .env_references import (
    methods_referencing_env,
    methods_whose_newest_version_uses_env,
    version_fingerprints_recording_env,
    versions_recording_env,
)
from .method import (
    MethodRegistration,
    MethodRegistrationPlan,
    RegistrationConfirmationRequired,
    RegistrationPlanChangedError,
    apply_method_registration,
    plan_method_registration,
    register_method,
)
from .method_ast import validate_save_artifacts
from .method_version import (
    VersionEntry,
    read_version_list,
    snapshot_code_fingerprint,
    write_version_list,
)
from .module import register_module
from .registered_code import (
    MalformedMethodRecordError,
    MethodCode,
    RegisteredCode,
    SnapshotChangedError,
    SnapshotPresentError,
    SyncedVersion,
    UnknownVersionError,
    UnregisteredSnapshotError,
    changed_copy_files,
    chosen_version,
    contracts_by_version,
    copy_file_digests,
    drop_copy_stamp,
    extract_version_copy,
    method_contract,
    newest_version,
    registered_env,
    resolve_method_code,
    sync_version_rows,
    verify_registered_snapshot,
    version_contracts,
    version_entry,
)
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
    "MalformedMethodRecordError",
    "MalformedSampleError",
    "MethodCode",
    "MethodRegistration",
    "ParamInfo",
    "RegisteredCode",
    "SnapshotChangedError",
    "SnapshotPresentError",
    "UnknownVersionError",
    "UnregisteredSnapshotError",
    "extract_version_copy",
    "methods_whose_newest_version_uses_env",
    "version_contracts",
    "chosen_version",
    "contracts_by_version",
    "registered_env",
    "version_entry",
    "copy_file_digests",
    "changed_copy_files",
    "drop_copy_stamp",
    "resolve_method_code",
    "verify_registered_snapshot",
    "SampleHealth",
    "SampleManifestError",
    "SampleState",
    "ScriptInfo",
    "UnreachableSampleError",
    "classify_sample",
    "classify_samples",
    "SyncedVersion",
    "VersionEntry",
    "method_contract",
    "newest_version",
    "read_version_list",
    "sync_version_rows",
    "write_version_list",
    "snapshot_code_fingerprint",
    "load_contract_map",
    "load_sample_hashes",
    "load_sample_health",
    "methods_referencing_env",
    "preflight_sample_content",
    "read_sample_manifest",
    "register_method",
    "plan_method_registration",
    "apply_method_registration",
    "MethodRegistrationPlan",
    "RegistrationConfirmationRequired",
    "RegistrationPlanChangedError",
    "register_module",
    "register_sample",
    "scan_script",
    "validate_save_artifacts",
    "version_fingerprints_recording_env",
    "versions_recording_env",
]
