"""The env fingerprint: a container env's content blob, and the md5 a run records.

:func:`capture_env_content` renders the blob, canonical JSON of what the
image runs with: its layers, its ``Env``, its ``User`` and its
``WorkingDir``, as :func:`wfc.environments.docker.image_content` inspected
them. The image's name, tag, registry and labels are not in it, so the same
image registered under two names records one fingerprint. ``register``
stores the blob through :func:`wfc.storage.store_env_content` and records the
returned md5 as the env's ``env_fingerprint``. At run time
:func:`resolve_env_fingerprint` reads that recorded value back; it inspects,
captures and stores nothing. :func:`content_fingerprint` computes the same
md5 without storing, for checking an image pulled from a registry against
its record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


def capture_env_content(content: Mapping) -> str:
    """Render an image's inspected content into the env's content blob.

    A pure function: :func:`wfc.environments.register` (through its record
    step) inspects the image with
    :func:`wfc.environments.docker.image_content` and hands the result here,
    then stores the blob with :func:`wfc.storage.store_env_content`, whose
    md5 is the env's ``env_fingerprint``. The runtime *read* path
    (:func:`resolve_env_fingerprint`) never calls this function.

    The blob is ``{"type": "container-content", "layers": [...],
    "env": [...], "user": ..., "workdir": ...}`` as canonical JSON (sorted
    keys, no spaces). Layers and ``Env`` keep the daemon's order; ``Env`` is
    not sorted, since its order is what the image runs with. A ``null``
    ``Env`` renders as ``[]``, and a ``null`` or empty ``User`` or
    ``WorkingDir`` as ``""``, so an image that sets none of them renders one
    stable blob.

    Args:
        content: ``{"layers": [...], "env": [...] | None,
            "user": str | None, "workdir": str | None}``, the shape
            :func:`wfc.environments.docker.image_content` returns.

    Returns:
        The canonical JSON blob; equal content always renders equal bytes.

    Raises:
        ValueError: If *content* has no layers, or a layer, an ``Env`` entry,
            the user or the workdir is not a string.
    """
    layers = content.get("layers")
    if not layers or not isinstance(layers, list | tuple):
        raise ValueError(
            f"Image content has no layers ({layers!r}): an image's "
            f"RootFS.Layers is never empty, so this is not an inspected image."
        )
    env = content.get("env") or []
    user = content.get("user") or ""
    workdir = content.get("workdir") or ""
    if not isinstance(env, list | tuple):
        raise ValueError(f"Image content's env is not a list: {env!r}")
    for value in (*layers, *env, user, workdir):
        if not isinstance(value, str):
            raise ValueError(
                f"Image content holds a non-string value {value!r}: layers, "
                f"Env entries, User and WorkingDir are strings."
            )
    return json.dumps(
        {
            "type": "container-content",
            "layers": list(layers),
            "env": list(env),
            "user": user,
            "workdir": workdir,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def content_fingerprint(content: Mapping) -> str:
    """Return the ``env_fingerprint`` an image with *content* records.

    The md5 of the blob :func:`capture_env_content` renders, the value
    :func:`wfc.storage.store_env_content` returns for it at registration,
    computed without storing anything. A pulled image is checked with it:
    its content must render the fingerprint its record names.

    Args:
        content: The shape :func:`wfc.environments.docker.image_content`
            returns.

    Returns:
        32-character hex MD5 of the rendered blob.

    Raises:
        ValueError: As :func:`capture_env_content` raises.
    """
    blob = capture_env_content(content)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def resolve_env_fingerprint(env_spec: str, project_dir: Path | str) -> str:
    """Resolve a method's env name to its ``env_fingerprint`` (md5).

    The single runtime entry point for turning ``Method.env`` into the
    fingerprint persisted on the Run row and folded into the cache key.

    A name registered in ``.wfc/envs.json`` with a non-empty ``container``
    field returns the manifest's precomputed ``env_fingerprint`` — no
    subprocess, no lock parse, and **no cache write** (the blob was stored
    at registration time; re-storing the fingerprint string here would
    hash the hash).

    Any other name is refused before anything is captured or stored: a name
    with no record (an env deleted after its method registered, say), a
    record that lacks a container, or a record with a container but an
    empty ``env_fingerprint``. The error names the env as not registered
    and points at ``wfc register-env``; when a record exists, it names the
    ``--force`` re-register (``wfc demo --force`` for the demo env), since
    that incomplete record blocks a plain re-register. A record with a
    container but no fingerprint (one a version-1 manifest's conversion
    carried over) is refused as missing its fingerprint, not as absent. A
    manifest that cannot be read raises its own error, naming the file.

    Args:
        env_spec: The env name, already taken through the env-spec
            grammar's read side by the caller
            (``wfc.contracts.parse_env_spec``).
        project_dir: Root directory of the wfc project.

    Returns:
        32-character hex MD5 env fingerprint.

    Raises:
        ValueError: If *env_spec* has no registered container env, or the
            env manifest cannot be read.
    """
    from .manifest import get as _envs_get
    from .manifest import recreate_command

    record = _envs_get(env_spec, Path(project_dir))
    if record is not None and record.container and record.env_fingerprint:
        return record.env_fingerprint

    # A record that exists but is incomplete (a converted manifest clears the
    # fingerprint of every env not yet re-registered) blocks a plain
    # re-register, so the command that replaces it is named.
    if record is not None and record.container:
        raise ValueError(
            f"Env {env_spec!r} has a record in .wfc/envs.json but its env "
            f"fingerprint is missing, so it is not registered for runs. "
            f"Re-register it with `{recreate_command(env_spec)}`."
        )
    if record is None:
        command = f"`wfc register-env {env_spec}`"
    else:
        command = f"`{recreate_command(env_spec)}` to replace its incomplete record"
    raise ValueError(
        f"Env {env_spec!r} is not registered: .wfc/envs.json has no container "
        f"env by that name. Build it with {command}."
    )
