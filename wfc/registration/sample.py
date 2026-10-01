"""Sample registration: refuse bad requests, store the bytes, write the sample row.

The bytes (a file's or a directory's) are handed to Storage's sample store.
"""

from __future__ import annotations

from pathlib import Path

from axiom_annotations import Step, task
from sqlmodel import select

from .. import layout
from ..identity import directory_manifest
from ..persistence import (
    Sample,
    get_session,
)
from ..persistence import (
    project_root as get_project_root,
)


@task(purpose="Register a data sample (a file or a directory): refuse "
              "everything refusable before any cache write, cache its source "
              "bytes through Storage, and write the sample row")
def register_sample(
    name: str,
    source_path: Path,
    project_root: Path | None = None,
    registration_mode: str = "copy",
    allow_reserved: bool = False,
    description: str | None = None,
    manifest_path: Path | None = None,
) -> Path:
    """Register a data sample, a file or a directory: cache its bytes, then write its row.

    Refuses first, before any cache write or DB write: a reserved
    ``__demo__*`` name (unless ``allow_reserved``), any registration mode
    but ``"copy"``, a missing source, a description that is not a string,
    a manifest the manifest reader refuses (or a manifest given together
    with a description), a project whose DVC setup fails Storage's
    readiness check (``ensure_dvc_ready``), a name that is already
    registered, and a directory whose content cannot be identified (a
    symlink inside, a case-only name collision, no files).

    Then reads the source's metadata (for a directory: the sum of its
    files' sizes, their count and the newest mtime) and hands the source
    to Storage's ``store_sample_bytes``, which hashes it, writes the bytes
    into the DVC cache in copy mode (the source stays in place) and settles
    the first push status: pushed at once (or failed) when standalone with
    a remote, pending inside a pipeline, deferred with no remote.

    Last, writes the sample row: the name, the content hash, the source
    path with its metadata, the description, the push fields, and
    ``registered_path``, relative to the project root.  Nothing is copied
    into ``data/samples/{name}/`` at registration: ``registered_path``
    records where the ``restore_sample`` rule will materialize the sample
    later, not something that exists now.

    If the cache write fails, no row is written.

    Args:
        name: Sample identifier (e.g. 'CFPAC_ERKi').
        source_path: Path to the source data file or directory.
        project_root: Project root directory (defaults to
            ``get_project_root()``).
        registration_mode: Only ``"copy"`` is implemented. ``"link"`` is
            reserved for a future path-only registration mode.
        allow_reserved: Permit a reserved ``__demo__*`` name; only
            ``wfc demo`` passes ``True``.
        description: Optional free-text description (metadata only; never
            part of the content hash).
        manifest_path: Optional YAML registration manifest, read by
            :func:`~wfc.registration.sample_manifest.read_sample_manifest`;
            its description is stored on the row.

    Returns:
        The absolute path under ``data/samples/{name}/`` where the
        ``restore_sample`` rule will materialize the sample.  Nothing is
        there at registration.

    Raises:
        ValueError: If the name is reserved and ``allow_reserved`` is
            ``False``, a sample with this name is already registered, or a
            manifest and a description are both given.
        SampleManifestError: If the description or the manifest is refused.
        DirectoryContentError: If a directory source holds a symlink, a
            case-only name collision, or no files.
        NotImplementedError: If ``registration_mode != "copy"``.
        FileNotFoundError: If the source does not exist.
        DvcNotConfiguredError: If the project's DVC setup fails
            ``ensure_dvc_ready``.
    """
    口 = Step(step_num=1, name="Refuse",
             purpose="Refuse a reserved name, an unimplemented mode, a "
                     "missing source, a refused description or manifest, an "
                     "unconfigured DVC, an already-registered name and a "
                     "directory whose content cannot be identified",
             critical="Every refusal runs before the cache write in step 2: "
                      "a refused registration leaves no new cache object and "
                      "attempts no push")
    # Reserved-namespace guard: refuse a __demo__* sample name before
    # any file operation or DB write. `wfc demo` opts in via allow_reserved.
    from ..reserved import check_reserved_name
    check_reserved_name(name, "sample", allow_reserved)

    if registration_mode != "copy":
        raise NotImplementedError(
            f"registration_mode={registration_mode!r} is not implemented. "
            "Only 'copy' is supported."
        )
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    from .sample_manifest import check_description, read_sample_manifest
    description = check_description(description)
    if manifest_path is not None:
        if description is not None:
            raise ValueError(
                "Give the sample's description either in the manifest or "
                "directly, not both."
            )
        description = read_sample_manifest(manifest_path)

    if project_root is None:
        project_root = get_project_root()
    project_root = Path(project_root).resolve()

    # DVC gate: require [dvc] config before any file operations
    from ..storage import ensure_dvc_ready, store_sample_bytes
    ensure_dvc_ready(project_root)

    with get_session() as session:
        existing = session.exec(
            select(Sample).where(Sample.name == name)
        ).first()
    if existing is not None:
        raise ValueError(f"Sample '{name}' is already registered (id={existing.id})")

    # A directory's identity refuses content it cannot identify (raising
    # DirectoryContentError naming the path) and yields its metadata.
    tree = directory_manifest(source_path) if source_path.is_dir() else None

    口 = Step(step_num=2, name="Read the metadata, cache and settle the first push",
             purpose="Read the source's size and mtime (for a directory: the "
                     "total, the newest and the file count) before the copy, "
                     "then hand the bytes to Storage's sample store, which "
                     "hashes them, caches them in copy mode and settles the "
                     "first push status")
    # Do NOT copy into data/samples/. The DVC cache is the sole
    # store; data/samples/ is ephemeral workspace, populated lazily by the
    # Snakemake restore_sample rule. registered_path is a contract (where the
    # sample WILL be restored) not a claim that it exists there now.
    dest = layout.sample_dir(project_root, name) / source_path.name

    # Capture the metadata FROM THE SOURCE, before the copy.
    if tree is not None:
        file_type = "directory"
        src_file_size = tree.total_size
        src_file_mtime = tree.newest_mtime
        file_count = tree.file_count
    else:
        src_stat = source_path.stat()
        file_type = source_path.suffix.lstrip(".")
        src_file_size = src_stat.st_size
        src_file_mtime = src_stat.st_mtime
        file_count = None

    # Storage writes the bytes (copy mode: the user's source stays in place),
    # settles the first push status and hands back the hash and push fields.
    stored = store_sample_bytes(source_path, project_root)

    口 = Step(step_num=3, name="Write the row",
             purpose="Insert the sample row, with registered_path relative "
                     "to the project root")
    with get_session() as session:
        sample = Sample(
            name=name,
            source_path=str(source_path),
            registered_path=dest.relative_to(project_root).as_posix(),
            file_type=file_type,
            file_size=src_file_size,
            file_mtime=src_file_mtime,
            file_count=file_count,
            description=description,
            registration_mode="copy",
            content_hash=stored.content_hash,
            push_status=stored.push_status,
            pushed_at=stored.pushed_at,
            push_error=stored.push_error,
        )
        session.add(sample)
        session.commit()

    return dest
