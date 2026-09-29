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

from tests.fixtures.fakes import fake_subprocess_run, stub_docker_command_builder


VALID_DIGEST = "a" * 64
CONTAINER_REF_DOCKER = (
    f"docker://ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"
)
CONTAINER_REF_BARE = (
    f"ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"
)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    """Each test resolves its own tree: the canonical resolver caches per process."""
    from wfc.persistence import reset_engine

    reset_engine()
    yield
    reset_engine()


def _setup_project(tmp_path: Path, *, executor: str | None = None) -> Path:
    (tmp_path / ".wfc").mkdir()
    toml = '[project]\nname="t"\n[database]\nurl="sqlite:///:memory:"\n'
    if executor is not None:
        toml += f'[executor]\ntype="{executor}"\n'
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text(toml)
    # byo record: a registry image attached by digest is what production
    # writes for a docker-registry ref like this one.
    from tests.fixtures.conftest import write_env_record
    write_env_record(tmp_path, "image-io", image="ghcr.io/dante/image-io",
                     digest=VALID_DIGEST)
    return tmp_path


# ---------------------------------------------------------------------------
# shell + exec reuse build_docker_command
# ---------------------------------------------------------------------------

@workflow(purpose="wfc shell and wfc exec both delegate argv construction to "
                  "wfc.environments.argv.build_docker_command so bind-mount, "
                  "--user, and -w /work discipline matches wfc run-step")
def test_dev_loop_reuses_container_runner_helper(tmp_path, monkeypatch):
    proj = _setup_project(tmp_path)
    monkeypatch.chdir(proj)

    sentinel_argv = [
        "docker", "run", "--rm",
        "--user", "1000:1000",
        "-v", "/proj:/work", "-w", "/work",
        "-v", "/dvc:/dvc-cache",
        CONTAINER_REF_BARE,
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
    assert shell_call["image_ref"] == CONTAINER_REF_BARE
    assert shell_call["project_root"] == proj.resolve()
    assert shell_call["dvc_cache_dir"] == (proj / ".dvc" / "cache").resolve() \
        or shell_call["dvc_cache_dir"] == proj / ".dvc" / "cache"
    assert shell_call["inner_argv"][0] == "sh"
    assert "bash" in " ".join(shell_call["inner_argv"])

    # Exec call: same image/root, user's literal cmd as inner_argv.
    exec_call = calls[1]
    assert exec_call["image_ref"] == CONTAINER_REF_BARE
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


# ---------------------------------------------------------------------------
# slurm executor carve-out
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["jupyter", "shell", "exec"])
def test_slurm_executor_carve_out_errors(tmp_path, monkeypatch, capsys, verb):
    """Under executor=slurm, all three dev-loop verbs exit non-zero with a
    clear 'cluster Apptainer dispatch is not supported' message and never
    invoke docker."""
    proj = _setup_project(tmp_path, executor="slurm")
    monkeypatch.chdir(proj)

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

def test_dev_loop_errors_when_env_not_registered(tmp_path, monkeypatch, capsys):
    proj = _setup_project(tmp_path)
    monkeypatch.chdir(proj)
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
