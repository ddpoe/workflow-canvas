"""Sample restore: put a registered sample's bytes where a pipeline run reads them.

:func:`restore_sample` is the body of ``wfc restore-sample``, which the
generated Snakefile's ``restore_sample`` rule runs before a root step.  It
restores the sample's cache entry to its registered path, pulling it from
the remote on a local miss, and touches the rule's sentinel.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from axiom_annotations import task
from sqlmodel import select

from .. import layout
from ..persistence import Sample, get_session
from ..persistence import project_root as get_project_root
from .cache import sample_repair


class MalformedSampleRecordError(ValueError):
    """A sample row whose restore path is empty or absolute.

    ``registered_path`` is recorded relative to the project root, so a
    moved project restores under its new root. An absolute (or missing)
    path is a malformed record: no dual lookup and no rewrite in place;
    the repair is re-registration.

    Attributes:
        name: The sample's name.
        recorded: The ``registered_path`` the row carries.
    """

    def __init__(self, name: str, recorded: str) -> None:
        self.name = name
        self.recorded = recorded
        super().__init__(
            f"sample '{name}' is a malformed record — its row records "
            f"an absolute or empty restore path ({recorded!r}), which "
            f"breaks when the project moves. {sample_repair(name)}"
        )


def sample_data_path(project_root: Path, sample: Sample | str) -> Path | None:
    """Return where a registered sample's data sits once restored.

    The one owner of that location: the row's ``registered_path`` (the
    file or directory registration recorded, relative to the project
    root) joined to the current project root. Restore writes there and
    Execution's materialize reads there.

    Args:
        project_root: The current project root.
        sample: The ``Sample`` row, or the sample's name to look it up by.

    Returns:
        The absolute data path (which may not exist yet), or ``None`` when
        a name is given and no sample of that name is registered.

    Raises:
        MalformedSampleRecordError: The row's ``registered_path`` is empty
            or absolute.
    """
    if isinstance(sample, str):
        with get_session() as session:
            row = session.exec(
                select(Sample).where(Sample.name == sample)
            ).first()
        if row is None:
            return None
        sample = row
    recorded = sample.registered_path or ""
    if (not recorded or PurePosixPath(recorded).is_absolute()
            or PureWindowsPath(recorded).is_absolute()
            or PureWindowsPath(recorded).drive):
        raise MalformedSampleRecordError(sample.name, recorded)
    return Path(project_root) / recorded


@task(purpose="Restore a registered sample from DVC cache to its "
              "project-relative registered path, refusing a malformed row "
              "(no content hash, an absolute path, or a malformed entry)")
def restore_sample(
    name: str,
    content_hash: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Restore a sample (a file or a directory) from the DVC cache into ``data/samples/{name}/``.

    Called by Snakemake ``restore_sample`` rules to lazily materialize
    samples before root pipeline steps execute.  The destination is the
    row's ``registered_path`` joined to the current project root, so a
    moved project restores under its new root.  A row whose
    ``registered_path`` is absolute is a malformed record and is refused,
    naming re-registration; so is a directory tree at an unsuffixed cache
    address.

    A row carrying no ``content_hash`` is a **malformed record**: the
    cache is content-addressed, so there is nothing to look the bytes up
    by. The restore refuses, naming the sample and the one command that
    fixes it, and writes no readiness sentinel -- the run stops here
    rather than proceeding over a sample wfc cannot materialize. A
    pipeline normally never reaches this: ``load_sample_hashes`` refuses
    the same record at pipeline start, where the whole sample set is
    visible.

    A local miss is two different situations and gets two different
    refusals. When ``pull_cache`` reports the archive answered and the
    content still is not here, the bytes are nowhere wfc can reach and
    re-registering from the source is the repair. When ``pull_cache``
    reports it could NOT ask -- no remote configured, DVC failures, or an
    exception, each of which it says only on stderr -- the content may
    well still be in the archive, so that message names the remote to
    check and explicitly does not prescribe re-registration.

    Idempotent: ``restore_from_cache`` handles integrity verification
    internally -- if the file already exists with matching content hash,
    the restore is skipped; if the hash mismatches, the file is replaced.

    Args:
        name: Sample identifier.
        content_hash: Expected content hash (passed from Snakemake rule).
            If not provided, looked up from the database.
        project_root: Project root directory (defaults to cwd).

    Raises:
        SystemExit: If the sample is not registered, its row carries no
            content hash, its content is in neither the local cache nor
            the archive, or the archive could not be reached.
    """
    if project_root is None:
        project_root = get_project_root()

    with get_session() as session:
        sample = session.exec(
            select(Sample).where(Sample.name == name)
        ).first()
        if sample is None:
            print(f"ERROR: sample '{name}' not found in the database.", file=sys.stderr)
            sys.exit(1)

        # Use DB content_hash if not passed explicitly
        hash_val = content_hash or sample.content_hash
        if not hash_val:
            # Malformed record. The cache is content-addressed, so with no
            # hash there is nothing to look the bytes up by: no conversion,
            # no fallback, no sentinel. Refuse and let it be re-registered.
            print(
                f"ERROR: sample '{name}' is a malformed record — its row "
                f"carries no content hash, so wfc cannot key a run on its "
                f"content and cannot restore it from the cache. "
                f"Re-register it from its source with: "
                f"wfc register-sample --name {name} --source <path>",
                file=sys.stderr,
            )
            sys.exit(1)

        # registered_path is relative to the project root, so a moved
        # project restores under its new root. An absolute path is a
        # malformed record: no dual lookup, no rewrite in place.
        try:
            dest = sample_data_path(project_root, sample)
        except MalformedSampleRecordError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        # Restore from DVC cache (handles idempotency: skips if dest
        # already exists with matching hash, replaces if mismatched)
        from .cache import (
            MalformedEntryError,
            check_entry_shape,
            restore_from_cache,
        )
        from .transport import pull_cache
        try:
            check_entry_shape(project_root, hash_val, sample_repair(name))
        except MalformedEntryError as exc:
            print(f"ERROR: cannot restore sample '{name}' — {exc}", file=sys.stderr)
            sys.exit(1)
        restored = restore_from_cache(hash_val, dest, project_root)
        # `reached` records whether the archive ANSWERED, not whether the
        # bytes were there: pull_cache returns False when no remote is
        # configured, when DVC reports failures and on any exception, and
        # says so only on stderr. Discarding it made one message serve two
        # different situations -- "the content is nowhere" and "wfc could
        # not ask" -- and the first prescribes re-registering from a source
        # the user may no longer have.
        reached = True
        if not restored:
            # Try pulling from remote first, then restore again
            reached = pull_cache([hash_val], project_root)
            restored = restore_from_cache(hash_val, dest, project_root)

        if not restored and not reached:
            print(
                f"ERROR: cannot restore sample '{name}' — content_hash "
                f"{hash_val} is not in this machine's DVC cache, and the "
                f"archive could not be reached (see the WARNING above). The "
                f"content may well still be in the archive; wfc could not "
                f"ask. Check the project's DVC remote (`dvc remote list` in "
                f"{project_root}) and retry — do NOT re-register the sample "
                f"on the strength of this message.",
                file=sys.stderr,
            )
            sys.exit(1)

        if not restored:
            print(
                f"ERROR: cannot restore sample '{name}' — content_hash "
                f"{hash_val} is in neither this machine's DVC cache nor the "
                f"archive, so its bytes are nowhere wfc can reach. "
                f"Re-register it from its source with: "
                f"wfc register-sample --name {name} --source <path>",
                file=sys.stderr,
            )
            sys.exit(1)

        _touch_sentinel(project_root, name)


def _touch_sentinel(project_root: Path, name: str) -> None:
    """Write the Snakemake readiness sentinel for a restored sample.

    Sample files live in the DVC cache rather than under
    ``data/samples/<name>/``, so Snakemake cannot use the sample file
    itself as the restore rule's output. The rule's output is a sentinel at
    ``<project_root>/data/samples/<name>/.sample_ready``; its parent
    directory is created if missing. The path is absolute via
    ``get_project_root()`` so it is cwd-independent, which matters on
    Windows UNC shell rules where ``cmd.exe`` rewrites cwd to ``C:\\Windows``.

    Args:
        project_root: Project root directory.
        name: Sample identifier.
    """
    sentinel = layout.sample_ready_sentinel(project_root, name)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()
