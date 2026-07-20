"""``wfc demo`` preflight safety (US-1): a failed scaffold changes NOTHING.

Every fallible check (initialised project, Docker, existing demo) runs
before the first state change — these tests assert on the ABSENCE of state
(byte-for-byte identical directory tree), not just the exit code.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import workflow

from wfc.cli import cli_main


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Map every file under *root* (relative path) to its sha256."""
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
        elif p.is_dir():
            snap[str(p.relative_to(root)) + "/"] = "<dir>"
    return snap


@workflow(purpose="wfc demo in an uninitialised directory exits non-zero and "
                  "creates nothing")
def test_demo_uninitialised_dir_changes_nothing(tmp_path):
    target = tmp_path / "not-a-project"
    target.mkdir()
    (target / "user-file.txt").write_text("mine")
    before = _tree_snapshot(target)

    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    assert _tree_snapshot(target) == before


@workflow(purpose="wfc demo with Docker unavailable exits non-zero and leaves "
                  "the initialised project byte-for-byte unchanged")
def test_demo_docker_down_changes_nothing(tmp_path, monkeypatch, capsys):
    # Minimal initialised project: marker + DB file + git repo. The DVC gate
    # is mocked to pass so the mocked Docker preflight is the failing check.
    target = tmp_path / "proj"
    (target / ".wfc").mkdir(parents=True)
    (target / ".wfc" / "wf-canvas.toml").write_text("[database]\n")
    (target / ".wfc" / "wfc.db").write_bytes(b"")
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)

    import wfc.demo.scaffold as scaffold
    from wfc.preflight import CheckResult

    monkeypatch.setattr(scaffold, "ensure_dvc_ready", lambda _p: {})
    monkeypatch.setattr(
        scaffold,
        "check_docker",
        lambda: CheckResult(
            name="docker", status="fail",
            message="Docker is installed but the daemon is not running.",
            fix_hint="Start Docker Desktop.",
        ),
    )

    before = _tree_snapshot(target)
    rc = cli_main(["demo", "--dir", str(target), "--no-open"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "Docker" in err
    assert _tree_snapshot(target) == before
