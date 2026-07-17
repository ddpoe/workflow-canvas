"""
DVC provenance storage -- content-addressed caching via DVC (ADR-007 Phase 2).

Uses DVC's content-addressed cache layout directly (no subprocess calls,
no .dvc pointer files).  The cache directory structure is:

    .dvc/cache/files/md5/{hash[:2]}/{hash[2:]}

Core operations:
- hash_file(path)             -- MD5 hash a file, return hex digest
- hash_directory(path)        -- MD5 hash a directory (sorted manifest of file hashes)
- cache_file(path, md5, project_dir)  -- copy/link file into .dvc/cache/
- restore_from_cache(md5, dest, project_dir)  -- copy from cache to workspace
- push_cache(md5s, project_dir)  -- copy cache objects to configured remote
- pull_cache(md5s, project_dir)  -- fetch cache objects from remote to local cache
- ensure_dvc_ready(project_dir)  -- validate [dvc] config in wf-canvas.toml
- init_dvc(project_dir, dvc_config) -- create .dvc/ cache structure and remote

Design decision (D-5 Builder): Direct cache directory manipulation instead of
DVC Python API.  The DVC Python API is heavyweight (slow import) and not
documented for cache-only usage.  The .dvc/cache/ two-level directory layout
is stable across DVC 2.x and 3.x.  See cycle manifest for details.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path


# =============================================================================
# Exceptions
# =============================================================================

class DvcNotInstalledError(Exception):
    """Raised when DVC is not installed or not on PATH."""


class DvcNotConfiguredError(Exception):
    """Raised when the [dvc] section is missing from wf-canvas.toml."""


class DvcRemoteError(Exception):
    """Raised when the DVC remote cannot be configured or reached."""


# =============================================================================
# Content hashing
# =============================================================================

_CHUNK_SIZE = 1 << 20  # 1 MiB — stream large files without loading into memory


def hash_file(path: Path | str) -> str:
    """Compute the MD5 hex digest of a file's content.

    Streams in chunks to handle large files without excessive memory use.

    Args:
        path: Path to the file to hash.

    Returns:
        32-character hex MD5 digest.

    Raises:
        FileNotFoundError: If path does not exist.
        IsADirectoryError: If path is a directory (use hash_directory instead).
    """
    path = Path(path)
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_directory(path: Path | str) -> str:
    """Compute a stable MD5 digest for a directory.

    Walks the directory tree in sorted order, hashing each file and
    combining with its relative path.  The result is an MD5 of the
    sorted manifest (path:md5 pairs), yielding a 32-char hex digest
    consistent with DVC's md5 format.

    Args:
        path: Path to the directory to hash.

    Returns:
        32-character hex digest representing the directory content.

    Raises:
        FileNotFoundError: If path does not exist.
        NotADirectoryError: If path is not a directory.
    """
    path = Path(path)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    parts: list[str] = []
    for root, _dirs, files in os.walk(path):
        root_path = Path(root)
        for fname in sorted(files):
            fpath = root_path / fname
            rel = fpath.relative_to(path).as_posix()
            file_md5 = hash_file(fpath)
            parts.append(f"{rel}:{file_md5}")

    parts.sort()
    manifest = "\n".join(parts)
    # Use md5 of the manifest for the directory hash
    return hashlib.md5(manifest.encode()).hexdigest()


def hash_path(path: Path | str) -> str:
    """Hash a file or directory, dispatching to the appropriate function.

    Args:
        path: Path to hash.

    Returns:
        32-character hex MD5 digest.
    """
    path = Path(path)
    if path.is_dir():
        return hash_directory(path)
    return hash_file(path)


# =============================================================================
# Cache operations
# =============================================================================

def _cache_dir(project_dir: Path) -> Path:
    """Return the DVC cache directory for files: .dvc/cache/files/md5/."""
    return project_dir / ".dvc" / "cache" / "files" / "md5"


def _cache_path(project_dir: Path, md5: str) -> Path:
    """Return the cache path for a given md5: .dvc/cache/files/md5/{md5[:2]}/{md5[2:]}."""
    return _cache_dir(project_dir) / md5[:2] / md5[2:]


def _make_read_only(path: Path | str) -> None:
    """Best-effort chmod a cache entry read-only (footgun guard, ADR-018).

    Files become 0444; directory entries are walked with files set to 0444
    and directories to 0555.  On Windows ``os.chmod(p, 0o444)`` sets the
    read-only attribute on files, which blocks accidental overwrites of
    cache entries handed out by path (e.g. ``wfc export --path``).

    Best-effort by design: a chmod failure (root-owned container outputs,
    exotic filesystems) warns to stderr and continues — archiving must
    never fail because protection could not be applied.  This is a footgun
    guard, not a security boundary.

    Args:
        path: Cache entry path (file or directory).
    """
    path = Path(path)
    try:
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                for name in files:
                    os.chmod(os.path.join(root, name), 0o444)
                for name in dirs:
                    os.chmod(os.path.join(root, name), 0o555)
            os.chmod(path, 0o555)
        else:
            os.chmod(path, 0o444)
    except OSError as exc:
        print(
            f"WARNING: could not mark cache entry read-only ({path}): {exc}",
            file=sys.stderr,
        )


def _make_writable(path: Path | str) -> None:
    """Best-effort chmod a path (recursively) back to owner-writable.

    Inverse of :func:`_make_read_only`, used before deleting or replacing
    protected entries/copies (the Windows read-only attribute blocks
    ``unlink``/``rmtree``).  Silent best-effort: failures surface later as
    the actual delete/replace error, which is more informative.

    Args:
        path: Path (file or directory) to make writable.
    """
    path = Path(path)
    try:
        if path.is_dir():
            os.chmod(path, 0o755)
            for root, dirs, files in os.walk(path):
                for name in dirs:
                    os.chmod(os.path.join(root, name), 0o755)
                for name in files:
                    os.chmod(os.path.join(root, name), 0o644)
        else:
            os.chmod(path, 0o644)
    except OSError:
        pass


def cache_file(
    path: Path | str,
    md5: str,
    project_dir: Path | str,
    move: bool = True,
) -> Path:
    """Move (or copy) a file into the DVC content-addressed cache.

    ADR-018: Cache is authoritative. Pipeline outputs are produced into a
    staging area and moved into the cache; the staging copy is consumed.
    For legacy callers (e.g. ``register_sample``) that pass a user-owned
    source, set ``move=False`` to preserve the source file.

    Move strategy:
    - Try ``os.rename`` first (atomic, fast on same volume).
    - On cross-volume ``OSError``, fall back to ``shutil.copy2`` + ``unlink``.
    - Directories use ``shutil.move`` (handles cross-volume internally).

    Idempotency:
    - If ``dest`` already exists, this is a dedup. Unlink the staging
      duplicate (when ``move=True``) and return ``dest`` without overwriting.

    Args:
        path: Source file/directory path.
        md5: Pre-computed MD5 hex digest of the path.
        project_dir: Root directory of the wfc project.
        move: If True (default), consume the source. If False, copy and
            leave source intact.

    Returns:
        The cache path where the file was stored.
    """
    project_dir = Path(project_dir).resolve()
    path = Path(path)
    dest = _cache_path(project_dir, md5)

    if dest.exists():
        # Content-addressed idempotency: dedup. Unlink the staging
        # duplicate so callers don't leave orphan staging copies behind.
        if move and path.exists() and path != dest:
            try:
                if path.is_dir():
                    shutil.rmtree(str(path))
                else:
                    path.unlink()
            except FileNotFoundError:
                pass
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    if path.is_dir():
        if move:
            # shutil.move handles cross-volume directory transfers.
            shutil.move(str(path), str(dest))
        else:
            shutil.copytree(str(path), str(dest))
        _make_read_only(dest)
        return dest

    # File path
    if move:
        try:
            # os.rename is atomic on same volume; OSError on cross-volume.
            os.rename(str(path), str(dest))
        except OSError:
            # Cross-volume fallback: copy then unlink.
            tmp = dest.with_suffix(".tmp")
            try:
                shutil.copy2(str(path), str(tmp))
                tmp.replace(dest)
            except Exception:
                if tmp.exists():
                    tmp.unlink()
                raise
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    else:
        # Copy mode: atomic write via tmp+rename, preserve source.
        tmp = dest.with_suffix(".tmp")
        try:
            shutil.copy2(str(path), str(tmp))
            tmp.replace(dest)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    _make_read_only(dest)
    return dest


def restore_from_cache(
    md5: str, dest: Path | str, project_dir: Path | str
) -> bool:
    """Restore a file from the DVC cache to a workspace path.

    Checkout-like behavior: if the destination already exists and its
    content hash matches ``md5``, the restore is skipped (idempotent).
    If the destination exists but the hash mismatches (stale or corrupted),
    the file is replaced from cache.

    Args:
        md5: Content hash of the file to restore.
        dest: Destination path in the workspace.
        project_dir: Root directory of the wfc project.

    Returns:
        True if restore succeeded (or was skipped because dest is valid),
        False if the cache entry is missing.
    """
    project_dir = Path(project_dir).resolve()
    dest = Path(dest)
    src = _cache_path(project_dir, md5)

    if not src.exists():
        return False

    # If dest already exists, verify its content hash
    if dest.exists():
        existing_hash = hash_path(dest)
        if existing_hash == md5:
            return True  # Already valid — skip restore
        # Stale/corrupted — remove before replacing. Workspace copies
        # restored from a protected cache entry inherit its read-only bits
        # (copy2/copytree preserve mode), and on Windows the read-only
        # attribute blocks unlink — make the stale dest writable first.
        _make_writable(dest)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        shutil.copytree(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))

    return True


# =============================================================================
# Remote operations (local remote only)
# =============================================================================

def _remote_path(project_dir: Path) -> Path | None:
    """Resolve the configured DVC remote path (local-FS remotes only).

    Returns a Path for file://, plain local paths, and legacy
    ``remote_path`` configs.  Returns None for non-local schemes
    (ssh://, s3://, ...) -- callers should route through ``wfc.remote``
    in that case.

    Kept as a safety-net helper for the legacy push_cache/pull_cache
    fallback path and for ``check_remote_reachable``.
    """
    try:
        dvc_config = ensure_dvc_ready(project_dir)
    except DvcNotConfiguredError:
        return None

    url = dvc_config.get("url") or dvc_config.get("remote_path") or ""
    if not url:
        return None

    # Strip file:// prefix if present.
    if url.startswith("file://"):
        url = url[len("file://"):]
    # Non-local schemes -> not a filesystem path.
    if "://" in url:
        return None

    rp = Path(url)
    if not rp.is_absolute():
        rp = project_dir / rp
    return rp.resolve()


def push_cache(md5s: list[str], project_dir: Path | str) -> bool:
    """Push cache objects to the configured DVC remote (ADR-018).

    Delegates to ``wfc.remote.push`` which uses the DVC Python API
    (``DataCloud.push``) -- this supports any DVC-native backend
    (file/, s3://, ssh://, gs://, ...).  When ``.dvc/config`` has no
    remotes, returns False with a warning (callers should pre-flight
    with ``ensure_dvc_ready``).

    Args:
        md5s: List of MD5 hex digests to push.
        project_dir: Root directory of the wfc project.

    Returns:
        True if all pushes succeeded, False on any failure or when
        no remote is configured.
    """
    project_dir = Path(project_dir).resolve()

    if not md5s:
        return True

    # ADR-018: route through wfc.remote (DVC Python API).
    from .remote import has_remote_configured, push as remote_push
    if not has_remote_configured(project_dir):
        print("WARNING: DVC remote not configured, push skipped.", file=sys.stderr)
        return False

    try:
        result = remote_push(md5s, project_dir)
        failed = getattr(result, "failed", None) or []
        if failed:
            print(
                f"WARNING: DVC push reported {len(failed)} failures.",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as exc:
        print(f"WARNING: DVC push failed: {exc}", file=sys.stderr)
        return False


def pull_cache(md5s: list[str], project_dir: Path | str) -> bool:
    """Pull cache objects from the configured DVC remote (ADR-018).

    Delegates to ``wfc.remote.pull``.  When ``.dvc/config`` has no
    remotes, returns False with a warning (callers should pre-flight
    with ``ensure_dvc_ready``).

    Args:
        md5s: List of MD5 hex digests to pull.
        project_dir: Root directory of the wfc project.

    Returns:
        True if all pulls succeeded, False on any failure or when
        no remote is configured.
    """
    project_dir = Path(project_dir).resolve()

    if not md5s:
        return True

    # ADR-018: DVC Python API.
    from .remote import has_remote_configured, pull as remote_pull
    if not has_remote_configured(project_dir):
        print("WARNING: DVC remote not configured, pull skipped.", file=sys.stderr)
        return False

    try:
        result = remote_pull(md5s, project_dir)
        failed = getattr(result, "failed", None) or []
        if failed:
            print(
                f"WARNING: DVC pull reported {len(failed)} failures.",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as exc:
        print(f"WARNING: DVC pull failed: {exc}", file=sys.stderr)
        return False


# =============================================================================
# Backward-compatible wrappers (used by existing call sites)
# =============================================================================

def push_artifacts(run_id: int, paths: list[str | Path], project_dir: Path) -> bool:
    """Hash and push artifact files to the DVC cache and remote.

    Replacement for the old subprocess-based push_artifacts that ran
    ``dvc add`` + ``dvc push``.  Now hashes files directly, stores in
    the local cache, and copies to the configured remote.  No .dvc
    pointer files are created.

    Args:
        run_id: The run ID (for logging context).
        paths: List of artifact file/directory paths to push.
        project_dir: Root directory of the wfc project.

    Returns:
        True if push succeeded, False if it failed (non-fatal).
    """
    project_dir = Path(project_dir).resolve()

    if not paths:
        return True

    try:
        ensure_dvc_ready(project_dir)
    except DvcNotConfiguredError as exc:
        print(f"WARNING: DVC provenance skipped for run {run_id}: {exc}", file=sys.stderr)
        return False

    md5s: list[str] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            print(f"WARNING: artifact path does not exist, skipping: {p}", file=sys.stderr)
            continue
        try:
            md5 = hash_path(p)
            # push_artifacts caches existing artifacts; preserve source.
            cache_file(p, md5, project_dir, move=False)
            md5s.append(md5)
        except Exception as exc:
            print(
                f"WARNING: cache failed for {p} (run {run_id}): {exc}",
                file=sys.stderr,
            )
            return False

    return push_cache(md5s, project_dir)


def pull_artifacts(run_id: int, project_dir: Path) -> bool:
    """Pull artifacts for a run from the DVC remote cache.

    Queries the DB for RunOutput rows with content_hash and pulls
    those cache objects from the remote.  Falls back gracefully when
    DVC is not configured or content_hash is null.

    Args:
        run_id: The run ID.
        project_dir: Root directory of the wfc project.

    Returns:
        True if pull succeeded or nothing to pull, False on failure.
    """
    project_dir = Path(project_dir).resolve()

    try:
        ensure_dvc_ready(project_dir)
    except DvcNotConfiguredError as exc:
        print(f"WARNING: DVC provenance pull skipped for run {run_id}: {exc}", file=sys.stderr)
        return False

    # Look up content hashes for this run's outputs
    try:
        from .database import get_session
        from .models import RunOutput
        from sqlmodel import select

        md5s: list[str] = []
        with get_session() as session:
            outputs = session.exec(
                select(RunOutput).where(RunOutput.run_id == run_id)
            ).all()
            for ro in outputs:
                if ro.content_hash:
                    md5s.append(ro.content_hash)

        if not md5s:
            return True  # Nothing to pull (old run without content_hash)

        return pull_cache(md5s, project_dir)
    except Exception as exc:
        print(f"WARNING: pull failed for run {run_id}: {exc}", file=sys.stderr)
        return False


# =============================================================================
# Configuration and initialization
# =============================================================================

def ensure_dvc_ready(project_dir: Path) -> dict:
    """Validate that DVC is configured for ADR-018 multi-backend storage.

    Checks prerequisites:
    1. The [dvc] section exists in .wfc/wf-canvas.toml and has a ``url`` field.
    2. ``.dvc/config`` declares at least one remote (any URL scheme).

    No longer gates on ``remote_type == "local"`` -- ADR-018 supports any
    DVC-native backend (file/, s3://, ssh://, gs://, azure://, ...).  Warns
    (does not error) on drift between ``[dvc] url`` and ``.dvc/config``.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict with the parsed DVC config (url, auto_init, plus any legacy keys).

    Raises:
        DvcNotConfiguredError: If [dvc] section is missing, ``url`` is unset,
            or ``.dvc/config`` declares no remotes.
    """
    project_dir = Path(project_dir).resolve()

    # 1. Check [dvc] config section exists in wf-canvas.toml
    from .init import read_config
    config = read_config(project_dir)
    dvc_config = config.get("dvc")
    if dvc_config is None:
        raise DvcNotConfiguredError(
            "No [dvc] section found in .wfc/wf-canvas.toml. "
            "Add a [dvc] section with a `url` field to enable provenance."
        )

    # 2. Validate `url` is set (ADR-018 replaces the legacy remote_path)
    url = dvc_config.get("url") or dvc_config.get("remote_path")
    if not url:
        raise DvcNotConfiguredError(
            "No `url` field in [dvc] config. "
            "Set `url = \"<scheme>://...\"` (e.g. file:///path, s3://bucket, ssh://host/path)."
        )

    # 3. Verify .dvc/config has at least one remote.  Cheap INI parse via
    # wfc.remote.has_remote_configured (no DVC import).
    from .remote import has_remote_configured
    if not has_remote_configured(project_dir):
        raise DvcNotConfiguredError(
            ".dvc/config has no remotes configured.  Run `wfc init` to mirror "
            "`[dvc] url` from wf-canvas.toml to .dvc/config, or run "
            "`dvc remote add default <url>` manually."
        )

    # 4. Warn (do not error) on drift between [dvc] url and .dvc/config
    # default remote.  This catches the rare case where the two diverge.
    try:
        import configparser as _cp
        parser = _cp.ConfigParser()
        parser.read(project_dir / ".dvc" / "config")
        # Locate the default-remote name from [core] remote = ...
        default_name = None
        if parser.has_option("core", "remote"):
            default_name = parser.get("core", "remote")
        # Look up the URL for that remote
        if default_name:
            section_name = f'remote "{default_name}"'
            if parser.has_section(section_name) and parser.has_option(section_name, "url"):
                dvc_config_url = parser.get(section_name, "url")
                if dvc_config_url != url:
                    print(
                        f"WARNING: [dvc] url={url!r} in wf-canvas.toml does not match "
                        f".dvc/config remote {default_name!r} url={dvc_config_url!r}. "
                        "Re-run `wfc init` to re-mirror.",
                        file=sys.stderr,
                    )
    except Exception:
        # Drift-check is best-effort; never fail ensure_dvc_ready over it.
        pass

    return dvc_config


def init_dvc(project_dir: Path, dvc_config: dict) -> None:
    """Initialize DVC cache structure and mirror remote URL to .dvc/config (ADR-018).

    Creates the .dvc/cache/files/md5/ directory tree, writes ``.dvc/config``
    via a direct INI write (no DVC import) mirroring the ``[dvc] url`` field
    from wf-canvas.toml to a remote named ``default``, and -- for local-FS
    URLs -- pre-creates the target directory.

    Called by ``wfc init`` when a [dvc] section is present in wf-canvas.toml.
    Safe to call if already initialized (idempotent).

    Args:
        project_dir: Root directory of the wfc project.
        dvc_config: Parsed [dvc] config dict.  Must contain ``url`` (or the
            legacy ``remote_path``).

    Raises:
        DvcNotConfiguredError: If ``url`` is empty.
    """
    project_dir = Path(project_dir).resolve()

    url = dvc_config.get("url") or dvc_config.get("remote_path", "")
    if not url:
        raise DvcNotConfiguredError(
            "No `url` specified in [dvc] config."
        )

    # Create .dvc/cache/ directory structure
    cache = _cache_dir(project_dir)
    cache.mkdir(parents=True, exist_ok=True)

    # Create .dvc/ marker (so other tools know DVC is initialized)
    dvc_dir = project_dir / ".dvc"
    dvc_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create local-FS remote directory (ssh://, s3://, ... are skipped).
    is_local_fs = "://" not in url or url.startswith("file://")
    if is_local_fs:
        local_path = url[len("file://"):] if url.startswith("file://") else url
        remote_path_obj = Path(local_path)
        if not remote_path_obj.is_absolute():
            remote_path_obj = project_dir / remote_path_obj
        remote_path_obj.mkdir(parents=True, exist_ok=True)

    # Mirror [dvc] url to .dvc/config via configparser (no DVC import).
    import configparser as _cp
    config_path = dvc_dir / "config"
    parser = _cp.ConfigParser()
    if config_path.exists():
        parser.read(config_path)
    if not parser.has_section("core"):
        parser.add_section("core")
    parser.set("core", "remote", "default")
    remote_section = 'remote "default"'
    if not parser.has_section(remote_section):
        parser.add_section(remote_section)
    parser.set(remote_section, "url", url)
    with open(config_path, "w", encoding="utf-8") as f:
        parser.write(f)


# =============================================================================
# Remote reachability check (ADR-011 safety)
# =============================================================================

def check_remote_reachable(project_dir: Path) -> tuple[bool, str]:
    """Check whether the configured DVC remote is reachable.

    For local-type remotes, checks that the remote directory exists and
    is accessible.  Returns (True, "") if reachable, or (False, reason)
    if not.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Tuple of (reachable, reason).  ``reason`` is empty when reachable.
    """
    remote = _remote_path(project_dir)
    if remote is None:
        return False, "DVC remote not configured (no [dvc] section in wf-canvas.toml)"
    if not remote.exists():
        return False, f"DVC remote path does not exist: {remote}"
    if not remote.is_dir():
        return False, f"DVC remote path is not a directory: {remote}"
    return True, ""


# =============================================================================
# Cache pruning utilities (ADR-011)
# =============================================================================

def referenced_run_ids() -> set[int]:
    """Return the set of run IDs still referenced by RunOutput records.

    Returns:
        Set of integer run IDs that have at least one RunOutput row.
    """
    from .database import get_session
    from .models import RunOutput
    from sqlmodel import select

    with get_session() as session:
        rows = session.exec(select(RunOutput.run_id)).all()
        return set(rows)


def referenced_content_hashes() -> set[str]:
    """Return the set of content hashes still referenced by RunOutput records.

    Returns:
        Set of MD5 hex strings from all RunOutput rows with non-null content_hash.
    """
    from .database import get_session
    from .models import RunOutput
    from sqlmodel import select

    with get_session() as session:
        rows = session.exec(select(RunOutput.content_hash)).all()
        return {h for h in rows if h is not None}


def scan_run_archives(project_dir: Path) -> dict[int, Path]:
    """Scan .runs/ and return a mapping of run_id -> archive directory path.

    Only directories whose names are all digits are included (the standard
    zero-padded format like 00000001).

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict mapping integer run ID to its archive Path.
    """
    runs = project_dir / ".runs"
    if not runs.exists():
        return {}
    result = {}
    for entry in runs.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            result[int(entry.name)] = entry
    return result


def scan_dvc_cache_entries(project_dir: Path) -> dict[str, Path]:
    """Scan .dvc/cache/files/md5/ and return a mapping of hash -> cache path.

    Reconstructs the full MD5 hex digest from the two-level directory
    layout: {hash[:2]}/{hash[2:]}.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict mapping MD5 hex string to its cache entry Path.
    """
    cache = _cache_dir(project_dir)
    if not cache.exists():
        return {}
    result = {}
    for prefix_dir in cache.iterdir():
        if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
            continue
        for entry in prefix_dir.iterdir():
            md5 = prefix_dir.name + entry.name
            result[md5] = entry
    return result


def prune_run_archives(
    project_dir: Path,
    *,
    all_archives: bool = False,
    dry_run: bool = False,
    exclude_run_ids: "set[int] | None" = None,
) -> list[Path]:
    """Remove unreferenced run archive directories from .runs/.

    Args:
        project_dir: Root directory of the wfc project.
        all_archives: If True, remove all archives regardless of reference status.
        dry_run: If True, return the list of paths that would be deleted
            without actually deleting them.
        exclude_run_ids: Optional set of run IDs to exclude from pruning
            (e.g., runs with un-archived outputs).

    Returns:
        List of archive paths that were (or would be) deleted.
    """
    project_dir = Path(project_dir).resolve()
    archives = scan_run_archives(project_dir)
    if not archives:
        return []

    if all_archives:
        to_delete = list(archives.values())
    else:
        referenced = referenced_run_ids()
        to_delete = [
            path for rid, path in archives.items() if rid not in referenced
        ]

    # Filter out excluded run IDs (e.g., un-archived outputs)
    if exclude_run_ids:
        excluded_paths = {
            archives[rid] for rid in exclude_run_ids if rid in archives
        }
        to_delete = [p for p in to_delete if p not in excluded_paths]

    if not dry_run:
        for path in to_delete:
            shutil.rmtree(path)

    return to_delete


def prune_dvc_cache(
    project_dir: Path,
    *,
    all_entries: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> list[Path]:
    """Remove DVC cache entries from .dvc/cache/files/md5/.

    By default, only unreferenced entries are removed (entries whose MD5
    hash does not appear in any RunOutput.content_hash record).  When
    ``all_entries`` is True, all cache entries are removed regardless of
    reference status.

    ADR-018 Task 7 guard: when a remote is configured and ``force`` is
    False, the prune skips any cache entry whose corresponding RunOutput
    or Sample row has ``pushed_at IS NULL`` (i.e., not yet pushed to the
    remote).  This prevents data loss when the worker is still draining.
    In local-only mode (no remote in ``.dvc/config``) the guard does not
    apply.

    Args:
        project_dir: Root directory of the wfc project.
        all_entries: If True, remove all cache entries regardless of
            reference status.
        dry_run: If True, return the list of paths that would be deleted
            without actually deleting them.
        force: Bypass the ADR-018 ``pushed_at IS NULL`` guard.

    Returns:
        List of cache entry paths that were (or would be) deleted.
    """
    project_dir = Path(project_dir).resolve()
    entries = scan_dvc_cache_entries(project_dir)
    if not entries:
        return []

    # ADR-018 guard: collect hashes that are referenced but not yet pushed.
    unpushed_hashes: set[str] = set()
    try:
        from .remote import has_remote_configured
        remote_active = has_remote_configured(project_dir)
    except Exception:
        remote_active = False
    if remote_active and not force:
        from .database import get_session
        from .models import RunOutput as _RO, Sample as _S
        from sqlmodel import select as _sel

        with get_session() as session:
            for r in session.exec(
                _sel(_RO).where(_RO.pushed_at.is_(None))  # type: ignore[union-attr]
            ).all():
                if r.content_hash:
                    unpushed_hashes.add(r.content_hash)
            for s in session.exec(
                _sel(_S).where(_S.pushed_at.is_(None))  # type: ignore[union-attr]
            ).all():
                if s.content_hash:
                    unpushed_hashes.add(s.content_hash)

    if all_entries:
        to_delete = [p for md5, p in entries.items() if md5 not in unpushed_hashes]
    else:
        referenced = referenced_content_hashes()
        to_delete = [
            path for md5, path in entries.items()
            if md5 not in referenced and md5 not in unpushed_hashes
        ]

    if not dry_run:
        for path in to_delete:
            # Cache entries are read-only (footgun guard); on Windows the
            # read-only attribute blocks unlink/rmtree, so make each
            # selected entry deletable first (best-effort).
            _make_writable(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    return to_delete


# =============================================================================
# Deferred output archiving
# =============================================================================

def archive_outputs(
    project_dir: "Path | str",
    *,
    run_id: "int | None" = None,
    progress_fn: "object | None" = None,
) -> list[dict]:
    """Hash and cache all un-archived RunOutput rows.

    Queries the DB for RunOutput rows where content_hash IS NULL (deferred
    archiving), computes hashes, copies files into the DVC cache, and
    updates the DB rows.

    Args:
        project_dir: Root directory of the wfc project.
        run_id: Optional filter to archive only outputs from a specific run.
        progress_fn: Optional callback called for each file with
            (output_name, status) where status is "archived", "missing",
            or "error:{message}".

    Returns:
        List of dicts with keys: run_id, output_name, content_hash, status.
    """
    from .database import get_session
    from .models import RunOutput, Run
    from sqlmodel import select

    project_dir = Path(project_dir).resolve()
    results: list[dict] = []

    with get_session() as session:
        query = select(RunOutput).where(RunOutput.content_hash.is_(None))  # type: ignore[union-attr]
        if run_id is not None:
            query = query.where(RunOutput.run_id == run_id)

        # Only archive outputs from completed runs
        query = query.join(Run, RunOutput.run_id == Run.id).where(  # type: ignore[arg-type]
            Run.status == "completed"
        )

        outputs = session.exec(query).all()

        for ro in outputs:
            artifact = Path(ro.artifact_path) if ro.artifact_path else None
            entry: dict = {
                "run_id": ro.run_id,
                "output_name": ro.output_name,
                "content_hash": None,
                "status": "unknown",
            }

            if artifact is None or not artifact.exists():
                entry["status"] = "missing"
                if progress_fn:
                    progress_fn(ro.output_name or "<unknown>", "missing")
                results.append(entry)
                continue

            try:
                content_hash = hash_path(artifact)
                # archive caches existing artifacts; preserve source.
                cache_file(artifact, content_hash, project_dir, move=False)
                ro.content_hash = content_hash
                session.add(ro)
                entry["content_hash"] = content_hash
                entry["status"] = "archived"
                if progress_fn:
                    progress_fn(ro.output_name or "<unknown>", "archived")
            except Exception as exc:
                entry["status"] = f"error:{exc}"
                if progress_fn:
                    progress_fn(ro.output_name or "<unknown>", f"error:{exc}")

            results.append(entry)

        session.commit()

    # Protection sweep (best-effort, idempotent): mark every cache entry
    # read-only. Covers entries written before write-time protection landed
    # and entries materialized by DVC pulls, which bypass cache_file. Runs
    # on both `wfc cache archive` and the run_pipeline auto-archive.
    for entry_path in scan_dvc_cache_entries(project_dir).values():
        _make_read_only(entry_path)

    return results
