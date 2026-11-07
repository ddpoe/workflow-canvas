"""A draft step runs its folder's script in the env's image, with the step's WFC_* values.

Real Docker: ``wfc run-step --draft`` on a one-method pipeline whose node is
pinned to its method's draft. The draft's script (edited after the method was
registered, so only the draft holds it) writes what it was handed; the test
reads it back from the draft's folder and checks that no run row was written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from axiom_annotations import Step, workflow

from tests.conftest import requires_docker
from tests.fixtures.conftest import sample_source_dir
from tests.integration.test_containerized_step_runs import _materialize_project
from wfc import layout

pytestmark = [pytest.mark.integration, requires_docker]

#: The draft's script: only the draft holds it; it writes the WFC_* values.
DRAFT_SCRIPT = (
    "import os\n"
    "from pathlib import Path\n"
    "\n"
    "run_dir = Path(os.environ['WFC_RUN_DIR'])\n"
    "seen = '|'.join(os.environ.get(k, '') for k in\n"
    "                ('WFC_RUN_ID', 'WFC_SAMPLE', 'WFC_NODE_ID', 'WFC_PIPELINE_ID'))\n"
    "(run_dir / 'output.txt').write_text('draft-from-container|' + seen)\n"
)


@workflow(purpose="A draft step really runs: wfc run-step --draft launches the "
                  "draft folder's script in the env's image with the step's "
                  "WFC_* values, its output lands in the draft's folder and "
                  "draft-run.json, and no run row is written")
def test_a_draft_step_runs_the_draft_folders_script_in_the_env_image(
        tmp_path: Path, minimal_image: str, monkeypatch) -> None:
    from wfc.execution.draft import read_draft_run, spell_draft_ref
    from wfc.registration import add_draft

    口 = Step(step_num=1, name="A registered method, then a draft of its folder",
             purpose="The script is edited after registration: only the draft "
                     "runs the new one")
    proj = _materialize_project(tmp_path, minimal_image, monkeypatch)
    method_dir = sample_source_dir(proj) / "_methods" / "smoke"
    (method_dir / "smoke.py").write_text(DRAFT_SCRIPT)
    pointer = add_draft("smoke", method_dir, proj)
    pipeline_path = proj / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text())
    for node in pipeline["nodes"]:
        if node["id"] == "n1":
            node["version"], node["folder"] = "draft", str(pointer.folder)
    pipeline_path.write_text(json.dumps(pipeline))

    口 = Step(step_num=2, name="wfc run-step --draft",
             purpose="The generated rule's own command for a draft-mode node")
    env = os.environ.copy()
    env["WFC_PROJECT_ROOT"] = str(proj)
    env["DATABASE_URL"] = f"sqlite:///{proj / '.wfc' / 'wfc.db'}"
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "wfc", "run-step", "--node-id", "n1",
         "--sample", "s1", "--variant", "default",
         "--pipeline-json", str(pipeline_path), "--pipeline-id", "p1", "--draft"],
        cwd=proj, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, (
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    口 = Step(step_num=3, name="The draft's output, its record, no run row",
             purpose="The container saw the draft string as its run id")
    ref = spell_draft_ref("smoke", "smoke", "n1", "s1")
    output = layout.draft_result_dir(proj, "smoke", "smoke", "n1", "s1") / "output.txt"
    assert output.read_text() == f"draft-from-container|{ref}|s1|n1|p1"
    record = read_draft_run("smoke", "smoke", proj)
    step = record["steps"]["n1"]["s1"]
    assert step["status"] == "completed" and step["version"] == "draft"
    assert step["outputs"] == {"output": "n1/s1/output.txt"}
    with sqlite3.connect(proj / ".wfc" / "wfc.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
