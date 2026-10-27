"""Identity -- the Tier 0 fingerprint primitive.

It answers what these bytes are, what code a method is, what a run
consumed, and what a run's identity is.

Five families, each a function from values to a hex digest. Documented by
``docs/system/identity.json``.

The package is pure: the standard library and the ``axiom_annotations``
envelope only -- no ``wfc`` import, no database, no DVC, no Docker, no
subprocess, no network, no writes. It opens files to hash them and for
nothing else. The input fingerprint takes the resolved identities as values;
the row reads stay with the caller that has the ids (Execution's claim
phase -- ``wfc.execution.claim.input_fingerprint_from_rows`` beside ``pre_run``).

Family modules:

- ``content_hash`` -- ``hash_file``, ``hash_directory``, ``hash_path``, the
  directory manifest (``directory_manifest``, ``DirectoryManifest``,
  ``manifest_bytes``, ``parse_manifest_bytes``, ``is_directory_hash``), the stat-only
  listing that shares its walk (``directory_listing``, ``DirectoryListing``),
  the incremental file hash (``content_hasher``) and the refusal
  ``DirectoryContentError``.
- ``code_fingerprint`` -- ``RECOGNIZED_SCRIPT_EXTENSIONS``,
  ``collect_method_scripts`` (script discovery),
  ``collect_fingerprinted_files`` (every file but ``method.yaml``),
  ``fingerprinted_file_digests`` (one digest per such file, as the
  fingerprint reads it), ``build_code_fingerprint``.
- ``input_fingerprint`` -- the part alphabet (``UpstreamRunIdentity``,
  ``SampleIdentity``, ``render_run_part``, ``render_sample_part``,
  ``render_input_parts``), the digest (``digest_input_parts``) and their
  composition ``build_input_fingerprint``.
- ``cache_key`` -- ``KEY_RECIPE`` and ``build_cache_key``.
- ``canonical`` -- ``canonical_json_bytes`` (one byte string per JSON value:
  sorted keys, compact separators, UTF-8, NaN and infinities refused) and
  ``canonical_json_hash`` (the md5 of those bytes, a document's hash).
"""

from __future__ import annotations

from .cache_key import KEY_RECIPE, build_cache_key
from .canonical import canonical_json_bytes, canonical_json_hash
from .code_fingerprint import (
    RECOGNIZED_SCRIPT_EXTENSIONS,
    build_code_fingerprint,
    build_version_identity,
    collect_fingerprinted_files,
    collect_method_scripts,
    fingerprinted_file_digests,
)
from .content_hash import (
    DIR_HASH_SUFFIX,
    DirectoryContentError,
    DirectoryListing,
    DirectoryManifest,
    check_case_collisions,
    content_hasher,
    directory_listing,
    directory_manifest,
    hash_directory,
    hash_file,
    hash_path,
    is_directory_hash,
    manifest_bytes,
    manifest_hash,
    parse_manifest_bytes,
)
from .input_fingerprint import (
    SampleIdentity,
    UpstreamRunIdentity,
    build_input_fingerprint,
    digest_input_parts,
    render_input_parts,
    render_run_part,
    render_sample_part,
)

__all__ = [
    "DIR_HASH_SUFFIX",
    "DirectoryContentError",
    "DirectoryListing",
    "DirectoryManifest",
    "KEY_RECIPE",
    "check_case_collisions",
    "content_hasher",
    "directory_listing",
    "directory_manifest",
    "is_directory_hash",
    "manifest_bytes",
    "manifest_hash",
    "parse_manifest_bytes",
    "RECOGNIZED_SCRIPT_EXTENSIONS",
    "SampleIdentity",
    "UpstreamRunIdentity",
    "build_cache_key",
    "build_code_fingerprint",
    "build_input_fingerprint",
    "build_version_identity",
    "canonical_json_bytes",
    "canonical_json_hash",
    "collect_fingerprinted_files",
    "collect_method_scripts",
    "fingerprinted_file_digests",
    "digest_input_parts",
    "hash_directory",
    "hash_file",
    "hash_path",
    "render_input_parts",
    "render_run_part",
    "render_sample_part",
]
