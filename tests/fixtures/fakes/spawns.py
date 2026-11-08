"""Process boundary: every ``subprocess.Popen`` spawn, recorded by program.

``wfc.environments.docker.build`` and every git command (through
``wfc.version``'s runner) spawn with ``Popen``; a test of what was spawned
records both while the docker build gets a canned client and git runs for
real.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from ._entry import fake

#: ``subprocess.Popen`` as it was before any fake.
_REAL_POPEN = subprocess.Popen


class _BuildClient:
    """A ``docker build`` client that streams its lines and exits 0."""

    def __init__(self, lines: list[str]) -> None:
        self.returncode = 0
        self.stdout = iter(f"{line}\n" for line in lines)

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout=None) -> int:
        return self.returncode


@dataclass
class Spawns:
    """The argv of each spawn, by program: ``docker build`` and git."""

    docker_builds: list[list[str]] = field(default_factory=list)
    git: list[list[str]] = field(default_factory=list)


@fake(
    boundary="subprocess.Popen -- the docker build client "
             "(wfc.environments.docker.build) and every git command "
             "(wfc.version's runner); any other spawn runs for real",
    preserves="the argv each caller handed to Popen (recorded), git's real "
              "answers, and the build's streamed output and exit status",
    not_proven="that an image is built",
    backed_by="pm_mvp::tests.integration.test_pixi_dockerfile_builds::"
              "test_generated_pixi_dockerfile_builds_and_records_live_interpreter",
)
def record_spawns(monkeypatch, *, build_output: tuple[str, ...] = (
        "#1 [1/2] FROM base", "#2 DONE 0.1s")) -> Spawns:
    """Record every spawn; answer ``docker build`` with a canned client.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        build_output: The lines each build streams.

    Returns:
        The :class:`Spawns`, filled as the test runs.
    """
    spawns = Spawns()

    def popen(cmd, *args, **kwargs):
        argv = [str(a) for a in cmd]
        if argv[:2] == ["docker", "build"]:
            spawns.docker_builds.append(argv)
            return _BuildClient(list(build_output))
        if argv[:1] == ["git"]:
            spawns.git.append(argv)
        return _REAL_POPEN(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return spawns
