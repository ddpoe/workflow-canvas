"""
wfc/env_introspect.py — Pure helpers for capturing environment content blobs.

Three public helpers consumed by ``capture_env_content`` in ``wfc/version.py``:

  pixi_lock_section(pixi_root, env_name, platform) -> str
      Locate ``{pixi_root}/{env_name}-*/pixi.lock``, parse its YAML,
      select the environment entry for ``platform`` from the ``environments``
      block, and re-emit SEMANTIC fields only as sorted JSON.

  conda_list_explicit(env_path) -> str
      Shell out to ``conda list --explicit --md5 --prefix <env_path>`` and
      return stdout.  Install-based (actuality).

  pip_freeze(env_python) -> str
      Shell out to ``<env_python> -m pip freeze --disable-pip-version-check``
      and return stdout.  Raises cleanly on nonzero exit / missing binary.

Design notes:

- **Lock-vs-installed framing (by design, do not normalize):**
    - pixi fingerprint -> lock-based (intent) from pixi.lock
    - conda fingerprint -> install-based (actuality) from ``conda list --explicit --md5``
    - ``pip_freeze`` is appended on ALL backends as a common actuality check
      (handled by the caller in ``capture_env_content``).

- **Stability under cosmetic churn:** ``pixi_lock_section`` emits SEMANTIC
  fields only (``name``, ``version``, ``build``, ``hash``, ``platform``/
  ``subdir``) as SORTED JSON.  YAML round-trip would leak pyyaml/pixi
  formatting bumps into the fingerprint; JSON with ``sort_keys=True`` keeps
  env_fingerprint stable across tool upgrades that do not change package
  content.

- **Platform selection:** the current platform MUST appear in the lock's
  ``environments`` block.  Missing platform is a hard error (not a silent
  fallback to "all platforms") — a lock that does not cover the current
  platform cannot honestly fingerprint what will be installed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


# =============================================================================
# Semantic fields for pixi.lock packages
# =============================================================================

# Fields we consider "semantic" — every other field (url, source, depends,
# timestamp, etc.) is treated as cosmetic churn and excluded from the hash.
# Rationale: a package's identity for cache-invalidation purposes is fully
# determined by name + version + build + hash + platform.
_PIXI_SEMANTIC_FIELDS = ("name", "version", "build", "hash", "platform", "subdir")


def _filter_semantic_fields(pkg: dict) -> dict:
    """Return a dict containing only the semantic fields present on ``pkg``."""
    return {k: pkg[k] for k in _PIXI_SEMANTIC_FIELDS if k in pkg}


# =============================================================================
# pixi.lock
# =============================================================================

def pixi_lock_section(
    pixi_root: str | Path,
    project_name: str,
    platform: str | None = None,
    *,
    env_name: str | None = None,
) -> str:
    """Extract the semantic slice of a pixi.lock for one env+platform.

    Globs ``{pixi_root}/{project_name}-*/pixi.lock`` (single match expected,
    mirroring ``_resolve_pixi_standalone``), parses YAML, and reads the
    ``environments`` block to find the entries for ``platform``.  For each
    package, only the semantic fields are retained; output is a sorted
    JSON string so that cosmetic lock churn does not change the fingerprint.

    Env selection:

    * ``env_name`` given — look up ``environments[env_name]`` directly.
      If missing, raise ``KeyError`` listing the lock's actual env keys.
      Fallbacks to "default" / single-env are NOT applied: an explicit
      env name means the caller knows what they want.
    * ``env_name`` omitted (2-segment ``pixi:<name>`` form) — prefer
      ``environments["default"]``; if absent and the lock has exactly one
      env, take it; otherwise raise.

    Platform selection is **lock-driven**.  When ``platform`` is ``None``
    (the default), the platform list is read directly from the target env's
    ``packages`` block:

    * Single-platform lock (typical for pixi standalone envs): use the
      one listed platform — no ambiguity, no mapping needed.
    * Multi-platform lock: raise ``ValueError`` pointing the caller at
      the explicit ``platform=`` argument.  We do NOT map from
      ``sys.platform`` to a conda platform tag; pixi's own resolution
      is the source of truth for that, and a homegrown mapping would
      silently produce a wrong fingerprint on an unexpected platform.

    Args:
        pixi_root: Absolute path to the pixi project root directory
            (the parent containing ``{project_name}-*/``).
        project_name: Name of the pixi project dir (e.g. ``"image-io"``,
            ``"wcia"``).  Used only for the directory glob.
        platform: Optional platform key (e.g. ``"win-64"``, ``"linux-64"``).
            When omitted, the platform is auto-detected from the lock.
            Must match one of the keys in the lock's ``environments`` block
            when provided.
        env_name: Optional exact env key inside the lock's ``environments``
            block.  Required for pixi projects with multiple envs where
            project name and env name differ.  When omitted, falls back
            to ``"default"`` or the sole env.

    Returns:
        A sorted-JSON string of the package list for this env+platform.
        Stable under cosmetic churn (field reordering, YAML formatting).

    Raises:
        FileNotFoundError: If no pixi.lock matches the glob, or more than
            one directory matches the pattern.
        KeyError: If ``env_name`` is explicitly provided and the lock does
            not contain it, or if ``platform`` is explicitly provided and
            the lock does not contain it for the env.
        ValueError: If the lock cannot be parsed as YAML, or if the lock
            lists multiple platforms and no ``platform`` override is passed.
    """
    import yaml

    pixi_root = Path(pixi_root)
    pattern = f"{project_name}-*/pixi.lock"
    matches = sorted(pixi_root.glob(pattern))

    if len(matches) == 0:
        raise FileNotFoundError(
            f"No pixi.lock found for project '{project_name}'. "
            f"Searched: {pixi_root / pattern}"
        )
    if len(matches) > 1:
        listing = "\n  ".join(str(m) for m in matches)
        raise FileNotFoundError(
            f"Multiple pixi.lock files match project '{project_name}':\n  {listing}\n"
            f"Remove duplicates so only one {project_name}-* directory exists "
            f"under {pixi_root}."
        )

    lock_path = matches[0]
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            lock = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Could not parse {lock_path} as YAML: {e}") from e

    if not isinstance(lock, dict):
        raise ValueError(f"pixi.lock at {lock_path} is not a mapping")

    environments = lock.get("environments") or {}
    # pixi v0.x places package listings per-env, keyed by platform.
    # Format: environments: {<env>: {channels: [...], packages: {<platform>: [{conda: ...}, ...]}}}
    if env_name is not None:
        # Explicit env: no fallbacks.  If it's missing, that's the user's bug
        # to fix (typo in env_spec, stale lock) — name the actual keys so they
        # can see the mismatch.
        if env_name not in environments:
            raise KeyError(
                f"pixi.lock at {lock_path} has no env named '{env_name}'. "
                f"Available envs: {sorted(environments.keys())}. "
                f"Check the env name in your method's 'env:' field, or "
                f"run `pixi install` in the pixi project source to "
                f"regenerate the lock."
            )
        target_env = environments[env_name]
        resolved_env_label = env_name
    elif "default" in environments:
        target_env = environments["default"]
        resolved_env_label = "default"
    elif len(environments) == 1:
        # Single-env standalone project — take what's there.
        resolved_env_label, target_env = next(iter(environments.items()))
    else:
        raise KeyError(
            f"pixi.lock at {lock_path} has multiple environments "
            f"({sorted(environments.keys())}) and no 'default' — "
            f"pass env_name= to select one, or use the 3-segment "
            f"'pixi:<project>:<env>' form in your method's env: field."
        )

    packages_by_platform = (target_env or {}).get("packages") or {}

    # Lock-driven platform selection.  We never map sys.platform -> conda
    # platform tag ourselves — pixi's own resolution is the source of truth.
    if platform is None:
        available = sorted(packages_by_platform.keys())
        if len(available) == 0:
            raise KeyError(
                f"pixi.lock at {lock_path} lists no platforms for env "
                f"'{resolved_env_label}'.  Run `pixi install` in the pixi "
                f"project source to regenerate the lock."
            )
        if len(available) > 1:
            raise ValueError(
                f"pixi.lock at {lock_path} lists multiple platforms "
                f"({available}) for env '{resolved_env_label}'; platform is "
                f"ambiguous.  Pass an explicit platform= argument (the one "
                f"you intend to run on), or restrict the pixi project to a "
                f"single platform."
            )
        platform = available[0]
    elif platform not in packages_by_platform:
        raise KeyError(
            f"Platform '{platform}' not found in pixi.lock at {lock_path} "
            f"for env '{resolved_env_label}'. Available platforms: "
            f"{sorted(packages_by_platform.keys())}. If you expected "
            f"'{platform}' to be present, run `pixi install` in the pixi "
            f"project source to regenerate the lock for this platform."
        )

    raw_entries = packages_by_platform[platform] or []

    # Each entry in raw_entries is typically ``{"conda": "url"}`` or
    # ``{"pypi": "url"}`` — the actual package metadata lives in the
    # top-level ``packages`` list keyed by URL/name.  We need to resolve
    # each reference to its full semantic record.
    top_packages = lock.get("packages") or []
    by_key: dict[str, dict] = {}
    for pkg in top_packages:
        if not isinstance(pkg, dict):
            continue
        # Keys we may match on: url, name, path — try url first (most stable
        # in pixi v0.x), fall back to name.
        for key in ("url", "path", "name"):
            val = pkg.get(key)
            if val:
                by_key[str(val)] = pkg

    resolved: list[dict] = []
    for ref in raw_entries:
        if isinstance(ref, dict):
            # e.g. {"conda": "https://.../foo-1.0-bz2"}
            ref_val = next(iter(ref.values())) if ref else None
            if ref_val and str(ref_val) in by_key:
                resolved.append(_filter_semantic_fields(by_key[str(ref_val)]))
            else:
                # Keep the reference as-is if we cannot resolve it — semantic
                # fields filter will still drop cosmetic URL noise.
                resolved.append(_filter_semantic_fields(ref))
        elif isinstance(ref, str):
            if ref in by_key:
                resolved.append(_filter_semantic_fields(by_key[ref]))

    # Sort by (name, version, build) for order-stability across lock writes.
    resolved.sort(key=lambda p: (
        p.get("name", ""),
        p.get("version", ""),
        p.get("build", ""),
    ))

    return json.dumps(resolved, sort_keys=True, separators=(",", ":"))


# =============================================================================
# conda list --explicit
# =============================================================================

def conda_list_explicit(env_path: str | Path) -> str:
    """Return the output of ``conda list --explicit --md5 --prefix <env_path>``.

    Install-based (actuality) fingerprint.  The ``--md5`` flag includes the
    content MD5 of each installed package, yielding a strong identity string
    that does not depend on lock files.

    Args:
        env_path: Absolute path to a conda environment prefix
            (the directory containing ``conda-meta/``).

    Returns:
        Raw stdout of ``conda list --explicit --md5``.

    Raises:
        FileNotFoundError: If ``conda`` is not on PATH.
        RuntimeError: If conda exits nonzero.
    """
    env_path = Path(env_path)
    try:
        result = subprocess.run(
            ["conda", "list", "--explicit", "--md5", "--prefix", str(env_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            "conda executable not found on PATH — cannot fingerprint "
            f"env at {env_path}"
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"conda list --explicit --md5 --prefix {env_path} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# =============================================================================
# pip freeze
# =============================================================================

def pip_freeze(env_python: str | Path) -> str:
    """Return the output of ``<env_python> -m pip freeze``.

    Raises a clear error on nonzero exit or missing binary so that env
    fingerprinting fails loudly rather than silently producing an empty
    freeze (which would look identical for two different envs).

    Args:
        env_python: Absolute path to the python interpreter to introspect.

    Returns:
        Raw stdout of ``pip freeze``.

    Raises:
        FileNotFoundError: If ``env_python`` is not an executable file.
        RuntimeError: If pip exits nonzero.
    """
    env_python = Path(env_python)
    try:
        result = subprocess.run(
            [str(env_python), "-m", "pip", "freeze", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Python interpreter not found at {env_python} — cannot run pip freeze"
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"'{env_python} -m pip freeze' failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# Sentinel emitted in place of `pip freeze` output when pip is provably
# absent from the target interpreter (pixi/conda envs assembled entirely
# from conda-forge, say). The sentinel must be distinct from any real pip
# freeze output and from an empty-pip-freeze-with-pip-present result so
# the env_fingerprint still encodes the fact that pip was missing.
PIP_MISSING_SENTINEL = "pip-freeze: unavailable (pip not installed)\n"


def pip_freeze_best_effort(env_python: str | Path) -> str:
    """Like :func:`pip_freeze`, but tolerate a missing pip module.

    Returns :data:`PIP_MISSING_SENTINEL` when `<env_python> -m pip` fails
    specifically because pip is not installed in the target env. All
    other failures (nonzero exit with a different error, missing
    interpreter, etc.) still raise — those are not "pip is absent" and
    silently swallowing them would hide real env problems.

    Intended for pixi/conda backends where the lock (pixi.lock) or
    explicit install list (conda list --explicit) is already the
    authoritative fingerprint and pip freeze only catches pip-installed
    drift on top.
    """
    env_python = Path(env_python)
    try:
        result = subprocess.run(
            [str(env_python), "-m", "pip", "freeze", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Python interpreter not found at {env_python} — cannot run pip freeze"
        ) from e

    if result.returncode == 0:
        return result.stdout

    # `python -m pip` with pip absent prints `No module named pip` to
    # stderr and exits nonzero. That signature is specific enough to
    # distinguish missing-pip from "pip crashed for some other reason";
    # the latter must still raise.
    if "No module named pip" in (result.stderr or ""):
        return PIP_MISSING_SENTINEL

    raise RuntimeError(
        f"'{env_python} -m pip freeze' failed "
        f"(exit {result.returncode}): {result.stderr.strip()}"
    )
