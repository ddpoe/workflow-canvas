"""Docker subprocess boundary for the container env backend.

Centralizes the ``docker`` CLI invocations wfc makes while building and
registering envs (``wfc register-env``); runtime dispatch assembles its
``docker run`` argv in :mod:`wfc.environments.argv` instead. Tests mock
the functions here rather than ``subprocess.run`` directly, which keeps
the boundary small.

Functions:

- :func:`build` — ``docker build -t <tag> <dir>`` with BuildKit enabled,
  plus a ``--label k=v`` flag per label asked for.
  ``DOCKER_BUILDKIT=1`` is merged into a copy of ``os.environ`` so the
  caller's PATH / DOCKER_HOST / HOME / proxy vars survive the spawn. The
  client runs in its own process group, marked as wfc's by
  :data:`BUILD_CLIENT_MARK` in that same copy; each output line goes to an
  optional sink as it is read; a Ctrl-C is passed on to the client
  (:func:`interrupt_process_group`), and only a client that ignores it for
  :data:`BUILD_INTERRUPT_WAIT_S` has its whole tree ended
  (:func:`terminate_process_tree`). Either way it raises
  :class:`BuildInterruptedError`.
- :func:`image_inspect` — ``docker image inspect <ref> --format '{{.Id}}'``.
  Returns the digest string verbatim, including the ``sha256:`` prefix.
- :func:`image_content` — ``docker image inspect <ref> --format '{{json .}}'``
  read for the image's layers and its ``Env``, ``User`` and ``WorkingDir``:
  the content the env fingerprint is rendered from.
- :func:`image_labels` — the same inspect, read for the image's labels.
- :func:`build_labelled` — a copy of a local image with labels added: a
  build whose Dockerfile is only ``FROM`` the image, so the copy's layers
  and run-time config are the source's.
- :func:`repo_digest` — the ``RepoDigests`` entry for one repository: the
  registry digest a byo registration records for an image outside
  ``local/``.
- :func:`pull` — ``docker pull <ref>``. Only used on the BYO branch when
  the image is not already present in the local daemon.
- :func:`local_image_ref` — the first of several references the daemon holds.
- :func:`save_image` — ``docker save -o <file> <ref>``, the image copy a
  rerun bundle carries; the only ``docker save`` wfc runs.

- :func:`copy_out` — the hardened copy-out: ``docker create`` with the
  hardened flag set (the container is never started), ``docker cp`` of one
  path to a host directory, then ``docker rm``. Reads what an image holds
  without running anything in it.
- :func:`run_hardened` — the hardened run: ``docker run --rm`` with the
  same flag set, a command, and the network only when the caller asks.

The hardened flag set is defined once (:func:`hardened_flags`): no mounts,
``--network none`` unless asked, ``--read-only``, ``--cap-drop ALL`` and
``--security-opt no-new-privileges``. Both hardened calls take a
digest-pinned image only (a tag-only ref is refused before Docker runs),
stop each docker call after a timeout, cap the output they accept, and
always remove their container, on success or failure.

The registry side:

- :func:`tag` — ``docker tag <source> <target>``, the name a push needs.
- :func:`push` — ``docker push <ref>``, returning the digest the registry
  reported.
- :func:`read_manifest` — ``docker manifest inspect <ref>``, one read of the
  registry, classified as found, missing, unauthorized or unreachable
  (:class:`RegistryRead`).

Every registry call goes through the Docker CLI and its own credential
store: wfc never reads Docker's config or a credential helper, and takes no
token.

All functions raise :class:`RuntimeError` on non-zero exit and surface
docker's own error text in the message — the user sees the real
``unable to resolve image`` / ``COPY failed`` text, not a wrapped
``CalledProcessError``.  (:func:`build` streams its output and attaches
the tail of the combined stream; the others attach stderr verbatim.)

Timeouts: a query has one, a transfer or build does not. The daemon queries
(``image_inspect``, ``image_content``, ``image_labels``, ``repo_digest``,
``tag``) stop after
:data:`DOCKER_QUERY_TIMEOUT_S` and raise :class:`DockerTimeoutError`, never
an :class:`ImageNotFoundError`; :func:`read_manifest` stops after
:data:`REGISTRY_READ_TIMEOUT_S` and answers unreachable. ``build``, ``pull``
and ``push`` run unbounded: no fixed bound tells a slow transfer from a
stalled one, and the user stops a stalled one with Ctrl-C.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from axiom_annotations import task

from . import progress

# BuildKit plain-progress step marker: `#7 [3/5] RUN pip install ...`,
# optionally with a stage name (`#7 [stage-1 3/5] COPY ...`).
_BUILD_STEP_RE = re.compile(r"^#\d+\s+\[(?:[\w][\w.-]*\s+)?(\d+)/(\d+)\]")


def _parse_build_step(line: str) -> tuple[int, int] | None:
    """Extract ``(step, total)`` from a BuildKit progress line, else None."""
    m = _BUILD_STEP_RE.match(line)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def _format_elapsed(seconds: float) -> str:
    """Render elapsed seconds as ``4m 03s`` / ``45s``."""
    s = int(seconds)
    if s >= 60:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s}s"


# --- the build client's process group --------------------------------------
#
# A killed `docker build` client orphans its build: on Windows killing
# docker.exe leaves its docker-buildx.exe plugin child (the real BuildKit
# client) holding the build, which runs on. An interrupt sent to the client's
# process group ends the build at once. So the build client runs in its own
# group, and the only way wfc ends it is the interrupt first, then, for a
# client that ignored it, the whole tree. Nothing here kills docker.exe alone.

#: How long a build client may take to end on an interrupt before its tree
#: is terminated. A client that obeys ends in a second or two.
BUILD_INTERRUPT_WAIT_S: Final = 15.0

#: How long a terminated process may take to go before it is killed.
_TERMINATE_GRACE_S: Final = 5.0

#: The variable :func:`build` sets to ``1`` in the environment of every
#: client it starts in a process group of its own. Its plugin inherits it.
#: An orphaned client that shows it leads a group wfc started, so
#: ``wfc prune-envs`` may send that group an interrupt; any other orphan is
#: terminated instead (``wfc.environments.build_clients``). It reaches only
#: the client's environment: not the argv, the recipe, a build argument or
#: anything an env's fingerprint is rendered from.
BUILD_CLIENT_MARK: Final = "WFC_BUILD_OWN_GROUP"

# The Windows constants, spelled out where the platform lacks them so both
# branches can be exercised on any host.
_CREATE_NEW_PROCESS_GROUP: Final = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP",
                                           0x00000200)
_CTRL_BREAK_EVENT: Final = getattr(signal, "CTRL_BREAK_EVENT", 1)


class BuildInterruptedError(KeyboardInterrupt):
    """A Ctrl-C stopped a ``docker build``; the interrupt was passed on to it.

    A ``KeyboardInterrupt``, so a caller that does not know this type ends
    on it as on any Ctrl-C.

    Attributes:
        client_ended: True when the client ended on the interrupt within the
            bounded wait; False when its process tree had to be terminated.
    """

    def __init__(self, client_ended: bool):
        super().__init__("docker build interrupted")
        self.client_ended = client_ended


class CopyOutInterruptedError(KeyboardInterrupt):
    """A Ctrl-C stopped :func:`copy_out`; its container was removed first.

    A ``KeyboardInterrupt``, so a caller that does not know this type ends
    on it as on any Ctrl-C.
    """

    def __init__(self) -> None:
        super().__init__("copy-out interrupted")


def _on_windows() -> bool:
    """Say whether this host is Windows (the seam the process-group code reads)."""
    return sys.platform == "win32"


def _own_group_kwargs() -> dict:
    """Return the ``Popen`` keywords that start a child in its own process group.

    Windows: a new process group, so the console's Ctrl-C does not reach the
    child and a ``CTRL_BREAK_EVENT`` can be sent to the group alone. POSIX: a
    new session (and so a new group), so the terminal's ``SIGINT`` does not
    reach it and ``os.killpg`` can signal it.
    """
    if _on_windows():
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def interrupt_process_group(group_id: int) -> bool:
    """Send an interrupt to a process group this process started.

    Windows: ``CTRL_BREAK_EVENT`` to the group (``GenerateConsoleCtrlEvent``;
    the group id is its leader's pid, and the group must share this
    process's console). POSIX: ``SIGINT`` to the group (``os.killpg``).

    Args:
        group_id: The process group id: the pid of the child started with
            :func:`_own_group_kwargs`.

    Returns:
        True when the interrupt was sent; False when it could not be (no
        console to send it through, the group gone).
    """
    try:
        if _on_windows():
            os.kill(group_id, _CTRL_BREAK_EVENT)
        else:
            os.killpg(group_id, signal.SIGINT)  # type: ignore[attr-defined, unused-ignore]
    except OSError:
        return False
    return True


def _process_tree(pid: int) -> list:
    """Return a process's descendants then the process, as psutil processes.

    Args:
        pid: The tree's root.

    Returns:
        The descendants (deepest reachable first in psutil's order) followed
        by the root; empty when the root is gone or unreadable.
    """
    import psutil

    try:
        root = psutil.Process(pid)
        return [*root.children(recursive=True), root]
    except psutil.Error:
        return []


def terminate_process_tree(procs: list) -> bool:
    """Terminate processes children first, then kill whatever outlives a grace.

    The last resort for a client that ignored its interrupt. Given a client's
    whole tree (:func:`_process_tree`), the plugin goes before
    ``docker.exe``, so the process holding the build never outlives its
    parent's end. A process already gone is skipped.

    Args:
        procs: psutil processes, in the order to terminate them.

    Returns:
        True when every process is gone without an error.
    """
    import psutil

    ok = True
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            ok = False
    try:
        _, alive = psutil.wait_procs(procs, timeout=_TERMINATE_GRACE_S)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=_TERMINATE_GRACE_S)
    except psutil.Error:
        return False
    return ok and not alive


def _end_build_client(proc: subprocess.Popen) -> bool:
    """End a build client on Ctrl-C: interrupt, bounded wait, then the tree.

    The tree is read before the interrupt, so a plugin whose ``docker.exe``
    ends first is still known. Whatever of it is alive after the wait (or
    after a second Ctrl-C cuts the wait short) is terminated.

    Args:
        proc: The running ``docker build`` client, started in its own group.

    Returns:
        True when the client ended on the interrupt within the wait.
    """
    before = _process_tree(proc.pid)
    sent = interrupt_process_group(proc.pid)
    if sent:
        try:
            proc.wait(timeout=BUILD_INTERRUPT_WAIT_S)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
    ended = sent and proc.poll() is not None
    known = {p.pid for p in before}
    tree = [*before, *(p for p in _process_tree(proc.pid) if p.pid not in known)]
    leftover = [p for p in tree if _still_running(p)]
    if leftover:
        terminate_process_tree(leftover)
        proc.poll()
    return ended


def _still_running(proc) -> bool:
    """Say whether a psutil process is still running (False once it is gone)."""
    import psutil

    try:
        return bool(proc.is_running())
    except psutil.Error:
        return False


@task(purpose="Run docker build -t <tag> <dir> with BuildKit enabled, the "
              "client in a process group of its own and marked as wfc's in "
              "its environment; a Ctrl-C is passed on to that group as an "
              "interrupt, and only a client that ignores it for a bounded "
              "wait has its whole process tree terminated",
      critical="never kills docker.exe alone (its plugin would hold the "
               "build on); the mark rides in the client's environment only, "
               "never the argv, the recipe or a build argument")
def build(dockerfile_dir: str | Path, tag: str,
          labels: dict[str, str] | None = None, *,
          sink: Callable[[str], None] | None = None) -> None:
    """Run ``docker build -t <tag> <dockerfile_dir>`` with BuildKit on.

    Each of *labels* becomes a ``--label key=value`` flag, so the image
    carries it without the Dockerfile changing. A label is image metadata:
    the env fingerprint, rendered from the image's layers and run-time
    config, does not move with it.

    BuildKit is required by the Dockerfile generators (they emit
    ``# syntax=docker/dockerfile:1.4`` and use ``--mount=type=cache``
    instructions). We merge ``DOCKER_BUILDKIT=1`` into a *copy* of the
    process env so the user's PATH, HOME, DOCKER_HOST, HTTP(S)_PROXY, etc.
    survive — passing ``env={'DOCKER_BUILDKIT': '1'}`` alone would strip
    every other variable and break the build on most setups. The same copy
    carries :data:`BUILD_CLIENT_MARK` ``=1``: the mark that tells
    ``wfc prune-envs`` an orphaned client leads a group wfc started, so it
    may be interrupted. It is in no argv and no build argument, so the
    recipe, the build context and the env fingerprint do not move with it.

    ``--provenance=false`` keeps BuildKit from attaching a provenance
    attestation, so the image is one manifest, not a manifest list holding
    the image and an attestation that records the build's own details.

    Progress: on a TTY, a single in-place spinner line shows a
    user-friendly status (``- building environment — step 3/7 (1m 12s)``)
    parsed from BuildKit's step markers — raw docker output is never
    printed on success.  Off-TTY (CI, piped logs) nothing is printed.
    Every output line goes to *sink*, when given, as it is read. The argv
    (not the environment) is logged as :func:`_run_docker` logs it: to
    ``wfc register-env``'s log, on the thread that opened it only.

    Ctrl-C: the client runs in its own process group, so the console's
    interrupt reaches wfc only. wfc passes it on to the client's group
    (Windows ``CTRL_BREAK_EVENT``, POSIX ``SIGINT``), which makes Docker
    cancel the build, and waits up to :data:`BUILD_INTERRUPT_WAIT_S` for the
    client to exit. Only a client that ignores the interrupt has its whole
    process tree terminated (on Windows ``docker.exe`` and its
    ``docker-buildx.exe`` plugin, which holds the build: killing
    ``docker.exe`` alone would leave the build running).

    Args:
        dockerfile_dir: Directory containing the ``Dockerfile`` to build.
            Used as docker's build context.
        tag: Image tag to assign to the resulting image (e.g.
            ``"my-env:_wfc-build"``).
        labels: Image labels by key (``org.opencontainers.image.source``),
            passed in key order; ``None`` or empty adds none.
        sink: Called with each output line (no newline) as it is read, on
            the reader thread. If it raises, it is called no more, the
            output is still drained, and its exception is raised once the
            client has exited (as the cause of a failed build's error).

    Raises:
        RuntimeError: If docker exits non-zero. The error message carries
            the last 40 lines of the interleaved build output (BuildKit
            reports errors on stdout/stderr mixed; the tail preserves the
            real ``COPY failed`` / ``unable to resolve image`` text). A
            caller with a log of the full output adds its path.
        BuildInterruptedError: On Ctrl-C, once the client has ended,
            saying whether it ended on the interrupt.
    """
    build_env = {**os.environ, "DOCKER_BUILDKIT": "1", BUILD_CLIENT_MARK: "1"}
    label_flags = [flag for key, value in sorted((labels or {}).items())
                   for flag in ("--label", f"{key}={value}")]
    cmd = ["docker", "build", "--provenance=false", "-t", tag, *label_flags,
           str(dockerfile_dir)]
    progress.command(cmd)
    proc = subprocess.Popen(
        cmd,
        env=build_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # docker always emits UTF-8 (BuildKit progress uses multi-byte chars);
        # the Windows default cp1252 decode crashes the reader thread.
        text=True,
        encoding="utf-8",
        errors="replace",
        # Its own process group: the console's Ctrl-C reaches wfc only, and
        # wfc passes it on to the client (see _end_build_client).
        **_own_group_kwargs(),
    )

    # Collect docker's output off the pipe (never shown on success — users
    # get the spinner below, not BuildKit noise), hand each line to the
    # caller's sink as it is read, and track the latest `[x/y]` step marker
    # for the spinner's progress text.
    lines: list[str] = []
    latest_step: list[tuple[int, int] | None] = [None]
    sink_error: list[BaseException] = []

    def _drain() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if sink is not None and not sink_error:
                try:
                    sink(line)
                except Exception as exc:  # the pipe must keep draining
                    sink_error.append(exc)
            step = _parse_build_step(raw)
            if step is not None:
                latest_step[0] = step

    # Single in-place spinner line on a TTY ("building environment — step
    # 3/7 (1m 12s)"); complete silence otherwise (CI / piped logs).
    show_spinner = sys.stderr.isatty()
    frames = "|/-\\"
    start = time.monotonic()
    frame = 0
    reader = threading.Thread(target=_drain, daemon=True)
    try:
        reader.start()
        while proc.poll() is None:
            if show_spinner:
                step = latest_step[0]
                step_txt = f" — step {step[0]}/{step[1]}" if step else ""
                status = (
                    f"\r{frames[frame % len(frames)]} building environment"
                    f"{step_txt} ({_format_elapsed(time.monotonic() - start)})"
                )
                print(status.ljust(79), end="", file=sys.stderr, flush=True)
                frame += 1
            time.sleep(0.2 if show_spinner else 0.5)
    except KeyboardInterrupt:
        client_ended = _end_build_client(proc)
        if reader.is_alive():
            reader.join(timeout=10)
        if show_spinner:
            print("\r" + " " * 79 + "\r", end="", file=sys.stderr, flush=True)
        raise BuildInterruptedError(client_ended=client_ended) from None
    reader.join(timeout=10)
    if show_spinner:
        print("\r" + " " * 79 + "\r", end="", file=sys.stderr, flush=True)

    if proc.returncode != 0:
        tail = "\n".join(lines[-40:])
        raise RuntimeError(
            f"docker build failed (exit {proc.returncode}):\n{tail}"
        ) from (sink_error[0] if sink_error else None)
    if sink_error:
        raise sink_error[0]
    if show_spinner:
        print(
            f"environment built ({_format_elapsed(time.monotonic() - start)})",
            file=sys.stderr,
        )


class ImageNotFoundError(RuntimeError):
    """``docker image inspect`` reached the daemon, and it has no such image."""


class DockerTimeoutError(RuntimeError):
    """A docker query did not finish within its bound: Docker is not answering.

    Never an :class:`ImageNotFoundError`: a daemon that does not answer says
    nothing about whether it holds an image.
    """


#: Seconds a daemon query (``docker image inspect``, ``docker tag``) may take:
#: the bound ``wfc doctor`` gives ``docker info``.
DOCKER_QUERY_TIMEOUT_S: Final[float] = 15.0

#: Seconds a registry read (``docker manifest inspect``: a token exchange and
#: one manifest GET) may take: the bound a ``gh`` call has.
REGISTRY_READ_TIMEOUT_S: Final[float] = 30.0

#: What the daemon prints for an image it lacks, on the classic and the
#: containerd image stores. Any other inspect failure (a daemon that is not
#: running, permission denied) is not a missing image.
_NO_SUCH_IMAGE = ("no such image", "no such object")


def _run_docker(args: list[str], *, timeout: float | None,
                what: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one ``docker`` command and capture its UTF-8 output.

    Every wrapper but :func:`build` (which streams) runs docker here, and
    states its bound: a query passes its timeout, a transfer (``pull``,
    ``push``) passes ``None``, since no fixed bound tells a slow transfer
    from a stalled one.

    The argv run is logged to ``wfc register-env``'s log when the call is
    on the thread that opened it (:func:`wfc.environments.progress.command`);
    a call on another thread, or outside a registration, logs nothing. Only
    the argv is logged, never the environment.

    Args:
        args: The arguments after ``docker``.
        timeout: Seconds before the docker client is stopped, or ``None``
            for no bound.
        what: How the command is named in a timeout's message; the first
            two arguments when not given.

    Returns:
        The completed process, whatever its exit status.

    Raises:
        DockerTimeoutError: The command did not finish within *timeout*.
    """
    cmd = ["docker", *args]
    progress.command(cmd)
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            # docker always emits UTF-8; the Windows default cp1252 decode
            # crashes on its multi-byte progress characters.
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        named = what or "docker " + " ".join(args[:2])
        raise DockerTimeoutError(
            f"{named} did not finish within {timeout:g} s: Docker is not answering"
        ) from exc


#: Seconds the server-version probe may take: it runs while a user waits for
#: the Report a bug page, so a daemon that does not answer at once reads as
#: not running.
DOCKER_VERSION_TIMEOUT_S: Final[float] = 5.0


@task(purpose="Ask the Docker daemon its server version with a short timeout; "
              "None when docker is missing, the daemon does not answer, or it "
              "answers nothing")
def docker_server_version() -> str | None:
    """Return the Docker daemon's version, or ``None`` when it does not answer.

    Runs ``docker version --format {{.Server.Version}}`` with a
    :data:`DOCKER_VERSION_TIMEOUT_S` bound. Never raises: the answer only
    fills a line of a bug report.

    Returns:
        The server version string (e.g. ``"27.3.1"``), or ``None`` when
        ``docker`` is not installed, the daemon is not running or does not
        answer in time, or the answer is empty.
    """
    try:
        proc = _run_docker(["version", "--format", "{{.Server.Version}}"],
                           timeout=DOCKER_VERSION_TIMEOUT_S)
    except (DockerTimeoutError, OSError, subprocess.SubprocessError):
        return None
    version = (proc.stdout or "").strip()
    if proc.returncode != 0 or not version:
        return None
    return version


def _inspect_failure(ref: str, proc: subprocess.CompletedProcess[str]) -> RuntimeError:
    """Return the error a failed ``docker image inspect`` of *ref* raises."""
    message = (f"docker image inspect {ref!r} failed "
               f"(exit {proc.returncode}):\n{proc.stderr}")
    if any(marker in proc.stderr.lower() for marker in _NO_SUCH_IMAGE):
        return ImageNotFoundError(message)
    return RuntimeError(message)


def image_inspect(ref: str) -> str:
    """Return the local image digest for *ref* via ``docker image inspect``.

    The format string ``'{{.Id}}'`` returns the canonical digest string
    including the ``sha256:`` prefix (e.g. ``"sha256:abc123..."``). The
    caller decides whether to strip or attach that prefix when assembling
    a manifest entry.

    Args:
        ref: Image reference understood by the local docker daemon
            (tag, name, or ``<name>@sha256:<digest>``).

    Returns:
        The digest string, including the ``sha256:`` prefix.

    Raises:
        ImageNotFoundError: If the daemon answered and has no such image.
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker exits non-zero for any other reason (the
            daemon is unreachable, permission denied).
    """
    proc = _run_docker(["image", "inspect", ref, "--format", "{{.Id}}"],
                       timeout=DOCKER_QUERY_TIMEOUT_S,
                       what=f"docker image inspect {ref!r}")
    if proc.returncode != 0:
        raise _inspect_failure(ref, proc)
    return proc.stdout.strip()


def image_content(ref: str) -> dict:
    """Return what an image runs with: its layers and its run-time config.

    One ``docker image inspect <ref> --format '{{json .}}'`` call, read for
    ``RootFS.Layers`` and ``Config.Env``, ``Config.User`` and
    ``Config.WorkingDir``. The values are returned as the daemon reports
    them, in its order; :func:`wfc.environments.fingerprint.capture_env_content`
    renders them into the env's content blob. The image's names, tags,
    registry digests and labels are not read.

    Args:
        ref: Image reference understood by the local docker daemon (tag,
            name, ``<name>@sha256:<digest>`` or a bare image ID).

    Returns:
        ``{"layers": [...], "env": [...] | None, "user": str | None,
        "workdir": str | None}``.

    Raises:
        ImageNotFoundError: If the daemon answered and has no such image.
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker exits non-zero for any other reason (the
            daemon is unreachable, permission denied), or prints something
            that is not an image's JSON.
    """
    inspected = _inspect_json(ref)
    rootfs = inspected.get("RootFS") or {}
    config = inspected.get("Config") or {}
    return {
        "layers": rootfs.get("Layers"),
        "env": config.get("Env"),
        "user": config.get("User"),
        "workdir": config.get("WorkingDir"),
    }


def image_labels(ref: str) -> dict[str, str]:
    """Return an image's labels (``Config.Labels``) by key.

    Args:
        ref: Image reference understood by the local docker daemon (tag,
            name, ``<name>@sha256:<digest>`` or a bare image ID).

    Returns:
        The labels; ``{}`` for an image that carries none.

    Raises:
        ImageNotFoundError: If the daemon answered and has no such image.
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: As :func:`image_content` raises for any other failure.
    """
    config = _inspect_json(ref).get("Config") or {}
    return dict(config.get("Labels") or {})


def _inspect_json(ref: str) -> dict:
    """Return ``docker image inspect <ref>``'s JSON for one image.

    Raises:
        ImageNotFoundError: If the daemon answered and has no such image.
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker exits non-zero for any other reason, or
            prints something that is not an image's JSON.
    """
    proc = _run_docker(["image", "inspect", ref, "--format", "{{json .}}"],
                       timeout=DOCKER_QUERY_TIMEOUT_S,
                       what=f"docker image inspect {ref!r}")
    if proc.returncode != 0:
        raise _inspect_failure(ref, proc)
    try:
        inspected = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"docker image inspect {ref!r} printed no image JSON: {exc}"
        ) from exc
    if not isinstance(inspected, dict):
        raise RuntimeError(f"docker image inspect {ref!r} printed no image JSON")
    return inspected


def build_labelled(source: str, target: str, labels: dict[str, str]) -> None:
    """Build *target*: the local image *source* with *labels* added.

    The build context holds one Dockerfile line, ``FROM <target>``, built
    with each label as a ``--label`` flag: the copy has the source's layers
    and run-time config, so its env fingerprint is the source's. BuildKit
    reads a ``FROM`` name, not a bare image ID (an ID is looked up on Docker
    Hub), so *source* is first tagged *target*; the build then moves that
    name to the copy. A ``FROM`` name the daemon holds is not pulled.

    Args:
        source: A local image reference (a tag or a bare image ID).
        target: The local ``<repository>:<tag>`` the copy is named.
        labels: The labels the copy carries, on top of the source's own.

    Raises:
        DockerTimeoutError: If ``docker tag`` does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If the tag or the build fails; the message carries
            docker's text.
    """
    tag(source, target)
    with tempfile.TemporaryDirectory(prefix="wfc-label-") as context:
        (Path(context) / "Dockerfile").write_text(f"FROM {target}\n",
                                                  encoding="utf-8")
        build(context, target, labels=labels)


#: Docker Hub's registry hosts, which ``RepoDigests`` omits from a name.
_DOCKER_HUB_HOSTS = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")


def _familiar_repository(repository: str) -> str:
    """Return *repository* in the familiar form ``RepoDigests`` lists it in.

    Docker lists a Docker Hub repository without its registry host, and an
    official image without ``library/``: ``docker.io/library/alpine`` is
    listed as ``alpine``. Any other registry's name is kept whole.

    Args:
        repository: A ``<host>/<path>`` repository name, without tag or
            digest.

    Returns:
        The name as ``RepoDigests`` spells it.
    """
    for host in _DOCKER_HUB_HOSTS:
        if repository.startswith(host):
            repository = repository.removeprefix(host)
            break
    return repository.removeprefix("library/")


def repo_digest(ref: str, repository: str) -> str:
    """Return the registry digest the local daemon records for *repository*.

    Reads ``docker image inspect <ref> --format '{{json .RepoDigests}}'``
    and returns the digest of the entry whose repository is *repository*.
    That is the manifest digest the registry serves, and the one a
    ``<repository>@sha256:<hex>`` reference resolves on every image store.
    ``.Id`` (see :func:`image_inspect`) is not: on the classic store it is
    the image's config digest.

    Args:
        ref: Image reference understood by the local docker daemon.
        repository: The ``<host>/<path>`` repository the digest belongs to.
            A Docker Hub name matches in either its full or familiar form.

    Returns:
        The digest string, including the ``sha256:`` prefix.

    Raises:
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker exits non-zero, or the image lists no
            registry digest for *repository* (a locally built image lists
            none).
    """
    proc = _run_docker(["image", "inspect", ref, "--format", "{{json .RepoDigests}}"],
                       timeout=DOCKER_QUERY_TIMEOUT_S,
                       what=f"docker image inspect {ref!r}")
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker image inspect {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    entries = json.loads(proc.stdout.strip() or "null") or []
    wanted = _familiar_repository(repository)
    for entry in entries:
        name, _, digest = entry.partition("@")
        if digest and _familiar_repository(name) == wanted:
            return digest
    raise RuntimeError(
        f"image {ref!r} has no registry digest for {repository!r} "
        f"(RepoDigests: {entries}). Pull it from its registry, or build it "
        f"locally under the local/ namespace."
    )


def pull(ref: str) -> None:
    """Run ``docker pull <ref>`` to fetch *ref* into the local daemon.

    Used at registration on the BYO branch when :func:`image_inspect`
    reports the image is not locally resolvable, and at run time by
    :func:`wfc.environments.runnable.pull_and_check` to fetch a version's
    pushed image (any backend) by digest when this machine lacks it. A
    transfer: it runs with no timeout (the user stops a stalled one with
    Ctrl-C).

    Args:
        ref: Image reference to pull. May be a floating-tag ref
            (``reg/img:latest``) or a digest-pinned ref
            (``reg/img@sha256:...``).

    Raises:
        RuntimeError: If docker exits non-zero (network failure,
            unauthorized, unknown image, etc.). The error message
            includes docker's stderr verbatim.
    """
    proc = _run_docker(["pull", ref], timeout=None)
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker pull {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )


def local_image_ref(refs: Iterable[str | None]) -> str | None:
    """Return the first of *refs* the local daemon holds an image under.

    Args:
        refs: Image references in the order to try; ``None`` and empty ones
            are skipped.

    Returns:
        The first reference :func:`image_inspect` resolves, or ``None`` when
        the daemon holds none of them.

    Raises:
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker fails for a reason other than a missing image
            (the daemon is unreachable, permission denied).
    """
    for ref in dict.fromkeys(r for r in refs if r):
        try:
            image_inspect(ref)
        except ImageNotFoundError:
            continue
        return ref
    return None


@task(purpose="Run docker save of one local image into a tar file, the copy a "
              "rerun bundle carries under --with-image",
      inputs="An image reference the local daemon holds; the tar file to write",
      outputs="The tar file, which `docker load` reads back with the same layers "
              "and run-time config")
def save_image(ref: str, dest: Path | str) -> Path:
    """Run ``docker save -o <dest> <ref>``: write one local image to a tar file.

    The only place wfc runs ``docker save``. ``docker load`` of the file
    gives back an image with the same layers and run-time config, so the
    same env fingerprint. A transfer: it runs with no timeout.

    Args:
        ref: A reference the local daemon holds (a tag, a bare image ID or a
            ``<name>@sha256:<digest>``).
        dest: The tar file to write; its folder must exist.

    Returns:
        *dest*, written.

    Raises:
        RuntimeError: If docker exits non-zero (no such image, the daemon is
            unreachable, the disk is full); the message carries docker's
            stderr verbatim.
    """
    dest = Path(dest)
    proc = _run_docker(["save", "-o", str(dest), ref], timeout=None)
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker save {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    return dest


# -----------------------------------------------------------------------------
# The registry side: tag, push, and a manifest read
# -----------------------------------------------------------------------------

def tag(source: str, target: str) -> None:
    """Run ``docker tag <source> <target>``: give a local image another name.

    Args:
        source: A local image reference (a tag, ``<name>@sha256:<digest>`` or
            a bare image ID).
        target: The new ``<repository>:<tag>`` name.

    Raises:
        DockerTimeoutError: If docker does not answer within
            :data:`DOCKER_QUERY_TIMEOUT_S`.
        RuntimeError: If docker exits non-zero; the message carries docker's
            stderr verbatim.
    """
    proc = _run_docker(["tag", source, target], timeout=DOCKER_QUERY_TIMEOUT_S,
                       what=f"docker tag {source!r} {target!r}")
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker tag {source!r} {target!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )


#: The line ``docker push`` ends with on success, on both image stores:
#: ``<tag>: digest: sha256:<hex> size: <n>``.
_PUSHED_DIGEST_RE = re.compile(r"digest:\s*(sha256:[0-9a-f]{64})")


def push(ref: str) -> str | None:
    """Run ``docker push <ref>`` and return the digest the registry reported.

    Docker logs in to the registry with its own credential store; wfc passes
    no credentials and reads none. A transfer: it runs with no timeout.

    Args:
        ref: The ``<repository>:<tag>`` to push; the local daemon must hold
            an image under that name.

    Returns:
        The manifest (or index) digest from docker's ``digest: sha256:...``
        line, including the ``sha256:`` prefix; ``None`` when docker printed
        none (the caller then reads it with :func:`repo_digest`).

    Raises:
        RuntimeError: If docker exits non-zero (not logged in, access
            denied, the registry unreachable); the message carries docker's
            stderr verbatim.
    """
    proc = _run_docker(["push", ref], timeout=None)
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker push {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    found = _PUSHED_DIGEST_RE.findall(proc.stdout)
    return found[-1] if found else None


#: The answers :func:`read_manifest` tells apart.
MANIFEST_FOUND: Final[str] = "found"
MANIFEST_MISSING: Final[str] = "missing"
MANIFEST_UNAUTHORIZED: Final[str] = "unauthorized"
MANIFEST_UNREACHABLE: Final[str] = "unreachable"
MANIFEST_ERROR: Final[str] = "error"

#: What docker prints when the registry answered and has no such manifest:
#: the OCI ``MANIFEST_UNKNOWN`` / ``NAME_UNKNOWN`` errors, and the CLI's own
#: wording for them.
_MANIFEST_MISSING_MARKERS = ("manifest unknown", "name unknown", "no such manifest",
                             "not found")
#: What docker prints when the registry refused the credentials it has, or
#: it has none: not logged in, or no access to the repository.
_MANIFEST_UNAUTHORIZED_MARKERS = ("unauthorized", "denied", "authentication required",
                                  "403 forbidden")
#: What docker prints when no registry answered at all.
_MANIFEST_UNREACHABLE_MARKERS = (
    "connection refused", "no such host", "i/o timeout", "dial tcp",
    "network is unreachable", "tls handshake timeout", "server misbehaving",
    "connectex", "context deadline exceeded", "could not resolve",
)


@dataclass(frozen=True)
class RegistryRead:
    """What a registry answered for one reference, as docker reported it.

    Attributes:
        ref: The reference read.
        state: One of :data:`MANIFEST_FOUND` (the registry has it),
            :data:`MANIFEST_MISSING` (the registry answered and has no such
            tag or repository: reachable and authorised),
            :data:`MANIFEST_UNAUTHORIZED` (not logged in, or no access),
            :data:`MANIFEST_UNREACHABLE` (no registry answered) or
            :data:`MANIFEST_ERROR` (docker failed some other way).
        detail: Docker's own output: the manifest JSON when found, its
            stderr verbatim otherwise.
    """

    ref: str
    state: str
    detail: str

    @property
    def reachable(self) -> bool:
        """Whether the registry answered and accepted the credentials docker has."""
        return self.state in (MANIFEST_FOUND, MANIFEST_MISSING)


def classify_manifest_answer(returncode: int, stderr: str) -> str:
    """Return what a ``docker manifest inspect`` answer says about the registry.

    Args:
        returncode: Docker's exit status.
        stderr: Docker's stderr.

    Returns:
        One of the ``MANIFEST_*`` states (see :class:`RegistryRead`). An
        unauthorized answer wins over a missing one: a registry that hides a
        private repository behind "denied" says nothing about the tag.
    """
    if returncode == 0:
        return MANIFEST_FOUND
    text = stderr.lower()
    if any(marker in text for marker in _MANIFEST_UNAUTHORIZED_MARKERS):
        return MANIFEST_UNAUTHORIZED
    if any(marker in text for marker in _MANIFEST_UNREACHABLE_MARKERS):
        return MANIFEST_UNREACHABLE
    if any(marker in text for marker in _MANIFEST_MISSING_MARKERS):
        return MANIFEST_MISSING
    return MANIFEST_ERROR


#: The registry hosts served from this machine, which docker reads over
#: plain HTTP; the host is matched whole, port aside.
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1"})


def read_manifest(ref: str) -> RegistryRead:
    """Ask the registry for *ref*'s manifest through ``docker manifest inspect``.

    The read goes through the Docker CLI, which uses Docker's own credential
    store: wfc never opens Docker's config or a credential helper, and takes
    no token. A tag that need not exist is a valid question: "no such
    manifest" from the registry still proves it is reachable and accepts the
    credentials docker has. Never raises for a registry's answer: a read
    that does not finish within :data:`REGISTRY_READ_TIMEOUT_S` is
    :data:`MANIFEST_UNREACHABLE`, its detail naming the bound.

    Args:
        ref: A ``<registry>/<repository>:<tag>`` or ``...@sha256:<digest>``
            reference.

    Returns:
        The :class:`RegistryRead`; its ``detail`` is docker's own text.
    """
    args = ["manifest", "inspect"]
    host = ref.split("/", 1)[0].partition(":")[0].lower()
    if host in _LOOPBACK_HOSTS:
        # A local registry serves plain HTTP; docker's daemon allows it for
        # loopback, the client-side manifest command needs the flag.
        args.append("--insecure")
    try:
        proc = _run_docker([*args, ref], timeout=REGISTRY_READ_TIMEOUT_S,
                           what=f"docker manifest inspect {ref!r}")
    except DockerTimeoutError:
        return RegistryRead(ref, MANIFEST_UNREACHABLE,
                            f"docker manifest inspect got no answer within "
                            f"{REGISTRY_READ_TIMEOUT_S:g} s")
    except OSError as exc:
        return RegistryRead(ref, MANIFEST_ERROR,
                            f"docker could not be started: {exc}")
    state = classify_manifest_answer(proc.returncode, proc.stderr)
    detail = proc.stdout if state == MANIFEST_FOUND else proc.stderr
    return RegistryRead(ref, state, detail)


# -----------------------------------------------------------------------------
# The hardened helper: one flag set, a copy-out call and a run call
# -----------------------------------------------------------------------------

#: Every hardened container's name starts with this, so a leftover one is
#: recognisable (``docker ps --all --filter name=wfc-hardened-``).
HARDENED_NAME_PREFIX: Final[str] = "wfc-hardened-"

#: Seconds each docker call of a hardened helper may take before it is
#: stopped. ``docker create`` pulls a missing image, so this covers a pull.
HARDENED_TIMEOUT_S: Final[float] = 600.0

#: The most a hardened call accepts: characters printed on stdout or on
#: stderr by :func:`run_hardened`, bytes copied by :func:`copy_out`.
HARDENED_OUTPUT_CAP: Final[int] = 16 * 1024 * 1024

#: The flags every hardened container carries, network aside: a read-only
#: root, every capability dropped, no privilege gain. No mount is ever added.
_HARDENED_FLAGS: Final[tuple[str, ...]] = (
    "--read-only",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
)

#: A digest-pinned reference: ``<name>@sha256:<64 hex>``, tag optional, or a
#: bare image ID ``sha256:<64 hex>`` (content-addressed, and the form a locally
#: built env is handed to Docker in, which both image stores resolve).
_PINNED_REF_RE = re.compile(r"^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$")

#: The command a copy-out container is created with. It never starts, so the
#: command never runs; a path that exists in no image makes that plain.
_NEVER_STARTED_COMMAND: Final[str] = "/wfc-copy-out-never-starts"


def hardened_flags(*, network: bool = False) -> list[str]:
    """Return the hardened flag set for ``docker create`` or ``docker run``.

    Args:
        network: Give the container the default network. Off by default,
            which adds ``--network none``.

    Returns:
        The flags, in a fresh list the caller may extend.
    """
    flags = list(_HARDENED_FLAGS)
    if not network:
        flags += ["--network", "none"]
    return flags


def _require_pinned(image: str) -> None:
    """Refuse an image reference that is not digest-pinned.

    Args:
        image: The reference the hardened call was given.

    Raises:
        ValueError: If *image* is neither ``<name>@sha256:<64 hex>`` nor a
            bare image ID ``sha256:<64 hex>``.
    """
    if not _PINNED_REF_RE.match(image):
        raise ValueError(
            f"the hardened Docker helper takes a digest-pinned image only "
            f"(<name>@sha256:<64 hex digits>, or an image ID "
            f"sha256:<64 hex digits>), got {image!r}"
        )


def _hardened_docker(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run one docker command of a hardened call; raise on failure.

    The argv is logged as :func:`_run_docker` logs it: to ``wfc
    register-env``'s log, on the thread that opened it only.

    Args:
        cmd: The full ``docker ...`` argv.
        timeout: Seconds before the docker process is stopped.

    Returns:
        The completed process (exit 0), output decoded as UTF-8.

    Raises:
        DockerTimeoutError: If docker does not finish within *timeout*.
        RuntimeError: If docker exits non-zero (with its stderr).
    """
    what = " ".join(cmd[:2])
    progress.command(cmd)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerTimeoutError(f"{what} did not finish within {timeout:g} s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed (exit {proc.returncode}):\n{proc.stderr}")
    return proc


def _remove_container(name: str, *, quiet: bool) -> None:
    """Remove a hardened container, running or not.

    Args:
        name: The container's name.
        quiet: Swallow a failure; used while another error is already on
            its way to the caller, which matters more.

    Raises:
        RuntimeError: If ``docker rm`` fails and *quiet* is false.
    """
    try:
        _hardened_docker(["docker", "rm", "--force", name],
                         timeout=HARDENED_TIMEOUT_S)
    except RuntimeError:
        if not quiet:
            raise


def _copied_size(path: Path) -> int:
    """Return the bytes under *path*: a file's size, or a tree's file sizes.

    Args:
        path: A copied file or directory on the host.

    Returns:
        The size in bytes; links are counted as themselves, not followed.
    """
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_size
    return sum(p.lstat().st_size for p in path.rglob("*") if not p.is_dir())


@task(
    purpose="Copy one path out of a digest-pinned image through a never-started "
            "hardened container, and remove the container",
    inputs="image (digest-pinned ref), container path, optional host directory",
    outputs="host path of the copied file or directory",
)
def copy_out(image: str, path: str, *, dest_dir: Path | None = None,
             labels: Mapping[str, str] | None = None,
             timeout: float = HARDENED_TIMEOUT_S,
             output_cap: int = HARDENED_OUTPUT_CAP) -> Path:
    """Copy *path* out of *image* without running anything in it.

    ``docker create`` makes a container with the hardened flag set (network
    off), the *labels* given and a command that never runs, ``docker cp``
    copies *path* into *dest_dir*, and ``docker rm --force`` removes the
    container, on success, failure or Ctrl-C. The container is never
    started.

    Args:
        image: A digest-pinned image reference.
        path: An absolute path inside the image, naming a file or directory.
        dest_dir: An existing host directory to copy into. When ``None`` a
            new temporary directory is made, and removed again on failure.
        labels: ``{label: value}`` the container carries
            (:func:`wfc.environments.containers.copy_out_labels`: the
            project and this process, so ``wfc prune-envs`` can remove one a
            crash left); ``None`` for none.
        timeout: Seconds each docker call may take.
        output_cap: The most bytes the copy may hold; a larger copy is
            deleted and refused.

    Returns:
        The copied file or directory: ``dest_dir / <last part of path>``.

    Raises:
        ValueError: If *image* is not digest-pinned, *path* is not an
            absolute path with a last part, or the destination already
            holds that name.
        RuntimeError: If a docker call fails (with Docker's stderr) or times
            out, the copy exceeds *output_cap*, or the container cannot be
            removed.
        CopyOutInterruptedError: Ctrl-C stopped it; the container was
            removed and the copy deleted first.
    """
    _require_pinned(image)
    source = PurePosixPath(path)
    if not source.is_absolute() or not source.name:
        raise ValueError(f"copy_out needs an absolute container path with a "
                         f"last part, got {path!r}")
    made_dir = dest_dir is None
    target_dir = (Path(tempfile.mkdtemp(prefix="wfc-copy-out-"))
                  if dest_dir is None else dest_dir)
    copied = target_dir / source.name
    if copied.exists():
        raise ValueError(f"copy_out destination {copied} already exists")
    name = HARDENED_NAME_PREFIX + uuid.uuid4().hex[:12]
    label_flags = [flag for key, value in (labels or {}).items()
                   for flag in ("--label", f"{key}={value}")]
    try:
        _hardened_docker(["docker", "create", "--name", name, *label_flags,
                          *hardened_flags(), image, _NEVER_STARTED_COMMAND],
                         timeout=timeout)
        _hardened_docker(["docker", "cp", f"{name}:{source}", str(target_dir)],
                         timeout=timeout)
        size = _copied_size(copied)
        if size > output_cap:
            raise RuntimeError(f"copying {path} out of {image} gave {size} bytes, "
                               f"over the {output_cap}-byte cap")
    except BaseException as exc:
        _remove_container(name, quiet=True)
        if made_dir:
            shutil.rmtree(target_dir, ignore_errors=True)
        elif copied.is_dir() and not copied.is_symlink():
            shutil.rmtree(copied, ignore_errors=True)
        else:
            copied.unlink(missing_ok=True)
        if isinstance(exc, KeyboardInterrupt) and not isinstance(exc, CopyOutInterruptedError):
            raise CopyOutInterruptedError() from exc
        raise
    _remove_container(name, quiet=False)
    return copied


@task(
    purpose="Run a command in a digest-pinned image inside a hardened, "
            "removed-on-exit container, network off unless asked",
    inputs="image (digest-pinned ref), command argv, network opt-in",
    outputs="the command's stdout",
)
def run_hardened(image: str, command: list[str], *, network: bool = False,
                 timeout: float = HARDENED_TIMEOUT_S,
                 output_cap: int = HARDENED_OUTPUT_CAP) -> str:
    """Run *command* in *image* with ``docker run --rm`` and the hardened flags.

    The container has no mounts, a read-only root, no capabilities, no
    privilege gain, and ``--network none`` unless *network* is true.
    ``--rm`` removes it when the command ends; when the timeout stops the
    docker client first, the container is removed by name.

    Args:
        image: A digest-pinned image reference.
        command: The argv to run in the container.
        network: Give the container the default network.
        timeout: Seconds the run may take.
        output_cap: The most characters accepted on stdout and on stderr;
            more is refused. The cap applies to what docker printed once it
            exits; the timeout bounds how long it can print.

    Returns:
        The command's stdout.

    Raises:
        ValueError: If *image* is not digest-pinned.
        RuntimeError: If docker or the command exits non-zero (with the
            stderr), the run times out, or its output exceeds *output_cap*.
    """
    _require_pinned(image)
    name = HARDENED_NAME_PREFIX + uuid.uuid4().hex[:12]
    try:
        proc = _hardened_docker(
            ["docker", "run", "--rm", "--name", name,
             *hardened_flags(network=network), image, *command],
            timeout=timeout)
    except RuntimeError:
        # --rm removes an exited container; a timed-out one may still run.
        _remove_container(name, quiet=True)
        raise
    for stream, text in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if len(text or "") > output_cap:
            raise RuntimeError(f"docker run in {image} printed {len(text)} "
                               f"characters on {stream}, over the "
                               f"{output_cap}-character cap")
    return proc.stdout
