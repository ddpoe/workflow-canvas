"""
Tests for env_fingerprint provenance (pev-2026-04-18).

Covers:
  - pixi_lock_section is deterministic under cosmetic lock churn
    (semantic fields only, sorted JSON — not YAML round-trip)
  - pip_freeze raises cleanly on subprocess failure / missing binary
  - store_env_content cleans up its temp file on ALL paths,
    including when cache_file raises
  - build_cache_key is sensitive to env_fingerprint changes
  - env content blob is retrievable from the DVC cache under the
    returned md5
  - Legacy Run rows (env_fingerprint NULL) still load
  - Changing the env between two otherwise-identical pre_run calls
    invalidates the cache (different env_fingerprint -> different
    cache_key -> MISS)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.database import get_session
from wfc.env_introspect import (
    conda_list_explicit,
    pip_freeze,
    pixi_lock_section,
)
from wfc.models import Method, MethodVersion, Module, Run, Sample
from wfc.version import (
    build_cache_key,
    capture_env_content,
    store_env_content,
)


# =============================================================================
# Fixtures
# =============================================================================

def _write_lock(path: Path, data: dict) -> None:
    """Write a pixi.lock-shaped dict as YAML to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _make_lock_dict(
    *,
    env_name: str = "default",
    platform: str = "linux-64",
    pkgs: list[dict] | None = None,
    extra_env_keys: dict | None = None,
) -> dict:
    """Build a minimal pixi.lock-shaped dict for fixture use."""
    pkgs = pkgs or [
        {
            "name": "numpy",
            "version": "1.26.0",
            "build": "py312h0",
            "hash": {"md5": "abc123"},
            "platform": platform,
            "url": "https://conda.example/numpy-1.26.0-py312h0.conda",
        },
        {
            "name": "pandas",
            "version": "2.1.0",
            "build": "py312h1",
            "hash": {"md5": "def456"},
            "platform": platform,
            "url": "https://conda.example/pandas-2.1.0-py312h1.conda",
        },
    ]
    env_block = {"packages": {platform: [{"conda": pkg["url"]} for pkg in pkgs]}}
    if extra_env_keys:
        env_block.update(extra_env_keys)
    return {
        "version": 6,
        "environments": {env_name: env_block},
        "packages": pkgs,
    }


# =============================================================================
# pixi_lock_section: cosmetic-churn determinism (load-bearing)
# =============================================================================

@workflow(
    purpose="pixi_lock_section returns identical output for two lock files "
            "that differ only in cosmetic form (field order, extra url/source "
            "noise) but match semantically"
)
def test_pixi_lock_section_cosmetic_churn_stable(tmp_path):
    """Lock-churn robustness is the headline property of env_fingerprint.

    A pyyaml bump that reorders fields, or a pixi bump that adds a new
    cosmetic field, must NOT invalidate every cache entry.  Only semantic
    field changes (name/version/build/hash/platform) should count.
    """
    口 = Step(step_num=1, name="Write baseline lock",
             purpose="Minimal two-package lock with semantic fields")
    pixi_root_a = tmp_path / "A"
    (pixi_root_a / "myenv-abc").mkdir(parents=True)
    lock_a = _make_lock_dict(platform="linux-64")
    _write_lock(pixi_root_a / "myenv-abc" / "pixi.lock", lock_a)

    口 = Step(step_num=2, name="Write cosmetically-churned lock",
             purpose="Same semantic content but extra url/source fields and "
                     "packages listed in reverse order")
    pixi_root_b = tmp_path / "B"
    (pixi_root_b / "myenv-xyz").mkdir(parents=True)
    # Same semantic content: pkgs reversed; add cosmetic 'size' and 'timestamp'.
    reversed_pkgs = list(reversed([
        {
            "size": 12345,  # cosmetic noise
            "timestamp": 1700000000,  # cosmetic noise
            "name": "numpy",
            "version": "1.26.0",
            "build": "py312h0",
            "hash": {"md5": "abc123"},
            "platform": "linux-64",
            "url": "https://conda.example/numpy-1.26.0-py312h0.conda",
        },
        {
            "size": 67890,
            "timestamp": 1700000001,
            "name": "pandas",
            "version": "2.1.0",
            "build": "py312h1",
            "hash": {"md5": "def456"},
            "platform": "linux-64",
            "url": "https://conda.example/pandas-2.1.0-py312h1.conda",
        },
    ]))
    lock_b = {
        "version": 6,
        "packages": reversed_pkgs,
        "environments": {
            "default": {
                "channels": ["conda-forge"],  # cosmetic: not in A
                "packages": {
                    "linux-64": [
                        {"conda": "https://conda.example/pandas-2.1.0-py312h1.conda"},
                        {"conda": "https://conda.example/numpy-1.26.0-py312h0.conda"},
                    ],
                },
            },
        },
    }
    _write_lock(pixi_root_b / "myenv-xyz" / "pixi.lock", lock_b)

    口 = Step(step_num=3, name="Compare outputs",
             purpose="Both locks must produce byte-identical pixi_lock_section output")
    sec_a = pixi_lock_section(pixi_root_a, "myenv", "linux-64")
    sec_b = pixi_lock_section(pixi_root_b, "myenv", "linux-64")
    assert sec_a == sec_b, f"Cosmetic churn changed output:\n  A: {sec_a}\n  B: {sec_b}"


@workflow(
    purpose="pixi_lock_section raises KeyError when the current platform is "
            "not present in the lock — no silent fallback to 'all platforms'"
)
def test_pixi_lock_section_missing_platform_raises(tmp_path):
    """A lock that does not list the current platform cannot honestly
    fingerprint what will be installed — must fail loud."""
    pixi_root = tmp_path / "proj"
    (pixi_root / "myenv-abc").mkdir(parents=True)
    lock = _make_lock_dict(platform="linux-64")
    _write_lock(pixi_root / "myenv-abc" / "pixi.lock", lock)

    with pytest.raises(KeyError, match="win-64"):
        pixi_lock_section(pixi_root, "myenv", "win-64")


@workflow(
    purpose="pixi_lock_section auto-detects the platform from a single-platform "
            "pixi.lock when no platform override is passed — avoids any "
            "homegrown sys.platform -> conda-platform-tag mapping"
)
def test_pixi_lock_section_auto_detects_single_platform(tmp_path):
    """Single-platform lock: platform=None -> use the one listed platform."""
    pixi_root = tmp_path / "proj"
    (pixi_root / "myenv-abc").mkdir(parents=True)
    # Use an unusual platform string to prove the function reads it from the
    # lock itself rather than mapping from sys.platform.
    lock = _make_lock_dict(platform="linux-aarch64")
    _write_lock(pixi_root / "myenv-abc" / "pixi.lock", lock)

    # Without an explicit platform, the function must pull "linux-aarch64"
    # from the lock's environments block and produce a non-empty section.
    sec_auto = pixi_lock_section(pixi_root, "myenv")
    sec_explicit = pixi_lock_section(pixi_root, "myenv", "linux-aarch64")
    assert sec_auto == sec_explicit
    assert "numpy" in sec_auto


@workflow(
    purpose="pixi_lock_section raises an actionable error when the lock lists "
            "multiple platforms and no override is passed — points the caller "
            "at pixi install / explicit platform= argument"
)
def test_pixi_lock_section_multi_platform_ambiguous_raises(tmp_path):
    """Multi-platform lock with no override: must fail loud with an actionable
    message.  We do NOT guess via sys.platform."""
    pixi_root = tmp_path / "proj"
    (pixi_root / "myenv-abc").mkdir(parents=True)
    # Hand-build a multi-platform lock so both linux-64 and win-64 are present.
    pkgs_linux = [{
        "name": "numpy", "version": "1.26.0", "build": "py312h0",
        "hash": {"md5": "abc123"}, "platform": "linux-64",
        "url": "https://conda.example/numpy-1.26.0-py312h0.conda",
    }]
    pkgs_win = [{
        "name": "numpy", "version": "1.26.0", "build": "py312h0_win",
        "hash": {"md5": "winwin"}, "platform": "win-64",
        "url": "https://conda.example/numpy-1.26.0-py312h0_win.conda",
    }]
    lock = {
        "version": 6,
        "environments": {
            "default": {
                "packages": {
                    "linux-64": [{"conda": pkgs_linux[0]["url"]}],
                    "win-64": [{"conda": pkgs_win[0]["url"]}],
                },
            },
        },
        "packages": pkgs_linux + pkgs_win,
    }
    _write_lock(pixi_root / "myenv-abc" / "pixi.lock", lock)

    # Ambiguous: no platform override, multiple platforms in the lock.
    with pytest.raises(ValueError, match="ambiguous|multiple platforms|platform="):
        pixi_lock_section(pixi_root, "myenv")

    # But an explicit platform argument still works.
    sec_linux = pixi_lock_section(pixi_root, "myenv", "linux-64")
    sec_win = pixi_lock_section(pixi_root, "myenv", "win-64")
    assert sec_linux != sec_win
    assert "py312h0" in sec_linux
    assert "py312h0_win" in sec_win


@workflow(
    purpose="capture_env_content('pixi:<project>:<env>', ...) fingerprints the "
            "named env, not whatever env happens to share the project's name — "
            "regression for the bug where parts[1] was used as env_name"
)
def test_capture_env_content_pixi_three_segment_picks_named_env(tmp_path, monkeypatch):
    """3-segment pixi specs must fingerprint parts[2] (env), not parts[1] (project).

    Repro for the reported bug: a pixi project ``wcia`` with a declared env
    ``cc-mapping`` — ``env: pixi:wcia:cc-mapping`` — must fingerprint the
    cc-mapping lock section.  The buggy code used ``parts[1]`` as env_name,
    which either fingerprinted the wrong env (silently wrong) or raised
    because the lock has no env matching the project name.
    """
    # Multi-env lock: envA has numpy, envB has pandas. No "default".
    # Bug behavior: env_name=parts[1]="myproject" is not in environments,
    #   no "default", len != 1 -> raises KeyError.
    # Correct behavior: env_name=parts[2]="envB" -> pandas fingerprinted.
    pixi_root = tmp_path / "pixi_cache"
    (pixi_root / "myproject-abc").mkdir(parents=True)
    pkgs = [
        {"name": "numpy", "version": "1.0", "build": "a",
         "hash": {"md5": "n1"}, "platform": "linux-64",
         "url": "https://ex.co/numpy-1.0-a.conda"},
        {"name": "pandas", "version": "2.0", "build": "b",
         "hash": {"md5": "p1"}, "platform": "linux-64",
         "url": "https://ex.co/pandas-2.0-b.conda"},
    ]
    lock = {
        "version": 6,
        "environments": {
            "envA": {"packages": {"linux-64": [{"conda": pkgs[0]["url"]}]}},
            "envB": {"packages": {"linux-64": [{"conda": pkgs[1]["url"]}]}},
        },
        "packages": pkgs,
    }
    _write_lock(pixi_root / "myproject-abc" / "pixi.lock", lock)

    # Point the project's wf-canvas.toml at our fake pixi_root
    (tmp_path / ".wfc").mkdir()
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text(
        f'[pixi]\nroot = "{pixi_root.as_posix()}"\n'
    )

    # Avoid real interpreter / pip freeze
    monkeypatch.setattr(
        "wfc.register.resolve_python_for_env",
        lambda *a, **k: Path("/fake/python"),
    )
    monkeypatch.setattr("wfc.env_introspect.pip_freeze", lambda _py: "")
    # capture_env_content also calls pip_freeze_best_effort, which deliberately
    # raises on a missing interpreter (a real env problem, per its docstring).
    # The fake /fake/python path is just a test shortcut, so mock it too — this
    # test exercises env *selection*, not pip-freeze behavior.
    monkeypatch.setattr(
        "wfc.env_introspect.pip_freeze_best_effort", lambda _py: ""
    )

    blob = capture_env_content("pixi:myproject:envB", tmp_path)

    assert "pandas" in blob, (
        f"envB's packages missing — 3-segment spec fingerprinted wrong env:\n{blob}"
    )
    assert "numpy" not in blob, (
        f"envA's packages present — 3-segment spec picked project name "
        f"instead of env name:\n{blob}"
    )


# =============================================================================
# pip_freeze: subprocess failure surfaces clearly
# =============================================================================

@workflow(
    purpose="pip_freeze raises RuntimeError with the stderr message on "
            "nonzero exit — silent failure would produce an empty freeze "
            "that looks identical for two different envs"
)
def test_pip_freeze_nonzero_exit_raises():
    """Mock subprocess.run to simulate pip exiting nonzero; confirm raise."""
    口 = Step(step_num=1, name="Patch subprocess.run",
             purpose="Simulate pip exiting nonzero with a useful stderr")
    failed = MagicMock(returncode=2, stdout="", stderr="ERROR: broken env\n")
    with patch("wfc.env_introspect.subprocess.run", return_value=failed):
        口 = Step(step_num=2, name="Call pip_freeze",
                 purpose="Verify RuntimeError is raised with stderr included")
        with pytest.raises(RuntimeError, match="broken env"):
            pip_freeze("/nonexistent/python")


@workflow(
    purpose="pip_freeze raises FileNotFoundError when the python interpreter "
            "binary is missing — clear error, not a cryptic subprocess trace"
)
def test_pip_freeze_missing_binary_raises():
    """Mock subprocess.run to raise FileNotFoundError; confirm it propagates."""
    with patch("wfc.env_introspect.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(FileNotFoundError, match="Python interpreter not found"):
            pip_freeze("/no/such/python")


# =============================================================================
# store_env_content: temp-file cleanup on exception
# =============================================================================

@workflow(
    purpose="store_env_content removes its temp file even when cache_file "
            "raises — no stray temp files accumulate in the system tmpdir "
            "on DVC cache failures"
)
def test_store_env_content_temp_cleanup_on_exception(tmp_path, monkeypatch):
    """Patch cache_file to raise; verify no leftover wfc-env-* temp files."""
    口 = Step(step_num=1, name="Record initial temp-file snapshot",
             purpose="Capture the set of wfc-env-* files before the call")
    import tempfile
    tmpdir = Path(tempfile.gettempdir())
    before = {p.name for p in tmpdir.glob("wfc-env-*")}

    口 = Step(step_num=2, name="Patch cache_file to raise",
             purpose="Force the error path inside store_env_content")
    def boom(*a, **kw):
        raise RuntimeError("cache write failed")

    monkeypatch.setattr("wfc.version.cache_file", boom, raising=False)
    # Also patch the symbol inside store_env_content's local import
    import wfc.provenance
    monkeypatch.setattr(wfc.provenance, "cache_file", boom)

    口 = Step(step_num=3, name="Call store_env_content and verify cleanup",
             purpose="RuntimeError must propagate, but no temp file may leak")
    with pytest.raises(RuntimeError, match="cache write failed"):
        store_env_content("some env blob content", tmp_path)

    after = {p.name for p in tmpdir.glob("wfc-env-*")}
    leaked = after - before
    assert not leaked, f"Leaked temp files: {leaked}"


# =============================================================================
# store_env_content: blob retrievable from DVC cache
# =============================================================================

@workflow(
    purpose="After store_env_content, the content blob is retrievable from "
            ".dvc/cache/files/md5/{first2}/{rest} under the returned md5 — "
            "satisfies the 'historical runs can be fully reconstructed' "
            "requirement from the request"
)
def test_store_env_content_blob_retrievable(tmp_path):
    """The md5 returned must point at a file whose content is the blob."""
    口 = Step(step_num=1, name="Store a known blob",
             purpose="Write a deterministic blob and capture the returned md5")
    blob = "env content sentinel\nnumpy==1.26.0\n"
    md5 = store_env_content(blob, tmp_path)
    assert isinstance(md5, str) and len(md5) == 32

    口 = Step(step_num=2, name="Verify DVC cache path exists",
             purpose="File at .dvc/cache/files/md5/{first2}/{rest} must contain the blob")
    cache_path = tmp_path / ".dvc" / "cache" / "files" / "md5" / md5[:2] / md5[2:]
    assert cache_path.exists()
    assert cache_path.read_text(encoding="utf-8") == blob


# =============================================================================
# build_cache_key 4-arg: env_fingerprint sensitivity
# =============================================================================

@workflow(
    purpose="build_cache_key with the 4-arg signature produces distinct keys "
            "when env_fingerprint changes and identical keys when it does not"
)
def test_build_cache_key_env_fingerprint_sensitivity():
    """Same 4 args -> same key; different env_fingerprint -> different key."""
    code_fp = "a" * 64
    params = {"x": 1}
    input_fp = "b" * 64
    env_fp_1 = "1" * 32
    env_fp_2 = "2" * 32
    k1 = build_cache_key(code_fp, params, input_fp, env_fp_1)
    k2 = build_cache_key(code_fp, params, input_fp, env_fp_1)
    k3 = build_cache_key(code_fp, params, input_fp, env_fp_2)
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64


# =============================================================================
# Legacy compatibility: NULL env_fingerprint Run rows load
# =============================================================================

@workflow(
    purpose="A Run row inserted without env_fingerprint loads cleanly from "
            "the DB — legacy rows predating this cycle keep NULL and stay "
            "readable (no migration script required)"
)
def test_legacy_null_env_fingerprint_run_loads(tmp_project):
    """Insert a Run with env_fingerprint unset; read it back and verify NULL."""
    口 = Step(step_num=1, name="Seed a minimal Module+Method and Run",
             purpose="Legacy-shaped Run row (env_fingerprint field omitted)")
    with get_session() as session:
        mod = Module(name="legacy_mod", description="legacy")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        method = Method(name="legacy_method", module_id=mod.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        # Insert without env_fingerprint (Pydantic default = None)
        run = Run(
            method_id=method.id,
            sample="s1",
            status="completed",
            cache_key="c" * 64,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    口 = Step(step_num=2, name="Read the Run back and verify NULL env_fingerprint",
             purpose="Legacy Runs must deserialize cleanly with env_fingerprint=None")
    with get_session() as session:
        loaded = session.get(Run, run_id)
    assert loaded is not None
    assert loaded.env_fingerprint is None
    assert loaded.cache_key == "c" * 64


# =============================================================================
# Integration: env change invalidates cache (US-1)
# =============================================================================

def _seed_env_method(module_name="envfp_mod", method_name="envfp_method"):
    """Seed a minimal Module + Method (env='container:demo') and return method.id."""
    with get_session() as session:
        mod = Module(name=module_name, description="env fp test")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        method = Method(
            name=method_name, module_id=mod.id, env="container:demo",
            script_path=f"methods/{method_name}/run.py",
        )
        session.add(method)
        session.commit()
        session.refresh(method)
        return method.id


def _ensure_method_source(project_dir, method_name="envfp_method"):
    method_dir = Path(project_dir) / "methods" / method_name
    method_dir.mkdir(parents=True, exist_ok=True)
    script = method_dir / f"{method_name}.py"
    if not script.exists():
        script.write_text("def main():\n    pass\n")


@workflow(
    purpose="Two otherwise-identical pre_run calls with different env content "
            "yield different env_fingerprint and different cache_key; the "
            "second call is a MISS, not a CACHED hit — US-1"
)
def test_env_change_invalidates_cache(tmp_project, monkeypatch):
    """Integration: pre_run under two 'envs' -> distinct cache_keys and MISS."""
    from wfc.cli import pre_run

    口 = Step(step_num=1, name="Seed method and source",
             purpose="Minimal method with env='container:demo'; capture_env_content is stubbed below")
    _seed_env_method()
    _ensure_method_source(tmp_project)

    口 = Step(step_num=2, name="First pre_run with stubbed env 'A'",
             purpose="Produces a NEW run; record its env_fingerprint and cache_key")
    # Stub capture_env_content so we fully control the env payload without
    # shelling out to pip.  Different return value = different env_fingerprint.
    monkeypatch.setattr(
        "wfc.version.capture_env_content",
        lambda env_spec, pd: "ENV_STATE_A\nnumpy==1.1\n",
    )
    commit = "e" * 40
    flag_1, run_id_1 = pre_run(
        method_name="envfp_method",
        module_name="envfp_mod",
        sample="s_env",
        params={"alpha": 1},
        git_commit=commit,
    )
    assert flag_1 == "NEW"
    with get_session() as session:
        run_a = session.get(Run, run_id_1)
    env_fp_a = run_a.env_fingerprint
    cache_key_a = run_a.cache_key
    assert env_fp_a is not None and len(env_fp_a) == 32

    # Mark completed so it's cache-eligible for the next call
    with get_session() as session:
        r = session.get(Run, run_id_1)
        r.status = "completed"
        session.add(r)
        session.commit()

    # Create a matching archive directory so the cache-hit check passes if
    # the keys happen to collide (they must NOT, but be defensive).
    from wfc.cli import _run_archive_dir
    _run_archive_dir(run_id_1).mkdir(parents=True, exist_ok=True)

    口 = Step(step_num=3, name="Second pre_run with stubbed env 'B'",
             purpose="Different env content -> different env_fingerprint -> MISS")
    monkeypatch.setattr(
        "wfc.version.capture_env_content",
        lambda env_spec, pd: "ENV_STATE_B\nnumpy==2.2\n",
    )
    flag_2, run_id_2 = pre_run(
        method_name="envfp_method",
        module_name="envfp_mod",
        sample="s_env",
        params={"alpha": 1},
        git_commit=commit,
    )

    口 = Step(step_num=4, name="Verify cache MISS and distinct fingerprints",
             purpose="Second run must be NEW (not CACHED); env_fingerprint "
                     "and cache_key must differ from the first run")
    assert flag_2 == "NEW"
    assert run_id_2 != run_id_1
    with get_session() as session:
        run_b = session.get(Run, run_id_2)
    assert run_b.env_fingerprint is not None
    assert run_b.env_fingerprint != env_fp_a
    assert run_b.cache_key != cache_key_a


@workflow(
    purpose="pre_run persists env_fingerprint on the cache-HIT audit Run row "
            "as well as on the MISS row — CACHED audit rows are equal "
            "provenance citizens (US-2 + US-5)"
)
def test_env_fingerprint_persisted_on_cached_audit_row(tmp_project, monkeypatch):
    """Same env on both calls -> second is CACHED; audit row has env_fingerprint set."""
    from wfc.cli import pre_run, _run_archive_dir

    _seed_env_method()
    _ensure_method_source(tmp_project)

    # Stable env content across both calls
    monkeypatch.setattr(
        "wfc.version.capture_env_content",
        lambda env_spec, pd: "STABLE_ENV\nnumpy==1.26\n",
    )

    commit = "7" * 40
    flag_1, run_id_1 = pre_run(
        method_name="envfp_method",
        module_name="envfp_mod",
        sample="s_stable",
        params={"k": 1},
        git_commit=commit,
    )
    assert flag_1 == "NEW"
    # Complete the first run and create its archive for cache eligibility
    with get_session() as session:
        r = session.get(Run, run_id_1)
        r.status = "completed"
        session.add(r)
        session.commit()
    _run_archive_dir(run_id_1).mkdir(parents=True, exist_ok=True)

    flag_2, audit_id = pre_run(
        method_name="envfp_method",
        module_name="envfp_mod",
        sample="s_stable",
        params={"k": 1},
        git_commit=commit,
    )
    assert flag_2 == "CACHED"
    # pre_run's CACHED contract now returns the audit row, not the source.
    assert audit_id != run_id_1

    with get_session() as session:
        audit = session.get(Run, audit_id)
    assert audit is not None
    assert audit.cache_source_run_id == run_id_1
    assert audit.env_fingerprint is not None
    # Same env content -> same md5
    with get_session() as session:
        origin = session.get(Run, run_id_1)
    assert audit.env_fingerprint == origin.env_fingerprint
