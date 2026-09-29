"""Canvas API diverse path: a real keep-going run through the routes (containerized).

One pipeline, submitted twice through ``POST /api/workflow/run`` and polled
through ``GET /api/workflow/status/{job_id}`` to a terminal state against the
real engine: the readiness gate, enrichment, Snakemake, run-step in the fixture
container, the cancelled-rows walk and the cache lookup all run for real.

The pipeline fans two samples through a method that fails for one of them and
a method downstream of it, under keep-going. So one submission covers a failed
sample, a mixed node, a node cancelled by an upstream failure and a pipeline
that completes with failures; the resubmission covers the cache hits.

Integration + requires_docker.
"""

from __future__ import annotations

import textwrap
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from axiom_annotations import workflow, Step

from tests.conftest import pin_project_root, requires_docker
from tests.fixtures.conftest import create_sample_csv, register_test_method

pytestmark = [pytest.mark.integration, requires_docker]

#: The sample the gate method refuses, and the one it passes.
FAILING_SAMPLE = "sample_b"
PASSING_SAMPLE = "sample_a"

#: How long a real containerized run may take to settle.
SETTLE_TIMEOUT_S = 300.0

_GATE_METHOD_YAML = textwrap.dedent("""\
    inputs:
      data:
        type: .csv
        required: true
        description: "Input CSV, copied through unchanged"
    outputs:
      output:
        type: .csv
        required: true
        description: "The input CSV, for every sample but the refused one"
    params:
      fail_sample:
        type: str
        required: true
        description: "The sample this method raises on"
    executor: python
    env: fixture-env
""")

_GATE_METHOD_SCRIPT = textwrap.dedent('''\
    """Test method: raises for the sample named in fail_sample, copies the rest."""
    import json
    import os
    import shutil
    from pathlib import Path


    def main():
        params = json.loads(os.environ.get("WFC_PARAMS", "{}"))
        sample = os.environ["WFC_SAMPLE"]
        if sample == params["fail_sample"]:
            raise RuntimeError(f"sample_gate: refusing sample {sample}")
        data = json.loads(os.environ["WFC_INPUT_PATHS"])["data"][0]
        shutil.copyfile(data, Path(os.environ["WFC_RUN_DIR"]) / "output.csv")


    if __name__ == "__main__":
        main()
''')


def _register_gate_method(project_dir) -> None:
    """Write the sample-gate method into the project and register it.

    Args:
        project_dir: The project, with the fixture env record already written.
    """
    method_dir = project_dir / "methods" / "sample_gate"
    method_dir.mkdir(parents=True, exist_ok=True)
    (method_dir / "method.yaml").write_text(_GATE_METHOD_YAML, encoding="utf-8")
    (method_dir / "sample_gate.py").write_text(_GATE_METHOD_SCRIPT, encoding="utf-8")
    register_test_method(
        project_dir=project_dir, module_name="test_pipeline",
        method_dir=method_dir, method_name="sample_gate",
    )


def _payload() -> dict:
    """The keep-going document: selector -> sample_gate -> transform.

    Returns:
        The request body for the run route.
    """
    samples = [PASSING_SAMPLE, FAILING_SAMPLE]
    return {
        "name": "diverse-path",
        "keep_going": True,
        "nodes": [
            {"id": "sel", "type": "input_selector", "params": {},
             "samples": samples, "source": "registered", "fan_mode": "out"},
            {"id": "gate", "type": "method", "method": "sample_gate",
             "module": "test_pipeline", "params": {"fail_sample": FAILING_SAMPLE}},
            {"id": "after", "type": "method", "method": "transform",
             "module": "test_pipeline", "params": {"suffix": "_after"}},
        ],
        "links": [
            {"source": "sel", "target": "gate",
             "sourceHandle": "output", "targetHandle": "data"},
            {"source": "gate", "target": "after",
             "sourceHandle": "output", "targetHandle": "data"},
        ],
        "samples": samples,
    }


def _submit(client: TestClient) -> str:
    """Submit the document through the run route.

    Args:
        client: The test client over the app.

    Returns:
        The job id.
    """
    res = client.post("/api/workflow/run", json=_payload())
    assert res.status_code == 200, f"run refused: {res.status_code} {res.text}"
    body = res.json()
    assert body["status"] == "submitted"
    assert body["step_map"] == {"sel": "", "gate": "sample_gate", "after": "transform"}
    return body["job_id"]


def _poll_to_terminal(client: TestClient, job_id: str) -> dict:
    """Poll the status route until the thread has ended and the status settled.

    Args:
        client: The test client over the app.
        job_id: The job to poll.

    Returns:
        The terminal status payload.
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    body: dict = {}
    while time.monotonic() < deadline:
        res = client.get(f"/api/workflow/status/{job_id}")
        assert res.status_code == 200, res.text
        body = res.json()
        if not body["thread_alive"] and body["overall_status"] not in ("pending", "running"):
            return body
        time.sleep(1.0)
    pytest.fail(f"job {job_id} did not settle in {SETTLE_TIMEOUT_S}s; last status: {body}")


@workflow(
    purpose="A keep-going pipeline submitted through the run route and polled "
            "through the status route against the real engine: one sample fails, "
            "its downstream node is cancelled by that failure, the pipeline "
            "completes with failures, and the resubmission reports cache hits",
)
def test_keep_going_run_settles_through_the_routes_and_resubmits_as_cache_hits(
    register_fixture_methods, monkeypatch,
):
    from wfc.canvas.server import app
    from wfc.canvas.state import _active_jobs
    from wfc.persistence import Run, get_session

    project_dir = register_fixture_methods

    口 = Step(step_num=1, name="Build the served project",
             purpose="Register a method that fails for one sample beside the fixture "
                     "methods, stage both samples, and serve the project")
    _register_gate_method(project_dir)
    create_sample_csv(project_dir, PASSING_SAMPLE, num_rows=3)
    create_sample_csv(project_dir, FAILING_SAMPLE, num_rows=3)
    pin_project_root(monkeypatch, project_dir)
    _active_jobs.clear()
    client = TestClient(app, raise_server_exceptions=False)

    口 = Step(step_num=2, name="Submit and settle the first run",
             purpose="The real readiness gate admits the submission; polling ends "
                     "when the thread has ended on a terminal overall status")
    first = _poll_to_terminal(client, _submit(client))

    口 = Step(step_num=3, name="Read the first run's outcome",
             purpose="The pipeline completes with failures; the gate node is mixed "
                     "with the failing sample's error; the downstream node counts "
                     "one completed and one cancelled row and reads completed; the "
                     "engine's failure is the job's classified error")
    assert first["overall_status"] == "completed_with_failures", first
    gate, after = first["node_states"]["gate"], first["node_states"]["after"]
    assert gate["status"] == "mixed", gate
    assert (gate["tally"]["completed"], gate["tally"]["failed"]) == (1, 1), gate
    assert gate["error_sample"] == FAILING_SAMPLE, gate
    assert "refusing sample" in gate["error"], gate
    assert gate["error_run_id"] in gate["run_ids"], gate
    assert after["status"] == "completed", after
    assert after["tally"] == {"running": 0, "completed": 1, "failed": 0, "cancelled": 1}, after
    assert not gate["cache_hit"] and not after["cache_hit"], first
    assert first["error"]["kind"] == "unknown", first["error"]

    # The cancelled row is the engine's cancellation by the upstream failure,
    # not a user cancel: it names the gate's failed run. The status route does
    # not carry the cancellation cause, so the row says it.
    with get_session() as session:
        cancelled = session.exec(
            select(Run).where(Run.pipeline_id == first["job_id"],
                              Run.status == "cancelled")
        ).all()
    assert [(r.sample, r.cancelled_due_to_run_id) for r in cancelled] == [
        (FAILING_SAMPLE, int(gate["error_run_id"])),
    ]

    口 = Step(step_num=4, name="Resubmit and settle the second run",
             purpose="The same document again")
    second = _poll_to_terminal(client, _submit(client))

    口 = Step(step_num=5, name="Read the cache hits",
             purpose="The passing sample's work is reused from the first run on "
                     "both nodes; the failing sample fails again and its downstream "
                     "row is cancelled again, so the outcome is unchanged")
    assert second["overall_status"] == "completed_with_failures", second
    gate2, after2 = second["node_states"]["gate"], second["node_states"]["after"]
    assert gate2["status"] == "mixed" and gate2["error_sample"] == FAILING_SAMPLE, gate2
    assert after2["status"] == "completed", after2
    assert after2["tally"]["cancelled"] == 1, after2
    first_passing_gate_run = next(r for r in gate["run_ids"] if r != gate["error_run_id"])
    assert gate2["cache_hit"] is True, gate2
    assert gate2["original_run_id"] == first_passing_gate_run, (gate, gate2)
    assert gate2["cache_key"], gate2
    assert after2["cache_hit"] is True, after2
    assert after2["original_run_id"] in after["run_ids"], (after, after2)

    _active_jobs.clear()
