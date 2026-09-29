"""The scenario harness: declare a pipeline scenario, get one bundle back.

A test states topology, samples, variants, per-method behavior and starting
state; the harness builds a project on disk, executes as much of it as the
test asks for, and returns one standard observation bundle — run rows,
output rows, run-input rows, sentinels, sidecars, outcomes, exit codes, and
the target list that ran. The standing invariant pack is asserted after
every run.

Public surface::

    from tests.harness import Scenario, Phase, STUB, ENGINE
    from tests.harness import build_project, run_target, run_scenario
    from tests.harness import drive_target

``run_target`` is ``build_project`` followed by ``drive_target``. A test
that needs to change the environment between those two acts — the run's own
root resolution is the subject, so the harness must not pin it — calls the
two itself instead of the fused entry point.

``drivers`` is the only module that names a phase function or an expansion
function; the harness itself is never a witness for a catalog case.
"""
from .drivers import (
    ENGINE,
    PHASE_ENTRY_POINTS,
    STUB,
    Phase,
    drive_target,
    expand_targets,
    observe_after_pipeline,
    run_scenario,
    run_target,
)
from .invariants import INVARIANT_NAMES, InvariantReport, check_invariants
from .observe import Observation, Target, TargetRun
from .project import STUB_DIGEST, Project, build_project
from .scenario import (
    DEFAULT_ENV_NAME,
    DEFAULT_MODULE,
    DEFAULT_SAMPLE,
    DEFAULT_VARIANT,
    Behavior,
    ModuleSpec,
    NodeSpec,
    PriorRun,
    Scenario,
    Wire,
    chain,
    completed,
    exits,
    fan_in,
    fan_in_nodes,
    selector_beside_method,
    selector_beside_method_and_reference,
    node,
    reference,
    selector,
    two_node_chain,
    wire,
)

__all__ = [
    # entry points and rungs
    "build_project", "drive_target", "run_target", "run_scenario",
    "Phase", "PHASE_ENTRY_POINTS", "STUB", "ENGINE", "expand_targets",
    "observe_after_pipeline",
    # declaration
    "Scenario", "NodeSpec", "ModuleSpec", "Behavior", "PriorRun", "Wire",
    "chain", "fan_in", "fan_in_nodes", "selector_beside_method",
    "selector_beside_method_and_reference", "two_node_chain",
    "node", "reference", "selector", "wire", "exits", "completed",
    "DEFAULT_ENV_NAME", "DEFAULT_MODULE", "DEFAULT_SAMPLE", "DEFAULT_VARIANT",
    # observation and invariants
    "Observation", "Target", "TargetRun", "Project", "STUB_DIGEST",
    "INVARIANT_NAMES", "InvariantReport", "check_invariants",
]
