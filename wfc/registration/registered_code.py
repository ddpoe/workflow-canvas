"""Registered code: a method's versions, their code, and the snapshot guard.

A method's staging snapshot lives at
``.wfc/registry/methods/<module>/<name>/`` and its version list beside it
(Layout owns both paths). A run never executes the snapshot: it executes a
version's committed tree, extracted under ``.wfc/runs/code/<tree hash>/``.

- ``restore_changed_snapshot``: registration's guard. A snapshot that
  differs from its newest version's commit was changed outside wfc; it is
  restored to that version and the registration stops.
- ``resolve_method_code``: the one resolver every reader of a method's code
  calls. It takes a version (the newest when none is named), extracts that
  version's tree into its verified copy and answers the script the copy
  selects.
- ``verify_registered_snapshot``: the verify rule. The version's extracted
  copy must fingerprint as the version records; an edit to the staging
  snapshot never reaches a run.
- ``registered_env`` and ``method_contract``: a version's env and contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from axiom_annotations import AutoStep, Step, task
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from .. import layout
from ..persistence import Method, MethodContract, MethodVersion, Module, get_session
from ..persistence import project_root as get_project_root
from ..version import (
    HeadMovedError,
    commit_over_head,
    commit_with_tree,
    diff_path_against_commit,
    head_commit,
    read_blob,
    restore_path_from_commit,
    tree_at,
)
from .advice import issues_url
from .method_version import (
    VersionEntry,
    as_utc,
    newest_entry,
    read_version_list,
    snapshot_code_fingerprint,
)

_V = TypeVar("_V")


class SnapshotChangedError(ValueError):
    """A method's registered copy was changed outside wfc (and was restored)."""


class SnapshotPresentError(ValueError):
    """A first registration found the snapshot directory already present."""


class MalformedMethodRecordError(ValueError):
    """A method's record does not lead to code a run can execute."""


class UnknownVersionError(MalformedMethodRecordError):
    """A version identity names no entry of the method's version list."""


class UnregisteredSnapshotError(ValueError):
    """A method's code is not the code its registered version recorded."""


@dataclass(frozen=True)
class MethodCode:
    """Where a method version's code is.

    Attributes:
        method_id: The ``Method`` row's id.
        version_id: The version's ``MethodVersion`` row's id.
        entry: The version's list entry.
        code_dir: The version's extracted, verified copy,
            ``.wfc/runs/code/<tree hash>/``: the directory a run executes.
        script: The script to execute, inside ``code_dir``.
        snapshot_dir: The method's staging snapshot directory (what the
            next registration compares with); never executed.
        git_commit: The registration commit the version's row records, or
            ``None`` for a version this database did not see registered.
    """

    method_id: int
    version_id: int
    entry: VersionEntry
    code_dir: Path
    script: Path
    snapshot_dir: Path
    git_commit: str | None


@dataclass(frozen=True)
class RegisteredCode:
    """A version's code the verify rule accepted.

    Attributes:
        code: Where the version's code is.
        version_id: The version's ``MethodVersion`` row's id.
        code_fingerprint: The version's code fingerprint (its copy's).
        git_commit: The registration commit the version records.
    """

    code: MethodCode
    version_id: int
    code_fingerprint: str
    git_commit: str | None


def _repair(module: str, method: str) -> str:
    return (f"Register the method again with `wfc register-method` "
            f"(module '{module}', method '{method}').")


def _find_method(session, module: str, method: str) -> Method | None:
    return session.exec(
        select(Method).join(Module).where(Module.name == module,
                                          Method.name == method)
    ).first()


@dataclass(frozen=True)
class SyncedVersion:
    """A version-list entry and the ``MethodVersion`` row that mirrors it.

    Attributes:
        entry: The list entry.
        version_id: Its row's id.
        git_commit: The registration commit the row records, or ``None``
            for an entry this database did not see registered.
    """

    entry: VersionEntry
    version_id: int
    git_commit: str | None


def no_version_list_message(module: str, method: str, root: Path) -> str:
    """Return the refusal for a registered method that has no version list.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root.

    Returns:
        The message, naming the list's path and ``wfc register-method``.
    """
    return (
        f"Method '{module}/{method}' has no version list "
        f"({layout.method_versions_path(root, module, method)}), so its record "
        f"is incomplete and no version of it can run. {_repair(module, method)}"
    )


def _matches(row: MethodVersion, entry: VersionEntry) -> bool:
    """Is *row* the row of *entry*: same identity and registration time, any label."""
    return (row.code_fingerprint == entry.code_fingerprint
            and row.env_fingerprint == entry.env_fingerprint
            and row.recorded_at is not None
            and as_utc(row.recorded_at) == entry.registered_datetime)


def _contract_from_tree(root: Path, entry: VersionEntry) -> dict | None:
    """Parse the ``method.yaml`` the entry's git tree holds, or ``None``."""
    import tempfile

    from ..contracts import parse_method_yaml

    text = read_blob(root, entry.tree_hash, "method.yaml")
    if text is None:
        return None
    with tempfile.TemporaryDirectory(prefix="wfc-contract-") as tmp:
        Path(tmp, "method.yaml").write_text(text, encoding="utf-8")
        return parse_method_yaml(Path(tmp))


def _add_contract(session, method_id: int, version_id: int, data: dict | None) -> None:
    if data is None:
        return
    session.add(MethodContract(
        method_id=method_id, version_id=version_id,
        input_slots=data["inputs"], output_slots=data["outputs"],
        params_schema=data["params"], executor=data["executor"],
    ))


@task(purpose="Make a method's MethodVersion rows follow its version list: create "
              "a row (and its contract, parsed from the entry's git tree) for an "
              "entry with none, move labels after a renumbering, and take the "
              "label off a row whose entry left the list",
      inputs="module name, method name, project root; optionally the entry a "
             "registration just committed, with its commit and contract",
      outputs="the list's entries with their rows, in label order",
      critical="Entries match rows by content identity and registration time, "
               "never by label, so Run.version_id keeps pointing at the same "
               "entry. Registration is the only writer of these rows.")
def sync_version_rows(module: str, method: str, root: Path | None = None, *,
                      registered: VersionEntry | None = None,
                      registered_commit: str | None = None,
                      registered_contract: dict | None = None) -> list[SyncedVersion]:
    """Reconcile a method's version rows with its version list and return them.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root; defaults to the resolved project root.
        registered: The entry a registration just committed; its row takes
            *registered_commit* and *registered_contract* rather than a
            contract parsed from git.
        registered_commit: That registration's commit.
        registered_contract: That registration's parsed ``method.yaml``.

    Returns:
        One :class:`SyncedVersion` per list entry, in label order.

    Raises:
        MalformedMethodRecordError: The method is not registered, or has no
            version list. The message names ``wfc register-method``.
    """
    root = Path(root) if root is not None else get_project_root()
    entries = read_version_list(root, module, method)
    for attempt in range(2):
        with get_session() as session:
            synced = sync_rows_in(session, module, method, entries, root,
                                  registered=registered,
                                  registered_commit=registered_commit,
                                  registered_contract=registered_contract)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if attempt == 0:
                    continue  # another process synced first: read again
                raise
            return synced
    raise AssertionError("unreachable")  # pragma: no cover


def sync_rows_in(session, module: str, method: str, entries: list[VersionEntry] | None,
                 root: Path, *, registered: VersionEntry | None = None,
                 registered_commit: str | None = None,
                 registered_contract: dict | None = None) -> list[SyncedVersion]:
    """Make the method's version rows follow *entries* inside *session*, uncommitted.

    The rows :func:`sync_version_rows` writes, flushed in the caller's
    transaction: registration writes its version row in the transaction
    that writes its method rows.

    Args:
        session: An open session; the caller commits it.
        module: The method's module name.
        method: The method's name.
        entries: The version list's entries, or ``None`` without a list.
        root: The project root.
        registered: The entry a registration just committed.
        registered_commit: That registration's commit.
        registered_contract: That registration's parsed ``method.yaml``.

    Returns:
        One :class:`SyncedVersion` per entry, in label order.

    Raises:
        MalformedMethodRecordError: The method is not registered, or has no
            version list.
    """
    row = _find_method(session, module, method)
    if row is None:
        raise MalformedMethodRecordError(
            f"Method '{module}/{method}' is not registered. "
            f"{_repair(module, method)}")
    if entries is None:
        raise MalformedMethodRecordError(
            no_version_list_message(module, method, root))
    method_id = row.id
    assert method_id is not None  # a stored row
    rows = list(session.exec(
        select(MethodVersion).where(MethodVersion.method_id == method_id)))
    matched: dict[int, MethodVersion] = {}
    for entry in entries:
        found = next((r for r in rows if _matches(r, entry)
                      and r.id not in {m.id for m in matched.values()}), None)
        if found is not None:
            matched[entry.label] = found
    # Phase 1: free every label that moves or whose entry is gone.
    keep = {id(r): label for label, r in matched.items()}
    changed = False
    for r in rows:
        if r.label is not None and keep.get(id(r)) != r.label:
            r.label = None
            session.add(r)
            changed = True
    if changed:
        session.flush()
    # Phase 2: give matched rows their labels; create missing rows.
    for entry in entries:
        r = matched.get(entry.label)
        is_registered = registered is not None and entry == registered
        if r is None:
            r = MethodVersion(
                method_id=method_id, label=entry.label,
                code_fingerprint=entry.code_fingerprint,
                env_name=entry.env_name,
                env_fingerprint=entry.env_fingerprint,
                image_ref=entry.image_ref, tree_hash=entry.tree_hash,
                recorded_at=entry.registered_datetime,
                git_commit=registered_commit if is_registered else None,
            )
            session.add(r)
            session.flush()
            assert r.id is not None  # assigned by the flush
            contract = (registered_contract if is_registered
                        else _contract_from_tree(root, entry))
            _add_contract(session, method_id, r.id, contract)
            matched[entry.label] = r
        else:
            if r.label != entry.label:
                r.label = entry.label
            if is_registered and r.git_commit is None:
                r.git_commit = registered_commit
            if r.tree_hash is None:
                r.tree_hash = entry.tree_hash
            session.add(r)
    session.flush()
    synced: list[SyncedVersion] = []
    for e in entries:
        r = matched[e.label]
        assert r.id is not None  # stored or flushed above
        synced.append(SyncedVersion(entry=e, version_id=r.id, git_commit=r.git_commit))
    return synced


def newest_version(module: str, method: str, root: Path | None = None) -> SyncedVersion:
    """Return a method's newest version (its highest label), rows synced.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root; defaults to the resolved project root.

    Returns:
        The newest version.

    Raises:
        MalformedMethodRecordError: Not registered, no list, or an empty one.
    """
    versions = sync_version_rows(module, method, root)
    if not versions:
        raise MalformedMethodRecordError(
            no_version_list_message(module, method,
                                    Path(root) if root else get_project_root()))
    return versions[-1]


@task(purpose="Return a method version's contract (the newest version when none "
              "is named), rows synced from the version list first",
      inputs="module name, method name, optional version identity, project root",
      outputs="the version's MethodContract row (detached), or None when the "
              "version has none")
def method_contract(module: str, method: str, version: str | None = None,
                    root: Path | None = None) -> MethodContract | None:
    """Return the contract a method version was registered with.

    Args:
        module: The method's module name.
        method: The method's name.
        version: A version's content identity; the newest entry carrying it
            is read. ``None`` reads the newest version.
        root: The project root; defaults to the resolved project root.

    Returns:
        The contract row, detached from its session, or ``None``.

    Raises:
        MalformedMethodRecordError: Not registered or no version list.
        UnknownVersionError: No entry carries that identity.
    """
    root = Path(root) if root is not None else get_project_root()
    versions = sync_version_rows(module, method, root)
    chosen = _require_version(versions, module, method, version, root)
    with get_session() as session:
        row = session.exec(select(MethodContract).where(
            MethodContract.version_id == chosen.version_id)).first()
        if row is not None:
            session.expunge(row)
        return row


def version_contracts(module: str, method: str, root: Path | None = None
                      ) -> list[tuple[SyncedVersion, MethodContract | None]]:
    """Return every version of a method with its contract, in label order.

    Rows are synced from the version list first, so an entry that arrived by
    ``git pull`` has its row and its contract (parsed from the entry's tree).

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root; defaults to the resolved project root.

    Returns:
        ``(version, contract)`` per list entry; the contract row is detached
        from its session, or ``None`` when the version has none.

    Raises:
        MalformedMethodRecordError: Not registered or no version list.
    """
    versions = sync_version_rows(module, method, root)
    with get_session() as session:
        by_version = contracts_by_version(session, [v.version_id for v in versions])
        for row in by_version.values():
            session.expunge(row)
    return [(v, by_version.get(v.version_id)) for v in versions]


def contracts_by_version(session, version_ids: Iterable[int]) -> dict[int, MethodContract]:
    """Read the contracts of method version rows, by version row id, as stored.

    The read behind :func:`version_contracts`, without its sync: a caller
    that already holds version row ids (a run records the one it ran) reads
    those versions' contracts as registration stored them, in its own
    session, and nothing is written.

    Args:
        session: An open database session.
        version_ids: ``MethodVersion`` row ids.

    Returns:
        ``{version_id: MethodContract}`` for each id with a contract row; a
        version with none is absent.
    """
    ids = sorted(set(version_ids))
    if not ids:
        return {}
    rows = session.exec(select(MethodContract).where(
        col(MethodContract.version_id).in_(ids)))
    return {row.version_id: row for row in rows}


def _copy_holds(copy_dir: Path, code_fingerprint: str) -> bool:
    """Does *copy_dir* exist and fingerprint as *code_fingerprint*."""
    if not copy_dir.is_dir():
        return False
    try:
        return snapshot_code_fingerprint(copy_dir) == code_fingerprint
    except ValueError:
        return False


def _write_stamp(stamp: Path, code_fingerprint: str) -> None:
    """Write the copy's stamp atomically (a temporary sibling, then replace)."""
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=stamp.parent, prefix=f".{stamp.name}-")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(code_fingerprint + "\n")
    os.replace(tmp, stamp)


@task(purpose="Extract a method version's registered tree into its shared code "
              "copy under .wfc/runs/code/<tree hash>/, verified against the "
              "version's code fingerprint and stamped; reuse a copy whose stamp "
              "and fingerprint hold",
      inputs="module name, method name, the version-list entry, project root",
      outputs="the verified copy's directory",
      critical="git archive into a temporary sibling, verified, then renamed "
               "into place: a parallel extractor that loses the rename uses the "
               "winner's copy. Never reads or writes the live git index.")
def extract_version_copy(module: str, method: str, entry: VersionEntry,
                         root: Path) -> Path:
    """Return the verified extracted copy of a method version's code.

    The copy is keyed by the version's tree hash, so two versions with the
    same tree share it. A copy whose stamp records the version's code
    fingerprint and whose files still fingerprint as it is reused. A missing
    copy is extracted; a damaged one (its files no longer fingerprint as
    the version) is replaced.

    Args:
        module: The method's module name.
        method: The method's name.
        entry: The version-list entry to extract.
        root: The project root.

    Returns:
        The copy's directory, ``.wfc/runs/code/<tree hash>/``.

    Raises:
        MalformedMethodRecordError: git no longer holds the version's tree.
        UnregisteredSnapshotError: The tree git holds does not fingerprint as
            the version records.
    """
    import os
    import shutil
    import tempfile

    from ..version import archive_tree

    口 = Step(step_num=1, name="Reuse a verified copy",
             purpose="The stamp records the version's fingerprint and the "
                     "files still fingerprint as it")
    target = layout.code_copy_dir(root, entry.tree_hash)
    stamp = layout.code_copy_stamp(root, entry.tree_hash)
    stamped = (stamp.is_file()
               and stamp.read_text(encoding="utf-8").strip() == entry.code_fingerprint)
    if stamped and _copy_holds(target, entry.code_fingerprint):
        return target

    口 = Step(step_num=2, name="Extract into a temporary sibling and verify",
             purpose="git archive of the entry's tree; refused naming the "
                     "version when git no longer holds the tree")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{entry.tree_hash}-"))
    try:
        if not archive_tree(root, entry.tree_hash, tmp):
            raise MalformedMethodRecordError(
                f"Version v{entry.label} of method '{module}/{method}' cannot be "
                f"run: git no longer holds its code (tree {entry.tree_hash}; the "
                f"history that held it was rewritten). Register the method again "
                f"with `wfc register-method`, or pin another version."
            )
        if not _copy_holds(tmp, entry.code_fingerprint):
            raise UnregisteredSnapshotError(
                f"Version v{entry.label} of method '{module}/{method}': the code "
                f"git holds for it (tree {entry.tree_hash}) is not the code its "
                f"version list records. {_repair(module, method)}"
            )

        口 = Step(step_num=3, name="Rename into place, or use the winner's copy",
                 purpose="A damaged copy is removed first; a concurrent "
                         "extractor's verified copy is used as it stands")
        for _attempt in range(2):
            try:
                os.rename(tmp, target)
                break
            except OSError:
                if _copy_holds(target, entry.code_fingerprint):
                    break  # another extractor won; tmp is discarded below
                shutil.rmtree(target, ignore_errors=True)
        else:
            raise RuntimeError(f"Could not move the extracted copy into {target}.")
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    口 = Step(step_num=4, name="Stamp the copy",
             purpose="The stamp beside the copy records the fingerprint it holds")
    _write_stamp(stamp, entry.code_fingerprint)
    return target


def changed_snapshot_message(module: str, method: str) -> str:
    """Return the message a registration that restored a snapshot stops with.

    Args:
        module: The method's module name.
        method: The method's name.

    Returns:
        The message.
    """
    return (
        f"The registered copy of `{module}/{method}` was changed outside wfc. "
        f"The changes above were reverted. Make changes in your source and run "
        f"`wfc register-method`. If you edited the registered copy for a reason "
        f"wfc doesn't support, please open an issue: {issues_url()}."
    )


@task(purpose="Before a registration writes, put back a snapshot changed outside "
              "wfc: restore the latest registered version, commit the restore "
              "naming only the snapshot, print the discarded diff and stop",
      inputs="module name, method name, project root",
      outputs="None when the snapshot is as registered (or absent with no "
              "version); raises otherwise",
      critical="Runs before any row is written, so a stopped registration "
               "leaves no rows. A first registration that finds the snapshot "
               "directory present removes nothing.")
def restore_changed_snapshot(module: str, method: str, root: Path) -> None:
    """Restore a snapshot changed outside wfc, then stop the registration.

    The snapshot is compared with the newest entry of the method's version
    list: with a commit whose tree at the snapshot path is that entry's
    tree hash (the commit the entry's row records, else ``HEAD``, else the
    newest such commit in the snapshot's history), so an entry that arrived
    by ``git pull``, whose row holds no commit, is compared too. Any
    difference (a committed hand edit, an uncommitted one, a stray file) is
    reverted: the snapshot is checked out from that commit, the restore is
    committed by pathspec naming only the snapshot (a registration commit),
    and the discarded diff is printed.

    When no commit holds the entry's tree (history rewritten and
    garbage-collected), there is nothing to compare with: the registration
    proceeds and rebuilds the snapshot from the source.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root.

    Raises:
        SnapshotChangedError: The snapshot was changed and has been restored.
        SnapshotPresentError: The method has no version list and was never
            registered, but the snapshot directory exists; nothing is removed.
    """
    口 = Step(step_num=1, name="Find the newest version",
             purpose="The version list's highest label, and the commit its row "
                     "records, if any")
    snapshot_dir = layout.method_dir(root, module, method)
    newest = newest_entry(read_version_list(root, module, method))
    row_commit = None
    with get_session() as session:
        row = _find_method(session, module, method)
        if row is not None and newest is not None:
            for version in session.exec(select(MethodVersion).where(
                    MethodVersion.method_id == row.id)):
                if _matches(version, newest):
                    row_commit = version.git_commit
        registered_before = row is not None and session.exec(
            select(MethodVersion).where(MethodVersion.method_id == row.id)
        ).first() is not None

    口 = Step(step_num=2, name="Refuse a snapshot no version accounts for",
             purpose="With no version list and no registration on record, a "
                     "present snapshot directory is not wfc's to judge; nothing "
                     "is removed",
             critical="A first registration that finds the directory present "
                      "removes nothing")
    if newest is None:
        if snapshot_dir.exists() and not registered_before:
            raise SnapshotPresentError(
                f"Cannot register '{module}/{method}': {snapshot_dir} already "
                f"exists, but no version of this method is registered, so "
                f"wfc cannot tell what it holds. Move or delete that directory "
                f"yourself, then run `wfc register-method` again."
            )
        return

    口 = Step(step_num=3, name="Find a commit holding the newest version's tree",
             purpose="The row's commit, else HEAD, else the snapshot's history; "
                     "with none, nothing can be compared with and the "
                     "registration proceeds")
    commit = commit_with_tree(root, snapshot_dir, newest.tree_hash,
                              [c for c in (row_commit, "HEAD") if c])
    if commit is None:
        return

    口 = Step(step_num=4, name="Compare the snapshot with the version's commit",
             purpose="A committed edit, an uncommitted one and a stray file all "
                     "show in the diff")
    diff = diff_path_against_commit(root, commit, snapshot_dir)
    if not diff:
        return

    口 = Step(step_num=5, name="Restore, commit the restore, print the diff and stop",
             purpose="The restore commit names only the snapshot; the "
                     "registration stops so the author re-registers from source")
    restore_path_from_commit(root, commit, snapshot_dir)
    restored = tree_at(root, commit, snapshot_dir)
    assert restored is not None  # commit_with_tree found the tree there
    for _attempt in range(10):
        try:
            commit_over_head(root, head_commit(root), trees={snapshot_dir: restored},
                             message=f"Register method {module}/{method}: "
                                     f"restore the registered copy")
            break
        except HeadMovedError:
            continue
    print(diff, end="" if diff.endswith("\n") else "\n")
    raise SnapshotChangedError(changed_snapshot_message(module, method))


def chosen_version(versions: Sequence[_V], identity: str | None,
                   entry_of: Callable[[_V], VersionEntry] | None = None,
                   ) -> _V | None:
    """Pick the version a caller names from a method's versions.

    wfc's one version-selection rule: the newest version when no identity is
    given, else the newest entry that carries the identity. Registration's
    readers, the document validator and a claim's params door all pick
    through it.

    Args:
        versions: The method's versions in label order (oldest first):
            ``SyncedVersion``s, or items ``entry_of`` maps to their list
            entry (``version_contracts``' ``(version, contract)`` pairs, or
            the ``VersionEntry``s themselves).
        identity: A version's content identity (a node's pin, a claim's
            version), or ``None`` for the newest.
        entry_of: Maps an item of ``versions`` to its ``VersionEntry``;
            ``None`` when the items are ``SyncedVersion``s.

    Returns:
        The chosen item, or ``None`` when ``versions`` is empty or no entry
        carries ``identity``.
    """
    if not versions:
        return None
    if identity is None:
        return versions[-1]
    as_entry = entry_of or (lambda item: cast(SyncedVersion, item).entry)
    return next((item for item in reversed(versions)
                 if as_entry(item).identity == identity), None)


def _require_version(versions: list[SyncedVersion], module: str, method: str,
                     version: str | None, root: Path) -> SyncedVersion:
    """Pick the version a caller names, refusing when there is none.

    The selection is :func:`chosen_version`'s; this adds Registration's
    refusals.

    Args:
        versions: The method's versions, in label order.
        module: The method's module name.
        method: The method's name.
        version: A version's content identity, or ``None`` for the newest.
        root: The project root.

    Returns:
        The chosen version.

    Raises:
        MalformedMethodRecordError: The list is empty.
        UnknownVersionError: No entry carries *version*.
    """
    if not versions:
        raise MalformedMethodRecordError(no_version_list_message(module, method, root))
    chosen = chosen_version(versions, version)
    if chosen is None:
        labels = ", ".join(f"v{v.entry.label}" for v in versions)
        raise UnknownVersionError(
            f"Method '{module}/{method}' has no version {version} in its version "
            f"list (its versions: {labels}). Pin one of its versions, or leave "
            f"the node unpinned to run the newest."
        )
    return chosen


@task(purpose="Resolve a method version's code: the version's extracted, "
              "verified copy under .wfc/runs/code/<tree hash>/ and the script "
              "that copy selects; the newest version when none is named",
      inputs="module name, method name, optional project root, optional "
             "version identity (keyword)",
      outputs="MethodCode (method id, version id, list entry, copy, script, "
              "staging snapshot)",
      critical="A run executes the version's committed tree, never the "
               "staging snapshot: an edit to the snapshot never reaches a run. "
               "No list, an unknown version and a tree git no longer holds are "
               "refused.")
def resolve_method_code(module: str, method: str, root: Path | None = None, *,
                        version: str | None = None) -> MethodCode:
    """Return where a method version's code is, extracting it when needed.

    The version is the newest list entry carrying *version* (a content
    identity); ``None`` is the newest entry. Its tree is extracted by
    :func:`extract_version_copy` (reused while its stamp and fingerprint
    hold), and the script is the one that copy selects on its own (its
    ``method.yaml`` ``script:`` key, else the ``{method}.<ext>`` probe), so
    a version registered with ``--script`` runs that script.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root; defaults to the resolved project root.
        version: A version's content identity; ``None`` is the newest.

    Returns:
        The version's copy and script.

    Raises:
        MalformedMethodRecordError: The method is not registered, has no
            version list, git no longer holds the version's tree (the
            message names the version), or the copy selects no script. The
            message names ``wfc register-method``.
        UnknownVersionError: No entry of the list carries *version*.
        UnregisteredSnapshotError: The tree git holds is not the code the
            version records.
    """
    from .discovery import snapshot_selected_script

    root = Path(root) if root is not None else get_project_root()

    口 = AutoStep(step_num=1, name="Sync the version rows with the list")
    versions = sync_version_rows(module, method, root)

    口 = Step(step_num=2, name="Pick the version",
             purpose="The newest entry carrying the named identity, or the "
                     "newest entry when none is named")
    chosen = _require_version(versions, module, method, version, root)

    口 = AutoStep(step_num=3, name="Extract the version's verified copy")
    code_dir = extract_version_copy(module, method, chosen.entry, root)

    口 = Step(step_num=4, name="Find the script the copy selects",
             purpose="The copy's own selection, so each version runs the "
                     "script it was registered with")
    selected = snapshot_selected_script(code_dir, method)
    if selected is None:
        raise MalformedMethodRecordError(
            f"Version v{chosen.entry.label} of method '{module}/{method}' selects "
            f"no script inside its code ({code_dir}). {_repair(module, method)}"
        )
    script = code_dir / selected.relative_to(code_dir.resolve())
    with get_session() as session:
        found = _find_method(session, module, method)
        if found is None or found.id is None:
            raise MalformedMethodRecordError(
                f"Method '{module}/{method}' is not registered. "
                f"{_repair(module, method)}")
        method_id = found.id
    return MethodCode(method_id=method_id, version_id=chosen.version_id,
                      entry=chosen.entry, code_dir=code_dir, script=script,
                      snapshot_dir=layout.method_dir(root, module, method),
                      git_commit=chosen.git_commit)


@task(purpose="Read the env a method version runs in and is keyed on: the "
              "env its version-list entry records (the newest entry when no "
              "version is named), never a document's or the staging "
              "snapshot's method.yaml",
      inputs="module name, method name, optional version identity, project root",
      outputs="the env name, or '' when the method is not registered or has "
              "no readable version list")
def registered_env(module: str, method: str, version: str | None = None, *,
                   root: Path | None = None) -> str:
    """Return the env a method version is registered in.

    It is the env registration validated against the manifest and recorded
    in the version's entry; dispatch and the pre-flights run the method in
    it. Only the list is read, so a missing or changed staging snapshot does
    not change the answer.

    Args:
        module: The method's module name.
        method: The method's name.
        version: A version's content identity; ``None`` is the newest.
        root: The project root; defaults to the resolved project root.

    Returns:
        The env name, or ``""`` when the method is not registered or has no
        readable version list (the claim reports that refusal).

    Raises:
        UnknownVersionError: No entry of the list carries *version*.
    """
    entry = version_entry(module, method, version, root=root)
    return entry.env_name if entry is not None else ""


def version_entry(module: str, method: str, version: str | None = None, *,
                  root: Path | None = None) -> VersionEntry | None:
    """Return a method version's list entry, without extracting its code.

    The entry records what the version runs in: its env name, the env
    fingerprint its key is over and the image reference a run launches.

    Args:
        module: The method's module name.
        method: The method's name.
        version: A version's content identity; ``None`` is the newest.
        root: The project root; defaults to the resolved project root.

    Returns:
        The newest entry carrying *version* (the newest entry when ``None``),
        or ``None`` when the method is not registered or has no readable
        version list (the claim reports that refusal).

    Raises:
        UnknownVersionError: No entry of the list carries *version*.
    """
    root = Path(root) if root is not None else get_project_root()
    with get_session() as session:
        if _find_method(session, module, method) is None:
            return None
    try:
        entries = read_version_list(root, module, method)
    except ValueError:
        return None
    if not entries:
        return None
    chosen = chosen_version(entries, version, entry_of=lambda e: e)
    if chosen is None:
        labels = ", ".join(f"v{e.label}" for e in entries)
        raise UnknownVersionError(
            f"Method '{module}/{method}' has no version {version} in its version "
            f"list (its versions: {labels})."
        )
    return chosen


def copy_file_digests(code_dir: Path) -> dict[str, str]:
    """Return a SHA-256 digest of every file under an extracted copy.

    Dispatch takes it before and after a run whose backend could not bind
    the copy read-only, to name any file the run wrote, changed or removed
    there (``__pycache__`` included).

    Args:
        code_dir: The extracted copy.

    Returns:
        ``{POSIX path relative to the copy: hex digest}``.
    """
    import hashlib

    code_dir = Path(code_dir)
    return {p.relative_to(code_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(code_dir.rglob("*")) if p.is_file()}


def changed_copy_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Name the files that differ between two :func:`copy_file_digests` reads.

    Args:
        before: The digests taken before the run.
        after: The digests taken after it.

    Returns:
        Every path added, removed or changed, sorted.
    """
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def drop_copy_stamp(entry: VersionEntry, root: Path | None = None) -> None:
    """Remove an extracted copy's verified stamp, so its next use re-extracts it.

    Args:
        entry: The version whose copy is no longer trusted.
        root: The project root; defaults to the resolved project root.
    """
    root = Path(root) if root is not None else get_project_root()
    layout.code_copy_stamp(root, entry.tree_hash).unlink(missing_ok=True)


@task(purpose="The verify rule: a method version's code is its extracted copy, "
              "and that copy fingerprints as the version's code fingerprint",
      inputs="module name, method name, optional project root, optional "
             "version identity (keyword)",
      outputs="RegisteredCode (resolved code, version id, fingerprint, commit)")
def verify_registered_snapshot(module: str, method: str,
                               root: Path | None = None, *,
                               version: str | None = None) -> RegisteredCode:
    """Return the registered version a run of a method executes, verified.

    The verify rule applies to the version's extracted copy, not to the
    staging snapshot: :func:`resolve_method_code` extracts the version's
    committed tree and accepts it only when it fingerprints as the version
    records, re-extracting a damaged copy. An edit to the staging snapshot,
    committed or not, or a stray file in it, never reaches a run.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The project root; defaults to the resolved project root.
        version: A version's content identity; ``None`` is the newest.

    Returns:
        The resolved code and the version it is.

    Raises:
        MalformedMethodRecordError: The resolver refused the record (no
            list, a tree git no longer holds, no script selected).
        UnknownVersionError: No entry carries *version*.
        UnregisteredSnapshotError: The tree git holds is not the code the
            version records. The message names ``wfc register-method``.
    """
    code = resolve_method_code(module, method, root, version=version)
    return RegisteredCode(code=code, version_id=code.version_id,
                          code_fingerprint=code.entry.code_fingerprint,
                          git_commit=code.git_commit)
