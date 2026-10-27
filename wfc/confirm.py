"""Confirm-or-refuse: the one terminal check and prompt the CLI's verbs share.

A verb that changes a project only after the user agrees shows what it will
do, then calls :func:`confirm`. ``--yes`` answers yes without asking. With
no terminal to ask on and no ``--yes``, nothing is changed and the verb's
:class:`ConfirmationRequired` is raised, so a script never applies a change
nobody confirmed. ``wfc init`` (an existing project), ``wfc
register-method`` (a new method or a new version), ``wfc push-env`` (a push
that writes), ``wfc export-run`` (a bundle that points at an image only
this machine holds) and ``wfc cancel`` (stopping a running pipeline) use it.

The decisions live in the units that own them; this module only asks.
"""

from __future__ import annotations

import sys


class ConfirmationRequired(RuntimeError):
    """A change needs confirmation and there is no terminal to ask on.

    Nothing has been changed when this is raised; ``--yes`` applies the
    change without asking.
    """


def stdin_is_interactive() -> bool:
    """Return True if stdin is an interactive terminal (safe to prompt)."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, AttributeError):
        return False


def confirm(question: str, *, assume_yes: bool,
            refusal: ConfirmationRequired) -> bool:
    """Ask *question* once, ``[y/N]``, unless ``--yes`` already answered it.

    Args:
        question: The question, without the ``[y/N]`` suffix.
        assume_yes: ``--yes``: answer yes without asking.
        refusal: What to raise when there is no terminal and no ``--yes``;
            its message says nothing was changed and to re-run with
            ``--yes``.

    Returns:
        True to apply. False on any answer but ``y``/``yes``, including an
        empty answer and end of input.

    Raises:
        ConfirmationRequired: *refusal*, with no terminal and no ``--yes``.
    """
    if assume_yes:
        return True
    if not stdin_is_interactive():
        raise refusal
    try:
        answer = input(f"{question} [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")
