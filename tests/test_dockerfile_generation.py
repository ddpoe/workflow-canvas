"""Subsystem + E2E tests for Dockerfile generation.

Covers:
  - pixi Dockerfile pins its base image by digest.
  - pixi Dockerfile orders `pip install --no-deps` after `pixi install --locked`
    (the layer-ordering discipline invariant).
  - CLI `register-env --dry-run` writes the Dockerfile, prints the path, never
    invokes docker, and leaves the manifest unchanged (the non-dry-run path
    proceeds to a real docker build).

These are string-level generator assertions. Actual image buildability is owned
by tests/integration/test_pixi_dockerfile_builds.py — do not read the string
asserts here as build guarantees.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from axiom_annotations import workflow, Step

from tests.fixtures.fakes import (
    fake_subprocess_run,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_interactive_prompt,
    stub_readiness_probes,
)


VALID_FREEZE = "numpy==1.26.4\npandas==2.2.1\n"


# ---------------------------------------------------------------------------
# Tier 2: pixi generator — base-image digest pin
# ---------------------------------------------------------------------------

@workflow(purpose="Pixi-backend Dockerfile pins the build-time base image by "
                  "digest (pixi/conda are pinned).")
def test_pixi_dockerfile_pins_base_digest():
    from wfc.environments.dockerfiles.pixi import generate

    dockerfile = generate(
        env_name="image-io",
        pip_freeze_content=VALID_FREEZE,
    )

    # First FROM line must be FROM ghcr.io/prefix-dev/pixi[:tag]@sha256:<64hex>
    # — a human-readable tag is allowed, the digest pin is mandatory.
    # Skip the `# syntax=docker/dockerfile:...` BuildKit directive that
    # precedes FROM.
    first = next(
        ln for ln in dockerfile.splitlines() if ln.strip().startswith("FROM")
    )
    assert re.match(
        r"^FROM ghcr\.io/prefix-dev/pixi(:[\w][\w.\-]*)?@sha256:[0-9a-f]{64}$",
        first,
    ), first

    # BuildKit directive + at least one cache mount (layer-caching).
    assert "# syntax=docker/dockerfile:" in dockerfile
    assert "--mount=type=cache" in dockerfile


# ---------------------------------------------------------------------------
# Tier 2: pixi generator — discipline invariant
# ---------------------------------------------------------------------------

@workflow(purpose="Pixi Dockerfile orders `pixi install --locked` BEFORE the "
                  "`pip install --no-deps` layer (invariant).")
def test_pixi_dockerfile_no_deps_pip_layer_after_pixi_install():
    from wfc.environments.dockerfiles.pixi import generate

    dockerfile = generate(
        env_name="image-io",
        pip_freeze_content=VALID_FREEZE,
    )
    lines = dockerfile.splitlines()

    pixi_install_idx = next(
        (i for i, ln in enumerate(lines) if "pixi install --locked" in ln),
        None,
    )
    pip_no_deps_idx = next(
        (i for i, ln in enumerate(lines) if "pip install --no-deps" in ln),
        None,
    )

    assert pixi_install_idx is not None, "missing `pixi install --locked` line"
    assert pip_no_deps_idx is not None, "missing `pip install --no-deps` line"
    assert pip_no_deps_idx > pixi_install_idx, (
        f"pip --no-deps layer (line {pip_no_deps_idx}) must come AFTER "
        f"pixi install --locked (line {pixi_install_idx}); the invariant "
        f"is that the locked env is installed first, then the unconstrained "
        f"freeze is layered on with --no-deps."
    )

    # The recipe also ends with a chmod that opens read-permissions for the
    # --user-mismatched runtime.
    assert any("chmod -R a+rX" in ln for ln in lines), (
        "missing chmod -R a+rX line that lets a --user runtime read the env"
    )

    # BuildKit directive + at least one cache mount (layer-caching).
    assert "# syntax=docker/dockerfile:" in dockerfile
    assert "--mount=type=cache" in dockerfile


# ---------------------------------------------------------------------------
# Tier 1: pixi lock validation — register-time guard before any docker work
# ---------------------------------------------------------------------------

_SINGLE_ENV_LOCK = """\
version: 6
environments:
  default:
    packages:
      linux-64: []
"""


def test_validate_lock_rejects_env_name_missing_from_lock():
    """The env name is installed verbatim via `pixi install --environment`,
    so a name the lock does not define must fail upfront, naming the
    lock's actual environment keys."""
    from wfc.environments.dockerfiles.pixi import validate_lock_for_env

    with pytest.raises(ValueError) as exc:
        validate_lock_for_env(_SINGLE_ENV_LOCK, "myname")

    msg = str(exc.value)
    assert "myname" in msg
    assert "default" in msg


def test_validate_lock_rejects_newer_lock_format_version():
    """A lock written by a newer local pixi than the pinned in-container
    build tool fails upfront with both versions named, instead of a
    cryptic in-container pixi parse error mid-build."""
    from wfc.environments.dockerfiles.bases import PIXI_LOCK_MAX_VERSION
    from wfc.environments.dockerfiles.pixi import validate_lock_for_env

    newer = PIXI_LOCK_MAX_VERSION + 1
    lock = f"version: {newer}\nenvironments:\n  default:\n    packages: {{}}\n"

    with pytest.raises(ValueError) as exc:
        validate_lock_for_env(lock, "default")

    msg = str(exc.value)
    assert str(newer) in msg
    assert f"lock version: {PIXI_LOCK_MAX_VERSION}" in msg


def test_validate_lock_accepts_matching_env_and_tolerates_missing_version():
    """A lock that defines the requested env at a supported format version
    passes; a lock with no version key is left for the build to judge."""
    from wfc.environments.dockerfiles.pixi import validate_lock_for_env

    validate_lock_for_env(_SINGLE_ENV_LOCK, "default")
    validate_lock_for_env(
        "environments:\n  default:\n    packages: {}\n", "default"
    )


def test_validate_lock_rejects_env_without_linux_64_packages():
    """A lock made on Windows lists only win-64 unless linux-64 was added to
    the workspace; the image is linux-64, so it fails upfront naming the
    platforms the lock has and the commands that add linux-64. A lock that
    has linux-64 beside other platforms passes."""
    from wfc.environments.dockerfiles.pixi import validate_lock_for_env

    win_only = (
        "version: 6\nenvironments:\n  default:\n    packages:\n"
        "      win-64:\n      - conda: https://conda.anaconda.org/conda-forge/"
        "win-64/python-3.11.0-h.conda\n"
    )
    with pytest.raises(ValueError) as exc:
        validate_lock_for_env(win_only, "default")

    msg = str(exc.value)
    assert "linux-64" in msg
    assert "win-64" in msg
    assert "pixi workspace platform add linux-64" in msg

    validate_lock_for_env(
        "version: 6\nenvironments:\n  default:\n    packages:\n"
        "      linux-64: []\n      win-64: []\n",
        "default",
    )


# ---------------------------------------------------------------------------
# Tier 1: conda explicit-list validation — register-time platform guard
# ---------------------------------------------------------------------------

def _explicit_list(platform, header=True):
    lines = ["# This file may be used to create an environment using:"]
    if header:
        lines.append(f"# platform: {platform}")
    lines += [
        "@EXPLICIT",
        f"https://repo.anaconda.com/pkgs/main/{platform}/python-3.11.16-h_0.conda#0b03",
        "https://repo.anaconda.com/pkgs/main/noarch/tzdata-2026c-h_0.conda#1c2d",
    ]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("platform", ["win-64", "osx-arm64"])
@pytest.mark.parametrize("header", [True, False], ids=["header", "urls-only"])
def test_validate_explicit_list_rejects_non_linux_capture(platform, header):
    """An explicit list captured on Windows or macOS fails naming the
    platform and the three commands that produce a linux-64 list for the
    env being registered, whether the platform comes from the header or
    only from the package URLs."""
    from wfc.environments.dockerfiles.conda import validate_explicit_list_platform

    with pytest.raises(ValueError) as exc:
        validate_explicit_list_platform(_explicit_list(platform, header), "my-env")

    msg = str(exc.value)
    assert platform in msg
    assert "--from-history" in msg
    assert "conda-lock -f environment.yml -p linux-64 --kind explicit" in msg
    assert "wfc register-env my-env --backend conda --from conda-linux-64.lock" in msg


def test_validate_explicit_list_accepts_linux_and_unrecognized_urls():
    """A linux-64 list passes, with or without its header; a list whose URLs
    carry no recognizable platform directory is left for the build."""
    from wfc.environments.dockerfiles.conda import validate_explicit_list_platform

    validate_explicit_list_platform(_explicit_list("linux-64"), "my-env")
    validate_explicit_list_platform(_explicit_list("linux-64", header=False), "my-env")
    validate_explicit_list_platform(
        "@EXPLICIT\nhttps://x/pkg-1.0.tar.bz2\nfile:///C:/channel/pkg-2.0.conda\n",
        "my-env",
    )


# ---------------------------------------------------------------------------
# Tier 3: CLI dry-run writes Dockerfile without invoking docker
# ---------------------------------------------------------------------------

@workflow(
    purpose="On a host with no Docker, wfc register-env --dry-run writes "
            ".wfc/build/<name>/Dockerfile, prints the absolute path, exits 0, "
            "and never invokes docker; .wfc/envs.json is untouched.",
)
def test_dry_run_writes_dockerfile_no_docker_invoked(
    cli, tmp_project, monkeypatch,
):
    口 = Step(step_num=1, name="Initialize project",
             purpose="Set up .wfc/ so the CLI can locate the project root")
    from wfc.init import init_project
    # tmp_project is already a git repo (per the git_project fixture). Skip
    # the registry prompt by stubbing input() to an empty string; the test
    # does not exercise the registry path.
    stub_interactive_prompt(monkeypatch, "")
    init_project(tmp_project)

    # Snapshot the manifest before the dry-run.
    manifest_path = tmp_project / ".wfc" / "envs.json"
    pre_manifest = (
        manifest_path.read_text() if manifest_path.exists() else None
    )

    # Spy on subprocess.run to detect any "docker" invocation.
    real_run = subprocess.run
    docker_calls: list[list[str]] = []

    def _spy_run(cmd, *args, **kwargs):
        argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
        if argv and "docker" in str(argv[0]).lower():
            docker_calls.append(list(argv))
        return real_run(cmd, *args, **kwargs)

    fake_subprocess_run(monkeypatch, _spy_run)
    # A host with no Docker: the probe fails, and --dry-run never asks it.
    stub_readiness_probes(monkeypatch, docker="fail")

    口 = Step(step_num=2, name="Run register-env --dry-run",
             purpose="Should write .wfc/build/foo/Dockerfile and print the path")
    lock_path = tmp_project / "pixi.lock"
    lock_path.write_text("version: 6\n", encoding="utf-8")
    result = cli(
        "register-env", "foo",
        "--from", str(lock_path),
        "--backend", "pixi",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr

    dockerfile_path = tmp_project / ".wfc" / "build" / "foo" / "Dockerfile"
    assert dockerfile_path.exists(), (
        f"Dockerfile not written at expected path {dockerfile_path}"
    )
    # The printed path is the absolute Dockerfile path.
    assert str(dockerfile_path.resolve()) in result.stdout, result.stdout

    # Docker was never invoked.
    assert docker_calls == [], (
        f"--dry-run must not invoke docker; saw: {docker_calls}"
    )

    # Manifest unchanged.
    post_manifest = (
        manifest_path.read_text() if manifest_path.exists() else None
    )
    assert post_manifest == pre_manifest, (
        "manifest was mutated during --dry-run"
    )

    # the non-dry-run path invokes docker, so it is not
    # exercised here — see tests/test_register_env_pixi_flow.py and
    # tests/test_register_env_byo_digest_resolve.py for the full path
    # with docker_runner mocked.


@workflow(
    purpose="register-env Docker-down pre-gate reframes ONLY the daemon-down "
            "shape: with the daemon UP, a genuine docker build/run failure "
            "surfaces its RAW error, never the friendly `wfc doctor` message.",
)
def test_register_env_genuine_build_error_surfaces_raw_not_reframed(
    cli, tmp_project, monkeypatch,
):
    """Negative-boundary: the readiness reframe is shape-scoped, not a catch-all.

    The Docker-down pre-gate only fires when ``check_docker`` reports ``fail``.
    With the daemon healthy, the pre-gate passes through and a real build
    failure deeper in registration must surface verbatim — proving the reframe
    does not swallow unrelated Docker errors into the one-door message.
    """
    from wfc.init import init_project
    stub_interactive_prompt(monkeypatch, "")
    init_project(tmp_project)

    # Daemon UP: the pre-gate is a pass-through, so the build path runs.
    stub_readiness_probes(monkeypatch, git=None, docker="ok")

    # A genuine docker failure deep in registration (NOT a daemon-down shape) —
    # the kind of error the reframe must NOT swallow. Patch only the docker
    # boundary: the byo path probes with image_inspect and, on a miss, pulls.
    # Make the probe miss and the pull fail, so the REAL register() runs and
    # propagates the raw pull error through the CLI.
    raw_error = "failed to pull image: manifest for example.com/foo not found"


    def _inspect_miss(*args, **kwargs):
        raise RuntimeError("no such image locally")

    def _pull_boom(*args, **kwargs):
        raise RuntimeError(raw_error)

    stub_docker_image_inspect(monkeypatch, _inspect_miss)
    stub_docker_pull(monkeypatch, _pull_boom)

    result = cli(
        "register-env", "foo",
        "--backend", "byo",
        "--image", "docker://example.com/foo@sha256:" + "a" * 64,
    )

    assert result.returncode == 1, result.stdout
    # The RAW build error surfaces verbatim...
    assert raw_error in result.stderr, result.stderr
    # ...and is NOT reframed into the readiness one-door message.
    assert "wfc doctor" not in result.stderr, (
        "a genuine build error must not be swallowed by the run-readiness "
        f"reframe; stderr was: {result.stderr!r}"
    )
