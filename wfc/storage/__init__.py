"""Storage & provenance: the content-addressed cache, its DVC transport and the archive.

Every artifact wfc keeps is stored once, under its content hash, in DVC's
cache layout (``.dvc/cache/files/md5/{hash[:2]}/{hash[2:]}``).  The cache is
read and written directly; DVC itself is imported only by the transport, on
first use, to push entries to the configured remote and pull them back.

- ``cache``: write an entry (:func:`cache_file`) and restore one to a
  workspace path (:func:`restore_from_cache`); new entries are read-only.
  A directory is stored as one object per member plus its ``.dir``
  manifest, and read through one read-only :func:`checkout`
  (:func:`local_path` is a file's address or a directory's checkout);
  :func:`entry_is_complete` judges an entry whole, and
  :func:`check_entry_shape` refuses a malformed one
  (:class:`MalformedEntryError`), naming its repair
  (:func:`sample_repair`, :func:`output_repair`).
- ``transport``: :func:`push` (one transfer and one
  :class:`PushOutcome` per entry), :func:`pull` and :func:`pull_cache`
  against the configured remote.  :func:`has_remote_configured` is a pure INI parse
  that never imports DVC.
- ``setup``: validate (:func:`ensure_dvc_ready`) and initialize
  (:func:`init_dvc`) a project's ``[dvc]`` configuration, and check that a
  local remote is reachable (:func:`check_remote_reachable`).
- ``prune``: scan run archives and cache entries, and remove the ones
  nothing references; :func:`cache_prune` is the ``wfc cache prune`` body.
- ``archive``: the archive pass (:func:`archive_outputs`), which hashes and
  caches every un-archived run output; :func:`unarchived_outputs` is the
  one selection of those outputs; :func:`cache_archive` is the
  ``wfc cache archive`` body.
- ``env_blob``: store an environment's content blob and return its
  fingerprint (:func:`store_env_content`), and read one back by that
  fingerprint (:func:`read_env_content`), refusing a malformed hash
  (:class:`InvalidEnvBlobHashError`) or a blob the cache lacks
  (:class:`EnvBlobNotFoundError`).

- ``resolve``: the readers' resolvers, selecting outputs by slot under one
  record-validity rule (:class:`MalformedRecordError`,
  :func:`has_malformed_output_records`).  :func:`output_location` is the
  one answer to where an output's bytes are (``local``, ``remote`` or
  ``missing``), read by Execution's hit rule and by both resolvers.
  :func:`resolve_input` serves a run's consumer (a pre-archive row
  resolves to the run archive, an archived row to its complete cache
  entry, pulled only when recorded pushed); :func:`resolve_output` serves
  a user, and only for archived rows; :func:`provider_outputs` is the Canvas
  provider's local-only slice; :func:`exportable_outputs` is
  ``wfc export --all``'s strict slice; :func:`output_export_name` and
  :func:`output_export_names` name outputs for a user;
  :func:`resolve_run_reference_outputs` is Execution's composer's read of
  the referenced runs' recorded outputs and sample.
- ``restore``: restore a registered sample for a pipeline run
  (:func:`restore_sample`); :func:`sample_data_path` is the one answer to
  where a sample's restored data sits (its project-relative
  ``registered_path`` under the current root), refusing an absolute one
  (:class:`MalformedSampleRecordError`).

- ``push_worker``: the first-push rule (:func:`first_push_status`) and the push
  worker, the ``push-lifecycle`` workflow.
- ``sample_store``: a registered sample's bytes into the cache, with their
  first push (:func:`store_sample_bytes`).
- ``egress``: copy a cache entry out to a user's destination, writable
  (:func:`copy_out`).

The content hash is ``wfc.identity``'s and every path is ``wfc.layout``'s;
this package calls them and composes neither.
"""

from .cache import (
    MalformedEntryError,
    cache_file,
    check_entry_shape,
    checkout,
    entry_is_complete,
    entry_is_local,
    local_path,
    output_repair,
    restore_from_cache,
    sample_repair,
)
from .transport import PushOutcome, has_remote_configured, pull, pull_cache, push
from .setup import (
    DvcNotConfiguredError,
    DvcNotInstalledError,
    check_remote_reachable,
    ensure_dvc_ready,
    init_dvc,
)
from .prune import (
    cache_prune,
    prune_dvc_cache,
    prune_run_archives,
    referenced_content_hashes,
    referenced_run_ids,
    scan_dvc_cache_entries,
    scan_run_archives,
)
from .archive import archive_outputs, cache_archive, unarchived_outputs
from .env_blob import (
    EnvBlobError,
    EnvBlobNotFoundError,
    InvalidEnvBlobHashError,
    read_env_content,
    store_env_content,
)
from .egress import copy_out
from .resolve import (
    OUTPUT_LOCAL,
    OUTPUT_MISSING,
    OUTPUT_REMOTE,
    CheckoutFailedError,
    InputUnavailableError,
    MalformedRecordError,
    NotArchivedError,
    NotInCacheError,
    ResolveOutputError,
    UnknownOutputError,
    UnknownRunError,
    exportable_outputs,
    has_malformed_output_records,
    recorded_output_slots,
    output_export_name,
    output_export_names,
    output_location,
    provider_outputs,
    resolve_input,
    resolve_output,
    resolve_run_reference_outputs,
)
from .restore import MalformedSampleRecordError, restore_sample, sample_data_path
from .push_worker import first_push_status
from .sample_store import StoredSample, store_sample_bytes

__all__ = [
    "OUTPUT_LOCAL",
    "OUTPUT_MISSING",
    "OUTPUT_REMOTE",
    "DvcNotConfiguredError",
    "DvcNotInstalledError",
    "EnvBlobError",
    "EnvBlobNotFoundError",
    "InputUnavailableError",
    "InvalidEnvBlobHashError",
    "MalformedEntryError",
    "MalformedRecordError",
    "MalformedSampleRecordError",
    "NotArchivedError",
    "NotInCacheError",
    "CheckoutFailedError",
    "PushOutcome",
    "ResolveOutputError",
    "StoredSample",
    "UnknownOutputError",
    "UnknownRunError",
    "archive_outputs",
    "cache_archive",
    "cache_file",
    "cache_prune",
    "check_entry_shape",
    "check_remote_reachable",
    "checkout",
    "copy_out",
    "entry_is_complete",
    "entry_is_local",
    "ensure_dvc_ready",
    "exportable_outputs",
    "first_push_status",
    "has_malformed_output_records",
    "recorded_output_slots",
    "has_remote_configured",
    "init_dvc",
    "local_path",
    "output_export_name",
    "output_export_names",
    "output_location",
    "output_repair",
    "provider_outputs",
    "prune_dvc_cache",
    "prune_run_archives",
    "pull",
    "pull_cache",
    "push",
    "read_env_content",
    "referenced_content_hashes",
    "referenced_run_ids",
    "resolve_input",
    "resolve_output",
    "resolve_run_reference_outputs",
    "restore_from_cache",
    "restore_sample",
    "sample_data_path",
    "sample_repair",
    "scan_dvc_cache_entries",
    "scan_run_archives",
    "store_env_content",
    "store_sample_bytes",
    "unarchived_outputs",
]
