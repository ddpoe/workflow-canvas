"""Code fingerprint -- what code is this method.

Two rules. Script discovery: every recognized script under the directory,
recursively, deduplicated, sorted by relative POSIX path. The digest: every
file under the directory but its top-level ``method.yaml`` (which enters as
the contract projection), sorted by relative POSIX path; SHA256 over
``relpath:`` and the file's content for each in that order -- a text file's
content with its line endings normalised to LF, any other file's raw bytes
-- then ``contract:`` and the projection. The directory is the caller's --
nothing here decides which directory is a method's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The single source of truth for which file extensions count as method
# scripts. Registration discovery (wfc.registration.register_method) and the
# permissive-mode snapshot copy use this set to find a method's scripts; the
# code fingerprint covers every file of the copy, scripts or not.
# Both `.R` and `.r` are accepted (Windows filesystems are case-insensitive;
# CRAN convention is `.R`).
RECOGNIZED_SCRIPT_EXTENSIONS: tuple[str, ...] = (".py", ".R", ".r", ".sh")

#: The method's contract file. It is not hashed as a file: its contract
#: reaches the fingerprint as the rendered projection, so a description edit
#: or a YAML reorder leaves the fingerprint where it was.
CONTRACT_FILENAME = "method.yaml"


def collect_method_scripts(source_dir: Path | str) -> list[Path]:
    """Collect all recognized method-script files under a directory.

    Recursively globs each extension in :data:`RECOGNIZED_SCRIPT_EXTENSIONS`,
    dedupes via a set (on case-insensitive filesystems ``rglob("*.R")`` and
    ``rglob("*.r")`` return the same files -- a plain concatenation would
    double-count them), and sorts by relative POSIX path. The sort is
    load-bearing for fingerprint determinism.

    Args:
        source_dir: Directory to scan (typically a method source dir or the
            registered snapshot under ``.wfc/registry/methods/<module>/<method>/``).

    Returns:
        Sorted list of absolute paths to recognized script files.
    """
    source_dir = Path(source_dir)
    found: set[Path] = set()
    for ext in RECOGNIZED_SCRIPT_EXTENSIONS:
        found.update(p for p in source_dir.rglob(f"*{ext}") if p.is_file())
    return sorted(found, key=lambda p: p.relative_to(source_dir).as_posix())


def collect_fingerprinted_files(source_dir: Path | str) -> list[Path]:
    """Collect the files the code fingerprint digests.

    Every file under the directory, recursively, of any type, except the
    top-level ``method.yaml`` (the contract enters as its projection). A
    ``method.yaml`` in a subdirectory is an ordinary file. Directories are
    not files, so an empty directory adds nothing. Sorted by relative POSIX
    path, which is load-bearing for determinism.

    Args:
        source_dir: The directory to fingerprint (a registered snapshot or
            an extracted copy of one).

    Returns:
        Sorted list of absolute paths to the fingerprinted files.
    """
    source_dir = Path(source_dir)
    files = (p for p in source_dir.rglob("*") if p.is_file())
    return sorted(
        (p for p in files
         if p.relative_to(source_dir).as_posix() != CONTRACT_FILENAME),
        key=lambda p: p.relative_to(source_dir).as_posix(),
    )


def fingerprinted_file_digests(source_dir: Path | str) -> dict[str, str]:
    """Return one digest per fingerprinted file, as the fingerprint reads it.

    The files are :func:`collect_fingerprinted_files`'s, and each digest is
    the SHA256 of the bytes the fingerprint hashes for that file (text with
    its line endings normalised to LF), so two copies whose file digests
    agree contribute the same files to their fingerprints, whatever line
    endings their checkouts wrote.

    Args:
        source_dir: A registered snapshot, an extracted copy, or a staging
            copy of either.

    Returns:
        ``{relative POSIX path: hex digest}``.
    """
    source_dir = Path(source_dir)
    return {
        path.relative_to(source_dir).as_posix():
            hashlib.sha256(_fingerprint_content(path)).hexdigest()
        for path in collect_fingerprinted_files(source_dir)
    }


def _fingerprint_content(path: Path) -> bytes:
    """Return the bytes a file contributes to the fingerprint after its relpath.

    A file is text when it decodes as UTF-8 and holds no NUL byte; its
    content is then the decoded text with CRLF and lone CR normalised to LF
    (what ``Path.read_text`` hands back), re-encoded. Any other file
    contributes its raw bytes. So a checkout that converted line endings
    fingerprints as the copy that did not, while a binary file is hashed
    exactly as it is.

    Args:
        path: The file.

    Returns:
        The bytes to hash.
    """
    raw = path.read_bytes()
    if b"\x00" in raw:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def build_code_fingerprint(
    method_source_dir: Path | str,
    contract_projection: str | None,
) -> str:
    """Build a SHA256 fingerprint of a method's files and its contract.

    Walks the directory and digests every file but its top-level
    ``method.yaml`` (:func:`collect_fingerprinted_files`), in relative-path
    order: scripts, helpers of any type, data files a script reads. A text
    file is hashed with its line endings normalised to LF, any other file as
    raw bytes (:func:`_fingerprint_content`), so the staging snapshot
    registration fingerprints and a ``git archive`` extraction of the
    committed tree agree on a checkout that converts line endings. The
    method's declared contract is folded in last, and the SHA256 hex digest
    returned.

    The contract is part of a method's code identity: an output slot's name
    and ``type`` decide the filename a run writes, and the rest of the
    declaration (slots, params, executor) is what the canvas, the validator
    and the claim read for the version, so two different contracts are two
    versions. It arrives here already rendered, because this package
    imports no ``wfc`` module and so cannot parse ``method.yaml`` itself --
    ``wfc.contracts.render_contract_projection`` is the one renderer, and it
    leaves out descriptions, formatting, ``env`` and ``helpers``, so a
    description edit or a YAML reorder leaves every key where it was.

    This function is the content-addressed replacement for using git_commit
    as the code identity component in cache keys.  The directory should be
    the registered snapshot under ``.wfc/registry/methods/<module>/<method>/``
    (or an extracted copy of a version's tree), and the projection should
    come from that same copy's ``method.yaml`` -- not from the database row,
    which a refused registration can leave out of step with the snapshot.

    Args:
        method_source_dir: Path to the directory containing the method's
            registered files.
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
    for path in collect_fingerprinted_files(source_dir):
        rel_path = path.relative_to(source_dir).as_posix()
        hasher.update(f"{rel_path}:".encode() + _fingerprint_content(path))
    hasher.update(f"contract:{contract_projection}".encode())

    return hasher.hexdigest()


#: Domain-separation prefix of the version identity: no other digest in wfc
#: hashes this string, so an identity never equals a fingerprint or a key.
VERSION_IDENTITY_DOMAIN = "wfc-method-version-identity-1"


def build_version_identity(code_fingerprint: str, env_fingerprint: str) -> str:
    """Return a method version's content identity: its code and its env.

    SHA256 over the compact JSON list ``[VERSION_IDENTITY_DOMAIN,
    code_fingerprint, env_fingerprint]``. Two versions with the same code in
    the same env have the same identity whatever their labels, so the
    identity, not the label, is what a pin names: a renumbering after a
    merge conflict never moves it.

    Args:
        code_fingerprint: The version's code fingerprint.
        env_fingerprint: The version's env fingerprint.

    Returns:
        64-char hex SHA256 string.
    """
    raw = json.dumps([VERSION_IDENTITY_DOMAIN, code_fingerprint, env_fingerprint],
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
