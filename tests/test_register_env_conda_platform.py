"""Subsystem tests: registering a conda env captured on a non-Linux host.

A conda explicit list names exact package files for the host's platform,
and the image is linux-64, so registration refuses a Windows or macOS list
before any Docker work and prints commands that produce a linux-64 list.
The second test runs the printed solve command for real and registers what
it produces.
"""

from __future__ import annotations

import shlex
import subprocess

import pytest

from axiom_annotations import workflow

from tests.conftest import requires_conda_lock
from tests.fixtures.fakes import stub_docker_build, stub_docker_image_inspect

_WIN_LIST = (
    "# platform: win-64\n@EXPLICIT\n"
    "https://repo.anaconda.com/pkgs/main/win-64/python-3.11.16-hb00fc5c_0.conda#1a2b\n"
    "https://repo.anaconda.com/pkgs/main/win-64/vc14_runtime-14.44.35208-h_0.conda#3c4d\n"
)


def _printed_commands(message: str) -> list[str]:
    """Return the indented command lines of a platform refusal message."""
    return [ln.strip() for ln in message.splitlines() if ln.startswith("    ")]


@workflow(
    purpose="register-env --from a conda explicit list captured on Windows "
            "fails before any docker work: the error names win-64 and the "
            "commands that produce a linux-64 list, no Dockerfile is staged, "
            "no docker build runs, and no manifest entry is written"
)
def test_register_env_refuses_windows_conda_list_before_docker(tmp_path, monkeypatch):
    from wfc import environments as envs_mod
    from wfc.environments.verbs import _stage_from_path

    (tmp_path / ".wfc").mkdir()
    build_calls = []
    stub_docker_build(monkeypatch, lambda dockerfile_dir, tag: build_calls.append(tag))
    stub_docker_image_inspect(monkeypatch, "sha256:" + "c" * 64)

    captured = tmp_path / "explicit-win.txt"
    captured.write_text(_WIN_LIST, encoding="utf-8")
    source = _stage_from_path(backend="conda", from_path=captured)

    with pytest.raises(ValueError) as exc:
        envs_mod.register(name="cl-env", backend="conda", source=source, project_dir=tmp_path)

    msg = str(exc.value)
    assert "win-64" in msg
    commands = _printed_commands(msg)
    assert commands[-1] == "wfc register-env cl-env --backend conda --from conda-linux-64.lock"

    assert build_calls == []
    assert not (tmp_path / ".wfc" / "build" / "cl-env").exists()
    assert not (tmp_path / ".wfc" / "envs.json").exists()


@pytest.mark.slow
@requires_conda_lock
@workflow(
    purpose="The conda-lock command the Windows refusal prints solves a "
            "package-name spec to a linux-64 explicit list, and registering "
            "that list passes the platform check and stages it for the image "
            "build (docker stubbed; conda-lock reaches conda-forge)"
)
def test_printed_conda_lock_command_produces_a_registrable_linux_list(tmp_path, monkeypatch):
    from wfc import environments as envs_mod
    from wfc.environments.dockerfiles.conda import validate_explicit_list_platform
    from wfc.environments.verbs import _stage_from_path

    # The refusal's printed commands, taken from the message itself so the
    # test follows the instructions a user actually sees.
    with pytest.raises(ValueError) as exc:
        validate_explicit_list_platform(_WIN_LIST, "cl-env")
    solve_command = next(c for c in _printed_commands(str(exc.value)) if c.startswith("conda-lock "))

    # What `conda env export --from-history` writes for `python=3.11`.
    (tmp_path / "environment.yml").write_text(
        "name: cl-test\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        shlex.split(solve_command), cwd=tmp_path, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stderr
    linux_list = tmp_path / "conda-linux-64.lock"
    text = linux_list.read_text(encoding="utf-8")
    assert "# platform: linux-64" in text
    assert "/linux-64/python-3.11" in text

    (tmp_path / ".wfc").mkdir()
    build_calls = []
    stub_docker_build(monkeypatch, lambda dockerfile_dir, tag: build_calls.append(tag))
    stub_docker_image_inspect(monkeypatch, "sha256:" + "d" * 64)

    envs_mod.register(
        name="cl-env",
        backend="conda",
        source=_stage_from_path(backend="conda", from_path=linux_list),
        project_dir=tmp_path,
    )

    assert build_calls == ["local/cl-env:_wfc-build"]
    staged = tmp_path / ".wfc" / "build" / "cl-env" / "explicit-list.txt"
    assert staged.read_text(encoding="utf-8") == text
