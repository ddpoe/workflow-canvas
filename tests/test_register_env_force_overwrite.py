"""E2E test: --force overwrites an existing manifest entry (Tier 3).

Drives the full CLI path: argparse → _cli_register_env →
wfc.environments.verbs.register_env → wfc.environments.register → manifest
write. Mocks the docker_runner boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.fakes import (
    stub_docker_build,
    stub_docker_image_content,
    stub_docker_image_inspect,
    stub_readiness_probes,
)

from axiom_annotations import workflow, Step


@workflow(
    purpose="On a host with no Docker, wfc register-env on an existing env "
            "errors actionably (exit non-zero, message names env and points "
            "at --force, manifest unchanged), and --force reaches the Docker "
            "gate's not-runnable message; with Docker up, --force rebuilds "
            "and overwrites the manifest "
            "entry with the new digest."
)
def test_register_env_force_overwrites_existing(tmp_path, monkeypatch, capsys):
    from wfc import environments as envs_mod
    from wfc.environments import docker as docker_runner
    from wfc.cli import cli_main

    口 = Step(step_num=1, name="Seed manifest with an existing env",
             purpose="Existing entry has an OLD digest; we will rebuild and "
                     "expect it to be replaced with a NEW digest only on --force.")
    (tmp_path / ".wfc").mkdir()
    old_digest = "a" * 64
    seed_manifest = {
        "schema_version": 3,
        "envs": {
            "image-io": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": f"image-io@sha256:{old_digest}",
                "env_fingerprint": "00" * 16,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-01T00:00:00Z",
            }
        },
    }
    (tmp_path / ".wfc" / "envs.json").write_text(json.dumps(seed_manifest))

    # A real project (marker included) with the cwd inside it, so the
    # canonical resolver's upward walk finds it; the per-process cache is
    # cleared so the walk actually runs.
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text('[project]\nname = "t"\n')
    monkeypatch.chdir(tmp_path)
    from wfc.persistence import reset_engine
    reset_engine()

    # File-mode source.
    (tmp_path / "pixi.lock").write_text("version: 6\n", encoding="utf-8")

    new_digest = "b" * 64
    stub_docker_build(monkeypatch, lambda d, t: None)
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{new_digest}")

    口 = Step(step_num=2, name="Run CLI without --force on a host with no Docker",
             purpose="Default behavior must refuse to clobber; message must "
                     "name the env and point at --force. The refusal is the "
                     "verb's own, reported before Docker is asked.")
    stub_readiness_probes(monkeypatch, docker="fail")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
    ])
    captured = capsys.readouterr()
    assert rc != 0
    assert "image-io" in captured.err
    assert "--force" in captured.err

    # Manifest unchanged.
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    assert manifest["envs"]["image-io"]["container"] == f"image-io@sha256:{old_digest}"

    口 = Step(step_num=3, name="Re-run with --force on a host with no Docker",
             purpose="A request that passes every check of its own stops at "
                     "the Docker gate with the not-runnable message; the "
                     "manifest is unchanged.")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
        "--force",
    ])
    captured = capsys.readouterr()
    assert rc != 0
    assert "This project isn't ready to run" in captured.err
    assert "wfc doctor" in captured.err
    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    assert manifest["envs"]["image-io"]["container"] == f"image-io@sha256:{old_digest}"

    口 = Step(step_num=4, name="Re-run with --force and Docker up, expect overwrite",
             purpose="Manifest container field flips to the new digest; "
                     "old image is intentionally NOT deleted from the "
                     "docker daemon (users prune manually).")
    stub_readiness_probes(monkeypatch, docker="ok")
    rc = cli_main([
        "register-env", "image-io", "--from", "pixi.lock", "--backend", "pixi",
        "--force",
    ])
    assert rc == 0

    manifest = json.loads((tmp_path / ".wfc" / "envs.json").read_text())
    new_entry = manifest["envs"]["image-io"]
    assert new_entry["container"] == f"docker://local/image-io@sha256:{new_digest}"
    # Different env_fingerprint (digest changed).
    assert new_entry["env_fingerprint"] != "00" * 16


ENV = "image-io"
OTHER_ENV = "other-env"


@workflow(
    purpose="wfc register-env --force on an env rebuilds it and mints no "
            "method version: it lists the methods whose newest version uses "
            "the env (not a method whose older version did) and tells the "
            "user to register them again with `wfc register-method`; "
            ".wfc/envs.json holds only the env's current record"
)
def test_register_env_force_lists_the_methods_whose_newest_version_uses_it(
    git_project, monkeypatch, capsys, tmp_path_factory,
):
    from sqlmodel import select

    from tests.harness import Scenario, build_project, node, selector, wire
    from tests.harness.project import method_source_dir
    from wfc import environments as envs_mod
    from wfc.cli import cli_main
    from wfc.persistence import MethodVersion, get_session
    from wfc.registration import register_method
    from wfc.registration.method_version import read_version_list

    口 = Step(step_num=1, name="Register two methods on the env",
             purpose="a and b run in the env; b is registered again in another "
                     "env, so only its older version uses the env")
    scenario = Scenario(
        nodes=[selector(), node("a", inputs=[wire("sel")]),
               node("b", inputs=[wire("a")])],
        samples=["S1"], pipeline_id="env-dependants",
        env_name=ENV, env_backend="pixi",
    )
    project = build_project(scenario, root=git_project, monkeypatch=monkeypatch)
    root = project.root
    stub_docker_build(monkeypatch, lambda d, t: None)
    stub_docker_image_content(monkeypatch)
    stub_docker_image_inspect(monkeypatch, f"sha256:{'d' * 64}")
    envs_mod.register(name=OTHER_ENV, backend="byo",
                      source={"image": "docker://local/other:latest"},
                      project_dir=root)
    b = scenario.node_by_id("b")
    b_yaml = method_source_dir(root, b) / "method.yaml"
    b_yaml.write_text(b_yaml.read_text(encoding="utf-8")
                      .replace(f"env: {ENV}\n", f"env: {OTHER_ENV}\n"),
                      encoding="utf-8")
    register_method(method_dir=method_source_dir(root, b), module_name=b.module, assume_yes=True)
    a = scenario.node_by_id("a")
    lists_before = {spec.method_name: read_version_list(root, spec.module, spec.method_name)
                    for spec in (a, b)}
    with get_session() as session:
        versions_before = len(session.exec(select(MethodVersion)).all())

    口 = Step(step_num=2, name="Rebuild the env with --force",
             purpose="The build succeeds with a new image")
    lock = tmp_path_factory.mktemp("lock") / "pixi.lock"
    lock.write_text("version: 6\n", encoding="utf-8")
    new_digest = "b" * 64
    stub_docker_image_inspect(monkeypatch, f"sha256:{new_digest}")
    stub_readiness_probes(monkeypatch, docker="ok")
    capsys.readouterr()
    rc = cli_main(["register-env", ENV, "--from", str(lock), "--backend", "pixi",
                   "--force"])
    out = capsys.readouterr().out
    assert rc == 0

    口 = Step(step_num=3, name="The newest-version dependants are listed",
             purpose="a is listed, b is not; the user is told to register them "
                     "again; no version was minted; the manifest is current-only")
    assert f"  - {a.module}/{a.method_name}" in out
    assert f"{b.module}/{b.method_name}" not in out
    assert "wfc register-method" in out
    with get_session() as session:
        assert len(session.exec(select(MethodVersion)).all()) == versions_before
    assert {spec.method_name: read_version_list(root, spec.module, spec.method_name)
            for spec in (a, b)} == lists_before
    manifest = json.loads((root / ".wfc" / "envs.json").read_text())
    assert manifest["envs"][ENV]["container"] == f"docker://local/{ENV}@sha256:{new_digest}"
    assert set(manifest["envs"]) == {ENV, OTHER_ENV}
    assert "_history" not in json.dumps(manifest)
