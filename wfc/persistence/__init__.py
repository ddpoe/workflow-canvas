"""Persistence: the project's relational store.

- :mod:`.schema` holds the fourteen table classes and their enums.
- :mod:`.engine` holds the process engine and its sessions;
  :func:`bootstrap_engine`, the one path that builds an engine;
  :func:`write_transaction`, the one way a phase opens a write transaction
  locked from its start; and :func:`use_project`, which binds another project
  for a block.
- :mod:`.backfill` holds the schema backfill: missing columns added, tables
  whose unique constraints or indexes (partial predicates included) drifted
  rebuilt.
- :mod:`.accessor` holds the project-root accessor and its per-process cache.
- :mod:`.config` holds the reader for the project's ``wf-canvas.toml`` and
  :func:`set_registry_url`, the text edit that sets its ``[registry] url``.
- :mod:`.run_graph` holds the run-graph reader: :func:`read_run_records`,
  run rows as the records Lineage walks, and :func:`list_runs`, each run's
  id, pipeline, node, method, sample and status.

Inside ``wfc`` the package imports only Layout.
"""

from .accessor import project_root
from .backfill import ensure_schema
from .config import (
    INTEGRITY_INPUTS_HASH,
    INTEGRITY_INPUTS_STAT_THEN_HASH,
    INTEGRITY_INPUTS_VALUES,
    read_config,
    set_registry_url,
)
from .engine import (
    BUSY_TIMEOUT_SECONDS,
    bootstrap_engine,
    build_engine,
    database_url,
    get_engine,
    get_session,
    reset_engine,
    use_project,
    write_transaction,
)
from .run_graph import RunGraphRecord, RunListing, list_runs, read_run_records
from .schema import (
    CANCEL_REASON_ENGINE_FAILED,
    CANCEL_REASON_INTERRUPTED,
    CANCEL_REASON_NOT_RUN,
    CANCEL_REASON_UPSTREAM_FAILED,
    CANCEL_REASON_USER,
    CANCEL_REASONS,
    ENGINE_SIDE_CANCEL_REASONS,
    RUNNING_CLAIM_PREDICATE,
    Blob,
    InUseKind,
    InUseRecord,
    Method,
    MethodContract,
    MethodVersion,
    Module,
    ModuleContract,
    OutputIntegrity,
    ParamDef,
    PushStatus,
    Run,
    RunAnnotation,
    RunInput,
    RunOutput,
    Sample,
    TrackedFunction,
)

__all__ = [
    "BUSY_TIMEOUT_SECONDS",
    "CANCEL_REASONS",
    "CANCEL_REASON_ENGINE_FAILED",
    "CANCEL_REASON_INTERRUPTED",
    "CANCEL_REASON_NOT_RUN",
    "CANCEL_REASON_UPSTREAM_FAILED",
    "CANCEL_REASON_USER",
    "ENGINE_SIDE_CANCEL_REASONS",
    "INTEGRITY_INPUTS_HASH",
    "INTEGRITY_INPUTS_STAT_THEN_HASH",
    "INTEGRITY_INPUTS_VALUES",
    "RUNNING_CLAIM_PREDICATE",
    "Blob",
    "InUseKind",
    "InUseRecord",
    "Method",
    "MethodContract",
    "MethodVersion",
    "Module",
    "ModuleContract",
    "OutputIntegrity",
    "ParamDef",
    "PushStatus",
    "Run",
    "RunAnnotation",
    "RunGraphRecord",
    "RunInput",
    "RunListing",
    "RunOutput",
    "Sample",
    "TrackedFunction",
    "bootstrap_engine",
    "build_engine",
    "database_url",
    "ensure_schema",
    "get_engine",
    "get_session",
    "list_runs",
    "project_root",
    "read_config",
    "read_run_records",
    "reset_engine",
    "set_registry_url",
    "use_project",
    "write_transaction",
]
