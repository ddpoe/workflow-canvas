"""Process and OS boundary: subprocesses, binaries, sockets, files, the prompt.

Everything here stands in for something outside the interpreter: a program
the code would have run, a file operation the OS would have performed, a
socket, the user at a prompt, the wall clock. The census found each of these
patched at several sites in several shapes; the parameters below are those
shapes, so a site's fake is a call with the handler it already wrote.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ._entry import fake

#: ``subprocess.run`` as it was before any fake, for a handler that delegates.
_REAL_RUN = subprocess.run


def real_subprocess_run(*args, **kwargs):
    """Call the real ``subprocess.run`` from inside a handler.

    Args:
        *args: Passed through.
        **kwargs: Passed through.

    Returns:
        The real ``CompletedProcess``.
    """
    return _REAL_RUN(*args, **kwargs)


def canned_process(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Return a handler that reports one fixed result for every command.

    Args:
        returncode: The exit status.
        stdout: Captured stdout text.
        stderr: Captured stderr text.

    Returns:
        A ``handler(cmd, *args, **kwargs)`` for :func:`fake_subprocess_run`.
    """
    def handler(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=returncode,
                                           stdout=stdout, stderr=stderr)
    return handler


def refusing_process(reason: str):
    """Return a handler that fails the test on any command.

    Args:
        reason: The assertion message.

    Returns:
        A handler for :func:`fake_subprocess_run`.
    """
    def handler(cmd, *args, **kwargs):
        raise AssertionError(f"{reason}: {cmd!r}")
    return handler


@fake(
    boundary="subprocess.run -- git (the readiness probes and init's own "
             "git init; wfc.version runs git through Popen, which this does "
             "not reach), docker info (check_docker), conda / pip (the live env "
             "capture), and the dev loop's docker run",
    preserves="the argv and kwargs the caller built; with `only`, every "
              "command for another program reaches the real subprocess.run",
    not_proven="that the program exists or answers as the canned result "
               "says; a refusing handler proves only that nothing was run",
    backed_by="pm_mvp::tests.test_preflight_checks::test_check_git_healthy",
)
def fake_subprocess_run(monkeypatch, handler: Callable, *,
                        only: str | None = None) -> None:
    """Replace ``subprocess.run`` with a handler.

    One global: ``wfc.execution.readiness.subprocess.run``,
    ``wfc.environments.introspect.subprocess.run`` and
    ``wfc.environments.dev_loop.subprocess.run`` are all the one
    ``subprocess`` module, so patching its ``run`` reaches every caller.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        handler: ``handler(cmd, *args, **kwargs)`` returning a
            ``CompletedProcess`` (or raising). See :func:`canned_process`,
            :func:`refusing_process`, :func:`real_subprocess_run`.
        only: A program name; when given, only a command whose ``argv[0]``
            is that program reaches the handler and every other command
            runs for real.
    """
    def run(cmd, *args, **kwargs):
        if only is not None:
            argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
            if not (argv and argv[0] == only):
                return _REAL_RUN(cmd, *args, **kwargs)
        return handler(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


class _FakeBuildProcess:
    """A ``docker build`` process that streams canned output and exits."""

    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        self._lines = output.splitlines(keepends=True)
        self.stdout = iter(self._lines)

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


@fake(
    boundary="subprocess.Popen inside wfc.environments.docker.build -- the "
             "docker build process",
    preserves="the argv and the env the build merges (both recorded), the "
              "streamed output and the exit status the caller reads",
    not_proven="that an image is built or that BuildKit honours the env",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_generated_pixi_dockerfile_builds_and_records_live_interpreter",
)
def fake_docker_build_process(monkeypatch, *, returncode: int = 0,
                              output: str = "", captured: dict | None = None
                              ) -> None:
    """Replace the docker build's ``Popen`` with a canned process.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        returncode: The build's exit status.
        output: The build's streamed output.
        captured: When given, receives ``cmd`` and ``env`` from the spawn.
    """
    def spawn(cmd, env=None, **kwargs):
        if captured is not None:
            captured["cmd"] = cmd
            captured["env"] = env
        return _FakeBuildProcess(returncode, output)

    monkeypatch.setattr(subprocess, "Popen", spawn)


@fake(
    boundary="shutil.which -- whether a binary (git, docker) is on PATH",
    preserves="every name not listed resolves for real",
    not_proven="that the binary is really absent or present",
    backed_by="pm_mvp::tests.test_preflight_checks::test_check_git_healthy",
)
def stub_binary_lookup(monkeypatch, **binaries: str | None) -> None:
    """Pin what ``shutil.which`` answers for the named binaries.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        **binaries: Name -> path, or ``None`` for "not installed". A name
            not listed is looked up for real.
    """
    real_which = shutil.which

    def which(name, *args, **kwargs):
        if name in binaries:
            return binaries[name]
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)


class _AlwaysBusy:
    """A socket whose every bind fails."""

    def __init__(self, *args, **kwargs):
        pass

    def bind(self, addr):
        raise OSError("simulated busy")

    def close(self):
        pass


@fake(
    boundary="socket.socket as wfc.environments.dev_loop binds it -- the "
             "port probe of the Jupyter auto-pick",
    preserves="the auto-pick's own loop over the range",
    not_proven="that a genuinely exhausted port range is reported as such",
    backed_by="undrivable: every port in a range cannot be held busy on a "
              "developer's machine or CI runner",
)
def always_busy_socket(monkeypatch) -> None:
    """Make every socket bind fail, so the port auto-pick exhausts its range.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    from wfc.environments import dev_loop

    monkeypatch.setattr(dev_loop.socket, "socket", _AlwaysBusy)


@fake(
    boundary="os.rename -- the cache's move-mode landing",
    preserves="everything around the call; the replacement decides what a "
              "rename does (raise, land a winner first, or both)",
    not_proven="that a real filesystem raises what the replacement raises",
    backed_by="pm_mvp::tests.test_content_hash::TestCacheOperations."
              "test_cache_file_creates_two_level_structure",
)
def fake_os_rename(monkeypatch, replacement: Callable) -> None:
    """Replace ``os.rename``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``replacement(src, dst)``.
    """
    monkeypatch.setattr(os, "rename", replacement)


@fake(
    boundary="pathlib.Path.replace -- the cache's copy-mode landing",
    preserves="everything around the call",
    not_proven="that a real filesystem raises what the replacement raises",
    backed_by="pm_mvp::tests.test_content_hash::TestCacheOperations."
              "test_cache_file_creates_two_level_structure",
)
def fake_path_replace(monkeypatch, replacement: Callable) -> None:
    """Replace ``pathlib.Path.replace``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``replacement(self, target)``.
    """
    monkeypatch.setattr(pathlib.Path, "replace", replacement)


@fake(
    boundary="os.chmod -- the cache's read-only marking",
    preserves="everything around the call; a replacement that delegates "
              "keeps the real mode change",
    not_proven="that a real filesystem refuses the read-only modes",
    backed_by="pm_mvp::tests.test_provenance::"
              "test_cache_file_leaves_new_entries_read_only",
)
def fake_os_chmod(monkeypatch, replacement: Callable) -> None:
    """Replace ``os.chmod``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        replacement: ``replacement(path, mode, *args, **kwargs)``.
    """
    monkeypatch.setattr(os, "chmod", replacement)


@fake(
    boundary="shutil.copy2 as wfc.storage.cache binds it -- the restore's "
             "file copy",
    preserves="the restore's own hash comparison before the copy",
    not_proven="that a copy lands the bytes; the entry exists to prove a "
               "copy was NOT made",
    backed_by="pm_mvp::tests.test_content_hash::TestCacheOperations."
              "test_restore_from_cache",
)
@contextmanager
def recording_file_copy():
    """Record the cache's ``copy2`` calls instead of copying.

    Yields:
        The list of ``(src, dst)`` pairs the restore asked for.
    """
    calls: list[tuple] = []
    with patch("wfc.storage.cache.shutil.copy2",
               side_effect=lambda s, d, *a, **k: calls.append((s, d))):
        yield calls


@fake(
    boundary="pathlib.Path.home -- where init's default DVC archive lands",
    preserves="everything that reads the home directory, redirected to a "
              "temp directory",
    not_proven="anything about the real home; the archive-leak invariant "
               "forbids writing there",
    backed_by="undrivable: no test can call Path.home unfaked; the suite's "
              "home-archive leak guard forbids writing under the real home",
)
def redirect_home(monkeypatch, home: Path) -> Path:
    """Point ``Path.home()`` at a temp directory for the test's duration.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        home: The directory to report; created when missing.

    Returns:
        ``home``.
    """
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return home


@fake(
    boundary="builtins.input -- the interactive prompt (init's registry "
             "prompt, delete-env's confirmation)",
    preserves="the prompt text, recorded when asked",
    not_proven="that a user at a terminal is asked, or what they see",
    backed_by="undrivable: no terminal in the suite",
)
def stub_interactive_prompt(monkeypatch, answer: str = "", *,
                            prompts: list[str] | None = None) -> None:
    """Answer every ``input()`` with one string.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        answer: What every prompt returns.
        prompts: When given, receives each prompt's text -- a test that
            asserts nothing was asked checks it stays empty.
    """
    def prompt(text=""):
        if prompts is not None:
            prompts.append(text)
        return answer

    monkeypatch.setattr("builtins.input", prompt)


@fake(
    boundary="wfc.init._stdin_is_interactive -- whether stdin is a terminal "
             "(init's existing-project confirmation and archive prompt)",
    preserves="everything init decides from the answer: prompting, or "
              "refusing with nothing changed",
    not_proven="that a real terminal is detected as one",
    backed_by="undrivable: no terminal in the suite",
)
def stub_terminal(monkeypatch, interactive: bool) -> None:
    """Make init see stdin as a terminal, or as not one.

    Signed by the user for one use only (D-23): the test that ``init`` in
    an existing project, with a terminal present, lists its changes and
    asks. The no-terminal refusal is tested with a real ``wfc init``
    process whose stdin is a pipe, never through this fake.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        interactive: What ``_stdin_is_interactive`` answers.
    """
    monkeypatch.setattr("wfc.init._stdin_is_interactive", lambda: interactive)


@fake(
    boundary="uvicorn.run (wfc canvas) and wfc.demo.scaffold._serve (wfc "
             "demo) -- the server bind",
    preserves="everything before the bind: project resolution, the export "
              "of WFC_PROJECT_ROOT, the demo's preflight and scaffolding; "
              "the bind's arguments are recorded",
    not_proven="that a server serves on the socket",
    backed_by="undrivable: binding a port and serving is outside the suite",
)
def stub_server_bind(monkeypatch, *, started: list, where: str = "uvicorn"
                     ) -> None:
    """Record the server bind instead of binding.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        started: Receives ``(args, kwargs)`` per bind attempt.
        where: ``"uvicorn"`` for ``uvicorn.run``; ``"demo"`` for the demo
            scaffold's ``_serve``.
    """
    def bind(*args, **kwargs):
        started.append((args, kwargs))
        return 0

    if where == "uvicorn":
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", bind)
    elif where == "demo":
        import wfc.demo.scaffold as scaffold

        monkeypatch.setattr(scaffold, "_serve", bind)
    else:
        raise ValueError(f"where must be 'uvicorn' or 'demo', not {where!r}")


@fake(
    boundary="time.time / time.monotonic -- the wall clock",
    preserves="every caller's arithmetic over a clock that stands still",
    not_proven="anything time-dependent",
    backed_by="undrivable: the clock cannot be driven to a chosen value",
)
def stub_wall_clock(monkeypatch, now: float) -> None:
    """Freeze the clock at one value.

    Declared for the registry's completeness: no site fakes the clock today.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        now: What ``time.time()`` and ``time.monotonic()`` report.
    """
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(time, "monotonic", lambda: now)


@fake(
    boundary="wfc.environments.introspect.conda_list_explicit -- the conda "
             "binary's explicit package list",
    preserves="the staging around it, and the env path asked for",
    not_proven="that conda answers with that list",
    backed_by="pm_mvp::tests.test_host_tool_witnesses::"
              "test_conda_lists_a_real_env_explicitly",
)
def fake_conda_list_explicit(monkeypatch, explicit_list: str, *,
                             seen: dict | None = None) -> None:
    """Replace the conda explicit-list capture with a canned list.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        explicit_list: The text returned.
        seen: When given, receives ``env_path`` under that key.
    """
    def capture(env_path):
        if seen is not None:
            seen["env_path"] = Path(env_path)
        return explicit_list

    monkeypatch.setattr("wfc.environments.introspect.conda_list_explicit",
                        capture)


@fake(
    boundary="wfc.environments.introspect.pip_freeze_best_effort -- the pip "
             "subprocess of the live env capture",
    preserves="the staging around it, and the interpreter asked for",
    not_proven="that pip answers with that freeze",
    backed_by="pm_mvp::tests.test_host_tool_witnesses::"
              "test_pip_freeze_of_the_running_interpreter_lists_pytest",
)
def fake_pip_freeze(monkeypatch, output: str, *,
                    frozen_from: list[Path] | None = None) -> None:
    """Replace the pip-freeze capture with a canned output.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        output: The freeze text returned.
        frozen_from: When given, receives the interpreter path per call.
    """
    def freeze(env_python):
        if frozen_from is not None:
            frozen_from.append(Path(env_python))
        return output

    monkeypatch.setattr("wfc.environments.introspect.pip_freeze_best_effort",
                        freeze)
