"""Tier 1 behaviour tests for ADR-019 Cycle H container-only enforcement.

Two new behaviours this cycle adds (D-5: enforcement over verification — these
are the only two new tests):

- US-2: ``run_step`` with an env that has NO container record (unknown name /
  removed ``inherit`` / a non-container record) exits non-zero with a message
  naming the env and pointing at ``wfc register-env <name>``, AND writes the
  failure outcome sidecar. No silent host fallback.
- US-3: a method contract with no ``env`` (or ``env: inherit``) is rejected at
  parse time with the "must name a built container env" error; it does not run.

Both tests are unmarked (no Docker) and run under the default ``pytest`` suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# =============================================================================
# US-2: non-resolving env errors loudly at runtime
# =============================================================================

def _setup_no_container_project(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal wfc project whose env has no container record.

    Returns:
        ``(project_dir, pipeline_json_path)``.
    """
    (tmp_path / ".wfc").mkdir()
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text(
        "[project]\nname=\"t\"\n[database]\nurl=\"sqlite:///:memory:\"\n"
    )
    # No envs.json -> envs.get returns None for any name (non-resolving env).
    method_dir = tmp_path / "methods" / "ml_plain"
    method_dir.mkdir(parents=True)
    (method_dir / "ml_plain.py").write_text("# stub\n")
    # method.yaml names a container-style env so parse-time validation passes
    # and we exercise the RUNTIME backstop (the env simply has no record).
    (method_dir / "method.yaml").write_text("executor: local\nenv: image-io\n")

    pj = tmp_path / "pipeline.json"
    pj.write_text(json.dumps({
        "nodes": [{
            "id": "n1", "method": "ml_plain", "module": "test",
            "env": "image-io", "script": str(method_dir / "ml_plain.py"),
        }],
        "links": [], "param_sets": {},
    }))
    return tmp_path, pj


def _patch_runtime(monkeypatch, project_dir: Path) -> None:
    """Stub pre_run / complete_run / get_project_root / get_session so run_step
    reaches the container-resolution branch without a real DB."""
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_dir))
    # wfc.database caches project_root() at module level; reset so this test's
    # project_dir is honoured regardless of test ordering (other tests in the
    # suite set WFC_PROJECT_ROOT to their own tmp dirs).
    from wfc.database import reset_engine
    reset_engine()
    from wfc import cli as cli_mod

    monkeypatch.setattr(cli_mod, "pre_run", lambda **kw: ("NEW", 42))
    monkeypatch.setattr(cli_mod, "complete_run", lambda **kw: None)
    monkeypatch.setattr(cli_mod, "get_project_root", lambda: project_dir)
    monkeypatch.setattr(cli_mod, "runs_dir", lambda: project_dir / ".runs")
    monkeypatch.setattr(cli_mod, "resolve_input", lambda **kw: None)

    class _NullSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def exec(self, *a, **kw):
            class _R:
                def first(self): return None
            return _R()
        def add(self, *a, **kw): pass
        def commit(self): pass
    monkeypatch.setattr(cli_mod, "get_session", lambda: _NullSession())


def test_run_step_no_container_env_errors_loudly(tmp_path, monkeypatch, capsys):
    """US-2: run_step on an env with no container record exits non-zero,
    names the env, points at ``wfc register-env``, and writes the failure
    outcome sidecar — no host fallback."""
    proj, pj = _setup_no_container_project(tmp_path)
    _patch_runtime(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(proj / "ref.txt")],
    )

    # Non-zero exit (no silent host fallback).
    assert rc == 1

    err = capsys.readouterr().err
    # Message names the offending env and points at the build command.
    assert "image-io" in err
    assert "wfc register-env" in err

    # Failure outcome sidecar was written so pipeline-summary aggregation
    # and the run row stay consistent.
    outcome_path = (
        proj / ".runs" / "pipelines" / "p1" / "outcomes"
        / "n1__s1__default.json"
    )
    assert outcome_path.exists(), f"outcome sidecar not written at {outcome_path}"
    outcome = json.loads(outcome_path.read_text())
    assert outcome["status"] == "failed"


# =============================================================================
# US-3: missing / inherit env rejected at parse-time validation
# =============================================================================

def test_parse_method_yaml_rejects_missing_env(tmp_path):
    """US-3: a method.yaml with no ``env`` is rejected with the
    'must name a built container env' error; it does not run."""
    from wfc.contracts import parse_method_yaml

    method_dir = tmp_path / "no_env"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text(
        "executor: local\ninputs: {}\noutputs: {}\n"
    )

    with pytest.raises(ValueError, match="must name a built container env"):
        parse_method_yaml(method_dir)


def test_parse_method_yaml_rejects_inherit_env(tmp_path):
    """US-3: the removed ``env: inherit`` keyword is rejected with the same
    'must name a built container env' error (it now reads as a non-built env)."""
    from wfc.contracts import parse_method_yaml

    method_dir = tmp_path / "inherit_env"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text("executor: local\nenv: inherit\n")

    with pytest.raises(ValueError, match="must name a built container env"):
        parse_method_yaml(method_dir)
