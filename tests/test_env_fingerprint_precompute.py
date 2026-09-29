"""Subsystem test: container-branch capture_env_content is canonical and
deterministic.

Covers the precompute *write* path used by wfc.environments.register to populate
``env_fingerprint`` at registration time. The runtime *read* path (looking
up a method's container) happens at run-step time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_annotations import workflow


@workflow(
    purpose="capture_env_content('container:<image>@sha256:<hex>', ...) "
            "emits canonical JSON (sorted keys, no spaces), is deterministic "
            "across calls, and produces a DIFFERENT hash when the digest "
            "changes — fingerprint must track image identity"
)
def test_env_fingerprint_precompute_canonical_and_sensitive_to_digest(tmp_path):
    from wfc.environments.fingerprint import capture_env_content
    # Need .wfc/ for downstream store_env_content; capture_env_content itself
    # does not touch disk for the container branch, but keep the layout
    # consistent.
    (tmp_path / ".wfc").mkdir()

    spec_a = "container:image-io@sha256:" + ("a" * 64)
    spec_a_again = "container:image-io@sha256:" + ("a" * 64)
    spec_b = "container:image-io@sha256:" + ("b" * 64)

    blob_a = capture_env_content(spec_a)
    blob_a_again = capture_env_content(spec_a_again)
    blob_b = capture_env_content(spec_b)

    # Canonical JSON: parseable, sorted keys, no spaces.
    parsed = json.loads(blob_a)
    assert parsed == {
        "type": "container",
        "image": "image-io",
        "digest": "sha256:" + ("a" * 64),
    }
    # No spaces (separators=(",",":")).
    assert ", " not in blob_a and ": " not in blob_a
    # Sorted keys: 'digest' < 'image' < 'type'.
    assert blob_a.index('"digest"') < blob_a.index('"image"') < blob_a.index('"type"')

    # Determinism.
    assert blob_a == blob_a_again

    # Digest sensitivity.
    assert blob_a != blob_b


def test_container_spec_malformed_rejected(tmp_path):
    """Missing @sha256 marker must raise ValueError, not silently produce
    a garbage fingerprint."""
    from wfc.environments.fingerprint import capture_env_content
    (tmp_path / ".wfc").mkdir()
    with pytest.raises(ValueError, match="container env spec"):
        capture_env_content("container:image-io-no-digest")
