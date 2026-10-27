"""Tier 3 integration test: an image pushed from one Docker image store runs on the other.

Docker keeps images in one of two stores: the classic (graph driver) store
and the containerd store. The same image has a different image ID in each,
and the classic store re-serialises a manifest it pushes, so wfc pulls and
runs an env image by its registry ref and checks it by content, never by a
recomputed ID. This test pushes an image from the host's daemon to a
throwaway ``registry:2`` and pulls it into a privileged ``docker:dind``
daemon that uses the other store, through the production pull-and-check
(``wfc.environments.runnable.pull_and_check``) with ``DOCKER_HOST`` pointed
at that daemon. The pulled image renders the host's env fingerprint and runs
by its registry ref with ``--pull never``.

The dind daemon shares the registry container's network namespace, so both
daemons reach the registry as ``127.0.0.1:<port>`` (a loopback registry needs
no TLS) and the ref is the same on both sides.

``integration``-marked and Linux only (CI's ``integration`` job); skipped
where a privileged ``docker:dind`` daemon cannot start. Every container and
image it creates is removed.
"""
from __future__ import annotations

import json
import socket
import sys
import time
import uuid
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.integration.registry_fixture import REGISTRY_IMAGE
from tests.integration.test_pinned_method_version_runs import _build_image, _run

pytestmark = [
    pytest.mark.integration,
    requires_docker,
    pytest.mark.skipif(not sys.platform.startswith("linux"),
                       reason="Docker-in-Docker cross-store test runs on Linux "
                              "only (CI's integration job)"),
]

#: The dind image: Engine 28, whose default store is the classic one.
DIND_IMAGE = "docker:28-dind"

#: The port dockerd listens on inside a dind container started without TLS.
_DIND_TCP_PORT = 2375

#: Seconds to wait for the dind daemon to answer.
_DIND_START_S = 120


def _free_port() -> int:
    """Return a loopback TCP port nothing listens on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _docker(*args: str, host: str | None = None, timeout: int = 300):
    """Run one docker command against the host daemon, or *host*."""
    prefix = ["docker", "-H", host] if host else ["docker"]
    return _run([*prefix, *args], Path.cwd(), timeout=timeout)


def _ok(proc) -> str:
    assert proc.returncode == 0, f"{proc.args}: {proc.stderr}"
    return proc.stdout.strip()


def _is_containerd_store(host: str | None = None) -> bool:
    """Whether a daemon keeps its images in the containerd store."""
    status = _ok(_docker("info", "--format", "{{json .DriverStatus}}", host=host))
    return "io.containerd.snapshotter" in status


@workflow(purpose="An image pushed from the host's Docker image store to a "
                  "registry:2 is pulled by its registry ref into a docker:dind "
                  "daemon on the other store through the production pull and "
                  "content check: it renders the host's env fingerprint and "
                  "runs by that ref with --pull never")
def test_an_image_pushed_from_one_store_runs_on_the_other(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    """A pushed image is pulled, checked and run on the other image store."""
    from wfc.environments import docker as docker_runner
    from wfc.environments.fingerprint import content_fingerprint
    from wfc.environments.runnable import pull_and_check

    suffix = uuid.uuid4().hex[:8]
    registry, dind = f"wfc-test-xreg-{suffix}", f"wfc-test-dind-{suffix}"
    tag = f"local/wfc-test-xstore-{suffix}:one"
    registry_port, dind_port = _free_port(), _free_port()
    dind_host = f"tcp://127.0.0.1:{dind_port}"
    host_containerd = _is_containerd_store()
    images: list[str] = [tag]
    try:
        口 = Step(step_num=1, name="Start the registry and a dind daemon on the other store",
                 purpose="registry:2 listens on the same port inside and out; "
                         "dind joins its network namespace, listens on tcp "
                         "without TLS, and uses the store the host does not")
        # Both ports on loopback only: the dind daemon's is unauthenticated.
        _ok(_docker("run", "-d", "--name", registry,
                    "-e", f"REGISTRY_HTTP_ADDR=0.0.0.0:{registry_port}",
                    "-p", f"127.0.0.1:{registry_port}:{registry_port}",
                    "-p", f"127.0.0.1:{dind_port}:{_DIND_TCP_PORT}",
                    REGISTRY_IMAGE, timeout=600))
        dind_args = ["run", "-d", "--privileged", "--name", dind,
                     "--network", f"container:{registry}",
                     "-e", "DOCKER_TLS_CERTDIR="]
        if not host_containerd:
            # Engine 28 defaults to the classic store; the host uses it too.
            config = tmp_path / "daemon.json"
            config.write_text(json.dumps(
                {"features": {"containerd-snapshotter": True}}), encoding="utf-8")
            dind_args += ["-v", f"{config}:/etc/docker/daemon.json:ro"]
        started = _docker(*dind_args, DIND_IMAGE, timeout=600)
        if started.returncode != 0:
            pytest.skip(f"a privileged {DIND_IMAGE} daemon cannot start here: "
                        f"{started.stderr.strip()}")
        deadline = time.monotonic() + _DIND_START_S
        while _docker("info", host=dind_host, timeout=30).returncode != 0:
            if time.monotonic() > deadline:
                logs = _docker("logs", "--tail", "40", dind).stderr
                pytest.skip(f"the {DIND_IMAGE} daemon did not answer in "
                            f"{_DIND_START_S} s:\n{logs}")
            time.sleep(1)
        assert _is_containerd_store(dind_host) != host_containerd

        口 = Step(step_num=2, name="Push from the host",
                 purpose="Build a small image, fingerprint its content, tag "
                         "and push it with wfc's docker wrappers")
        _build_image(tmp_path / "image", tag, "xstore")
        fingerprint = content_fingerprint(docker_runner.image_content(tag))
        target = f"127.0.0.1:{registry_port}/xstore/env:fp-{fingerprint[:12]}"
        images.append(target)
        docker_runner.tag(tag, target)
        digest = docker_runner.push(target)
        assert digest, "docker push reported no digest"
        ref = f"127.0.0.1:{registry_port}/xstore/env@{digest}"

        口 = Step(step_num=3, name="Pull and check on the other store",
                 purpose="pull_and_check with DOCKER_HOST at the dind daemon: "
                         "the pulled content renders the host's fingerprint")
        monkeypatch.setenv("DOCKER_HOST", dind_host)
        monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
        pull_and_check("xstore", fingerprint, ref)

        口 = Step(step_num=4, name="Run the pulled image by its registry ref",
                 purpose="docker run --pull never <ref> on the dind daemon")
        ran = _ok(_docker("run", "--rm", "--pull", "never", ref, "python", "-c",
                          "import os; print(os.environ['WFC_PIN_IMAGE'])",
                          host=dind_host))
        assert ran == "xstore"
    finally:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        _docker("rm", "-f", "-v", dind, timeout=120)
        _docker("rm", "-f", "-v", registry, timeout=120)
        for name in images:
            _docker("rmi", "-f", name, timeout=120)
