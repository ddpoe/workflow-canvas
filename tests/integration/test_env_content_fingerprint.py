"""The env fingerprint follows what an image runs, read from a real daemon.

The register-path tests replace ``wfc.environments.docker.image_content``
with ``tests.fixtures.fakes.stub_docker_image_content``; this test is the
fake's backing witness. On real Docker it inspects one image under three
names (its tag, a second tag, its bare image ID), a sibling built from it
with only a ``LABEL`` added (a different image ID, the same content) and a
sibling that differs by one ``ENV`` line, renders each through the production
:func:`wfc.environments.capture_env_content` and stores the blob with
:func:`wfc.storage.store_env_content`, whose md5 is the env fingerprint.

``integration``-marked (deselected by default); skips cleanly without a
reachable Docker daemon. It reuses the session's minimal fixture image, so
it adds no base pull.
"""

from __future__ import annotations

import subprocess

import pytest

from axiom_annotations import workflow

from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

_SOURCE_TAG = "local/wfc-test-minimal:latest"
_ALIAS_TAG = "local/wfc-test-content-alias:latest"
_VARIANT_TAG = "local/wfc-test-content-variant:latest"
_LABELLED_TAG = "local/wfc-test-content-labelled:latest"


def _docker(*args: str, stdin: str | None = None) -> str:
    """Run one docker command, fail the test with its stderr if it fails.

    Returns:
        The command's stdout, stripped.
    """
    proc = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, f"docker {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _image_id(ref: str) -> str:
    """Return the image ID the daemon reports for *ref*."""
    return _docker("image", "inspect", ref, "--format", "{{.Id}}")


@workflow(
    purpose="On a real daemon, one image inspected under its tag, a second "
            "tag and its bare image ID, and a sibling built FROM it with only "
            "a LABEL added (a different image ID), render one content blob "
            "and one env fingerprint, and a sibling image that differs by one "
            "ENV line gets a different fingerprint"
)
def test_content_fingerprint_follows_image_content_not_its_name(
    minimal_image, tmp_path,
):
    from wfc.environments import capture_env_content
    from wfc.environments.docker import image_content
    from wfc.storage import store_env_content

    _docker("tag", _SOURCE_TAG, _ALIAS_TAG)
    _docker("build", "-t", _VARIANT_TAG, "-",
            stdin=f"FROM {_SOURCE_TAG}\nENV WFC_CONTENT_PROBE=1\n")
    _docker("build", "-t", _LABELLED_TAG, "-",
            stdin=f"FROM {_SOURCE_TAG}\nLABEL org.wfc.content-probe=1\n")
    try:
        names = (_SOURCE_TAG, _ALIAS_TAG, f"sha256:{minimal_image}",
                 _LABELLED_TAG)
        blobs = {name: capture_env_content(image_content(name))
                 for name in names}
        image_ids = {name: _image_id(name) for name in (_SOURCE_TAG, _LABELLED_TAG)}
        variant_content = image_content(_VARIANT_TAG)
        variant_blob = capture_env_content(variant_content)
    finally:
        subprocess.run(["docker", "rmi", _ALIAS_TAG, _VARIANT_TAG, _LABELLED_TAG],
                       capture_output=True, timeout=120)

    # The labelled sibling is a different image with the same content.
    assert image_ids[_LABELLED_TAG] != image_ids[_SOURCE_TAG]
    assert len(set(blobs.values())) == 1, blobs
    fingerprints = {store_env_content(blob, tmp_path) for blob in blobs.values()}
    assert len(fingerprints) == 1

    # ENV adds no layer: the variant shares the layers and differs in Env.
    source_content = image_content(_SOURCE_TAG)
    assert variant_content["layers"] == source_content["layers"]
    assert "WFC_CONTENT_PROBE=1" in variant_content["env"]
    assert store_env_content(variant_blob, tmp_path) not in fingerprints
