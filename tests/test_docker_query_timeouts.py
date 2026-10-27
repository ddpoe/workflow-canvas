"""Docker queries are bounded; transfers are not.

A daemon query (``image_inspect``, ``image_content``, ``repo_digest``,
``tag``) passes :data:`DOCKER_QUERY_TIMEOUT_S` and turns an expiry into a
``DockerTimeoutError``, never an ``ImageNotFoundError``. ``read_manifest``
passes :data:`REGISTRY_READ_TIMEOUT_S` and answers unreachable without
raising. ``pull`` and ``push`` run with no timeout. Faked at the subprocess
boundary, docker only.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures.fakes import canned_process, fake_subprocess_run
from wfc.environments import docker as docker_runner

REF = "local/tools@sha256:" + "a" * 64


def _expiring(seen: list):
    """A docker handler that records the timeout asked for, then expires."""
    def handler(cmd, *args, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
    return handler


@pytest.mark.parametrize("call", [
    lambda: docker_runner.image_inspect(REF),
    lambda: docker_runner.image_content(REF),
    lambda: docker_runner.repo_digest(REF, "local/tools"),
    lambda: docker_runner.tag(REF, "ghcr.io/o/r/tools:fp-000000000000"),
], ids=["image_inspect", "image_content", "repo_digest", "tag"])
def test_a_daemon_query_that_expires_is_docker_not_answering(monkeypatch, call):
    seen: list = []
    fake_subprocess_run(monkeypatch, _expiring(seen), only="docker")

    with pytest.raises(docker_runner.DockerTimeoutError) as exc:
        call()

    assert seen == [docker_runner.DOCKER_QUERY_TIMEOUT_S]
    assert not isinstance(exc.value, docker_runner.ImageNotFoundError)
    assert "did not finish within 15 s: Docker is not answering" in str(exc.value)


def test_a_registry_read_that_expires_answers_unreachable(monkeypatch):
    seen: list = []
    fake_subprocess_run(monkeypatch, _expiring(seen), only="docker")

    answer = docker_runner.read_manifest("ghcr.io/o/r/tools:fp-000000000000")

    assert seen == [docker_runner.REGISTRY_READ_TIMEOUT_S]
    assert answer.state == docker_runner.MANIFEST_UNREACHABLE
    assert "30 s" in answer.detail


@pytest.mark.parametrize("call", [
    lambda: docker_runner.pull(REF),
    lambda: docker_runner.push("ghcr.io/o/r/tools:fp-000000000000"),
], ids=["pull", "push"])
def test_a_transfer_runs_unbounded(monkeypatch, call):
    seen: list = []

    def handler(cmd, *args, **kwargs):
        seen.append(("timeout" in kwargs, kwargs.get("timeout")))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    fake_subprocess_run(monkeypatch, handler, only="docker")
    call()
    assert seen == [(True, None)]


@pytest.mark.parametrize(("ref", "insecure"), [
    ("localhost:5000/o/tools:fp-0", True),
    ("127.0.0.1:5000/o/tools:fp-0", True),
    ("localhost.example.org/o/tools:fp-0", False),
    ("127.0.0.1.nip.io/o/tools:fp-0", False),
])
def test_only_a_loopback_registry_is_read_over_plain_http(monkeypatch, ref, insecure):
    argvs: list = []

    def handler(cmd, *args, **kwargs):
        argvs.append(cmd)
        return canned_process(stdout="{}")(cmd)

    fake_subprocess_run(monkeypatch, handler, only="docker")
    docker_runner.read_manifest(ref)
    assert ("--insecure" in argvs[0]) is insecure
