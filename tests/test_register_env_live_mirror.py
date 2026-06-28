"""Tests for the live-env-capture and --from flows added to
``wfc register-env`` for the ADR-019 live-mirror cycle.

Scope:
  * :class:`wfc.envs.EnvRecord` round-trip preserves ``source_fingerprint``.
  * :func:`wfc.register._resolve_pixi_standalone` falls back to a local
    ``<project>/.pixi/envs/<env>`` directory when the configured pixi_root
    glob produces zero matches.
  * :func:`wfc.cli._stage_from_path` shapes the source payload correctly for
    conda and pixi (including the pixi.toml sibling pickup) and rejects
    other backends.
  * The CLI mutex around positional typed-spec ⨯ ``--backend`` ⨯ ``--from``
    errors before any docker subprocess fires.

All tests are pure-python and do not need docker / pixi / conda installed.
The full live-spec → docker-build → manifest round-trip is gated on
``requires_docker`` and ``requires_pixi`` / ``requires_conda``; those
integration cases land in ``tests/integration/`` when CI gets a docker
runner.
"""

import sys
from pathlib import Path

import pytest

from wfc.envs import EnvRecord
from wfc.register import _local_pixi_env_dir, _resolve_pixi_standalone


# =============================================================================
# EnvRecord round-trip with source_fingerprint
# =============================================================================

def test_envrecord_roundtrip_carries_source_fingerprint():
    """source_fingerprint must survive to_dict → from_dict so the manifest
    can be re-read after a live-spec registration."""
    rec = EnvRecord(
        backend="pixi",
        source="pixi:wcia:hello",
        container="cell_pose@sha256:" + "a" * 64,
        env_fingerprint="b" * 32,
        built_at="2026-05-19T00:00:00Z",
        built_from_lock="pixi.lock",
        source_fingerprint="c" * 32,
    )
    payload = rec.to_dict()
    assert payload["source_fingerprint"] == "c" * 32
    restored = EnvRecord.from_dict(payload)
    assert restored == rec


def test_envrecord_from_dict_tolerates_missing_source_fingerprint():
    """Records written by older cycles (pre-Cycle-E) must still load and
    expose source_fingerprint as None."""
    legacy = {
        "backend": "pixi",
        "source": None,
        "container": "x@sha256:" + "d" * 64,
        "env_fingerprint": "e" * 32,
        "built_at": "2026-05-01T00:00:00Z",
    }
    rec = EnvRecord.from_dict(legacy)
    assert rec.source_fingerprint is None


# =============================================================================
# Pixi local .pixi/envs fallback
# =============================================================================

def test_resolve_pixi_standalone_uses_local_fallback_when_pixi_root_unset(tmp_path):
    """When pixi_root is None and a local ``<project>/.pixi/envs/default``
    exists, resolution must return the local env's python instead of
    erroring on missing pixi_root config."""
    env_dir = tmp_path / ".pixi" / "envs" / "default"
    if sys.platform == "win32":
        py = env_dir / "python.exe"
    else:
        py = env_dir / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.touch()

    resolved = _resolve_pixi_standalone(
        "wcia", pixi_root=None, project_dir=tmp_path,
    )
    assert resolved == py


def test_resolve_pixi_standalone_errors_when_neither_root_nor_local(tmp_path):
    """With no pixi_root configured and no local .pixi/envs/default, the
    error message must mention both search locations so the user knows
    which knob to turn."""
    with pytest.raises(ValueError, match=r"\.pixi[\\/]envs[\\/]default"):
        _resolve_pixi_standalone(
            "wcia", pixi_root=None, project_dir=tmp_path,
        )


def test_local_pixi_env_dir_returns_none_when_missing(tmp_path):
    """The fallback helper returns None (not a non-existent Path) so
    callers can branch cleanly."""
    assert _local_pixi_env_dir("default", tmp_path) is None


# =============================================================================
# CLI --from staging
# =============================================================================

def test_stage_from_path_pixi_picks_up_sibling_pixi_toml(tmp_path):
    """For ``--backend pixi``, an adjacent pixi.toml next to the lock file
    is staged too — losing it would build an image without the manifest
    that pixi reads at install-time."""
    from wfc.cli import _stage_from_path

    lock = tmp_path / "pixi.lock"
    lock.write_text("version: 4\n", encoding="utf-8")
    toml = tmp_path / "pixi.toml"
    toml.write_text("[project]\nname = \"x\"\n", encoding="utf-8")

    source = _stage_from_path(backend="pixi", from_path=lock)
    assert source["pixi_lock_content"] == "version: 4\n"
    assert source["pixi_toml_content"] == "[project]\nname = \"x\"\n"
    assert source["pip_freeze_content"] == ""


def test_stage_from_path_conda_shapes_explicit_list(tmp_path):
    from wfc.cli import _stage_from_path

    explicit = tmp_path / "explicit.txt"
    explicit.write_text("# conda-explicit\n@EXPLICIT\nhttps://x/pkg-1.0.tar.bz2\n", encoding="utf-8")

    source = _stage_from_path(backend="conda", from_path=explicit)
    assert "@EXPLICIT" in source["explicit_list_content"]
    assert source["pip_freeze_content"] == ""


def test_stage_from_path_rejects_byo_and_inherit(tmp_path):
    from wfc.cli import _stage_from_path
    f = tmp_path / "x.txt"
    f.write_text("noop", encoding="utf-8")
    with pytest.raises(ValueError, match="pixi or conda"):
        _stage_from_path(backend="byo", from_path=f)


# =============================================================================
# CLI mutex enforcement
# =============================================================================

def test_register_env_mutex_typed_spec_with_backend_errors(cli):
    """Positional typed-spec + --backend is contradictory — must error
    BEFORE any docker subprocess fires."""
    result = cli("register-env", "my", "conda:cell_pose", "--backend", "conda")
    assert result.returncode == 1
    assert "typed-spec" in result.stderr
    assert "--backend" in result.stderr


def test_register_env_mutex_typed_spec_with_from_errors(cli):
    """Positional typed-spec captures from a live env; --from is for file
    mode. Combining them is contradictory."""
    result = cli(
        "register-env", "my", "conda:cell_pose",
        "--from", "explicit.txt",
    )
    assert result.returncode == 1
    assert "--from" in result.stderr


def test_register_env_from_without_backend_errors(cli):
    """--from needs --backend to know which generator filename to stage as."""
    result = cli("register-env", "my", "--from", "explicit.txt")
    assert result.returncode == 1
    assert "--backend" in result.stderr


# =============================================================================
# --from file-mode source_fingerprint capture (US-2)
# =============================================================================

_FROM_PIXI_LOCK = """\
version: 5
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda
  name: python
  version: 3.11.0
- pypi: https://files.pythonhosted.org/packages/numpy-1.24.0-cp311.whl
  name: numpy
  version: 1.24.0
"""


@pytest.mark.parametrize("backend,source_key,lock_text", [
    ("pixi", "pixi_lock_content", _FROM_PIXI_LOCK),
    ("conda", "explicit_list_content",
     "@EXPLICIT\nhttps://conda.anaconda.org/conda-forge/linux-64/python-3.11.0-h.conda#0a\n"),
])
def test_register_from_records_source_fingerprint_that_round_trips(
    tmp_path, monkeypatch, backend, source_key, lock_text
):
    """A --from pixi/conda registration (no live env, empty pip-freeze) records
    a non-null source_fingerprint whose cached blob round-trips through
    parse_packages — the file-mode capture this cycle adds (US-2)."""
    from wfc import envs as envs_mod
    from wfc import docker_runner
    from wfc.env_packages import parse_packages

    (tmp_path / ".wfc").mkdir()
    monkeypatch.setattr(docker_runner, "build", lambda d, t: None)
    monkeypatch.setattr(docker_runner, "image_inspect", lambda r: "sha256:" + "c" * 64)

    # Shape mirrors wfc.cli._stage_from_path: full lock/explicit-list content
    # plus an empty pip-freeze section.
    source = {source_key: lock_text, "pip_freeze_content": ""}
    record = envs_mod.register(
        name="demo", backend=backend, source=source, project_dir=tmp_path,
    )

    assert record.source_fingerprint is not None
    assert len(record.source_fingerprint) == 32

    md5 = record.source_fingerprint
    blob_path = tmp_path / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
    blob = blob_path.read_text(encoding="utf-8")
    pkgs = parse_packages(blob, backend)
    names = {p["name"] for p in pkgs}
    assert "python" in names
    assert all(p["source"] == backend for p in pkgs)
