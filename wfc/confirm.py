"""Confirm-or-refuse: the one terminal check and prompt the CLI's verbs share.

A verb that changes a project only after the user agrees shows what it will
do, then calls :func:`confirm`. ``--yes`` answers yes without asking. With
no terminal to ask on (stdin a pipe, a file or the null device, on every
platform) and no ``--yes``, nothing is changed and the verb's
:class:`ConfirmationRequired` is raised, so a script never applies a change
nobody confirmed. ``wfc init`` (an existing project), ``wfc
register-method`` (a new method or a new version), ``wfc push-env`` (a push
that writes), ``wfc export-run`` (a bundle that points at an image only
this machine holds), ``wfc cancel`` (stopping a running pipeline), ``wfc
delete-env``, ``wfc cache prune`` and ``wfc demo --remove`` use it.

The decisions live in the units that own them; this module only asks.
``delete-env`` and ``cache prune`` run in units that never import the CLI,
so the CLI hands their bodies the question as a callable that asks here.
"""

from __future__ import annotations

import sys


class ConfirmationRequired(RuntimeError):
    """A change needs confirmation and there is no terminal to ask on.

    Nothing has been changed when this is raised; ``--yes`` applies the
    change without asking.
    """


def stdin_is_interactive() -> bool:
    """Return True if stdin is a terminal someone can answer on.

    A pipe, a file and the null device are not. On Windows ``isatty()`` is
    also true for the null device (``< NUL``), since it is a character
    device, so there stdin must also be a console: ``GetConsoleMode``
    succeeds on a console handle and fails on ``NUL``.
    """
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        return _is_console(sys.stdin.fileno())
    except (ValueError, AttributeError, OSError):
        return False


def _is_console(fd: int) -> bool:
    """Return True if *fd* is a console; any terminal counts off Windows."""
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_console_mode = kernel32.GetConsoleMode
    get_console_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_console_mode.restype = wintypes.BOOL
    mode = wintypes.DWORD()
    return bool(get_console_mode(msvcrt.get_osfhandle(fd), ctypes.byref(mode)))


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
