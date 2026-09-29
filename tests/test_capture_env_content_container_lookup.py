"""resolve_env_fingerprint answers only from a registered container env.

When env_spec names a manifest entry with a non-empty ``container``
field, resolve_env_fingerprint returns the precomputed ``env_fingerprint``
verbatim with no subprocess invocation and no cache write (re-storing the
fingerprint string would hash the hash). Any other name is refused before
anything is captured or stored: the error names the env as not registered
and points at ``wfc register-env``. capture_env_content's
``container:<image>@sha256:<hex>`` precompute path is pinned here too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.fakes import fake_subprocess_run, refusing_process
from axiom_annotations import workflow


def _write_manifest(project_dir: Path, name: str, container: str, fingerprint: str) -> None:
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "envs": {
            name: {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": container,
                "env_fingerprint": fingerprint,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-17T00:00:00Z",
            }
        },
    }
    (project_dir / ".wfc" / "envs.json").write_text(json.dumps(payload))


def test_container_lookup_returns_precomputed_fingerprint_no_subprocess(tmp_path, monkeypatch):
    """Manifest entry with `container` set → return env_fingerprint verbatim.

    Patch subprocess.run to raise so any accidental shell-out blows up, and
    assert no DVC cache write happens — the blob was stored at registration
    time; the runtime read path must not hash the fingerprint string.
    """
    import subprocess as _sp

    from wfc.environments.fingerprint import resolve_env_fingerprint
    fingerprint = "deadbeef" * 8
    container = "docker://ghcr.io/dante/image-io@sha256:" + ("a" * 64)
    _write_manifest(tmp_path, "image-io", container, fingerprint)

    fake_subprocess_run(monkeypatch, refusing_process(
        "subprocess.run must not be called for container-env runtime lookup"))

    result = resolve_env_fingerprint("image-io", tmp_path)
    assert result == fingerprint
    assert not (tmp_path / ".dvc").exists()


def test_container_spec_string_branch_still_works(tmp_path):
    """The ``container:<image>@sha256:<hex>`` spec-string branch (precompute
    write path) captures a container blob, independent of the manifest
    lookup branch."""
    from wfc.environments.fingerprint import capture_env_content
    (tmp_path / ".wfc").mkdir()
    spec = "container:image-io@sha256:" + ("a" * 64)
    blob = capture_env_content(spec)
    parsed = json.loads(blob)
    assert parsed["type"] == "container"


@workflow(purpose="A name with no registered container env (deleted from the "
                  "manifest after its method registered, or recorded without a "
                  "container) is refused: the error names the env as not "
                  "registered and points at `wfc register-env`, with no capture "
                  "subprocess and no cache write")
def test_fingerprint_refuses_a_name_with_no_registered_container_env(tmp_path, monkeypatch):
    """Both kinds of unregistered name get the same refusal."""
    import subprocess as _sp

    from wfc.environments.fingerprint import resolve_env_fingerprint
    _write_manifest(tmp_path, "broken-env", container="", fingerprint="x" * 64)

    fake_subprocess_run(monkeypatch, refusing_process(
        "an unregistered env must be refused without a capture subprocess"))

    # "deleted-env" has no record at all; "broken-env" has one with no container.
    for name in ("deleted-env", "broken-env"):
        with pytest.raises(ValueError, match="not registered") as excinfo:
            resolve_env_fingerprint(name, tmp_path)
        message = str(excinfo.value)
        assert name in message
        assert "wfc register-env" in message
    assert not (tmp_path / ".dvc").exists()


def test_fingerprint_surfaces_an_unreadable_manifest(tmp_path):
    """A manifest that cannot be read raises its own error, naming the
    file, rather than reporting the env as unregistered."""
    from wfc.environments.fingerprint import resolve_env_fingerprint
    (tmp_path / ".wfc").mkdir()
    (tmp_path / ".wfc" / "envs.json").write_text("{not json")
    with pytest.raises(ValueError, match="envs.json: not valid JSON"):
        resolve_env_fingerprint("image-io", tmp_path)

