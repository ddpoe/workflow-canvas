"""Layout — the Tier 0 path primitive.

Every path the project derives comes from here: the project tree under a
root, the database URL, the DVC cache and remote shapes, the container mount
table with host-to-container translation, and the one root-resolution rule.
Documented by ``docs/system/layout.json``; the layering gate
(``tools/check_layering.py``, R4) makes this package the only home for path
fragment literals.

The package is pure and stdlib-only. Its inputs are values — a root, config
values, identifiers — and it never creates a directory, reads a config file,
or imports from ``wfc`` outside itself. Directory creation belongs to the
callers that need the directory; the root resolver's reads of the working
directory and the environment are the unit's one sanctioned impure surface,
and even those are handed in by the caller as values.
"""
from __future__ import annotations

from .dvc import (
    DVC_DIR_NAME,
    default_archive_url,
    dvc_cache_dir,
    dvc_cache_entry,
    dvc_cache_files_dir,
    dvc_config_path,
    dvc_dir,
    remote_path_from_url,
)
from .mounts import (
    CONTAINER_WORKDIR,
    DVC_CACHE_MOUNT,
    WORK_MOUNT,
    Mount,
    bind_spec,
    mount_table,
    translate_host_path,
)
from .root import (
    ROOT_ENV_VAR,
    is_project_root,
    resolve_project_root,
    root_candidates,
)
from .tree import (
    ARTIFACT_STORE_NAME,
    BUILD_DIR_NAME,
    CHECKOUTS_DIR_NAME,
    DATA_DIR_NAME,
    DB_FILENAME,
    ENV_MANIFEST_FILENAME,
    MARKER_FILENAME,
    METHODS_DIR_NAME,
    MODULES_DIR_NAME,
    OUTCOMES_DIR_NAME,
    PIPELINE_DOC_FILENAME,
    PIPELINE_EDITABLE_DOC_FILENAME,
    PIPELINE_RUN_LOGS_DIR_NAME,
    PIPELINES_DIR_NAME,
    RUN_ID_SIDECAR_FILENAME,
    RUN_SENTINEL_FILENAME,
    SAMPLE_READY_SENTINEL,
    SAMPLES_DIR_NAME,
    SENTINELS_DIR_NAME,
    STATE_DIR_NAME,
    WORKSPACE_DIR_NAME,
    artifact_store,
    checkout_dir,
    checkout_stamp,
    checkouts_dir,
    database_url,
    db_path,
    db_relpath,
    env_build_dir,
    env_manifest_path,
    marker_path,
    marker_relpath,
    method_dir,
    method_script_path,
    methods_dir,
    module_dir,
    modules_dir,
    pipeline_doc_path,
    pipeline_editable_doc_path,
    pipeline_outcomes_dir,
    pipeline_run_dir,
    pipeline_run_logs_dir,
    pipelines_dir,
    pipelines_relpath,
    run_archive_dir,
    run_archive_name,
    run_id_sidecar_path,
    run_sentinel_dir,
    run_sentinel_path,
    run_sentinel_relpath,
    sample_dir,
    sample_ready_sentinel,
    sample_ready_sentinel_relpath,
    samples_dir,
    sentinels_dir,
    sentinels_relpath,
    state_dir,
    workspace_dir,
    workspace_target_dir,
)

__all__ = [
    # tree names
    "STATE_DIR_NAME", "MARKER_FILENAME", "DB_FILENAME", "ARTIFACT_STORE_NAME",
    "WORKSPACE_DIR_NAME", "SENTINELS_DIR_NAME", "PIPELINES_DIR_NAME",
    "OUTCOMES_DIR_NAME", "PIPELINE_RUN_LOGS_DIR_NAME", "DATA_DIR_NAME",
    "SAMPLES_DIR_NAME", "SAMPLE_READY_SENTINEL", "METHODS_DIR_NAME",
    "MODULES_DIR_NAME", "ENV_MANIFEST_FILENAME", "BUILD_DIR_NAME",
    "RUN_SENTINEL_FILENAME", "RUN_ID_SIDECAR_FILENAME", "PIPELINE_DOC_FILENAME",
    "PIPELINE_EDITABLE_DOC_FILENAME",
    # tree derivations
    "state_dir", "marker_path", "marker_relpath", "db_path", "db_relpath",
    "database_url", "artifact_store", "CHECKOUTS_DIR_NAME", "checkouts_dir",
    "checkout_dir", "checkout_stamp", "run_archive_name", "run_archive_dir",
    "workspace_dir", "workspace_target_dir", "sentinels_dir", "sentinels_relpath",
    "run_sentinel_dir", "run_sentinel_path", "run_sentinel_relpath",
    "run_id_sidecar_path", "pipelines_dir", "pipelines_relpath",
    "pipeline_run_dir", "pipeline_doc_path", "pipeline_editable_doc_path",
    "pipeline_outcomes_dir", "pipeline_run_logs_dir", "samples_dir",
    "sample_dir", "sample_ready_sentinel", "sample_ready_sentinel_relpath",
    "methods_dir", "method_dir", "method_script_path", "modules_dir",
    "module_dir", "env_manifest_path", "env_build_dir",
    # dvc
    "DVC_DIR_NAME", "dvc_dir", "dvc_config_path", "dvc_cache_dir",
    "dvc_cache_files_dir", "dvc_cache_entry", "remote_path_from_url",
    "default_archive_url",
    # mounts
    "WORK_MOUNT", "DVC_CACHE_MOUNT", "CONTAINER_WORKDIR", "Mount",
    "mount_table", "bind_spec", "translate_host_path",
    # root
    "ROOT_ENV_VAR", "root_candidates", "is_project_root", "resolve_project_root",
]
