"""
Subsystem Tests: .wfc/envs.json manifest (ADR-019 cycle A)

Covers:
  - validate_container_ref: shape check accepts digest-pinned refs and
    rejects floating tags / malformed scheme / missing host (Tier 1).
  - load_manifest: missing file returns empty manifest; unknown
    schema_version raises with a clear message (Tier 1).
  - list_envs / get / delete: read + remove against a sample manifest
    file written directly to .wfc/envs.json (Tier 2).
  - [registry] block parsed by read_config (Tier 2).
  - wfc.cli list-envs / show-env / delete-env happy paths via the in-process
    cli runner (Tier 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dflow.core.decorators import workflow, Step


VALID_DIGEST = "a" * 64
VALID_REF = f"docker://ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(project_dir: Path, envs: dict, schema_version: int = 1) -> None:
    """Write a manifest dict to ``<project>/.wfc/envs.json``."""
    (project_dir / ".wfc").mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": schema_version, "envs": envs}
    (project_dir / ".wfc" / "envs.json").write_text(json.dumps(payload, indent=2))


def _sample_record(container: str = VALID_REF) -> dict:
    """Return a sample record-dict (the VALUE side of an envs[name] entry).

    Per ADR-019 §registration-model-and-manifest, the env name is the KEY
    of the ``envs`` dict, not a field inside the record — so this helper
    does NOT include a ``name`` key.
    """
    return {
        "backend": "pixi",
        "source": "pixi.toml",
        "container": container,
        "env_fingerprint": "deadbeef" * 8,
        "built_from_lock": "pixi.lock",
        "built_at": "2026-05-16T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Tier 1: validate_container_ref
# ---------------------------------------------------------------------------

def test_validate_container_ref_accepts_digest_pin():
    from wfc.envs import validate_container_ref
    validate_container_ref(VALID_REF)  # no raise


@pytest.mark.parametrize("bad_ref", [
    "docker://ghcr.io/dante/image-io:latest",            # floating tag
    "docker://ghcr.io/dante/image-io:v1",                # floating tag
    "ghcr.io/dante/image-io@sha256:" + ("a" * 64),       # missing scheme
    "docker://@sha256:" + ("a" * 64),                    # missing host
    "docker://ghcr.io/dante/image-io@sha256:tooshort",   # bad digest length
    "",                                                   # empty
])
def test_validate_container_ref_rejects_non_digest_pin(bad_ref):
    from wfc.envs import validate_container_ref
    with pytest.raises(ValueError, match="digest-pinned|non-empty"):
        validate_container_ref(bad_ref)


# ---------------------------------------------------------------------------
# Tier 1: load_manifest edge cases
# ---------------------------------------------------------------------------

def test_load_manifest_missing_file_returns_empty(tmp_path):
    (tmp_path / ".wfc").mkdir()
    from wfc.envs import load_manifest
    manifest = load_manifest(tmp_path)
    assert manifest == {"schema_version": 1, "envs": {}}


def test_load_manifest_unknown_schema_version_raises(tmp_path):
    _write_manifest(tmp_path, envs={}, schema_version=99)
    from wfc.envs import load_manifest
    with pytest.raises(ValueError, match="schema_version"):
        load_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Tier 2: list_envs / get / delete
# ---------------------------------------------------------------------------

@workflow(purpose="list_envs returns (name, record) tuples sorted by name; "
                  "missing manifest yields empty list")
def test_list_envs_empty_and_populated(tmp_path):
    from wfc.envs import list_envs
    (tmp_path / ".wfc").mkdir()

    # No manifest file -> empty
    assert list_envs(tmp_path) == []

    # Populated -> 2 sorted (name, EnvRecord) tuples
    _write_manifest(tmp_path, envs={
        "zeta": _sample_record(),
        "alpha": _sample_record(),
    })
    records = list_envs(tmp_path)
    assert [name for name, _ in records] == ["alpha", "zeta"]
    assert records[0][1].container == VALID_REF
    assert records[0][1].backend == "pixi"


@workflow(purpose="get returns full record on hit, None on miss")
def test_get_hit_and_miss(tmp_path):
    from wfc.envs import get
    _write_manifest(tmp_path, envs={"image-io": _sample_record()})

    record = get("image-io", tmp_path)
    assert record is not None
    # The name is the dict key in .wfc/envs.json::envs, not a field on the
    # record itself (ADR-019). Callers know the name from the lookup args.
    assert record.env_fingerprint == "deadbeef" * 8
    assert record.backend == "pixi"

    assert get("nonexistent", tmp_path) is None


@workflow(purpose="delete removes the entry from the manifest "
                  "and survives across reload")
def test_delete_removes_entry(tmp_path):
    from wfc.envs import delete, get, list_envs
    _write_manifest(tmp_path, envs={
        "image-io": _sample_record(),
        "other": _sample_record(),
    })

    delete("image-io", tmp_path)
    assert get("image-io", tmp_path) is None
    assert [name for name, _ in list_envs(tmp_path)] == ["other"]

    # KeyError on second delete
    with pytest.raises(KeyError):
        delete("image-io", tmp_path)


def test_save_manifest_is_atomic(tmp_path):
    """save_manifest writes via tempfile + os.replace so a reader never
    sees a half-flushed file even if the process is interrupted."""
    from wfc.envs import save_manifest, load_manifest
    (tmp_path / ".wfc").mkdir()

    payload = {"schema_version": 1, "envs": {"image-io": _sample_record()}}
    save_manifest(tmp_path, payload)

    # No leftover temp files in .wfc/
    leftovers = [p.name for p in (tmp_path / ".wfc").iterdir()
                 if p.name.startswith(".envs.")]
    assert leftovers == []

    # Round-trip via load_manifest
    assert load_manifest(tmp_path)["envs"]["image-io"]["container"] == VALID_REF


# ---------------------------------------------------------------------------
# Tier 2: read_config parses [registry] block
# ---------------------------------------------------------------------------

@workflow(purpose="read_config exposes config['registry'] when [registry] is "
                  "declared in wf-canvas.toml; None when absent")
def test_read_config_registry_block(tmp_path):
    from wfc.init import read_config
    wfc_dir = tmp_path / ".wfc"
    wfc_dir.mkdir()

    # Without [registry]: config['registry'] is None
    (wfc_dir / "wf-canvas.toml").write_text(
        '[project]\nname = "demo"\n'
    )
    cfg = read_config(tmp_path)
    assert cfg["registry"] is None

    # With [registry]: parsed into a dict
    (wfc_dir / "wf-canvas.toml").write_text(
        '[project]\nname = "demo"\n'
        '\n[registry]\n'
        'url = "ghcr.io/dante"\n'
    )
    cfg = read_config(tmp_path)
    assert cfg["registry"] == {"url": "ghcr.io/dante"}


# ---------------------------------------------------------------------------
# Tier 3: CLI surface (US-1, US-2, US-3)
# ---------------------------------------------------------------------------

@workflow(
    purpose="wfc list-envs prints a friendly empty-state line in a fresh "
            "project, then a 2-row table once envs are registered "
            "(US-1 acceptance)",
)
def test_cli_list_envs_empty_then_populated(cli, tmp_project):
    口 = Step(step_num=1, name="Empty state",
             purpose="Fresh project -> friendly message, exit 0")
    result = cli("list-envs")
    assert result.returncode == 0, result.stderr
    assert "No container envs registered" in result.stdout

    口 = Step(step_num=2, name="Populate manifest",
             purpose="Write a 2-env manifest directly to .wfc/envs.json")
    _write_manifest(tmp_project, envs={
        "image-io": _sample_record(),
        "ml": _sample_record(),
    })

    口 = Step(step_num=3, name="Re-run list-envs",
             purpose="Verify header + 2 data rows are printed")
    result = cli("list-envs")
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # 1 header + 2 rows
    assert len(lines) == 3
    assert "NAME" in lines[0]
    assert "image-io" in lines[1]
    assert "ml" in lines[2]


@workflow(
    purpose="wfc delete-env warns when methods reference the env, declines "
            "to delete on 'N', and removes the entry on --force without "
            "touching method rows (US-3 acceptance)",
)
def test_cli_delete_env_warns_and_preserves_method_rows(
    cli, tmp_project, monkeypatch,
):
    from wfc.init import init_project
    from wfc.register import register_module, register_method
    from wfc.database import get_session
    from wfc.models import Method
    from sqlmodel import select

    口 = Step(step_num=1, name="Initialise project + register a method",
             purpose="Set up DB rows so the warn-on-reference path has data")
    init_project(tmp_project)
    register_module(name="data_transform",
                    contracts=[{"type": "output", "name": "output",
                                "value_type": "csv", "required": True}],
                    description="Test")

    # Write a manifest with image-io, then register a method whose
    # method.yaml declares env: container:image-io.
    _write_manifest(tmp_project, envs={"image-io": _sample_record()})

    method_dir = tmp_project / "methods" / "transform"
    # The transform method exists from fixture copy in conftest; add an
    # env field to its method.yaml.
    yaml_path = method_dir / "method.yaml"
    assert yaml_path.exists()
    original_yaml = yaml_path.read_text()
    yaml_path.write_text(original_yaml + "\nenv: container:image-io\n")

    register_method(method_dir=method_dir, module_name="data_transform")

    # Verify DB has Method.env == 'container:image-io'
    with get_session() as session:
        m = session.exec(select(Method).where(Method.name == "transform")).one()
        assert m.env == "container:image-io"

    口 = Step(step_num=2, name="Decline the prompt",
             purpose="Answer 'N' -> entry stays in manifest")
    monkeypatch.setattr("builtins.input", lambda *_: "N")
    result = cli("delete-env", "image-io")
    assert result.returncode == 1
    assert "WARNING" in result.stdout
    assert "data_transform/transform" in result.stdout
    # Manifest still has the env
    from wfc.envs import get as env_get
    assert env_get("image-io", tmp_project) is not None

    口 = Step(step_num=3, name="Force-delete",
             purpose="--force skips the prompt; entry removed; method row stays")
    result = cli("delete-env", "image-io", "--force")
    assert result.returncode == 0
    assert env_get("image-io", tmp_project) is None

    with get_session() as session:
        rows = session.exec(select(Method).where(Method.name == "transform")).all()
        assert len(rows) == 1
        assert rows[0].env == "container:image-io"  # untouched


# ---------------------------------------------------------------------------
# Tier 1: _resolve_env edge cases not exercised through register_method
# ---------------------------------------------------------------------------
# The happy-path + missing-env + floating-tag scenarios for
# ``container:<envname>`` are covered end-to-end in
# tests/test_registration.py::test_register_method_resolves_container_env.
# What remains here are the pure resolution edges of the direct-ref branch
# (which does NOT touch the manifest), kept thin so the unit-level branch
# logic stays pinned even if the integration path changes shape later.


def test_resolve_env_direct_ref_bypasses_manifest(tmp_project):
    """`container:docker://...@sha256:...` is validated for shape only —
    no manifest lookup, no error if the manifest is missing entirely."""
    from wfc.register import _resolve_env

    direct_ref = f"container:docker://ghcr.io/dante/x@sha256:{VALID_DIGEST}"
    # No .wfc/envs.json on disk at all — direct ref must still resolve.
    assert _resolve_env(direct_ref, tmp_project) == direct_ref


def test_resolve_env_direct_ref_floating_tag_rejected(tmp_project):
    """A direct `container:docker://...` ref without a digest is rejected."""
    from wfc.register import _resolve_env
    with pytest.raises(ValueError, match="digest-pinned"):
        _resolve_env("container:docker://ghcr.io/dante/x:latest", tmp_project)
