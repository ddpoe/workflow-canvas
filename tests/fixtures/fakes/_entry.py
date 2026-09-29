"""The registry entry shape: one decorator per kind, four declared fields.

Every fake, declared shortcut and spy the suite uses is one importable
callable in this package, decorated with :func:`fake`, :func:`shortcut` or
:func:`spy`. The decorator takes the four fields the registry declares as
**keyword string literals** -- ``boundary``, ``preserves``, ``not_proven``
and ``backed_by`` -- and stores them on the callable, so a runtime reader
(``fakes.<entry>.not_proven``) and a stdlib AST reader (``tools/``, which
imports neither pytest nor this package) see the same text. The literals
must be plain constants: an f-string or a computed attribute is refused by
the harvest, because the harvest reads source and cannot evaluate it.

``backed_by`` is one of three shapes:

- a node id (``pm_mvp::tests.integration.test_x::test_y``) naming the test
  that drives the same boundary unfaked;
- ``owed: <reason>`` -- no such test exists yet; counted against the
  only-falling ``OWED_BACKING_WITNESSES`` constant in
  ``tests/test_suite_invariants.py``;
- ``undrivable: <reason>`` -- the suite cannot drive this boundary unfaked
  at all (the wall clock, an interactive prompt, a server bind, an
  exhausted port); declared, not counted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)

#: The kinds an entry can declare, in the inventory's vocabulary.
KINDS = ("fake", "shortcut", "spy")

#: The four fields every entry carries, in the order they are read.
FIELDS = ("boundary", "preserves", "not_proven", "backed_by")


def _declare(kind: str, *, boundary: str, preserves: str, not_proven: str,
             backed_by: str) -> Callable[[F], F]:
    """Return a decorator that stamps the four fields on a callable.

    Args:
        kind: ``"fake"``, ``"shortcut"`` or ``"spy"``.
        boundary: What production function or external process the entry
            replaces, or what state it writes directly.
        preserves: What of the real boundary's behaviour the entry keeps.
        not_proven: What a witness built on this entry cannot claim.
        backed_by: A node id, ``owed: <reason>`` or ``undrivable: <reason>``.

    Returns:
        The decorator.
    """
    if kind not in KINDS:
        raise ValueError(f"entry kind must be one of {KINDS}, not {kind!r}")

    def decorate(func: F) -> F:
        func.entry_kind = kind  # type: ignore[attr-defined]
        func.boundary = boundary  # type: ignore[attr-defined]
        func.preserves = preserves  # type: ignore[attr-defined]
        func.not_proven = not_proven  # type: ignore[attr-defined]
        func.backed_by = backed_by  # type: ignore[attr-defined]
        return func

    return decorate


def fake(*, boundary: str, preserves: str, not_proven: str,
         backed_by: str) -> Callable[[F], F]:
    """Declare a fake: a production function or external process replaced.

    Args:
        boundary: The function or process the fake stands in for.
        preserves: What the fake keeps of the real behaviour.
        not_proven: What a witness on this fake cannot claim.
        backed_by: The test driving the boundary unfaked, or ``owed`` /
            ``undrivable`` with a reason.

    Returns:
        The decorator.
    """
    return _declare("fake", boundary=boundary, preserves=preserves,
                    not_proven=not_proven, backed_by=backed_by)


def shortcut(*, boundary: str, preserves: str, not_proven: str,
             backed_by: str) -> Callable[[F], F]:
    """Declare a shortcut: product state written directly, bypassing its writer.

    Args:
        boundary: The writer bypassed and the state written.
        preserves: What of the writer's output the shortcut reproduces.
        not_proven: What a witness on this shortcut cannot claim.
        backed_by: The test that produces the same state through the writer,
            or ``owed`` / ``undrivable`` with a reason.

    Returns:
        The decorator.
    """
    return _declare("shortcut", boundary=boundary, preserves=preserves,
                    not_proven=not_proven, backed_by=backed_by)


def spy(*, boundary: str, preserves: str, not_proven: str,
        backed_by: str) -> Callable[[F], F]:
    """Declare a spy: a patch site that wraps the real callable and calls through.

    Args:
        boundary: The callable wrapped.
        preserves: Everything -- a spy delegates; say what it records.
        not_proven: Nothing, usually; say so.
        backed_by: The same test, since nothing is faked; or ``owed``.

    Returns:
        The decorator.
    """
    return _declare("spy", boundary=boundary, preserves=preserves,
                    not_proven=not_proven, backed_by=backed_by)


def is_entry(obj: object) -> bool:
    """Say whether an object is a registry entry (carries the four fields).

    Args:
        obj: Any object.

    Returns:
        ``True`` when every field in :data:`FIELDS` is set on it.
    """
    return all(hasattr(obj, name) for name in ("entry_kind", *FIELDS))
