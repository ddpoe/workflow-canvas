"""wfc/env_packages.py — Pure blob -> package-list parser (ADR-019 env contents).

A registered pixi/conda env stores a content-addressed ``source_fingerprint``
blob in the DVC cache (assembled by :func:`wfc.envs.register`). That blob is

    <lock-or-explicit-list content><PIP_FREEZE_DELIMITER><pip-freeze content>

This module turns that blob back into a normalized, sorted, source-tagged
package list so the canvas can render "what packages are in this env" without
any disk I/O or subprocess — the parser is **pure** (blob + backend in, list
out).

The delimiter is the single cross-module contract: :func:`wfc.envs.register`
joins on :data:`PIP_FREEZE_DELIMITER` and :func:`parse_packages` splits on it.
A full ``pixi.lock`` is multi-line YAML, so the old single-``\\n`` join is
ambiguous — this sentinel line cannot occur inside a lock file, an explicit
list, or pip-freeze output.
"""

from __future__ import annotations


# =============================================================================
# Cross-module blob contract
# =============================================================================

# Sentinel separating the lock / explicit-list section from the pip-freeze
# section in a ``source_fingerprint`` blob.  Assembled by
# ``wfc.envs.register`` and split here.  Chosen to be impossible to occur
# inside real pixi.lock YAML, a ``conda list --explicit`` listing, or
# ``pip freeze`` output, so ``str.partition`` on it is unambiguous.
PIP_FREEZE_DELIMITER = "\n===WFC-PIP-FREEZE-SECTION===\n"


def parse_packages(blob: str, backend: str) -> list[dict]:
    """Parse a ``source_fingerprint`` blob into a normalized package list.

    Pure function: no disk access, no subprocess.  Splits *blob* on
    :data:`PIP_FREEZE_DELIMITER` into a lock/explicit-list section (parsed
    per *backend*) and a pip-freeze section (always parsed as
    ``name==version``).  Entries are de-duplicated by case-insensitive name
    — a pip entry that duplicates a conda/pixi name wins, because pip installs
    last (``--no-deps``) and reflects the actual on-disk version.

    Args:
        backend: ``"pixi"`` or ``"conda"``.  Selects the lock-section parser.
            Any other value yields an empty lock section (only pip entries,
            if any, are returned).
        blob: The raw blob string read from the DVC content cache.

    Returns:
        A list of ``{"name": str, "version": str, "source": str}`` dicts,
        sorted case-insensitively by ``name``.  ``source`` is one of
        ``"conda"``, ``"pixi"``, ``"pip"``.  Empty list when the blob carries
        no recognizable packages.
    """
    lock_part, _sep, pip_part = blob.partition(PIP_FREEZE_DELIMITER)

    if backend == "conda":
        lock_pkgs = _parse_conda_explicit(lock_part)
    elif backend == "pixi":
        lock_pkgs = _parse_pixi_lock(lock_part)
    else:
        lock_pkgs = []

    # Dedup by lowercased name.  Lock entries are added first (first wins
    # among lock duplicates — e.g. multi-platform builds of the same name);
    # pip entries then override, since pip is the install-last actuality.
    by_name: dict[str, dict] = {}
    for pkg in lock_pkgs:
        by_name.setdefault(pkg["name"].lower(), pkg)
    for pkg in _parse_pip_freeze(pip_part):
        by_name[pkg["name"].lower()] = pkg

    return sorted(by_name.values(), key=lambda p: p["name"].lower())


# =============================================================================
# Per-section parsers
# =============================================================================

def _parse_conda_explicit(text: str) -> list[dict]:
    """Parse a ``conda list --explicit`` listing into package dicts.

    Handles both line shapes conda emits:

    * URL lines — ``https://.../<name>-<version>-<build>.conda#<md5>`` (or
      ``.tar.bz2``).  The ``#<md5>`` fragment, directory prefix, and archive
      extension are stripped, then the basename is split on the last two
      ``-`` to recover ``<name>``/``<version>``.
    * ``name=version=build`` lines (the ``=``-delimited spec form).

    Comment lines (``#``...), the ``@EXPLICIT`` marker, and blanks are skipped.
    """
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line == "@EXPLICIT":
            continue

        if "://" in line or line.endswith((".conda", ".tar.bz2")):
            # URL form. Drop the optional ``#<md5>`` fragment, take basename.
            url = line.split("#", 1)[0]
            filename = url.rsplit("/", 1)[-1]
            for ext in (".conda", ".tar.bz2"):
                if filename.endswith(ext):
                    filename = filename[: -len(ext)]
                    break
            parts = filename.rsplit("-", 2)
            if len(parts) >= 2 and parts[0]:
                out.append({"name": parts[0], "version": parts[1], "source": "conda"})
            continue

        if "=" in line:
            # ``name=version=build`` form.
            parts = line.split("=")
            name = parts[0].strip()
            version = parts[1].strip() if len(parts) > 1 else ""
            if name:
                out.append({"name": name, "version": version, "source": "conda"})
    return out


def _parse_pixi_lock(text: str) -> list[dict]:
    """Parse a full ``pixi.lock`` YAML string into package dicts.

    Mirrors the field extraction in
    :func:`wfc.env_introspect.pixi_lock_section` (name/version), but reads
    the lock **from the string** — it does NOT glob the filesystem.  Every
    entry in the top-level ``packages:`` list (conda or pypi) is tagged
    ``source="pixi"``.
    """
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []

    out: list[dict] = []
    for pkg in data.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        if not name:
            continue
        version = pkg.get("version")
        out.append({
            "name": str(name),
            "version": "" if version is None else str(version),
            "source": "pixi",
        })
    return out


def _parse_pip_freeze(text: str) -> list[dict]:
    """Parse ``pip freeze`` output (``name==version`` lines) into dicts.

    Lines without ``==`` (editable installs, VCS/URL refs, blanks, comments)
    are skipped — only pinned ``name==version`` entries are surfaced.  An
    empty section yields an empty list (no spurious entries).
    """
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        name = name.strip()
        version = version.strip()
        if name:
            out.append({"name": name, "version": version, "source": "pip"})
    return out
