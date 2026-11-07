"""Module colours: the ``.wfc/registry/modules.json`` store, defaults and changes.

A module's colour is kept in ``.wfc/registry/modules.json`` (Layout's
:func:`~wfc.layout.module_colors_path`), committed with the project, so
everyone who opens the project sees the same colours. Each entry holds the
module's colour and the default it was given when first stored::

    {"modules": {"<name>": {"color": "#rrggbb", "default": "#rrggbb"}}}

A default is chosen by the module's name, not its registry order: a hash of
the name picks a palette slot, and when another stored module already shows
that slot's colour the next free slot is taken (with every slot taken, the
hashed one). Once stored it never moves when modules are added.

Every write is committed by pathspec through
:func:`wfc.version.commit_paths` when the file is inside the project's git
repository, and only then (outside a repository nothing is committed). A
commit that fails puts the file back as it was and re-raises, so a refused
commit leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from axiom_annotations import Step, task

from .. import layout
from .color_standard import PALETTE, ColorCheck, check_color, normalize_hex

#: One process-wide lock over the read-modify-write-commit of the store: the
#: canvas serves requests on a thread pool.
_STORE_LOCK = threading.RLock()


class ModuleColorRefusedError(ValueError):
    """A colour the colour standard refuses; nothing was written.

    Attributes:
        check: The check's answer (failed test, reserved meaning, suggestion).
    """

    def __init__(self, check: ColorCheck) -> None:
        super().__init__(f"{check.color} cannot be a module colour: {check.message}")
        self.check = check


@dataclass(frozen=True)
class ModuleColor:
    """One module's stored colour.

    Attributes:
        color: The colour every view shows, ``#rrggbb``.
        default: The default it was given when first stored, ``#rrggbb``.
    """

    color: str
    default: str


@dataclass(frozen=True)
class ColorWrite:
    """What a store write left.

    Attributes:
        colors: Every stored module's colour, by name.
        commit: The commit made, or ``None`` (nothing changed, or no
            repository).
    """

    colors: dict[str, str]
    commit: str | None


def read_module_colors(root: Path) -> dict[str, ModuleColor]:
    """Read the stored module colours.

    Args:
        root: The project root.

    Returns:
        Each stored module's colour, by name; empty when there is no file.

    Raises:
        ValueError: The file is not a module colour file.
    """
    path = layout.module_colors_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["modules"]
        return {
            str(name): ModuleColor(color=normalize_hex(entry["color"]),
                                   default=normalize_hex(entry.get("default", entry["color"])))
            for name, entry in entries.items()
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"{path} is not a module colour file ({exc}); expected "
            '{"modules": {"<module>": {"color": "#rrggbb", "default": "#rrggbb"}}}.'
        ) from exc


def _render(store: dict[str, ModuleColor]) -> bytes:
    """The file's bytes: names sorted, two-space indent, LF endings."""
    body = {"modules": {name: {"color": entry.color, "default": entry.default}
                        for name, entry in sorted(store.items())}}
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def default_color(name: str, taken: Sequence[str] = ()) -> str:
    """A module's default colour: its name's palette slot, or the next free one.

    Args:
        name: The module's name.
        taken: The colours other modules already show.

    Returns:
        A palette colour, ``#rrggbb``.
    """
    held = {normalize_hex(c) for c in taken}
    start = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big") % len(PALETTE)
    for offset in range(len(PALETTE)):
        color = PALETTE[(start + offset) % len(PALETTE)][1]
        if color not in held:
            return color
    return PALETTE[start][1]


def _write_and_commit(root: Path, data: bytes | None, message: str,
                      also_commit: Sequence[Path]) -> str | None:
    """Write the store (when *data* is given) and commit it with *also_commit*.

    A failed commit puts the file back as it was and re-raises.
    """
    from ..version import commit_paths

    path = layout.module_colors_path(root)
    before = path.read_bytes() if path.is_file() else None
    paths: list[Path] = list(also_commit)
    if data is not None and data != before:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
    if not paths:
        return None
    try:
        return commit_paths(root, paths, message, require_repo=False)
    except BaseException:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(before)
        raise


@task(purpose="Give every module missing from modules.json its default colour, "
              "in registration order, and commit the file once",
      inputs="project root, module names in registration order, the commit "
             "message, other paths for the same commit",
      outputs="every stored module's colour and the commit made",
      critical="A stored colour never moves; a failed commit restores the file")
def assign_module_colors(root: Path, names: Sequence[str], *,
                         message: str = "Assign module colours",
                         also_commit: Sequence[Path] = ()) -> ColorWrite:
    """Store a default colour for each module that has none, then commit once.

    This is the one-time assignment for modules registered before colours
    were stored, and ``register_module``'s assignment for a new module: the
    names go in registration order, so earlier modules take their slots
    first. Modules already stored keep their colours.

    Args:
        root: The project root.
        names: Module names in registration order.
        message: The commit message.
        also_commit: Other paths to commit in the same commit (a module's
            ``module.yaml``).

    Returns:
        Every stored colour and the commit made.

    Raises:
        ValueError: The store is not a module colour file.
        RuntimeError: git refused the commit (the file is as it was).
    """
    with _STORE_LOCK:
        口 = Step(step_num=1, name="Read the store",
                 purpose="Read modules.json; an absent file is an empty store")
        store = read_module_colors(root)
        口 = Step(step_num=2, name="Assign missing defaults",
                 purpose="In registration order, give each module the store "
                         "lacks its name's free palette slot")
        added = False
        for name in names:
            if name in store:
                continue
            color = default_color(name, [e.color for e in store.values()])
            store[name] = ModuleColor(color=color, default=color)
            added = True
        口 = Step(step_num=3, name="Write and commit",
                 purpose="Write the file when it changed and commit it, with "
                         "the other paths, by pathspec; restore it if the "
                         "commit fails")
        sha = _write_and_commit(root, _render(store) if added else None,
                                message, also_commit)
        return ColorWrite(colors={n: e.color for n, e in store.items()}, commit=sha)


@task(purpose="Change one module's colour in modules.json, refusing a colour "
              "the colour standard refuses, as its own commit",
      inputs="project root, module name, the new colour or None for its default",
      outputs="every stored module's colour and the commit made",
      critical="A refused colour writes nothing; a failed commit restores the file")
def set_module_color(root: Path, name: str, color: str | None) -> ColorWrite:
    """Set a stored module's colour; ``None`` puts back its default.

    Args:
        root: The project root.
        name: The module's name; it must be stored already
            (:func:`assign_module_colors`).
        color: ``#rgb`` or ``#rrggbb``, or ``None`` for the default.

    Returns:
        Every stored colour and the commit made.

    Raises:
        ModuleColorRefusedError: The colour fails a test.
        ValueError: *color* is not a hex colour, or the store is malformed.
        LookupError: No colour is stored for *name*.
        RuntimeError: git refused the commit (the file is as it was).
    """
    with _STORE_LOCK:
        口 = Step(step_num=1, name="Check the colour",
                 purpose="Run the six tests on a given colour; a refusal "
                         "writes nothing")
        if color is not None:
            check = check_color(color)
            if not check.ok:
                raise ModuleColorRefusedError(check)
        store = read_module_colors(root)
        if name not in store:
            raise LookupError(f"No colour is stored for module {name!r}.")
        new = store[name].default if color is None else normalize_hex(color)
        store[name] = ModuleColor(color=new, default=store[name].default)
        口 = Step(step_num=2, name="Write and commit",
                 purpose="Write the file and commit it alone, by pathspec; "
                         "restore it if the commit fails")
        sha = _write_and_commit(root, _render(store), f"Set the colour of module {name}", ())
        return ColorWrite(colors={n: e.color for n, e in store.items()}, commit=sha)
