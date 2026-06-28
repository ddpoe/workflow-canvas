"""Tier 3 integration test: a Tier-1 ``@wfc.method`` method end-to-end (ADR-020).

Builds a user-env image WITH the ``wfc-client`` package installed
(``tests/fixtures/Dockerfile.client``), registers the Tier-1 ``qc`` fixture
method (``tests/fixtures/methods_client/qc``) through the production
registration path, and runs it through a real container via ``wfc run-step``.

Asserts the full Tier-1 results channel:
  - the container wrote ``_wfc_results.json`` (the single results manifest)
    recording each declared output as a run-dir-relative path plus metrics;
  - the declared output files exist at the recorded paths on the host;
  - the host created the right ``RunOutput`` rows and recorded the metrics;
  - the existing ``archive_outputs`` sweep hashes + DVC-caches the outputs.

This is the Tier-1 half of the archive-parity pair; ``test_method_tier_2.py``
proves the equivalent env-vars + file method (NO client) archives identically.

SKIPs cleanly when Docker isn't reachable. Default pytest invocation
deselects the ``integration`` marker via ``addopts``; run explicitly with
``pytest -m integration tests/integration/``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.conftest import register_test_method


def _docker_available() -> bool:
    """True iff ``docker`` is on PATH and ``docker info`` succeeds."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _docker_available(),
        reason="Docker not reachable on PATH",
    ),
]


def _init_git_project(proj: Path) -> None:
    """Initialize ``proj`` as a git repo with the line-ending hygiene the
    containerized ``git status`` needs (matches the existing harness)."""
    subprocess.run(["git", "init"], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "wfc@wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "wfc"],
        cwd=proj, check=True, capture_output=True,
    )
    (proj / ".gitattributes").write_text("* -text\n")
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=proj, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "core.fileMode", "false"],
        cwd=proj, check=True, capture_output=True,
    )


def _write_envs_json(proj: Path, image_digest: str, env_name: str) -> None:
    """Write ``.wfc/envs.json`` with a digest-pinned container ref for env_name."""
    wfc_dir = proj / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    container_ref = f"docker://local/wfc-test-client@sha256:{image_digest}"
    (wfc_dir / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            env_name: {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": container_ref,
                "env_fingerprint": image_digest,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-06-24T00:00:00Z",
            }
        },
    }))


def _materialize_project(tmp_path: Path, image_digest: str, monkeypatch) -> Path:
    """Create a tmp wfc project running the Tier-1 ``qc`` method in a container.

    The method (``tests/fixtures/methods_client/qc``) is authored against
    ``wfc-client`` (``@wfc.method`` + ``ctx.save_artifact`` / ``ctx.log_metric``)
    and registered via the production code path. The container env points at
    the client image (host wfc + wfc-client).
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    _init_git_project(proj)

    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")

    _write_envs_json(proj, image_digest, "client-env")

    # Copy the Tier-1 qc fixture into the project's methods/ tree. method.yaml
    # already names `env: container:client-env`.
    src_method = (
        Path(__file__).resolve().parent.parent
        / "fixtures" / "methods_client" / "qc"
    )
    method_dir = proj / "methods" / "qc"
    method_dir.mkdir(parents=True)
    shutil.copyfile(src_method / "qc.py", method_dir / "qc.py")
    shutil.copyfile(src_method / "method.yaml", method_dir / "method.yaml")

    register_test_method(
        project_dir=proj,
        module_name="qcmod",
        method_dir=method_dir,
        method_name="qc",
    )

    # Sample data the input_selector feeds into the `data` slot. Two clean
    # rows + one non-numeric row so qc keeps 2 and drops 1.
    sample_dir = proj / "data" / "samples" / "s1"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "data.csv").write_text(
        "id,value\n1,10\n2,20\n3,not_a_number\n"
    )

    pipeline = {
        "nodes": [
            {
                "id": "sel",
                "type": "input_selector",
                "method": "",
                "module": "",
                "samples": ["s1"],
            },
            {
                "id": "n1",
                "method": "qc",
                "module": "qcmod",
                "env": "client-env",
                "script": "methods/qc/qc.py",
                "slot_outputs": {
                    "report": "report.json",
                    "clean": "clean.csv",
                    "dropped": "dropped.csv",
                },
            },
        ],
        "links": [
            {"source": "sel", "target": "n1", "target_slot": "data"},
        ],
        "samples": ["s1"],
        "param_sets": {},
    }
    (proj / "pipeline.json").write_text(json.dumps(pipeline))

    subprocess.run(["git", "add", "."], cwd=proj, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=proj, check=True, capture_output=True,
    )

    return proj


def test_tier1_method_writes_manifest_and_archives(
    tmp_path: Path, client_image: str, monkeypatch
) -> None:
    """End-to-end: a Tier-1 ``@wfc.method`` runs in a container with wfc-client
    installed; the client writes ``_wfc_results.json``; the host populates
    ``RunOutput`` rows + metrics from it; ``archive_outputs`` then hashes and
    DVC-caches every declared output.
    """
    from wfc.database import get_session, reset_engine
    from wfc.models import Run, RunOutput
    from wfc.provenance import archive_outputs
    from sqlmodel import select

    proj = _materialize_project(tmp_path, client_image, monkeypatch)

    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env["DATABASE_URL"] = f"sqlite:///{proj / '.wfc' / 'wfc.db'}"
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable, "-m", "wfc", "run-step",
            "--node-id", "n1",
            "--sample", "s1",
            "--variant", "default",
            "--pipeline-json", str(proj / "pipeline.json"),
            "--pipeline-id", "p1",
        ],
        cwd=proj,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, (
        f"wfc run-step failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # The client must have written the single results manifest.
    manifests = list((proj / ".runs").rglob("_wfc_results.json"))
    assert manifests, (
        f"_wfc_results.json not written under {proj / '.runs'}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    manifest = json.loads(manifests[0].read_text())
    assert set(manifest["outputs"]) == {"report", "clean", "dropped"}, manifest
    assert manifest["metrics"] == {"kept_rows": 2, "dropped_rows": 1}, manifest

    # Each declared output exists at the manifest-recorded (run-dir-relative) path.
    run_dir = manifests[0].parent
    for name, rel in manifest["outputs"].items():
        recorded = (run_dir / rel)
        assert recorded.exists(), f"output '{name}' missing at recorded path {recorded}"

    # The qc transform kept 2 / dropped 1 (the non-numeric row).
    clean_csv = (run_dir / manifest["outputs"]["clean"]).read_text()
    assert clean_csv.count("\n") == 3, f"expected header + 2 rows, got: {clean_csv!r}"

    # The host engine (separate process) wrote its DB; read it here.
    reset_engine()
    with get_session() as session:
        run = session.exec(
            select(Run).where(Run.status == "completed")
        ).first()
        assert run is not None, "no completed Run row written by run-step"
        run_id = run.id
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        names = {r.output_name for r in rows}
        assert names == {"report.json", "clean.csv", "dropped.csv"}, names
        # Pre-archive: content_hash is NULL (deferred archiving).
        assert all(r.content_hash is None for r in rows)
        # Metrics flow through the single _wfc_results.json channel into
        # Run.metrics (JSON column) — no separate metrics.json read.
        assert run.metrics == {"kept_rows": 2, "dropped_rows": 1}, run.metrics

    # The existing archive sweep hashes + DVC-caches the outputs (reuse, not
    # re-implementation).
    archive_outputs(project_dir=proj, run_id=run_id)
    reset_engine()
    with get_session() as session:
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        assert rows, "no RunOutput rows after archive"
        for r in rows:
            assert r.content_hash is not None, (
                f"output '{r.output_name}' not archived (content_hash NULL)"
            )
            cache_path = (
                proj / ".dvc" / "cache" / "files" / "md5"
                / r.content_hash[:2] / r.content_hash[2:]
            )
            assert cache_path.exists(), (
                f"archived output '{r.output_name}' missing from DVC cache "
                f"at {cache_path}"
            )
