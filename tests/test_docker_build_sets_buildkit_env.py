"""Subsystem test: docker_runner.build sets DOCKER_BUILDKIT=1 and preserves env.

This is the one test file that mocks the subprocess boundary directly — it
covers the spawn itself (now ``subprocess.Popen``, streaming). All other
Cycle C tests mock the ``wfc.docker_runner`` functions instead.
"""

from __future__ import annotations

import io
import subprocess

import pytest

from axiom_annotations import workflow


class _FakePopen:
    """Minimal Popen stand-in: canned output stream + exit code."""

    def __init__(self, returncode: int, output: str = ""):
        self.returncode = returncode
        self.stdout = io.StringIO(output)

    def poll(self):
        return self.returncode


@workflow(
    purpose="docker_runner.build invokes subprocess.Popen with "
            "DOCKER_BUILDKIT=1 merged INTO a copy of os.environ — "
            "caller env vars (PATH, HOME, etc.) survive the merge so the "
            "docker spawn finds binaries and respects HTTP_PROXY/DOCKER_HOST"
)
def test_docker_build_sets_buildkit_env(monkeypatch, tmp_path):
    """Mock subprocess.Popen; assert env kwarg has BuildKit set AND
    preserves a sentinel env var the caller has in os.environ.
    """
    from wfc import docker_runner

    # Plant a sentinel in os.environ so we can verify it survived the merge.
    monkeypatch.setenv("WFC_TEST_SENTINEL", "preserved")

    captured = {}

    def fake_popen(cmd, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakePopen(returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

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
    """Non-zero docker exit must raise RuntimeError with the build-output
    tail surfaced (the real error text, not a wrapped CalledProcessError).
    """
    from wfc import docker_runner

    output = "#4 [2/3] COPY app /app\nCOPY failed: file not found\n"
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda cmd, env=None, **kwargs: _FakePopen(returncode=1, output=output),
    )

    with pytest.raises(RuntimeError, match="COPY failed"):
        docker_runner.build(tmp_path, "myimage:tag")


@workflow(
    purpose="the spinner's step display parses BuildKit plain-progress "
            "markers — plain and named-stage forms — and ignores "
            "non-marker lines"
)
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("#7 [3/5] RUN pip install matplotlib", (3, 5)),
        ("#7 [stage-1 2/4] COPY . /src", (2, 4)),
        ("#7 0.512 Collecting matplotlib", None),
        ("#7 DONE 1.2s", None),
        ("random noise", None),
    ],
)
def test_parse_build_step(line, expected):
    from wfc.docker_runner import _parse_build_step

    assert _parse_build_step(line) == expected
