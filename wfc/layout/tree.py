"""The project tree: every directory and file name derived from a root.

Catalog rows 1 and 3-11 of ``docs/system/layout.json``. Each function takes
a root (or a root plus identifiers) as a plain value and returns the path;
nothing here creates what it names. The ``*_relpath`` forms render the same
shapes as root-relative POSIX strings for generated text — the Snakefile —
where the root is the engine's working directory rather than a value.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

# --- names -----------------------------------------------------------------

#: The state dir: config marker, database, env manifest, build contexts.
STATE_DIR_NAME = ".wfc"
#: The config marker inside the state dir; its presence defines a project root.
MARKER_FILENAME = "wf-canvas.toml"
#: The SQLite database inside the state dir.
DB_FILENAME = "wfc.db"
#: The artifact store inside the state dir: run archives, run sentinels,
#: pipeline run dirs. Ignored by git.
ARTIFACT_STORE_NAME = "runs"
#: The legacy scratch tree under the artifact store (vestigial), and the
#: name of the restore workspace inside the state dir (ignored by git).
WORKSPACE_DIR_NAME = "workspace"
#: Run sentinels under the artifact store.
SENTINELS_DIR_NAME = "sentinels"
#: Per-pipeline run dirs under the artifact store.
PIPELINES_DIR_NAME = "pipelines"
#: The outcome-sidecar dir inside a pipeline run dir.
OUTCOMES_DIR_NAME = "outcomes"
#: The per-run log dir inside a pipeline run dir.
PIPELINE_RUN_LOGS_DIR_NAME = "runs"
#: The frozen pipeline document inside a pipeline run dir.
PIPELINE_DOC_FILENAME = "pipeline.json"
#: The editable sidecar beside the frozen document.
PIPELINE_EDITABLE_DOC_FILENAME = "pipeline.editable.json"
#: The pipeline record inside a pipeline run dir: while it exists the
#: pipeline is running (its owner, its engine, its cancel flag).
PIPELINE_RECORD_FILENAME = "pipeline_record.json"
#: The run-completion sentinel Snakemake waits on.
RUN_SENTINEL_FILENAME = ".complete"
#: The run-id lineage sidecar beside the sentinel.
RUN_ID_SIDECAR_FILENAME = "run_id.txt"
#: The sample workspace beneath the restore workspace, where a run's restore
#: puts each registered sample (also the samples dir of the demo's assets).
SAMPLES_DIR_NAME = "samples"
#: The sample-restore sentinel beside a sample's data.
SAMPLE_READY_SENTINEL = ".sample_ready"
#: The registry inside the state dir: what registration copied. Tracked by git.
REGISTRY_DIR_NAME = "registry"
#: Method snapshots beneath the registry (also the methods dir of the demo's
#: assets).
METHODS_DIR_NAME = "methods"
#: The user's own module directory name ``register-module`` looks for when
#: no module dir is given; wfc never creates it.
MODULES_DIR_NAME = "modules"
#: The env manifest inside the state dir and the build contexts beneath it.
ENV_MANIFEST_FILENAME = "envs.json"
BUILD_DIR_NAME = "build"
#: Each env image's captures folder lives beneath this one, by env and env
#: fingerprint. Tracked by git.
CAPTURES_DIR_NAME = "captures"
#: The files of a captures folder: the package list (the bytes the record's
#: ``source_fingerprint`` is the md5 of), the rendered Dockerfile, the
#: image's facts, and the host's original capture for a solved env.
CAPTURE_PACKAGES_FILENAME = "packages.lock"
CAPTURE_DOCKERFILE_FILENAME = "Dockerfile"
CAPTURE_IMAGE_FILENAME = "image.json"
#: The push record of a captures folder: the registry digest, the tag and
#: the time of the push that put the image in the project's registry. Added
#: to an existing folder after a push succeeds; written once.
CAPTURE_PUSHED_FILENAME = "pushed.json"
CAPTURE_HOST_DIR_NAME = "host"
#: The files of a solved env's ``host/`` folder: the host's explicit list, its
#: ``conda env export --from-history`` spec, the pinned spec that was solved,
#: the host's full pip freeze, and the host's platform, conda version and
#: capture time.
CAPTURE_HOST_EXPLICIT_LIST_FILENAME = "explicit-list.txt"
CAPTURE_HOST_FROM_HISTORY_FILENAME = "from-history.yml"
CAPTURE_HOST_PINNED_SPEC_FILENAME = "pinned-spec.yml"
CAPTURE_HOST_PIP_FREEZE_FILENAME = "pip-freeze.txt"
CAPTURE_HOST_INFO_FILENAME = "host.json"
#: The project's git attributes file, and the line in it that keeps every
#: captures file byte-exact in git (no line-ending conversion on checkout).
GITATTRIBUTES_FILENAME = ".gitattributes"
CAPTURES_GITATTRIBUTES_LINE = f"{STATE_DIR_NAME}/{CAPTURES_DIR_NAME}/** -text"
#: The line that keeps every registry file byte-exact in git: no line-ending
#: conversion when registration stages a snapshot or a run extracts it.
REGISTRY_GITATTRIBUTES_LINE = f"{STATE_DIR_NAME}/{REGISTRY_DIR_NAME}/** -text"


# --- row 1: state dir and marker; row 2: database URL ----------------------

def state_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/``."""
    return Path(root) / STATE_DIR_NAME


def marker_path(root: Path) -> Path:
    """Return the config marker ``<root>/.wfc/wf-canvas.toml``."""
    return state_dir(root) / MARKER_FILENAME


def marker_relpath() -> str:
    """Return the marker as a root-relative POSIX string, ``.wfc/wf-canvas.toml``."""
    return str(PurePosixPath(STATE_DIR_NAME) / MARKER_FILENAME)


def db_path(root: Path) -> Path:
    """Return the database file ``<root>/.wfc/wfc.db``."""
    return state_dir(root) / DB_FILENAME


def db_relpath() -> str:
    """Return the database file as a root-relative POSIX string, ``.wfc/wfc.db``."""
    return str(PurePosixPath(STATE_DIR_NAME) / DB_FILENAME)


def database_url(root: Path) -> str:
    """Return the SQLAlchemy URL ``sqlite:///<root>/.wfc/wfc.db``.

    The path is interpolated in the platform's native form (backslashes on
    Windows).
    """
    return f"sqlite:///{db_path(root)}"


# --- rows 3-4: artifact store and run archive ------------------------------

def artifact_store(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/``."""
    return state_dir(root) / ARTIFACT_STORE_NAME


def artifact_store_relpath() -> str:
    """Return the artifact store as a root-relative POSIX string, ``.wfc/runs``."""
    return str(PurePosixPath(STATE_DIR_NAME) / ARTIFACT_STORE_NAME)


def restore_workspace_dir(root: Path) -> Path:
    """Return the restore workspace ``<root>/.wfc/workspace/``.

    The ignored tree where wfc materializes data for a run (registered
    samples are restored beneath it). Distinct from the vestigial
    ``workspace_dir`` scratch tree under the artifact store.
    """
    return state_dir(root) / WORKSPACE_DIR_NAME


def restore_workspace_relpath() -> str:
    """Return the restore workspace as a root-relative POSIX string, ``.wfc/workspace``."""
    return str(PurePosixPath(STATE_DIR_NAME) / WORKSPACE_DIR_NAME)


def run_archive_name(run_id: int) -> str:
    """Return a run's archive directory name: the id zero-padded to 8."""
    return f"{run_id:08d}"


def run_archive_dir(root: Path, run_id: int) -> Path:
    """Return the run archive ``<root>/.wfc/runs/<run-id zero-padded to 8>/``."""
    return artifact_store(root) / run_archive_name(run_id)


# --- directory-entry checkouts ---------------------------------------------

#: Checkouts of directory cache entries, under the artifact store.
CHECKOUTS_DIR_NAME = "checkouts"


def checkouts_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/checkouts/``, the home of directory-entry checkouts."""
    return artifact_store(root) / CHECKOUTS_DIR_NAME


def checkout_dir(root: Path, dir_hash: str) -> Path:
    """Return the checkout of a directory cache entry, ``<root>/.wfc/runs/checkouts/<hash>/``.

    A directory's cache entry is a manifest plus one object per file, not a
    tree, so a reader reads a checkout: a real directory built from the
    entry. It lies under the project root, which a container run mounts,
    and outside the DVC cache, whose address space holds only DVC objects.

    Args:
        root: The project root.
        dir_hash: The directory's ``.dir`` content hash.

    Returns:
        The checkout directory's path.
    """
    return checkouts_dir(root) / dir_hash


#: Suffix of the stamp a verified read-only checkout carries beside it.
CHECKOUT_STAMP_SUFFIX = ".verified"


def checkout_stamp(root: Path, dir_hash: str) -> Path:
    """Return the stamp of a directory checkout, ``<root>/.wfc/runs/checkouts/<hash>.verified``.

    The stamp sits beside the checkout, not inside it, so the checkout holds
    exactly the directory's files.

    Args:
        root: The project root.
        dir_hash: The directory's ``.dir`` content hash.

    Returns:
        The stamp file's path.
    """
    return checkouts_dir(root) / f"{dir_hash}{CHECKOUT_STAMP_SUFFIX}"


# --- views of cache entries --------------------------------------------------

#: Suffix of a view Storage is building before it renames it into place.
VIEW_TEMP_SUFFIX = ".view"
#: Name prefix of the scratch files the link-tier probe writes and removes.
VIEW_PROBE_PREFIX = ".wfc-view-probe"


def view_temp_path(directory: Path, dest_name: str, token: str) -> Path:
    """Return where a view is built before it is renamed over its destination.

    ``<directory>/.<dest_name>.<token>.view``: in the directory the caller
    names (the destination's own, or the one holding a checkout, so a
    checkout never holds a half-built member), on the destination's
    filesystem so the rename is a rename. The leading dot keeps it out of
    a glob over the destination's siblings.

    Args:
        directory: The directory the view is built in.
        dest_name: The destination's file name.
        token: What makes the name the caller's own (its process and thread).

    Returns:
        The temporary path (which may not exist yet).
    """
    return directory / f".{dest_name}.{token}{VIEW_TEMP_SUFFIX}"


def view_probe_path(directory: Path, token: str) -> Path:
    """Return a scratch file of the link-tier probe, ``<directory>/.wfc-view-probe.<token>``.

    Args:
        directory: The directory probed (the cache's, a workspace's, the
            checkouts').
        token: What makes the name the probe's own.

    Returns:
        The scratch file's path (which may not exist yet).
    """
    return directory / f"{VIEW_PROBE_PREFIX}.{token}"


# --- integrity stamps --------------------------------------------------------

#: Machine-local integrity stamps, under the artifact store: what a verified
#: read last saw of a cache object, a restored sample or a directory output.
STAMPS_DIR_NAME = "stamps"
#: The stamps of cache objects, of restored samples, and of directory
#: outputs' member lists, each in its own dir under the stamps root.
OBJECT_STAMPS_DIR_NAME = "objects"
SAMPLE_STAMPS_DIR_NAME = "samples"
OUTPUT_STAMPS_DIR_NAME = "outputs"
#: Suffix of a cache object's or a restored sample's stamp.
INTEGRITY_STAMP_SUFFIX = ".verified"
#: Suffix of a directory output's member stamp.
MEMBERS_STAMP_SUFFIX = ".members.json"


def stamps_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/stamps/``, the home of integrity stamps.

    Stamps sit outside every entry they describe, so a cache object, a
    restored sample and a run's outputs hold exactly their own bytes.
    """
    return artifact_store(root) / STAMPS_DIR_NAME


def cache_object_stamp(root: Path, md5: str) -> Path:
    """Return a cache object's stamp, ``<root>/.wfc/runs/stamps/objects/<md5>.verified``.

    One per object: a file entry, a directory entry's ``.dir`` manifest or
    one of its members.

    Args:
        root: The project root.
        md5: The object's content hash (a directory's with its ``.dir``).

    Returns:
        The stamp file's path.
    """
    return stamps_dir(root) / OBJECT_STAMPS_DIR_NAME / f"{md5}{INTEGRITY_STAMP_SUFFIX}"


def sample_stamp(root: Path, sample: str) -> Path:
    """Return a restored sample's stamp, ``<root>/.wfc/runs/stamps/samples/<name>.verified``.

    It lies outside the sample workspace, so the restored data and its
    ready sentinel are all the sample dir holds.

    Args:
        root: The project root.
        sample: The sample's name.

    Returns:
        The stamp file's path.
    """
    return stamps_dir(root) / SAMPLE_STAMPS_DIR_NAME / f"{sample}{INTEGRITY_STAMP_SUFFIX}"


def output_stamps_dir(root: Path, run_id: int) -> Path:
    """Return one run's member stamps, ``<root>/.wfc/runs/stamps/outputs/<run-id zero-padded to 8>/``.

    It lies outside the run archive, so no output path holds a stamp; it
    is removed with the run archive.

    Args:
        root: The project root.
        run_id: The run's id.

    Returns:
        The directory's path (which may not exist).
    """
    return stamps_dir(root) / OUTPUT_STAMPS_DIR_NAME / run_archive_name(run_id)


def output_members_stamp(root: Path, run_id: int, slot: str) -> Path:
    """Return a directory output's member stamp, ``.../stamps/outputs/<run>/<slot>.members.json``.

    The stamp holds the member list collect recorded: each member's
    relative path and stat fields.

    Args:
        root: The project root.
        run_id: The run's id.
        slot: The output slot the directory fills.

    Returns:
        The stamp file's path.
    """
    return output_stamps_dir(root, run_id) / f"{slot}{MEMBERS_STAMP_SUFFIX}"


# --- extracted method-version code copies ------------------------------------

#: Extracted copies of registered method versions, under the artifact store.
CODE_COPIES_DIR_NAME = "code"
#: Suffix of the stamp a verified code copy carries beside it.
CODE_COPY_STAMP_SUFFIX = ".verified"


def code_copies_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/code/``, the home of extracted code copies."""
    return artifact_store(root) / CODE_COPIES_DIR_NAME


def code_copy_dir(root: Path, tree_hash: str) -> Path:
    """Return the extracted copy of a version's tree, ``<root>/.wfc/runs/code/<tree hash>/``.

    A run executes a method version's code from here: the git tree its
    registration committed, extracted once and shared by every version with
    the same tree. It lies under the project root, which a container run
    mounts, so its container path is the one translation already gives it.

    Args:
        root: The project root.
        tree_hash: The git tree hash of the version's snapshot.

    Returns:
        The copy's directory path (which may not exist yet).
    """
    return code_copies_dir(root) / tree_hash


def code_copy_stamp(root: Path, tree_hash: str) -> Path:
    """Return the stamp of a verified code copy, ``<root>/.wfc/runs/code/<tree hash>.verified``.

    The stamp sits beside the copy, not inside it, so the copy holds exactly
    the tree's files and the stamp is never one of the files its code
    fingerprint covers.

    Args:
        root: The project root.
        tree_hash: The git tree hash of the version's snapshot.

    Returns:
        The stamp file's path.
    """
    return code_copies_dir(root) / f"{tree_hash}{CODE_COPY_STAMP_SUFFIX}"


# --- row 5: workspace (vestigial) ------------------------------------------

def workspace_dir(root: Path) -> Path:
    """Return the legacy scratch tree ``<root>/.wfc/runs/workspace/``."""
    return artifact_store(root) / WORKSPACE_DIR_NAME


def workspace_target_dir(root: Path, pipeline_id: str, node_id: str,
                         sample: str, variant: str) -> Path:
    """Return ``<root>/.wfc/runs/workspace/<pipeline>/<node>/<sample>/<variant>/``."""
    return workspace_dir(root) / pipeline_id / node_id / sample / variant


# --- row 6: run sentinels ---------------------------------------------------

def sentinels_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/sentinels/``."""
    return artifact_store(root) / SENTINELS_DIR_NAME


def sentinels_relpath() -> str:
    """Return the sentinel root as a root-relative POSIX string, ``.wfc/runs/sentinels``."""
    return str(PurePosixPath(artifact_store_relpath()) / SENTINELS_DIR_NAME)


def run_sentinel_dir(root: Path, pipeline_id: str, node_id: str,
                     sample: str, variant: str) -> Path:
    """Return ``<root>/.wfc/runs/sentinels/<pipeline>/<node>/<sample>/<variant>/``."""
    return sentinels_dir(root) / pipeline_id / node_id / sample / variant


def run_sentinel_path(root: Path, pipeline_id: str, node_id: str,
                      sample: str, variant: str) -> Path:
    """Return the ``.complete`` sentinel inside the target's sentinel dir."""
    return (run_sentinel_dir(root, pipeline_id, node_id, sample, variant)
            / RUN_SENTINEL_FILENAME)


def run_id_sidecar_path(root: Path, pipeline_id: str, node_id: str,
                        sample: str, variant: str) -> Path:
    """Return the ``run_id.txt`` sidecar inside the target's sentinel dir."""
    return (run_sentinel_dir(root, pipeline_id, node_id, sample, variant)
            / RUN_ID_SIDECAR_FILENAME)


def run_sentinel_relpath(pipeline_id: str | None, node_id: str,
                         sample_segment: str, variant_segment: str) -> str:
    """Return the sentinel as a root-relative POSIX string for generated rules.

    The sample and variant segments are handed in verbatim so a generator can
    place wildcards (``{sample}``, ``{variant}``) or a baked literal
    (``__all__``) in them. Without a pipeline id the sentinel sits directly
    under ``.wfc/runs/sentinels``.

    Args:
        pipeline_id: The pipeline's id, or ``None`` for the legacy unscoped
            shape.
        node_id: Node identity within the pipeline.
        sample_segment: The sample path segment, wildcard or literal.
        variant_segment: The variant path segment, wildcard or literal.

    Returns:
        ``.wfc/runs/sentinels[/<pipeline>]/<node>/<sample>/<variant>/.complete``.
    """
    prefix = PurePosixPath(sentinels_relpath())
    if pipeline_id:
        prefix = prefix / pipeline_id
    return str(prefix / node_id / sample_segment / variant_segment
               / RUN_SENTINEL_FILENAME)


# --- row 7: pipeline run dir ------------------------------------------------

def pipelines_dir(root: Path) -> Path:
    """Return ``<root>/.wfc/runs/pipelines/``."""
    return artifact_store(root) / PIPELINES_DIR_NAME


def pipelines_relpath() -> str:
    """Return the pipelines dir as a root-relative POSIX string, ``.wfc/runs/pipelines``."""
    return str(PurePosixPath(artifact_store_relpath()) / PIPELINES_DIR_NAME)


def pipeline_run_dir(root: Path, pipeline_id: str) -> Path:
    """Return ``<root>/.wfc/runs/pipelines/<pipeline-id>/`` — also the pipeline log dir."""
    return pipelines_dir(root) / pipeline_id


def pipeline_doc_path(root: Path, pipeline_id: str) -> Path:
    """Return the frozen ``pipeline.json`` inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_DOC_FILENAME


def pipeline_editable_doc_path(root: Path, pipeline_id: str) -> Path:
    """Return the ``pipeline.editable.json`` sidecar inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_EDITABLE_DOC_FILENAME


def pipeline_record_path(root: Path, pipeline_id: str) -> Path:
    """Return the ``pipeline_record.json`` inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_RECORD_FILENAME


def pipeline_outcomes_dir(root: Path, pipeline_id: str) -> Path:
    """Return the ``outcomes/`` sidecar dir inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / OUTCOMES_DIR_NAME


def pipeline_run_logs_dir(root: Path, pipeline_id: str) -> Path:
    """Return the per-run ``runs/`` log dir inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_RUN_LOGS_DIR_NAME


# --- row 8: sample workspace and ready sentinel -----------------------------

def samples_dir(root: Path) -> Path:
    """Return the sample workspace ``<root>/.wfc/workspace/samples/``.

    Restore's destination: a run's ``restore_sample`` rule materializes each
    registered sample beneath it. Registration writes nothing here.
    """
    return restore_workspace_dir(root) / SAMPLES_DIR_NAME


def sample_dir(root: Path, sample: str) -> Path:
    """Return ``<root>/.wfc/workspace/samples/<name>/``."""
    return samples_dir(root) / sample


def restored_sample_path(root: Path, sample: str, file_name: str) -> Path:
    """Return where a sample's data sits once restored.

    ``<root>/.wfc/workspace/samples/<name>/<file_name>``, where ``file_name``
    is the file (or directory) name the sample row records.

    Args:
        root: The project root.
        sample: The sample's name.
        file_name: The bare name the row's ``registered_path`` records.

    Returns:
        The restored data's path (which may not exist yet).
    """
    return sample_dir(root, sample) / file_name


def sample_ready_sentinel(root: Path, sample: str) -> Path:
    """Return ``<root>/.wfc/workspace/samples/<name>/.sample_ready``."""
    return sample_dir(root, sample) / SAMPLE_READY_SENTINEL


def sample_ready_sentinel_relpath(sample_segment: str) -> str:
    """Return the ready sentinel as a root-relative POSIX string for generated rules.

    Args:
        sample_segment: The sample path segment, wildcard (``{sample}``) or
            a literal sample name.

    Returns:
        ``.wfc/workspace/samples/<sample>/.sample_ready``.
    """
    return str(PurePosixPath(restore_workspace_relpath()) / SAMPLES_DIR_NAME
               / sample_segment / SAMPLE_READY_SENTINEL)


# --- row 9: the method registry ---------------------------------------------

def registry_dir(root: Path) -> Path:
    """Return the registry ``<root>/.wfc/registry/``, tracked by git."""
    return state_dir(root) / REGISTRY_DIR_NAME


def methods_dir(root: Path) -> Path:
    """Return the method snapshots' home ``<root>/.wfc/registry/methods/``."""
    return registry_dir(root) / METHODS_DIR_NAME


def module_methods_dir(root: Path, module: str) -> Path:
    """Return one module's snapshots ``<root>/.wfc/registry/methods/<module>/``."""
    return methods_dir(root) / module


def method_dir(root: Path, module: str, method: str) -> Path:
    """Return a registered method's snapshot dir.

    ``<root>/.wfc/registry/methods/<module>/<method>/``: registration copies
    the method's scripts, ``method.yaml`` and declared helpers here and
    commits them. A run does not execute what is here: it executes the
    registered version's copy extracted from git (:func:`code_copy_dir`), so
    a later edit to this directory changes no run. Two modules' same-named
    methods have two snapshots.

    Both names must meet the one name rule (Contracts), so the path never
    leaves the registry's own directory for the method.

    Args:
        root: The project root.
        module: The method's module name.
        method: The method's name.

    Returns:
        The snapshot directory's path (which may not exist yet).

    Raises:
        ValueError: *module* or *method* breaks the name rule.
    """
    from ..contracts.envspec import validate_env_name
    validate_env_name(module, "module")
    validate_env_name(method, "method")
    return module_methods_dir(root, module) / method


#: Suffix of a method's version list, the file beside its snapshot dir.
METHOD_VERSIONS_SUFFIX = ".versions.json"


def method_versions_path(root: Path, module: str, method: str) -> Path:
    """Return a registered method's version list.

    ``<root>/.wfc/registry/methods/<module>/<method>.versions.json``: the
    git-tracked list of the method's versions, a sibling of its snapshot
    dir, so it is not a file of the snapshot and never enters the code
    fingerprint.

    Args:
        root: The project root.
        module: The method's module name.
        method: The method's name.

    Returns:
        The version list's path (which may not exist yet).
    """
    return module_methods_dir(root, module) / f"{method}{METHOD_VERSIONS_SUFFIX}"


#: Name of the registry's staging directory under ``.wfc/build/``; the dot
#: keeps it apart from every env's build context (env names have no dot).
REGISTRY_STAGING_DIR_NAME = ".registry-staging"


def registry_staging_dir(root: Path) -> Path:
    """Return where registration builds a snapshot or list before renaming it in.

    ``<root>/.wfc/build/.registry-staging/``: on the registry's filesystem,
    so the rename into ``.wfc/registry/`` is a rename, and git-ignored
    (``.wfc/build/``), so a staging copy a crash leaves behind is never in
    ``git status``. Each registration takes its own directory inside it.

    Args:
        root: The project root.

    Returns:
        The staging directory's path (which may not exist yet).
    """
    return state_dir(root) / BUILD_DIR_NAME / REGISTRY_STAGING_DIR_NAME


# --- rows 10-11: env manifest and build context ----------------------------

def env_manifest_path(root: Path) -> Path:
    """Return the env manifest ``<root>/.wfc/envs.json``."""
    return state_dir(root) / ENV_MANIFEST_FILENAME


def env_build_dir(root: Path, env: str) -> Path:
    """Return an env's build context ``<root>/.wfc/build/<env>/``."""
    return state_dir(root) / BUILD_DIR_NAME / env


#: The demo schema stamp's file name, under the state dir.
DEMO_STAMP_FILENAME = "demo.json"


def demo_stamp_path(root: Path) -> Path:
    """Return the demo schema stamp ``<root>/.wfc/demo.json``.

    ``wfc demo`` writes it (git-tracked) to record which version of the
    demo built the project's demo.

    Args:
        root: The project root.

    Returns:
        The stamp's path (which may not exist).
    """
    return state_dir(root) / DEMO_STAMP_FILENAME


def gitattributes_path(root: Path) -> Path:
    """Return the project's git attributes file ``<root>/.gitattributes``."""
    return root / GITATTRIBUTES_FILENAME


def captures_dir(root: Path) -> Path:
    """Return the captures root ``<root>/.wfc/captures/``."""
    return state_dir(root) / CAPTURES_DIR_NAME


def env_captures_dir(root: Path, env: str) -> Path:
    """Return an env's captures ``<root>/.wfc/captures/<env>/``, one folder per image."""
    return captures_dir(root) / env


def env_capture_dir(root: Path, env: str, fingerprint: str) -> Path:
    """Return one image's captures folder ``<root>/.wfc/captures/<env>/<fingerprint>/``.

    Args:
        root: The project root.
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.

    Returns:
        The folder's path (which may not exist).
    """
    return env_captures_dir(root, env) / fingerprint


def env_capture_relpath(env: str, fingerprint: str) -> str:
    """Return a captures folder as a root-relative POSIX string.

    Args:
        env: The env name.
        fingerprint: The image's ``env_fingerprint``.

    Returns:
        ``.wfc/captures/<env>/<fingerprint>``.
    """
    return str(PurePosixPath(STATE_DIR_NAME) / CAPTURES_DIR_NAME / env / fingerprint)
