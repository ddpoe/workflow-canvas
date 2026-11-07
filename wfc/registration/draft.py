"""Drafts: a method folder tried as one of a module's methods before it is registered.

A draft points a module's method name at a method folder on disk. It is a
DRAFT version when the module already has that method (registering it adds
the next version) and a DRAFT method otherwise (registering it adds v1). One
method has one draft, from one folder. The method's name is the folder's
name, as registration takes it.

Each draft's pointer is ``draft.json`` in the draft's own folder, Layout's
:func:`~wfc.layout.draft_dir` (``.wfc/runs/drafts/<module>/<method>/``,
inside the artifact store every project ignores). It is personal scratch,
never committed or recorded::

    {"module": "<module>", "method": "<method>", "folder": "<absolute path>",
     "kind": "version" | "method", "added_at": "<ISO 8601 UTC>",
     "snapshot": {"<folder-relative POSIX path>": [<size>, <mtime_ns>]},
     "contract": {<parse_method_yaml's result>} | null,
     "contract_error": "<why method.yaml could not be read>" | null}

The snapshot is the size and mtime of every file under the folder. Only
``method.yaml``'s entry marks a stale draft (its contract changed since it
was last read), which the Registry shows; any other changed file only marks
the folder's files updated. Nothing refuses a stale draft: the re-read
(:func:`reread_draft`) reads ``method.yaml`` again through Contracts' parser
and runs the Registration check. Its writing form, which every draft run
makes and Update is, stores the contract and ``method.yaml``'s stat on the
pointer; its read-only form, which Validate makes, writes nothing. Neither
registers anything. The stored contract is the one the contract map offers
as the method's ``versions["draft"]``.

The Registration check (:func:`check_method_folder`) collects every refusal
registration would make into one ✓/✗ list. It reuses the checks
:func:`~wfc.registration.method.plan_method_registration` makes, without its
first step (restoring a registered copy changed outside wfc) and without
writing anything to the project. Registering a draft
(:func:`register_draft`) goes through the ordinary plan and apply and then
removes the draft folder.

Remove draft deletes the draft folder (pointer, draft run and results); the
method folder is the author's and is never touched. Delete outputs removes
everything in the draft folder but the pointer.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow

from .. import layout
from ..contracts import parse_method_yaml, validate_env_name
from ..persistence import project_root as get_project_root

#: The two kinds of draft.
DRAFT_VERSION, DRAFT_METHOD = "version", "method"
#: The snapshot entry that decides a stale draft.
_CONTRACT_FILE = "method.yaml"

#: One process-wide lock over pointer writes: the canvas serves requests on
#: a thread pool.
_POINTER_LOCK = threading.RLock()


class DraftRefusedError(ValueError):
    """A draft cannot be added or updated as asked; nothing was written."""


class DraftNotFoundError(LookupError):
    """The module's method has no draft."""

    def __init__(self, module: str, method: str) -> None:
        """Name the module and method that have no draft.

        Args:
            module: The module named.
            method: The method named.
        """
        super().__init__(f"{module}.{method} has no draft. Add one with "
                         f"\"+ Draft from folder…\" in the Registry, or `wfc try`.")
        self.module = module
        self.method = method


@dataclass(frozen=True)
class DraftPointer:
    """One draft's pointer, as ``draft.json`` holds it.

    Attributes:
        module: The draft's module.
        method: The draft's method name (the folder's name).
        folder: The method folder, absolute.
        kind: ``"version"`` or ``"method"``, as it was when the draft was added.
        added_at: When it was added, ISO 8601 UTC.
        snapshot: Each file under the folder, folder-relative POSIX, with its
            ``(size, mtime_ns)`` (``method.yaml``'s as of the last Update).
        contract: ``method.yaml`` as Contracts parsed it at add or Update, or
            ``None`` when it could not be read.
        contract_error: Why it could not be read, or ``None``.
    """

    module: str
    method: str
    folder: Path
    kind: str
    added_at: str
    snapshot: dict[str, tuple[int, int]] = field(default_factory=dict)
    contract: dict | None = None
    contract_error: str | None = None


@dataclass(frozen=True)
class DraftPlan:
    """What adding a folder as a draft would do; nothing is written.

    Attributes:
        module: The module named.
        method: The method name (the folder's name).
        folder: The method folder, absolute.
        kind: ``"version"`` (the module has the method) or ``"method"``.
        newest_label: The method's newest version, or ``None``.
        registers_as: The version registering the draft adds.
        message: The dialog's sentence.
        existing: The pointer when this folder already is the method's draft.
    """

    module: str
    method: str
    folder: Path
    kind: str
    newest_label: int | None
    registers_as: int
    message: str
    existing: DraftPointer | None = None


@dataclass(frozen=True)
class DraftStatus:
    """A draft now: its kind, what registering adds, and its folder against its snapshot.

    Attributes:
        pointer: The draft's pointer.
        kind: ``"version"`` when the module has the method now, else ``"method"``.
        newest_label: The method's newest version, or ``None``.
        registers_as: The version registering the draft adds.
        missing: The method folder is gone.
        stale: ``method.yaml`` changed (or went) since it was last read.
        files_updated: Another file of the folder changed, was added or went.
        changed_files: Those other files, sorted.
    """

    pointer: DraftPointer
    kind: str
    newest_label: int | None
    registers_as: int
    missing: bool
    stale: bool
    files_updated: bool
    changed_files: list[str]


@dataclass(frozen=True)
class DraftReread:
    """A draft read again: its ``method.yaml`` now and its Registration check.

    Attributes:
        pointer: The pointer with the contract read now and ``method.yaml``'s
            stat: as written by the writing form, as it would be written by
            the read-only form (which writes nothing).
        contract: The contract read now, or ``None`` when ``method.yaml``
            cannot be read (``pointer.contract_error`` says why; the check
            carries it as a ✗ line).
        changed: The contract differs from the one the pointer stored.
        check: The Registration check of the draft's folder now.
        message: What the re-read found, for the user (Update shows it).
    """

    pointer: DraftPointer
    contract: dict | None
    changed: bool
    check: RegistrationCheck
    message: str


@dataclass(frozen=True)
class CheckLine:
    """One line of the Registration check.

    Attributes:
        ok: ✓ when True, ✗ when False.
        text: What was checked (✓) or the refusal registration would make (✗).
    """

    ok: bool
    text: str


@dataclass(frozen=True)
class RegistrationCheck:
    """The Registration check: every line, ✓ or ✗. Nothing was written.

    Attributes:
        lines: Each check made, in order.
        warnings: What registration would print as ``WARNING:`` lines
            (dynamic or unreachable saves, declared params the script never
            reads); never a ✗.
    """

    lines: tuple[CheckLine, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when every line is ✓."""
        return all(line.ok for line in self.lines)

    @property
    def failures(self) -> list[str]:
        """The text of each ✗ line, in order."""
        return [line.text for line in self.lines if not line.ok]


# --- pointers -------------------------------------------------------------------


def folder_snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """The stat snapshot of a folder: each file's size and mtime.

    Args:
        folder: The method folder.

    Returns:
        ``{folder-relative POSIX path: (size, mtime_ns)}`` for every file
        under it; empty when the folder is gone.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[path.relative_to(folder).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return out


def _read_contract(folder: Path) -> tuple[dict | None, str | None]:
    """Parse a folder's ``method.yaml`` through Contracts: ``(contract, error)``."""
    try:
        contract = parse_method_yaml(folder)
    except ValueError as exc:
        return None, str(exc)
    if contract is None:
        return None, f"{folder} has no method.yaml."
    return contract, None


def draft_contract_now(pointer: DraftPointer) -> tuple[dict | None, str | None]:
    """A draft's ``method.yaml`` read now, as the re-read reads it; nothing is written.

    The contract map and the params check read a draft's contract here, so
    a ``method.yaml`` edited since the pointer was written is what they see
    (:func:`reread_draft` reads the same file the same way, and also makes
    the Registration check).

    Args:
        pointer: The draft's pointer.

    Returns:
        ``(contract, None)``, or ``(None, why)`` when ``method.yaml`` cannot
        be read (the folder gone included).
    """
    return _read_contract(pointer.folder)


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else get_project_root()


def _to_json(pointer: DraftPointer) -> bytes:
    body = {
        "module": pointer.module, "method": pointer.method,
        "folder": pointer.folder.as_posix(), "kind": pointer.kind,
        "added_at": pointer.added_at,
        "snapshot": {path: list(entry) for path, entry in sorted(pointer.snapshot.items())},
        "contract": pointer.contract, "contract_error": pointer.contract_error,
    }
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def _from_json(path: Path) -> DraftPointer:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DraftPointer(
            module=str(data["module"]), method=str(data["method"]),
            folder=Path(data["folder"]), kind=str(data["kind"]),
            added_at=str(data["added_at"]),
            snapshot={str(p): (int(e[0]), int(e[1])) for p, e in data["snapshot"].items()},
            contract=data.get("contract"), contract_error=data.get("contract_error"),
        )
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(
            f"{path} is not a draft pointer ({exc}). Remove the draft folder "
            f"{path.parent} and add the draft again."
        ) from exc


def _write_pointer(root: Path, pointer: DraftPointer) -> None:
    """Write ``draft.json`` whole: a reader finds the old pointer or the new one."""
    path = layout.draft_pointer_path(root, pointer.module, pointer.method)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(_to_json(pointer))
    os.replace(tmp, path)


def read_draft(module: str, method: str, root: Path | None = None) -> DraftPointer | None:
    """Read one draft's pointer.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Returns:
        The pointer, or ``None`` when the method has no draft.

    Raises:
        ValueError: The pointer file is not one wfc wrote.
    """
    path = layout.draft_pointer_path(_root(root), module, method)
    return _from_json(path) if path.is_file() else None


def list_drafts(root: Path | None = None) -> list[DraftPointer]:
    """Every draft's pointer, by module then method.

    Args:
        root: The project root; the resolved one when omitted.

    Returns:
        The pointers; empty when there are none.

    Raises:
        ValueError: A pointer file is not one wfc wrote.
    """
    drafts = layout.drafts_dir(_root(root))
    if not drafts.is_dir():
        return []
    return [_from_json(path)
            for path in sorted(drafts.glob(f"*/*/{layout.DRAFT_POINTER_FILENAME}"))]


def _require(module: str, method: str, root: Path) -> DraftPointer:
    pointer = read_draft(module, method, root)
    if pointer is None:
        raise DraftNotFoundError(module, method)
    return pointer


def _newest_label(root: Path, module: str, method: str) -> int | None:
    from .method_version import newest_entry, read_version_list

    newest = newest_entry(read_version_list(root, module, method))
    return newest.label if newest is not None else None


def _module_registered(module: str) -> bool:
    from sqlmodel import select

    from ..persistence import Module, get_session

    with get_session() as session:
        return session.exec(select(Module).where(Module.name == module)).first() is not None


def _resolve_folder(folder: Path | str, root: Path) -> Path:
    path = Path(folder)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _allow_reserved(module: str) -> bool:
    """A draft in the demo's own module checks and registers as ``wfc demo``'s methods do."""
    from ..reserved import RESERVED_DEMO_PREFIX

    return module.startswith(RESERVED_DEMO_PREFIX)


@task(purpose="Say what adding a method folder as a module's draft would do: a "
              "DRAFT version of the module's method of the folder's name, or a "
              "DRAFT method, and the version registering it adds; nothing written",
      inputs="module name, method folder, project root",
      outputs="the plan with the dialog's sentence; a refusal raises",
      critical="The module comes first and must be registered; one method has "
               "one draft, from one folder")
def plan_draft(module: str, folder: Path | str, root: Path | None = None) -> DraftPlan:
    """Plan adding a folder as a draft: its kind and the dialog's sentence.

    An invalid folder (no script, an env not registered) can still be a
    draft: its Registration check shows why, and it cannot run until fixed.

    Args:
        module: The module the draft belongs to.
        folder: The method folder, absolute or relative to the project root.
        root: The project root; the resolved one when omitted.

    Returns:
        The plan. ``existing`` is set when the folder already is the method's
        draft.

    Raises:
        DraftRefusedError: The module is not registered, the folder is not a
            folder or lies inside the registry, its name cannot be a method
            name, or the method already has a draft from another folder.
    """
    root = _root(root)
    path = _resolve_folder(folder, root)
    method = path.name
    if not path.is_dir():
        raise DraftRefusedError(
            f"Cannot add {path} as a draft: it is not a folder. Pick the method "
            f"folder that holds method.yaml.")
    registry = layout.methods_dir(root).resolve()
    if path == registry or registry in path.parents:
        raise DraftRefusedError(
            f"Cannot add {path} as a draft: it is inside the registry ({registry}), "
            f"which holds wfc's registered copies. Pick your own method folder.")
    try:
        validate_env_name(module, "module")
        validate_env_name(method, "method")
    except ValueError as exc:
        raise DraftRefusedError(f"Cannot add {path} as a draft: {exc}") from exc
    if not _module_registered(module):
        raise DraftRefusedError(
            f"Module '{module}' is not registered, so it cannot hold a draft. "
            f"Run 'wfc register-module --name {module}' first, or pick a "
            f"registered module.")
    existing = read_draft(module, method, root)
    if existing is not None and existing.folder != path:
        raise DraftRefusedError(
            f"{module}.{method} already has a draft from {existing.folder}. A "
            f"method has one draft: remove that draft (Remove draft in the "
            f"Registry) before adding {path}.")

    newest = _newest_label(root, module, method)
    if newest is not None:
        kind, registers_as = DRAFT_VERSION, newest + 1
        message = (f"Module {module} already has a method called {method} "
                   f"(newest version v{newest}). This folder becomes its DRAFT "
                   f"version; registering it adds v{registers_as}.")
    else:
        kind, registers_as = DRAFT_METHOD, 1
        message = (f"Module {module} has no method called {method}. This folder "
                   f"becomes a DRAFT method; registering it adds {method} as v1.")
    return DraftPlan(module=module, method=method, folder=path, kind=kind,
                     newest_label=newest, registers_as=registers_as,
                     message=message, existing=existing)


@workflow(purpose="Add a method folder as a module's draft: plan it, then write "
                  "its pointer with the folder's stat snapshot and the contract "
                  "method.yaml declares")
def add_draft(module: str, folder: Path | str, root: Path | None = None) -> DraftPointer:
    """Add a folder as a draft (the outcome is stated by :func:`plan_draft`; no prompt).

    Adding the folder that already is the method's draft keeps its pointer
    as it is.

    Args:
        module: The module the draft belongs to.
        folder: The method folder, absolute or relative to the project root.
        root: The project root; the resolved one when omitted.

    Returns:
        The draft's pointer.

    Raises:
        DraftRefusedError: As :func:`plan_draft`.
    """
    root = _root(root)
    with _POINTER_LOCK:
        口 = AutoStep(step_num=1, name="Plan the draft")
        plan = plan_draft(module, folder, root)
        if plan.existing is not None:
            return plan.existing

        口 = Step(step_num=2, name="Write the pointer",
                 purpose="The folder's stat snapshot and its contract as read now",
                 critical="Only the draft folder under the artifact store is written")
        contract, error = _read_contract(plan.folder)
        pointer = DraftPointer(
            module=module, method=plan.method, folder=plan.folder, kind=plan.kind,
            added_at=datetime.now(UTC).isoformat(timespec="seconds"),
            snapshot=folder_snapshot(plan.folder), contract=contract,
            contract_error=error,
        )
        _write_pointer(root, pointer)
    return pointer


def draft_status(pointer: DraftPointer, root: Path | None = None) -> DraftStatus:
    """Compare a draft's folder with its snapshot and read what registering adds.

    Args:
        pointer: The draft's pointer.
        root: The project root; the resolved one when omitted.

    Returns:
        The draft's status now. Nothing is written.
    """
    root = _root(root)
    missing = not pointer.folder.is_dir()
    current = folder_snapshot(pointer.folder)
    before = pointer.snapshot
    changed = sorted(path for path in set(current) | set(before)
                     if path != _CONTRACT_FILE and current.get(path) != before.get(path))
    newest = _newest_label(root, pointer.module, pointer.method)
    return DraftStatus(
        pointer=pointer,
        kind=DRAFT_VERSION if newest is not None else DRAFT_METHOD,
        newest_label=newest,
        registers_as=newest + 1 if newest is not None else 1,
        missing=missing,
        stale=current.get(_CONTRACT_FILE) != before.get(_CONTRACT_FILE),
        files_updated=bool(changed),
        changed_files=changed,
    )


def draft_run_refusal(module: str, method: str, folder: Path | str | None = None,
                      root: Path | None = None) -> str | None:
    """Why a method's draft cannot be read for a run now, or ``None`` when it can.

    A changed ``method.yaml`` never refuses the draft, and neither does one
    that cannot be read: the run's re-read (:func:`reread_draft`) reads it
    again, and its Registration check carries any ✗.

    Args:
        module: The draft's module.
        method: The draft's method name.
        folder: The folder the caller means to run (a pipeline node's
            ``folder``); ``None`` for the draft's own.
        root: The project root; the resolved one when omitted.

    Returns:
        The refusal (the method has no draft, the folder is not the draft's,
        or the draft's folder is gone), or ``None``.
    """
    root = _root(root)
    qualified = f"{module}.{method}"
    try:
        pointer = read_draft(module, method, root)
    except ValueError as exc:
        return str(exc)
    if pointer is None:
        return (f"{qualified} has no draft. Add one (\"+ Draft from folder…\" in "
                f"the Registry, or `wfc try`), or pick a registered version.")
    if folder is not None and _resolve_folder(folder, root) != pointer.folder:
        return (f"the node runs the draft from {folder}, but {qualified}'s draft "
                f"is from {pointer.folder}. Pick the method again in the Builder "
                f"to run its current draft.")
    if not pointer.folder.is_dir():
        return (f"{qualified}'s draft folder {pointer.folder} is gone. Put the "
                f"folder back, or remove the draft in the Registry.")
    return None


def _reread_pointer(pointer: DraftPointer) -> tuple[DraftPointer, bool]:
    """The pointer with ``method.yaml`` read now: ``(pointer, changed)``.

    The other files' snapshot entries are kept, so a script edit still shows
    as files updated.
    """
    contract, error = _read_contract(pointer.folder)
    current = folder_snapshot(pointer.folder)
    snapshot = {path: entry for path, entry in pointer.snapshot.items()
                if path != _CONTRACT_FILE}
    if _CONTRACT_FILE in current:
        snapshot[_CONTRACT_FILE] = current[_CONTRACT_FILE]
    changed = (contract, error) != (pointer.contract, pointer.contract_error)
    reread = DraftPointer(
        module=pointer.module, method=pointer.method, folder=pointer.folder,
        kind=pointer.kind, added_at=pointer.added_at, snapshot=snapshot,
        contract=contract, contract_error=error,
    )
    return reread, changed


@workflow(purpose="Read a draft again: its method.yaml through Contracts' parser "
                  "and the Registration check of its folder; the writing form "
                  "stores the contract and method.yaml's stat on the pointer, "
                  "the read-only form writes nothing; nothing is registered")
def reread_draft(module: str, method: str, root: Path | None = None, *,
                 write: bool = True) -> DraftReread:
    """Read a draft's ``method.yaml`` again and check its folder as registration would.

    Every draft run makes the writing form (through Execution's draft
    preflight) and so does the Registry's Update (:func:`update_draft`);
    Validate makes the read-only form. Callers check params and links
    against ``contract``, the contract read now, in both forms.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.
        write: Store the contract and ``method.yaml``'s stat on the pointer
            (the writing form); ``False`` writes nothing (the read-only form)
            and prints no ``WARNING:`` lines: the check keeps them as data.

    Returns:
        The contract read now, the pointer with it, whether it changed, and
        the Registration check.

    Raises:
        DraftNotFoundError: The method has no draft.
        DraftRefusedError: The method folder is gone.
        ValueError: The pointer file is not one wfc wrote.
    """
    root = _root(root)
    with _POINTER_LOCK:
        pointer = _require(module, method, root)
        if not pointer.folder.is_dir():
            raise DraftRefusedError(
                f"The draft of {module}.{method} points at {pointer.folder}, which "
                f"is gone. Remove the draft, or put the folder back.")

        口 = Step(step_num=1, name="Read method.yaml again",
                 purpose="Contracts' parser, as registration reads it; the other "
                         "files keep their snapshot entries")
        reread, changed = _reread_pointer(pointer)

        if write:
            口 = Step(step_num=2, name="Store the contract and method.yaml's stat",
                     purpose="The writing form: the pointer holds the contract read now",
                     critical="Only the pointer is written; the read-only form "
                              "writes nothing")
            _write_pointer(root, reread)

    口 = AutoStep(step_num=3, name="The Registration check")
    check = check_method_folder(pointer.folder, module, method,
                                allow_reserved=_allow_reserved(module), root=root,
                                quiet=not write)

    if reread.contract_error is not None:
        message = f"Read method.yaml again; it cannot be used: {reread.contract_error}"
    elif changed:
        message = "Read method.yaml again: the contract changed."
    else:
        message = "Read method.yaml again: the contract is unchanged."
    return DraftReread(pointer=reread, contract=reread.contract, changed=changed,
                       check=check, message=message)


def update_draft(module: str, method: str, root: Path | None = None) -> DraftReread:
    """The Registry's Update: the writing re-read, with no run.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Returns:
        The re-read, as :func:`reread_draft` returns it.

    Raises:
        DraftNotFoundError: The method has no draft.
        DraftRefusedError: The method folder is gone.
    """
    return reread_draft(module, method, root, write=True)


def remove_draft(module: str, method: str, root: Path | None = None) -> None:
    """Remove a draft: delete its draft folder (pointer, draft run and results).

    The method folder the pointer names is the author's and is untouched.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Raises:
        DraftNotFoundError: The method has no draft.
    """
    root = _root(root)
    with _POINTER_LOCK:
        _require(module, method, root)
        shutil.rmtree(layout.draft_dir(root, module, method))


def delete_draft_outputs(module: str, method: str, root: Path | None = None) -> int:
    """Delete outputs: remove everything in the draft folder but its pointer.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Returns:
        How many entries of the draft folder were removed.

    Raises:
        DraftNotFoundError: The method has no draft.
    """
    root = _root(root)
    removed = 0
    with _POINTER_LOCK:
        _require(module, method, root)
        for entry in sorted(layout.draft_dir(root, module, method).iterdir()):
            if entry.name == layout.DRAFT_POINTER_FILENAME:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
    return removed


# --- the Registration check -------------------------------------------------------


def _attempt(lines: list[CheckLine], ok_text, fn, *args, **kwargs):
    """Run one check: a ✓ line with *ok_text* (or its result's text), or a ✗ line.

    Returns:
        ``(True, result)`` or ``(False, None)``.
    """
    try:
        result = fn(*args, **kwargs)
    except (ValueError, OSError, SyntaxError) as exc:
        lines.append(CheckLine(False, str(exc).strip()))
        return False, None
    lines.append(CheckLine(True, ok_text(result) if callable(ok_text) else ok_text))
    return True, result


def _check_names(module: str, method: str, allow_reserved: bool) -> None:
    from ..reserved import check_reserved_name

    validate_env_name(method, "method")
    validate_env_name(module, "module")
    check_reserved_name(method, "method", allow_reserved)
    check_reserved_name(module, "module", allow_reserved)


def _check_module(module: str) -> None:
    from ..persistence import get_session
    from .method import _resolve_module

    with get_session() as session:
        _resolve_module(session, module)


def _check_folder(folder: Path, root: Path) -> None:
    if not folder.exists():
        raise ValueError(f"The method folder {folder} is gone.")
    if not folder.is_dir():
        raise ValueError(f"{folder} is a file, not a method folder.")
    registry = layout.methods_dir(root).resolve()
    if folder == registry or registry in folder.parents:
        raise ValueError(
            f"Cannot register from {folder}: it is inside the registry "
            f"({registry}), which holds wfc's registered copies.")


def _parse_contract(folder: Path, method: str) -> dict:
    from .method import _check_method_env

    contract = parse_method_yaml(folder)
    if contract is None:
        _check_method_env(method, None)  # raises the no-method.yaml refusal
    return contract


def _check_env(method: str, contract: dict, root: Path, *, quiet: bool = False) -> str:
    from .method import _check_method_env, _version_env

    _check_method_env(method, contract, quiet=quiet)
    _version_env(contract["env"], root)
    return contract["env"]


def _check_module_outputs(module: str, method: str, contract: dict, *,
                          quiet: bool = False) -> None:
    from ..persistence import get_session
    from .method import _check_module_contract, _resolve_module

    with get_session() as session:
        _check_module_contract(session, _resolve_module(session, module),
                               module, method, contract, quiet=quiet)


def _check_snapshot(folder: Path, module: str, method: str, located, root: Path) -> None:
    """The would-be snapshot, assembled outside the project, as registration checks it."""
    from .method import (
        _newest_copy_facts,
        _plan_holds,
        _refuse_crlf_shell_scripts,
        _refuse_renamed_main_script,
    )
    from .method_version import newest_entry, read_version_list
    from .snapshot import _write_snapshot

    staging_root = Path(tempfile.mkdtemp(prefix="wfc-check-"))
    try:
        staging = staging_root / method
        not_copied = _write_snapshot(folder, staging, located.script_path,
                                     located.strict_helpers, located.helper_paths, method)
        ref = f"{module}/{method}"
        _refuse_crlf_shell_scripts(staging, ref)
        newest = newest_entry(read_version_list(root, module, method))
        if newest is not None:
            holds = _plan_holds(staging, located.script_path, folder, not_copied)
            _refuse_renamed_main_script(ref, folder, holds, newest,
                                        _newest_copy_facts(root, newest.tree_hash, method))
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


@workflow(purpose="The Registration check: every refusal registering a method folder "
                  "would make, collected into one ✓/✗ list, with no snapshot "
                  "restore and nothing written to the project")
def check_method_folder(folder: Path, module: str, method: str | None = None, *,
                        allow_reserved: bool = False,
                        root: Path | None = None,
                        quiet: bool = False) -> RegistrationCheck:
    """Check a method folder the way registration would, collecting every refusal.

    The checks are :func:`~wfc.registration.method.plan_method_registration`'s
    own, called one by one so a refusal does not hide the next: the names, the
    module, the folder, ``method.yaml``, the env, the script, the contract and
    the script's literal names (the undeclared-name scan), the module's
    required outputs, and the would-be snapshot (assembled in a temporary
    directory outside the project: CRLF shell scripts, the main script's
    file name). A check that needs what an earlier one could not read is
    left out. The plan's first step, restoring a registered copy changed
    outside wfc, is not made. What registration would print as ``WARNING:``
    lines is kept in the check's ``warnings``, never a ✗, and printed here
    too unless *quiet*.

    Args:
        folder: The method folder.
        module: The module it registers under.
        method: The method name; the folder's name when omitted.
        allow_reserved: Allow the reserved ``__demo__`` prefix.
        root: The project root; the resolved one when omitted.
        quiet: Print nothing: the warnings are kept as data only, and no
            ``WARNING:``, env, scan or contract line is printed (a draft's
            read-only re-read).

    Returns:
        Every line, ✓ or ✗.
    """
    from .method import _check_contract, _locate_and_scan

    root = _root(root)
    folder = Path(folder).resolve()
    method = method or folder.name
    lines: list[CheckLine] = []

    口 = Step(step_num=1, name="Names and module",
             purpose="The method and module names registration accepts, and the "
                     "module is registered")
    _attempt(lines, f"Names {module}/{method} can be registered",
             _check_names, module, method, allow_reserved)
    module_ok, _ = _attempt(lines, f"Module {module} is registered", _check_module, module)

    口 = Step(step_num=2, name="The folder",
             purpose="The method folder exists, outside the registry",
             critical="Nothing else can be read without it")
    folder_ok, _ = _attempt(lines, f"Folder {folder} exists", _check_folder, folder, root)
    if not folder_ok:
        return RegistrationCheck(tuple(lines))

    口 = Step(step_num=3, name="method.yaml and its env",
             purpose="method.yaml parses and names a registered env")
    parsed, contract = _attempt(lines, "method.yaml reads", _parse_contract, folder, method)
    if parsed:
        _attempt(lines, lambda env: f"Env {env} is registered",
                 _check_env, method, contract, root, quiet=quiet)

    口 = Step(step_num=4, name="The script",
             purpose="The folder holds one method and its script is found and scanned")
    found, located = _attempt(lines, lambda loc: f"Script {loc.script_path.name} found",
                              _locate_and_scan, folder, method, None, quiet=quiet)

    口 = Step(step_num=5, name="The contract and the script's names",
             purpose="Input slots, parameter defaults, no retired options form, and "
                     "the script's literal ctx.input and save_artifact names against "
                     "the declared ones")
    warnings: list[str] = []
    if parsed:
        _, contract_warnings = _attempt(
            lines,
            "Contract checks pass: inputs, parameter defaults, options forms "
            "and the script's names"
            if found else
            "Contract checks pass: inputs, parameter defaults and options forms",
            _check_contract, method, contract,
            located.script_info if found else None,
            located.script_path if found else folder, quiet=quiet)
        warnings += contract_warnings or []
    if parsed and module_ok:
        _attempt(lines, f"Declares module {module}'s required outputs",
                 _check_module_outputs, module, method, contract, quiet=quiet)

    口 = Step(step_num=6, name="The would-be snapshot",
             purpose="Assembled outside the project: no CRLF shell script, and a "
                     "new version keeps the newest version's main script name")
    if found:
        _attempt(lines, "The snapshot can be registered",
                 _check_snapshot, folder, module, method, located, root)
    return RegistrationCheck(tuple(lines), tuple(warnings))


def check_draft(module: str, method: str, root: Path | None = None) -> RegistrationCheck:
    """A draft's Registration check (:func:`check_method_folder` on its folder).

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Returns:
        Every line, ✓ or ✗. Nothing is written.

    Raises:
        DraftNotFoundError: The method has no draft.
    """
    root = _root(root)
    pointer = _require(module, method, root)
    return check_method_folder(pointer.folder, module, method,
                               allow_reserved=_allow_reserved(module), root=root)


# --- registering a draft ---------------------------------------------------------


def plan_draft_registration(module: str, method: str, root: Path | None = None):
    """Plan registering a draft: the ordinary registration plan of its folder.

    Args:
        module: The draft's module.
        method: The draft's method name.
        root: The project root; the resolved one when omitted.

    Returns:
        The :class:`~wfc.registration.method.MethodRegistrationPlan`.

    Raises:
        DraftNotFoundError: The method has no draft.
        ValueError: Any refusal registration makes.
    """
    from .method import plan_method_registration

    pointer = _require(module, method, _root(root))
    return plan_method_registration(pointer.folder, module, method,
                                    allow_reserved=_allow_reserved(module))


@workflow(purpose="Register a draft through the ordinary plan and apply, then "
                  "remove its draft folder; a refused registration keeps the draft")
def register_draft(module: str, method: str, *, assume_yes: bool = False,
                   planned: str | None = None, root: Path | None = None):
    """Register a draft's folder as its method's next version (or v1), then remove the draft.

    Args:
        module: The draft's module.
        method: The draft's method name.
        assume_yes: Confirm a plan that adds a version, as
            :func:`~wfc.registration.method.register_method` takes it.
        planned: The token of the plan the user confirmed (the canvas's dry
            run); the registration applies only when the fresh plan has it.
        root: The project root; the resolved one when omitted.

    Returns:
        The :class:`~wfc.registration.method.MethodRegistration`.

    Raises:
        DraftNotFoundError: The method has no draft.
        RegistrationConfirmationRequired: The plan adds a version and is not
            confirmed; the draft is kept.
        ValueError: Any refusal registration makes; the draft is kept.
    """
    from .method import register_method

    root = _root(root)
    pointer = _require(module, method, root)

    口 = AutoStep(step_num=1, name="Register the folder")
    registration = register_method(pointer.folder, module, method,
                                   allow_reserved=_allow_reserved(module),
                                   assume_yes=assume_yes, planned=planned)

    口 = Step(step_num=2, name="Remove the draft",
             purpose="The draft is registered: its pointer, draft run and results go",
             critical="Only after the registration is applied")
    with _POINTER_LOCK:
        draft = layout.draft_dir(root, module, method)
        if draft.exists():
            shutil.rmtree(draft)
    return registration
