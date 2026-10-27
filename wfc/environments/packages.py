r"""Pure package-list parser for registered env contents.

A registered pixi, conda or uv env's image has a captures folder whose
``packages.lock`` holds its package list, the bytes the record's
``source_fingerprint`` is the md5 of (written by
:func:`wfc.environments.register`, read back verified by
:func:`wfc.environments.verified_package_list`). That list is

    <lock-or-explicit-list content><PIP_FREEZE_DELIMITER><pip-freeze content>

(for uv, the exported ``requirements.txt`` and an empty pip section).
This module turns that text into a normalized, sorted, source-tagged
package list so the canvas can render "what packages are in this env" without
any disk I/O or subprocess — the parser is **pure** (text + backend in, list
out).

The delimiter is the single cross-module contract: :func:`wfc.environments.register`
joins on :data:`PIP_FREEZE_DELIMITER` and :func:`parse_packages` splits on it.
A full ``pixi.lock`` is multi-line YAML, so a single-``\n`` join would be
ambiguous — this sentinel line cannot occur inside a lock file, an explicit
list, or pip-freeze output.
"""

from __future__ import annotations

# =============================================================================
# Cross-module blob contract
# =============================================================================

# Sentinel separating the lock / explicit-list section from the pip-freeze
# section in a captures folder's ``packages.lock``.  Assembled by
# ``wfc.environments.register`` and split here.  Chosen to be impossible to occur
# inside real pixi.lock YAML, a ``conda list --explicit`` listing, or
# ``pip freeze`` output, so ``str.partition`` on it is unambiguous.
PIP_FREEZE_DELIMITER = "\n===WFC-PIP-FREEZE-SECTION===\n"


def parse_packages(blob: str, backend: str, *,
                   python_minor: str | None = None,
                   env_name: str | None = None) -> list[dict]:
    """Parse a package list (``packages.lock``) into normalized packages.

    Pure function: no disk access, no subprocess.  Splits *blob* on
    :data:`PIP_FREEZE_DELIMITER` into a lock/explicit-list section (parsed
    per *backend*) and a pip-freeze section (always parsed as
    ``name==version``).  Entries are de-duplicated by case-insensitive name
    — a pip entry that duplicates a conda/pixi name wins, because pip installs
    last (``--no-deps``) and reflects the actual on-disk version.

    Args:
        backend: A backend name.  Its backend-table row's ``parse_lock``
            reads the lock section.  A backend with no parser, or any
            other value, yields an empty lock section (only pip entries,
            if any, are returned).
        blob: The package list text, as
            :func:`wfc.environments.verified_package_list` returns it.
        python_minor: The image's Python minor, as its ``image.json``
            records it (uv only). A uv lock section lists the lines whose
            environment marker holds on Linux x86_64 with that CPython;
            ``None`` evaluates no marker and lists every line.
        env_name: The env's name, which is the pixi environment its image
            installed (pixi only). A pixi lock section lists that
            environment's linux-64 packages; ``None`` lists every package
            in the lock.

    Returns:
        A list of ``{"name": str, "version": str, "source": str}`` dicts,
        sorted case-insensitively by ``name``.  ``source`` is one of
        ``"conda"``, ``"pixi"``, ``"uv"``, ``"pip"``.  Empty list when the blob carries
        no recognizable packages.
    """
    lock_part, _sep, pip_part = blob.partition(PIP_FREEZE_DELIMITER)

    from .backends import BACKENDS

    row = BACKENDS.get(backend)
    parse_lock = row.parse_lock if row is not None else None
    lock_pkgs = (parse_lock(lock_part, python_minor, env_name)
                 if parse_lock is not None else [])

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
            # URL form. Drop the optional ``#<md5>`` fragment.
            named = _conda_url_name_version(line.split("#", 1)[0])
            if named is not None:
                out.append({"name": named[0], "version": named[1], "source": "conda"})
            continue

        if "=" in line:
            # ``name=version=build`` form.
            parts = line.split("=")
            name = parts[0].strip()
            version = parts[1].strip() if len(parts) > 1 else ""
            if name:
                out.append({"name": name, "version": version, "source": "conda"})
    return out


def _conda_url_name_version(url: str) -> tuple[str, str] | None:
    """Return the ``(name, version)`` a conda package URL's file name encodes.

    The basename is ``<name>-<version>-<build>.conda`` (or ``.tar.bz2``):
    the archive extension is stripped and the rest split on its last two
    ``-``. ``None`` when no name can be read.
    """
    filename = url.rsplit("/", 1)[-1]
    for ext in (".conda", ".tar.bz2"):
        if filename.endswith(ext):
            filename = filename[: -len(ext)]
            break
    parts = filename.rsplit("-", 2)
    if len(parts) >= 2 and parts[0]:
        return parts[0], parts[1]
    return None


def _parse_pixi_lock(text: str, env_name: str | None = None) -> list[dict]:
    """Parse a full ``pixi.lock`` YAML string into package dicts.

    Reads the lock **from the string** — it does NOT glob the filesystem.
    With *env_name*, the packages are that environment's linux-64 entries
    (the image is linux-64), each looked up by URL in the top-level
    ``packages:`` list; a lock whose ``environments`` block does not define
    the env, or gives it no linux-64 entries, lists nothing. Without
    *env_name*, or for a lock with no ``environments`` block, every
    top-level entry is listed. A conda entry with no ``name``/``version``
    (lock format 6 records only the URL) is named from its file name. Every
    entry is tagged ``source="pixi"``.
    """
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []

    records = [pkg for pkg in data.get("packages") or [] if isinstance(pkg, dict)]
    environments = data.get("environments")
    if env_name is not None and isinstance(environments, dict):
        env = environments.get(env_name)
        platforms = env.get("packages") if isinstance(env, dict) else None
        entries = platforms.get("linux-64") if isinstance(platforms, dict) else None
        by_url = {pkg.get("conda") or pkg.get("pypi"): pkg for pkg in records}
        records = [by_url.get(entry.get("conda") or entry.get("pypi"), entry)
                   for entry in entries or [] if isinstance(entry, dict)]

    out: list[dict] = []
    for pkg in records:
        name, version = pkg.get("name"), pkg.get("version")
        if not name and pkg.get("conda"):
            name, version = _conda_url_name_version(str(pkg["conda"])) or (None, None)
        if not name:
            continue
        out.append({
            "name": str(name),
            "version": "" if version is None else str(version),
            "source": "pixi",
        })
    return out


def _parse_uv_requirements(text: str, python_minor: str | None = None) -> list[dict]:
    r"""Parse ``uv export``'s requirements-txt output into package dicts.

    Each requirement is a ``name==version`` line, possibly followed by an
    environment marker (``; sys_platform == 'win32'``) and a trailing
    ``\\``; its ``--hash=...`` continuation lines, comments (``# via ...``)
    and blanks are skipped. With *python_minor*, a line is kept only when
    its marker holds in the image (:func:`wfc.environments.uv.target_environment`),
    as ``uv pip install`` keeps it: a platform-only line is dropped, and of
    a name the export forks by Python version only the minor's line stays.
    Entries are tagged ``source="uv"``.

    Args:
        text: The lock section: the exported requirements.
        python_minor: The image's Python minor; ``None`` keeps every line.

    Returns:
        One ``{"name", "version", "source": "uv"}`` dict per kept line.
    """
    from .uv import marker_applies, target_environment

    environment = target_environment(python_minor) if python_minor else None
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")) or "==" not in line:
            continue
        requirement, _, marker = line.rstrip("\\").partition(";")
        if environment is not None and not marker_applies(marker.strip(), environment):
            continue
        requirement = requirement.strip()
        name, _, version = requirement.partition("==")
        name = name.split("[", 1)[0].strip()
        version = version.split()[0] if version.split() else ""
        if name:
            out.append({"name": name, "version": version, "source": "uv"})
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
