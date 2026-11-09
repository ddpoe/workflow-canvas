"""Replace a file whole while other processes may hold it open.

wfc writes a shared file by writing a temporary sibling and renaming it
over the file, so no reader ever sees half a file. On Windows that rename
(``os.replace``) is refused with ``PermissionError`` while any other
handle holds the file open: a parallel step reading it, the canvas polling
it, a virus scanner. Such a hold is brief, so on Windows the refused action
is tried again, for about two seconds in all, before the error is raised.
Reads and removals of the same files meet the same refusal and are retried
the same way (:func:`retry_while_held`). On other platforms an open handle
never refuses a rename, so a ``PermissionError`` there is a real denial and
is raised at once.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")

#: The waits between tries, in seconds: doubling from 10 ms to 200 ms, then
#: 200 ms, about two seconds in all over sixteen tries.
RETRY_WAITS: tuple[float, ...] = (0.01, 0.02, 0.04, 0.08, 0.16) + (0.2,) * 10

#: Whether an open handle refuses a rename, read or removal (Windows only).
HOLDS_REFUSE = os.name == "nt"


def retry_while_held(action: Callable[[], _T]) -> _T:
    """Run a file action, trying again while another handle holds the file.

    Args:
        action: The action; on Windows a ``PermissionError`` it raises is
            retried after each wait in :data:`RETRY_WAITS`.

    Returns:
        What the action returns.

    Raises:
        PermissionError: The last try was refused too (on Windows), or the
            only try was (elsewhere).
    """
    if HOLDS_REFUSE:
        for wait in RETRY_WAITS:
            try:
                return action()
            except PermissionError:
                time.sleep(wait)
    return action()


def replace_file(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """``os.replace`` *source* over *target*, trying again while *target* is held.

    Args:
        source: The finished temporary file, on the same volume as *target*.
        target: The file it replaces.

    Raises:
        PermissionError: *target* stayed held past every try; *source* is
            left for the caller to remove.
    """
    retry_while_held(lambda: os.replace(source, target))


def write_file(path: Path, data: bytes, *, temporary: Path) -> None:
    """Write *path* whole: *data* into *temporary*, then replace *path* with it.

    A failed write or a refused replace removes *temporary* before the error
    is raised; a removal that is refused too is left, so it never hides the
    error.

    Args:
        path: The file to write.
        data: Its bytes.
        temporary: A sibling of *path* no other writer uses.

    Raises:
        OSError: The write or the replace failed.
    """
    try:
        temporary.write_bytes(data)
        replace_file(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
