"""register-env's progress: phase lines with their times, a heartbeat, a log.

``wfc register-env`` can run for most of an hour (a conda capture on Windows,
a large build), so it says what it is doing. :func:`registration_log` opens a
:class:`Reporter` for one registration; while it is open:

- each phase (the capture and each of its host calls, the staging, the
  build, the solve build and its copy-out off linux-64, the resolve, the
  record, the push, the commit) prints a start line and an end line with
  its time on stderr, on a terminal and off one;
- off a terminal, a heartbeat line names the running phase whenever
  :data:`HEARTBEAT_INTERVAL_S` pass with no line printed (on a terminal the
  build's own spinner shows it is alive);
- every line, every command wfc ran and Docker's full build output (the
  build's ``sink``) also go to the log,
  ``.wfc/logs/register-env/<name>-<UTC time>.log``, which a user can send
  with an issue.

The log folder is created with an ignore file of its own that ignores
everything in it, so a project whose ``.gitignore`` predates the logs folder
keeps a clean ``git status``.

The phases are reported from where they run (:mod:`wfc.environments.build`,
the capture calls in :mod:`~wfc.environments.introspect`,
:mod:`~wfc.environments.host` and :mod:`~wfc.environments.uv`) through the
module functions :func:`phase`, :func:`command` and :func:`build_sink`. They
find the open reporter in a context variable and do nothing when none is
open, so the same code reports nothing for a caller that is not
register-env (``wfc demo``, a test calling :func:`wfc.environments.register`).

The commands are logged where they run, as the argv handed to the process:
every Docker command in :mod:`wfc.environments.docker` (its one ``docker
build`` and the two helpers every other docker call goes through), and
every git command through :func:`wfc.version.observing_commands`, which
:func:`registration_log` holds open. Only the argv is logged, never the
environment or a command's stdin. A command run on another thread is not
logged: the context variables are read on the calling thread.
"""

from __future__ import annotations

import contextlib
import contextvars
import shlex
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Final

from .. import layout

#: Seconds with no line printed, while a phase runs off a terminal, before a
#: heartbeat line names it.
HEARTBEAT_INTERVAL_S: Final[float] = 30.0

#: What every line on stderr starts with.
LINE_PREFIX: Final[str] = "register-env: "

_ACTIVE: contextvars.ContextVar[Reporter | None] = contextvars.ContextVar(
    "wfc_register_env_reporter", default=None)


def format_elapsed(seconds: float) -> str:
    """Render a duration for a phase line: ``4.2s`` under a minute, ``3m 07s`` after.

    Args:
        seconds: The duration.

    Returns:
        The rendered duration.
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


@dataclass
class _Phase:
    """A running phase: its name, its depth and when it started."""

    name: str
    depth: int
    started: float


class Reporter:
    """One registration's phase lines, heartbeat and log.

    Writes are serialised by one lock: a heartbeat never prints after its
    phase's end line, and Docker's output (written from the build's reader
    thread) never interleaves a line.
    """

    def __init__(self, log_path: Path | None, log_file: IO[str] | None, *,
                 stream: IO[str], heartbeat_s: float | None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """Start a reporter.

        Args:
            log_path: The log's path, or ``None`` when it could not be opened.
            log_file: The open log, or ``None``.
            stream: Where the lines are printed (stderr).
            heartbeat_s: Seconds of silence before a heartbeat line, or
                ``None`` for no heartbeat (on a terminal).
            clock: The monotonic clock the times are read from.
        """
        self.log_path = log_path
        self._log_file = log_file
        self._stream = stream
        self._heartbeat_s = heartbeat_s
        self._clock = clock
        self._lock = threading.Lock()
        self._phases: list[_Phase] = []
        self._last_line = clock()
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None
        if heartbeat_s is not None and heartbeat_s > 0:
            self._beat = threading.Thread(target=self._heartbeat, daemon=True,
                                          name="register-env-heartbeat")
            self._beat.start()

    # --- writing ----------------------------------------------------------

    def _log(self, text: str) -> None:
        """Append one line to the log, stamped with the UTC time (lock held)."""
        if self._log_file is None:
            return
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            self._log_file.write(f"{stamp} {text}\n")
            self._log_file.flush()
        except (OSError, ValueError):
            pass  # a log that cannot be written never stops the registration

    def _show(self, text: str) -> None:
        """Print one line on the stream and log it (lock held)."""
        try:
            print(f"{LINE_PREFIX}{text}", file=self._stream, flush=True)
        except (OSError, ValueError):
            pass
        self._last_line = self._clock()
        self._log(text)

    def note(self, text: str) -> None:
        """Print a line on stderr and log it.

        Args:
            text: The line, without the ``register-env:`` prefix.
        """
        with self._lock:
            self._show(text)

    def log(self, text: str) -> None:
        """Write a line to the log only.

        Args:
            text: The line.
        """
        with self._lock:
            self._log(text)

    def command(self, argv: Sequence[str]) -> None:
        """Log a command wfc runs, as a shell would show it.

        Args:
            argv: The command and its arguments.
        """
        self.log("$ " + shlex.join(str(a) for a in argv))

    def output(self, line: str) -> None:
        """Log one line of a command's output: the build's sink.

        Args:
            line: The output line, without its newline.
        """
        self.log("| " + line)

    # --- phases -----------------------------------------------------------

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Report a phase: a start line, then an end line with its time.

        The end line says ``done``, ``failed`` (an exception) or ``stopped``
        (Ctrl-C); the exception is re-raised.

        Args:
            name: The phase's name.

        Yields:
            Nothing; the phase runs in the ``with`` block.
        """
        with self._lock:
            entry = _Phase(name, len(self._phases), self._clock())
            self._phases.append(entry)
            self._show(f"{'  ' * entry.depth}{name} started")
        outcome = "failed"
        try:
            yield
            outcome = "done"
        except KeyboardInterrupt:
            outcome = "stopped"
            raise
        finally:
            with self._lock:
                if entry in self._phases:
                    self._phases.remove(entry)
                took = format_elapsed(self._clock() - entry.started)
                self._show(f"{'  ' * entry.depth}{name} {outcome} ({took})")

    def _heartbeat(self) -> None:
        """Name the innermost running phase after each silent interval."""
        interval = float(self._heartbeat_s or 0)
        tick = min(1.0, interval / 5)
        while not self._stop.wait(tick):
            with self._lock:
                if not self._phases:
                    continue
                now = self._clock()
                if now - self._last_line < interval:
                    continue
                running = self._phases[-1]
                took = format_elapsed(now - running.started)
                self._show(f"{'  ' * running.depth}{running.name} still "
                           f"running ({took})")

    def close(self) -> None:
        """Stop the heartbeat and close the log; nothing is printed after."""
        self._stop.set()
        if self._beat is not None:
            self._beat.join(timeout=5)
        with self._lock:
            self._phases.clear()
            if self._log_file is not None:
                with contextlib.suppress(OSError, ValueError):
                    self._log_file.close()
                self._log_file = None


def _open_log(project_dir: Path, name: str, started: datetime
              ) -> tuple[Path | None, IO[str] | None]:
    """Create the log folder with its ignore file, and open a new log.

    Args:
        project_dir: The project root.
        name: The env being registered.
        started: When the registration started.

    Returns:
        The log's path and the open file, or ``(None, None)`` when the
        folder or the file cannot be created.
    """
    path = layout.register_env_log_path(project_dir, name, started)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ignore = path.parent / layout.LOG_DIR_IGNORE_FILENAME
        if not ignore.exists():
            ignore.write_text(layout.LOG_DIR_IGNORE_CONTENT, encoding="utf-8",
                              newline="\n")
        # Appended, so two registrations of one env in the same second share
        # a log rather than one overwriting the other.
        handle = open(path, "a", encoding="utf-8", newline="\n")  # noqa: SIM115
    except OSError:
        return None, None
    return path, handle


@contextlib.contextmanager
def registration_log(project_dir: Path, name: str, *,
                     stream: IO[str] | None = None) -> Iterator[Reporter]:
    """Open one registration's reporter and log, the reporter the phases find.

    The heartbeat runs only off a terminal, every
    :data:`HEARTBEAT_INTERVAL_S` seconds of silence (read when the log is
    opened). When the log cannot be created a line says so and the
    registration goes on with its lines on stderr only.

    Args:
        project_dir: The project root.
        name: The env being registered.
        stream: Where the lines are printed; ``sys.stderr`` when ``None``.

    Yields:
        The open :class:`Reporter`; it is closed (heartbeat stopped, log
        closed) on every ending.
    """
    from ..version import observing_commands

    out = sys.stderr if stream is None else stream
    started = datetime.now(UTC)
    path, handle = _open_log(project_dir, name, started)
    try:
        on_terminal = bool(out.isatty())
    except (AttributeError, ValueError):
        on_terminal = False
    reporter = Reporter(path, handle, stream=out,
                        heartbeat_s=None if on_terminal else HEARTBEAT_INTERVAL_S)
    token = _ACTIVE.set(reporter)
    try:
        if path is None:
            reporter.note("the log could not be created under "
                          f"{layout.register_env_log_dir(project_dir)}; "
                          "the lines below are not kept")
        reporter.log(f"wfc register-env {name}, started "
                     f"{started.isoformat(timespec='seconds')}")
        # Every git command the registration runs (the commit's, the remote
        # read) is logged as it is handed to git.
        with observing_commands(reporter.command):
            yield reporter
    finally:
        _ACTIVE.reset(token)
        reporter.close()


def active() -> Reporter | None:
    """Return the open reporter, or ``None`` outside a registration."""
    return _ACTIVE.get()


@contextlib.contextmanager
def phase(name: str) -> Iterator[None]:
    """Report a phase on the open reporter; do nothing when none is open.

    Args:
        name: The phase's name.

    Yields:
        Nothing; the phase runs in the ``with`` block.
    """
    reporter = _ACTIVE.get()
    if reporter is None:
        yield
        return
    with reporter.phase(name):
        yield


def command(argv: Sequence[str]) -> None:
    """Log a command wfc runs on the open reporter; nothing when none is open.

    Read on the calling thread: a command run on another thread is not
    logged.

    Args:
        argv: The command and its arguments.
    """
    reporter = _ACTIVE.get()
    if reporter is not None:
        reporter.command(argv)


@contextlib.contextmanager
def host_call(label: str, argv: Sequence[str]) -> Iterator[None]:
    """Report one host call of the capture: a phase, and its command logged.

    Args:
        label: The phase's name (``conda list --explicit``).
        argv: The command run.

    Yields:
        Nothing; the call runs in the ``with`` block.
    """
    with phase(label):
        command(argv)
        yield


def build_sink() -> Callable[[str], None] | None:
    """Return the sink a build's output goes to, or ``None`` when none is open.

    Read on the calling thread: the build calls the sink from its reader
    thread, which does not see the context variable.
    """
    reporter = _ACTIVE.get()
    return None if reporter is None else reporter.output
