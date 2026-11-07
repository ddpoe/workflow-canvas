"""The CLI's shared terminal check, ``wfc.confirm``.

The verbs that ask through it are tested in their own files; a real ``wfc``
process with stdin from the null device is in
``tests/test_register_method_confirm.py``.
"""

import os
import sys

import pytest

from wfc.confirm import _is_console


@pytest.mark.skipif(sys.platform != "win32", reason="the console probe is Windows-only")
def test_the_null_device_is_not_a_console_on_windows():
    """Tier 1: on Windows ``isatty()`` is true for ``NUL`` (a character
    device), so the check also asks for a console, and ``NUL`` is not one."""
    with open(os.devnull, encoding="utf-8") as nul:
        assert nul.isatty()
        assert _is_console(nul.fileno()) is False
