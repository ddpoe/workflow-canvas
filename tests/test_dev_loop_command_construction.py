"""Tier 2 + Tier 1 tests for dev-loop command construction.

Covers:
- ``wfc shell`` and ``wfc exec`` reuse
  :func:`wfc.environments.argv.build_docker_command` so the bind-mount,
  ``--user``, and ``-w /work`` discipline matches ``wfc run-step`` exactly.
- ``executor = "slurm"`` triggers a clean "cluster Apptainer dispatch is
  not supported" error and a non-zero exit code, with no docker invocation.
- ``--help`` output for each of ``wfc jupyter``, ``wfc shell``, and
  ``wfc exec`` includes the ephemeral-container reminder sentence.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    FAKE_PUSHED_DIGEST,
    fake_subprocess_run,
    stub_docker_build,
    stub_docker_command_builder,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_push,
    stub_docker_tag,
)
from wfc.environments.docker import ImageNotFoundError

BASE = "ghcr.io/owner/repo"
VALID_DIGEST = "a" * 64
#: The env is a locally built pixi env, recorded as
#: docker://local/image-io@sha256:<image ID>; Docker is handed its daemon
#: ref, the bare image ID.
DAEMON_REF = f"sha256:{VALID_DIGEST}"


@pytest.fixture(autouse=True)
def _fresh_resolver():
    """Each test resolves its own tree: the canonical resolver caches per process."""
    from wfc.persistence import reset_engine

    reset_engine()
    yield
    reset_engine()


def _setup_project(project: Path, *, executor: str | None = None) -> Path:
    """Register the test's env in a ``tmp_project`` and set its executor.

    Args:
        project: The ``tmp_project`` fixture's root (``wfc init``'s project,
            already active).
        executor: Executor type appended to init's config, or ``None`` to
            keep init's default.

    Returns:
        The project root.
    """
    if executor is not None:
        config = project / ".wfc" / "wf-canvas.toml"
        with config.open("a", encoding="utf-8") as fh:
            fh.write(f'\n[executor]\ntype = "{executor}"\n')
    # A pixi env built locally: the record production writes names the
    # local/<name> repo with the image ID in the digest slot.
    from tests.fixtures.conftest import write_env_record
    write_env_record(project, "image-io", backend="pixi", digest=VALID_DIGEST)
    return project


# ---------------------------------------------------------------------------
# shell + exec reuse build_docker_command
# ---------------------------------------------------------------------------

@workflow(purpose="wfc shell and wfc exec both delegate argv construction to "
                  "wfc.environments.argv.build_docker_command so bind-mount, "
                  "--user, and -w /work discipline matches wfc run-step")
def test_dev_loop_reuses_container_runner_helper(tmp_project, monkeypatch):
    proj = _setup_project(tmp_project)
    # The daemon holds the env's image, so nothing is pulled.
    stub_docker_image_inspect(monkeypatch, DAEMON_REF)

    sentinel_argv = [
        "docker", "run", "--rm",
        "--user", "1000:1000",
        "-v", "/proj:/work", "-w", "/work",
        "-v", "/dvc:/dvc-cache",
        DAEMON_REF,
        # Inner argv gets appended by the helper from the caller's input;
        # tests check it landed correctly via the spy below.
    ]
    calls: list[dict] = []

    def _fake_build(image_ref, project_root, dvc_cache_dir, inner_argv,
                    *, uid, gid, gpus=False):
        calls.append({
            "image_ref": image_ref,
            "project_root": Path(project_root),
            "dvc_cache_dir": Path(dvc_cache_dir),
            "inner_argv": list(inner_argv),
            "uid": uid,
            "gid": gid,
            "gpus": gpus,
        })
        return list(sentinel_argv) + list(inner_argv)

    captured_subprocess: list[list[str]] = []

    class _FakeResult:
        returncode = 0

    def _fake_run(argv, check=False):  # noqa: ARG001
        captured_subprocess.append(list(argv))
        return _FakeResult()

    fake_subprocess_run(monkeypatch, _fake_run)
    with stub_docker_command_builder(_fake_build):
        from wfc.environments import dev_loop

        rc_shell = dev_loop.shell("image-io")
        rc_exec = dev_loop.exec_("image-io", ["python", "-c", "print(1)"])

    assert rc_shell == 0
    assert rc_exec == 0
    assert len(calls) == 2, "build_docker_command must be called once per verb"

    # Shell call: image ref + project root + dvc cache + sh fallback inner.
    shell_call = calls[0]
    assert shell_call["image_ref"] == DAEMON_REF
    assert shell_call["project_root"] == proj.resolve()
    assert shell_call["dvc_cache_dir"] == (proj / ".dvc" / "cache").resolve() \
        or shell_call["dvc_cache_dir"] == proj / ".dvc" / "cache"
    assert shell_call["inner_argv"][0] == "sh"
    assert "bash" in " ".join(shell_call["inner_argv"])

    # Exec call: same image/root, user's literal cmd as inner_argv.
    exec_call = calls[1]
    assert exec_call["image_ref"] == DAEMON_REF
    assert exec_call["inner_argv"] == ["python", "-c", "print(1)"]

    # The argv handed to subprocess.run must include the per-verb
    # injected flags (-it for shell, -i for exec) spliced after --rm.
    assert len(captured_subprocess) == 2
    shell_argv = captured_subprocess[0]
    exec_argv = captured_subprocess[1]
    assert "-it" in shell_argv
    assert "-i" in exec_argv
    assert "-it" not in exec_argv  # exec uses -i only (no TTY)

    # Bind-mount + --user from the helper's return must pass through.
    for argv in (shell_argv, exec_argv):
        assert "--user" in argv
        assert "-w" in argv
        # bind-mounts: at least one -v with :/work and one with :/dvc-cache
        joined = " ".join(argv)
        assert ":/work" in joined
        assert ":/dvc-cache" in joined


@pytest.mark.parametrize("pushed", [True, False], ids=["pushed", "never-pushed"])
@workflow(purpose="wfc shell and wfc exec on a pixi env whose image the "
                  "Docker daemon lacks ask ensure_runnable before the docker "
                  "run argv is built: a pushed image is pulled once by its "
                  "registry ref, checked, and that ref is run with --pull "
                  "never; a never-pushed one is refused naming `wfc "
                  "register-env <env> --force` and nothing runs; nothing is "
                  "ever built and the record is unchanged")
def test_dev_loop_pulls_or_refuses_a_missing_env_before_the_argv(
    tmp_project, monkeypatch, capsys, pushed,
):
    from wfc import environments as envs_mod

    stub_docker_build(monkeypatch, None)
    content = {"layers": ["sha256:layer"], "env": [], "user": "", "workdir": "/"}
    stub_docker_image_content(monkeypatch, content)
    stub_docker_image_inspect(monkeypatch, DAEMON_REF)
    record = envs_mod.register(
        name="image-io", backend="pixi",
        source={"pixi_lock_content":
                    "version: 6\nenvironments:\n  image-io:\n    packages: {}\n",
                "pixi_toml_content": '[project]\nname = "image-io"\n',
                "pip_freeze_content": "numpy==1.26.4\n"},
        project_dir=tmp_project, push=False,
    )
    registry_ref = f"{BASE}/image-io@{FAKE_PUSHED_DIGEST}"
    if pushed:
        from wfc.persistence import set_registry_url

        set_registry_url(tmp_project, BASE)
        stub_docker_tag(monkeypatch, None)
        stub_docker_push(monkeypatch, None)
        envs_mod.push_image(tmp_project, "image-io", record.env_fingerprint,
                            record.container, BASE)

    held: set = set()
    pulls: list[str] = []

    def inspect(ref):
        # The registered image is gone; only a pulled ref is held.
        if ref in held:
            return "sha256:" + "f" * 64
        raise ImageNotFoundError(f"Error: No such image: {ref}")

    stub_docker_image_inspect(monkeypatch, inspect)
    stub_docker_image_content(monkeypatch, content)
    stub_docker_pull(monkeypatch, lambda ref: (pulls.append(ref), held.add(ref)))
    stub_docker_build(monkeypatch, AssertionError("built in the dev loop"))

    docker_runs: list[list[str]] = []

    class _FakeResult:
        returncode = 0

    def _run(argv, check=False):  # noqa: ARG001
        docker_runs.append(list(argv))
        return _FakeResult()

    fake_subprocess_run(monkeypatch, _run)
    from wfc.environments import dev_loop

    rc_shell = dev_loop.shell("image-io")
    rc_exec = dev_loop.exec_("image-io", ["python", "-V"])

    assert envs_mod.get("image-io", tmp_project) == record
    if not pushed:
        assert (rc_shell, rc_exec) == (1, 1)
        assert pulls == [] and docker_runs == []
        assert "wfc register-env image-io --force" in capsys.readouterr().err
        return
    assert (rc_shell, rc_exec) == (0, 0)
    # Pulled once; the second verb finds the registry ref held.
    assert pulls == [registry_ref]
    assert len(docker_runs) == 2
    for argv in docker_runs:
        assert argv[argv.index("--pull") + 1] == "never"
        assert registry_ref in argv
        assert DAEMON_REF not in argv


# ---------------------------------------------------------------------------
# slurm executor carve-out
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["jupyter", "shell", "exec"])
def test_slurm_executor_carve_out_errors(tmp_project, monkeypatch, capsys, verb):
    """Under executor=slurm, all three dev-loop verbs exit non-zero with a
    clear 'cluster Apptainer dispatch is not supported' message and never
    invoke docker."""
    _setup_project(tmp_project, executor="slurm")

    called = {"docker": False}

    def _fake_run(argv, check=False):  # noqa: ARG001
        called["docker"] = True
        class _R:
            returncode = 0
        return _R()

    fake_subprocess_run(monkeypatch, _fake_run)
    from wfc.environments import dev_loop
    if verb == "jupyter":
        rc = dev_loop.jupyter("image-io")
    elif verb == "shell":
        rc = dev_loop.shell("image-io")
    else:
        rc = dev_loop.exec_("image-io", ["echo", "x"])

    assert rc == 1
    assert not called["docker"], (
        "dev-loop must not spawn docker under executor=slurm"
    )
    err = capsys.readouterr().err
    assert "cluster Apptainer dispatch (executor=slurm) is not supported" in err


# ---------------------------------------------------------------------------
# --help text includes the ephemeral-container reminder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["jupyter", "shell", "exec"])
def test_dev_loop_help_includes_ephemeral_reminder(verb, capsys):
    """Tier 1: rendered --help output for each verb mentions the
    ephemeral-container discipline (container fresh per invocation;
    in-session changes including pip install do not persist)."""
    from wfc.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([verb, "--help"])
    out = capsys.readouterr().out

    # Load-bearing concepts (not exact wording): the help text must
    # mention both "fresh" / "ephemeral" and the persistence-warning idea
    # ("pip" + "not persist" or equivalent).
    lowered = out.lower()
    assert "fresh" in lowered or "ephemeral" in lowered, (
        f"--help for {verb} must mention the container is spawned fresh "
        f"per invocation, got:\n{out}"
    )
    assert "pip" in lowered, (
        f"--help for {verb} must mention pip-install discipline, got:\n{out}"
    )
    assert "persist" in lowered or "carry" in lowered, (
        f"--help for {verb} must mention that changes do not persist into "
        f"pipeline runs, got:\n{out}"
    )


# ---------------------------------------------------------------------------
# Resolution error paths (Tier 1)
# ---------------------------------------------------------------------------

def test_dev_loop_errors_when_env_not_registered(tmp_project, capsys):
    _setup_project(tmp_project)
    from wfc.environments import dev_loop
    rc = dev_loop.shell("nonexistent")
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_dev_loop_errors_when_no_wfc_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .wfc/
    from wfc.environments import dev_loop
    # The canonical resolver walks up to the filesystem root, so a marker
    # above tmp_path would mask the no-project case. Pinning the override at
    # the bare tmp_path makes the resolver validate it and refuse — the
    # no-project condition, deterministically.
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    from wfc.persistence import reset_engine
    reset_engine()
    rc = dev_loop.shell("image-io")
    assert rc == 1
    err = capsys.readouterr().err
    assert "no wfc project" in err.lower() or "wfc init" in err.lower()
