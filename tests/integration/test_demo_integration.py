"""Tier 3 integration tests for ``wfc demo`` scaffold shape and teardown precision.

Uses the ``WFC_DEMO_IMAGE`` seam (D-B3): the scaffold registers the
session-built ``local/wfc-test-client`` image instead of building the real
demo image (``pip install workflow-canvas==0.5.0`` cannot succeed before the
release exists on PyPI). Every registration step remains the genuine
production path — env probe/digest-pin, module/method/sample registration,
git commit, DVC cache.

Demo/user RUN rows are created via the ORM rather than executing the full
15-job container pipeline (D-B5): the teardown logic under test operates on
DB rows, and executing containers adds minutes without covering more of the
deletion logic.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from wfc.demo.scaffold import _project_env, run_demo
from wfc.demo.remove import remove_demo


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_available(), reason="Docker not reachable"),
]

CLIENT_REF = "docker://local/wfc-test-client:latest"


def _init_project(tmp_path: Path) -> Path:
    """Create a real initialised project (git + DVC + DB) via init_project."""
    from wfc.init import init_project

    proj = tmp_path / "proj"
    proj.mkdir()
    archive = tmp_path / "archive"
    init_project(proj, archive=str(archive), assume_yes=True)
    # Consistent line endings for in-container git status parity (unused here
    # but keeps the fixture shape aligned with the other integration tests).
    subprocess.run(["git", "config", "core.autocrlf", "false"],
                   cwd=proj, check=True, capture_output=True)
    return proj


def _scaffold_demo(proj: Path, monkeypatch) -> None:
    monkeypatch.setenv("WFC_DEMO_IMAGE", CLIENT_REF)
    rc = run_demo(target_dir=proj, no_open=True, serve=False)
    assert rc == 0


def _q(proj: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(proj / ".wfc" / "wfc.db")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@workflow(purpose="US-1 scaffold shape: a real `wfc demo` populates the project "
                  "through the genuine registration path")
def test_demo_scaffold_shape(tmp_path, monkeypatch, client_image):
    口 = Step(step_num=1, name="Init and scaffold",
             purpose="Real init_project then run_demo with the WFC_DEMO_IMAGE seam")
    proj = _init_project(tmp_path)
    _scaffold_demo(proj, monkeypatch)

    口 = Step(step_num=2, name="DB shape",
             purpose="Module __demo__ with five contracted methods and three "
                     "tagged samples exists in the project DB")
    mods = _q(proj, "SELECT id FROM modules WHERE name='__demo__'")
    assert len(mods) == 1
    methods = _q(
        proj,
        f"SELECT name FROM methods WHERE module_id={mods[0][0]} ORDER BY name",
    )
    assert {m[0] for m in methods} == {
        "preprocess", "filter_cells", "label", "summarize", "plot",
    }
    contracts = _q(
        proj,
        f"SELECT COUNT(*) FROM method_contracts WHERE method_id IN "
        f"(SELECT id FROM methods WHERE module_id={mods[0][0]})",
    )
    assert contracts[0][0] == 5
    samples = _q(proj, "SELECT name FROM samples WHERE name LIKE '\\_\\_demo\\_\\_%' ESCAPE '\\'")
    assert {s[0] for s in samples} == {
        "__demo__ctrl_01", "__demo__treat_01", "__demo__treat_02",
    }

    口 = Step(step_num=3, name="Env and files",
             purpose="Digest-pinned __demo__env manifest entry plus method dirs, "
                     "sample files, and demo-pipeline.json on disk")
    import json
    manifest = json.loads((proj / ".wfc" / "envs.json").read_text())
    env = manifest["envs"]["__demo__env"]
    assert env["backend"] == "byo"
    assert "@sha256:" in env["container"]
    for m in ("preprocess", "filter_cells", "label", "summarize", "plot"):
        assert (proj / "methods" / m / "method.yaml").exists()
    assert (proj / "demo-pipeline.json").exists()
    assert (proj / "data" / "samples" / "__demo__ctrl_01" / "ctrl_01.csv").exists()


@workflow(purpose="US-3 teardown precision: `wfc demo --remove` deletes every "
                  "demo entity and run but leaves overlapping user entities "
                  "intact with zero orphaned rows")
def test_demo_teardown_precision_with_user_overlap(tmp_path, monkeypatch, client_image):
    口 = Step(step_num=1, name="Scaffold demo then register overlapping user entities",
             purpose="User module my-analysis with a method NAMED preprocess, a "
                     "user sample, and a user env coexist with the demo")
    proj = _init_project(tmp_path)
    _scaffold_demo(proj, monkeypatch)

    from wfc.envs import register as register_env

    register_env(
        name="user-env", backend="byo",
        source={"image": CLIENT_REF}, project_dir=proj,
    )

    staging = proj / "user_src" / "preprocess"
    staging.mkdir(parents=True)
    (staging / "preprocess.py").write_text(
        "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    )
    (staging / "method.yaml").write_text(
        "inputs:\n  data:\n    type: .csv\n    required: true\n"
        "outputs:\n  clean:\n    type: .csv\n    required: true\n"
        "params: {}\nexecutor: local\nenv: container:user-env\n"
    )
    user_csv = proj / "user_input.csv"
    user_csv.write_text("id,v\n1,2\n")

    with _project_env(proj):
        from wfc.cli import register_sample
        from wfc.register import register_method, register_module

        register_module(name="my-analysis", contracts=[])
        register_method(
            method_dir=staging, module_name="my-analysis",
            method_name="preprocess",
        )
        register_sample(name="my_sample", source_path=user_csv, project_root=proj)

        口 = Step(step_num=2, name="Create demo and user run rows via the ORM",
                 purpose="Demo runs with children + a cache chain, and a user "
                         "run whose FKs point INTO the demo set (D-5)")
        from sqlmodel import select

        from wfc.database import get_session
        from wfc.models import (
            Method, Module, Run, RunAnnotation, RunInput, RunOutput,
        )

        with get_session() as session:
            demo_mod = session.exec(
                select(Module).where(Module.name == "__demo__")
            ).one()
            demo_label = session.exec(
                select(Method).where(
                    Method.module_id == demo_mod.id, Method.name == "label"
                )
            ).one()
            user_mod = session.exec(
                select(Module).where(Module.name == "my-analysis")
            ).one()
            user_pre = session.exec(
                select(Method).where(
                    Method.module_id == user_mod.id, Method.name == "preprocess"
                )
            ).one()

            r1 = Run(method_id=demo_label.id, status="completed", sample="__demo__ctrl_01")
            session.add(r1); session.commit(); session.refresh(r1)
            session.add(RunInput(run_id=r1.id, input_name="data", artifact_path="x.csv"))
            session.add(RunOutput(run_id=r1.id, output_name="labeled",
                                  artifact_path="labeled.csv", artifact_type="method_file"))
            session.add(RunAnnotation(run_id=r1.id, favorite=True))
            r2 = Run(method_id=demo_label.id, status="completed",
                     sample="__demo__treat_01", cache_source_run_id=r1.id)
            session.add(r2); session.commit(); session.refresh(r2)

            u1 = Run(method_id=user_pre.id, status="completed", sample="my_sample",
                     cache_source_run_id=r1.id)
            session.add(u1); session.commit(); session.refresh(u1)
            session.add(RunInput(run_id=u1.id, input_name="data",
                                 source_run_id=r1.id, artifact_path="y.csv"))
            session.add(RunOutput(run_id=u1.id, output_name="clean",
                                  artifact_path="clean.csv", artifact_type="method_file"))
            session.commit()
            user_run_id = u1.id

    口 = Step(step_num=3, name="Remove the demo",
             purpose="Tag/module-cascade teardown with --yes")
    rc = remove_demo(proj, assume_yes=True)
    assert rc == 0

    口 = Step(step_num=4, name="Assert precision and zero orphans",
             purpose="Direct SQL: demo gone, user survives, no dangling FK")
    assert _q(proj, "SELECT COUNT(*) FROM modules WHERE name='__demo__'")[0][0] == 0
    assert _q(proj, "SELECT COUNT(*) FROM samples WHERE name LIKE '\\_\\_demo\\_\\_%' ESCAPE '\\'")[0][0] == 0

    # Direct-SQL orphan checks — the storage layer enforces nothing (FK OFF).
    assert _q(proj, "SELECT COUNT(*) FROM runs r LEFT JOIN methods m ON r.method_id=m.id WHERE m.id IS NULL")[0][0] == 0
    assert _q(proj, "SELECT COUNT(*) FROM methods m LEFT JOIN modules mo ON m.module_id=mo.id WHERE mo.id IS NULL")[0][0] == 0
    assert _q(proj, "SELECT COUNT(*) FROM run_inputs ri LEFT JOIN runs r ON ri.run_id=r.id WHERE r.id IS NULL")[0][0] == 0
    assert _q(proj, "SELECT COUNT(*) FROM run_outputs ro LEFT JOIN runs r ON ro.run_id=r.id WHERE r.id IS NULL")[0][0] == 0

    # User entities survive — including the method NAMED preprocess.
    assert _q(proj, "SELECT COUNT(*) FROM modules WHERE name='my-analysis'")[0][0] == 1
    assert _q(proj, "SELECT COUNT(*) FROM methods WHERE name='preprocess'")[0][0] == 1
    assert _q(proj, "SELECT COUNT(*) FROM samples WHERE name='my_sample'")[0][0] == 1
    urow = _q(proj, f"SELECT status, cache_source_run_id FROM runs WHERE id={user_run_id}")
    assert urow == [("completed", None)]  # survives; demo cache-source nulled
    assert _q(proj, f"SELECT COUNT(*) FROM run_inputs WHERE run_id={user_run_id}")[0][0] == 1
    assert _q(proj, f"SELECT source_run_id FROM run_inputs WHERE run_id={user_run_id}")[0][0] is None
    assert _q(proj, f"SELECT COUNT(*) FROM run_outputs WHERE run_id={user_run_id}")[0][0] == 1

    # Env manifest: demo env gone, user env intact.
    import json
    manifest = json.loads((proj / ".wfc" / "envs.json").read_text())
    assert "__demo__env" not in manifest["envs"]
    assert "user-env" in manifest["envs"]

    # Files: user's methods/preprocess snapshot survives (claimed by the
    # surviving method row); other demo method dirs and demo files are gone.
    assert (proj / "methods" / "preprocess").exists()
    for m in ("filter_cells", "label", "summarize", "plot"):
        assert not (proj / "methods" / m).exists()
    assert not (proj / "demo-pipeline.json").exists()
    assert not list((proj / "data" / "samples").glob("__demo__*"))

    口 = Step(step_num=5, name="Idempotency",
             purpose="A second --remove is a no-op success")
    assert remove_demo(proj, assume_yes=True) == 0
