"""Sample restore: put a registered sample's bytes where a pipeline run reads them.

:func:`restore_sample` is the body of ``wfc restore-sample``, which the
generated Snakefile's ``restore_sample`` rule runs before a root step.  It
restores the sample's cache entry into the sample workspace
(``.wfc/workspace/samples/<name>/<file name>``), pulling it from
the remote on a local miss, and touches the rule's sentinel.

A restored copy is read-only and stamped (``layout.sample_stamp``) only
after its bytes are checked against the sample's ``content_hash``. While
the sentinel stands the rule does not run again, so materialize asks
:func:`check_sample` before every read: a copy that is no longer what wfc
registered is restored again from a checked cache entry, with a warning
naming the sample.

A restored sample is a view of its cache entry, held by the pipelines
reading it: inside a pipeline both the restore and the check record the
pipeline as holding the sample (the sentinel another pipeline left means
this one's rule may never run), and :func:`end_pipeline_samples` removes
the views the ending pipeline held that no other pipeline holds, with
their readiness sentinels and stamps.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from axiom_annotations import AutoStep, Step, task
from sqlmodel import select

from .. import layout
from ..persistence import InUseKind, PushStatus, Sample, get_session
from ..persistence import project_root as get_project_root
from .cache import sample_repair
from .in_use import hold_for_pipeline, pipeline_keys, release_pipeline, remove_if_unheld
from .integrity import check_entry, inputs_setting, read_stamp, record_keeps_change_time

#: The restored copy is what wfc registered (an unstamped copy was hashed
#: and stamped).
SAMPLE_INTACT = "intact"
#: The copy was not what wfc registered and was restored again.
SAMPLE_REPAIRED = "repaired"
#: Nothing to check: no such sample, a malformed row, or no copy restored.
#: The reader's own refusal names these.
SAMPLE_UNCHECKED = "unchecked"
#: The copy is not what wfc registered and could not be restored exactly.
SAMPLE_REFUSED = "refused"


@dataclass(frozen=True)
class SampleCheck:
    """What :func:`check_sample` found and did.

    Attributes:
        name: The sample's name.
        status: :data:`SAMPLE_INTACT`, :data:`SAMPLE_REPAIRED`,
            :data:`SAMPLE_UNCHECKED` or :data:`SAMPLE_REFUSED`.
        message: For a refusal, why the copy cannot be read, naming the
            sample and the repair.
        warning: For a repair, the warning naming the sample, as printed
            on stderr; the step log carries it too.
    """

    name: str
    status: str
    message: str | None = None
    warning: str | None = None

    @property
    def refused(self) -> bool:
        """Whether the sample must not be read."""
        return self.status == SAMPLE_REFUSED


class MalformedSampleRecordError(ValueError):
    """A sample row whose ``registered_path`` is not a bare file name.

    ``registered_path`` records only the name of the file (or directory)
    registration cached; Layout derives where it is restored from the
    project root and the sample's name, so a moved project restores under
    its new root. A row recording anything else (an empty value, an
    absolute path, a path with a directory component, ``.`` or ``..``)
    is a malformed record: no dual lookup and no rewrite in place; the
    repair is re-registration.

    Attributes:
        name: The sample's name.
        recorded: The ``registered_path`` the row carries.
    """

    def __init__(self, name: str, recorded: str) -> None:
        self.name = name
        self.recorded = recorded
        super().__init__(
            f"sample '{name}' is a malformed record — its row records "
            f"{recorded!r} where a bare file name belongs, so wfc cannot "
            f"tell where to restore it. {sample_repair(name)}"
        )


def _is_bare_file_name(recorded: str) -> bool:
    """Say whether a recorded value is a single file or directory name.

    Args:
        recorded: The row's ``registered_path``.

    Returns:
        ``True`` when the value is non-empty, holds no separator or drive,
        and is not ``.`` or ``..``.
    """
    if not recorded or recorded in (".", ".."):
        return False
    if "/" in recorded or "\\" in recorded:
        return False
    return not PureWindowsPath(recorded).drive


def sample_data_path(project_root: Path, sample: Sample | str) -> Path | None:
    """Return where a registered sample's data sits once restored.

    The one owner of that location: Layout's sample workspace path for the
    sample's name and the file name the row's ``registered_path`` records,
    under the current project root. Restore writes there and Execution's
    materialize reads there.

    Args:
        project_root: The current project root.
        sample: The ``Sample`` row, or the sample's name to look it up by.

    Returns:
        The absolute data path (which may not exist yet), or ``None`` when
        a name is given and no sample of that name is registered.

    Raises:
        MalformedSampleRecordError: The row's ``registered_path`` is not a
            bare file name.
    """
    if isinstance(sample, str):
        with get_session() as session:
            row: Sample | None = session.exec(
                select(Sample).where(Sample.name == sample)
            ).first()
        if row is None:
            return None
        sample = row
    recorded = sample.registered_path or ""
    if not _is_bare_file_name(recorded):
        raise MalformedSampleRecordError(sample.name, recorded)
    return layout.restored_sample_path(Path(project_root), sample.name, recorded)


@task(purpose="Restore a registered sample from DVC cache into the sample "
              "workspace (.wfc/workspace/samples/<name>/<file name>), refusing "
              "a malformed row (no content hash, a registered_path that is not "
              "a bare file name, or a malformed entry)")
def restore_sample(
    name: str,
    content_hash: str | None = None,
    project_root: Path | None = None,
    pipeline_id: str | None = None,
) -> None:
    """Restore a sample (a file or a directory) from the DVC cache into ``.wfc/workspace/samples/{name}/``.

    Called by Snakemake ``restore_sample`` rules to lazily materialize
    samples before root pipeline steps execute.  The destination is
    :func:`sample_data_path`: Layout's sample workspace under the current
    project root plus the file name the row records, so a moved project
    restores under its new root.  A row whose ``registered_path`` is not a
    bare file name is a malformed record and is refused, naming
    re-registration; so is a directory tree at an unsuffixed cache
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

    Idempotent: a copy the one integrity check finds intact (by the
    sample's stamp, or by a re-hash that then stamps it) is kept. Anything
    else is restored again: the sample's cache entry is checked first (a
    changed object is pulled again when the sample is recorded pushed and
    refused otherwise), then the copy is made with the hashing copy (a
    file) or the checkout (a directory), read-only, and stamped only when
    it matches ``content_hash``.

    Inside a pipeline the restore first records the pipeline as holding
    the sample's view (Storage's in-use records), before it checks or
    makes the view, so the end of another pipeline that restored the same
    sample keeps it; the end of this one removes it
    (:func:`end_pipeline_samples`). A restore outside a pipeline records
    nothing.

    Args:
        name: Sample identifier.
        content_hash: Expected content hash (passed from Snakemake rule).
            If not provided, looked up from the database.
        project_root: Project root directory (defaults to cwd).
        pipeline_id: The pipeline restoring it; ``WFC_PIPELINE_ID`` (the
            engine sets it for every job it runs) when None, and no
            pipeline when that is unset too.

    Raises:
        SystemExit: If the sample is not registered, its row carries no
            content hash, its content is in neither the local cache nor
            the archive, the archive could not be reached, or its cache
            entry changed and cannot be pulled again.
    """
    project_root = Path(project_root) if project_root is not None else get_project_root()

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

        # registered_path records only the file name; Layout derives the
        # rest under the current root, so a moved project restores under
        # its new root. Anything but a bare name is a malformed record: no
        # dual lookup, no rewrite in place.
        try:
            dest = sample_data_path(project_root, sample)
        except MalformedSampleRecordError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        # Given a row (not a name), sample_data_path always returns a path.
        assert dest is not None

        from .cache import MalformedEntryError, check_entry_shape
        try:
            check_entry_shape(project_root, hash_val, sample_repair(name))
        except MalformedEntryError as exc:
            print(f"ERROR: cannot restore sample '{name}' — {exc}", file=sys.stderr)
            sys.exit(1)
        pushed = sample.push_status == PushStatus.pushed.value

    # The pipeline's record goes first: the end of another pipeline that
    # restored this sample keeps a view a record names, and one whose
    # removal commits before this record finds the view gone here and makes
    # it again.
    holder = _pipeline_holding(pipeline_id)
    if holder is not None:
        hold_for_pipeline(InUseKind.sample_view, name, holder)

    # Idempotent: a copy that is what wfc registered (by its stamp, or by a
    # re-hash that then stamps it) is kept; anything else is restored again
    # from a checked cache entry.
    setting = inputs_setting(project_root)
    stamp = layout.sample_stamp(project_root, name)
    if not check_entry(dest, recorded_hash=hash_val, stamp=stamp, setting=setting).intact:
        problem = restore_verified(name, hash_val, pushed, dest, project_root, setting)
        if problem is not None:
            print(f"ERROR: cannot restore sample '{name}' — {problem}", file=sys.stderr)
            sys.exit(1)

    _touch_sentinel(project_root, name)
    # The folder holding the sample is read-only too, so a method that
    # writes beside its input fails at the write (POSIX; Windows ignores
    # the read-only attribute on a directory).
    from .cache import protect_dirs
    protect_dirs(layout.sample_dir(project_root, name))


@task(purpose="Restore a sample whose copy is missing or changed from a checked "
              "cache entry: refuse a malformed entry naming the "
              "re-registration, pull a changed pushed entry again, refuse an "
              "unpushed one, then copy with the hashing copy or the checkout "
              "and stamp only a verified copy",
      inputs="the sample's name, content hash and push status; where it is "
             "restored; [integrity] inputs",
      outputs="None with the registered bytes restored and stamped and the "
              "sample's folder read-only again, or why not, naming the repair")
def restore_verified(name: str, hash_val: str, pushed: bool, dest: Path,
                      project_root: Path, setting: str) -> str | None:
    """Restore a sample whose copy is missing or changed, from a checked cache entry.

    The sample's own cache entry is checked first. A malformed entry (a
    directory tree at an address without the ``.dir`` suffix) is refused
    with :class:`~wfc.storage.cache.MalformedEntryError`'s message, naming
    the re-registration, pushed or not: nothing pulls or converts it. A
    changed object is pulled again when the sample is recorded pushed and
    refused otherwise, so wfc never restores from bad bytes. The copy is
    then removed and restored through ``restore_from_cache`` (the hashing
    copy, or the checkout for a directory), which stamps it only after it
    matches. A local miss pulls from the remote, then restores again.

    The restore opens the sample's folder to write into it; a restore that
    succeeds makes the folder and every directory under it read-only again,
    so every repair (the restore rule's, a step's check, ``wfc verify
    --repair``) leaves it protected. A caller that then writes the ready
    sentinel protects the folder again after it.

    Args:
        name: The sample's name.
        hash_val: The sample's ``content_hash``.
        pushed: Whether the sample's entry is recorded pushed.
        dest: Where the sample is restored.
        project_root: Project root directory.
        setting: ``[integrity] inputs``.

    Returns:
        None when ``dest`` now holds the registered bytes, stamped, in a
        read-only folder; else why it could not be restored, naming the
        repair.
    """
    from .cache import (
        ChangedEntryError,
        MalformedEntryError,
        changed_objects,
        changed_phrase,
        check_entry_shape,
        restore_from_cache,
    )
    from .resolve import NotInCacheError, pull_again
    from .transport import pull_cache

    口 = Step(step_num=1, name="Check the sample's cache entry",
             purpose="A malformed entry is refused naming the re-registration, "
                     "pushed or not; a changed object is pulled again when the "
                     "sample is recorded pushed, and refused otherwise")
    where = f"sample '{name}' (content_hash={hash_val})"
    repair = sample_repair(name)
    stamp = layout.sample_stamp(project_root, name)
    # The copy is missing or changed: its stamp vouches for nothing now.
    stamp.unlink(missing_ok=True)

    try:
        check_entry_shape(project_root, hash_val, repair)
    except MalformedEntryError as exc:
        return str(exc)
    changed = changed_objects(project_root, hash_val, setting)
    if changed:
        if not pushed:
            return (f"its cache entry changed in the local cache "
                    f"({changed_phrase(changed)}) and was never pushed, so "
                    f"there is nothing to restore it from. {repair}")
        try:
            pull_again(project_root, hash_val, changed, where, repair,
                       "restore_sample", setting)
        except NotInCacheError as exc:
            return str(exc)

    口 = Step(step_num=2, name="Restore the view",
             purpose="Rename a read-only view of the checked entry over the "
                     "copy through restore_from_cache (a file's view, or the "
                     "checkout member by member), never deleting it first, "
                     "pulling on a local miss; stamped from what sits there")
    try:
        restored = restore_from_cache(hash_val, dest, project_root, stamp=stamp,
                                      setting=setting, check_dest=False)
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
            restored = restore_from_cache(hash_val, dest, project_root, stamp=stamp,
                                          setting=setting, check_dest=False)
    except ChangedEntryError as exc:
        # The bytes copied are not the sample's: nothing is placed or stamped.
        return f"{exc}; the copy was refused and nothing was restored. {repair}"

    if not restored and not reached:
        return (
            f"content_hash "
            f"{hash_val} is not in this machine's DVC cache, and the "
            f"archive could not be reached (see the WARNING above). The "
            f"content may well still be in the archive; wfc could not "
            f"ask. Check the project's DVC remote (`dvc remote list` in "
            f"{project_root}) and retry — do NOT re-register the sample "
            f"on the strength of this message."
        )
    if not restored:
        return (
            f"content_hash "
            f"{hash_val} is in neither this machine's DVC cache nor the "
            f"archive, so its bytes are nowhere wfc can reach. "
            f"Re-register it from its source with: "
            f"wfc register-sample --name {name} --source <path>"
        )

    口 = Step(step_num=3, name="Protect the folder",
             purpose="The folder the restore opened, and every directory under "
                     "it, is read-only again (POSIX; Windows ignores the "
                     "read-only attribute on a directory)")
    from .cache import protect_dirs
    protect_dirs(layout.sample_dir(project_root, name))
    return None


@task(purpose="Check a restored sample before a step reads it: a copy that is no "
              "longer what wfc registered is restored again from a checked cache "
              "entry, re-stamped, and named in a warning on stderr",
      inputs="the sample's name; its row (content hash, registered file name, "
             "push status); its restored copy and stamp; [integrity] inputs",
      outputs="a SampleCheck: intact, repaired (with the warning it printed), "
              "unchecked (no row or no copy) or refused with a message naming "
              "the sample")
def check_sample(name: str, project_root: Path | str | None = None,
                 setting: str | None = None,
                 pipeline_id: str | None = None) -> SampleCheck:
    """Make sure a sample's restored copy is what wfc registered, before it is read.

    The copy is asked the one integrity check against the sample's stamp:
    under ``stat-then-hash`` matching stat fields vouch for it, otherwise,
    and always under ``hash``, it is re-hashed. A copy with no stamp (one
    restored before stamps existed) is hashed and stamped; a copy a re-hash
    found intact that is still writable is then made read-only, so the
    next check re-hashes it once. A stamp that records a change time shows
    any chmod since it was written, so a copy it vouches for is not walked
    for a write bit; one vouched for by a stamp with no change time (FAT32,
    exFAT) is walked and protected as a re-hashed one is. A copy that is
    not what wfc registered is repaired through the restore path, which
    checks the sample's cache entry first (a malformed entry is refused
    naming the re-registration; a changed one is pulled again when pushed
    and refused otherwise) and stamps only a verified copy; a warning
    naming the sample goes to stderr. Outside a pipeline a missing copy is
    not restored here: the reader refuses it as not restored.

    Inside a pipeline the check first records the pipeline as holding the
    sample's view. The pipeline's restore rule does not run while another
    pipeline's sentinel stands, so this is the record that keeps that
    pipeline's end from removing the view this step reads. A copy missing
    after the record (the other pipeline's end removed it first) is
    restored here as the restore rule restores it.

    Args:
        name: The sample's name.
        project_root: Project root directory; the active project when None.
        setting: ``[integrity] inputs``; the project's when None.
        pipeline_id: The pipeline whose step reads it; ``WFC_PIPELINE_ID``
            when None, and no pipeline when that is unset too.

    Returns:
        The :class:`SampleCheck`.
    """
    root = Path(project_root) if project_root is not None else get_project_root()

    口 = Step(step_num=1, name="Find the restored copy",
             purpose="Read the sample's row and derive where its copy sits; "
                     "inside a pipeline, record the pipeline as holding the "
                     "sample's view first. No row or a malformed row is left "
                     "to the reader's own refusal, and so is no copy outside "
                     "a pipeline",
             critical="A row with no content hash is refused: there is nothing "
                      "to check the copy against. The record precedes the look "
                      "at the copy, so another pipeline's end either keeps the "
                      "view or has removed it before the look")
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == name)).first()
    if row is None:
        return SampleCheck(name, SAMPLE_UNCHECKED)
    try:
        dest = sample_data_path(root, row)
    except MalformedSampleRecordError:
        return SampleCheck(name, SAMPLE_UNCHECKED)
    assert dest is not None  # given a row, sample_data_path returns a path
    holder = _pipeline_holding(pipeline_id)
    if holder is not None:
        hold_for_pipeline(InUseKind.sample_view, name, holder)
    if not os.path.lexists(dest) and (holder is None or not row.content_hash):
        return SampleCheck(name, SAMPLE_UNCHECKED)
    if not row.content_hash:
        return SampleCheck(name, SAMPLE_REFUSED, (
            f"sample '{name}' is a malformed record — its row carries no "
            f"content hash, so wfc cannot check its restored copy. "
            f"{sample_repair(name)}"
        ))
    pushed = row.push_status == PushStatus.pushed.value
    if not os.path.lexists(dest):
        # Inside a pipeline: another pipeline's end removed the view after
        # this pipeline's rule saw its sentinel. Restore it as the rule does.
        problem = restore_verified(name, row.content_hash, pushed, dest, root,
                                   setting if setting is not None else inputs_setting(root))
        if problem is not None:
            return SampleCheck(name, SAMPLE_REFUSED, (
                f"sample '{name}' is not restored, and wfc could not restore "
                f"it: {problem}"))
        _touch_sentinel(root, name)
        from .cache import protect_dirs
        protect_dirs(layout.sample_dir(root, name))
        return SampleCheck(name, SAMPLE_INTACT)

    口 = Step(step_num=2, name="Check and protect the copy",
             purpose="The one integrity check against the sample's stamp; an "
                     "unstamped copy is hashed and stamped. A copy found intact "
                     "by a re-hash, or vouched for by a stamp with no change "
                     "time, that is still writable is made read-only; one a "
                     "stamp with a change time vouched for is not walked",
             critical="The protect step never re-stamps: the stamp the check "
                      "wrote holds the record taken before its hash, so a "
                      "write after the hash is found by the next check")
    setting = setting if setting is not None else inputs_setting(root)
    stamp = layout.sample_stamp(root, name)
    verdict = check_entry(dest, recorded_hash=row.content_hash, stamp=stamp,
                          setting=setting)
    if verdict.intact:
        # A vouching stamp that records a change time means no chmod since
        # it was written (a chmod moves it), so the copy is not walked. A
        # re-hashed copy, or one vouched for by a stamp with no change time
        # (FAT32, exFAT), may still carry a write bit.
        if verdict.hashed or not record_keeps_change_time(
                read_stamp(stamp, row.content_hash)):
            _protect_intact_copy(dest)
        return SampleCheck(name, SAMPLE_INTACT)

    口 = AutoStep(step_num=3, name="Repair from a checked cache entry")
    problem = restore_verified(name, row.content_hash, pushed, dest, root, setting)
    if problem is not None:
        return SampleCheck(name, SAMPLE_REFUSED, (
            f"sample '{name}' at {dest} is not what wfc registered, and wfc "
            f"could not restore it: {problem}"
        ))
    warning = (
        f"WARNING: sample '{name}' at {dest} was not what wfc registered "
        f"(content_hash {row.content_hash}); restored the registered bytes "
        f"before the step reads it."
    )
    print(warning, file=sys.stderr)
    return SampleCheck(name, SAMPLE_REPAIRED, warning=warning)


def _writable_anywhere(path: Path) -> bool:
    """Whether a copy, or any file or directory under it, carries a write bit."""
    if path.stat().st_mode & 0o222:
        return True
    if not path.is_dir():
        return False
    for base, dirs, files in os.walk(path):
        for entry in dirs + files:
            if os.lstat(os.path.join(base, entry)).st_mode & 0o222:
                return True
    return False


def _protect_intact_copy(dest: Path) -> None:
    """Make a copy a re-hash just found intact read-only when it is still writable.

    A copy restored before restores were read-only (a directory sample, or
    a file) may still be writable after it checks intact. The stamp is not
    written again: the one the check wrote holds the stat record taken
    before its hash, as every stamp does, so a write after that hash (before
    or after the chmod) leaves a record that does not match. The chmod moves
    the change time too, so the next check re-hashes once and stamps the
    read-only copy.

    Args:
        dest: The restored copy.
    """
    from .cache import _make_read_only

    try:
        if not _writable_anywhere(dest):
            return
        _make_read_only(dest)
    except OSError as exc:
        print(f"WARNING: could not protect restored copy {dest}: {exc}",
              file=sys.stderr)


def _touch_sentinel(project_root: Path, name: str) -> None:
    r"""Write the Snakemake readiness sentinel for a restored sample.

    Sample files live in the DVC cache rather than under
    ``.wfc/workspace/samples/<name>/``, so Snakemake cannot use the sample
    file itself as the restore rule's output. The rule's output is a
    sentinel at ``<project_root>/.wfc/workspace/samples/<name>/.sample_ready``; its parent
    directory is created if missing. The path is absolute via
    ``get_project_root()`` so it is cwd-independent, which matters on
    Windows UNC shell rules where ``cmd.exe`` rewrites cwd to ``C:\Windows``.

    Args:
        project_root: Project root directory.
        name: Sample identifier.
    """
    from .cache import open_dirs

    sentinel = layout.sample_ready_sentinel(project_root, name)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    open_dirs(sentinel.parent)
    sentinel.touch()


def _pipeline_holding(pipeline_id: str | None) -> str | None:
    """The pipeline a restore or a check runs in: the argument, else ``WFC_PIPELINE_ID``."""
    return pipeline_id if pipeline_id is not None else (os.environ.get("WFC_PIPELINE_ID") or None)


def remove_restored_view(project_root: Path, name: str,
                         content_hash: str | None = None) -> None:
    """Remove a restored sample's view: its folder (data and readiness sentinel) and stamp.

    The stamp goes first, so a view left half-removed is re-checked before
    use. No cache object is chmodded; with the sample's ``content_hash``
    each object's stamp is kept current (:func:`~wfc.storage.cache.remove_view`),
    so the next restore costs no re-hash.

    Args:
        project_root: Project root directory.
        name: The sample's name.
        content_hash: The sample's entry, whose objects' stamps are kept;
            None when the entry is being removed too.

    Raises:
        OSError: When the folder cannot be removed; what is left stays.
    """
    from .cache import remove_view

    layout.sample_stamp(project_root, name).unlink(missing_ok=True)
    remove_view(layout.sample_dir(project_root, name),
                project_root if content_hash else None, content_hash or None)


@task(purpose="End of a pipeline: remove the restored sample views the pipeline "
              "held that no other pipeline holds, with their readiness sentinels "
              "and stamps, and release the pipeline's in-use records",
      inputs="the project root, the pipeline id; the pipeline's in-use records",
      outputs="the names of the samples whose views were removed; the cache "
              "entries untouched",
      critical="Each view is removed inside remove_if_unheld's one transaction, "
               "so a pipeline still reading the sample keeps it, and one that "
               "records after the removal makes the view again")
def end_pipeline_samples(project_root: Path | str, pipeline_id: str) -> list[str]:
    """Remove the views of the samples a pipeline restored or read, unless another pipeline holds them.

    Only the views go: each sample's folder under
    ``.wfc/workspace/samples/`` (its data and ``.sample_ready`` sentinel)
    and its stamp. The cache entry keeps the bytes, so the next pipeline's
    restore makes the view again, at no copy cost where the filesystem
    links. A view whose removal fails is named on stderr and left; the
    others still go, and every record of the pipeline is released.

    Args:
        project_root: Project root directory.
        pipeline_id: The pipeline that ended (succeeded, failed or cancelled).

    Returns:
        The names of the samples whose views were removed, sorted.
    """
    root = Path(project_root)
    removed: list[str] = []
    for name in pipeline_keys(InUseKind.sample_view, pipeline_id):
        with get_session() as session:
            row = session.exec(select(Sample).where(Sample.name == name)).first()
        content_hash = row.content_hash if row is not None else None

        def remove(name: str = name, content_hash: str | None = content_hash) -> None:
            remove_restored_view(root, name, content_hash)

        try:
            if remove_if_unheld(InUseKind.sample_view, name, remove,
                                pipeline_id=pipeline_id):
                removed.append(name)
        except OSError as exc:
            print(f"WARNING: could not remove the restored sample '{name}' at "
                  f"{layout.sample_dir(root, name)}: {exc}; the next pipeline "
                  f"checks it before reading.", file=sys.stderr)
    release_pipeline(pipeline_id)
    return removed
