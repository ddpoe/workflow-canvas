"""Subsystem tests: an older env manifest is refused, and re-registering converts it.

A version-1 ``.wfc/envs.json`` holds fingerprints taken from each image's
name; a version-2 one holds content fingerprints but no captures folder
behind them. Every reader refuses either with one message naming each env's
re-register command; the register path alone opens it and writes it back
as the current version, fingerprinting and capturing the registered env and
clearing the fingerprint of every other env until it is re-registered too.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from axiom_annotations import workflow

from tests.fixtures.fakes import (
    FAKE_IMAGE_CONTENT,
    fake_subprocess_run,
    redirect_home,
    refusing_process,
    stub_demo_docker_probe,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_docker_pull,
    stub_docker_repo_digest,
    stub_readiness_probes,
    stub_server_bind,
)

from wfc.reserved import DEMO_ENV_NAME as DEMO_ENV

USER_ENV = "image-io"
USER_LINE = f"wfc register-env {USER_ENV} --force"
DEMO_LINE = "wfc demo --force"
OLDER = 2


def _write_older_manifest(project_dir: Path, version: int = OLDER) -> dict:
    """Write an older ``.wfc/envs.json`` holding a user env and the demo env.

    Declared fixture deviation: the file is written raw, because no
    production path writes a version-1 or version-2 manifest any more. Its
    records have the shape those versions recorded, with no captures folder
    behind any fingerprint. The project is a real one (marker included) with
    the cwd inside it, so the CLI's project resolver finds it; an
    initialised project keeps the marker ``wfc init`` wrote.

    Args:
        project_dir: Project root; ``.wfc/`` is created if absent.
        version: The ``schema_version`` to write (1 or 2).

    Returns:
        The manifest as written.
    """
    state = project_dir / ".wfc"
    state.mkdir(exist_ok=True)
    marker = state / "wf-canvas.toml"
    if not marker.exists():
        marker.write_text('[project]\nname = "t"\n')
    manifest = {
        "schema_version": version,
        "envs": {
            USER_ENV: {
                "backend": "byo",
                "source": "docker://ghcr.io/lab/tools:0.9",
                "container": "docker://ghcr.io/lab/tools@sha256:" + "1" * 64,
                "env_fingerprint": "11" * 16,
                "built_from_lock": None,
                "built_at": "2026-05-01T00:00:00Z",
                "python": "python",
            },
            DEMO_ENV: {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": f"docker://local/{DEMO_ENV}@sha256:" + "2" * 64,
                "env_fingerprint": "22" * 16,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-02T00:00:00Z",
                "source_fingerprint": "33" * 16,
                "python": "/opt/env/bin/python",
            },
        },
    }
    (state / "envs.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.fixture
def older_project(tmp_path, monkeypatch):
    """A project whose env manifest is version 2, with the cwd inside it."""
    manifest = _write_older_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)
    from wfc.persistence import reset_engine
    reset_engine()
    return tmp_path, manifest


@pytest.mark.parametrize("version", [1, 2])
@workflow(
    purpose="A version-1 or version-2 env manifest holding a user env and the "
            "demo env is refused by load_manifest, list_envs, "
            "resolve_env_fingerprint and wfc list-envs, each with one message "
            "naming the version and every env's re-register command (wfc "
            "register-env <name> --force; wfc demo --force for the demo env); "
            "the file is left as it was"
)
def test_every_reader_refuses_an_older_manifest_naming_each_env(
        tmp_path, monkeypatch, capsys, version):
    from wfc.cli import cli_main
    from wfc.environments import (
        list_envs,
        load_manifest,
        resolve_env_fingerprint,
    )
    from wfc.persistence import reset_engine

    project_dir = tmp_path
    _write_older_manifest(project_dir, version)
    monkeypatch.chdir(project_dir)
    reset_engine()
    path = project_dir / ".wfc" / "envs.json"
    before = path.read_bytes()

    def assert_names_every_env(message: str) -> None:
        assert f"schema_version {version}" in message
        assert USER_LINE in message
        assert DEMO_LINE in message

    for read in (load_manifest, list_envs):
        with pytest.raises(ValueError) as exc:
            read(project_dir)
        assert_names_every_env(str(exc.value))

    with pytest.raises(ValueError) as exc:
        resolve_env_fingerprint(USER_ENV, project_dir)
    assert_names_every_env(str(exc.value))

    rc = cli_main(["list-envs"])
    assert rc == 1
    assert_names_every_env(capsys.readouterr().err)

    assert path.read_bytes() == before


@workflow(
    purpose="On a version-2 env manifest, wfc register-env <name> --force for "
            "one byo env (Docker faked) succeeds and writes version 3: that "
            "env gets the content fingerprint and a captures folder, the demo "
            "env keeps every field but a cleared env_fingerprint, and a run "
            "resolving the demo env is refused as missing its fingerprint, "
            "naming wfc demo --force, until it is re-registered"
)
def test_re_registering_one_env_converts_an_older_manifest(
        older_project, monkeypatch):
    from wfc.cli import cli_main
    from wfc.environments import (
        capture_exists,
        load_manifest,
        resolve_env_fingerprint,
    )
    from wfc.environments.fingerprint import capture_env_content

    project_dir, old = older_project
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_inspect(monkeypatch, "sha256:" + "f" * 64)
    stub_docker_repo_digest(monkeypatch, lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_pull(monkeypatch)
    stub_docker_image_content(monkeypatch)

    rc = cli_main([
        "register-env", USER_ENV, "--backend", "byo",
        "--image", "docker://ghcr.io/lab/tools:1.0", "--force",
    ])
    assert rc == 0

    manifest = load_manifest(project_dir)
    assert manifest["schema_version"] == 3
    expected = hashlib.md5(
        capture_env_content(FAKE_IMAGE_CONTENT).encode("utf-8")).hexdigest()
    assert manifest["envs"][USER_ENV]["env_fingerprint"] == expected
    assert resolve_env_fingerprint(USER_ENV, project_dir) == expected
    assert capture_exists(USER_ENV, expected, project_dir)

    demo = manifest["envs"][DEMO_ENV]
    assert demo["env_fingerprint"] == ""
    assert {k: v for k, v in demo.items() if k != "env_fingerprint"} == {
        k: v for k, v in old["envs"][DEMO_ENV].items() if k != "env_fingerprint"
    }

    with pytest.raises(ValueError, match="fingerprint is missing") as exc:
        resolve_env_fingerprint(DEMO_ENV, project_dir)
    assert DEMO_LINE in str(exc.value)
    assert "no container env by that name" not in str(exc.value)


@workflow(
    purpose="On an initialised project whose env manifest is version 2, wfc "
            "demo --force (Docker faked) tears the demo down and re-registers "
            "it without a WARNING quoting the older-version refusal, and "
            "leaves the manifest at version 3 with the demo env fingerprinted "
            "and captured and the user env's fingerprint cleared"
)
def test_demo_force_converts_an_older_manifest_without_a_warning(
        tmp_path, monkeypatch, capsys):
    from wfc.cli import cli_main
    from wfc.environments import capture_exists, load_manifest
    from wfc.init import init_project

    redirect_home(monkeypatch, tmp_path / "home")
    target = tmp_path / "proj"
    init_project(target, assume_yes=True)
    _write_older_manifest(target)

    # Every Docker entry point the demo reaches is faked; a docker command
    # that slips past them fails the test rather than reaching a daemon.
    fake_subprocess_run(monkeypatch, refusing_process(
        "wfc demo --force must not reach docker"), only="docker")
    stub_demo_docker_probe(monkeypatch, "ok")
    monkeypatch.setenv("WFC_DEMO_IMAGE", "local/wfc-demo-env:test")
    stub_readiness_probes(monkeypatch, docker="ok")
    stub_docker_image_inspect(monkeypatch, "sha256:" + "f" * 64)
    stub_docker_repo_digest(monkeypatch, lambda ref, repository: "sha256:" + "a" * 64)
    stub_docker_pull(monkeypatch)
    stub_docker_image_content(monkeypatch)
    stub_server_bind(monkeypatch, started=[], where="demo")
    capsys.readouterr()

    rc = cli_main(["demo", "--dir", str(target), "--no-open", "--force"])

    err = capsys.readouterr().err
    assert rc == 0, err
    assert f"schema_version {OLDER}" not in err
    assert "could not read the env manifest" not in err

    manifest = load_manifest(target)
    assert manifest["schema_version"] == 3
    demo_fp = manifest["envs"][DEMO_ENV]["env_fingerprint"]
    assert demo_fp
    assert capture_exists(DEMO_ENV, demo_fp, target)
    assert manifest["envs"][USER_ENV]["env_fingerprint"] == ""


@workflow(
    purpose="On an initialised project whose version-2 env manifest holds only "
            "the demo env, wfc demo --remove (Docker faked) leaves an empty "
            "manifest at version 3, which load_manifest reads"
)
def test_demo_remove_saves_an_emptied_older_manifest_at_the_current_version(
        tmp_path, monkeypatch, capsys):
    from wfc.cli import cli_main
    from wfc.environments import load_manifest
    from wfc.init import init_project

    redirect_home(monkeypatch, tmp_path / "home")
    target = tmp_path / "proj"
    init_project(target, assume_yes=True)
    manifest = _write_older_manifest(target)
    # Declared fixture deviation (as in the helper): the version-2 file is
    # rewritten raw, holding the demo env alone.
    del manifest["envs"][USER_ENV]
    (target / ".wfc" / "envs.json").write_text(
        json.dumps(manifest), encoding="utf-8")

    fake_subprocess_run(monkeypatch, refusing_process(
        "wfc demo --remove must not reach docker"), only="docker")
    capsys.readouterr()

    rc = cli_main(["demo", "--dir", str(target), "--remove", "--yes"])

    assert rc == 0, capsys.readouterr().err
    stored = json.loads((target / ".wfc" / "envs.json").read_text("utf-8"))
    assert stored == {"schema_version": 3, "envs": {}}
    assert load_manifest(target) == {"schema_version": 3, "envs": {}}
