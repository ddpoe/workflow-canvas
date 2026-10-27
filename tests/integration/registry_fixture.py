"""A throwaway Docker registry for the integration tests that push and pull.

:func:`local_registry` starts ``registry:2`` on a free loopback port and
removes it on exit. :func:`docker_cli` and :func:`image_absent` are the two
docker calls the registry tests make around it. Shared by the env image
round trip, the cross-store test and the rerun bundle's image-lookup case.
"""
from __future__ import annotations

import contextlib
import subprocess
import uuid
from collections.abc import Iterator

#: The registry image every registry test starts.
REGISTRY_IMAGE = "registry:2"

#: The TCP forwarder that carries the registry's port into Docker Desktop's VM.
FORWARDER_IMAGE = "alpine/socat"


def _run_docker(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def docker_cli(*args: str, timeout: int = 300) -> str:
    """Run one docker command; fail the test with its stderr if it fails.

    Returns:
        The command's stdout, stripped.
    """
    proc = _run_docker(list(args), timeout)
    assert proc.returncode == 0, f"docker {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def image_absent(ref: str) -> bool:
    """Whether the local daemon has no image under *ref*."""
    return _run_docker(["image", "inspect", ref], 60).returncode != 0


def remove_images(*refs: str) -> None:
    """``docker rmi -f`` each reference, ignoring the ones already gone."""
    for ref in refs:
        _run_docker(["rmi", "-f", ref], 120)


@contextlib.contextmanager
def local_registry() -> Iterator[str]:
    """Start a throwaway ``registry:2`` on a free loopback port.

    Yields:
        ``127.0.0.1:<port>``, the registry's address for the host's Docker
        CLI and its daemon alike; Docker allows plain HTTP to any loopback
        address (``localhost`` may resolve to ``::1``, where the published
        IPv4 port does not answer). On Docker Desktop the daemon runs in a
        VM whose own loopback does not carry the published port, so a
        host-network forwarder listens there on the same port. Every
        container is removed on exit, whether the test passed or not.
    """
    suffix = uuid.uuid4().hex[:8]
    name, forwarder = f"wfc-test-registry-{suffix}", f"wfc-test-regfwd-{suffix}"
    docker_cli("run", "-d", "--name", name, "-p", "127.0.0.1::5000", REGISTRY_IMAGE,
               timeout=600)
    try:
        # "127.0.0.1:49153" -- the host port Docker chose.
        published = docker_cli("port", name, "5000/tcp").splitlines()[0]
        port = published.rsplit(":", 1)[1]
        if "Docker Desktop" in docker_cli("info", "--format", "{{.OperatingSystem}}"):
            address = docker_cli("inspect", "-f", "{{range .NetworkSettings.Networks}}"
                                 "{{.IPAddress}}{{end}}", name)
            docker_cli("run", "-d", "--name", forwarder, "--network", "host",
                       FORWARDER_IMAGE, f"TCP-LISTEN:{port},bind=127.0.0.1,fork,reuseaddr",
                       f"TCP:{address}:5000", timeout=600)
        yield f"127.0.0.1:{port}"
    finally:
        _run_docker(["rm", "-f", "-v", forwarder, name], 120)
