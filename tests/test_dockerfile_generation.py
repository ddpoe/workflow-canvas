"""Subsystem + E2E tests for ADR-019 Cycle B Dockerfile generation.

Covers (per Architect test plan):
  - US-2 Tier 2: pixi Dockerfile pins base by digest.
  - US-3 Tier 2: pixi Dockerfile orders `pip install --no-deps` after
    `pixi install --locked` (discipline invariant from ADR-019
    §dockerfile-generation).
  - US-1+5 Tier 3: CLI `register-env --dry-run` writes the Dockerfile,
    prints the path, never invokes docker, and the manifest is unchanged.
    Non-dry-run path errors with the Cycle-C-deferred message.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from axiom_annotations import workflow, Step


VALID_FREEZE = "numpy==1.26.4\npandas==2.2.1\n"


# ---------------------------------------------------------------------------
# Tier 2: pixi generator — base-image digest pin (US-2)
# ---------------------------------------------------------------------------

@workflow(purpose="Pixi-backend Dockerfile pins the build-time base image by "
                  "digest (ADR-019 decision #11 — pixi/conda are pinned).")
def test_pixi_dockerfile_pins_base_digest():
    from wfc.dockerfiles.pixi import generate

    dockerfile = generate(
        env_name="image-io",
        pixi_lock_path=Path("/proj/pixi.lock"),
        pip_freeze_content=VALID_FREEZE,
    )

    # First FROM line must be FROM ghcr.io/prefix-dev/pixi@sha256:<64hex>.
    # Skip the `# syntax=docker/dockerfile:...` BuildKit directive that
    # precedes FROM (ADR-019 §dockerfile-generation 2026-05-17 amendment).
    first = next(
        ln for ln in dockerfile.splitlines() if ln.strip().startswith("FROM")
    )
    assert re.match(
        r"^FROM ghcr\.io/prefix-dev/pixi@sha256:[0-9a-f]{64}$",
        first,
    ), first

    # BuildKit directive + at least one cache mount (ADR-019 layer-caching).
    assert "# syntax=docker/dockerfile:" in dockerfile
    assert "--mount=type=cache" in dockerfile


# ---------------------------------------------------------------------------
# Tier 2: pixi generator — discipline invariant (US-3)
# ---------------------------------------------------------------------------

@workflow(purpose="Pixi Dockerfile orders `pixi install --locked` BEFORE the "
                  "`pip install --no-deps` layer (ADR-019 §dockerfile-generation "
                  "invariant).")
def test_pixi_dockerfile_no_deps_pip_layer_after_pixi_install():
    from wfc.dockerfiles.pixi import generate

    dockerfile = generate(
        env_name="image-io",
        pixi_lock_path=Path("/proj/pixi.lock"),
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
        f"pixi install --locked (line {pixi_install_idx}); ADR-019 invariant "
        f"is that the locked env is installed first, then the unconstrained "
        f"freeze is layered on with --no-deps."
    )

    # The recipe also ends with a chmod that opens read-permissions for the
    # --user-mismatched runtime (ADR-019 decision #9 pair).
    assert any("chmod -R a+rX" in ln for ln in lines), (
        "missing chmod -R a+rX line that pairs with the --user runtime fix"
    )

    # BuildKit directive + at least one cache mount (ADR-019 layer-caching).
    assert "# syntax=docker/dockerfile:" in dockerfile
    assert "--mount=type=cache" in dockerfile


# ---------------------------------------------------------------------------
# Tier 3: CLI dry-run writes Dockerfile; non-dry-run errors (US-1 + US-5)
# ---------------------------------------------------------------------------

@workflow(
    purpose="wfc register-env --dry-run writes .wfc/build/<name>/Dockerfile, "
            "prints the absolute path, exits 0, and never invokes docker; "
            ".wfc/envs.json is untouched.",
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
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
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

    monkeypatch.setattr(subprocess, "run", _spy_run)

    口 = Step(step_num=2, name="Run register-env --dry-run",
             purpose="Should write .wfc/build/foo/Dockerfile and print the path")
    result = cli(
        "register-env", "foo",
        "--backend", "pixi",
        "--pixi-env", "foo",
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

    # Cycle C: the non-dry-run path now invokes docker, so we don't
    # exercise it here — see tests/test_register_env_pixi_flow.py and
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
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")
    init_project(tmp_project)

    # Daemon UP: the pre-gate is a pass-through, so the build path runs.
    from wfc import preflight
    monkeypatch.setattr(
        preflight, "check_docker",
        lambda *a, **k: preflight.CheckResult("docker", "ok", "ok"),
    )

    # A genuine `docker build` failure deep in registration (NOT a daemon-down
    # shape) — the kind of error the reframe must NOT swallow.
    raw_error = "failed to build image: layer sha256:deadbeef not found"

    import wfc.envs as envs_mod

    def _boom(*args, **kwargs):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(envs_mod, "register", _boom)

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
