"""A step's command: the ``docker run`` argv its invocation record describes.

The invocation record holds container-side values only. :func:`render_argv`
supplies each mount's host side and renders the argv in the exact shape
dispatch handed Docker: ``docker run --rm``, the forwarded ``-e`` values in
recorded order, ``--pull never --user <uid:gid>``, each bind (``-w`` after
the bind of the working directory), ``--gpus all`` when recorded, then the
image, the interpreter and the script. :func:`command_script` renders the
same argv as a step's ``command.sh``, with shell variables for the host sides.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from .. import layout

#: The shell variable each mount role's host side is read from in command.sh.
ROLE_VARIABLES = {"project": "WORK", "cache": "DVC_CACHE", "code": "CODE"}

#: Marks a host side the script renders as a shell variable.
_VAR = "\x00"


def render_argv(invocation: Mapping, hosts: Mapping[str, str]) -> list[str]:
    """Render the ``docker run`` argv an invocation record describes.

    Args:
        invocation: The run's invocation record.
        hosts: The host side of each mount, by its container path.

    Returns:
        The argv, token for token as dispatch would hand it to Docker.

    Raises:
        KeyError: A mount has no host side in *hosts*.
    """
    argv = ["docker", "run", "--rm"]
    for name, value in (invocation.get("env") or {}).items():
        argv.extend(["-e", f"{name}={value}"])
    argv.extend(["--pull", "never", "--user", str(invocation["user"])])
    workdir = invocation.get("workdir")
    for mount in invocation.get("mounts") or ():
        spec = f"{hosts[mount['container']]}:{mount['container']}"
        argv.extend(["-v", f"{spec}:ro" if mount.get("read_only") else spec])
        if workdir is not None and mount["container"] == workdir:
            argv.extend(["-w", workdir])
    if invocation.get("gpus"):
        argv.extend(["--gpus", "all"])
    argv.extend([str(invocation["image_ref"]), str(invocation["interpreter"]),
                 str(invocation["script"])])
    return argv


def _quote(token: str) -> str:
    """Quote one argv token for sh, a variable host side as ``"$VAR"``."""
    if token.startswith(_VAR):
        name, _, rest = token[1:].partition(_VAR)
        return f'"${name}"' + (shlex.quote(rest) if rest else "")
    return shlex.quote(token)


def command_script(*, number: int, count: int, run_id: int, label: str,
                   version_label: int | None, sample: str | None,
                   started_at: str | None, env_name: str | None,
                   image: str | None, folder: str, invocation: Mapping,
                   inputs: Sequence[tuple[str, str, str]]) -> str:
    """Render a step's ``command.sh``: the recorded command, host sides as variables.

    ``BUNDLE`` defaults to the bundle's root (two folders above the
    script), ``WORK`` and ``DVC_CACHE`` (the directories bound at the
    project and cache mounts) must be set, and ``CODE`` defaults to the
    step's ``code/`` folder.

    Args:
        number: The step's number.
        count: How many steps the bundle holds.
        run_id: The step's run.
        label: ``module.method``.
        version_label: The method version's label.
        sample: The sample the run ran on.
        started_at: When the run started, ISO 8601.
        env_name: The env.
        image: The image's recorded reference.
        folder: The step's folder under ``steps/``.
        invocation: The run's invocation record.
        inputs: ``(slot, container path, bundle path)`` per input value.

    Returns:
        The script's text, LF line endings.
    """
    hosts = {m["container"]: f"{_VAR}{ROLE_VARIABLES.get(m['role'], 'CODE')}{_VAR}"
             for m in invocation.get("mounts") or ()}
    argv = render_argv(invocation, hosts)
    lines = [
        "#!/bin/sh",
        f"# wfc rerun bundle: step {number} of {count}",
        f"# run {run_id}: {label} v{version_label}, sample {sample}",
        f"# ran at {started_at} (UTC), env {env_name}, image {image}",
        f"# recorded user {invocation.get('user')}, "
        f"working directory {invocation.get('workdir')}",
        "# mounts (container path, role, host side):",
    ]
    for mount in invocation.get("mounts") or ():
        mode = "read-only" if mount.get("read_only") else "read-write"
        lines.append(f"#   {mount['container']} ({mount['role']}, {mode}) <- "
                     f"${ROLE_VARIABLES.get(mount['role'], 'CODE')}")
    if inputs:
        lines.append("# inputs (slot: container path <- bundle file):")
        lines.extend(f"#   {slot}: {container} <- {bundled}"
                     for slot, container, bundled in inputs)
    lines += [
        "set -eu",
        'BUNDLE="${BUNDLE:-$(cd "$(dirname "$0")/../.." && pwd)}"',
        f': "${{WORK:?set WORK to the directory bound at {layout.WORK_MOUNT}}}"',
        f': "${{DVC_CACHE:?set DVC_CACHE to the directory bound at '
        f'{layout.DVC_CACHE_MOUNT}}}"',
        f'CODE="${{CODE:-$BUNDLE/steps/{folder}/code}}"',
        "exec " + " \\\n  ".join(_quote(token) for token in argv),
    ]
    return "\n".join(lines) + "\n"
