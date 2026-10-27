"""The registration git commit: verify the tree, commit it, put the registry in step.

A registration builds its snapshot in a staging directory. Before anything
is committed, the tree git would commit for that copy at the snapshot's path
is built and extracted as a run extracts it; any file it would leave out or
change refuses the registration, naming the file and the reason. The commit
is made from a temporary index holding HEAD's tree, the snapshot's tree, the
version list and, when HEAD's ``.gitattributes`` lacks it, the registry's
``-text`` line, through ``git commit`` (Project git). A HEAD that moved
meanwhile is re-read and the version takes the next label. Then the working
tree and the index are set to HEAD at the registry paths, so ``git status``
shows nothing new and what the user staged elsewhere stays staged.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import task

from .. import layout
from ..version import (
    GitObject,
    HeadMovedError,
    NotAGitRepositoryError,
    add_gitattributes_lines,
    archive_tree,
    commit_over_head,
    git_toplevel,
    head_commit,
    index_matches,
    object_exists,
    objects_at,
    set_index_to,
    tree_as_if_at,
    tree_at,
    tree_differences,
)
from .method_version import (
    VersionEntry,
    next_entry,
    parse_version_list,
    read_version_list,
    render_version_list,
    write_version_list_bytes,
)

#: How often a registration rebuilds on a HEAD that moved before giving up.
HEAD_MOVED_ATTEMPTS = 10


@dataclass(frozen=True)
class RegistrationCommit:
    """What :func:`commit_registration` committed.

    Attributes:
        entry: The version-list entry it appended, or ``None`` when it added
            no version (the same content as the newest version).
        commit: The registration commit's SHA, or ``None`` when nothing was
            committed (HEAD already held the newest version's tree).
    """

    entry: VersionEntry | None
    commit: str | None


def _refusal(module: str, method: str, differences) -> ValueError:
    lines = "\n".join(f"  {d.path}: {d.reason}" for d in differences)
    return ValueError(
        f"Cannot register '{module}/{method}': git would not commit its "
        f"registered copy as registration checked it, so this version would "
        f"not run:\n{lines}\nNothing was committed, and the registered copy, "
        f"its version list and your git index are as they were. Remove the "
        f"rule or attribute that matches each file (or leave the file out of "
        f"the method) and run `wfc register-method` again."
    )


def _blob(held: GitObject | None) -> bytes | None:
    """The bytes of *held* when it is a file, else ``None``."""
    return held.data if held is not None and held.kind == "blob" else None


def _list_at(root: Path, base: str | None, data: bytes | None, module: str, method: str):
    """The version list HEAD holds (*data*, read at *base*); the one on disk when HEAD holds none."""
    list_path = layout.method_versions_path(root, module, method)
    if data is None:
        on_disk = read_version_list(root, module, method)
        return on_disk, (list_path.read_bytes() if list_path.is_file() else None)
    return parse_version_list(data, module, method, f"{base}:{list_path.name}"), data


def _replace_file(source: Path, dest: Path) -> None:
    """Rename the file *source* over *dest*, waiting briefly while a reader holds it open."""
    for attempt in range(20):
        try:
            os.replace(source, dest)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))  # Windows: open without delete sharing


def _match_case(path: Path) -> None:
    """Give *path* its own spelling when the filesystem holds it under another case.

    On a case-insensitive filesystem a rename over, or a mkdir of, a name
    that exists in another case can keep the old spelling (APFS keeps it
    on a rename over a file), and the sweep of the names the new tree lacks
    would then remove the file the tree holds. A case-only rename fixes the
    spelling and changes no bytes.
    """
    try:
        names = os.listdir(path.parent)
        if path.name in names:
            return
        want = os.lstat(path)
    except OSError:
        return
    folded = path.name.casefold()
    for name in names:
        if name.casefold() != folded:
            continue
        other = path.parent / name
        try:
            # The entry itself, never a symlink to it or a hardlink of it.
            found = os.lstat(other)
            if (found.st_ino, found.st_dev) == (want.st_ino, want.st_dev):
                os.rename(other, path)
                return
        except OSError:
            continue


def _replace_tree(source: Path, target: Path) -> None:
    """Make the directory *target* hold exactly the files of *source*, file by file.

    Each file is renamed over its counterpart, so a reader of any file finds
    its old bytes or its new ones, never a partial file, and a file both
    trees hold is never absent; *target* itself is never absent. Files only
    the old tree held are removed last, then the directories left empty.
    *source*'s files are moved, not copied.
    """
    target.mkdir(parents=True, exist_ok=True)
    wanted: set[str] = set()
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source)
        wanted.add(rel.as_posix())
        parent = target
        for part in rel.parts[:-1]:
            parent = parent / part
            if parent.is_file():
                parent.unlink()
            parent.mkdir(exist_ok=True)
            _match_case(parent)
        dest = target / rel
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        _replace_file(path, dest)
        _match_case(dest)
    for path in sorted((p for p in target.rglob("*")
                        if p.is_file() or p.is_symlink()), reverse=True):
        if path.relative_to(target).as_posix() not in wanted:
            path.unlink()
    for path in sorted((p for p in target.rglob("*") if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()


def _sync_to_head(root: Path, top: Path, snapshot_dir: Path, list_path: Path,
                  staging: Path, staged_tree: str, extra: list[Path]) -> None:
    """Make the working tree and the index hold HEAD's registry paths.

    The list is renamed in first, then the snapshot's files one by one
    (:func:`_replace_tree`). When another registration's commit lands
    meanwhile, the pass is made again from the new HEAD, so the last
    registration to finish leaves HEAD's content. *top* is the top level
    the registration resolved.
    """
    holder = Path(tempfile.mkdtemp(dir=layout.registry_staging_dir(root), prefix="sync-"))
    staging_unused = staging.is_dir()
    try:
        for attempt in range(HEAD_MOVED_ATTEMPTS):
            head = head_commit(root, top=top)
            assert head is not None  # called after a commit landed
            held_list, held_tree = objects_at(root, head, [list_path, snapshot_dir], top=top)
            data = _blob(held_list)
            if data is not None:
                write_version_list_bytes(root, snapshot_dir.parent.name,
                                         snapshot_dir.name, data)
            tree = held_tree.oid if held_tree is not None else None
            if tree is not None:
                if tree == staged_tree and staging_unused:
                    source, staging_unused = staging, False
                else:
                    source = holder / f"head-{attempt}"
                    source.mkdir()
                    archive_tree(root, tree, source, top=top)
                _replace_tree(source, snapshot_dir)
            set_index_to(root, head, [snapshot_dir, list_path, *extra], top=top)
            if head_commit(root, top=top) == head:
                return
        raise RuntimeError("HEAD kept moving while the registry was put in step.")
    finally:
        shutil.rmtree(holder, ignore_errors=True)


@task(
    purpose="Verify the tree git would commit for the staged snapshot, commit "
            "it with the version list (and the registry's .gitattributes line "
            "when HEAD lacks it) from a temporary index on HEAD, then set the "
            "working tree and the index to HEAD at the registry paths",
    inputs="project root, module and method names, the staging copy, the "
           "plan's outcome, the newest entry, the new version's fingerprints "
           "and image reference",
    outputs="the entry appended (None for the same content) and the commit",
    critical="Refused before any commit when a file would be left out or "
             "changed (ignored, export-ignore, a line-ending conversion or "
             "filter); a refusal leaves the snapshot, the list, .gitattributes "
             "and the user's index as they were. HEAD moves only from the "
             "commit the list was read from; a moved HEAD takes the next label. "
             "Only the registry paths of the index change.",
)
def commit_registration(root: Path, module: str, method: str, staging: Path, *,
                        same: bool, newest: VersionEntry | None,
                        code_fingerprint: str, env_name: str,
                        env_fingerprint: str, image_ref: str) -> RegistrationCommit:
    """Commit a registration's snapshot and version list, or refuse having changed nothing.

    A new version (``same`` false) appends the next label to the list HEAD
    holds. The same content as the newest version commits nothing when HEAD
    holds that version's tree; when it does not (history rewritten), the
    version's tree is committed again with the list as it is, and the list
    is not rewritten.

    Args:
        root: The project root.
        module: The module's name.
        method: The method's name.
        staging: The snapshot built in the staging directory.
        same: The plan matched the newest version.
        newest: The newest entry the plan read.
        code_fingerprint: The staging copy's code fingerprint.
        env_name: The env the version runs in.
        env_fingerprint: That env's fingerprint.
        image_ref: That env's recorded image reference.

    Returns:
        The entry appended and the commit made.

    Raises:
        ValueError: The tree git would commit is not the staging copy, or a
            same-content version's tree is gone and the source does not
            rebuild it.
        NotAGitRepositoryError: The project is not a git repository.
        RuntimeError: A hook refused the commit, or git failed.
    """
    snapshot_dir = layout.method_dir(root, module, method)
    list_path = layout.method_versions_path(root, module, method)
    # Resolved once for this registration and passed to every git call.
    top = git_toplevel(root)
    if top is None:
        raise NotAGitRepositoryError(f"{str(root)!r} is not a git repository.")
    if same and newest is not None and tree_at(
            root, "HEAD", snapshot_dir, top=top) == newest.tree_hash:
        return RegistrationCommit(entry=None, commit=None)

    attrs_path = layout.gitattributes_path(root)
    attrs_before = attrs_path.read_bytes() if attrs_path.is_file() else None
    line = layout.REGISTRY_GITATTRIBUTES_LINE
    # The line must be in effect while the tree is built.
    add_gitattributes_lines(root, [line])
    try:
        base = head_commit(root, top=top)
        tree, ignored = tree_as_if_at(root, staging, snapshot_dir, base, top=top)
        if same:
            assert newest is not None  # a SAME plan has a newest version
            if tree != newest.tree_hash and not object_exists(root, newest.tree_hash,
                                                              top=top):
                raise ValueError(
                    f"Cannot register '{module}/{method}': it matches its newest "
                    f"version v{newest.label}, but git no longer holds that "
                    f"version's code (tree {newest.tree_hash}) and the source "
                    f"does not rebuild it byte for byte (it differs in line "
                    f"endings, or in method.yaml text runs do not use). Nothing "
                    f"was committed. Change the source to register a new version."
                )
            commit_tree = newest.tree_hash
        else:
            differences = tree_differences(root, tree, staging, snapshot_dir, ignored,
                                           top=top)
            if differences:
                raise _refusal(module, method, differences)
            commit_tree = tree
        attrs_extra: list[Path] = []
        for _attempt in range(HEAD_MOVED_ATTEMPTS):
            base = head_commit(root, top=top)
            held_list, held_attrs = (objects_at(root, base, [list_path, attrs_path], top=top)
                                     if base else (None, None))
            entries, list_bytes = _list_at(root, base, _blob(held_list), module, method)
            entry = None
            if not same:
                entry = next_entry(entries, code_fingerprint=code_fingerprint,
                                   env_name=env_name, env_fingerprint=env_fingerprint,
                                   image_ref=image_ref, tree_hash=tree)
                list_bytes = render_version_list([*(entries or []), entry])
            blobs: dict[Path | str, bytes] = {list_path: list_bytes}
            base_attrs = _blob(held_attrs)
            if _lacks_line(base_attrs, line):
                held = base_attrs or b""
                sep = b"\n" if held and not held.endswith(b"\n") else b""
                blobs[attrs_path] = held + sep + f"{line}\n".encode()
                attrs_extra = [attrs_path] if (base is None or index_matches(
                    root, base, attrs_path, top=top)) else []
            try:
                sha = commit_over_head(root, base, trees={snapshot_dir: commit_tree},
                                       blobs=blobs,
                                       message=f"Register method {module}/{method}",
                                       top=top)
            except HeadMovedError:
                continue
            break
        else:
            raise RuntimeError(
                f"Cannot register '{module}/{method}': HEAD kept moving (other "
                f"commits landing) while registration committed. Nothing was "
                f"committed; run `wfc register-method` again."
            )
    except BaseException:
        if attrs_before is None:
            attrs_path.unlink(missing_ok=True)
        else:
            attrs_path.write_bytes(attrs_before)
        raise
    if sha is None:
        print(f"  git: no changes to commit for '{module}/{method}'")
    else:
        print(f"  git: committed as {sha[:12]}")
        _sync_to_head(root, top, snapshot_dir, list_path, staging, tree, attrs_extra)
    return RegistrationCommit(entry=entry, commit=sha)


def _lacks_line(data: bytes | None, line: str) -> bool:
    """Say whether the ``.gitattributes`` bytes *data* lack the line *line*."""
    present = {ln.strip() for ln in (data or b"").decode("utf-8", "replace").splitlines()}
    return line not in present
