"""The schema: fourteen SQLModel table classes and their enums.

Tables:
  modules, methods, method_versions, tracked_functions, param_defs,
  module_contracts, method_contracts, runs, run_inputs, run_outputs,
  run_annotations, samples, blobs, in_use_records
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Index, Integer, UniqueConstraint, text
from sqlmodel import Column, Field, Relationship, SQLModel

# =============================================================================
# Push-status enum
# =============================================================================

class PushStatus(str, Enum):
    """Status of a RunOutput / Sample / Blob's push to the configured DVC remote.

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


class OutputIntegrity(str, Enum):
    """The one non-NULL value of ``RunOutput.integrity_status``.

    NULL means the output is as its run wrote it (as collected). ``changed``
    means Storage's check found it changed after the run wrote it; the mark
    is one-way, and a changed output counts as missing.
    """
    changed = "changed"


# =============================================================================
# Module & Method hierarchy
# =============================================================================

class Module(SQLModel, table=True):
    """Pipeline stage / domain (e.g. data_preprocessing, data_labeling)."""

    __tablename__ = "modules"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=200)
    description: str | None = None

    # relationships
    methods: list["Method"] = Relationship(back_populates="module")
    contracts: list["ModuleContract"] = Relationship(back_populates="module")


class Method(SQLModel, table=True):
    """Specific analysis approach within a module.

    A method is named within its module: two modules may each hold a method
    of the same name, each with its own snapshot. ``script_path`` is the
    script's path inside the snapshot (e.g. ``filter.py``); Layout derives
    the snapshot directory from the module and method names.
    """

    __tablename__ = "methods"
    __table_args__ = (
        UniqueConstraint("module_id", "name", name="uq_method_module_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    module_id: int = Field(foreign_key="modules.id", index=True)
    name: str = Field(index=True, max_length=200)
    script_path: str | None = Field(default=None, max_length=500)
    # Required typed env spec or named shared env (e.g. "pixi:image-io",
    # "conda:analysis", "container:<name>", "byo", or a bare manifest name).
    # No Python-side default — a method must declare an env (enforced at
    # registration parse, pre_run, and the Graph load). The DB-only
    # ``server_default=''`` exists ONLY so ``ensure_schema`` can additively
    # ADD this NOT-NULL column to a *legacy* methods table that predates it
    # (SQLite needs a constant DEFAULT to backfill existing rows). Backfilled
    # legacy rows get ``''`` — which the run-time guards reject loudly, exactly
    # as a missing env should (it is NOT a silent working default).
    env: str = Field(
        max_length=50,
        sa_column_kwargs={"server_default": text("''")},
    )

    # relationships
    module: Module | None = Relationship(back_populates="methods")
    tracked_functions: list["TrackedFunction"] = Relationship(back_populates="method")
    runs: list["Run"] = Relationship(back_populates="method")
    versions: list["MethodVersion"] = Relationship(back_populates="method")


class MethodVersion(SQLModel, table=True):
    """One entry of a method's version list, as a row runs can point at.

    The version list (``<method>.versions.json`` beside the snapshot,
    committed with it) is authoritative; a row mirrors one entry: its
    ``label`` (v1, v2, ...), code fingerprint, env name, env fingerprint,
    image reference, snapshot tree hash and registration time
    (``recorded_at``). Rows are written only by Registration: by
    registration after its commit, and by the sync that creates a row for
    an entry that arrived another way (``git pull``) and moves labels after
    a renumbering. A row is matched to its entry by content identity and
    registration time, never by label, so ``Run.version_id`` keeps pointing
    at the same entry. Two labels may share a code fingerprint (an earlier
    version's content registered again), so the fingerprint is indexed, not
    unique; the label is unique per method. A row with no label (written
    before version lists, or whose entry left the list) is kept for run
    history and is never a version a node resolves. ``git_commit`` is the
    registration commit that wrote the entry, when this database saw it; it
    is not part of any identity or cache key.
    """

    __tablename__ = "method_versions"
    __table_args__ = (
        UniqueConstraint("method_id", "label", name="uq_method_version_label"),
        Index("ix_method_versions_method_code", "method_id", "code_fingerprint"),
    )

    id: int | None = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    code_fingerprint: str = Field(max_length=64)
    git_commit: str | None = Field(default=None, max_length=64)
    recorded_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    label: int | None = Field(default=None)
    env_name: str | None = Field(default=None, max_length=200)
    env_fingerprint: str | None = Field(default=None, max_length=64)
    image_ref: str | None = Field(default=None, max_length=500)
    tree_hash: str | None = Field(default=None, max_length=64)

    # relationships
    method: Method | None = Relationship(back_populates="versions")


# =============================================================================
# Parameter schema
# =============================================================================

class TrackedFunction(SQLModel, table=True):
    """A function in a method whose parameters are tracked."""

    __tablename__ = "tracked_functions"

    id: int | None = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    function_name: str = Field(max_length=200)
    ordinal: int = Field(default=0)

    # relationships
    method: Method | None = Relationship(back_populates="tracked_functions")
    param_defs: list["ParamDef"] = Relationship(back_populates="tracked_function")


class ParamDef(SQLModel, table=True):
    """Parameter definition for a tracked function."""

    __tablename__ = "param_defs"

    id: int | None = Field(default=None, primary_key=True)
    tracked_function_id: int = Field(foreign_key="tracked_functions.id", index=True)
    param_name: str = Field(max_length=200)
    param_type: str | None = Field(default=None, max_length=100)
    default_value: str | None = None

    # relationships
    tracked_function: TrackedFunction | None = Relationship(back_populates="param_defs")


# =============================================================================
# Run tracking
# =============================================================================

#: The rows the running-claim index covers: a claim that computes (not a
#: cache-hit audit row), still running, and carrying the claim heartbeat. A
#: failed, completed or cancelled row, and a row with no heartbeat (written
#: before the heartbeat existed, or by the hand-run verb), is outside it, so
#: a key may hold a retried failure and an outputs-missing rerun.
RUNNING_CLAIM_PREDICATE = (
    "cache_source_run_id IS NULL AND status = 'running' "
    "AND heartbeat IS NOT NULL"
)


#: A cancelled row's reason: the user stopped the pipeline.
CANCEL_REASON_USER = "user"
#: A cancelled row's reason: an upstream run it needed failed.
CANCEL_REASON_UPSTREAM_FAILED = "upstream_failed"
#: A cancelled row's reason: the pipeline ended before it started.
CANCEL_REASON_NOT_RUN = "not_run"
#: A cancelled row's reason: the server stopped before the target started.
CANCEL_REASON_INTERRUPTED = "interrupted"
#: A cancelled row's reason: the engine failed before the target started.
CANCEL_REASON_ENGINE_FAILED = "engine_failed"
#: Every value ``Run.cancel_reason`` takes.
CANCEL_REASONS = (CANCEL_REASON_USER, CANCEL_REASON_UPSTREAM_FAILED,
                  CANCEL_REASON_NOT_RUN, CANCEL_REASON_INTERRUPTED,
                  CANCEL_REASON_ENGINE_FAILED)
#: The engine-side reasons: wfc could not run the target, so a pipeline in
#: which nothing completed reads failed, not cancelled.
ENGINE_SIDE_CANCEL_REASONS = (CANCEL_REASON_INTERRUPTED,
                              CANCEL_REASON_ENGINE_FAILED)


class Run(SQLModel, table=True):
    """A single execution of a method with specific parameters.

    At most one running claim computes per method, sample and cache key: the
    partial unique index ``uq_runs_running_claim`` refuses a second row under
    :data:`RUNNING_CLAIM_PREDICATE`. A claim that loses the insert waits on
    the winner. SQLite and PostgreSQL both treat a NULL in the key as
    distinct, so a keyless row never meets the index.
    """

    __tablename__ = "runs"
    __table_args__ = (
        Index(
            "uq_runs_running_claim", "method_id", "sample", "cache_key",
            unique=True,
            sqlite_where=text(RUNNING_CLAIM_PREDICATE),
            postgresql_where=text(RUNNING_CLAIM_PREDICATE),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    params: dict | None = Field(default=None, sa_column=Column(JSON))
    sample: str | None = Field(default=None, max_length=200, index=True)
    status: str = Field(default="running", max_length=50)
    pipeline_id: str | None = Field(default=None, max_length=200, index=True)
    nf_process_name: str | None = Field(default=None, max_length=200)
    # started_at is nullable: cancelled rows (written post-hoc at pipeline-end
    # by ``_write_cancelled_rows``) never executed and carry ``started_at=None``.
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = None
    metrics: dict | None = Field(default=None, sa_column=Column(JSON))
    # Versioning & caching
    version_id: int | None = Field(default=None, foreign_key="method_versions.id", index=True)
    cache_key: str | None = Field(default=None, max_length=64, index=True)
    cache_source_run_id: int | None = Field(default=None, foreign_key="runs.id")
    # env_fingerprint: 32-char MD5 hex of the cached env content blob that
    # identifies what the env's image contains (its layers, environment
    # variables, user and working directory), resolved at pre_run time.
    # Folded into the cache key so env drift invalidates cache.  Nullable for
    # backward compat with pre-env-fp runs.
    env_fingerprint: str | None = Field(default=None, max_length=32)
    # What the run ran under, recorded by the claim in the row's own INSERT
    # (fresh and cache-hit rows alike) and never part of the cache key:
    # git_commit is the project commit the claim ran at (or the one passed
    # with --git-commit); env_name is the env of the method version actually
    # claimed; wfc_version is the installed wfc version. NULL on rows written
    # without a claim (refused, cancelled, register_run) and on older rows.
    git_commit: str | None = Field(default=None, max_length=64)
    env_name: str | None = Field(default=None, max_length=200)
    wfc_version: str | None = Field(default=None, max_length=100)
    # The hash (canonical JSON md5) of the pipeline document the run came
    # from; the document is in the cache under it before any row names it.
    # Set on every row of a launched pipeline (fresh, cached, refused,
    # cancelled) and on a run-step given --pipeline-json. Not in the key.
    pipeline_doc_hash: str | None = Field(default=None, max_length=36)
    # key_recipe: the version of the key recipe (``wfc.identity.KEY_RECIPE``)
    # that composed ``cache_key``; stamped by the claim on keyed rows only.
    # The SQL ``DEFAULT 1`` exists for ``ensure_schema``: rows already in a
    # database that predates the column were keyed under recipe 1, and the
    # ADD COLUMN backfills them to 1 without rewriting them. ``evaluates_none``
    # makes the ORM write an explicit NULL for a keyless row instead of
    # leaving the column to that default.
    key_recipe: int | None = Field(
        default=None,
        sa_column=Column(
            Integer().evaluates_none(), nullable=True, server_default=text("1"),
        ),
    )
    # cache_source_run_id is set when a run reused a previous run's outputs;
    # the run still appears in the audit log, it just did not re-execute.
    # Error persistence
    error_message: str | None = Field(default=None)
    error_traceback: str | None = Field(default=None)
    # Causality link for cancelled rows: points at the nearest failed ancestor
    # in the frozen pipeline DAG. Populated only for ``status='cancelled'``
    # rows written post-hoc by ``_write_cancelled_rows`` at pipeline-end.
    # NULL on executed rows and on a cancelled row with no failed ancestor.
    cancelled_due_to_run_id: int | None = Field(
        default=None, foreign_key="runs.id", index=True
    )
    # Why a cancelled row was cancelled: one of CANCEL_REASONS. NULL on rows
    # that are not cancelled. ``ensure_schema`` gives the cancelled rows of a
    # database that predates the column their reason once, when it adds it.
    cancel_reason: str | None = Field(default=None, max_length=50)
    # The claim heartbeat: a counter the process holding a running claim
    # advances at a fixed interval until its record phase. A waiting claim
    # reads it only for movement, timed by its own monotonic clock; it is
    # never a time and never compared with one. NULL on rows no claim holds
    # (cache-hit audit rows, register_run, cancelled rows) and on rows
    # written before this column; such a row is outside the running-claim
    # index. Not part of any cache key or fingerprint.
    heartbeat: int | None = Field(default=None)
    # What the run invoked, written by dispatch just before the launch (so a
    # run whose method then fails keeps it): the container-side launch as it
    # was handed to Docker -- image reference, interpreter, script path,
    # mounts as (container path, role, read-only), the WFC_* values as
    # forwarded, user, gpus, working directory and wfc version. No host path.
    # NULL on rows dispatch launched nothing for (cache-hit, refused,
    # cancelled, register_run) and on rows written before this column. Not
    # part of any cache key or fingerprint.
    invocation: dict | None = Field(default=None, sa_column=Column(JSON))
    # NID: custom run label from canvas node (nullable = auto-version)
    nid: str | None = Field(default=None, max_length=200)
    # The pipeline node the run executed, by the node's raw document id (a
    # standalone run-step records its --node-id as given). NULL for runs no
    # node wrote, e.g. register_run. Not indexed: readers select by
    # pipeline_id and match node_id in memory.
    node_id: str | None = Field(default=None, max_length=200)

    # relationships
    method: Method | None = Relationship(back_populates="runs")
    inputs: list["RunInput"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"foreign_keys": "[RunInput.run_id]"},
    )
    outputs: list["RunOutput"] = Relationship(back_populates="run")
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
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # When set, the run is soft-deleted: hidden from default views but still
    # in the DB. Hard delete is only permitted after archiving. NULL = live.
    # Timestamp (not bool) so the UI can show "archived 3d ago" and future
    # auto-retention policies can act on age.
    archived_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    run: Run | None = Relationship(back_populates="annotation")


class ModuleContract(SQLModel, table=True):
    """Contract that all methods in a module must satisfy.

    Two contract types:
      - 'output': a required file artifact (e.g. 'feature_set_list' → '.parquet')
      - 'metric': a required metric key (e.g. 'n_features' → 'int')
    """

    __tablename__ = "module_contracts"

    id: int | None = Field(default=None, primary_key=True)
    module_id: int = Field(foreign_key="modules.id", index=True)
    contract_type: str = Field(max_length=50)       # 'output' or 'metric'
    name: str = Field(max_length=200)               # artifact name or metric name
    value_type: str | None = Field(default=None, max_length=100)  # '.parquet', 'int', etc.
    required: bool = Field(default=True)

    # relationships
    module: Module | None = Relationship(back_populates="contracts")

    class Config:
        """Model configuration naming the ``module_contracts`` table."""

        table_name = "module_contracts"


class RunInput(SQLModel, table=True):
    """One input a run read: an upstream run's output, or a registered sample.

    A parent row carries ``source_run_id`` (and ``source_slot`` when the
    entry named the output). A sample row carries ``sample_name`` and
    ``content_hash`` and leaves ``source_run_id`` NULL. On both,
    ``input_name`` is the input slot the value was read into.
    """

    __tablename__ = "run_inputs"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    source_run_id: int | None = Field(default=None, foreign_key="runs.id")
    input_name: str | None = Field(default=None, max_length=200)
    # The output slot of the upstream run that fed ``input_name``, taken from
    # the parent entry (``input:output:run``). NULL when the entry named no
    # output (the two-part ``input:run`` and bare ``run`` forms).
    source_slot: str | None = Field(default=None, max_length=200)
    artifact_path: str | None = Field(default=None, max_length=500)
    # On a sample row: the registered sample the run read into
    # ``input_name``, and that sample's content hash at claim time -- the
    # same hash the run's cache key is over. NULL on a parent row.
    sample_name: str | None = Field(default=None, max_length=200)
    content_hash: str | None = Field(default=None, max_length=36)

    # relationships
    run: Run | None = Relationship(
        back_populates="inputs",
        sa_relationship_kwargs={"foreign_keys": "[RunInput.run_id]"},
    )


class RunOutput(SQLModel, table=True):
    """Artifact produced by a run."""

    __tablename__ = "run_outputs"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    # The contract output slot this record fills. Every output is found by
    # its slot; a run with an output record that lacks one has a malformed
    # record. One record per (run_id, slot), kept by the collect phase's
    # insert-or-update rather than a database constraint.
    slot: str | None = Field(default=None, max_length=200)
    # The file name the method saved the output under, for display.
    output_name: str | None = Field(default=None, max_length=200)
    artifact_path: str | None = Field(default=None, max_length=500)
    # artifact_type has no default — must be passed explicitly at every write site.
    # Valid values: "module_file", "method_file", "module_directory", "method_directory".
    # Only "module_file" rows participate in wfc.identity.build_input_fingerprint().
    # "_directory" variants are reserved enum values; directory handling not yet implemented.
    artifact_type: str = Field(max_length=100)
    # Size and mtime captured at write time (from the file on disk).
    # mtime is read from the source file before any copy so calendar-day variation
    # in copy timestamps doesn't poison fingerprints for re-registered identical content.
    file_size: int | None = None
    file_mtime: float | None = None
    # Content-addressed hash (md5 hex; a directory's is its manifest's md5
    # plus ``.dir``).  NULL until the post-pipeline archive pass (or
    # ``wfc cache archive``) hashes the output, and on rows from runs
    # recorded before DVC integration.
    content_hash: str | None = Field(default=None, max_length=36)

    # Collect's stat record of the output, written by the collect phase after
    # it makes the output read-only (a chmod moves ctime), and read only by
    # Storage's integrity check. For a file: its st_mtime_ns plus its change
    # time (st_ctime_ns on Linux and macOS, NTFS's ChangeTime on Windows; NULL
    # on a volume that keeps none) and, on Windows, its file index (st_ino;
    # text, since an index can exceed SQLite's signed 64-bit integer). For a directory: its newest
    # member's st_mtime_ns and its member count (each member's fields are in
    # Layout's member stamp). ``file_size`` above is the size or total size.
    stat_mtime_ns: int | None = None
    stat_ctime_ns: int | None = None
    stat_file_index: str | None = Field(default=None, max_length=40)
    member_count: int | None = None
    # A file's extended-attribute names, sorted, read with its stat fields
    # (a directory's members' are in the member stamp). NULL where the read
    # failed, for a directory, and on rows collected before names were
    # recorded: the integrity check then compares the other fields alone.
    stat_xattr_names: list[str] | None = Field(default=None, sa_column=Column(JSON))
    # NULL: as collected. ``changed`` (OutputIntegrity): Storage's check found
    # the output changed after the run wrote it; written once, compare-and-set.
    integrity_status: str | None = Field(default=None, max_length=20)
    # The output's hash taken at collect, only under ``[integrity] inputs =
    # "hash"``. Never read in place of ``content_hash``, whose NULL marks an
    # un-archived output: only Storage's integrity check and the archive pass
    # read it, and it is never a cache address, a push or a prune key.
    collect_hash: str | None = Field(default=None, max_length=36)

    # Async push-to-remote bookkeeping. ``push_status`` defaults to
    # ``deferred`` (the no-remote-configured terminal). The CLI flips it to
    # ``pending`` at row-insert time when a remote is configured; the push
    # worker drives the lifecycle from there.
    push_status: str = Field(default=PushStatus.deferred.value, max_length=20, index=True)
    pushed_at: datetime | None = Field(default=None)
    push_attempts: int = Field(default=0)
    push_error: str | None = Field(default=None)

    # relationships
    run: Run | None = Relationship(back_populates="outputs")


# =============================================================================
# Method contracts (method.yaml)
# =============================================================================

class MethodContract(SQLModel, table=True):
    """One method version's named slot contract, parsed from its ``method.yaml``.

    Captures input slots, output slots (with types and filenames), params
    schema and executor hint. Keyed by ``version_id``: each version keeps
    the contract it was registered with, and readers ask Registration for
    a version's contract (the newest when unpinned). ``method_id`` stays as
    a lookup column; a row with no ``version_id`` was written before
    contracts were per version and is never read as one.
    """

    __tablename__ = "method_contracts"

    id: int | None = Field(default=None, primary_key=True)
    method_id: int = Field(foreign_key="methods.id", index=True)
    version_id: int | None = Field(
        default=None, foreign_key="method_versions.id", unique=True,
    )
    input_slots: dict | None = Field(default=None, sa_column=Column(JSON))
    # {slot_name: {type, required, multiple, description}}
    output_slots: dict | None = Field(default=None, sa_column=Column(JSON))
    # {slot_name: {type, required, description}}
    params_schema: dict | None = Field(default=None, sa_column=Column(JSON))
    # {param_name: {type, required, default, description}}
    executor: str = Field(default="python", max_length=50)
    # "python" | "nextflow"  (forward-looking: NF shim generation)


# =============================================================================
# Sample registry
# =============================================================================

class Sample(SQLModel, table=True):
    """A registered data sample managed by wfc.

    A registered sample is a file or a directory whose bytes live in the
    content-addressed cache under ``content_hash``.  ``registered_path``
    records only the file (or directory) name; Layout derives where a run's
    restore materializes the sample from it,
    ``.wfc/workspace/samples/{name}/{file name}``.  A ``registered_path``
    that is not a bare file name (empty, absolute, or with a directory
    component) is a malformed record and is refused, naming
    re-registration.
    """

    __tablename__ = "samples"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=200)
    source_path: str = Field(max_length=500)
    registered_path: str = Field(max_length=500)
    file_type: str = Field(max_length=50)
    registered_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    # Size and mtime captured from the source file *before* the copy.
    # Post-copy timestamps vary by calendar day and would produce different mtimes
    # for identical content re-registered later, poisoning cache fingerprints.
    # For a directory: the sum of its files' sizes and the newest file's mtime.
    file_size: int | None = None
    file_mtime: float | None = None
    # Number of files in a directory sample; NULL for a file sample.
    file_count: int | None = None
    # Free-text description from the registration manifest (or the canvas
    # form). Metadata only: never part of the content hash or a cache key.
    description: str | None = None
    # "copy" = wfc owns the file (default); "link" = wfc records path only (deferred).
    registration_mode: str = Field(default="copy", max_length=20)
    # DVC content hash (MD5). Always set by register_sample, which is the
    # only writer: a sample's identity IS its content, and the cache is
    # addressed by this hash. Optional only because rows predating DVC
    # integration left it NULL — such a row is a malformed record and is
    # refused loudly (load_sample_hashes, restore_sample), never degraded
    # around, so it is re-registered rather than migrated.
    content_hash: str | None = Field(default=None, max_length=36)

    # Async push-to-remote bookkeeping for sample registrations.
    # Same shape and defaults as RunOutput; the same push worker drains both.
    push_status: str = Field(default=PushStatus.deferred.value, max_length=20, index=True)
    pushed_at: datetime | None = Field(default=None)
    push_attempts: int = Field(default=0)
    push_error: str | None = Field(default=None)


# =============================================================================
# Content blobs
# =============================================================================

class Blob(SQLModel, table=True):
    """A content-addressed cache entry that no output or sample row owns.

    One row per hash, entered (insert if absent) when Storage stores the
    bytes, so the row only ever names bytes already in the cache. ``kind``
    says what the bytes are and so what recreates them: this release writes
    ``pipeline-document`` and ``env-fingerprint``; the column is open-ended
    text, not an enum. The push bookkeeping has the same shape and defaults
    as RunOutput's and Sample's, and the same push worker drives it; every
    status change is a compare-and-set on ``push_status``, so several
    writers never overwrite each other. ``stored_at`` is when the row was
    entered, which gates the orphan reset of a recent failed push.
    """

    __tablename__ = "blobs"

    # md5 hex; a directory's is its manifest's md5 plus ``.dir``.
    hash: str = Field(primary_key=True, max_length=36)
    kind: str = Field(max_length=50)
    push_status: str = Field(default=PushStatus.deferred.value, max_length=20, index=True)
    push_attempts: int = Field(default=0)
    push_error: str | None = Field(default=None)
    pushed_at: datetime | None = Field(default=None)
    stored_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    # Size in bytes of the stored content, when the storer knew it.
    size: int | None = None


# =============================================================================
# In-use records
# =============================================================================

class InUseKind(str, Enum):
    """What an :class:`InUseRecord` holds in use.

    ``code-copy`` is a version's extracted code copy, keyed by its snapshot
    tree hash. ``sample-view`` is a restored sample's view in the workspace,
    keyed by the sample's name.
    """
    code_copy = "code-copy"
    sample_view = "sample-view"


class InUseRecord(SQLModel, table=True):
    """One holder's claim on a code copy or a sample view.

    Storage owns these rows: it writes a record before the holder makes,
    checks or reuses what it names, releases it when the holder is done, and
    removes a copy or a view only inside the one write transaction that
    finds no other record for it. ``kind`` (:class:`InUseKind`) and ``key``
    name what is in use. The holder is a claim, by the token the claim drew
    before its run row existed (``run_id`` is set once the claim binds the
    record to its run), or a pipeline, by ``pipeline_id``. There is no time
    column: a record never expires by age.
    """

    __tablename__ = "in_use_records"
    __table_args__ = (
        Index("ix_in_use_records_kind_key", "kind", "key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(max_length=20)
    # A code copy's snapshot tree hash, or a sample's name.
    key: str = Field(max_length=200)
    claim_token: str | None = Field(default=None, max_length=64, index=True)
    run_id: int | None = Field(default=None, foreign_key="runs.id", index=True)
    pipeline_id: str | None = Field(default=None, max_length=200, index=True)
