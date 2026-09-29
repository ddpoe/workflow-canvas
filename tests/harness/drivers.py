"""The three entry points, the phase enum, and the two fidelity rungs.

This is the only harness module that names a phase function or an expansion
function, so moving one touches one file and no test.

**Interpose, do not re-sequence.** ``through`` is implemented by calling
production ``run_step`` and interposing a stop at the phase *after* the
stopping point, reached through the same enum-keyed map. Production remains
the only sequencer: the cache-hit short-circuit, the ending propagation and
the outcomes-directory creation all stay where they are. A driver that
walked the map itself would be a second copy of that control flow, and a
drifted copy makes the harness observe something production never does.
"""
from __future__ import annotations

import inspect
import subprocess as _sp
import sys
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow

from tests.fixtures.fakes import interpose_phases, snapshot_output_writers
from tests.fixtures.fakes import stub_method_process as _stub_method_process
from wfc.graph import carries_sample_bundle

from .invariants import check_invariants
from .observe import (
    Observation,
    Target,
    TargetRun,
    max_run_id,
    read_observation,
    read_outcomes,
    read_output_rows,
)
from .project import (
    Project,
    _layout,
    build_project,
    commit_everything as _commit_everything,
)
from .scenario import Scenario


class Phase(Enum):
    """The five phases of processing one target, in execution order.

    ``through`` resolves against this ordering: running through a phase
    means running every phase at or before its position. The members are
    also the key of the driver's phase-to-entry-point map, so the map and
    the parameter cannot drift apart, and a phase rename fails every
    affected test at import in a single run.
    """

    CLAIM = 1
    MATERIALIZE = 2
    DISPATCH = 3
    COLLECT = 4
    RECORD = 5

    @property
    def label(self) -> str:
        """The phase's lower-case name, as the bundle reports it."""
        return self.name.lower()


#: Phase -> the attribute name the orchestrator imported the phase function
#: under. One member plus one entry is the whole cost of a phase rename.
PHASE_ENTRY_POINTS: dict[Phase, str] = {
    Phase.CLAIM: "run_claim",
    Phase.MATERIALIZE: "run_materialize",
    Phase.DISPATCH: "run_dispatch",
    Phase.COLLECT: "run_collect",
    Phase.RECORD: "run_record",
}

#: Fidelity rungs. Stub: harness-sequenced, method process faked at the
#: dispatch boundary only. Engine: the real engine sequences and the real
#: fixture container image runs the methods.
STUB = "stub"
ENGINE = "engine"


class _StopPhase(Exception):
    """Private stop signal raised by the interposed phase."""

    def __init__(self, phase: Phase):
        super().__init__(phase.label)
        self.phase = phase


# =============================================================================
# The single expansion indirection point
# =============================================================================

def _expansion():
    """Return the production expansion functions.

    The load is Execution's composer (``load_pipeline_from_path``:
    references, contract map and sample hashes fetched, then the Graph
    unit's pure load);
    the sort and the per-step expansion are the Graph unit's. Reaching
    them through one accessor means a move changes this function and no
    test.

    Returns:
        ``(load_pipeline_from_path, topo_sort_steps, expand_step_combos)``.
    """
    from wfc import execution, graph

    return (execution.load_pipeline_from_path,
            graph.topo_sort_steps,
            graph.expand_step_combos)


def _orchestrator():
    """Return the run-step orchestrator MODULE.

    ``wfc.execution`` re-exports the ``run_step`` *function* under the same
    name as its module, so the module has to be reached through the import
    machinery rather than by attribute access. Interposition patches the
    phase attributes on this module.

    Returns:
        The ``wfc.execution.run_step`` module.
    """
    import importlib

    return importlib.import_module("wfc.execution.run_step")


@task(purpose="Expand the project's pipeline for the seeding window only — "
              "before run ids are bound — standing a pending output in for "
              "each reference the document leaves blank so it loads; the "
              "document that is executed goes through the normal expansion "
              "afterwards",
      inputs="The built project, before prior runs have executed and before "
             "run ids are bound into its run_of references",
      outputs="[(step_def, (node_id, sample, variant)), ...] in execution order")
def expand_targets_unbound(project: Project) -> list[tuple[object, Target]]:
    """Expand the pipeline's targets for the seeding window, before run ids bind.

    A reference is the run it names, so the load refuses a document whose
    reference names none — and a ``run_of`` reference cannot name one until
    the run it points at has executed, which is what seeding does. For that
    window this stands a pending output in for each reference the document
    leaves blank, so the document parses and the prior runs can be read off
    it. Binding follows seeding, and the document that is actually executed
    goes through :func:`expand_targets`, the normal expansion, with every
    reference naming a real run. Only the step definitions are read from
    the result here: which prior run to execute, for which sample and
    variant, is the scenario's own declaration.

    Args:
        project: The built project, before its prior runs have run.

    Returns:
        ``[(step_def, (node_id, sample, variant)), ...]`` in execution
        order.
    """
    import json

    from wfc import graph
    from wfc.persistence import get_session
    from wfc.registration import load_contract_map
    from wfc.storage import resolve_run_reference_outputs

    _, topo_sort_steps, expand_step_combos = _expansion()
    document = json.loads(Path(project.pipeline_json).read_text())
    with get_session() as session:
        refs = resolve_run_reference_outputs(
            graph.reference_nodes(document), session)
        contract_map = load_contract_map(session)

    named: dict[str, set] = {}
    for link in document.get("links", []):
        slot = link.get("source_slot")
        if slot:
            named.setdefault(str(link["source"]), set()).add(slot)
    for ref_id, info in refs.items():
        if info.get("run_id"):
            continue
        info["run_id"] = "pending"
        info["output_paths"] = {slot: "" for slot in named.get(ref_id, ())} \
            or {"pending": ""}

    pdef = graph.load_pipeline(document, contract_map=contract_map,
                               reference_outputs=refs)
    steps = topo_sort_steps(pdef.steps)
    return [
        (step, (step.node_id, combo["sample"], combo["variant"]))
        for step, combo in expand_step_combos(
            steps, pdef.samples, pdef.param_sets, pdef.explicit_combos
        )
    ]


@task(purpose="Expand the project's pipeline into the ordered target list, "
              "using production's own loader, topo sort and variant expansion",
      inputs="The built project",
      outputs="[(step_def, (node_id, sample, variant)), ...] in execution order")
def expand_targets(project: Project) -> list[tuple[object, Target]]:
    """Expand the project's pipeline into the target list the engine would run.

    The harness never computes a target list of its own — whatever the
    engine would schedule, the driver schedules, because the same code
    decides.

    Production answers per step: a sample-collapsed step's combos sit at
    the collapsed sample, one per variant, and a per-sample step's are
    samples x variants. The harness takes that answer as given -- it does
    not expand the collapsed and per-sample subsets itself, so there is
    no second source of truth for a mixed pipeline to disagree with.

    Args:
        project: The built project.

    Returns:
        ``[(step_def, (node_id, sample, variant)), ...]`` in execution
        order.
    """
    load_pipeline, topo_sort_steps, expand_step_combos = _expansion()
    pdef = load_pipeline(project.pipeline_json)
    steps = topo_sort_steps(pdef.steps)

    return [
        (step, (step.node_id, combo["sample"], combo["variant"]))
        for step, combo in expand_step_combos(
            steps, pdef.samples, pdef.param_sets, pdef.explicit_combos
        )
    ]


# =============================================================================
# Entry points
# =============================================================================

@workflow(purpose="Build the scenario's project and run one target in it, "
                  "optionally stopping after a named phase",
          inputs="A scenario declaration and the target to run",
          outputs="The observation bundle, with the pack's report attached")
def run_target(
    scn: Scenario,
    target: str | Target,
    *,
    root: Path,
    monkeypatch,
    through: Phase | None = None,
    fidelity: str = STUB,
    waive: str | None = None,
    reason: str | None = None,
) -> Observation:
    """Build the scenario's project and run a single target in it.

    Build it, then drive it: the two acts are separately callable.
    ``build_project`` constructs the project and pins ``WFC_PROJECT_ROOT``,
    ``DATABASE_URL`` and the working directory to it; :func:`drive_target`
    drives one target in a project already on disk. A test whose subject is
    what the run resolves for *itself* — one driven from a root the harness
    did not pin — calls the two in turn and rearranges the environment in
    between.

    Args:
        scn: The scenario declaration.
        target: A node id, or a full ``(node_id, sample, variant)`` triple.
            A bare node id selects that node's first expanded target.
        root: Directory to build into.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        through: The phase to stop after. ``None`` runs the record phase.
        fidelity: ``STUB`` or ``ENGINE``.
        waive: Name of the one standing invariant this scenario is
            expected to violate.
        reason: Required justification for ``waive``.

    Returns:
        The observation bundle.
    """
    口 = AutoStep(step_num=1, name="Build project")
    project = build_project(scn, root=root, monkeypatch=monkeypatch)

    口 = AutoStep(step_num=2, name="Run the target in the built project")
    return drive_target(project, target, monkeypatch=monkeypatch,
                            through=through, fidelity=fidelity,
                            waive=waive, reason=reason)


@workflow(purpose="Run one target through production run_step in a project "
                  "that is already on disk, optionally stopping after a named "
                  "phase, and assert the standing invariant pack",
          inputs="A built project and the target to run",
          outputs="The observation bundle, with the pack's report attached")
def drive_target(
    project: Project,
    target: str | Target,
    *,
    monkeypatch,
    through: Phase | None = None,
    fidelity: str = STUB,
    waive: str | None = None,
    reason: str | None = None,
) -> Observation:
    """Run a single target in a project that is already built.

    Every phase at or before ``through`` runs for real; none is faked.
    Starting state comes from the scenario's ``prior_runs``, never from
    skipping a phase — so the preconditions the stopped-at phase saw are
    ones a run actually produced.

    Everything the run needs is read off the project handle rather than off
    the environment, so a caller may unset ``WFC_PROJECT_ROOT`` and move the
    working directory between building and driving. The run then resolves
    its own root exactly the way production does, which is the only way to
    observe a resolver that silently falls back to the cwd.

    Args:
        project: A project built by :func:`~tests.harness.build_project`.
        target: A node id, or a full ``(node_id, sample, variant)`` triple.
            A bare node id selects that node's first expanded target.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        through: The phase to stop after. ``None`` runs the record phase.
        fidelity: ``STUB`` or ``ENGINE``.
        waive: Name of the one standing invariant this scenario is
            expected to violate.
        reason: Required justification for ``waive``.

    Returns:
        The observation bundle.
    """
    口 = AutoStep(step_num=1, name="Seed prior runs")
    _seed_prior_runs(project, monkeypatch)

    口 = AutoStep(step_num=2, name="Bind run_of references to their seeded runs")
    _bind_reference_runs(project)

    口 = AutoStep(step_num=3, name="Expand targets")
    all_targets = expand_targets(project)

    口 = Step(step_num=4, name="Select target and snapshot the run-id baseline",
             purpose="The baseline is what tells the invariant pack which run "
                     "rows this execution wrote, as opposed to rows a prior "
                     "run left behind")
    chosen = _select_target(all_targets, target)
    baseline = max_run_id()

    口 = Step(step_num=5, name="Run the target through production run_step",
             purpose="Hand the one selected target to the per-target loop, "
                     "which installs the interpositions around the call",
             critical="Interposition happens inside; production stays the "
                      "only sequencer")
    runs = _run_targets(project, [chosen], monkeypatch, through=through,
                        fidelity=fidelity)

    口 = AutoStep(step_num=6, name="Read bundle and assert the invariant pack")
    return _finish(project, runs, baseline, waive=waive, reason=reason)


@workflow(purpose="Run every target the pipeline expands to, each to "
                  "completion, on either fidelity rung, then assert the "
                  "standing invariant pack",
          inputs="A scenario declaration and the fidelity rung",
          outputs="The observation bundle, with the pack's report attached")
def run_scenario(
    scn: Scenario,
    *,
    root: Path,
    monkeypatch,
    fidelity: str = STUB,
    waive: str | None = None,
    reason: str | None = None,
    image_digest: str | None = None,
    interrupted: str | Target | None = None,
    pipeline_end: bool = True,
) -> Observation:
    """Run every target the pipeline expands to, each to completion.

    ``through`` is deliberately not offered here: across a scenario the
    targets chain — a downstream target's inputs are an upstream target's
    recorded outputs — so stopping every target early would break the
    chain.

    Args:
        scn: The scenario declaration.
        root: Directory to build into.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        fidelity: ``STUB`` or ``ENGINE``.
        waive: Name of the one standing invariant this scenario is
            expected to violate.
        reason: Required justification for ``waive``.
        image_digest: Engine rung only — the bare sha256 hex of the built
            fixture image the methods run inside.
        interrupted: The hard-abort starting state, named as the target the
            engine was still running when it aborted. That target runs
            through the claim phase only, so the in-flight run row is the
            one production's own claim writes, and no later target is
            scheduled at all — which is what a fail-fast abort leaves
            behind. Distinct from a target that *failed*: the pipeline tail
            treats an in-flight row and a failed row differently, and only
            a run stopped inside the lifecycle produces the former.
        pipeline_end: When False the harness stops after the last target
            and does NOT run the pipeline-end cancelled-rows walk. For a
            test whose subject is ``run_pipeline``'s own call of that walk:
            the walk is idempotent, so a harness that ran it first would
            leave the call under test nothing to do and the test would be
            asserting against rows the harness wrote.

    Returns:
        The observation bundle.
    """
    口 = AutoStep(step_num=1, name="Build project")
    project = build_project(scn, root=root, monkeypatch=monkeypatch)

    if fidelity == ENGINE:
        口 = Step(step_num=2, name="Hand off to the engine rung",
                 purpose="On the engine rung the real engine sequences the "
                         "scenario in real containers, so none of the "
                         "stub-rung steps below apply",
                 critical="The engine rung interposes nothing — from here the "
                          "flow is _run_engine's, not this one's")
        return _run_engine(project, monkeypatch, image_digest=image_digest,
                           waive=waive, reason=reason)

    口 = AutoStep(step_num=3, name="Seed prior runs")
    _seed_prior_runs(project, monkeypatch)

    口 = AutoStep(step_num=4, name="Bind run_of references to their seeded runs")
    _bind_reference_runs(project)

    口 = Step(step_num=5, name="Snapshot the run-id baseline",
             purpose="Fixes the boundary between run rows a prior run left "
                     "behind and rows this execution writes, which is what "
                     "the invariant pack counts against")
    baseline = max_run_id()

    口 = AutoStep(step_num=6, name="Freeze the pipeline document")
    _freeze_pipeline_doc(project)

    口 = AutoStep(step_num=7, name="Expand targets")
    all_targets = expand_targets(project)

    口 = Step(step_num=8, name="Resolve the interrupted target",
             purpose="The hard-abort starting state: this target runs through "
                     "claim only and nothing after it is scheduled")
    stop_at: Target | None = (
        _select_target(all_targets, interrupted)[1]
        if interrupted is not None else None
    )

    口 = AutoStep(step_num=9, name="Run every target through production run_step")
    runs = _run_targets(project, all_targets, monkeypatch,
                        through=None, fidelity=fidelity, skip_on_failure=True,
                        interrupted=stop_at)

    if pipeline_end:
        口 = AutoStep(step_num=10, name="Pipeline-end cancelled-rows walk")
        _pipeline_end_walk(project)

    口 = AutoStep(step_num=11, name="Read bundle and assert the invariant pack")
    return _finish(project, runs, baseline, waive=waive, reason=reason)


@task(purpose="Copy the pipeline document into the pipeline run dir, standing "
              "in for the engine's launch so the cancelled-rows walk and the "
              "canvas history readers have the frozen document they resolve",
      inputs="The built project",
      outputs=".runs/pipelines/<pid>/pipeline.json")
def _freeze_pipeline_doc(project) -> None:
    """Copy the pipeline document into the pipeline run dir, as launch does.

    ``run_pipeline`` freezes the executed document at
    ``.runs/pipelines/<pid>/pipeline.json`` before scheduling anything; the
    pipeline-end cancelled-rows walk and the canvas history readers resolve
    that path. The stub rung stands in for the engine's launch, so it
    performs the same freeze rather than leaving the walk with nothing to
    reconcile.

    Args:
        project: The built project.
    """
    import shutil

    pipeline_dir = _layout().pipeline_run_dir(project.root, project.pipeline_id)
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project.pipeline_json,
                    _layout().pipeline_doc_path(project.root, project.pipeline_id))


@task(purpose="Run production's own pipeline-end cancelled-rows walk, exactly "
              "as run_pipeline calls it on both the success and failure paths",
      inputs="The built project",
      outputs="Cancelled Run rows for targets that never started")
def _pipeline_end_walk(project) -> None:
    """Run the production pipeline-end cancelled-rows walk.

    Always-on, exactly as ``run_pipeline`` calls it on both the success and
    the failure path: on a fully-successful scenario it finds nothing
    missing and writes nothing.

    Args:
        project: The built project.
    """
    from wfc.execution import lifecycle as _lifecycle

    _lifecycle._write_cancelled_rows(project.pipeline_id, str(project.root))


# =============================================================================
# Sequencing
# =============================================================================

@task(purpose="Read the observation bundle and assert the standing invariant "
              "pack against it — the one exit every rung shares",
      inputs="The project, the per-target records, and the run-id baseline",
      outputs="The observation bundle with the pack's report attached",
      critical="assert_clean() fails the test on any unwaived divergence, and "
               "also on a waived invariant that unexpectedly passed")
def _finish(project, runs, baseline, *, waive, reason) -> Observation:
    """Read the bundle and attach the standing invariant pack's report.

    Three products in three lines: the disk and database state becomes a
    bundle, the bundle becomes a report, and the report becomes a test
    verdict. This is the one exit every entry point and both fidelity
    rungs share.
    """
    口 = AutoStep(step_num=1, name="Read the observation bundle")
    obs = read_observation(project, runs, baseline_run_id=baseline)

    口 = AutoStep(step_num=2, name="Evaluate the standing invariant pack")
    obs.invariants = check_invariants(obs, waive=waive, reason=reason)

    口 = Step(step_num=3, name="Turn the report into a test verdict",
             purpose="The report is data until this call; assert_clean is "
                     "what makes the pack a gate rather than a diagnostic",
             outputs="The bundle, returned to the test — or a failed test",
             critical="Fails on any unwaived divergence, and ALSO on a waived "
                      "invariant that unexpectedly passed: an unexpected pass "
                      "means the divergence was resolved or the waiver was "
                      "never needed for this scenario")
    obs.invariants.assert_clean()
    return obs


@task(purpose="Re-read the bundle after a pipeline-level entry point and "
              "assert the standing pack against the rows actually in the "
              "database -- the post-pipeline door",
      inputs="A bundle from run_scenario, after the test itself has called "
             "run_pipeline (or the cancelled-rows walk) on the project",
      outputs="A fresh bundle with the pack's report attached",
      critical="Invariant 1 takes its artifact-derived expression here: a "
               "fresh query of the run table, terminal rows only, cancelled "
               "rows counting toward uniqueness and not toward the claimed "
               "count. Invariants 2 and 3 keep their observed-phase "
               "expressions over the phases the stub rung recorded")
def observe_after_pipeline(obs: Observation, *, waive: str | None = None,
                           reason: str | None = None) -> Observation:
    """Re-observe a scenario after a pipeline-level call and assert the pack.

    The pack evaluated at ``_finish`` is blind, by construction, to every
    row a later ``run_pipeline`` call writes -- notably the cancelled rows
    its pipeline-end walk reconciles. A test whose subject is that call
    runs the scenario with ``pipeline_end=False``, makes the call itself,
    then passes the bundle back through this door.

    Args:
        obs: The bundle ``run_scenario`` returned.
        waive: Name of the one standing invariant this scenario is
            expected to violate.
        reason: Required justification for ``waive``.

    Returns:
        A fresh bundle over the same targets and baseline, with the
        pack's report attached -- or a failed test.
    """
    fresh = read_observation(obs.project, obs.runs,
                             baseline_run_id=obs.baseline_run_id)
    fresh.invariants = check_invariants(fresh, waive=waive, reason=reason,
                                        after_pipeline=True)
    fresh.invariants.assert_clean()
    return fresh


def _select_target(all_targets, target) -> tuple[object, Target]:
    """Resolve a node id or full triple against the expanded target list."""
    if isinstance(target, str):
        for entry in all_targets:
            if entry[1][0] == target:
                return entry
        raise KeyError(f"node {target!r} expands to no target "
                       f"(have {[t for _, t in all_targets]})")
    for entry in all_targets:
        if entry[1] == tuple(target):
            return entry
    raise KeyError(f"target {target!r} is not in the expanded list "
                   f"(have {[t for _, t in all_targets]})")


def _step_kwargs(project, step, key: Target) -> dict:
    """Build the ``run_step`` keyword arguments for one target.

    ``ref_inputs`` is read off the loaded step rather than off the scenario:
    the generator is what turns a ``run_reference`` link into a
    ``--ref-input <slot>=<path>`` flag, so asking the loaded step keeps that
    resolution inside production code.

    ``collapsed_samples`` is gated by ``wfc.graph.carries_sample_bundle`` --
    the same predicate the generated Snakefile's ``--collapsed-sample``
    flags and sentinel ``input:`` block read. ``step.collapsed_samples``
    alone is not the generator's answer: collapse is contagious, so a step
    further down a fan-in chain carries the list for path and variant
    purposes while the generator hands it no bundle. Reading the raw field
    gave a chain-collapsed step a bundle in the harness that production
    never gives it, and the two rungs computed different cache keys for the
    same step.

    ``git_commit`` is deliberately NOT supplied, for a related reason. No
    production caller supplies it — the generated Snakefile emits no
    ``--git-commit`` flag — so the claim phase always derives it itself,
    and deriving it is where the dirty-working-tree check lives. A driver
    that handed the commit over would skip that check on every clean
    scenario and would make the dirty scenario fail because the harness
    said so rather than because the tree was dirty.

    Args:
        project: The built project.
        step: The loaded step definition.
        key: The ``(node_id, sample, variant)`` target.

    Returns:
        The keyword arguments to hand ``run_step``.
    """
    # slot -> [path, ...]: one flag per occurrence, with the slot name
    # repeated, exactly as the generated Snakefile's shell emits it.
    refs = [f"{label}={path}"
            for label, paths in (getattr(step, "run_ref_inputs", None) or {}).items()
            for path in paths]
    return {
        "node_id": step.node_id,
        "sample": key[1],
        "variant": key[2],
        "pipeline_json": str(project.pipeline_json),
        "pipeline_id": project.pipeline_id,
        "ref_inputs": refs or None,
        "collapsed_samples": (list(step.collapsed_samples)
                              if carries_sample_bundle(step) else None),
    }


@workflow(purpose="Run each target through production run_step in schedule "
                  "order, with the harness's three interpositions installed "
                  "around each call, leaving a failed target's descendants "
                  "unscheduled so the cancelled-rows walk has something to "
                  "reconcile",
          inputs="The built project and the ordered target list",
          outputs="Target -> its execution record",
          critical="The harness never re-sequences: it rebinds the phase "
                   "attributes and lets production decide which phase runs "
                   "next, so a cache hit still jumps claim -> record")
def _run_targets(project, targets, monkeypatch, *, through, fidelity,
                 skip_on_failure: bool = False,
                 interrupted: Target | None = None):
    """Run each target through production ``run_step``, in order.

    Args:
        project: The built project.
        targets: ``[(step_def, target), ...]`` in execution order.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        through: The stopping point, or ``None``.
        fidelity: ``STUB`` or ``ENGINE``.
        skip_on_failure: When True, a target whose combo has a failed
            upstream target is not scheduled — the engine does not run a
            job whose inputs never arrived. Those targets are what the
            pipeline-end cancelled-rows walk reconciles.
        interrupted: The target the engine was mid-way through when it
            aborted. It runs through the claim phase only and every target
            after it in the schedule is left unscheduled.

    Returns:
        Target -> its execution record.
    """
    口 = Step(step_num=1, name="Resolve the orchestrator module and the "
                              "ancestor map",
             purpose="The module is reached through importlib because "
                     "wfc.execution re-exports the run_step FUNCTION under "
                     "the module's own name; the ancestor map is what decides "
                     "which targets a failure leaves unscheduled")
    orchestrator = _orchestrator()
    ancestors = _ancestor_map(targets)

    runs: dict[Target, TargetRun] = {}
    failed: list[Target] = []
    aborted = False

    口 = Step(step_num=2, name="Run each target in schedule order",
             purpose="One pass over the expanded target list; each iteration "
                     "decides schedulability, interposes, runs, and records")
    for step, key in targets:
        口 = Step(step_num=2.1, name="Decide whether this target is scheduled",
                 purpose="A target after the abort point, or one whose "
                         "upstream failed, is never scheduled — the engine "
                         "does not run a job whose inputs never arrived. "
                         "Those are what the cancelled-rows walk reconciles")
        record = TargetRun(target=key)
        runs[key] = record
        if aborted or (skip_on_failure and _blocked_by(key, failed, ancestors)):
            record.skipped = True
            continue

        口 = Step(step_num=2.2,
                 name="Interpose, then hand the target to production run_step",
                 purpose="Three patches go on, all through monkeypatch: "
                         "_interpose rebinds the five phase attributes to "
                         "record-then-delegate wrappers; "
                         "_snapshot_output_writers wraps record.complete_run to "
                         "capture RunOutput rows either side of it; "
                         "_stub_method_process replaces the container launch "
                         "with a local subprocess (stub rung only)",
                 outputs="The target's rc, its observed phases, and whatever "
                         "the three interpositions recorded on the way past",
                 critical="A stop is a _StopPhase raised by the wrapper for "
                          "the phase AFTER the stopping point, which unwinds "
                          "out of run_step and lands rc=None — production is "
                          "never asked to stop early")
        stop = Phase.CLAIM if key == interrupted else through
        with interpose_phases(orchestrator, record, stop, monkeypatch), \
                snapshot_output_writers(record, monkeypatch), \
                _stub_method_process(project, step, record, monkeypatch, fidelity):
            try:
                record.rc = orchestrator.run_step(**_step_kwargs(project, step, key))
            except _StopPhase:
                record.rc = None

        口 = Step(step_num=2.3,
                 name="Record the verdict and attach the per-run logs",
                 purpose="rc=None means stopped at a phase boundary and is "
                         "not a failure; any other non-zero rc marks the "
                         "target failed and blocks its descendants")
        if key == interrupted:
            aborted = True
        if record.rc not in (0, None):
            failed.append(key)
        _attach_logs(project, record)
    return runs


def _ancestor_map(targets) -> dict[str, set[str]]:
    """Return each node's transitive upstream node ids.

    Args:
        targets: ``[(step_def, target), ...]``.

    Returns:
        Node id -> the set of node ids upstream of it.
    """
    direct = {step.node_id: set(step.depends_on) for step, _ in targets}
    resolved: dict[str, set[str]] = {}

    def walk(nid: str) -> set[str]:
        if nid in resolved:
            return resolved[nid]
        resolved[nid] = set()
        out: set[str] = set()
        for parent in direct.get(nid, ()):
            out.add(parent)
            out |= walk(parent)
        resolved[nid] = out
        return out

    for nid in direct:
        walk(nid)
    return resolved


def _blocked_by(key: Target, failed: list[Target],
                ancestors: dict[str, set[str]]) -> bool:
    """Report whether a failed upstream target blocks this one.

    Args:
        key: The target under consideration.
        failed: Targets that have already failed.
        ancestors: Node id -> transitive upstream node ids.

    Returns:
        True when some failed target is upstream of this one on the same
        variant and a matching sample axis (a collapsed target consumes
        every sample, so ``__all__`` matches on either side).
    """
    node_id, sample, variant = key
    upstream = ancestors.get(node_id, set())
    for f_node, f_sample, f_variant in failed:
        if f_node not in upstream or f_variant != variant:
            continue
        if f_sample == sample or "__all__" in (f_sample, sample):
            return True
    return False


@task(purpose="Rebind every phase entry point on the orchestrator module to a "
              "record-then-delegate wrapper — the interposition that lets the "
              "harness watch production without sequencing it",
      inputs="The orchestrator module, the target's record, and the stopping "
             "point",
      outputs="record.phases_ran and record.phase_args, filled in as "
              "production reaches each phase",
      critical="The orchestrator's own control flow is untouched, so which "
               "phase is reached first is PRODUCTION's decision — a cache hit "
               "jumps from claim straight to record. A phase past the stopping "
               "point raises _StopPhase instead of delegating, which unwinds "
               "out of run_step; production is never asked to stop early")
@contextmanager
def _interpose(orchestrator, record: TargetRun, through: Phase | None,
               monkeypatch):
    """Wrap every phase entry point on the orchestrator module.

    The wrapper records the phase as having run and the arguments it was
    handed, then either delegates to the real phase function or — for a
    phase past the stopping point — raises the private stop signal. The
    orchestrator's own control flow is untouched, so which phase is
    reached first (and therefore stops the run) is production's decision:
    a cache hit jumps from claim straight to record.

    The phase functions are module globals of ``wfc.execution.run_step``,
    and ``run_step`` resolves those names at call time — so rebinding the
    module attribute is what redirects the call, with the function body
    untouched. ``PHASE_ENTRY_POINTS`` holds the names as strings, which is
    why the rebinding goes through ``setattr`` rather than five literal
    assignments.

    Args:
        orchestrator: The ``wfc.execution.run_step`` module.
        record: The target's execution record.
        through: The stopping point, or ``None``.
        monkeypatch: An active ``pytest.MonkeyPatch``. Every patch the
            harness installs goes through it, so restoration is pytest's
            responsibility rather than a hand-written ``finally`` — which
            matters here because ``_StopPhase`` unwinds through this
            manager on every stopped run.

    Yields:
        None, with the wrappers installed.
    """
    originals = {
        phase: getattr(orchestrator, attr)
        for phase, attr in PHASE_ENTRY_POINTS.items()
    }

    def make(phase: Phase, real):
        def wrapper(*args, **kwargs):
            # Production calls every phase by keyword today; a positional
            # call is still production's call to make, so it is recorded
            # under the phase function's own parameter names and delegated
            # rather than rejected by the wrapper's signature.
            seen = _name_arguments(real, args, kwargs)
            record.phase_args[phase.label] = seen
            if through is not None and phase.value > through.value:
                record.stopped_at = phase.label
                raise _StopPhase(phase)
            record.phases_ran.append(phase.label)
            if phase is Phase.RECORD:
                record.ending = seen.get("ending")
                record.run_id = seen.get("run_id", record.run_id)
            result = real(*args, **kwargs)
            if phase is Phase.CLAIM and isinstance(result, dict):
                record.run_id = result.get("run_id")
            return result
        return wrapper

    with monkeypatch.context() as mp:
        for phase, attr in PHASE_ENTRY_POINTS.items():
            mp.setattr(orchestrator, attr, make(phase, originals[phase]))
        yield


def _name_arguments(func, args: tuple, kwargs: dict) -> dict:
    """Record a phase call's arguments under the phase function's own names.

    A keyword call comes back as the same dict it was made with; a
    positional call is bound against ``func``'s signature so the record
    reads the same either way. A call the signature cannot bind is still
    recorded (positionals under ``"*args"``) and left for ``func`` itself
    to reject.

    Args:
        func: The real phase function being delegated to.
        args: Positional arguments of the call.
        kwargs: Keyword arguments of the call.

    Returns:
        Parameter name -> argument value.
    """
    if not args:
        return dict(kwargs)
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return {"*args": list(args), **kwargs}
    return dict(bound.arguments)


@task(purpose="Wrap record.complete_run so the RunOutput rows either side of the "
              "second writer's call are captured — the executable form of the "
              "pack's third invariant",
      inputs="The target's execution record and an active MonkeyPatch",
      outputs="record.output_rows_before / output_rows_after, and the "
              "completion-write count",
      critical="collect is the sole RunOutput writer in the execution flow; "
               "complete_run keeps its own writer for standalone CLI-verb use "
               "and is handed no output_files here, so its re-upsert loop must "
               "be a no-op on every column")
@contextmanager
def _snapshot_output_writers(record: TargetRun, monkeypatch):
    """Snapshot the output rows either side of the second writer's call.

    ``collect`` is the sole ``RunOutput`` writer in the execution flow;
    ``complete_run`` keeps its own writer for its standalone CLI-verb
    behavior and is handed no ``output_files`` here, so its re-upsert loop
    must be a no-op on every column. Comparing the two snapshots is the
    executable form of the pack's third invariant.

    Args:
        record: The target's execution record.
        monkeypatch: An active ``pytest.MonkeyPatch``, for the same reason
            as :func:`_interpose`.

    Yields:
        None, with the wrapper installed.
    """
    from wfc.execution import record as _record

    real = _record.complete_run

    def snapshotting(**kwargs):
        if kwargs.get("status") != "completed":
            return real(**kwargs)
        record.completion_writes += 1
        before = read_output_rows(kwargs["run_id"])
        result = real(**kwargs)
        after = read_output_rows(kwargs["run_id"])
        if before:
            record.output_rows_before = before
            record.output_rows_after = after
        return result

    with monkeypatch.context() as mp:
        mp.setattr(_record, "complete_run", snapshotting)
        yield


def _attach_logs(project, record: TargetRun) -> None:
    """Point the record at the per-run logs, when the run got that far."""
    args = record.phase_args.get("dispatch") or {}
    run_id = args.get("run_id")
    if run_id is None:
        return
    from wfc.persistence import project_root as get_project_root
    from wfc import layout

    run_dir = layout.run_archive_dir(get_project_root(), run_id)
    for attr, name in (("stdout_log", "stdout.log"), ("stderr_log", "stderr.log")):
        path = run_dir / name
        if path.exists():
            setattr(record, attr, path)


@task(purpose="Produce the scenario's starting state by RUNNING its declared "
              "prior runs and then deleting what it declares missing — never "
              "by skipping a phase",
      inputs="The built project and its scenario's prior_runs",
      outputs="Run rows, archives and sentinels a real run wrote",
      critical="The archive a later phase finds missing is one a real run "
               "created, so the preconditions under test are reachable ones")
def _seed_prior_runs(project, monkeypatch) -> None:
    """Run the scenario's declared prior runs, then apply their mutations.

    Starting state is produced by running, never by skipping a phase: the
    archive a later phase finds missing is one a real run wrote and this
    function then deleted.

    Args:
        project: The built project.
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    import shutil

    prior = project.scenario.prior_runs
    if not prior:
        return

    口 = Step(step_num=1, name="Index the expanded targets by node id",
             purpose="A prior run names a node; the target it corresponds to "
                     "comes from production's own expansion, run here with a "
                     "pending output standing in for each reference whose run "
                     "id seeding has not bound yet, so a seeded run and a "
                     "scheduled run are the same kind of thing",
             outputs="node id -> (step_def, target)")
    by_node = {t[0]: (step, t) for step, t in expand_targets_unbound(project)}

    口 = Step(step_num=2, name="Seed each declared prior run",
             purpose="One pass over the scenario's prior_runs: each is "
                     "executed for real, then mutated to whatever absence the "
                     "scenario declares",
             outputs="Run rows, archives and sentinels a real run wrote — "
                     "minus what the scenario declares missing")
    for seed in prior:
        口 = Step(step_num=2.1,
                 name="Bind every reference whose run has been seeded, then "
                      "re-expand",
                 purpose="A prior run may itself consume a run_of reference "
                         "to an earlier prior run. Binding each reference as "
                         "soon as its run exists, and expanding again, hands "
                         "the seeded step the artifact path the load resolves "
                         "for the bound run -- the same step a scheduled run "
                         "of it would get -- instead of the pending stand-in",
                 outputs="The document's references bound as far as seeding "
                         "has got, and node id -> (step_def, target) re-read",
                 critical="Only references whose run has already executed "
                          "are bound here; the rest keep their stand-in until "
                          "their run is seeded or the strict binding after "
                          "seeding refuses them")
        if _bind_reference_runs(project, available_only=True):
            by_node = {t[0]: (step, t)
                       for step, t in expand_targets_unbound(project)}

        口 = Step(step_num=2.2,
                 name="Run it through production run_step, under interposition",
                 purpose="The same orchestrator, the same phases and the same "
                         "stub method process the driver uses for the targets "
                         "under test — a seeded run is not a cheaper "
                         "imitation of a run",
                 outputs="A completed run: its row, its archive, its sentinel "
                         "and its run_id sidecar",
                 critical="Runs to completion with through=None. Starting "
                          "state is never produced by stopping a run early")
        step, _ = by_node[seed.node]
        key: Target = (seed.node, seed.sample, seed.variant)
        record = TargetRun(target=key)
        with interpose_phases(_orchestrator(), record, None, monkeypatch), \
                _stub_method_process(project, step, record, monkeypatch, STUB):
            _orchestrator().run_step(**_step_kwargs(project, step, key))

        口 = Step(step_num=2.3, name="Apply the declared absences",
                 purpose="Delete the archive or the sentinel the run just "
                         "wrote, as the scenario declares",
                 outputs="The starting state the scenario asked for",
                 critical="This is what makes the precondition REACHABLE: the "
                          "archive a later phase finds missing is one a real "
                          "run created and this deleted, not one that never "
                          "existed because a phase was skipped")
        if seed.archive_deleted:
            run_id = project.sentinel_dir(seed.node, seed.sample, seed.variant)
            sidecar = run_id / "run_id.txt"
            if sidecar.exists():
                from wfc.persistence import project_root as get_project_root
                from wfc import layout
                archive = layout.run_archive_dir(
                    get_project_root(), int(sidecar.read_text().strip()))
                shutil.rmtree(archive, ignore_errors=True)
        if seed.sentinel_deleted:
            shutil.rmtree(
                project.sentinel_dir(seed.node, seed.sample, seed.variant),
                ignore_errors=True,
            )


@task(purpose="Bind each run_of reference to the run its pipeline-qualified "
              "locator names, writing that run's identity into the pipeline "
              "document",
      inputs="The built project, after its prior runs have executed",
      outputs="The document's run_reference nodes carrying real run ids",
      critical="Runs before the document is read and after the prior runs "
               "have executed, so the run id in the document is one a real "
               "run produced rather than a fabricated number. The sidecar is "
               "read under the locator's pipeline, not the project's, so a "
               "reference across pipelines binds to the run the other "
               "pipeline recorded")
def _bind_reference_runs(project, *, available_only: bool = False) -> bool:
    """Resolve each ``run_of`` reference against the run that produced it.

    A ``run_reference`` node names a run id in the pipeline document, and
    the document is written before anything executes — so a scenario whose
    reference points at one of its own seeded prior runs, or at a run
    another scenario over the same root produced under its own pipeline
    id, cannot state that run's id up front. This closes the loop after
    seeding: the run's identity comes off the sidecar it wrote under the
    locator's pipeline (this project's unless the reference names another),
    and the load resolves that run's outputs from the records it recorded.

    Seeding binds in the same way while it runs (``available_only``), so a
    prior run that consumes a reference to an earlier prior run is seeded
    against the run it names rather than against a stand-in.

    Args:
        project: The built project.
        available_only: Bind only the references whose run has already
            left its sidecar, leaving the rest for a later call, instead of
            refusing a reference whose run is not there.

    Returns:
        True when the document was rewritten.
    """
    import json

    refs = [n for n in project.scenario.nodes
            if n.type == "run_reference" and n.run_of is not None]
    if not refs:
        return False

    doc = json.loads(Path(project.pipeline_json).read_text())
    by_id = {str(n["id"]): n for n in doc["nodes"]}
    changed = False
    for spec in refs:
        node_id, sample, variant = spec.run_of_target
        pipeline_id = spec.run_of_pipeline or project.pipeline_id
        sidecar = _layout().run_sentinel_dir(
            project.root, pipeline_id, node_id, sample, variant) / "run_id.txt"
        if not sidecar.exists():
            if available_only:
                continue
            raise KeyError(
                f"reference {spec.id!r} names run_of={spec.run_of!r} under "
                f"pipeline {pipeline_id!r}, but that target left no run_id "
                f"sidecar — declare it in prior_runs, or run that pipeline "
                f"over this root first"
            )
        run_id = str(int(sidecar.read_text().strip()))
        entry = by_id[spec.id]
        if entry.get("run_id") != run_id:
            entry["run_id"] = run_id
            changed = True
    if not changed:
        return False
    Path(project.pipeline_json).write_text(json.dumps(doc, indent=2))
    # The document is a tracked file and pre_run refuses a dirty tree, so the
    # rewrite is committed exactly as build_project committed the original.
    _commit_everything(project.root)
    return True


# =============================================================================
# Engine rung
# =============================================================================

#: Repository root — the path worker processes need on ``PYTHONPATH`` for
#: ``import wfc`` to resolve, which is NOT the scenario's project root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@workflow(purpose="Sequence the scenario through the real engine, in real "
                  "containers, interposing nothing",
          inputs="The built project and the built fixture image digest",
          outputs="The observation bundle — the same shape the stub rung "
                  "returns",
          critical="phases_ran is empty on this rung because the harness did "
                   "not watch the phases. The two phase-derived invariants "
                   "report themselves skipped rather than asserting against "
                   "a fiction")
def _run_engine(project, monkeypatch, *, image_digest, waive, reason):
    """Sequence the scenario through the real engine, in real containers.

    The harness interposes nothing here: the engine schedules its own jobs
    in its own worker processes, so each target's record is reconstructed
    from what production left behind rather than from observed phases. Two
    consequences the bundle reports honestly:

    * ``phases_ran`` is empty. The harness did not watch the phases, so it
      does not claim to have. The two phase-derived invariants therefore
      report themselves as skipped on this rung instead of asserting
      against a fiction.
    * ``rc`` is the engine's own per-job verdict: ``run_pipeline`` returning
      without raising is Snakemake's statement that every scheduled job ran
      to completion. That signal is independent of the sentinel, the run
      row and the outcome sidecar, which is what makes it usable as the
      reference the completion-signal invariant compares against.

    Args:
        project: The built project.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        image_digest: Bare sha256 hex of the built fixture image.
        waive: Name of the waived invariant, or ``None``.
        reason: Required justification for ``waive``.

    Returns:
        The observation bundle — the same shape the stub rung returns.
    """
    from tests.fixtures.conftest import write_env_record

    from .project import commit_everything

    口 = Step(step_num=1, name="Resolve and re-pin the fixture image digest",
             purpose="The engine runs the methods inside the real fixture "
                     "image, so the env record must name the digest that was "
                     "actually built",
             critical=".wfc/envs.json is tracked, so re-pinning it dirties the "
                      "working tree and the claim phase's clean-tree check "
                      "would fail every target — hence the commit")
    digest = image_digest or project.scenario.image_digest
    if digest is None:
        raise ValueError("the engine rung needs the built fixture image digest")
    if digest != project.scenario.image_digest:
        write_env_record(project.root, project.env_name, digest=digest)
        commit_everything(project.root)

    from wfc.execution import run_pipeline

    口 = Step(step_num=2, name="Snapshot the run-id baseline",
             purpose="Same boundary the stub rung takes, so the invariant "
                     "pack counts the same population on both rungs")
    baseline = max_run_id()

    口 = Step(step_num=3, name="Run the real engine over the pipeline",
             purpose="run_pipeline returning without raising is Snakemake's "
                     "statement that every scheduled job completed — a signal "
                     "independent of the sentinel, the run row and the "
                     "outcome sidecar, which is what makes it usable as the "
                     "reference the completion-signal invariant compares "
                     "against",
             outputs="Whether the engine reported a failure, carried into "
                     "step 4's reconstruction",
             critical="A failing job makes run_pipeline RAISE, so the raise is "
                      "caught rather than escaping: an uncaught one unwinds "
                      "before any bundle exists, which would make a failing "
                      "scenario inexpressible on this rung. The catch "
                      "is narrow on purpose — it records that some scheduled "
                      "job did not complete and changes nothing on the "
                      "success path")
    engine_failed = False
    try:
        run_pipeline(
            pipeline_path=str(project.pipeline_json),
            project_root=str(project.root),
            wfc_root=str(_REPO_ROOT),
            cores=1,
            pipeline_id=project.pipeline_id,
            archive=False,
        )
    except RuntimeError:
        engine_failed = True

    口 = Step(step_num=4, name="Reconstruct each target's record from disk",
             purpose="No phase was observed, so the run identity is read back "
                     "from production's own sentinel sidecar rather than from "
                     "an instrumented claim",
             critical="rc is read off the engine's whole-pipeline verdict on "
                      "the success path, NOT off the sidecar — that "
                      "independence is what lets the completion-signal "
                      "invariant compare the sidecar against something other "
                      "than itself. Only when the engine reported a failure "
                      "does the per-target verdict come from the sidecars, "
                      "because the whole-pipeline signal can no longer say "
                      "which targets it covers. A target with no sidecar on "
                      "that path was never scheduled")
    outcomes = read_outcomes(project) if engine_failed else {}
    runs: dict[Target, TargetRun] = {}
    for _step, key in expand_targets(project):
        record = TargetRun(target=key)
        sidecar = project.sentinel_dir(*key) / "run_id.txt"
        if sidecar.exists():
            record.run_id = int(sidecar.read_text().strip())
        if not engine_failed:
            record.rc = 0
        elif key not in outcomes:
            record.skipped = True
        else:
            record.rc = 1 if outcomes[key].get("status") == "failed" else 0
        runs[key] = record

    口 = AutoStep(step_num=5, name="Read bundle and assert the invariant pack")
    return _finish(project, runs, baseline, waive=waive, reason=reason)


__all__ = [
    "ENGINE", "PHASE_ENTRY_POINTS", "Phase", "STUB",
    "expand_targets", "observe_after_pipeline", "run_scenario", "run_target",
]
