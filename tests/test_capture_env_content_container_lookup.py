"""Tier 2: capture_env_content manifest-lookup branch is subprocess-free.

US-4 acceptance: when env_spec resolves to a manifest entry with a non-empty
``container`` field, capture_env_content returns the precomputed
``env_fingerprint`` verbatim with no subprocess invocation.

The pixi branch retains its current subprocess behavior — regression check
included.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


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

    Patch subprocess.run to raise so any accidental shell-out blows up.
    """
    import subprocess as _sp

    from wfc.version import capture_env_content

    fingerprint = "deadbeef" * 8
    container = "docker://ghcr.io/dante/image-io@sha256:" + ("a" * 64)
    _write_manifest(tmp_path, "image-io", container, fingerprint)

    def boom(*args, **kwargs):
        raise AssertionError(
            "subprocess.run must not be called for container-env runtime lookup"
        )

    monkeypatch.setattr(_sp, "run", boom)

    result = capture_env_content("image-io", tmp_path)
    assert result == fingerprint


def test_container_spec_string_branch_still_works(tmp_path):
    """Regression: the existing ``container:<image>@sha256:<hex>`` spec-string
    branch (Cycle C precompute write path) is unchanged by the new lookup
    branch."""
    from wfc.version import capture_env_content

    (tmp_path / ".wfc").mkdir()
    spec = "container:image-io@sha256:" + ("a" * 64)
    blob = capture_env_content(spec, tmp_path)
    parsed = json.loads(blob)
    assert parsed["type"] == "container"


def test_pixi_branch_still_calls_subprocess(tmp_path, monkeypatch):
    """Regression: pixi env spec MUST still shell out — only container
    envs short-circuit. We assert this by patching subprocess.run with a
    sentinel and confirming the call happens (we then short-circuit the
    rest with an early raise to avoid running real pixi)."""
    import subprocess as _sp

    from wfc import version as version_mod

    called = {"count": 0}

    # We don't have a real pixi env, so we instead patch the helpers used
    # by the pixi branch to raise a specific error, then assert that the
    # container-lookup branch did NOT swallow the call. The signal is that
    # capture_env_content raises (not silently returns).
    def fake_lock(*args, **kwargs):
        called["count"] += 1
        raise RuntimeError("pixi-branch reached")

    # capture_env_content does ``from .env_introspect import pixi_lock_section``
    # inside the function body, so patch the source module.
    from wfc import env_introspect as _ei
    monkeypatch.setattr(_ei, "pixi_lock_section", fake_lock, raising=True)
    # Make read_config return a sensible pixi_root so we reach pixi_lock_section.
    monkeypatch.setattr(
        "wfc.init.read_config",
        lambda d: {"pixi_root": str(tmp_path), "conda_root": ""},
    )

    (tmp_path / ".wfc").mkdir()
    with pytest.raises(RuntimeError, match="pixi-branch reached"):
        version_mod.capture_env_content("pixi:image-io", tmp_path)
    assert called["count"] == 1


def test_manifest_lookup_falls_through_when_container_field_absent(tmp_path):
    """A manifest entry MUST have ``container`` set non-empty for the
    short-circuit to fire. Manifest entry with no container → fall through
    to the existing dispatch (which will raise ValueError for the bare
    name because it's not a typed backend spec)."""
    from wfc.version import capture_env_content

    # Write a manifest entry with container="" so the lookup branch skips.
    _write_manifest(tmp_path, "broken-env", container="", fingerprint="x" * 64)
    # The bare name "broken-env" is not a typed backend (pixi:/conda:/inherit/container:),
    # so we expect the existing unknown-backend ValueError.
    with pytest.raises(ValueError, match="Unknown env backend"):
        capture_env_content("broken-env", tmp_path)
