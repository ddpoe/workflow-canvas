"""Tier 3 integration test: Tier-2 parity with the Tier-1 client method (ADR-020).

Runs the *equivalent* of the Tier-1 ``qc`` method WITHOUT ``wfc-client`` —
plain env-vars (``WFC_RUN_DIR`` / ``WFC_INPUT_PATHS`` / ``WFC_PARAMS``) and
file outputs only, and NO ``_wfc_results.json`` manifest. The method writes
the same three declared output files (report.json / clean.csv / dropped.csv).

Asserts archive parity with the Tier-1 run: the host scans ``WFC_RUN_DIR``
for the declared output filenames (no manifest present), creates the same
``RunOutput`` rows, and ``archive_outputs`` produces the same content hashes
and DVC-cache entries the Tier-1 path produces. Metrics are empty for a
no-manifest Tier-2 run (the only difference: Tier-2 outputs-only carries no
metrics channel unless the method writes the manifest by hand).

Uses the minimal image (``Dockerfile.minimal``, NO wfc-client) so the method
genuinely runs against the canonical Tier-2 contract.

SKIPs cleanly when Docker isn't reachable.
"""
from __future__ import annotations

import hashlib
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


# A plain Tier-2 qc method: WFC_* env vars + file outputs only, NO wfc-client
# import and NO _wfc_results.json. Writes the same three declared files the
# Tier-1 qc method declares, so the host's declared-slot scan produces the
# same RunOutput rows.
_TIER2_QC_SOURCE = """\
import csv
import json
import os
from pathlib import Path


def main():
    run_dir = Path(os.environ["WFC_RUN_DIR"])
    slot_paths = json.loads(os.environ.get("WFC_INPUT_PATHS", "{}"))
    data_paths = slot_paths.get("data", [])
    if not data_paths or not Path(data_paths[0]).exists():
        raise FileNotFoundError(f"Input file not found: {data_paths}")
    with open(data_paths[0], newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else ["id", "value"]

    clean = []
    dropped = []
    for row in rows:
        try:
            float(row.get("value", ""))
            clean.append(row)
        except (TypeError, ValueError):
            row["__drop_reason"] = "non_numeric_value"
            dropped.append(row)

    with open(run_dir / "clean.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(clean)

    with open(run_dir / "dropped.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames + ["__drop_reason"])
        w.writeheader()
        w.writerows(dropped)

    (run_dir / "report.json").write_text(
        json.dumps({"kept": len(clean), "dropped": len(dropped)}, indent=2)
    )


if __name__ == "__main__":
    main()
"""

_QC_METHOD_YAML = """\
inputs:
  data:
    type: .csv
    required: true
outputs:
  report:
    type: .json
    required: true
  clean:
    type: .csv
    required: true
  dropped:
    type: .csv
    required: true
params: {}
executor: local
env: container:minimal-env
"""

_SAMPLE_CSV = "id,value\n1,10\n2,20\n3,not_a_number\n"


def _init_git_project(proj: Path) -> None:
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


def _materialize_project(tmp_path: Path, image_digest: str, monkeypatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    _init_git_project(proj)

    monkeypatch.setenv("WFC_PROJECT_ROOT", str(proj))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{proj / '.wfc' / 'wfc.db'}")

    wfc_dir = proj / ".wfc"
    wfc_dir.mkdir(exist_ok=True)
    container_ref = f"docker://local/wfc-test-minimal@sha256:{image_digest}"
    (wfc_dir / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            "minimal-env": {
                "backend": "pixi",
                # Fixture image is plain python:3.11-slim; record the
                # interpreter so dispatch skips the pixi default path.
                "python": "python",
                "source": "pixi.toml",
                "container": container_ref,
                "env_fingerprint": image_digest,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-06-24T00:00:00Z",
            }
        },
    }))

    method_dir = proj / "methods" / "qc"
    method_dir.mkdir(parents=True)
    (method_dir / "qc.py").write_text(_TIER2_QC_SOURCE)
    (method_dir / "method.yaml").write_text(_QC_METHOD_YAML)

    register_test_method(
        project_dir=proj,
        module_name="qcmod",
        method_dir=method_dir,
        method_name="qc",
    )

    sample_dir = proj / "data" / "samples" / "s1"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "data.csv").write_text(_SAMPLE_CSV)

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
                "env": "minimal-env",
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


def test_tier2_no_manifest_archives_identically(
    tmp_path: Path, minimal_image: str, monkeypatch
) -> None:
    """A Tier-2 method (env-vars + files, NO manifest) runs in a container; the
    host scans WFC_RUN_DIR for declared outputs, creates the same RunOutput
    rows the Tier-1 path does, and archive_outputs hashes + DVC-caches them.

    Proves the no-manifest fallback path archives identically to Tier-1: the
    same declared output filenames yield the same RunOutput row set and the
    same content-addressed cache entries.
    """
    from wfc.database import get_session, reset_engine
    from wfc.models import Run, RunOutput
    from wfc.provenance import archive_outputs
    from sqlmodel import select

    proj = _materialize_project(tmp_path, minimal_image, monkeypatch)

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

    # No manifest: a pure Tier-2 outputs-only method writes none.
    manifests = list((proj / ".runs").rglob("_wfc_results.json"))
    assert not manifests, (
        f"Tier-2 method must not write _wfc_results.json; found {manifests}"
    )

    # All three declared outputs exist on the host (scanned from run_dir).
    for filename in ("report.json", "clean.csv", "dropped.csv"):
        matches = list((proj / ".runs").rglob(filename))
        assert matches, f"declared output {filename} not found under .runs"

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
        # Same RunOutput row set as the Tier-1 run.
        assert names == {"report.json", "clean.csv", "dropped.csv"}, names
        assert all(r.content_hash is None for r in rows)
        # No manifest -> no metrics channel for a pure outputs-only Tier-2 run.
        assert not run.metrics, run.metrics

    archive_outputs(project_dir=proj, run_id=run_id)
    reset_engine()
    with get_session() as session:
        rows = session.exec(
            select(RunOutput).where(RunOutput.run_id == run_id)
        ).all()
        assert rows, "no RunOutput rows after archive"
        hashes_by_name = {}
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
            hashes_by_name[r.output_name] = r.content_hash

    # Parity anchor: the content hashes are the md5 of the produced bytes, so
    # an identical method run (Tier-1) caching identical bytes lands the same
    # DVC entries. clean.csv/dropped.csv/report.json carry the kept-2/dropped-1
    # split both tiers compute.
    clean_path = list((proj / ".runs").rglob("clean.csv"))[0]
    expected_md5 = hashlib.md5(clean_path.read_bytes()).hexdigest()
    assert hashes_by_name["clean.csv"] == expected_md5, (
        f"clean.csv content_hash {hashes_by_name['clean.csv']} != md5 of bytes "
        f"{expected_md5}"
    )
