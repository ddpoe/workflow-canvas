"""
SQLModel table definitions for the Process Manager MVP.

9-table schema:
  modules, methods, tracked_functions, param_defs,
  module_contracts, runs, run_inputs, run_outputs, samples
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List

from sqlmodel import SQLModel, Field, Relationship, Column
from sqlalchemy import JSON, UniqueConstraint, text


# =============================================================================
# Push-status enum (ADR-018)
# =============================================================================

class PushStatus(str, Enum):
    """Status of a RunOutput / Sample's push to the configured DVC remote.

    Lifecycle when a remote is configured:
      pending -> in_flight -> pushed         (happy path)
      pending -> in_flight -> failed -> ... (retried with backoff up to 5 attempts)

    ``deferred`` is the row-default when no remote is configured at row-insert
    time — these rows are never enqueued by the worker.
    """
    pending = "pending"
    in_flight = "in_flight"
    pushed = "pushed"
    failed = "failed"
    deferred = "deferred"


# =============================================================================
# Module & Method hierarchy
# =============================================================================

class Module(SQLModel, table=True):
    """Pipeline stage / domain (e.g. data_preprocessing, data_labeling)."""

    __tablename__ = "modules"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=200)
    description: Optional[str] = None

    # relationships
    methods: List["Method"] = Relationship(back_populates="module")
    contracts: List["ModuleContract"] = Relationship(back_populates="module")


class Method(SQLModel, table=True):
    """Specific analysis approach within a module."""

    __tablename__ = "methods"

    id: Optional[int] = Field(default=None, primary_key=True)
    module_id: int = Field(foreign_key="modules.id", index=True)
    name: str = Field(index=True, max_length=200)
    script_path: Optional[str] = Field(default=None, max_length=500)
    # Required typed env spec or named shared env (e.g. "pixi:image-io",
    # "conda:analysis", "container:<name>", "byo", or a bare manifest name).
    # No Python-side default — a method must declare an env (enforced at
    # registration parse, pre_run, and snakemake_gen). The DB-only
    # ``server_default=''`` exists ONLY so ``ensure_schema`` can additively
    # ADD this NOT-NULL column to a *legacy* methods table that predates it
    # (SQLite needs a constant DEFAULT to backfill existing rows). Backfilled
    # legacy rows get ``''`` — which the run-time guards reject loudly, exactly
    # as a missing env should (it is NOT a silent working default like the old
    # ``"inherit"``).
    env: str = Field(
        max_length=50,
        sa_column_kwargs={"server_default": text("''")},
    )

    # relationships
    module: Optional[Module] = Relationship(back_populates="methods")
    tracked_functions: List["TrackedFunction"] = Relationship(back_populates="method")
    runs: List["Run"] = Relationship(back_populates="method")
    contract: Optional["MethodContract"] = Relationship(back_populates="method")
    versions: List["MethodVersion"] = Relationship(back_populates="method")


class MethodVersion(SQLModel, table=True):
    """Records a content-addressed code version of a method.

    Deduplicates on (method_id, code_fingerprint) -- one row per unique
    source-code snapshot of each method.  The DB UniqueConstraint is the
    safety net; get_or_create_version() provides the upsert logic on top.

    git_commit is retained as optional audit metadata (nullable) -- it
    records which commit was checked out when the version was first seen,
    but does not participate in identity or cache key computation.
    """

    __tablename__ = "method_versions"
    __table_args__ = (
        UniqueConstraint("method_id", "code_fingerprint", name="uq_method_version"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    code_fingerprint: str = Field(max_length=64)
    git_commit: Optional[str] = Field(default=None, max_length=40)
    recorded_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # relationships
    method: Optional[Method] = Relationship(back_populates="versions")


# =============================================================================
# Parameter schema
# =============================================================================

class TrackedFunction(SQLModel, table=True):
    """A function in a method whose parameters are tracked."""

    __tablename__ = "tracked_functions"

    id: Optional[int] = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    function_name: str = Field(max_length=200)
    ordinal: int = Field(default=0)

    # relationships
    method: Optional[Method] = Relationship(back_populates="tracked_functions")
    param_defs: List["ParamDef"] = Relationship(back_populates="tracked_function")


class ParamDef(SQLModel, table=True):
    """Parameter definition for a tracked function."""

    __tablename__ = "param_defs"

    id: Optional[int] = Field(default=None, primary_key=True)
    tracked_function_id: int = Field(foreign_key="tracked_functions.id", index=True)
    param_name: str = Field(max_length=200)
    param_type: Optional[str] = Field(default=None, max_length=100)
    default_value: Optional[str] = None

    # relationships
    tracked_function: Optional[TrackedFunction] = Relationship(back_populates="param_defs")


# =============================================================================
# Run tracking
# =============================================================================

class Run(SQLModel, table=True):
    """A single execution of a method with specific parameters."""

    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    params: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    sample: Optional[str] = Field(default=None, max_length=200, index=True)
    status: str = Field(default="running", max_length=50)
    pipeline_id: Optional[str] = Field(default=None, max_length=200, index=True)
    nf_process_name: Optional[str] = Field(default=None, max_length=200)
    # started_at is nullable: cancelled rows (written post-hoc at pipeline-end
    # by ``_write_cancelled_rows``) never executed and carry ``started_at=None``.
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = None
    metrics: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Gap 15: versioning & caching
    version_id: Optional[int] = Field(default=None, foreign_key="method_versions.id", index=True)
    cache_key: Optional[str] = Field(default=None, max_length=64, index=True)
    cache_source_run_id: Optional[int] = Field(default=None, foreign_key="runs.id")
    # env_fingerprint: 32-char MD5 hex of the DVC-cached env content blob
    # captured at pre_run time (lock + pip freeze, or a container env's
    # image fingerprint).  Folded into the cache key so env drift
    # invalidates cache.  Nullable for backward compat with pre-env-fp runs.
    env_fingerprint: Optional[str] = Field(default=None, max_length=32)
    # cache_source_run_id is set when a run reused a previous run's outputs;
    # the run still appears in the audit log, it just did not re-execute.
    # ADR 004: error persistence
    error_message: Optional[str] = Field(default=None)
    error_traceback: Optional[str] = Field(default=None)
    # Causality link for cancelled rows: points at the nearest failed ancestor
    # in the frozen pipeline DAG. Populated only for ``status='cancelled'``
    # rows written post-hoc by ``_write_cancelled_rows`` at pipeline-end.
    # NULL on executed rows and on cancelled rows whose failed ancestor could
    # not be resolved (defensive -- shouldn't happen in practice).
    cancelled_due_to_run_id: Optional[int] = Field(
        default=None, foreign_key="runs.id", index=True
    )
    # NID: custom run label from canvas node (nullable = auto-version)
    nid: Optional[str] = Field(default=None, max_length=200)

    # relationships
    method: Optional[Method] = Relationship(back_populates="runs")
    inputs: List["RunInput"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"foreign_keys": "[RunInput.run_id]"},
    )
    outputs: List["RunOutput"] = Relationship(back_populates="run")
    annotation: Optional["RunAnnotation"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )


class RunAnnotation(SQLModel, table=True):
    """User-editable metadata attached to a Run.

    Kept separate from ``runs`` so that immutable provenance (code, params,
    cache_key chain) stays distinct from mutable UI metadata. New annotation
    concepts (notes, pins, colors, ...) can be added here without touching
    the provenance schema.
    """

    __tablename__ = "run_annotations"

    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    favorite: bool = Field(default=False)
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    # When set, the run is soft-deleted: hidden from default views but still
    # in the DB. Hard delete is only permitted after archiving. NULL = live.
    # Timestamp (not bool) so the UI can show "archived 3d ago" and future
    # auto-retention policies can act on age.
    archived_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    run: Optional[Run] = Relationship(back_populates="annotation")


class ModuleContract(SQLModel, table=True):
    """Contract that all methods in a module must satisfy.

    Two contract types:
      - 'output': a required file artifact (e.g. 'feature_set_list' → '.parquet')
      - 'metric': a required metric key (e.g. 'n_features' → 'int')
    """

    __tablename__ = "module_contracts"

    id: Optional[int] = Field(default=None, primary_key=True)
    module_id: int = Field(foreign_key="modules.id", index=True)
    contract_type: str = Field(max_length=50)       # 'output' or 'metric'
    name: str = Field(max_length=200)               # artifact name or metric name
    value_type: Optional[str] = Field(default=None, max_length=100)  # '.parquet', 'int', etc.
    required: bool = Field(default=True)

    # relationships
    module: Optional[Module] = Relationship(back_populates="contracts")

    class Config:
        table_name = "module_contracts"


class RunInput(SQLModel, table=True):
    """Data consumed by a run (links to upstream run for lineage)."""

    __tablename__ = "run_inputs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    source_run_id: Optional[int] = Field(default=None, foreign_key="runs.id")
    input_name: Optional[str] = Field(default=None, max_length=200)
    artifact_path: Optional[str] = Field(default=None, max_length=500)

    # relationships
    run: Optional[Run] = Relationship(
        back_populates="inputs",
        sa_relationship_kwargs={"foreign_keys": "[RunInput.run_id]"},
    )


class RunOutput(SQLModel, table=True):
    """Artifact produced by a run."""

    __tablename__ = "run_outputs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    output_name: Optional[str] = Field(default=None, max_length=200)
    artifact_path: Optional[str] = Field(default=None, max_length=500)
    # artifact_type has no default — must be passed explicitly at every write site.
    # Valid values: "module_file", "method_file", "module_directory", "method_directory".
    # Only "module_file" rows participate in build_input_fingerprint().
    # "_directory" variants are reserved enum values; directory handling not yet implemented.
    artifact_type: str = Field(max_length=100)
    # Gap 15: size and mtime captured at write time (from the file on disk).
    # mtime is read from the source file before any copy so calendar-day variation
    # in copy timestamps doesn't poison fingerprints for re-registered identical content.
    file_size: Optional[int] = None
    file_mtime: Optional[float] = None
    # ADR-007 Phase 2: content-addressed hash (md5 hex).  Nullable for backward
    # compat with pre-DVC runs.  Populated by complete_run for successful runs.
    content_hash: Optional[str] = Field(default=None, max_length=32)

    # ADR-018: async push-to-remote bookkeeping. ``push_status`` defaults to
    # ``deferred`` (the no-remote-configured terminal). The CLI flips it to
    # ``pending`` at row-insert time when a remote is configured; the push
    # worker drives the lifecycle from there.
    push_status: str = Field(default=PushStatus.deferred.value, max_length=20, index=True)
    pushed_at: Optional[datetime] = Field(default=None)
    push_attempts: int = Field(default=0)
    push_error: Optional[str] = Field(default=None)

    # relationships
    run: Optional[Run] = Relationship(back_populates="outputs")


# =============================================================================
# Method contracts (Gap 3: shim contract / method.yaml)
# =============================================================================

class MethodContract(SQLModel, table=True):
    """Per-method named slot contract parsed from ``method.yaml``.

    Captures input slots, output slots (with types and filenames),
    params schema, and executor hint so the Snakemake generator
    and future NF/Docker shims can read contracts from the DB instead
    of re-parsing YAML at generation time.
    """

    __tablename__ = "method_contracts"

    id: Optional[int] = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", unique=True, index=True)
    input_slots: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # {slot_name: {type, required, multiple, description}}
    output_slots: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # {slot_name: {type, required, description}}
    params_schema: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # {param_name: {type, required, default, description}}
    executor: str = Field(default="python", max_length=50)
    # "python" | "nextflow"  (forward-looking: NF shim generation)

    # relationships
    method: Optional[Method] = Relationship(back_populates="contract")


# =============================================================================
# Sample registry
# =============================================================================

class Sample(SQLModel, table=True):
    """A registered data sample managed by wfc.

    When a user registers a sample, the source file is copied into
    ``data/samples/{name}/`` and tracked here. Root pipeline steps
    automatically resolve their input from the registered path.
    """

    __tablename__ = "samples"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=200)
    source_path: str = Field(max_length=500)
    registered_path: str = Field(max_length=500)
    file_type: str = Field(max_length=50)
    registered_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # Gap 15: size and mtime captured from the source file *before* the copy.
    # Post-copy timestamps vary by calendar day and would produce different mtimes
    # for identical content re-registered later, poisoning cache fingerprints.
    file_size: Optional[int] = None
    file_mtime: Optional[float] = None
    # "copy" = wfc owns the file (default); "link" = wfc records path only (deferred).
    registration_mode: str = Field(default="copy", max_length=20)
    # DVC content hash (MD5). Set by register_sample when DVC is configured.
    # NULL for legacy samples registered before DVC integration.
    content_hash: Optional[str] = Field(default=None, max_length=32)

    # ADR-018: async push-to-remote bookkeeping for sample registrations.
    # Same shape and defaults as RunOutput; the same push worker drains both.
    push_status: str = Field(default=PushStatus.deferred.value, max_length=20, index=True)
    pushed_at: Optional[datetime] = Field(default=None)
    push_attempts: int = Field(default=0)
    push_error: Optional[str] = Field(default=None)
