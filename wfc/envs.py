"""Container-env manifest at ``.wfc/envs.json`` (ADR-019).

The manifest is the tool-managed record of every container env known to a
project. Cycle A landed the read-only public API (:func:`list_envs`,
:func:`get`, :func:`delete`, :func:`validate_container_ref`) plus
:class:`EnvRecord` and :func:`save_manifest`. Cycle C adds the write
orchestration entry point :func:`register`, which runs the per-backend
Dockerfile generator, invokes ``docker build`` via :mod:`wfc.docker_runner`,
resolves the digest, precomputes ``env_fingerprint``, and writes a record
to the manifest.

Schema (``schema_version == 1``)::

    {
      "schema_version": 1,
      "envs": {
        "<env_name>": {
          "backend": "pixi" | "conda" | "inherit",
          "source": "pixi.toml" | "environment.yml" | null,
          "container": "docker://<host>/<path>@sha256:<hex>",
          "env_fingerprint": "<sha256 of source-file content or inherit fingerprint>",
          "built_from_lock": "pixi.lock" | "conda-lock.yml" | null,
          "built_at": "<ISO-8601 timestamp>"
        }
      }
    }

The env name is the KEY of the ``envs`` dict; it is NOT stored inside the
record (per ADR-019 §registration-model-and-manifest). Callers that need
to pair a name with its record receive a ``(name, EnvRecord)`` tuple from
:func:`list_envs`.

A missing manifest file is treated as an empty manifest (zero envs) — every
project starts that way until the first ``wfc register-env`` call in cycle B.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "envs.json"

# Per ADR-019 decision #11: image refs MUST be digest-pinned (``@sha256:<hex>``).
# Floating tags (``:latest``, ``:v1``) are rejected so that bit-identical
# image identity is recoverable across machines.
_DOCKER_DIGEST_REF = re.compile(
    r"^docker://"
    r"(?P<host>[a-z0-9][a-z0-9.\-]*(?::\d+)?)"      # host[:port]
    r"/(?P<path>[a-z0-9][a-z0-9._/\-]*)"            # repository path
    r"@sha256:(?P<digest>[a-f0-9]{64})$"
)


@dataclass
class EnvRecord:
    """One row in ``.wfc/envs.json::envs``.

    Mirrors the JSON shape declared in ADR-019 §registration-model-and-manifest.
    The env *name* is the KEY of the ``envs`` dict and is **not** a field on
    this record — pairing a name with a record is the caller's responsibility
    (see :func:`list_envs`, which returns ``(name, EnvRecord)`` tuples).

    Optional fields default to ``None`` so the dataclass survives loading a
    record that was written by an older / partial cycle.
    """

    backend: str
    source: Optional[str]
    container: str
    env_fingerprint: str
    built_at: str
    built_from_lock: Optional[str] = None
    source_fingerprint: Optional[str] = None

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialization.

        The returned dict does NOT contain a ``name`` key — the name is the
        outer dict key in ``.wfc/envs.json::envs`` and is supplied by callers.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EnvRecord":
        """Reconstruct an ``EnvRecord`` from a JSON-loaded dict.

        Unknown keys (including a stray ``name`` key from an older write)
        are ignored so a future or older cycle's record can still be read
        without crashing — forward-compat for additive fields only.
        """
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


# =============================================================================
# Manifest IO
# =============================================================================

def _manifest_path(project_dir: Path) -> Path:
    """Return the absolute path of ``<project>/.wfc/envs.json``."""
    return Path(project_dir).resolve() / ".wfc" / MANIFEST_FILENAME


def load_manifest(project_dir: Path) -> dict:
    """Read ``.wfc/envs.json`` and return the parsed manifest dict.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        A dict shaped ``{"schema_version": 1, "envs": {<name>: <record-dict>}}``.
        When the file does not exist, an empty manifest is returned — this is
        the normal state for a project before any ``wfc register-env`` call.

    Raises:
        ValueError: If ``schema_version`` is not :data:`MANIFEST_SCHEMA_VERSION`
            or the file is not valid JSON.
    """
    path = _manifest_path(project_dir)
    if not path.exists():
        return {"schema_version": MANIFEST_SCHEMA_VERSION, "envs": {}}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc

    schema = raw.get("schema_version")
    if schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unknown schema_version {schema!r} "
            f"(this wfc version supports {MANIFEST_SCHEMA_VERSION})"
        )

    if "envs" not in raw or not isinstance(raw["envs"], dict):
        raise ValueError(f"{path}: missing or malformed 'envs' object")

    return raw


def save_manifest(project_dir: Path, manifest: dict) -> None:
    """Atomically write *manifest* to ``.wfc/envs.json``.

    Uses ``tempfile + os.replace`` so a partial write cannot leave the
    manifest in a corrupted state — readers see either the old content
    or the new content, never a half-flushed file.

    Args:
        project_dir: Root directory of the wfc project (must contain ``.wfc/``).
        manifest: Manifest dict in the schema documented at module-level.
            The caller is responsible for setting ``schema_version`` correctly;
            this function does not patch it.

    Raises:
        FileNotFoundError: If the ``.wfc/`` directory does not exist.
    """
    path = _manifest_path(project_dir)
    wfc_state_dir = path.parent
    if not wfc_state_dir.exists():
        raise FileNotFoundError(
            f"No .wfc/ directory at {wfc_state_dir.parent} — run `wfc init` first"
        )

    # Write to a sibling temp file in the same directory so os.replace is
    # atomic on every platform (rename across filesystems is not atomic).
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=".envs.", suffix=".json.tmp", dir=str(wfc_state_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path_str, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


# =============================================================================
# Read-side public API
# =============================================================================

def list_envs(project_dir: Path) -> list[tuple[str, EnvRecord]]:
    """Return every env in the manifest as ``(name, EnvRecord)`` tuples, sorted by name.

    The name is paired with the record at the API boundary because it is the
    KEY of ``.wfc/envs.json::envs`` — not a field stored inside the record
    (per ADR-019 §registration-model-and-manifest).

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        List of ``(env_name, EnvRecord)`` tuples sorted by env_name. Empty
        when no manifest exists or the manifest's ``envs`` block is empty.
    """
    manifest = load_manifest(project_dir)
    envs = manifest.get("envs", {})
    return [(name, EnvRecord.from_dict(envs[name])) for name in sorted(envs)]


def get(name: str, project_dir: Path) -> Optional[EnvRecord]:
    """Return the env record for *name*, or ``None`` if missing.

    Args:
        name: Env name (key in ``.wfc/envs.json::envs``).
        project_dir: Root directory of the wfc project.

    Returns:
        The :class:`EnvRecord` if present, else ``None``.
    """
    manifest = load_manifest(project_dir)
    record = manifest.get("envs", {}).get(name)
    if record is None:
        return None
    return EnvRecord.from_dict(record)


def delete(name: str, project_dir: Path) -> None:
    """Remove *name* from the manifest. Errors if the env is unknown.

    The registry image tag is **not** removed — that is out of scope per
    ADR-019 decision #7. Method rows that reference this env are also left
    untouched; the CLI surface is responsible for warning the user before
    calling this.

    Args:
        name: Env name to remove.
        project_dir: Root directory of the wfc project.

    Raises:
        KeyError: If *name* is not in the manifest.
    """
    manifest = load_manifest(project_dir)
    envs = manifest.get("envs", {})
    if name not in envs:
        raise KeyError(name)
    del envs[name]
    manifest["envs"] = envs
    save_manifest(project_dir, manifest)


# =============================================================================
# Container-ref shape validation
# =============================================================================

def validate_container_ref(ref: str) -> None:
    """Validate that *ref* is a digest-pinned ``docker://`` reference.

    Accepted shape::

        docker://<host>[:<port>]/<path>@sha256:<64-hex-digest>

    Rejects floating tags (``:latest``, ``:v1``), missing schemes, and
    anything else that would let two different images masquerade as the
    same ref. Per ADR-019 decision #5, only digest-pinned refs are allowed
    so cache keys and method registrations remain reproducible.

    Args:
        ref: The image reference to check.

    Raises:
        ValueError: If *ref* is not a digest-pinned ``docker://`` reference.
            The error message names the rejected ref and points at the
            required shape.
    """
    if not isinstance(ref, str) or not ref:
        raise ValueError(
            f"Container reference must be a non-empty string, got {ref!r}"
        )
    if not _DOCKER_DIGEST_REF.match(ref):
        raise ValueError(
            f"Container reference {ref!r} is not digest-pinned. "
            f"Expected shape: docker://<host>/<path>@sha256:<hex64>. "
            f"Floating tags (e.g. :latest, :v1) are rejected — ADR-019 "
            f"requires every container ref to be digest-pinned so the "
            f"image identity is recoverable across machines."
        )


# =============================================================================
# Write-side public API (ADR-019 Cycle C)
# =============================================================================

# Accept a BYO input ref like ``docker://reg/img:tag``, ``docker://reg/img``,
# or already-pinned ``docker://reg/img@sha256:<hex>``. The post-resolution
# manifest value is always digest-pinned (validated via _DOCKER_DIGEST_REF).
_DOCKER_BYO_INPUT = re.compile(
    r"^docker://"
    r"(?P<host>[a-z0-9][a-z0-9.\-]*(?::\d+)?)"
    r"/(?P<path>[a-z0-9][a-z0-9._/\-]*?)"
    r"(?::(?P<tag>[a-zA-Z0-9._\-]+))?"
    r"(?:@sha256:(?P<digest>[a-f0-9]{64}))?$"
)


def _parse_byo_ref(ref: str) -> tuple[str, str | None, str | None]:
    """Split a BYO ``docker://`` input ref into (image_without_digest_or_tag,
    tag-or-None, digest-or-None).

    The first element is the docker-daemon-resolvable ref preserving the
    scheme prefix stripped — i.e. ``reg/img`` — useful for assembling the
    final manifest container value as ``docker://<prefix>@sha256:<digest>``.
    """
    if not isinstance(ref, str) or not ref.startswith("docker://"):
        raise ValueError(
            f"BYO image ref {ref!r} must start with 'docker://' scheme."
        )
    m = _DOCKER_BYO_INPUT.match(ref)
    if not m:
        raise ValueError(
            f"BYO image ref {ref!r} is not a valid docker:// reference."
        )
    host = m.group("host")
    path = m.group("path")
    tag = m.group("tag")
    digest = m.group("digest")
    image_prefix = f"{host}/{path}"
    return image_prefix, tag, digest


def register(
    name: str,
    backend: str,
    source: dict,
    base_image: Optional[str] = None,
    force: bool = False,
    project_dir: Optional[Path] = None,
    *,
    live_spec: Optional[str] = None,
) -> EnvRecord:
    """Register a container env: build the image, resolve its digest, and
    write the manifest entry.

    Orchestration entry point for ``wfc register-env``. Dispatch by backend:

      - **pixi / conda / inherit:** assemble per-backend generator kwargs
        from *source*, render the Dockerfile via
        :func:`wfc.dockerfiles.generate_for_backend`, write it to
        ``.wfc/build/<name>/Dockerfile``, stage any source-content blobs
        from ``source`` into the build context under the generator's
        expected filenames, run ``docker build`` via :mod:`wfc.docker_runner`,
        resolve the digest, and store a local-only ref ``<name>@sha256:<hex>``
        as the manifest's ``container`` field.

      - **byo:** validate the user-supplied ``docker://`` ref, pull if not
        already local, resolve the digest, and store
        ``docker://<original-prefix>@sha256:<hex>``.

    The image fingerprint (``env_fingerprint``) is precomputed at
    registration time by feeding a canonical
    ``container:<image>@sha256:<hex>`` spec through
    :func:`wfc.version.capture_env_content` and :func:`wfc.version.store_env_content`.

    When ``live_spec`` is provided (e.g. ``"conda:cell_pose"`` or
    ``"pixi:wcia:hello"``), the captured package-list blob is also
    stored as ``source_fingerprint`` on the returned :class:`EnvRecord`,
    pointing at content retrievable via the canvas
    ``GET /api/registry/envs/blob/<md5>`` endpoint.

    Args:
        name: Env name (KEY in ``.wfc/envs.json::envs``; not stored inside the
            record).
        backend: One of ``"pixi"``, ``"conda"``, ``"inherit"``, ``"byo"``.
        source: Per-backend payload dict. Supported keys (all optional):
            * ``"pip_freeze_content"``: verbatim pip freeze output, fed to
              the pixi/conda/inherit Dockerfile generators and staged as
              ``pip-freeze.txt`` in the build context.
            * ``"explicit_list_content"``: conda explicit-list contents
              (output of ``conda list --explicit --md5``). Staged as
              ``explicit-list.txt``. Conda backend only.
            * ``"pixi_lock_content"``: contents of a ``pixi.lock`` file.
              Staged as ``pixi.lock``. Pixi backend only.
            * ``"pixi_toml_content"``: contents of a ``pixi.toml`` file.
              Staged as ``pixi.toml``. Pixi backend only.
            * ``"image"``: ``docker://<host>/<path>[:<tag>][@sha256:<hex>]``.
              Byo backend only.
            Legacy keys ``"pixi_env"`` and ``"conda_env"`` are accepted but
            ignored — the live-spec ``live_spec`` kwarg supersedes them.
        base_image: Optional base-image override (pixi/conda/inherit only).
            Rejected for byo because there is no Dockerfile to override.
        force: When ``True``, overwrite an existing entry for *name*.
            When ``False`` (default), an existing entry raises
            :class:`FileExistsError`.
        project_dir: Project root (containing ``.wfc/``). When ``None``,
            the cwd is used.
        live_spec: Optional typed env spec (``"conda:<env>"`` or
            ``"pixi:<proj>:<env>"``) that names the live local env this
            registration mirrors. When set, a content-addressed blob of the
            env's captured package list is stored in the DVC cache and
            its md5 is persisted as :attr:`EnvRecord.source_fingerprint`,
            so the canvas can render the package contents via the
            existing ``GET /api/registry/envs/blob/<md5>`` endpoint.
            Stays ``None`` for ``--from`` file-mode, ``inherit``, and
            ``byo``.

    Returns:
        The persisted :class:`EnvRecord`.

    Raises:
        FileExistsError: If *name* already exists and ``force=False``.
        ValueError: On invalid ref, missing required source field,
            ``base_image`` on byo, or unknown backend.
        RuntimeError: If a docker subprocess fails (build / pull / inspect).
        FileNotFoundError: If ``.wfc/`` does not exist.
    """
    from . import dockerfiles as df_pkg
    from . import docker_runner
    from .version import capture_env_content, store_env_content

    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = Path(project_dir).resolve()
    if not (project_dir / ".wfc").is_dir():
        raise FileNotFoundError(
            f"No .wfc/ directory at {project_dir} — run `wfc init` first"
        )

    # Existence check up front so we can fail BEFORE running docker.
    manifest = load_manifest(project_dir)
    envs_block = manifest.get("envs", {})
    if name in envs_block and not force:
        raise FileExistsError(
            f"Env {name!r} already exists in .wfc/envs.json. "
            f"Re-run with `--force` to overwrite."
        )

    if backend == "byo" and base_image is not None:
        raise ValueError(
            "--base-image is not valid for the byo backend — there is no "
            "Dockerfile to override. The upstream image is used as-is."
        )

    # -------------------------------------------------------------------------
    # Backend dispatch
    # -------------------------------------------------------------------------
    container_ref: str
    source_field: Optional[str]
    built_from_lock: Optional[str]

    if backend in ("pixi", "conda", "inherit"):
        gen_kwargs: dict = {"env_name": name}
        if base_image is not None:
            gen_kwargs["base_image"] = base_image

        if backend == "pixi":
            gen_kwargs["pixi_lock_path"] = project_dir / "pixi.lock"
            gen_kwargs["pip_freeze_content"] = source.get(
                "pip_freeze_content", ""
            )
            source_field = "pixi.toml"
            built_from_lock = "pixi.lock"
        elif backend == "conda":
            gen_kwargs["explicit_list_path"] = (
                project_dir / "explicit-list.txt"
            )
            gen_kwargs["pip_freeze_content"] = source.get(
                "pip_freeze_content", ""
            )
            source_field = "environment.yml"
            built_from_lock = "conda-lock.yml"
        else:  # inherit
            gen_kwargs["pip_freeze_content"] = source.get(
                "pip_freeze_content", ""
            )
            source_field = None
            built_from_lock = None

        dockerfile = df_pkg.generate_for_backend(backend, **gen_kwargs)
        if dockerfile is None:  # defensive — only byo returns None
            raise RuntimeError(
                f"Generator for backend {backend!r} returned no Dockerfile."
            )

        build_dir = project_dir / ".wfc" / "build" / name
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

        # Stage source-content blobs into the build context under the
        # filenames the generators emit COPY for. The Dockerfile generators
        # are pure (string in, string out); register() is the one place
        # that knows about disk, so file staging lives here.
        pip_freeze_text = source.get("pip_freeze_content", "")
        if backend in ("pixi", "conda", "inherit"):
            (build_dir / "pip-freeze.txt").write_text(
                pip_freeze_text, encoding="utf-8"
            )
        if backend == "pixi":
            pixi_lock_text = source.get("pixi_lock_content")
            if pixi_lock_text is not None:
                (build_dir / "pixi.lock").write_text(
                    pixi_lock_text, encoding="utf-8"
                )
            pixi_toml_text = source.get("pixi_toml_content")
            if pixi_toml_text is not None:
                (build_dir / "pixi.toml").write_text(
                    pixi_toml_text, encoding="utf-8"
                )
        if backend == "conda":
            explicit_list_text = source.get("explicit_list_content")
            if explicit_list_text is not None:
                (build_dir / "explicit-list.txt").write_text(
                    explicit_list_text, encoding="utf-8"
                )

        # Local-only build tag; manifest stores the digest-pinned form.
        build_tag = f"{name}:_wfc-build"
        docker_runner.build(build_dir, build_tag)
        raw_digest = docker_runner.image_inspect(build_tag)
        digest_hex = raw_digest.removeprefix("sha256:").strip()
        if not digest_hex:
            raise RuntimeError(
                f"docker image inspect returned no digest for {build_tag!r}"
            )
        container_ref = f"{name}@sha256:{digest_hex}"
        # Image part used for env_fingerprint (local-only, no scheme).
        fingerprint_image = name

    elif backend == "byo":
        ref = source.get("image")
        if not ref:
            raise ValueError(
                "BYO backend requires source['image'] = 'docker://...'"
            )
        # Parse the user-supplied ref. Floating tags are allowed at input
        # (we resolve them); the post-resolution manifest value is always
        # digest-pinned.
        image_prefix, _tag, user_digest = _parse_byo_ref(ref)

        # Daemon-side ref strips the docker:// scheme.
        daemon_ref = ref[len("docker://"):]

        # Probe-then-pull: only pull if not already local.
        try:
            raw_digest = docker_runner.image_inspect(daemon_ref)
        except RuntimeError:
            docker_runner.pull(daemon_ref)
            raw_digest = docker_runner.image_inspect(daemon_ref)

        digest_hex = raw_digest.removeprefix("sha256:").strip()
        if not digest_hex:
            raise RuntimeError(
                f"docker image inspect returned no digest for {daemon_ref!r}"
            )
        # If the user supplied a digest, confirm it matches what the daemon
        # reports — silently swapping in a different digest would defeat the
        # whole point of digest pinning.
        if user_digest is not None and user_digest != digest_hex:
            raise RuntimeError(
                f"Digest mismatch for {ref!r}: user supplied "
                f"sha256:{user_digest}, but local daemon resolved to "
                f"sha256:{digest_hex}."
            )
        container_ref = f"docker://{image_prefix}@sha256:{digest_hex}"
        # Validate the final form passes the strict shape check.
        validate_container_ref(container_ref)
        source_field = ref
        built_from_lock = None
        fingerprint_image = image_prefix

    else:
        raise ValueError(
            f"Unknown backend {backend!r}. "
            f"Supported: 'pixi', 'conda', 'inherit', 'byo'."
        )

    # -------------------------------------------------------------------------
    # env_fingerprint precompute (Cycle C write side; runtime read = Cycle D).
    # -------------------------------------------------------------------------
    fp_spec = f"container:{fingerprint_image}@sha256:{digest_hex}"
    blob = capture_env_content(fp_spec, project_dir)
    env_fingerprint = store_env_content(blob, project_dir)

    # -------------------------------------------------------------------------
    # source_fingerprint: package-list md5 (live-spec registrations only).
    #
    # Lets the canvas point its existing
    # ``GET /api/registry/envs/blob/<md5>`` endpoint at the captured
    # package list (conda explicit list + pip freeze, or pixi.lock
    # semantic slice + pip freeze) so a reviewer can see what packages
    # actually went into the image. Stays None for --from file-mode
    # (no live env to introspect) and for inherit/byo (no separate
    # env to capture distinct from env_fingerprint).
    # -------------------------------------------------------------------------
    source_fingerprint: Optional[str] = None
    if live_spec is not None:
        sf_blob = capture_env_content(live_spec, project_dir)
        source_fingerprint = store_env_content(sf_blob, project_dir)

    # -------------------------------------------------------------------------
    # Manifest write
    # -------------------------------------------------------------------------
    from datetime import datetime, timezone
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = EnvRecord(
        backend=backend,
        source=source_field,
        container=container_ref,
        env_fingerprint=env_fingerprint,
        built_from_lock=built_from_lock,
        built_at=built_at,
        source_fingerprint=source_fingerprint,
    )
    envs_block[name] = record.to_dict()
    manifest["envs"] = envs_block
    manifest.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    save_manifest(project_dir, manifest)
    return record
