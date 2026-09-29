"""Runs -- produced by production's own phases on the harness's stub rung.

``tests.harness`` is imported lazily throughout: the harness's project
builder imports ``tests.fixtures.conftest``, which re-exports this package,
so a top-level import here would be circular.

Pins / does not prove lines are on each builder's docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from axiom_annotations import AutoStep, Step, workflow

if TYPE_CHECKING:  # pragma: no cover - typing only; the harness imports this module
    from tests.harness import Observation, Phase, Project, Scenario


#: Pipeline id the one-node route scenarios run under.
ROUTE_PIPELINE_ID = "route-pipeline"
#: Document name (the pipeline JSON filename) the route scenarios build.
ROUTE_PIPELINE_NAME = "route"
#: Node id / method name a route run declares when the caller names none.
ROUTE_METHOD = "route_method"


@dataclass
class DrivenRun:
    """The identity of one run a route drove through production.

    Everything a test reads off this is what production recorded -- the
    row, the output rows, the archive location -- read back through the
    harness bundle and ``wfc.layout``, never composed here.

    Attributes:
        run_id: The ``runs`` row id the claim phase registered.
        target: The ``(node_id, sample, variant)`` the route drove.
        project: The built project handle (root, method ids, sample ids,
            the scenario it was built from).
        observation: The harness bundle for this drive, with the standing
            invariant pack's report attached.
    """

    run_id: int
    target: tuple[str, str, str]
    project: "Project"
    observation: "Observation"

    @property
    def root(self) -> Path:
        """The project root the run lives in."""
        return self.project.root

    @property
    def run_row(self) -> dict:
        """The ``Run`` row as the bundle read it, every column."""
        return next(r for r in self.observation.run_rows if r["id"] == self.run_id)

    @property
    def status(self) -> str:
        """The row's status as production left it."""
        return self.run_row["status"]

    @property
    def cache_key(self) -> str:
        """The key the claim phase composed for this run."""
        return self.run_row["cache_key"]

    @property
    def archive_dir(self) -> Path:
        """The run's archive directory, from the layout catalog."""
        from wfc import layout

        return layout.run_archive_dir(self.project.root, self.run_id)

    @property
    def output_rows(self) -> list[dict]:
        """The ``RunOutput`` rows the collect phase recorded, by output name."""
        return self.observation.output_rows_for_run(self.run_id)

    def output_path(self, slot: str) -> Path:
        """Return the recorded artifact path of one output slot.

        Args:
            slot: The declared output slot.

        Returns:
            The ``artifact_path`` the collect phase recorded for it.

        Raises:
            KeyError: When no output row carries that slot.
        """
        for row in self.output_rows:
            if row["slot"] == slot:
                return Path(row["artifact_path"])
        raise KeyError(f"run {self.run_id} recorded no output for slot {slot!r} "
                       f"(have {[r['slot'] for r in self.output_rows]})")


def _route_scenario(
    *,
    method: str,
    module: str | None,
    sample: str | None,
    samples: list[str] | None,
    outputs: dict[str, str] | None,
    params: dict | None,
    label: str | None,
    pipeline_id: str,
    name: str,
    node_kwargs: dict,
) -> "Scenario":
    """Declare the smallest scenario that names one method on one sample.

    A selector root feeding one method node -- the shape every hand-seeded
    run stood for. Every field defaults to the harness's own default, so a
    caller declares only what its test varies.

    Pins: the topology -- one selector root feeding one method node.
    Does not prove: anything about a wider topology; a caller with one
    hands ``completed_run`` its own scenario.

    Args:
        method: Method name; also the node id.
        module: Owning module (the harness default when ``None``).
        sample: The sample the run is driven on (the harness default when
            ``None``).
        samples: Every sample the project registers. Defaults to just
            ``sample``.
        outputs: Declared output slot -> type. The harness default
            (``{"data": ".csv"}``) when ``None``.
        params: Node-level params.
        label: Canvas label; ``Run.nid`` is stamped from it.
        pipeline_id: Pipeline execution id.
        name: Document name.
        node_kwargs: Any other :class:`NodeSpec` field.

    Returns:
        The scenario declaration.
    """
    from tests.harness import Scenario, node, selector, wire
    from tests.harness.scenario import (
        DEFAULT_MODULE, DEFAULT_SAMPLE, SELECTOR_ID,
    )

    sample = sample or DEFAULT_SAMPLE
    spec_kwargs: dict = dict(
        method=method,
        module=module or DEFAULT_MODULE,
        inputs=[wire(SELECTOR_ID)],
        params=dict(params or {}),
        label=label,
        **node_kwargs,
    )
    if outputs is not None:
        spec_kwargs["outputs"] = dict(outputs)
    return Scenario(
        nodes=[selector(), node(method, **spec_kwargs)],
        samples=list(samples) if samples else [sample],
        pipeline_id=pipeline_id,
        name=name,
    )


@workflow(purpose="Produce one run by building the declared scenario's project "
                  "and driving one target through production run_step, stopping "
                  "after a named phase when asked -- the one mechanism under "
                  "completed_run and claimed_run",
          inputs="A project root, a scenario, the target to drive, and the "
                 "stopping phase",
          outputs="The run's identity, read back from what production recorded")
def _drive_declared_run(
    root: Path,
    *,
    monkeypatch,
    scenario: "Scenario",
    target,
    through: "Phase | None",
) -> DrivenRun:
    """Build the scenario over ``root`` and drive one target.

    Repeatable over a root that already ran: ``build_project`` re-runs
    ``init_project`` (idempotent), re-registers methods (an upsert),
    re-registers a sample only when its declared content changed, and
    commits the tree again, so a second call drives a second target -- or
    a second claim of the same one -- beside the first.

    Pins: everything ``build_project`` and ``drive_target`` pin -- the
    stub rung's method process, cwd and the ``WFC_*`` environment, the
    committed tree, the standing invariant pack.
    Does not prove: see ``completed_run``; nothing past the stopping phase.

    Args:
        root: Project root. Built when it is not a project yet.
        monkeypatch: The test's ``MonkeyPatch``; cwd and the ``WFC_*``
            environment stay pinned to ``root`` for the test's duration.
        scenario: The declaration to build and drive.
        target: A node id, or a full ``(node_id, sample, variant)`` triple.
        through: The phase to stop after, or ``None`` to run to completion.

    Returns:
        The driven run's identity.

    Raises:
        AssertionError: When the drive registered no run at all.
    """
    from tests.harness import build_project, drive_target

    口 = AutoStep(step_num=1, name="Build the scenario's project")
    project = build_project(scenario, root=root, monkeypatch=monkeypatch)

    口 = AutoStep(step_num=2, name="Drive the target through production run_step")
    observation = drive_target(project, target, monkeypatch=monkeypatch,
                               through=through)

    口 = Step(step_num=3, name="Read the run identity off the bundle",
             purpose="The run id is the one the claim phase returned and the "
                     "harness recorded; nothing here invents an identity",
             outputs="The DrivenRun handle")
    (key, record), = observation.runs.items()
    if record.run_id is None:
        raise AssertionError(
            f"driving {key} registered no run (phases ran: {record.phases_ran}, "
            f"rc={record.rc}); the claim phase never returned an id"
        )
    return DrivenRun(run_id=int(record.run_id), target=key, project=project,
                     observation=observation)


@workflow(purpose="A completed run: the claim, materialize, dispatch, collect "
                  "and record phases run for real over a declared method and "
                  "sample, on the harness's stub rung",
          inputs="A project root and the run's declaration",
          outputs="The run's identity -- row, outputs and archive as production "
                  "recorded them")
def completed_run(
    root: Path,
    *,
    monkeypatch,
    method: str = ROUTE_METHOD,
    module: str | None = None,
    sample: str | None = None,
    samples: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    params: dict | None = None,
    label: str | None = None,
    pipeline_id: str = ROUTE_PIPELINE_ID,
    name: str = ROUTE_PIPELINE_NAME,
    scenario: "Scenario | None" = None,
    target=None,
    **node_kwargs,
) -> DrivenRun:
    """A run that ran, produced by production's own five phases.

    The unit-tier wrapper on the scenario harness's route: the smallest
    scenario that names the method (module, method name, output slots,
    params, sample) is built by :func:`tests.harness.build_project` and
    driven to completion by :func:`tests.harness.drive_target`. The run
    row, its ``cache_key``, its ``RunOutput`` rows and its archive are
    what the claim and collect phases wrote. A caller with a topology
    of its own hands over ``scenario`` and ``target`` instead of the
    keyword declaration.

    Repeatable over one root: each call rebuilds (idempotently) and drives
    one target, so several targets can be completed in order beside each
    other. The same method, sample and params twice is a cache hit -- an
    audit row, not a second execution.

    Pins: the method process -- the harness's stub rung runs the node's
    generated script as a local subprocess in place of the container
    launch (``drivers._stub_method_process``); the env record's image
    digest is a placeholder; the tree is committed by the build so the
    claim's clean-tree check passes; ``cwd`` and the ``WFC_*`` environment
    are pinned to ``root``; the standing invariant pack is asserted after
    the drive.
    Does not prove: runtime image resolution or the docker argv executing
    (the engine rung under ``-m integration`` does); anything about the
    engine's scheduling -- one target ran, on the harness's order.

    Args:
        root: Project root. A ``tmp_project`` or any git repo; built into a
            project when it is not one yet.
        monkeypatch: The test's ``MonkeyPatch``.
        method: Method name (also the node id). Choose one that does not
            collide with a method the caller registered by another route.
        module: Owning module name. The harness default when ``None``.
        sample: The sample the run is driven on. The harness default when
            ``None``.
        samples: Every sample the project registers. Defaults to
            ``[sample]``.
        outputs: Declared output slot -> type string. The harness default
            when ``None``.
        params: Node-level params.
        label: Canvas label; ``Run.nid`` is stamped from it.
        pipeline_id: Pipeline execution id. Two scenarios over one root
            need distinct ids or the second overwrites the first's run dir.
        name: Document name (the pipeline JSON filename); same rule.
        scenario: A full declaration to build instead of the keyword one.
            When given, ``method`` .. ``label`` and ``node_kwargs`` are
            ignored and ``target`` names the node to drive.
        target: The node id (or ``(node, sample, variant)``) to drive
            when ``scenario`` is given. Defaults to ``method`` and
            ``sample`` otherwise.
        **node_kwargs: Any other :class:`NodeSpec` field for the keyword
            declaration (``behavior``, ``output_files``, ``input_columns``,
            ...).

    Returns:
        The run's identity.
    """
    口 = Step(step_num=1, name="Resolve the declaration",
             purpose="Either the caller's own scenario or the one-node "
                     "keyword shape; the target is the node the run is of",
             outputs="A scenario and the target triple to drive")
    scenario, target = _resolve_declaration(
        scenario=scenario, target=target, method=method, module=module,
        sample=sample, samples=samples, outputs=outputs, params=params,
        label=label, pipeline_id=pipeline_id, name=name,
        node_kwargs=node_kwargs,
    )

    口 = AutoStep(step_num=2, name="Build and drive to completion")
    return _drive_declared_run(root, monkeypatch=monkeypatch,
                               scenario=scenario, target=target, through=None)


@workflow(purpose="A run stopped inside its lifecycle -- by default right after "
                  "the claim phase, so the row exists with the claim's cache key "
                  "and nothing has been materialized, dispatched or collected",
          inputs="A project root, the run's declaration, and the phase to stop "
                 "after",
          outputs="The run's identity as the stopped-at phase left it")
def claimed_run(
    root: Path,
    *,
    monkeypatch,
    through: "Phase | None" = None,
    method: str = ROUTE_METHOD,
    module: str | None = None,
    sample: str | None = None,
    samples: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    params: dict | None = None,
    label: str | None = None,
    pipeline_id: str = ROUTE_PIPELINE_ID,
    name: str = ROUTE_PIPELINE_NAME,
    scenario: "Scenario | None" = None,
    target=None,
    **node_kwargs,
) -> DrivenRun:
    """A run the claim phase registered and nothing after it touched.

    The same mechanism as :func:`completed_run`, stopped after a phase.
    ``through`` defaults to the claim phase: the row exists in the status
    the claim registers, keyed by the claim's own recipe, with no
    ``RunOutput`` rows and an empty archive directory. A run stopped after
    a later phase -- ``through=Phase.COLLECT`` is the collected-but-not-
    completed state -- is declared the same way.

    Every phase at or before the stopping point runs for real; the stop is
    the harness refusing to delegate the phase after it. Production is
    never asked to stop early and no phase is faked.

    Pins: everything :func:`completed_run` pins, plus the stop itself --
    the phase after ``through`` is intercepted by the harness rather than
    reached by production.
    Does not prove: anything about the phases after the stop, including
    that they would have succeeded.

    Args:
        root: Project root.
        monkeypatch: The test's ``MonkeyPatch``.
        through: The last phase that runs. ``None`` means the claim phase.
        method: Method name (also the node id).
        module: Owning module name.
        sample: The sample the run is driven on.
        samples: Every sample the project registers.
        outputs: Declared output slot -> type string.
        params: Node-level params.
        label: Canvas label.
        pipeline_id: Pipeline execution id.
        name: Document name.
        scenario: A full declaration to build instead of the keyword one.
        target: The node to drive when ``scenario`` is given.
        **node_kwargs: Any other :class:`NodeSpec` field.

    Returns:
        The run's identity.
    """
    from tests.harness import Phase

    口 = Step(step_num=1, name="Resolve the declaration and the stopping phase",
             purpose="Either the caller's own scenario or the one-node "
                     "keyword shape; the claim phase when no phase is named",
             outputs="A scenario, the target triple, and the phase to stop after")
    scenario, target = _resolve_declaration(
        scenario=scenario, target=target, method=method, module=module,
        sample=sample, samples=samples, outputs=outputs, params=params,
        label=label, pipeline_id=pipeline_id, name=name,
        node_kwargs=node_kwargs,
    )
    stop = Phase.CLAIM if through is None else through

    口 = AutoStep(step_num=2, name="Build and drive through the stopping phase")
    return _drive_declared_run(root, monkeypatch=monkeypatch,
                               scenario=scenario, target=target, through=stop)


def _resolve_declaration(*, scenario, target, method, module, sample, samples,
                         outputs, params, label, pipeline_id, name,
                         node_kwargs) -> tuple:
    """Return ``(scenario, target)`` for the run builders' two calling shapes.

    Pins: nothing of its own -- the keyword shape becomes the one-node
    scenario ``_route_scenario`` pins; a caller's scenario passes through
    untouched.
    Does not prove: anything; it drives nothing and reads no state.

    Args:
        scenario: The caller's scenario, or ``None`` for the keyword shape.
        target: The caller's target, or ``None`` for the keyword shape.
        method: ... ``node_kwargs``: The keyword declaration.

    Returns:
        The scenario to build and the target to drive.

    Raises:
        ValueError: When a scenario is given without a target.
    """
    if scenario is not None:
        if target is None:
            raise ValueError("a scenario needs a target: name the node (or the "
                             "(node, sample, variant) triple) to drive")
        return scenario, target
    from tests.harness.scenario import DEFAULT_SAMPLE, DEFAULT_VARIANT

    scn = _route_scenario(
        method=method, module=module, sample=sample, samples=samples,
        outputs=outputs, params=params, label=label,
        pipeline_id=pipeline_id, name=name, node_kwargs=node_kwargs,
    )
    return scn, (method, sample or DEFAULT_SAMPLE, DEFAULT_VARIANT)
