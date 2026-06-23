"""
wfc/version.py — Content-addressed versioning and input fingerprinting.

Five public functions:
  get_git_commit(repo_path)             Fail-fast on dirty working tree.
  build_code_fingerprint(method_source_dir)  SHA256 of method source files.
  get_or_create_version(method_id, code_fingerprint, git_commit)
  build_input_fingerprint(upstream_run_ids, sample_ids)
  build_cache_key(code_fingerprint, params, input_fingerprint, env_fingerprint)
  capture_env_content(env_spec, project_dir)
  store_env_content(content, project_dir) -> md5 (env_fingerprint)

Design notes (aligned with design/l2/gaps.md § Gap 15):
  - get_git_commit raises DirtyRepositoryError if there are uncommitted changes.
    Commit-then-run is the intended discipline; there is no --allow-dirty escape hatch.
  - build_code_fingerprint hashes .py files from the registered method copy,
    sorted by relative path.  This makes cache keys stable across non-code commits.
  - build_input_fingerprint uses upstream Run.cache_key (deterministic chain),
    NOT RunOutput.content_hash.  content_hash is archival-only.
    sorted() on the parts list is load-bearing, not an optimisation —
    DB row order is not guaranteed. Changing it silently breaks cache keys.
  - cache_key = SHA256(code_fingerprint + json.dumps(params, sort_keys=True) + fingerprint)
  - git_commit is retained as optional audit metadata on MethodVersion, not part of cache key.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from .database import get_session, project_root
from .models import MethodVersion, Run, RunOutput, Sample


# =============================================================================
# Exceptions
# =============================================================================

class DirtyRepositoryError(RuntimeError):
    """Raised when the git working tree has uncommitted changes.

    The fix is always ``git commit`` (or ``git stash``), never a bypass flag.
    """


# =============================================================================
# Public API
# =============================================================================

def get_git_commit(repo_path: Path | str | None = None) -> str:
    """Return the current HEAD git commit SHA (40 hex chars).

    Args:
        repo_path: Directory within the git repository. Defaults to the
            resolved workflow-canvas project root (not ``Path.cwd()`` — the
            Snakemake hot path runs with cwd rewritten to ``C:\\Windows`` on
            Windows and ``wfc run-step`` must still find the real repo).

    Returns:
        40-character SHA1 hex string.

    Raises:
        DirtyRepositoryError: If the working tree has uncommitted changes.
        RuntimeError: If git is not available or the path is not a git repo.
    """
    if repo_path is None:
        repo_path = project_root()
    cwd = str(repo_path)

    # Fail-fast: check working tree first
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        raise RuntimeError(
            f"git status failed in {cwd!r}: {status.stderr.strip()}"
        )
    # Only tracked-file changes (modified, staged, deleted, renamed) block the
    # run.  Untracked files (??) do not affect the commit SHA cache key, so
    # they are ignored here.
    tracked_dirty = [
        line for line in status.stdout.strip().splitlines()
        if line[:2].strip() and not line.startswith("??")
    ]
    if tracked_dirty:
        dirty_lines = "\n".join(f"    {line}" for line in tracked_dirty)
        raise DirtyRepositoryError(
            "Working tree has uncommitted changes to tracked files — commit before running.\n"
            f"  Dirty files:\n{dirty_lines}"
        )

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git rev-parse HEAD failed in {cwd!r}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def build_code_fingerprint(method_source_dir: Path | str) -> str:
    """Build a SHA256 fingerprint of a method's source files.

    Walks the method source directory, reads all ``.py`` files, sorts them
    by relative path (load-bearing for determinism), concatenates their
    contents, and returns a SHA256 hex digest.

    This function is the content-addressed replacement for using git_commit
    as the code identity component in cache keys.  The directory should be
    the registered copy under ``methods/{method_name}/``.

    Args:
        method_source_dir: Path to the directory containing the method's
            registered source files.

    Returns:
        64-char hex SHA256 string.

    Raises:
        ValueError: If the directory does not exist or contains no ``.py`` files.
    """
    source_dir = Path(method_source_dir)
    if not source_dir.is_dir():
        raise ValueError(
            f"Method source directory does not exist: {source_dir}"
        )

    # Collect all .py files, sorted by relative path for determinism
    py_files = sorted(
        source_dir.rglob("*.py"),
        key=lambda p: p.relative_to(source_dir).as_posix(),
    )
    if not py_files:
        raise ValueError(
            f"Method source directory contains no .py files: {source_dir}"
        )

    hasher = hashlib.sha256()
    for py_file in py_files:
        rel_path = py_file.relative_to(source_dir).as_posix()
        content = py_file.read_text(encoding="utf-8")
        hasher.update(f"{rel_path}:{content}".encode("utf-8"))

    return hasher.hexdigest()


def get_or_create_version(method_id: int, code_fingerprint: str, git_commit: str | None = None) -> int:
    """Return MethodVersion.id for (method_id, code_fingerprint), creating if needed.

    The DB UniqueConstraint on (method_id, code_fingerprint) is the safety net;
    this function provides the upsert logic on top.  Concurrent callers (e.g. 4+
    parallel Snakemake workers sharing the same method/fingerprint) will race on
    the INSERT -- the loser catches IntegrityError and re-SELECTs the winning row.

    Args:
        method_id: Database ID of the Method row.
        code_fingerprint: 64-char SHA256 hex digest from build_code_fingerprint().
        git_commit: Optional 40-char git commit SHA for audit metadata.  Stored
            on the MethodVersion row but not used for identity or cache keys.

    Returns:
        MethodVersion.id (integer).
    """
    # Fast path: row already exists (common case after first worker wins).
    with get_session() as session:
        existing = session.exec(
            select(MethodVersion).where(
                MethodVersion.method_id == method_id,
                MethodVersion.code_fingerprint == code_fingerprint,
            )
        ).first()
        if existing is not None:
            return existing.id  # type: ignore[return-value]

    # Slow path: try to INSERT, fall back to SELECT if another worker beat us.
    try:
        with get_session() as session:
            version = MethodVersion(
                method_id=method_id,
                code_fingerprint=code_fingerprint,
                git_commit=git_commit,
                recorded_at=datetime.now(timezone.utc),
            )
            session.add(version)
            session.commit()
            session.refresh(version)
            return version.id  # type: ignore[return-value]
    except IntegrityError:
        # Another concurrent worker inserted the same (method_id, code_fingerprint)
        # first — retrieve their row.
        with get_session() as session:
            existing = session.exec(
                select(MethodVersion).where(
                    MethodVersion.method_id == method_id,
                    MethodVersion.code_fingerprint == code_fingerprint,
                )
            ).first()
            if existing is None:  # pragma: no cover — should be impossible
                raise RuntimeError(
                    f"get_or_create_version: INSERT failed with IntegrityError "
                    f"but follow-up SELECT found nothing for "
                    f"method_id={method_id}, code_fingerprint={code_fingerprint!r}"
                )
            return existing.id  # type: ignore[return-value]


def build_input_fingerprint(
    upstream_run_ids: Sequence[int],
    sample_ids: Sequence[int] | None = None,
    label: str | None = None,
    strict: bool = False,
) -> str:
    """Build a SHA256 fingerprint of all inputs to a run.

    Uses upstream Run.cache_key (deterministic cache key chain) instead of
    RunOutput.content_hash.  This decouples cache validity from archival
    state -- NULL content_hash (deferred archiving) has no effect on
    fingerprint computation.

    The cache key chain is deterministic: each node's cache_key is
    SHA256(code_fingerprint + params + upstream_cache_keys).  Using
    upstream cache_key here closes the chain.

    Handles both upstream Run rows (downstream nodes) and Sample rows
    (root nodes) in one call -- caller never branches. For Sample rows,
    the DVC content hash is preferred (content-addressed); samples predating
    ADR-009 with content_hash=NULL fall back to path:size:mtime.

    ``sorted()`` on the parts list is **load-bearing**, not an optimisation --
    DB row order is not guaranteed and will silently break cache key stability.

    Args:
        upstream_run_ids: IDs of upstream Run rows. Empty list for root nodes.
        sample_ids: IDs of Sample rows. Provided for root nodes only.
        label: Optional label for fingerprint context (unused, reserved).
        strict: If True, raise on missing data (unused, reserved).

    Returns:
        64-char hex SHA256 string.
    """
    parts: list[str] = []

    with get_session() as session:
        for run_id in upstream_run_ids:
            run = session.get(Run, run_id)
            if run is None:
                continue
            if run.cache_key:
                parts.append(f"key:{run.cache_key}")
            else:
                # Upstream run predates cache key integration -- use a
                # sentinel so the fingerprint is still deterministic
                # (always produces the same value for this run).
                parts.append(f"key:legacy-run-{run_id}")

        for sid in (sample_ids or []):
            sample = session.get(Sample, sid)
            if sample is None:
                continue
            # Prefer DVC content hash (MD5) when available — it is content-
            # addressed, so identical content produces an identical key even
            # if path, size, or mtime differ (re-registration, relocation).
            # Legacy rows registered before ADR-009 have content_hash=NULL
            # and fall back to the path:size:mtime sentinel.
            if sample.content_hash:
                parts.append(f"hash:{sample.content_hash}")
            else:
                path = sample.registered_path or ""
                size = sample.file_size or 0
                mtime = sample.file_mtime or 0.0
                parts.append(f"{path}:{size}:{mtime}")

    return hashlib.sha256(",".join(sorted(parts)).encode()).hexdigest()


def build_cache_key(
    code_fingerprint: str,
    params: dict,
    input_fingerprint: str,
    env_fingerprint: str,
) -> str:
    """Build a SHA256 cache key for a run.

    Pure function -- no DB access.  Uses the content-addressed code fingerprint
    (not git commit) so that unrelated commits do not invalidate cache keys.

    Args:
        code_fingerprint: 64-char SHA256 hex digest from build_code_fingerprint().
        params: Parameter dict for this run (serialised deterministically).
        input_fingerprint: Output of build_input_fingerprint().
        env_fingerprint: 32-char MD5 hex digest from ``store_env_content()``,
            identifying the resolved environment content (lock + pip freeze,
            or interpreter identity + pip freeze for ``inherit``).  Folding
            env into the cache key means that installing a different numpy
            version between runs invalidates the cache — no more silent
            stale-env hits.

    Returns:
        64-char hex SHA256 string.
    """
    raw = (
        code_fingerprint
        + json.dumps(params, sort_keys=True)
        + input_fingerprint
        + env_fingerprint
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# =============================================================================
# Environment content capture (Gap: env_fingerprint provenance)
# =============================================================================

def capture_env_content(env_spec: str, project_dir: Path | str) -> str:
    """Capture a deterministic content blob describing a method's env.

    Backend dispatch:

      - ``pixi:<name>`` or ``pixi:<project>:<env>`` — lock-based intent from
        ``pixi.lock`` (semantic fields, sorted JSON) plus ``pip freeze`` of
        the resolved interpreter.  Uses :func:`resolve_python_for_env` to
        find the interpreter.

      - ``conda:<name>`` — install-based actuality from
        ``conda list --explicit --md5`` plus ``pip freeze``.

      - ``inherit`` — interpreter identity (``sys.executable`` +
        ``sys.version``) plus ``pip freeze`` of the current interpreter.
        Does NOT call :func:`resolve_python_for_env` (which raises on
        ``inherit``).

      - ``container:<image>@sha256:<hex>`` — canonical JSON blob naming
        the container image and its digest. This is the **precompute
        write path** used by :func:`wfc.envs.register` to populate
        ``env_fingerprint`` at registration time. The runtime *read*
        path (looking up a method's container at run-step time) lands
        in Cycle D.

    The returned string is deterministic for a given env state and is the
    input to :func:`store_env_content`, which hashes it and stores the blob
    in the DVC content-addressed cache.

    Args:
        env_spec: Typed env string from ``Method.env`` (e.g.
            ``"pixi:image-io"``, ``"conda:analysis"``, ``"inherit"``,
            ``"container:<image>@sha256:<hex>"``).
        project_dir: Root directory of the wfc project (used to resolve
            ``[pixi]`` / ``[conda]`` roots from ``wf-canvas.toml`` for the
            typed backends).

    Returns:
        A newline-delimited blob whose content varies by backend but is
        deterministic for a given env state.
    """
    import sys
    from .env_introspect import (
        conda_list_explicit,
        pip_freeze,
        pip_freeze_best_effort,
        pixi_lock_section,
    )
    from .register import resolve_python_for_env

    project_dir = Path(project_dir)

    # ADR-019 Cycle D runtime-read short-circuit: if env_spec is a bare env
    # name registered in .wfc/envs.json with a non-empty ``container`` field,
    # return the precomputed env_fingerprint verbatim. No subprocess, no
    # pixi.lock parse — this is the primary runtime perf benefit of the
    # container backend (US-4).
    #
    # Only fires for bare names (no backend prefix). Typed specs (pixi:,
    # conda:, container:, inherit) fall through to their existing branches
    # below. A manifest entry with container="" is treated as non-container
    # and falls through.
    if ":" not in env_spec and env_spec != "inherit":
        try:
            from .envs import get as _envs_get
            record = _envs_get(env_spec, project_dir)
        except Exception:
            record = None
        if record is not None and getattr(record, "container", "") and record.env_fingerprint:
            return record.env_fingerprint

    if env_spec.startswith("container:"):
        # Container-backend precompute path (ADR-019 Cycle C). The spec is
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

    if env_spec == "inherit":
        # No lock, no env resolution — interpreter identity + installed
        # packages is the honest fingerprint.  Order is load-bearing:
        # interpreter path, interpreter version, then pip freeze.
        return (
            f"executable={sys.executable}\n"
            f"version={sys.version}\n"
            f"{pip_freeze(sys.executable)}"
        )

    parts = env_spec.split(":")
    backend = parts[0]

    if backend == "pixi":
        from .init import read_config
        config = read_config(project_dir)
        pixi_root = config.get("pixi_root")
        if not pixi_root:
            raise ValueError(
                f"No [pixi] root configured in {project_dir}/.wfc/wf-canvas.toml; "
                f"cannot capture env for '{env_spec}'."
            )
        # "pixi:<name>"           -> <name> is the project dir name; env
        #                            is resolved by pixi_lock_section's
        #                            "default" / single-env fallback.
        # "pixi:<project>:<env>"  -> project is the dir name, env is the
        #                            exact key into the lock's environments
        #                            block.  Project and env may differ.
        if len(parts) == 2:
            project_name = parts[1]
            env_name = None
        elif len(parts) == 3:
            project_name = parts[1]
            env_name = parts[2]
        else:
            raise ValueError(
                f"Malformed pixi env spec {env_spec!r}: expected "
                f"'pixi:<name>' or 'pixi:<project>:<env>'."
            )
        # Platform selection is lock-driven: pixi_lock_section reads the
        # platform list directly from the lock's ``environments`` block.
        # For standalone pixi envs (the wfc convention) the lock lists a
        # single platform, which is unambiguously the right answer.  Multi-
        # platform locks surface an actionable error pointing at an explicit
        # platform argument — we never guess via sys.platform.
        lock_section = pixi_lock_section(
            pixi_root, project_name, env_name=env_name
        )

        env_python = resolve_python_for_env(
            env_spec,
            pixi_root=pixi_root,
            conda_root=config.get("conda_root") or None,
            project_dir=project_dir,
        )
        # pixi.lock is the authoritative intent; pip freeze is a drift
        # check for anything `pip install`ed on top. If the env has no
        # pip at all (pure conda-forge), tolerate that by recording a
        # sentinel so the fingerprint still reflects "pip absent" vs
        # "pip present with no extras".
        return f"{lock_section}\n{pip_freeze_best_effort(env_python)}"

    if backend == "conda":
        from .init import read_config
        config = read_config(project_dir)
        env_python = resolve_python_for_env(
            env_spec,
            pixi_root=config.get("pixi_root") or None,
            conda_root=config.get("conda_root") or None,
            project_dir=project_dir,
        )
        # conda prefix = parent of bin/python (posix) or parent of python.exe
        env_prefix = env_python.parent.parent if env_python.name == "python" else env_python.parent
        explicit = conda_list_explicit(env_prefix)
        # `conda list --explicit --md5` is already the authoritative
        # actuality blob; pip freeze is the drift check. Tolerate a
        # missing pip the same way pixi does.
        return f"{explicit}\n{pip_freeze_best_effort(env_python)}"

    raise ValueError(
        f"Unknown env backend in spec '{env_spec}'. "
        f"Expected 'pixi:...', 'conda:...', or 'inherit'."
    )


def store_env_content(content: str, project_dir: Path | str) -> str:
    """Write ``content`` to a temp file, hash it, cache it in DVC, return md5.

    The md5 returned is the environment fingerprint used as the 4th arg to
    :func:`build_cache_key` and persisted as ``Run.env_fingerprint``.

    The blob is stored under ``.dvc/cache/files/md5/{first2}/{rest}`` so it
    can be retrieved later by the returned md5.

    Args:
        content: Deterministic env content blob (from
            :func:`capture_env_content`).
        project_dir: Root directory of the wfc project.

    Returns:
        32-character hex MD5 digest of ``content``.

    Raises:
        Whatever :func:`wfc.provenance.cache_file` raises — the temp file is
        always cleaned up, success OR failure.
    """
    import os
    import tempfile

    from .provenance import cache_file, hash_file

    # Write to a NamedTemporaryFile with delete=False so we control cleanup.
    # encoding=utf-8 and newline="" keep the bytes deterministic across
    # platforms; the caller is expected to provide canonical content.
    fd, tmp_path = tempfile.mkstemp(prefix="wfc-env-", suffix=".blob")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        md5 = hash_file(tmp_path)
        cache_file(tmp_path, md5, project_dir)
        return md5
    finally:
        # Clean up the temp file on ALL paths — success, hash_file failure,
        # cache_file failure.  Use try/except to stay defensive on Windows
        # where the file could be held open briefly.
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
