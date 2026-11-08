"""A git command observer that fails is logged at debug level and never stops git.

``wfc.version._git`` hands each argv to the observer ``observing_commands``
set (register-env's log); an observer that raises is dropped, and the drop is
logged at debug level with its traceback (ADR-014: no silent swallow). git
runs for real.
"""

from __future__ import annotations

import logging

from wfc import version


def test_an_observer_that_raises_is_logged_at_debug_and_git_still_runs(tmp_path, caplog):
    """The observer's error is a debug record with exc_info; git's answer is returned."""
    seen: list[list[str]] = []

    def broken(argv):
        seen.append(list(argv))
        raise RuntimeError("the log file is gone")

    caplog.set_level(logging.DEBUG, logger="wfc.version")
    with version.observing_commands(broken):
        proc = version._git(["git", "--version"], tmp_path)

    assert proc.returncode == 0 and proc.stdout.startswith("git version")
    assert seen == [["git", "-c", "core.longpaths=true", "--version"]]
    (record,) = [r for r in caplog.records if r.name == "wfc.version"]
    assert record.levelno == logging.DEBUG
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError
    assert "the log file is gone" in caplog.text
