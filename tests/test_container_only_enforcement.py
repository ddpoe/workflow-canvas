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

import subprocess

import pytest

from tests.harness import Scenario, build_project, chain, drive_target

T1 = ("n1", "s1", "default")


# =============================================================================
# Non-resolving env errors loudly at runtime
# =============================================================================

def test_run_step_no_container_env_errors_loudly(git_project, monkeypatch, capsys):
    """run_step on an env with no container record exits non-zero,
    names the env, points at ``wfc register-env``, and writes the failure
    outcome sidecar — no host fallback.
    """
    from wfc.environments import delete

    scn = Scenario(nodes=chain("n1"))
    env = scn.env_name
    # The method registers in the harness's built env; its record then
    # leaves .wfc/envs.json through the manifest's own remover (as a merge
    # or a hand edit would: `wfc delete-env` refuses an env a version records).
    project = build_project(scn, root=git_project, monkeypatch=monkeypatch)
    delete(env, project.root)
    subprocess.run(["git", "commit", "-q", "-am", f"delete env {env}"],
                   cwd=project.root, check=True, capture_output=True)
    capsys.readouterr()
    obs = drive_target(project, "n1", monkeypatch=monkeypatch)

    # Non-zero exit (no silent host fallback).
    assert obs.exit_code(T1) == 1

    err = capsys.readouterr().err
    # Message names the offending env and points at the build command.
    assert f"wfc register-env {env}" in err

    # A failure outcome sidecar was written, so the pipeline-end walk can
    # record the refused target as a failed row.
    outcome = obs.outcomes.get(T1)
    assert outcome is not None and outcome["status"] == "failed"


# =============================================================================
# Missing / inherit env rejected at parse-time validation
# =============================================================================

def test_parse_method_yaml_rejects_missing_env(tmp_path):
    """A method.yaml with no ``env`` is rejected with the
    'must name a built container env' error; it does not run.
    """
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
    'must name a built container env' error, as a non-built env.
    """
    from wfc.contracts import parse_method_yaml

    method_dir = tmp_path / "inherit_env"
    method_dir.mkdir()
    (method_dir / "method.yaml").write_text("executor: local\nenv: inherit\n")

    with pytest.raises(ValueError, match="must name a built container env"):
        parse_method_yaml(method_dir)
