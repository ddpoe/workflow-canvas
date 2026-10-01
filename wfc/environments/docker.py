"""Docker subprocess boundary for the container env backend.

Centralizes the ``docker`` CLI invocations wfc makes while building and
registering envs (``wfc register-env``); runtime dispatch assembles its
``docker run`` argv in :mod:`wfc.environments.argv` instead. Tests mock
the four functions here rather than ``subprocess.run`` directly, which
keeps the boundary small.

Functions:

- :func:`build` — ``docker build -t <tag> <dir>`` with BuildKit enabled.
  ``DOCKER_BUILDKIT=1`` is merged into a copy of ``os.environ`` so the
  caller's PATH / DOCKER_HOST / HOME / proxy vars survive the spawn.
- :func:`image_inspect` — ``docker image inspect <ref> --format '{{.Id}}'``.
  Returns the digest string verbatim, including the ``sha256:`` prefix.
- :func:`repo_digest` — the ``RepoDigests`` entry for one repository: the
  registry digest a byo registration records for an image outside
  ``local/``.
- :func:`pull` — ``docker pull <ref>``. Only used on the BYO branch when
  the image is not already present in the local daemon.

There is no ``push`` function: wfc does not push images to a registry.

All functions raise :class:`RuntimeError` on non-zero exit and surface
docker's own error text in the message — the user sees the real
``unable to resolve image`` / ``COPY failed`` text, not a wrapped
``CalledProcessError``.  (:func:`build` streams its output and attaches
the tail of the combined stream; the others attach stderr verbatim.)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from axiom_annotations import task

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


@task(purpose="Run docker build -t <tag> <dir> with BuildKit enabled")
def build(dockerfile_dir: str | Path, tag: str) -> None:
    """Run ``docker build -t <tag> <dockerfile_dir>`` with BuildKit on.

    BuildKit is required by the Dockerfile generators (they emit
    ``# syntax=docker/dockerfile:1.4`` and use ``--mount=type=cache``
    instructions). We merge ``DOCKER_BUILDKIT=1`` into a *copy* of the
    process env so the user's PATH, HOME, DOCKER_HOST, HTTP(S)_PROXY, etc.
    survive — passing ``env={'DOCKER_BUILDKIT': '1'}`` alone would strip
    every other variable and break the build on most setups.

    Progress: on a TTY, a single in-place spinner line shows a
    user-friendly status (``- building environment — step 3/7 (1m 12s)``)
    parsed from BuildKit's step markers — raw docker output is never
    printed on success.  Off-TTY (CI, piped logs) nothing is printed.

    Args:
        dockerfile_dir: Directory containing the ``Dockerfile`` to build.
            Used as docker's build context.
        tag: Image tag to assign to the resulting image (e.g.
            ``"my-env:_wfc-build"``).

    Raises:
        RuntimeError: If docker exits non-zero. The error message carries
            the last 40 lines of the interleaved build output (BuildKit
            reports errors on stdout/stderr mixed; the tail preserves the
            real ``COPY failed`` / ``unable to resolve image`` text).
    """
    build_env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    cmd = ["docker", "build", "-t", tag, str(dockerfile_dir)]
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
    )

    # Collect docker's output off the pipe (never shown on success — users
    # get the spinner below, not BuildKit noise) and track the latest
    # `[x/y]` step marker for the spinner's progress text.
    lines: list[str] = []
    latest_step: list[tuple[int, int] | None] = [None]

    def _drain() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.append(raw.rstrip("\n"))
            step = _parse_build_step(raw)
            if step is not None:
                latest_step[0] = step

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    # Single in-place spinner line on a TTY ("building environment — step
    # 3/7 (1m 12s)"); complete silence otherwise (CI / piped logs).
    show_spinner = sys.stderr.isatty()
    frames = "|/-\\"
    start = time.monotonic()
    frame = 0
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
    reader.join(timeout=10)
    if show_spinner:
        print("\r" + " " * 79 + "\r", end="", file=sys.stderr, flush=True)

    if proc.returncode != 0:
        tail = "\n".join(lines[-40:])
        raise RuntimeError(
            f"docker build failed (exit {proc.returncode}):\n{tail}"
        )
    if show_spinner:
        print(
            f"environment built ({_format_elapsed(time.monotonic() - start)})",
            file=sys.stderr,
        )


class ImageNotFoundError(RuntimeError):
    """``docker image inspect`` reached the daemon, and it has no such image."""


#: What the daemon prints for an image it lacks, on the classic and the
#: containerd image stores. Any other inspect failure (a daemon that is not
#: running, permission denied) is not a missing image.
_NO_SUCH_IMAGE = ("no such image", "no such object")


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
        RuntimeError: If docker exits non-zero for any other reason (the
            daemon is unreachable, permission denied).
    """
    cmd = ["docker", "image", "inspect", ref, "--format", "{{.Id}}"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        # docker always emits UTF-8 (BuildKit progress uses multi-byte chars);
        # the Windows default cp1252 decode crashes the reader thread.
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        message = (f"docker image inspect {ref!r} failed "
                   f"(exit {proc.returncode}):\n{proc.stderr}")
        if any(marker in proc.stderr.lower() for marker in _NO_SUCH_IMAGE):
            raise ImageNotFoundError(message)
        raise RuntimeError(message)
    return proc.stdout.strip()


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
        RuntimeError: If docker exits non-zero, or the image lists no
            registry digest for *repository* (a locally built image lists
            none).
    """
    cmd = ["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
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

    Used on the BYO branch when :func:`image_inspect` reports the image
    is not locally resolvable. Generator-backed envs never pull — they
    build the image locally instead.

    Args:
        ref: Image reference to pull. May be a floating-tag ref
            (``reg/img:latest``) or a digest-pinned ref
            (``reg/img@sha256:...``).

    Raises:
        RuntimeError: If docker exits non-zero (network failure,
            unauthorized, unknown image, etc.). The error message
            includes docker's stderr verbatim.
    """
    cmd = ["docker", "pull", ref]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        # docker always emits UTF-8 (BuildKit progress uses multi-byte chars);
        # the Windows default cp1252 decode crashes the reader thread.
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker pull {ref!r} failed (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
