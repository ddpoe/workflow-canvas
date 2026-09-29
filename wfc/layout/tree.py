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
#: The artifact store: run archives, run sentinels, pipeline run dirs.
ARTIFACT_STORE_NAME = ".runs"
#: The legacy scratch tree under the artifact store (vestigial).
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
#: The run-completion sentinel Snakemake waits on.
RUN_SENTINEL_FILENAME = ".complete"
#: The run-id lineage sidecar beside the sentinel.
RUN_ID_SIDECAR_FILENAME = "run_id.txt"
#: The user-data tree and the registered-sample store beneath it.
DATA_DIR_NAME = "data"
SAMPLES_DIR_NAME = "samples"
#: The sample-restore sentinel beside a sample's data.
SAMPLE_READY_SENTINEL = ".sample_ready"
#: Flat standalone methods and nested module hierarchies.
METHODS_DIR_NAME = "methods"
MODULES_DIR_NAME = "modules"
#: The env manifest inside the state dir and the build contexts beneath it.
ENV_MANIFEST_FILENAME = "envs.json"
BUILD_DIR_NAME = "build"


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
    """Return ``<root>/.runs/``."""
    return Path(root) / ARTIFACT_STORE_NAME


def run_archive_name(run_id: int) -> str:
    """Return a run's archive directory name: the id zero-padded to 8."""
    return f"{run_id:08d}"


def run_archive_dir(root: Path, run_id: int) -> Path:
    """Return the run archive ``<root>/.runs/<run-id zero-padded to 8>/``."""
    return artifact_store(root) / run_archive_name(run_id)


# --- directory-entry checkouts ---------------------------------------------

#: Checkouts of directory cache entries, under the artifact store.
CHECKOUTS_DIR_NAME = "checkouts"


def checkouts_dir(root: Path) -> Path:
    """Return ``<root>/.runs/checkouts/``, the home of directory-entry checkouts."""
    return artifact_store(root) / CHECKOUTS_DIR_NAME


def checkout_dir(root: Path, dir_hash: str) -> Path:
    """Return the checkout of a directory cache entry, ``<root>/.runs/checkouts/<hash>/``.

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
    """Return the stamp of a directory checkout, ``<root>/.runs/checkouts/<hash>.verified``.

    The stamp sits beside the checkout, not inside it, so the checkout holds
    exactly the directory's files.

    Args:
        root: The project root.
        dir_hash: The directory's ``.dir`` content hash.

    Returns:
        The stamp file's path.
    """
    return checkouts_dir(root) / f"{dir_hash}{CHECKOUT_STAMP_SUFFIX}"


# --- row 5: workspace (vestigial) ------------------------------------------

def workspace_dir(root: Path) -> Path:
    """Return the legacy scratch tree ``<root>/.runs/workspace/``."""
    return artifact_store(root) / WORKSPACE_DIR_NAME


def workspace_target_dir(root: Path, pipeline_id: str, node_id: str,
                         sample: str, variant: str) -> Path:
    """Return ``<root>/.runs/workspace/<pipeline>/<node>/<sample>/<variant>/``."""
    return workspace_dir(root) / pipeline_id / node_id / sample / variant


# --- row 6: run sentinels ---------------------------------------------------

def sentinels_dir(root: Path) -> Path:
    """Return ``<root>/.runs/sentinels/``."""
    return artifact_store(root) / SENTINELS_DIR_NAME


def sentinels_relpath() -> str:
    """Return the sentinel root as a root-relative POSIX string, ``.runs/sentinels``."""
    return str(PurePosixPath(ARTIFACT_STORE_NAME) / SENTINELS_DIR_NAME)


def run_sentinel_dir(root: Path, pipeline_id: str, node_id: str,
                     sample: str, variant: str) -> Path:
    """Return ``<root>/.runs/sentinels/<pipeline>/<node>/<sample>/<variant>/``."""
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
    under ``.runs/sentinels``.

    Args:
        pipeline_id: The pipeline's id, or ``None`` for the legacy unscoped
            shape.
        node_id: Node identity within the pipeline.
        sample_segment: The sample path segment, wildcard or literal.
        variant_segment: The variant path segment, wildcard or literal.

    Returns:
        ``.runs/sentinels[/<pipeline>]/<node>/<sample>/<variant>/.complete``.
    """
    prefix = PurePosixPath(sentinels_relpath())
    if pipeline_id:
        prefix = prefix / pipeline_id
    return str(prefix / node_id / sample_segment / variant_segment
               / RUN_SENTINEL_FILENAME)


# --- row 7: pipeline run dir ------------------------------------------------

def pipelines_dir(root: Path) -> Path:
    """Return ``<root>/.runs/pipelines/``."""
    return artifact_store(root) / PIPELINES_DIR_NAME


def pipelines_relpath() -> str:
    """Return the pipelines dir as a root-relative POSIX string, ``.runs/pipelines``."""
    return str(PurePosixPath(ARTIFACT_STORE_NAME) / PIPELINES_DIR_NAME)


def pipeline_run_dir(root: Path, pipeline_id: str) -> Path:
    """Return ``<root>/.runs/pipelines/<pipeline-id>/`` — also the pipeline log dir."""
    return pipelines_dir(root) / pipeline_id


def pipeline_doc_path(root: Path, pipeline_id: str) -> Path:
    """Return the frozen ``pipeline.json`` inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_DOC_FILENAME


def pipeline_editable_doc_path(root: Path, pipeline_id: str) -> Path:
    """Return the ``pipeline.editable.json`` sidecar inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_EDITABLE_DOC_FILENAME


def pipeline_outcomes_dir(root: Path, pipeline_id: str) -> Path:
    """Return the ``outcomes/`` sidecar dir inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / OUTCOMES_DIR_NAME


def pipeline_run_logs_dir(root: Path, pipeline_id: str) -> Path:
    """Return the per-run ``runs/`` log dir inside the pipeline run dir."""
    return pipeline_run_dir(root, pipeline_id) / PIPELINE_RUN_LOGS_DIR_NAME


# --- row 8: sample data dir and ready sentinel ------------------------------

def samples_dir(root: Path) -> Path:
    """Return the registered-sample store ``<root>/data/samples/``."""
    return Path(root) / DATA_DIR_NAME / SAMPLES_DIR_NAME


def sample_dir(root: Path, sample: str) -> Path:
    """Return ``<root>/data/samples/<name>/``."""
    return samples_dir(root) / sample


def sample_ready_sentinel(root: Path, sample: str) -> Path:
    """Return ``<root>/data/samples/<name>/.sample_ready``."""
    return sample_dir(root, sample) / SAMPLE_READY_SENTINEL


def sample_ready_sentinel_relpath(sample_segment: str) -> str:
    """Return the ready sentinel as a root-relative POSIX string for generated rules.

    Args:
        sample_segment: The sample path segment, wildcard (``{sample}``) or
            a literal sample name.

    Returns:
        ``data/samples/<sample>/.sample_ready``.
    """
    return str(PurePosixPath(DATA_DIR_NAME) / SAMPLES_DIR_NAME / sample_segment
               / SAMPLE_READY_SENTINEL)


# --- row 9: methods and modules --------------------------------------------

def methods_dir(root: Path) -> Path:
    """Return the flat standalone-methods dir ``<root>/methods/``."""
    return Path(root) / METHODS_DIR_NAME


def method_dir(root: Path, method: str) -> Path:
    """Return a registered method's dir ``<root>/methods/<method>/``."""
    return methods_dir(root) / method


def method_script_path(root: Path, method: str) -> Path:
    """Return a registered method's script ``<root>/methods/<method>/<method>.py``."""
    return method_dir(root, method) / f"{method}.py"


def modules_dir(root: Path) -> Path:
    """Return the nested module hierarchy dir ``<root>/modules/``."""
    return Path(root) / MODULES_DIR_NAME


def module_dir(root: Path, module: str) -> Path:
    """Return ``<root>/modules/<module>/``."""
    return modules_dir(root) / module


# --- rows 10-11: env manifest and build context ----------------------------

def env_manifest_path(root: Path) -> Path:
    """Return the env manifest ``<root>/.wfc/envs.json``."""
    return state_dir(root) / ENV_MANIFEST_FILENAME


def env_build_dir(root: Path, env: str) -> Path:
    """Return an env's build context ``<root>/.wfc/build/<env>/``."""
    return state_dir(root) / BUILD_DIR_NAME / env
