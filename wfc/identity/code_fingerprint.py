"""Code fingerprint -- what code is this method.

The selection rule, then the digest: every recognized script under the
directory, recursively, deduplicated, sorted by relative POSIX path; SHA256
over ``relpath:content`` for each in that order. The directory is the
caller's -- nothing here decides which directory is a method's.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The single source of truth for which file extensions count as method
# scripts. Registration discovery (wfc.registration.register_method), the
# registration snapshot copy (step 8), and build_code_fingerprint all use
# this set -- snapshot and fingerprint MUST stay in lockstep, so neither may
# define its own list. Both `.R` and `.r` are accepted (Windows filesystems
# are case-insensitive; CRAN convention is `.R`).
RECOGNIZED_SCRIPT_EXTENSIONS: tuple[str, ...] = (".py", ".R", ".r", ".sh")


def collect_method_scripts(source_dir: Path | str) -> list[Path]:
    """Collect all recognized method-script files under a directory.

    Recursively globs each extension in :data:`RECOGNIZED_SCRIPT_EXTENSIONS`,
    dedupes via a set (on case-insensitive filesystems ``rglob("*.R")`` and
    ``rglob("*.r")`` return the same files -- a plain concatenation would
    double-count them), and sorts by relative POSIX path. The sort is
    load-bearing for fingerprint determinism.

    Args:
        source_dir: Directory to scan (typically a method source dir or the
            registered copy under ``methods/{method_name}/``).

    Returns:
        Sorted list of absolute paths to recognized script files.
    """
    source_dir = Path(source_dir)
    found: set[Path] = set()
    for ext in RECOGNIZED_SCRIPT_EXTENSIONS:
        found.update(p for p in source_dir.rglob(f"*{ext}") if p.is_file())
    return sorted(found, key=lambda p: p.relative_to(source_dir).as_posix())


def build_code_fingerprint(
    method_source_dir: Path | str,
    contract_projection: str | None,
) -> str:
    """Build a SHA256 fingerprint of a method's source files and its contract.

    Walks the method source directory, reads all recognized script files
    (see :data:`RECOGNIZED_SCRIPT_EXTENSIONS` -- ``.py``/``.R``/``.r``/``.sh``),
    sorts them by relative path (load-bearing for determinism), concatenates
    their contents, folds in the method's declared contract, and returns a
    SHA256 hex digest.

    The contract is part of a method's code identity: an output slot's name
    and ``type`` decide the filename a run writes, so a cached result computed
    under the previous contract is an artifact named for a promise the method
    no longer makes. It arrives here already rendered, because this package
    imports no ``wfc`` module and so cannot parse ``method.yaml`` itself --
    ``wfc.contracts.render_contract_projection`` is the one renderer, and it
    keeps only the computation-bearing fields, so a description edit or a
    YAML reorder leaves every key where it was.

    This function is the content-addressed replacement for using git_commit
    as the code identity component in cache keys.  The directory should be
    the registered copy under ``methods/{method_name}/``, and the projection
    should come from that same registered copy's ``method.yaml`` -- not from
    the database row, which a refused registration can leave out of step with
    the snapshot.

    Args:
        method_source_dir: Path to the directory containing the method's
            registered source files.
        contract_projection: The rendering of the method's declared contract
            from ``wfc.contracts.render_contract_projection``. ``None`` means
            the registered copy holds no ``method.yaml``, which is refused: a
            method with no contract has no identity to fingerprint, and a
            stand-in marker would give one silently.

    Returns:
        64-char hex SHA256 string.

    Raises:
        ValueError: If the directory does not exist, contains no recognized
            script files, or has no registered contract.
    """
    source_dir = Path(method_source_dir)
    if not source_dir.is_dir():
        raise ValueError(
            f"Method source directory does not exist: {source_dir}"
        )

    script_files = collect_method_scripts(source_dir)
    if not script_files:
        exts = "/".join(RECOGNIZED_SCRIPT_EXTENSIONS)
        raise ValueError(
            f"Method source directory contains no recognized script files "
            f"({exts}): {source_dir}"
        )

    if not contract_projection:
        raise ValueError(
            f"Method source directory has no registered contract to "
            f"fingerprint (method.yaml is missing from the registered copy): "
            f"{source_dir}. Re-register the method so its declared slots are "
            f"part of its identity."
        )

    hasher = hashlib.sha256()
    for script_file in script_files:
        rel_path = script_file.relative_to(source_dir).as_posix()
        content = script_file.read_text(encoding="utf-8")
        hasher.update(f"{rel_path}:{content}".encode())
    hasher.update(f"contract:{contract_projection}".encode())

    return hasher.hexdigest()
