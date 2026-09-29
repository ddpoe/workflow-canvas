"""Storage setup: validate and initialize a project's ``[dvc]`` configuration.

- :func:`ensure_dvc_ready` checks that ``wf-canvas.toml`` has a ``[dvc]``
  section with a ``url``, that a local-filesystem url lies outside the
  project, and that ``.dvc/config`` declares a remote.  It warns, without
  failing, when the two urls drift apart.
- :func:`init_dvc` creates the cache tree and mirrors ``[dvc] url`` into
  ``.dvc/config`` as the ``default`` remote, without importing DVC.
- :func:`check_remote_reachable` checks that a local-filesystem remote
  exists and is a directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import layout
from .cache import _cache_dir


class DvcNotInstalledError(Exception):
    """Raised when DVC is not installed or not on PATH."""


class DvcNotConfiguredError(Exception):
    """Raised when the [dvc] section is missing from wf-canvas.toml."""


def _validate_remote_containment(url: str, project_dir: Path) -> None:
    """Reject a local-FS remote url that resolves inside the project.

    The archive's durability contract (prune deletes local copies on the
    assumption the remote holds another) breaks when the remote lives
    inside the project tree -- worst case ``url = ".dvc/cache"`` makes
    every push a self-copy no-op and prune then deletes the only copy.
    The project tree is also bind-mounted into method containers, so an
    inside-project archive would be writable by container code.
    Non-local schemes (s3://, ssh://, ...) are skipped.

    Args:
        url: The ``[dvc] url`` value (plain path or file:// URL).
        project_dir: Resolved project root.

    Raises:
        DvcNotConfiguredError: If the resolved remote path is the project
            directory or any path inside it.
    """
    # Same parsing as _remote_path: strip file://, skip non-local schemes.
    if url.startswith("file://"):
        url = url[len("file://"):]
    elif "://" in url:
        return
    rp = Path(url)
    if not rp.is_absolute():
        rp = project_dir / rp
    rp = rp.resolve()
    if rp.is_relative_to(project_dir):
        raise DvcNotConfiguredError(
            f"[dvc] url resolves inside the project ({rp}). The archive "
            f"must live outside the project tree -- otherwise `wfc prune` "
            f"can delete the only copy of your data. Point `url` at a "
            f"separate location, e.g. a shared drive or ~/.wfc/archives/."
        )


def _remote_path(project_dir: Path) -> Path | None:
    """Resolve the configured DVC remote path (local-FS remotes only).

    Returns a Path for file://, plain local paths, and legacy
    ``remote_path`` configs.  Returns None for non-local schemes
    (ssh://, s3://, ...), which ``check_remote_reachable`` probes through
    ``wfc.storage.transport`` instead, and for an unconfigured project.
    """
    try:
        dvc_config = ensure_dvc_ready(project_dir)
    except DvcNotConfiguredError:
        return None

    url = dvc_config.get("url") or dvc_config.get("remote_path") or ""
    return layout.remote_path_from_url(url, project_dir)


def ensure_dvc_ready(project_dir: Path) -> dict:
    """Validate that DVC is configured for multi-backend storage.

    Checks prerequisites:
    1. The [dvc] section exists in .wfc/wf-canvas.toml and has a ``url`` field.
    2. A local-FS ``url`` resolves outside the project tree (prune-safety;
       see :func:`_validate_remote_containment`).
    3. ``.dvc/config`` declares at least one remote (any URL scheme).

    Any DVC-native backend is accepted (file://, s3://, ssh://, gs://,
    azure://, ...); ``remote_type`` is not checked.  Warns (does not error)
    on drift between ``[dvc] url`` and ``.dvc/config``.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Dict with the parsed DVC config (url, auto_init, plus any legacy keys).

    Raises:
        DvcNotConfiguredError: If [dvc] section is missing, ``url`` is unset,
            a local-FS url resolves inside the project tree, or
            ``.dvc/config`` declares no remotes.
    """
    project_dir = Path(project_dir).resolve()

    # 1. Check [dvc] config section exists in wf-canvas.toml
    from ..persistence import read_config
    config = read_config(project_dir)
    dvc_config = config.get("dvc")
    if dvc_config is None:
        raise DvcNotConfiguredError(
            "No [dvc] section found in .wfc/wf-canvas.toml. "
            "Add a [dvc] section with a `url` field to enable provenance."
        )

    # 2. Validate `url` is set (the legacy `remote_path` is the fallback)
    url = dvc_config.get("url") or dvc_config.get("remote_path")
    if not url:
        raise DvcNotConfiguredError(
            "No `url` field in [dvc] config. "
            "Set `url = \"<scheme>://...\"` (e.g. file:///path, s3://bucket, ssh://host/path)."
        )

    # 2b. Reject local-FS archives inside the project tree (prune-safety
    # footgun; also covers hand-edits made after `wfc init`).
    _validate_remote_containment(url, project_dir)

    # 3. Verify .dvc/config has at least one remote.  Cheap INI parse via
    # has_remote_configured (no DVC import).
    from .transport import has_remote_configured
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
        parser.read(layout.dvc_config_path(project_dir))
        # Locate the default-remote name from [core] remote = ...
        default_name = None
        if parser.has_option("core", "remote"):
            default_name = parser.get("core", "remote")
        # Look up the URL for that remote
        if default_name:
            from .transport import remote_sections
            section_name = remote_sections(parser).get(default_name)
            if section_name and parser.has_option(section_name, "url"):
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


#: The ``.dvc/.gitignore`` that :func:`init_dvc` writes.
DVC_GITIGNORE = "/config.local\n/tmp\n/cache\n"


def init_dvc(project_dir: Path, dvc_config: dict) -> None:
    """Initialize DVC cache structure and mirror remote URL to .dvc/config.

    Creates the .dvc/cache/files/md5/ directory tree, writes
    ``.dvc/.gitignore`` (``/config.local``, ``/tmp``, ``/cache``) when it is
    absent, writes ``.dvc/config``
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
        DvcNotConfiguredError: If ``url`` is empty, or a local-FS url
            resolves inside the project tree.
    """
    project_dir = Path(project_dir).resolve()

    url = dvc_config.get("url") or dvc_config.get("remote_path", "")
    if not url:
        raise DvcNotConfiguredError(
            "No `url` specified in [dvc] config."
        )

    # Reject local-FS archives inside the project tree BEFORE creating
    # anything (prune-safety footgun -- see _validate_remote_containment).
    _validate_remote_containment(url, project_dir)

    # Create .dvc/cache/ directory structure
    cache = _cache_dir(project_dir)
    cache.mkdir(parents=True, exist_ok=True)

    # Create .dvc/ marker (so other tools know DVC is initialized)
    dvc_dir = layout.dvc_dir(project_dir)
    dvc_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create local-FS remote directory (ssh://, s3://, ... are skipped).
    remote_path_obj = layout.remote_path_from_url(url, project_dir)
    if remote_path_obj is not None:
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
    from .transport import remote_sections
    remote_section = remote_sections(parser).get("default", 'remote "default"')
    if not parser.has_section(remote_section):
        parser.add_section(remote_section)
    parser.set(remote_section, "url", url)
    with open(config_path, "w", encoding="utf-8") as f:
        parser.write(f)

    # DVC's own ignore file: the local cache, its scratch space and the
    # credentials file (`config.local`) never enter version control. A file
    # the user already has is left as it is.
    dvc_ignore = dvc_dir / ".gitignore"
    if not dvc_ignore.exists():
        dvc_ignore.write_text(DVC_GITIGNORE, encoding="utf-8")


def check_remote_reachable(project_dir: Path) -> tuple[bool, str]:
    """Check whether the configured DVC remote is reachable.

    A local-FS remote is checked on disk: its directory exists and is a
    directory.  A non-local remote (S3, SSH, GCS, Azure, ...) is asked
    through DVC (:func:`wfc.storage.transport.probe_remote`), and a failure
    names the remote.  Returns (True, "") if reachable, or (False, reason)
    if not.

    Args:
        project_dir: Root directory of the wfc project.

    Returns:
        Tuple of (reachable, reason).  ``reason`` is empty when reachable.
    """
    try:
        ensure_dvc_ready(project_dir)
    except DvcNotConfiguredError as exc:
        return False, f"DVC remote not configured: {exc}"
    remote = _remote_path(project_dir)
    if remote is None:
        from .transport import probe_remote
        return probe_remote(project_dir)
    if not remote.exists():
        return False, f"DVC remote path does not exist: {remote}"
    if not remote.is_dir():
        return False, f"DVC remote path is not a directory: {remote}"
    return True, ""
