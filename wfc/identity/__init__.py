"""Identity -- the Tier 0 fingerprint primitive: what are these bytes, what
code is this method, what did this run consume, what is this run's identity.

Four families, each a function from values to a hex digest. Documented by
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
  ``manifest_bytes``, ``parse_manifest_bytes``, ``is_directory_hash``) and its
  refusal ``DirectoryContentError``.
- ``code_fingerprint`` -- ``RECOGNIZED_SCRIPT_EXTENSIONS``,
  ``collect_method_scripts``, ``build_code_fingerprint``.
- ``input_fingerprint`` -- the part alphabet (``UpstreamRunIdentity``,
  ``SampleIdentity``, ``render_run_part``, ``render_sample_part``,
  ``render_input_parts``), the digest (``digest_input_parts``) and their
  composition ``build_input_fingerprint``.
- ``cache_key`` -- ``build_cache_key``.
"""

from __future__ import annotations

from .cache_key import build_cache_key
from .code_fingerprint import (
    RECOGNIZED_SCRIPT_EXTENSIONS,
    build_code_fingerprint,
    collect_method_scripts,
)
from .content_hash import (
    DIR_HASH_SUFFIX,
    DirectoryContentError,
    DirectoryManifest,
    check_case_collisions,
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
    "DirectoryManifest",
    "check_case_collisions",
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
    "collect_method_scripts",
    "digest_input_parts",
    "hash_directory",
    "hash_file",
    "hash_path",
    "render_input_parts",
    "render_run_part",
    "render_sample_part",
]
