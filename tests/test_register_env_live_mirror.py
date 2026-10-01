"""Tests for the live-env-capture and --from flows of
``wfc register-env``.

Scope:
  * :class:`wfc.environments.EnvRecord` round-trip preserves ``source_fingerprint``.
  * :func:`wfc.environments.host._resolve_pixi_standalone` falls back to a local
    ``<project>/.pixi/envs/<env>`` directory when the configured pixi_root
    glob produces zero matches.
  * :func:`wfc.environments.verbs._stage_from_path` shapes the source payload correctly for
    conda and pixi (including the pixi.toml sibling pickup) and rejects
    other backends.
  * The CLI mutex around positional typed-spec ⨯ ``--backend`` ⨯ ``--from``
    errors before any docker subprocess fires.
  * :func:`wfc.environments.verbs._stage_live_env_source` pixi branch stages the resolved
    env's project lock, toml and pip freeze, and refuses a missing lock.

All tests are pure-python and do not need docker / pixi / conda installed.
"""

import sys
from pathlib import Path

import pytest

from axiom_annotations import workflow

from tests.fixtures.fakes import (
    fake_pip_freeze,
    stub_docker_build,
    stub_docker_image_inspect,
    stub_readiness_probes,
)
from wfc.environments import EnvRecord
from wfc.environments.host import _local_pixi_env_dir, _resolve_pixi_standalone


# =============================================================================
# EnvRecord round-trip with source_fingerprint
# =============================================================================

def test_envrecord_roundtrip_carries_source_fingerprint():
    """source_fingerprint must survive to_dict → from_dict so the manifest
    can be re-read after a live-spec registration."""
    rec = EnvRecord(
        backend="pixi",
        source="pixi:wcia:hello",
        container="docker://local/cell_pose@sha256:" + "a" * 64,
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
    """A record with no source_fingerprint key must still load and expose
    source_fingerprint as None."""
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
    error message lists the local path it searched, so the user sees where
    it looked; `[pixi] root` appears only as the suggested fix."""
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
    from wfc.environments.verbs import _stage_from_path

    lock = tmp_path / "pixi.lock"
    lock.write_text("version: 4\n", encoding="utf-8")
    toml = tmp_path / "pixi.toml"
    toml.write_text("[project]\nname = \"x\"\n", encoding="utf-8")

    source = _stage_from_path(backend="pixi", from_path=lock)
    assert source["pixi_lock_content"] == "version: 4\n"
    assert source["pixi_toml_content"] == "[project]\nname = \"x\"\n"
    assert source["pip_freeze_content"] == ""


def test_stage_from_path_conda_shapes_explicit_list(tmp_path):
    from wfc.environments.verbs import _stage_from_path

    explicit = tmp_path / "explicit.txt"
    explicit.write_text("# conda-explicit\n@EXPLICIT\nhttps://x/pkg-1.0.tar.bz2\n", encoding="utf-8")

    source = _stage_from_path(backend="conda", from_path=explicit)
    assert "@EXPLICIT" in source["explicit_list_content"]
    assert source["pip_freeze_content"] == ""


def test_stage_from_path_rejects_byo_and_inherit(tmp_path):
    from wfc.environments.verbs import _stage_from_path
    f = tmp_path / "x.txt"
    f.write_text("noop", encoding="utf-8")
    with pytest.raises(ValueError, match="pixi or conda"):
        _stage_from_path(backend="byo", from_path=f)


# =============================================================================
# CLI mutex enforcement
# =============================================================================
#
# Given a host with no Docker: each refusal is the verb's own, reported
# before the Docker gate is asked.


@pytest.fixture
def no_docker(monkeypatch):
    """A host whose Docker probe fails."""
    stub_readiness_probes(monkeypatch, docker="fail")


def test_register_env_mutex_typed_spec_with_backend_errors(cli, no_docker):
    """Positional typed-spec + --backend is contradictory — must error
    BEFORE any docker subprocess fires."""
    result = cli("register-env", "my", "conda:cell_pose", "--backend", "conda")
    assert result.returncode == 1
    assert "typed-spec" in result.stderr
    assert "--backend" in result.stderr


def test_register_env_mutex_typed_spec_with_from_errors(cli, no_docker):
    """Positional typed-spec captures from a live env; --from is for file
    mode. Combining them is contradictory."""
    result = cli(
        "register-env", "my", "conda:cell_pose",
        "--from", "explicit.txt",
    )
    assert result.returncode == 1
    assert "--from" in result.stderr


def test_register_env_from_without_backend_errors(cli, no_docker):
    """--from needs --backend to know which generator filename to stage as."""
    result = cli("register-env", "my", "--from", "explicit.txt")
    assert result.returncode == 1
    assert "--backend" in result.stderr


def test_register_env_backend_alone_pixi_conda_errors_with_guidance(cli, no_docker):
    """``--backend pixi|conda`` without ``--from`` or a typed spec errors
    before any docker subprocess and names both supported modes, so the user
    knows which one to use."""
    for backend in ("pixi", "conda"):
        result = cli("register-env", "my", "--backend", backend)
        assert result.returncode == 1, backend
        assert "--from" in result.stderr, backend
        assert "typed spec" in result.stderr, backend


# =============================================================================
# --from file-mode source_fingerprint capture
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
    parse_packages — the file-mode capture path."""
    from wfc import environments as envs_mod
    from wfc.environments.packages import parse_packages

    (tmp_path / ".wfc").mkdir()
    stub_docker_build(monkeypatch, None)
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)

    # Shape mirrors wfc.environments.verbs._stage_from_path: full lock/explicit-list content
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


# =============================================================================
# Live capture of a pixi env
# =============================================================================

@workflow(
    purpose="Live capture of a pixi:<project>:<env> spec stages the pixi "
            "project's pixi.lock, its pixi.toml and the env's pip freeze, and "
            "refuses a project with no pixi.lock with a FileNotFoundError "
            "that says to run pixi install"
)
def test_stage_live_env_source_pixi_stages_project_lock_toml_and_freeze(
    tmp_path, monkeypatch
):
    from wfc.environments.verbs import _stage_live_env_source

    # A pixi project tree under the configured [pixi] root: the env lives at
    # <root>/<project>-<hash>/envs/<env>/, and the lock and toml sit at the
    # project level above it.
    pixi_root = tmp_path / "pixi_root"
    pixi_project = pixi_root / "wcia-3f2a"
    env_dir = pixi_project / "envs" / "hello"
    python = env_dir / ("python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    lock_text = "version: 6\npackages: []\n"
    toml_text = '[workspace]\nname = "wcia"\n'
    (pixi_project / "pixi.lock").write_text(lock_text, encoding="utf-8")
    (pixi_project / "pixi.toml").write_text(toml_text, encoding="utf-8")

    project_dir = tmp_path / "project"
    (project_dir / ".wfc").mkdir(parents=True)
    (project_dir / ".wfc" / "wf-canvas.toml").write_text(
        f'[pixi]\nroot = "{pixi_root.as_posix()}"\n', encoding="utf-8"
    )

    frozen_from: list[Path] = []

    # The pip subprocess is the one external edge — stub it.
    fake_pip_freeze(monkeypatch, "numpy==1.26.4\n", frozen_from=frozen_from)

    source = _stage_live_env_source("pixi:wcia:hello", project_dir)

    assert source == {
        "pixi_lock_content": lock_text,
        "pixi_toml_content": toml_text,
        "pip_freeze_content": "numpy==1.26.4\n",
    }
    assert frozen_from == [python.resolve()]

    (pixi_project / "pixi.lock").unlink()
    with pytest.raises(FileNotFoundError, match="pixi install"):
        _stage_live_env_source("pixi:wcia:hello", project_dir)
