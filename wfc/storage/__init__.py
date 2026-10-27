"""Storage & provenance: the content-addressed cache, its DVC transport and the archive.

Every artifact wfc keeps is stored once, under its content hash, in DVC's
cache layout (``.dvc/cache/files/md5/{hash[:2]}/{hash[2:]}``).  The cache is
read and written directly; DVC itself is imported only by the transport, on
first use, to push entries to the configured remote and pull them back.

- ``cache``: write an entry (:func:`cache_file`) and restore one to a
  workspace path (:func:`restore_from_cache`); new entries are read-only.
  Every copy wfc puts beside the cache is a read-only view of an entry
  (:func:`make_view`: a reflink, else a hardlink, else a copy, built beside
  its destination and renamed over it; :func:`remove_view` removes one and
  leaves the cache object protected); :func:`probe_view_tiers` says which
  tier works where (``wfc doctor``'s ``views`` row). The platform calls are
  ``links``'.
  A directory is stored as one object per member plus its ``.dir``
  manifest, and read through one read-only :func:`checkout`
  (:func:`local_path` is a file's address or a directory's checkout);
  :func:`entry_is_complete` judges an entry whole, and
  :func:`check_entry_shape` refuses a malformed one
  (:class:`MalformedEntryError`), naming its repair
  (:func:`sample_repair`, :func:`output_repair`, :func:`blob_repair`).
- ``blobs``: the blob table, cache entries no output or sample row owns
  (a pipeline document, an env fingerprint): :func:`enter_blob` (insert if
  absent, after the bytes are cached) and the push worker's
  compare-and-set reads and writes of the rows' push status.
- ``transport``: :func:`push` (one transfer and one
  :class:`PushOutcome` per entry), :func:`pull` and :func:`pull_cache`
  against the configured remote.  :func:`has_remote_configured` is a pure INI parse
  that never imports DVC.
- ``setup``: validate (:func:`ensure_dvc_ready`) and initialize
  (:func:`init_dvc`) a project's ``[dvc]`` configuration, and check that a
  local remote is reachable (:func:`check_remote_reachable`).
- ``prune``: scan run archives and cache entries, and remove the archives
  nothing references, the cache entries no unpushed record names
  (:func:`unpushed_record_hashes`; the env manifest's hashes come from the
  caller), and the extracted code copies no holder has in use
  (:func:`prune_code_copies`); :func:`cache_prune` is the ``wfc cache
  prune`` body.
- ``in_use``: the in-use records of code copies and sample views. A claim
  (by its token, bound to its run in the claim transaction) or a pipeline
  writes one before it uses what it names (:func:`hold_code_copy`,
  :func:`hold_for_pipeline`, :func:`bind_claim`); the record phase, a
  refused claim and the end of a pipeline release them
  (:func:`release_run`, :func:`release_claim`, :func:`release_pipeline`);
  :func:`remove_if_unheld` is the one transaction that removes a copy or a
  view, and only when no other holder has it.
- ``cache_push``: :func:`cache_push`, the ``wfc cache push`` body: push
  every output, sample and blob awaiting a push now, through the push
  worker's own tick, and report each failure (:class:`CachePushReport`);
  with no remote it refuses (:class:`NoRemoteConfiguredError`).
- ``archive``: the archive pass (:func:`archive_outputs`), which hashes and
  caches every un-archived run output; :func:`unarchived_outputs` is the
  one selection of those outputs; :func:`cache_archive` is the
  ``wfc cache archive`` body.
- ``env_blob``: store an environment's content blob and return its
  fingerprint (:func:`store_env_content`).
- ``integrity``: the one check that an entry is what wfc recorded
  (:func:`check_entry`): the platform's stat fields (:func:`stat_fields`,
  :func:`entry_record`), the machine-local stamps (:func:`read_stamp`,
  :func:`write_stamp`), the ``[integrity] inputs`` setting
  (:func:`inputs_setting`) and the re-hash through Identity; a run
  output's collect record (:func:`record_output`), its check before the
  archive (:func:`check_output`) and its one-way changed mark
  (:func:`mark_changed`).

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
  ``wfc export --all``'s strict slice; :func:`output_listing` lists a run's
  outputs unresolved; :func:`read_entry` is the checked read of an entry no
  output row owns (a sample, a pipeline document, an env content blob),
  pulling one recorded pushed, and :func:`entry_state` its probe;
  :func:`output_export_name` and
  :func:`output_export_names` name outputs for a user. Every refusal a
  reader of an output meets is a :class:`ResolveOutputError`, a malformed
  cache entry included;
  :func:`resolve_run_reference_outputs` is Execution's composer's read of
  the referenced runs' recorded outputs and sample.
- ``restore``: restore a registered sample for a pipeline run
  (:func:`restore_sample`); :func:`sample_data_path` is the one answer to
  where a sample's restored data sits (Layout's sample workspace under the
  current root plus the file name ``registered_path`` records), refusing a
  row that records anything but a bare file name
  (:class:`MalformedSampleRecordError`). Inside a pipeline the restore and
  the check before a step reads (:func:`check_sample`) record the pipeline
  as holding the sample's view; the end of the pipeline removes the views
  no other pipeline holds, with their readiness sentinels
  (:func:`end_pipeline_samples`). :func:`remove_restored_view` removes one
  sample's view (its read-only folder and its stamp), raising when it cannot.

- ``push_worker``: the first-push rule (:func:`first_push_status`) and the push
  worker, the ``push-lifecycle`` workflow.
- ``sample_store``: a registered sample's bytes into the cache, with their
  first push (:func:`store_sample_bytes`).
- ``egress``: copy a cache entry out to a user's destination, writable
  (:func:`copy_out`).
- ``verify``: the ``wfc verify`` body (:func:`verify_project`): re-hash
  every sample, pre-archive output, archived cache entry, checkout and blob,
  list each that is not what wfc recorded by kind (:class:`VerifyReport`),
  and with ``repair`` put back what wfc can restore exactly.

The content hash is ``wfc.identity``'s and every path is ``wfc.layout``'s;
this package calls them and composes neither.
"""

from .archive import archive_outputs, cache_archive, unarchived_outputs
from .blobs import KIND_ENV_FINGERPRINT, KIND_PIPELINE_DOCUMENT, enter_blob
from .cache import (
    MalformedEntryError,
    ViewProbe,
    blob_repair,
    cache_file,
    check_entry_shape,
    checkout,
    entry_is_complete,
    entry_is_local,
    local_path,
    make_view,
    output_repair,
    probe_view_tiers,
    remove_view,
    restore_from_cache,
    sample_repair,
)
from .cache_push import CachePushReport, NoRemoteConfiguredError, PushFailure, cache_push
from .egress import copy_out
from .env_blob import store_env_content
from .in_use import (
    bind_claim,
    held_keys,
    hold_code_copy,
    hold_for_pipeline,
    new_claim_token,
    release_claim,
    release_finished_runs,
    release_pipeline,
    release_run,
    remove_if_unheld,
)
from .integrity import (
    CHANGED,
    INTACT,
    MISSING,
    IntegrityVerdict,
    OutputRecord,
    check_entry,
    check_output,
    entry_record,
    inputs_setting,
    mark_changed,
    read_stamp,
    record_output,
    stat_fields,
    write_stamp,
)
from .prune import (
    cache_prune,
    prune_code_copies,
    prune_dvc_cache,
    prune_run_archives,
    referenced_run_ids,
    scan_dvc_cache_entries,
    scan_run_archives,
    unpushed_record_hashes,
)
from .push_worker import first_push_status
from .resolve import (
    KIND_SAMPLE,
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
    entry_state,
    exportable_outputs,
    has_malformed_output_records,
    output_export_name,
    output_export_names,
    output_listing,
    output_location,
    output_records,
    provider_outputs,
    read_entry,
    recorded_output_slots,
    resolve_input,
    resolve_output,
    resolve_run_reference_outputs,
)
from .restore import (
    SAMPLE_INTACT,
    SAMPLE_REFUSED,
    SAMPLE_REPAIRED,
    SAMPLE_UNCHECKED,
    MalformedSampleRecordError,
    SampleCheck,
    check_sample,
    end_pipeline_samples,
    remove_restored_view,
    restore_sample,
    sample_data_path,
)
from .sample_store import StoredSample, store_sample_bytes
from .setup import (
    DvcNotConfiguredError,
    DvcNotInstalledError,
    check_remote_reachable,
    ensure_dvc_ready,
    init_dvc,
)
from .transport import PushOutcome, has_remote_configured, pull, pull_cache, push
from .verify import VERIFY_KINDS, VerifyProblem, VerifyReport, verify_project

__all__ = [
    "VERIFY_KINDS",
    "VerifyProblem",
    "VerifyReport",
    "verify_project",
    "CHANGED",
    "INTACT",
    "MISSING",
    "IntegrityVerdict",
    "OutputRecord",
    "check_entry",
    "check_output",
    "entry_record",
    "inputs_setting",
    "mark_changed",
    "read_stamp",
    "record_output",
    "stat_fields",
    "write_stamp",
    "KIND_ENV_FINGERPRINT",
    "KIND_PIPELINE_DOCUMENT",
    "KIND_SAMPLE",
    "OUTPUT_LOCAL",
    "OUTPUT_MISSING",
    "OUTPUT_REMOTE",
    "DvcNotConfiguredError",
    "DvcNotInstalledError",
    "InputUnavailableError",
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
    "blob_repair",
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
    "enter_blob",
    "entry_state",
    "exportable_outputs",
    "read_entry",
    "first_push_status",
    "has_malformed_output_records",
    "recorded_output_slots",
    "has_remote_configured",
    "init_dvc",
    "local_path",
    "output_export_name",
    "output_export_names",
    "output_listing",
    "output_location",
    "output_records",
    "output_repair",
    "provider_outputs",
    "prune_code_copies",
    "prune_dvc_cache",
    "prune_run_archives",
    "pull",
    "pull_cache",
    "push",
    "referenced_run_ids",
    "unpushed_record_hashes",
    "CachePushReport",
    "NoRemoteConfiguredError",
    "PushFailure",
    "cache_push",
    "resolve_input",
    "resolve_output",
    "resolve_run_reference_outputs",
    "restore_from_cache",
    "restore_sample",
    "check_sample",
    "end_pipeline_samples",
    "remove_restored_view",
    "SampleCheck",
    "SAMPLE_INTACT",
    "SAMPLE_REFUSED",
    "SAMPLE_REPAIRED",
    "SAMPLE_UNCHECKED",
    "sample_data_path",
    "sample_repair",
    "scan_dvc_cache_entries",
    "scan_run_archives",
    "store_env_content",
    "store_sample_bytes",
    "unarchived_outputs",
    "bind_claim",
    "held_keys",
    "hold_code_copy",
    "hold_for_pipeline",
    "new_claim_token",
    "release_claim",
    "release_finished_runs",
    "release_pipeline",
    "release_run",
    "remove_if_unheld",
    "ViewProbe",
    "make_view",
    "probe_view_tiers",
    "remove_view",
]
