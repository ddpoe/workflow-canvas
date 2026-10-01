"""The env fingerprint: a container env's content blob, and the md5 a run records.

:func:`capture_env_content` renders the blob, canonical JSON naming the
image and its ``sha256:`` digest. ``register`` stores it through
:func:`wfc.storage.store_env_content` and records the returned md5 as the
env's ``env_fingerprint``. At run time :func:`resolve_env_fingerprint` reads
that recorded value back; it captures and stores nothing.
"""

from __future__ import annotations

from pathlib import Path


def capture_env_content(env_spec: str) -> str:
    """Capture the deterministic content blob of a container env.

    The spec is ``container:<image>@sha256:<hex>``; the blob is canonical
    JSON naming the image and its ``sha256:``-prefixed digest. This is the
    **precompute write path** used by :func:`wfc.environments.register` to
    populate ``env_fingerprint`` at registration time. The runtime *read*
    path (a method's registered env at run-step time) is
    :func:`resolve_env_fingerprint`, which never calls this function.

    The returned string is always real env content — never a precomputed
    fingerprint — deterministic for a given image digest, and is the input
    to :func:`wfc.storage.store_env_content`, which hashes it and stores
    the blob in the DVC content-addressed cache.

    Args:
        env_spec: ``"container:<image>@sha256:<hex>"``.

    Returns:
        Canonical JSON (sorted keys, no spaces), so two calls with the same
        (image, digest) hash to the same ``env_fingerprint``.

    Raises:
        ValueError: If *env_spec* is not a ``container:`` spec, or its
            image or digest is empty.
    """
    if env_spec.startswith("container:"):
        # Container-backend precompute path. The spec is
        # ``container:<image>@sha256:<hex>``. We split on the last
        # ``@sha256:`` so an image ref like ``docker://reg/img@sha256:...``
        # is correctly partitioned even though the image part contains
        # extra punctuation. The output is canonical JSON (sorted keys,
        # no spaces) so two calls with the same (image, digest) hash to
        # the same env_fingerprint regardless of input formatting.
        import json as _json
        payload = env_spec[len("container:"):]
        marker = "@sha256:"
        idx = payload.rfind(marker)
        if idx < 0:
            raise ValueError(
                f"Malformed container env spec {env_spec!r}: expected "
                f"'container:<image>@sha256:<hex>'."
            )
        image = payload[:idx]
        digest_hex = payload[idx + len(marker):]
        if not image or not digest_hex:
            raise ValueError(
                f"Malformed container env spec {env_spec!r}: image and "
                f"digest must both be non-empty."
            )
        blob = _json.dumps(
            {"type": "container", "image": image, "digest": f"sha256:{digest_hex}"},
            sort_keys=True,
            separators=(",", ":"),
        )
        return blob

    raise ValueError(
        f"Unsupported env spec {env_spec!r}: expected "
        f"'container:<image>@sha256:<hex>'."
    )


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
    with no record (an env deleted after its method registered, say) or
    with a record that lacks a container. The error names the env as not
    registered and points at ``wfc register-env``. A manifest that cannot
    be read raises its own error, naming the file.

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

    record = _envs_get(env_spec, Path(project_dir))
    if record is not None and record.container and record.env_fingerprint:
        return record.env_fingerprint

    # A record that exists but is incomplete blocks a plain re-register.
    replace_hint = " (add `--force` to replace its incomplete record)" if record is not None else ""
    raise ValueError(
        f"Env {env_spec!r} is not registered: .wfc/envs.json has no container "
        f"env by that name. Build it with `wfc register-env {env_spec}`"
        f"{replace_hint}."
    )
