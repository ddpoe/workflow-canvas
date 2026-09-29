"""The observation bundle: one object, one reader, one shape.

Every entry point and both fidelity rungs return this, so a case moves
between rungs without being rewritten and assertions survive a phase's
implementation moving to another unit.

Database state is read through ``wfc.persistence`` against the schema those
models build (``wfc.persistence`` runs ``SQLModel.metadata.create_all`` plus
the model-driven backfill). No table DDL is written here — a second schema
definition drifts silently, and the drift surfaces as a production-looking
``no such column`` error.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiom_annotations import AutoStep, Step, task

from .project import Project, _layout

#: A target's identity: (node_id, sample, variant).
Target = tuple[str, str, str]


@dataclass
class TargetRun:
    """What one target's execution produced.

    Attributes:
        target: The ``(node_id, sample, variant)`` triple.
        rc: The orchestrator's exit code, or ``None`` when the run was
            stopped early by a ``through=`` boundary.
        phases_ran: Phase names that actually executed, in order. Derived
            from what ran — a cache hit never enters materialize, dispatch
            or collect, however far ``through`` reached.
        phase_args: Interposed phase name -> the keyword arguments it was
            handed. The stopped phase's arguments are its predecessor's
            outputs.
        stopped_at: The phase whose entry raised the stop, or ``None``.
        skipped: True when the engine analogue never scheduled this target
            because an upstream target of the same combo failed. A skipped
            target ran no phase and is the input the pipeline-end
            cancelled-rows walk reconciles.
        dispatch_cmd: The argv handed to the method process.
        dispatch_env: The environment handed to the method process.
        stdout_log: Path to the per-run stdout log, when one was written.
        stderr_log: Path to the per-run stderr log, when one was written.
        run_id: The run identity the claim phase registered.
        ending: The ending the record phase was handed.
        output_rows_before: ``RunOutput`` rows as the collect phase left
            them, snapshotted immediately before the record phase.
        output_rows_after: The same rows immediately after it. Equality is
            the executable form of the two-writers invariant.
        completion_writes: How many times the second writer was invoked
            with ``status="completed"``. The invariant is about agreement
            between two writers, so how often the second one fires is a
            separate, assertable fact.
    """

    target: Target
    rc: int | None = None
    phases_ran: list[str] = field(default_factory=list)
    phase_args: dict[str, dict] = field(default_factory=dict)
    stopped_at: str | None = None
    skipped: bool = False
    dispatch_cmd: list[str] | None = None
    dispatch_env: dict[str, str] | None = None
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    run_id: int | None = None
    ending: str | None = None
    output_rows_before: list[dict] | None = None
    output_rows_after: list[dict] | None = None
    completion_writes: int = 0


@dataclass
class Observation:
    """One standard bundle of everything a scenario run leaves behind.

    Attributes:
        project: The built project handle.
        targets: The target list that ran, in execution order.
        runs: Per-target execution records.
        run_rows: Every ``Run`` row, as column dicts.
        output_rows: Every ``RunOutput`` row, as column dicts.
        run_input_rows: Every ``RunInput`` row, as column dicts.
        sentinels: Targets whose ``.complete`` sentinel exists.
        run_id_sidecars: Target -> the run id recorded beside its sentinel.
        outcomes: Target -> the parsed outcome sidecar.
        baseline_run_id: Highest run id that existed before this
            execution's targets started, so the pack can scope itself to
            the rows this execution created rather than to seeded state.
        invariants: The standing pack's report, attached after every run.
        database_url: The URL of the process engine the database rows were
            read through, as SQLAlchemy renders it. ``None`` until
            :func:`read_observation` fills it. A scenario compares it with
            the URL its project was bound with, so rows read from another
            database cannot pass as this scenario's.
    """

    project: Project
    targets: list[Target] = field(default_factory=list)
    runs: dict[Target, TargetRun] = field(default_factory=dict)
    run_rows: list[dict] = field(default_factory=list)
    output_rows: list[dict] = field(default_factory=list)
    run_input_rows: list[dict] = field(default_factory=list)
    sentinels: set[Target] = field(default_factory=set)
    run_id_sidecars: dict[Target, int] = field(default_factory=dict)
    outcomes: dict[Target, dict] = field(default_factory=dict)
    baseline_run_id: int = 0
    invariants: Any = None
    database_url: str | None = None

    # -- convenience accessors -------------------------------------------

    def exit_code(self, target: Target) -> int | None:
        """Return one target's orchestrator exit code.

        Args:
            target: The target triple.

        Returns:
            The exit code, or ``None`` when the run stopped early.
        """
        return self.runs[target].rc

    def exit_codes(self) -> dict[Target, int | None]:
        """Return every target's exit code keyed by target."""
        return {t: r.rc for t, r in self.runs.items()}

    def phases_ran(self, target: Target | None = None) -> list[str]:
        """Return the phases that executed for one target.

        Args:
            target: The target triple. Defaults to the only target when
                the scenario ran exactly one.

        Returns:
            Phase names in execution order.
        """
        return self.runs[self._one(target)].phases_ran

    def phase_args(self, phase: str, target: Target | None = None) -> dict:
        """Return the keyword arguments an interposed phase was handed.

        Args:
            phase: Phase name.
            target: The target triple, defaulting to the only target.

        Returns:
            The recorded keyword arguments.
        """
        return self.runs[self._one(target)].phase_args[phase]

    def dispatch_env(self, target: Target | None = None) -> dict[str, str]:
        """Return the environment handed to one target's method process."""
        env = self.runs[self._one(target)].dispatch_env
        assert env is not None, "no method dispatch happened for this target"
        return env

    def dispatch_cmd(self, target: Target | None = None) -> list[str]:
        """Return the argv handed to one target's method process."""
        cmd = self.runs[self._one(target)].dispatch_cmd
        assert cmd is not None, "no method dispatch happened for this target"
        return cmd

    def input_paths(self, target: Target | None = None) -> dict:
        """Return the resolved per-slot input paths for one target.

        Args:
            target: The target triple, defaulting to the only target.

        Returns:
            The decoded ``WFC_INPUT_PATHS`` map.
        """
        raw = self.dispatch_env(target).get("WFC_INPUT_PATHS")
        return json.loads(raw) if raw else {}

    def run_row(self, target: Target | None = None) -> dict | None:
        """Return the ``Run`` row this execution registered for one target.

        Args:
            target: The target triple, defaulting to the only target.

        Returns:
            The row as a column dict, or ``None`` when the target never
            reached run registration.
        """
        run_id = self.runs[self._one(target)].run_id
        if run_id is None:
            return None
        return next((r for r in self.run_rows if r["id"] == run_id), None)

    def output_rows_for(self, target: Target | None = None) -> list[dict]:
        """Return the ``RunOutput`` rows written for one target.

        Args:
            target: The target triple, defaulting to the only target.

        Returns:
            Matching output rows ordered by output name.
        """
        return self.output_rows_for_run(self.runs[self._one(target)].run_id)

    def output_rows_for_run(self, run_id: int | None) -> list[dict]:
        """Return the ``RunOutput`` rows written under one run id.

        Reaches rows that belong to a run no target of this execution
        owns — notably a cache hit's *source* run, whose id the audit row
        carries in ``cache_source_run_id``.

        Args:
            run_id: The run whose output rows to return.

        Returns:
            Matching output rows ordered by output name.
        """
        return sorted(
            (r for r in self.output_rows if r["run_id"] == run_id),
            key=lambda r: r["output_name"],
        )

    def rows_for_node(self, node_id: str, *, sample: str | None = None,
                      status: str | None = None) -> list[dict]:
        """Return the ``Run`` rows recorded for one node.

        Rows are matched on the node id each run records, so two nodes of
        one method get their own rows. Reaches rows the driver never held a
        handle on — notably the ``cancelled`` rows the pipeline-end walk
        writes for targets that never started.

        Args:
            node_id: The node's document id.
            sample: Restrict to one sample identifier.
            status: Restrict to one run status.

        Returns:
            Matching rows, ordered by run id.
        """
        return sorted(
            (r for r in self.run_rows
             if r["node_id"] == node_id
             and (sample is None or r["sample"] == sample)
             and (status is None or r["status"] == status)),
            key=lambda r: r["id"] or 0,
        )

    def to_mermaid(self) -> str:
        """Render what this run actually did as a mermaid flowchart.

        One box per target, grouped by sample, wired by the scenario's
        declared topology and coloured by what happened to it. Declared
        nodes that expand to no target stay visible: selectors and
        references are drawn as system nodes, and a method node this run
        never took a target from is drawn absent — distinct from a target
        the run decided against, which is drawn skipped.

        The topology comes from the declaration; every verdict comes from
        the bundle. A diagram of a run that failed therefore shows the
        prune, not the pipeline someone hoped for.

        Returns:
            Mermaid ``flowchart`` source.
        """
        specs = {n.id: n for n in self.project.scenario.nodes}
        by_node: dict[str, list[Target]] = {}
        for target in self.targets:
            by_node.setdefault(target[0], []).append(target)

        lines = ["flowchart LR"]
        for node_id, spec in specs.items():
            if node_id in by_node:
                continue
            if spec.type == "method":
                # A method node outside this run's target list -- run_target
                # ran one target, so the rest of the pipeline is declared but
                # untouched. Distinct from "not scheduled", which is a target
                # the run decided against.
                lines.append(
                    f'  {_mid(node_id)}["{node_id}<br/>not in this run"]:::absent'
                )
            else:
                lines.append(
                    f'  {_mid(node_id)}(["{node_id}<br/>{spec.type}"]):::system'
                )

        samples: list[str] = []
        for target in self.targets:
            if target[1] not in samples:
                samples.append(target[1])
        for sample in samples:
            lines.append(f'  subgraph sg_{_mid(sample)}["sample {sample}"]')
            for target in self.targets:
                if target[1] != sample:
                    continue
                style, note = self._target_state(target)
                lines.append(
                    f'    {_mid(target)}["{target[0]}<br/>{note}"]:::{style}'
                )
            lines.append("  end")

        for target in self.targets:
            spec = specs.get(target[0])
            for wire_in in (spec.inputs if spec else []):
                for source in self._mermaid_sources(wire_in.source, target,
                                                    by_node):
                    lines.append(f"  {source} --> {_mid(target)}")

        lines += [
            "  classDef ok fill:#dff3e0,stroke:#3f9142,color:#14351f",
            "  classDef failed fill:#fbe0e0,stroke:#c0392b,color:#4a1512",
            "  classDef skipped fill:#eeeeee,stroke:#999,color:#444,"
            "stroke-dasharray:4 3",
            "  classDef cached fill:#e3ecfb,stroke:#3b6fb6,color:#152944",
            "  classDef stopped fill:#fdf0d5,stroke:#c8890a,color:#4a3305",
            "  classDef system fill:#fff,stroke:#777,color:#333",
            "  classDef absent fill:#fafafa,stroke:#bbb,color:#888,"
            "stroke-dasharray:2 4",
        ]
        return "\n".join(lines)

    def _target_state(self, target: Target) -> tuple[str, str]:
        """Return one target's mermaid class and its label's second line.

        The label is written for someone reading the test, not for someone
        who already knows the internals: no exit-code jargon, no phase
        arithmetic. A stop names the phase it stopped before, because that
        is the one case where which phases ran is the point.
        """
        record = self.runs[target]
        if record.skipped:
            return "skipped", "not scheduled"
        if record.rc is None:
            return "stopped", (f"stopped before {record.stopped_at}"
                               if record.stopped_at else "no verdict")
        if record.rc != 0:
            return "failed", f"FAILED (exit {record.rc})"
        if (self.outcomes.get(target) or {}).get("status") == "cached":
            return "cached", "cache hit"
        return "ok", "completed"

    def _mermaid_sources(self, source: str, target: Target,
                         by_node: dict[str, list[Target]]) -> list[str]:
        """Return the mermaid ids feeding one target from a declared source.

        A source that expands to no target is a system node and feeds every
        consumer directly. Otherwise the same sample and variant wins; a
        collapsed source (the ``__all__`` sentinel) is next; and a target
        that consumes a whole axis takes every source target on its variant.
        """
        _, sample, variant = target
        candidates = by_node.get(source)
        if not candidates:
            return [_mid(source)]
        exact = [t for t in candidates if t[1] == sample and t[2] == variant]
        if exact:
            return [_mid(t) for t in exact]
        collapsed = [t for t in candidates
                     if t[1] == "__all__" and t[2] == variant]
        if collapsed:
            return [_mid(t) for t in collapsed]
        return [_mid(t) for t in candidates if t[2] == variant]

    def _one(self, target: Target | None) -> Target:
        if target is not None:
            return target
        if len(self.runs) != 1:
            raise ValueError(
                f"scenario ran {len(self.runs)} targets — name the target "
                f"explicitly (have {list(self.runs)})"
            )
        return next(iter(self.runs))


def _mid(value: Target | str) -> str:
    """Return a mermaid-safe node id for a target triple or a node id."""
    text = "_".join(value) if isinstance(value, tuple) else value
    return "".join(c if c.isalnum() else "_" for c in text)


def max_run_id() -> int:
    """Return the highest ``Run`` id currently in the project database.

    Returns:
        The high-water mark, or ``0`` when no run exists yet.
    """
    from sqlmodel import select

    from wfc.persistence import get_session, Run

    with get_session() as session:
        ids = [r.id or 0 for r in session.exec(select(Run)).all()]
    return max(ids) if ids else 0


def read_output_rows(run_id: int) -> list[dict]:
    """Read one run's ``RunOutput`` rows, every column, ordered by name.

    Args:
        run_id: The run whose output rows to read.

    Returns:
        One dict per row.
    """
    from sqlmodel import select

    from wfc.persistence import get_session, RunOutput

    cols = [c.name for c in RunOutput.__table__.columns]
    with get_session() as session:
        rows = session.exec(
            select(RunOutput)
            .where(RunOutput.run_id == run_id)
            .order_by(RunOutput.output_name)
        ).all()
        return [{c: getattr(r, c) for c in cols} for r in rows]


@task(purpose="Read every trace a completed scenario left behind into one "
              "bundle — the single shape both fidelity rungs return",
      inputs="The built project, the per-target records, and the run-id "
             "baseline",
      outputs="The populated Observation, with invariants not yet attached")
def read_observation(project: Project, runs: dict[Target, TargetRun],
                     baseline_run_id: int = 0) -> Observation:
    """Read the full observation bundle for a completed scenario run.

    Args:
        project: The built project.
        runs: Per-target execution records collected by the driver.
        baseline_run_id: Highest run id that existed before this
            execution's targets started.

    Returns:
        The populated :class:`Observation` (invariants not yet attached).
    """
    口 = Step(step_num=1, name="Open the bundle over the driver's records",
             purpose="The records are what the driver observed; everything "
                     "after this reads what production left on disk and in "
                     "the database",
             outputs="An empty Observation carrying the target list and the "
                     "run-id baseline")
    obs = Observation(project=project, targets=list(runs), runs=dict(runs),
                      baseline_run_id=baseline_run_id)

    口 = Step(step_num=2, name="Read the database rows",
             purpose="Run, RunOutput and RunInput rows are read through "
                     "wfc.persistence against the schema those models build, so "
                     "no second schema definition can drift from production's",
             outputs="obs.run_rows, obs.output_rows and obs.run_input_rows, "
                     "plus obs.database_url, the URL of the process engine "
                     "those reads went through")
    from wfc.persistence import get_engine

    obs.run_rows = _read_rows("Run")
    obs.output_rows = _read_rows("RunOutput")
    obs.run_input_rows = _read_rows("RunInput")
    obs.database_url = get_engine().url.render_as_string(hide_password=False)

    口 = Step(step_num=3, name="Walk the sentinel tree",
             purpose="A .complete file names the target that finished, and "
                     "the run_id.txt beside it names the run that wrote it — "
                     "production's own completion signals, read back from "
                     "disk rather than from an instrumented phase",
             outputs="obs.sentinels and obs.run_id_sidecars")
    sentinel_root = _layout().sentinels_dir(project.root) / project.pipeline_id
    if sentinel_root.is_dir():
        for complete in sentinel_root.rglob(".complete"):
            variant = complete.parent.name
            sample = complete.parent.parent.name
            node_id = complete.parent.parent.parent.name
            obs.sentinels.add((node_id, sample, variant))
            sidecar = complete.parent / "run_id.txt"
            if sidecar.exists():
                obs.run_id_sidecars[(node_id, sample, variant)] = int(
                    sidecar.read_text().strip()
                )

    口 = AutoStep(step_num=4, name="Read the outcome sidecars")
    obs.outcomes = read_outcomes(project)
    return obs


@task(purpose="Read the per-target outcome sidecars production wrote — the "
              "completion signal that survives on disk whether or not the "
              "harness watched any phase",
      inputs="The built project",
      outputs="Target -> the parsed outcome sidecar; a target that was never "
              "scheduled has no entry, which is what makes absence readable")
def read_outcomes(project: Project) -> dict[Target, dict]:
    """Read every outcome sidecar the pipeline's targets left behind.

    Args:
        project: The built project.

    Returns:
        Target -> the parsed sidecar. Absence of a key means that target
        wrote no outcome — it either never ran or died before the record
        phase.
    """
    outcomes: dict[Target, dict] = {}
    outcomes_dir = _layout().pipeline_outcomes_dir(project.root, project.pipeline_id)
    if outcomes_dir.is_dir():
        for path in sorted(outcomes_dir.glob("*.json")):
            data = json.loads(path.read_text())
            key = (str(data.get("node_id")), str(data.get("sample")),
                   str(data.get("variant")))
            outcomes[key] = data
    return outcomes


def _read_rows(model_name: str) -> list[dict]:
    """Read every row of one model as a column dict.

    Args:
        model_name: Attribute name on ``wfc.persistence``.

    Returns:
        One dict per row, carrying every mapped column.
    """
    from sqlmodel import select

    from wfc import persistence as _models
    from wfc.persistence import get_session

    model = getattr(_models, model_name)
    cols = [c.name for c in model.__table__.columns]
    with get_session() as session:
        rows = session.exec(select(model)).all()
        return [{c: getattr(r, c) for c in cols} for r in rows]


__all__ = ["Observation", "Target", "TargetRun", "max_run_id",
           "read_observation", "read_output_rows"]
