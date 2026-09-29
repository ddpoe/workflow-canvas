"""Tier 1 behaviour tests for container-only enforcement.

Two behaviours (enforcement over verification):

- ``run_step`` with an env that has NO container record (unknown name /
  ``inherit`` / a non-container record) exits non-zero with a message
  naming the env and pointing at ``wfc register-env <name>``, AND writes the
  failure outcome sidecar. No silent host fallback.
- a method contract with no ``env`` (or ``env: inherit``) is rejected at
  parse time with the "must name a built container env" error; it does not run.

Both tests are unmarked (no Docker) and run under the default ``pytest`` suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.fakes import stub_runtime_phases


# =============================================================================
# Non-resolving env errors loudly at runtime
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


def test_run_step_no_container_env_errors_loudly(tmp_path, monkeypatch, capsys):
    """run_step on an env with no container record exits non-zero,
    names the env, points at ``wfc register-env``, and writes the failure
    outcome sidecar — no host fallback."""
    proj, pj = _setup_no_container_project(tmp_path)
    stub_runtime_phases(monkeypatch, proj)

    from wfc.execution import run_step
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

    # A failure outcome sidecar was written, so the pipeline-end walk can
    # record the refused target as a failed row.
    outcome_path = (
        proj / ".runs" / "pipelines" / "p1" / "outcomes"
        / "n1__s1__default.json"
    )
    assert outcome_path.exists(), f"outcome sidecar not written at {outcome_path}"
    outcome = json.loads(outcome_path.read_text())
    assert outcome["status"] == "failed"


# =============================================================================
# Missing / inherit env rejected at parse-time validation
# =============================================================================

def test_parse_method_yaml_rejects_missing_env(tmp_path):
    """A method.yaml with no ``env`` is rejected with the
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
    """``env: inherit`` is not a keyword: it is rejected with the same
    'must name a built container env' error, as a non-built env."""
    from wfc.contracts import parse_method_yaml

    method_dir = tmp_path / "inherit_env"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text("executor: local\nenv: inherit\n")

    with pytest.raises(ValueError, match="must name a built container env"):
        parse_method_yaml(method_dir)
