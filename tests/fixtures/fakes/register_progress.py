"""register-env's progress boundary: how long the heartbeat waits.

``wfc.environments.progress`` prints a heartbeat off a terminal after
``HEARTBEAT_INTERVAL_S`` seconds (30 in production) with no line shown while a
phase runs. :func:`short_register_env_heartbeat` shortens that wait so a test
sees a heartbeat during a build that runs well under a second.
"""

from __future__ import annotations

from ._entry import fake


@fake(
    boundary="wfc.environments.progress.HEARTBEAT_INTERVAL_S -- how long "
             "register-env's reporter waits with no line shown before it "
             "prints a heartbeat off a terminal",
    preserves="everything: the phases, their lines and times, the heartbeat "
              "thread, its stop on every ending and the log; only the wait "
              "before a heartbeat is shorter",
    not_proven="the production interval (30 s)",
    backed_by="undrivable: the wall clock; a test that waits 30 s per "
              "heartbeat is not run",
)
def short_register_env_heartbeat(monkeypatch, seconds: float) -> None:
    """Shorten the wait before register-env's heartbeat line.

    Read when a registration's log is opened, so call it before the
    registration starts.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        seconds: The wait with no line shown before a heartbeat.
    """
    from wfc.environments import progress

    monkeypatch.setattr(progress, "HEARTBEAT_INTERVAL_S", seconds)
