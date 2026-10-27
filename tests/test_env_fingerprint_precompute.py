"""Subsystem test: the env content blob is canonical and tracks content.

Covers the precompute *write* path: ``capture_env_content`` renders what
``wfc.environments.docker.image_content`` inspected (layers, Env, User,
WorkingDir) into the blob ``register`` stores as ``env_fingerprint``. The
runtime *read* path (looking up a method's env) happens at run-step time
and never renders.
"""

from __future__ import annotations

import hashlib

import pytest

from axiom_annotations import workflow

#: An inspected image: two layers and a config that sets every field.
BASE_CONTENT = {
    "layers": ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
    "env": ["PATH=/usr/bin:/bin", "LANG=C.UTF-8"],
    "user": "1000",
    "workdir": "/work",
}


def _md5(blob: str) -> str:
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


@workflow(
    purpose="capture_env_content renders inspected image content as canonical "
            "JSON (sorted keys, no spaces) with layers and Env in the daemon's "
            "order, and renders a null Env and an empty User and WorkingDir "
            "as stable values"
)
def test_env_content_blob_is_canonical_and_stable():
    from wfc.environments.fingerprint import capture_env_content

    blob = capture_env_content(BASE_CONTENT)
    assert blob == (
        '{"env":["PATH=/usr/bin:/bin","LANG=C.UTF-8"],'
        '"layers":["sha256:' + "a" * 64 + '","sha256:' + "b" * 64 + '"],'
        '"type":"container-content","user":"1000","workdir":"/work"}'
    )
    assert capture_env_content(dict(BASE_CONTENT)) == blob

    # An image that sets no Env, User or WorkingDir renders one stable blob,
    # whether the daemon reports them as null or as empty.
    bare_null = {"layers": ["sha256:" + "a" * 64], "env": None,
                 "user": None, "workdir": None}
    bare_empty = {"layers": ["sha256:" + "a" * 64], "env": [],
                  "user": "", "workdir": ""}
    assert capture_env_content(bare_null) == capture_env_content(bare_empty) == (
        '{"env":[],"layers":["sha256:' + "a" * 64 + '"],'
        '"type":"container-content","user":"","workdir":""}'
    )


@workflow(
    purpose="Images that differ in one layer, or in Env (including only its "
            "order), User or WorkingDir, render different blobs and so "
            "record different env fingerprints"
)
@pytest.mark.parametrize("field, changed", [
    ("layers", ["sha256:" + "a" * 64, "sha256:" + "c" * 64]),
    ("env", ["PATH=/usr/bin:/bin", "LANG=en_US.UTF-8"]),
    ("env", ["LANG=C.UTF-8", "PATH=/usr/bin:/bin"]),
    ("user", "root"),
    ("workdir", "/"),
])
def test_one_field_difference_gives_a_different_fingerprint(field, changed):
    from wfc.environments.fingerprint import capture_env_content

    other = {**BASE_CONTENT, field: changed}
    assert (_md5(capture_env_content(other))
            != _md5(capture_env_content(BASE_CONTENT)))


@pytest.mark.parametrize("layers", [None, [], ()])
def test_container_spec_malformed_rejected(layers):
    """Content with no layers is refused, not rendered into a fingerprint:
    every image has at least one layer, so this is not an inspected image."""
    from wfc.environments.fingerprint import capture_env_content

    with pytest.raises(ValueError, match="no layers"):
        capture_env_content({**BASE_CONTENT, "layers": layers})
