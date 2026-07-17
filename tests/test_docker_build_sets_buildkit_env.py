"""Subsystem test: docker_runner.build sets DOCKER_BUILDKIT=1 and preserves env.

This is the one test that mocks ``subprocess.run`` directly — it covers the
subprocess boundary itself. All other Cycle C tests mock the
``wfc.docker_runner`` functions instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from axiom_annotations import workflow


@workflow(
    purpose="docker_runner.build invokes subprocess.run with "
            "DOCKER_BUILDKIT=1 merged INTO a copy of os.environ — "
            "caller env vars (PATH, HOME, etc.) survive the merge so the "
            "docker spawn finds binaries and respects HTTP_PROXY/DOCKER_HOST"
)
def test_docker_build_sets_buildkit_env(monkeypatch, tmp_path):
    """Mock subprocess.run; assert env kwarg has BuildKit set AND
    preserves a sentinel env var the caller has in os.environ.
    """
    from wfc import docker_runner

    # Plant a sentinel in os.environ so we can verify it survived the merge.
    monkeypatch.setenv("WFC_TEST_SENTINEL", "preserved")

    captured = {}

    def fake_run(cmd, env=None, capture_output=False, text=False, check=False,
                 **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    docker_runner.build(tmp_path, "myimage:tag")

    assert captured["cmd"][:3] == ["docker", "build", "-t"]
    assert captured["cmd"][3] == "myimage:tag"
    assert captured["cmd"][4] == str(tmp_path)

    env = captured["env"]
    assert env is not None, "env kwarg must be passed, not None"
    assert env.get("DOCKER_BUILDKIT") == "1", \
        "DOCKER_BUILDKIT=1 must be set so the dockerfile:1.4 syntax works"
    assert env.get("WFC_TEST_SENTINEL") == "preserved", \
        "caller's env vars must survive the merge (env={...} alone would strip them)"


def test_docker_build_raises_runtimeerror_on_nonzero(monkeypatch, tmp_path):
    """Non-zero docker exit must raise RuntimeError with stderr surfaced."""
    from wfc import docker_runner

    def fake_run(cmd, env=None, capture_output=False, text=False, check=False,
                 **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="COPY failed: file not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="COPY failed"):
        docker_runner.build(tmp_path, "myimage:tag")
