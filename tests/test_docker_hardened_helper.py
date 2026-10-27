"""The hardened Docker helper: one flag set, a copy-out call and a run call.

The Tier 1 tests drive canned ``docker`` results through the subprocess fake
and read the argv the helper built; no daemon is reached. The integration
test copies a known file out of the pinned micromamba base.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.conftest import requires_docker
from tests.fixtures.fakes import fake_subprocess_run, refusing_process
from wfc.environments import docker as docker_runner
from wfc.environments.dockerfiles.bases import MICROMAMBA_BASE

_IMAGE = "docker.io/library/alpine@sha256:" + "a" * 64

#: Every hardened container carries these, whatever the call.
_ALWAYS = ["--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges"]


def _recording(results: dict[str, subprocess.CompletedProcess] | None = None):
    """Return a handler that records each docker argv and answers per verb.

    Args:
        results: A canned result per docker verb (``"create"``, ``"cp"``,
            ``"rm"``, ``"run"``); a verb not named exits 0 with no output.

    Returns:
        ``(calls, handler)``: the list the argv lands in, and the handler
        for :func:`fake_subprocess_run`.
    """
    calls: list[list[str]] = []

    def handler(cmd, *args, **kwargs):
        calls.append(list(cmd))
        canned = (results or {}).get(cmd[1])
        if canned is not None:
            return subprocess.CompletedProcess(cmd, canned.returncode,
                                               canned.stdout, canned.stderr)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return calls, handler


def _flags_of(argv: list[str], image: str) -> list[str]:
    """Return the options between the verb and the image in *argv*."""
    return argv[2:argv.index(image)]


@pytest.mark.parametrize("network", [False, True], ids=["network-off", "network-on"])
def test_run_hardened_argv_carries_the_flag_set_and_opt_in_network(
        monkeypatch, network):
    """``docker run --rm`` carries the hardened flags, mounts nothing, and
    has ``--network none`` unless the caller asks for the network."""
    calls, handler = _recording({"run": subprocess.CompletedProcess(
        [], 0, "pkg 1.0\n", "")})
    fake_subprocess_run(monkeypatch, handler, only="docker")

    out = docker_runner.run_hardened(_IMAGE, ["pip", "list"], network=network)

    assert out == "pkg 1.0\n"
    [argv] = calls
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[-3:] == [_IMAGE, "pip", "list"]
    flags = _flags_of(argv, _IMAGE)
    for flag in _ALWAYS:
        assert flag in flags
    assert ("--network" in flags) is not network
    if not network:
        assert flags[flags.index("--network") + 1] == "none"
    assert not {"-v", "--volume", "--mount"} & set(flags)


def test_copy_out_creates_unstarted_container_and_removes_it_when_cp_fails(
        monkeypatch, tmp_path):
    """``docker create`` carries the same flag set with the network off, the
    container is never started, and a failing ``docker cp`` still removes it
    and surfaces Docker's stderr."""
    calls, handler = _recording({"cp": subprocess.CompletedProcess(
        [], 1, "", "Error: Could not find the file /opt/missing")})
    fake_subprocess_run(monkeypatch, handler, only="docker")

    with pytest.raises(RuntimeError, match="Could not find the file /opt/missing"):
        docker_runner.copy_out(_IMAGE, "/opt/missing", dest_dir=tmp_path)

    verbs = [argv[1] for argv in calls]
    assert verbs == ["create", "cp", "rm"]
    create, cp, rm = calls
    flags = _flags_of(create, _IMAGE)
    for flag in _ALWAYS:
        assert flag in flags
    assert flags[flags.index("--network") + 1] == "none"
    name = flags[flags.index("--name") + 1]
    assert cp[2] == f"{name}:/opt/missing"
    assert rm == ["docker", "rm", "--force", name]
    assert "start" not in verbs


def test_copy_out_returns_the_copied_path_and_removes_the_container(
        monkeypatch, tmp_path):
    """A successful copy returns the host path under the destination named
    after the container path's last part, and removes the container."""
    calls: list[list[str]] = []

    def handler(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[1] == "cp":
            (tmp_path / "packages.txt").write_text("@EXPLICIT\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    fake_subprocess_run(monkeypatch, handler, only="docker")

    copied = docker_runner.copy_out(_IMAGE, "/opt/packages.txt", dest_dir=tmp_path)

    assert copied == tmp_path / "packages.txt"
    assert [argv[1] for argv in calls] == ["create", "cp", "rm"]


def test_a_bare_image_id_is_a_pinned_ref(monkeypatch, tmp_path):
    """A locally built image's ID, ``sha256:<64 hex>``, is content-addressed
    and is what both image stores resolve; copy_out hands it to Docker as is."""
    image_id = "sha256:" + "b" * 64
    calls, handler = _recording()
    fake_subprocess_run(monkeypatch, handler, only="docker")

    with pytest.raises(FileNotFoundError):
        # The fake cp copies nothing, so measuring the copy fails after the
        # create; the ref has passed the guard and reached Docker.
        docker_runner.copy_out(image_id, "/opt/list.txt", dest_dir=tmp_path)

    create = calls[0]
    assert create[1] == "create"
    assert create[-2:] == [image_id, "/wfc-copy-out-never-starts"]
    assert calls[-1][:3] == ["docker", "rm", "--force"]


@pytest.mark.parametrize("image", [
    "docker.io/library/alpine:3.20",
    "docker.io/library/alpine",
    "docker.io/library/alpine@sha256:abc",
    "sha256:abc",
])
def test_a_ref_without_a_digest_is_refused_before_docker(monkeypatch, image):
    """A tag-only (or malformed digest) ref is refused naming the ref, and
    Docker is never called."""
    fake_subprocess_run(monkeypatch, refusing_process("docker must not run"),
                        only="docker")

    with pytest.raises(ValueError, match="digest-pinned"):
        docker_runner.run_hardened(image, ["true"])
    with pytest.raises(ValueError, match="digest-pinned"):
        docker_runner.copy_out(image, "/etc/hostname")


@pytest.mark.integration
@requires_docker
def test_copy_out_reads_a_known_file_from_the_pinned_micromamba_base(tmp_path):
    """A real ``docker create`` / ``docker cp`` / ``docker rm`` copies the
    base's os-release out, and leaves no container behind."""
    copied = docker_runner.copy_out(MICROMAMBA_BASE, "/usr/lib/os-release",
                                    dest_dir=tmp_path)

    assert copied == tmp_path / "os-release"
    assert "ID=" in copied.read_text(encoding="utf-8")
    left = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter",
         f"name={docker_runner.HARDENED_NAME_PREFIX}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    assert left.stdout.strip() == ""
