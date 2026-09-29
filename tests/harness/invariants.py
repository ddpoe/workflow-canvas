"""The standing invariant pack: applicability, named skips, strict waivers.

The three named invariants are asserted after every harness run, whatever
the scenario was testing:

1. ``single-run-record`` — exactly one authoritative run record per
   ``(node, sample, variant)`` target, whatever happened. The target is
   read off the row as ``(node_id, sample, params)``: ``node_id`` is the
   document node's raw id, which every run row records, and a variant is
   its params set (the ``Run`` table has no variant column). Two unlabelled
   nodes sharing a method under identical params are two targets, told
   apart by their node ids.
2. ``completion-signals-agree`` — sentinel, completed-or-cached run row and
   outcome sidecar agree.
3. ``output-writers-agree`` — the two output-row writers produce
   byte-identical rows.

Applicability is derived from what a run actually **executed**, not from
what ``through`` requested: a cache hit jumps from claim straight to
record, so a cache-hit scenario stopped through dispatch never entered
dispatch at all and the pack says so. A skipped invariant is never silent.

Waivers are strict. A test may waive exactly one invariant by name with a
required reason string, and the rest of the pack still asserts. A waived
invariant that *passes* fails the test: the waiver asserts that this
scenario is expected to violate this invariant, so an unexpected pass is a
finding — either the divergence was resolved or the waiver was never needed
for this scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from axiom_annotations import AutoStep, Step, task

#: Every invariant the pack knows, in report order.
INVARIANT_NAMES = (
    "single-run-record",
    "completion-signals-agree",
    "output-writers-agree",
)

#: Run statuses that count as a completion signal on the row side.
_COMPLETED_STATUSES = ("completed",)

#: Run statuses a finished pipeline leaves as its final word on a target.
#: A ``running`` row after the pipeline has ended is not a record at all.
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


@dataclass
class InvariantReport:
    """The pack's verdict for one run.

    Attributes:
        checked: Invariants that were applicable and evaluated.
        passed: Applicable invariants that held.
        failures: Invariant name -> the divergence found.
        skipped: Invariant name -> why it did not apply.
        waived: The single waived invariant's name, or ``None``.
        waiver_reason: The waiver's required justification.
    """

    checked: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    waived: str | None = None
    waiver_reason: str | None = None

    def assert_clean(self) -> None:
        """Fail the test on any unwaived divergence or an unexpected pass.

        Raises:
            AssertionError: When an unwaived invariant diverged, or when
                the waived invariant held after all.
        """
        problems: list[str] = []
        for name, message in self.failures.items():
            if name == self.waived:
                continue
            problems.append(f"invariant {name!r} diverged: {message}")
        if self.waived is not None and self.waived in self.passed:
            problems.append(
                f"waived invariant {self.waived!r} PASSED. A waiver asserts "
                f"that this scenario is expected to violate it, so an "
                f"unexpected pass is a finding: either the divergence was "
                f"resolved (drop the waiver) or this scenario never "
                f"triggered it (adjust the scenario or drop the waiver). "
                f"Waiver reason was: {self.waiver_reason!r}"
            )
        if problems:
            raise AssertionError(
                "\n".join(problems)
                + f"\nskipped (not applicable): {self.skipped or 'none'}"
            )

    def __str__(self) -> str:
        return (f"InvariantReport(passed={self.passed}, "
                f"failures={list(self.failures)}, skipped={list(self.skipped)}, "
                f"waived={self.waived!r})")


@task(purpose="Evaluate the three standing invariants against one observation "
              "bundle — the pack asserted after every harness run, whatever "
              "the scenario was testing",
      inputs="The observation bundle, and at most one waived invariant name "
             "with its required reason",
      outputs="The pack's report: checked, passed, skipped and failed "
              "invariants",
      critical="Each invariant derives its own applicable population from "
               "what the run actually EXECUTED, never from what the caller "
               "requested — and a population that comes out empty is a named "
               "skip, never a silent pass")
def check_invariants(obs, *, waive: str | None = None,
                     reason: str | None = None,
                     after_pipeline: bool = False) -> InvariantReport:
    """Evaluate the standing pack against one observation bundle.

    Args:
        obs: The observation bundle.
        waive: Name of the one invariant this scenario is expected to
            violate.
        reason: Required justification for ``waive``.
        after_pipeline: Evaluate at the post-pipeline door -- after a
            pipeline-level entry point (``run_pipeline`` and its
            cancelled-rows walk) has run on top of the scenario.
            Invariant 1 then takes its artifact-derived expression: a
            fresh query of the run table rather than the rows the
            bundle carried, terminal rows only. Invariants 2 and 3 are
            unchanged; their observed-phase populations are the ones
            the stub rung recorded before the pipeline call.

    Returns:
        The pack's report. Call :meth:`InvariantReport.assert_clean` to
        turn it into a test outcome.

    Raises:
        ValueError: When ``waive`` names an unknown invariant, or is given
            without a reason.
    """
    口 = Step(step_num=1, name="Validate the waiver, then open the report",
             purpose="A waiver names one invariant and carries a required "
                     "reason; the rest of the pack still asserts",
             outputs="An empty InvariantReport carrying the waiver",
             critical="A waiver is an inventory entry for a live divergence, "
                      "not a mute button — an unknown name or a missing "
                      "reason raises rather than silently widening the "
                      "waiver")
    if waive is not None:
        if waive not in INVARIANT_NAMES:
            raise ValueError(
                f"unknown invariant {waive!r}; the pack holds {list(INVARIANT_NAMES)}"
            )
        if not reason:
            raise ValueError(
                f"waiving {waive!r} requires a reason string — a waiver is an "
                f"inventory entry for a live divergence, not a mute button"
            )
    report = InvariantReport(waived=waive, waiver_reason=reason)

    口 = AutoStep(step_num=2, name="Invariant 1 — single-run-record")
    _check_single_run_record(obs, report, after_pipeline=after_pipeline)

    口 = AutoStep(step_num=3, name="Invariant 2 — completion-signals-agree")
    _check_completion_signals(obs, report)

    口 = AutoStep(step_num=4, name="Invariant 3 — output-writers-agree")
    _check_output_writers(obs, report)
    return report


def _record(report: InvariantReport, name: str, failure: str | None) -> None:
    """Land one invariant's outcome in the report."""
    report.checked.append(name)
    if failure is None:
        report.passed.append(name)
    else:
        report.failures[name] = failure


def _skip(report: InvariantReport, name: str, why: str) -> None:
    """Name one invariant as inapplicable rather than passing it silently."""
    report.skipped[name] = why


def _targets_that_ran(obs, phase: str) -> list:
    """Return the targets whose run entered a named phase."""
    return [t for t, r in obs.runs.items() if phase in r.phases_ran]


# =============================================================================
# Invariant 1 — one authoritative run record per target
# =============================================================================

@task(purpose="Assert exactly one authoritative run record per "
              "(node, sample, variant) target, whatever happened — the "
              "target read off the row as (node_id, sample, params), "
              "node_id being the document node's raw id every run row "
              "records, so two unlabelled nodes sharing a method under "
              "identical params are two targets",
      inputs="The observation bundle and the report to land the outcome in",
      outputs="The invariant recorded as passed, failed or skipped",
      critical="Applicable population is targets that REGISTERED a run "
               "identity, not targets that entered the claim phase: claim has "
               "exits that precede registration (an unresolvable node, a dirty "
               "working tree), and a target that never got a run identity "
               "never entered the lifecycle this invariant is about. Read off "
               "the run identity alone, so the engine rung — which observes "
               "no phase — still has a population. Rows the pipeline-end "
               "walk writes after the fact (cancelled rows, and the failed "
               "row of a claim-refused target) count for uniqueness but not "
               "against the claimed count")
def _check_single_run_record(obs, report: InvariantReport, *,
                             after_pipeline: bool = False) -> None:
    name = "single-run-record"
    # Applicability is registration, not phase entry: the claim phase has
    # exits that precede run registration (an unresolvable node, a dirty
    # working tree), and a target that never got a run identity never
    # entered the run-record lifecycle this invariant is about. Registration
    # is read off the run identity alone, not off an observed claim phase —
    # the engine rung learns the identity from production's own sentinel
    # sidecar and observes no phase at all.
    claimed = [t for t, r in obs.runs.items() if r.run_id is not None]
    if not claimed:
        _skip(report, name,
              "no target registered a run — every claim either did not run or "
              "exited before registration, so no run record was created")
        return

    if after_pipeline:
        # The artifact-derived expression: the bundle's rows were read at
        # _finish, before the pipeline-level call; ask the database now.
        _single_run_record_from_rows(obs, report, name, claimed)
        return

    fresh = [r for r in obs.run_rows if (r["id"] or 0) > obs.baseline_run_id]
    counts: dict[tuple, int] = {}
    for row in fresh:
        key = _target_key(row)
        counts[key] = counts.get(key, 0) + 1
    duplicated = {k: v for k, v in counts.items() if v != 1}
    if duplicated:
        _record(report, name,
                f"targets with a run-row count other than 1: {duplicated}")
        return
    # The pipeline-end walk writes rows after the fact for targets that
    # never registered a run -- cancelled rows, and the failed row of a
    # target the claim refused -- so they are counted for uniqueness but not
    # against the set of targets that registered. A registered run always
    # carries a method version; a walk-written row never does.
    executed = [r for r in fresh if r["version_id"] is not None]
    if len(executed) != len(claimed):
        _record(report, name,
                f"{len(claimed)} target(s) claimed but {len(executed)} run row(s) "
                f"were registered")
        return
    _record(report, name, None)


def _target_key(row: dict) -> tuple:
    """Read the ``(node, sample, variant)`` target off one run row.

    Args:
        row: A ``Run`` row as ``_read_rows("Run")`` returns it.

    Returns:
        ``(node_id, sample, params)`` — the document node's raw id, the
        sample, and the params set rendered as a stable key.
    """
    return (row["node_id"], row["sample"], _params_key(row["params"]))


def _params_key(params) -> str:
    """Render a run row's params into a stable grouping key."""
    import json

    try:
        return json.dumps(params, sort_keys=True, default=str)
    except TypeError:
        return str(params)


def _single_run_record_from_rows(obs, report: InvariantReport, name: str,
                                 claimed: list) -> None:
    """Invariant 1 in its artifact-derived form: a fresh query of the run table.

    Evaluated at the post-pipeline door, where the rows the bundle read at
    ``_finish`` predate the pipeline-level call and its cancelled-rows
    walk. The population is still the targets that registered a run; the
    rows are read again now. Only terminal rows count. Cancelled rows
    count toward uniqueness -- a cancelled row beside an executed one for
    the same target is the double write this invariant exists to catch --
    and not toward the claimed count, since the walk writes them for
    targets that never registered.

    Args:
        obs: The observation bundle (its baseline and registered targets).
        report: The report to land the outcome in.
        name: The invariant's name.
        claimed: Targets whose claim registered a run.
    """
    from .observe import _read_rows

    rows = [r for r in _read_rows("Run")
            if (r["id"] or 0) > obs.baseline_run_id
            and r["status"] in _TERMINAL_STATUSES]
    counts: dict[tuple, int] = {}
    for row in rows:
        key = _target_key(row)
        counts[key] = counts.get(key, 0) + 1
    duplicated = {k: v for k, v in counts.items() if v != 1}
    if duplicated:
        _record(report, name,
                f"targets with a terminal run-row count other than 1 after "
                f"the pipeline: {duplicated}")
        return
    by_id = {row["id"]: row for row in rows}
    unrecorded = [t for t in claimed if obs.runs[t].run_id not in by_id]
    if unrecorded:
        _record(report, name,
                f"registered target(s) with no terminal run row after the "
                f"pipeline: {unrecorded}")
        return
    executed = [r for r in rows if r["version_id"] is not None]
    if len(executed) != len(claimed):
        _record(report, name,
                f"{len(claimed)} target(s) claimed but {len(executed)} terminal "
                f"run row(s) were registered after the pipeline")
        return
    _record(report, name, None)


# =============================================================================
# Invariant 2 — completion signals agree
# =============================================================================

@task(purpose="Assert the sentinel, the completed-or-cached run row and the "
              "outcome sidecar agree with each other and with the run's rc",
      inputs="The observation bundle and the report to land the outcome in",
      outputs="The invariant recorded as passed, failed or skipped",
      critical="Applicable population is targets that entered the RECORD "
               "phase. A cache hit jumps claim straight to record, so a "
               "cache-hit scenario stopped through dispatch never entered "
               "dispatch at all. The population is empty on the engine rung, "
               "where no phase is observed, and the invariant reports itself "
               "skipped rather than asserting against a fiction")
def _check_completion_signals(obs, report: InvariantReport) -> None:
    name = "completion-signals-agree"
    recorded = _targets_that_ran(obs, "record")
    if not recorded:
        _skip(report, name,
              "no target was OBSERVED entering the record phase — either "
              "none ran that far, or the run was driven in a way the harness "
              "does not watch phases through (the engine rung, which "
              "interposes nothing) — so this run proves nothing about the "
              "completion lifecycle")
        return

    problems: list[str] = []
    for target in recorded:
        run = obs.runs[target]
        sentinel = target in obs.sentinels
        outcome = obs.outcomes.get(target)
        outcome_ok = outcome is not None and outcome.get("status") in (
            "completed", "cached")
        row = _row_for(obs, target)
        row_ok = row is not None and (
            row["status"] in _COMPLETED_STATUSES
            or row["cache_source_run_id"] is not None
        )
        if run.rc == 0:
            if not (sentinel and outcome_ok and row_ok):
                problems.append(
                    f"{target}: rc=0 but sentinel={sentinel}, "
                    f"outcome={outcome.get('status') if outcome else None}, "
                    f"row_status={row['status'] if row else None}"
                )
        else:
            if sentinel or outcome_ok:
                problems.append(
                    f"{target}: rc={run.rc} but sentinel={sentinel}, "
                    f"outcome={outcome.get('status') if outcome else None}"
                )
    if problems:
        _record(report, name, "; ".join(problems))
    else:
        _record(report, name, None)


def _row_for(obs, target):
    """Return the run row this execution registered for one target."""
    run_id = obs.runs[target].run_id
    if run_id is None:
        return None
    for row in obs.run_rows:
        if row["id"] == run_id:
            return row
    return None


# =============================================================================
# Invariant 3 — the two output-row writers agree
# =============================================================================

@task(purpose="Assert the two output-row writers produce byte-identical rows "
              "— the completion write must not change what collect recorded",
      inputs="The observation bundle and the report to land the outcome in",
      outputs="The invariant recorded as passed, failed or skipped",
      critical="Applicable population is targets carrying BOTH output-row "
               "snapshots, which only the stub rung's _snapshot_output_writers "
               "interposition captures. A target that never reached the record "
               "phase with collected rows never exercised both writers, so the "
               "invariant reports itself skipped")
def _check_output_writers(obs, report: InvariantReport) -> None:
    name = "output-writers-agree"
    comparable = [
        t for t, r in obs.runs.items()
        if r.output_rows_before is not None and r.output_rows_after is not None
    ]
    if not comparable:
        _skip(report, name,
              "no target was OBSERVED reaching the record phase with "
              "collected output rows — either none did, or the phases were "
              "not watched (the engine rung, which interposes nothing) — so "
              "the two output-row writers were never observed both exercised")
        return

    problems: list[str] = []
    for target in comparable:
        run = obs.runs[target]
        if run.output_rows_before != run.output_rows_after:
            problems.append(
                f"{target}: the completion write changed RunOutput rows.\n"
                f"  before: {run.output_rows_before}\n"
                f"  after:  {run.output_rows_after}"
            )
    if problems:
        _record(report, name, "\n".join(problems))
    else:
        _record(report, name, None)


__all__ = ["INVARIANT_NAMES", "InvariantReport", "check_invariants"]
