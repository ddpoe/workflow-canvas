"""
Tests for env_fingerprint provenance.

Covers:
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

import pytest
from sqlmodel import select

from axiom_annotations import workflow, Step

from wfc.persistence import get_session
from wfc.environments.introspect import (
    conda_list_explicit,
)
from wfc.environments import get as _envs_get
from wfc.persistence import Method, MethodVersion, Module, Run, Sample
# seed_sample_row, not create_sample_csv, deliberately: these tests compose
# cache keys, where the row's content_hash IS the input under test.
from tests.conftest import seed_sample_row
from tests.fixtures.conftest import write_env_record
from tests.fixtures.fakes import stub_cache_writers
from wfc.identity import build_cache_key
from wfc.storage import store_env_content
from wfc.environments.fingerprint import capture_env_content
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

    # store_env_content imports cache_file at call time; patch its home.
    stub_cache_writers(monkeypatch, cache_file=boom)

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
            "so a historical run's env can be fully reconstructed"
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
# build_cache_key: env_fingerprint sensitivity
# =============================================================================

@workflow(
    purpose="build_cache_key produces distinct keys "
            "when env_fingerprint changes and identical keys when it does not"
)
def test_build_cache_key_env_fingerprint_sensitivity():
    """Same 4 args -> same key; different env_fingerprint -> different key."""
    code_fp = "a" * 64
    params = {"x": 1}
    input_fp = "b" * 64
    env_fp_1 = "1" * 32
    env_fp_2 = "2" * 32
    k1 = build_cache_key(code_fp, params, input_fp, env_fp_1, "envfp_mod.envfp_method")
    k2 = build_cache_key(code_fp, params, input_fp, env_fp_1, "envfp_mod.envfp_method")
    k3 = build_cache_key(code_fp, params, input_fp, env_fp_2, "envfp_mod.envfp_method")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64


# =============================================================================
# Legacy compatibility: NULL env_fingerprint Run rows load
# =============================================================================

@workflow(
    purpose="A Run row inserted without env_fingerprint loads cleanly from "
            "the DB — a row with a NULL env_fingerprint stays readable "
            "(no migration script required)"
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
# Integration: env change invalidates cache
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
    # The registered copy's contract is the other half of the method's code
    # identity, so a registered copy without one is refused at fingerprint
    # time. It is held fixed here: this module drives the ENV axis.
    contract = method_dir / "method.yaml"
    if not contract.exists():
        contract.write_text(
            "env: demo\n"
            "inputs:\n  data:\n    required: false\n"
            "outputs:\n  result:\n    type: .csv\n"
        )


@workflow(
    purpose="Two otherwise-identical pre_run calls with different env content "
            "yield different env_fingerprint and different cache_key; the "
            "second call is a MISS, not a CACHED hit"
)
def test_env_change_invalidates_cache(tmp_project, monkeypatch):
    """Integration: pre_run under two 'envs' -> distinct cache_keys and MISS."""
    from wfc.execution.claim import pre_run

    口 = Step(step_num=1, name="Seed method, source, and a real 'demo' env",
             purpose="Method env='container:demo' resolves through the real manifest "
                     "branch — env_fingerprint comes from the registered record verbatim")
    _seed_env_method()
    _ensure_method_source(tmp_project)
    seed_sample_row("s_env")
    # Register a real container env 'demo' so resolve_env_fingerprint reads its
    # precomputed env_fingerprint verbatim (the manifest short-circuit).
    # Digest 'a' == env state A;
    # a different digest below == env state B and yields a different fingerprint.
    write_env_record(tmp_project, "demo", digest="a" * 64)
    demo_fp_a = _envs_get("demo", tmp_project).env_fingerprint

    口 = Step(step_num=2, name="First pre_run under real env 'A'",
             purpose="Produces a NEW run; record its env_fingerprint and cache_key")
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
    # The persisted fingerprint is the manifest's value, byte-for-byte.
    assert env_fp_a == demo_fp_a

    # Mark completed so it's cache-eligible for the next call
    with get_session() as session:
        r = session.get(Run, run_id_1)
        r.status = "completed"
        session.add(r)
        session.commit()

    # Create a matching archive directory so the cache-hit check passes if
    # the keys happen to collide (they must NOT, but be defensive).
    from wfc.persistence import project_root as get_project_root
    from wfc.layout import run_archive_dir
    run_archive_dir(get_project_root(), run_id_1).mkdir(parents=True, exist_ok=True)

    口 = Step(step_num=3, name="Re-register env 'B' and second pre_run",
             purpose="A different env digest -> different env_fingerprint -> MISS")
    # Overwrite 'demo' with a new digest == env state B. The manifest branch now
    # resolves a different env_fingerprint, so the second run must MISS.
    write_env_record(tmp_project, "demo", digest="b" * 64)
    demo_fp_b = _envs_get("demo", tmp_project).env_fingerprint
    assert demo_fp_b != demo_fp_a
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
    # The second run's fingerprint is env B's manifest value, verbatim.
    assert run_b.env_fingerprint == demo_fp_b


@workflow(
    purpose="pre_run persists env_fingerprint on the cache-HIT audit Run row "
            "as well as on the MISS row — CACHED audit rows are equal "
            "provenance citizens"
)
def test_env_fingerprint_persisted_on_cached_audit_row(tmp_project, monkeypatch):
    """Same env on both calls -> second is CACHED; audit row has env_fingerprint set."""
    from wfc.execution.claim import pre_run
    from wfc.persistence import project_root as get_project_root
    from wfc.layout import run_archive_dir

    _seed_env_method()
    _ensure_method_source(tmp_project)
    seed_sample_row("s_stable")

    # Register a real 'demo' env; both calls resolve the same manifest
    # env_fingerprint verbatim, so the second is a CACHED hit and the audit
    # row carries that same fingerprint.
    write_env_record(tmp_project, "demo", digest="a" * 64)
    demo_fp = _envs_get("demo", tmp_project).env_fingerprint

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
    run_archive_dir(get_project_root(), run_id_1).mkdir(parents=True, exist_ok=True)

    flag_2, audit_id = pre_run(
        method_name="envfp_method",
        module_name="envfp_mod",
        sample="s_stable",
        params={"k": 1},
        git_commit=commit,
    )
    assert flag_2 == "CACHED"
    # pre_run's CACHED contract returns the audit row, not the source.
    assert audit_id != run_id_1

    with get_session() as session:
        audit = session.get(Run, audit_id)
    assert audit is not None
    assert audit.cache_source_run_id == run_id_1
    assert audit.env_fingerprint is not None
    # The audit row carries the manifest's env_fingerprint verbatim.
    assert audit.env_fingerprint == demo_fp
    # Same env content -> same md5
    with get_session() as session:
        origin = session.get(Run, run_id_1)
    assert audit.env_fingerprint == origin.env_fingerprint
