"""The project the draft-step CLI witnesses run in.

``wfc try``, ``wfc runs``, ``wfc export`` on draft steps and ``wfc
delete-draft`` all start from one project built through the scenario
harness: ``up`` (recorded) feeding ``mid`` (a draft), run once on ``s1``.

Pins: the Docker image probes and the readiness probes are stubbed (Docker
said to be up); the harness's stub rung runs the steps as local processes.
Does not prove: anything about a real Docker daemon.
"""

from __future__ import annotations

from tests.fixtures.fakes.docker import stub_docker_image_content, stub_docker_image_inspect
from tests.fixtures.fakes.readiness import stub_readiness_probes
from tests.harness import Scenario, node, selector, wire
from tests.harness.drivers import run_scenario
from tests.harness.project import STUB_DIGEST
from tests.harness.scenario import SELECTOR_ID
from wfc.execution.draft import spell_draft_ref


def built_draft_project(tmp_project, monkeypatch):
    """A project with up (recorded) feeding mid (a draft), run once.

    Args:
        tmp_project: The project root to build in.
        monkeypatch: The test's monkeypatch, for the registered fakes.

    Returns:
        ``(scenario, module, up_run, mid_ref)``: the scenario, the draft's
        module, up's recorded run id and mid's draft-step reference on s1.
    """
    scenario = Scenario(
        nodes=[selector(),
               node("up", inputs=[wire(SELECTOR_ID)], outputs={"table": ".csv"}),
               node("mid", inputs=[wire("up", source_slot="table")],
                    outputs={"table": ".csv"})],
        samples=["s1"], pipeline_id="try-base", name="try_base", drafts=("mid",))
    stub_docker_image_inspect(monkeypatch, f"sha256:{STUB_DIGEST}")
    stub_docker_image_content(monkeypatch)
    stub_readiness_probes(monkeypatch)
    observed = run_scenario(scenario, root=tmp_project, monkeypatch=monkeypatch,
                            pipeline_end=False)
    up_run = observed.runs[("up", "s1", "default")].run_id
    mid = scenario.node_by_id("mid")
    mid_ref = spell_draft_ref(mid.module, mid.method_name, "mid", "s1")
    return scenario, mid.module, up_run, mid_ref
