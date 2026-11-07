"""Clearing draft runs: replacement, Delete outputs, Remove draft and Register.

A draft keeps only its latest draft run. That run's results are cleared when
the draft runs again in another pipeline (replaced), when its outputs are
deleted, when the draft is removed, and when it is registered. Clearing a
draft's run also clears every later draft's results computed from it (a
step whose parent entry names one of its draft strings), and so on down,
never anything upstream: recorded runs are never touched.

Each clearing writes, on the ``pipeline_runs`` row of every pipeline whose
draft steps it cleared, why (``cleared_reason``) and when (``cleared_at``),
then calls the status helper, since what is left has changed (d-16). A
pipeline that recorded nothing and has no draft step left leaves
``pipeline_runs``.

The cleared reason is one of ``replaced by the draft run of HH:MM:SS``,
``outputs deleted``, ``registered as vN`` and ``removed``. A pipeline that
ran more than one draft names each: ``<module>.<method>: <reason>`` per
draft, joined by ``; ``, in the pipeline's draft order.

Registration owns the draft folder and its pointer, and cannot import
Execution, so the three user actions that clear (Delete outputs, Remove
draft, Register) are entries here that call Registration's own function and
then clear; replacement is cleared from the draft-run store's writer.

Remove draft and Delete outputs are planned before anything is deleted
(:func:`plan_draft_delete`): what goes and an opaque token. Their apply
halves take that token and refuse, having deleted nothing, when a fresh
plan differs from it, as method registration's plan and token do; the CLI
and the canvas confirm against the same plan.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from axiom_annotations import AutoStep, Step, task, workflow
from sqlmodel import select

from .. import layout
from ..persistence import PipelineRun, Run, get_session, write_transaction
from ..persistence import project_root as get_project_root
from .draft import (
    DRAFT_REF_PREFIX,
    _all_draft_runs,
    _record_steps,
    clear_draft_run,
    draft_run_label,
    draft_run_time,
    draft_steps_by_pipeline,
    qualified_draft,
    read_draft_run,
)

#: The cleared reason after Delete outputs.
REASON_OUTPUTS_DELETED = "outputs deleted"
#: The cleared reason after Remove draft.
REASON_REMOVED = "removed"

_Key = tuple[str, str]


def replaced_reason(record: dict) -> str:
    """The cleared reason of a draft run replaced by *record*'s run.

    Args:
        record: The new draft run's ``draft-run.json`` record.

    Returns:
        ``replaced by the draft run of HH:MM:SS`` (the new run's local start).
    """
    return f"replaced by the draft run of {draft_run_time(record)}"


def registered_reason(label: int | str | None) -> str:
    """The cleared reason of a registered draft: ``registered as vN``."""
    return f"registered as v{label}"


def _seed(module: str, method: str, root: Path) -> dict[_Key, dict]:
    record = read_draft_run(module, method, root)
    return {} if record is None else {(module, method): record}


def _dependents(seeds: dict[_Key, dict], root: Path,
                spare_pipeline: str | None = None) -> dict[_Key, dict]:
    """The later drafts whose results were computed from a cleared one, down the chain.

    Args:
        seeds: The drafts being cleared, with their records.
        root: The project root.
        spare_pipeline: A pipeline whose draft runs are never cleared (the
            replacing run's own).

    Returns:
        Each dependent draft with its record (the seeds left out).
    """
    runs = {(m, n): record for m, n, record in _all_draft_runs(root)
            if record.get("pipeline_id") != spare_pipeline}
    cleared = dict(seeds)
    grew = True
    while grew:
        grew = False
        prefixes = [f"{DRAFT_REF_PREFIX}{qualified_draft(*key)}/" for key in cleared]
        for key, record in runs.items():
            if key in cleared:
                continue
            if any(prefix in str(entry)
                   for step in _record_steps(record)
                   for entry in step.get("inputs") or []
                   for prefix in prefixes):
                cleared[key] = record
                grew = True
    return {key: record for key, record in cleared.items() if key not in seeds}


def _merge_reason(existing: str | None, drafts: list[str], now: list[str],
                  reason: str) -> str:
    """The pipeline's cleared reason once *now*'s drafts are cleared for *reason*."""
    if len(set(drafts) | set(now)) <= 1:
        return reason
    parts: dict[str, str] = {}
    for part in (existing or "").split("; "):
        draft, sep, why = part.partition(": ")
        if sep and "." in draft and " " not in draft:
            parts[draft] = why
    for draft in now:
        parts[draft] = reason
    order = [d for d in drafts if d in parts] + [d for d in parts if d not in drafts]
    return "; ".join(f"{draft}: {parts[draft]}" for draft in order)


@task(purpose="After a clearing: write the cleared reason and time on each "
              "pipeline whose draft steps were cleared, call the status helper "
              "for it, and drop the row of one that recorded nothing and has no "
              "draft step left",
      inputs="the cleared drafts with their records, the reason, project root",
      outputs="the pipelines touched",
      critical="the status and end are written only by the status helper")
def settle_cleared_pipelines(cleared: dict[_Key, dict], reason: str,
                             root: Path | str) -> list[str]:
    """Record a clearing on the pipelines it touched.

    Args:
        cleared: ``{(module, method): the cleared draft run's record}``.
        reason: The cleared reason.
        root: The project root.

    Returns:
        The pipeline ids whose rows were touched, sorted.
    """
    from .pipeline_outcome import store_pipeline_outcome

    root = Path(root)
    by_pipeline: dict[str, list[tuple[str, int]]] = {}
    for key, record in cleared.items():
        by_pipeline.setdefault(str(record.get("pipeline_id")), []).append(
            (qualified_draft(*key), len(_record_steps(record))))
    remaining = draft_steps_by_pipeline(root)
    now = datetime.now(UTC)
    touched: list[str] = []
    for pipeline_id, parts in sorted(by_pipeline.items()):
        left = remaining.get(pipeline_id, [])
        with write_transaction() as session:
            row = session.get(PipelineRun, pipeline_id)
            if row is None:
                continue
            names = [name for name, _count in parts]
            row.cleared_reason = _merge_reason(row.cleared_reason,
                                               list(row.drafts or names), names, reason)
            row.cleared_at = now
            row.draft_step_count = max(row.draft_step_count or 0,
                                       sum(c for _n, c in parts) + len(left))
            session.add(row)
        touched.append(pipeline_id)
        store_pipeline_outcome(root, pipeline_id)
        if left:
            continue
        with write_transaction() as session:
            recorded = session.exec(
                select(Run.id).where(Run.pipeline_id == pipeline_id)).first()
            row = session.get(PipelineRun, pipeline_id)
            if recorded is None and row is not None:
                session.delete(row)
    return touched


def _clear_dependents(dependents: dict[_Key, dict], root: Path) -> None:
    for module, method in dependents:
        clear_draft_run(module, method, root)


def settle_replacement(module: str, method: str, replaced: dict, record: dict,
                       root: Path | str) -> list[str]:
    """Clear what a replaced draft run leaves behind, and record it.

    The store's writer has already replaced the draft's own record and
    result folders; here the later drafts' results computed from the old run
    go, and every pipeline touched gets ``replaced by the draft run of
    HH:MM:SS``.

    Args:
        module: The draft's module.
        method: The draft's method.
        replaced: The old draft run's record.
        record: The new draft run's record.
        root: The project root.

    Returns:
        The pipelines touched.
    """
    root = Path(root)
    seeds = {(module, method): replaced}
    dependents = _dependents(seeds, root, spare_pipeline=record.get("pipeline_id"))
    _clear_dependents(dependents, root)
    return settle_cleared_pipelines({**seeds, **dependents}, replaced_reason(record),
                                    root)


class DraftDeletePlanChangedError(ValueError):
    """What a confirmed Remove draft or Delete outputs deletes changed after its plan."""


@dataclass(frozen=True)
class ClearedDraft:
    """A later draft whose results a delete clears.

    Attributes:
        draft: The draft as ``<module>.<method>``.
        run: Its draft run, as every message names one (``the draft run of
            HH:MM:SS in <pipeline>``).
    """

    draft: str
    run: str


@dataclass(frozen=True)
class PlannedPipeline:
    """A pipeline whose cleared line a delete changes.

    Attributes:
        pipeline_id: The pipeline's id.
        name: Its name, else its id.
    """

    pipeline_id: str
    name: str


@dataclass(frozen=True)
class DraftDeletePlan:
    """What Remove draft or Delete outputs will delete, worked out before any write.

    Attributes:
        module: The draft's module.
        method: The draft's method.
        outputs_only: ``True`` for Delete outputs (the draft is kept),
            ``False`` for Remove draft (its draft folder goes).
        folder: The draft folder, project-relative (POSIX).
        source: The method folder the draft points at; never touched.
        draft_run: The draft's own draft run, as messages name one, or
            ``None`` when it has none.
        later_drafts: The later drafts whose results computed from it are
            cleared, down the chain, by name.
        pipelines: The pipelines whose cleared line changes, by id.
        token: One opaque string naming all of the above; the apply half
            refuses when a fresh plan's token differs from the one it is given.
        deletes: What the delete removes, one line each, as the CLI and the
            canvas list them under "Deletes"; a line with nothing to name is
            left out. Empty when nothing is deleted.
        keeps: What the delete leaves, one line each, listed under "Keeps".
        hint: The line pointing at the other delete; empty when nothing is
            deleted.
    """

    module: str
    method: str
    outputs_only: bool
    folder: str
    source: str
    draft_run: str | None
    later_drafts: tuple[ClearedDraft, ...]
    pipelines: tuple[PlannedPipeline, ...]
    token: str
    deletes: tuple[str, ...] = ()
    keeps: tuple[str, ...] = ()
    hint: str = ""

    @property
    def nothing_to_delete(self) -> bool:
        """Whether applying the plan deletes nothing.

        Only Delete outputs on a draft with no draft run; Remove draft always
        deletes the draft folder.
        """
        return self.outputs_only and self.draft_run is None

    @property
    def message(self) -> str:
        """The plan stated for the user: what ``wfc delete-draft`` prints.

        A title line, the Deletes list, the Keeps list and the hint; the
        canvas renders the same fields as lists.
        """
        name = qualified_draft(self.module, self.method)
        if self.nothing_to_delete:
            return f"{name} has no draft run: nothing is deleted. The draft is kept."
        title = (f"Delete the outputs of {name}" if self.outputs_only
                 else f"Remove the draft of {name}")
        lines = [title, "Deletes:", *(f"  - {line}" for line in self.deletes),
                 "Keeps:", *(f"  - {line}" for line in self.keeps)]
        if self.hint:
            lines.append(self.hint)
        return "\n".join(lines)

    @property
    def done_message(self) -> str:
        """What applying the plan did, stated for the user after the delete.

        ``wfc delete-draft`` prints it after a confirmed delete and the
        canvas's two delete routes return it as their message.
        """
        name = qualified_draft(self.module, self.method)
        if self.nothing_to_delete:
            return f"{name} had no draft run: nothing was deleted. The draft is kept."
        if self.outputs_only:
            return f"Deleted the draft run of {name}; the draft is kept."
        return f"Removed the draft of {name}. Your method folder {self.source} is untouched."


#: Delete outputs' hint: the other delete.
HINT_REMOVE_INSTEAD = ("Want wfc to forget this draft's script and contract too? "
                       "Use Remove draft instead.")
#: Remove draft's hint: the other delete.
HINT_OUTPUTS_INSTEAD = "Only want to free the results? Use Delete outputs."


def size_label(size: int) -> str:
    """A byte count in the largest unit it fills: ``512 B``, ``1.5 KB``, ``5.0 MB``.

    Args:
        size: The byte count.

    Returns:
        Bytes below 1024 as a whole number, else KB, MB or GB (base 1024)
        with one decimal.
    """
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def _results_size(module: str, method: str, root: Path) -> int:
    """The bytes of a draft's draft-run record and results: what Delete outputs frees."""
    folder = layout.draft_dir(root, module, method)
    if not folder.is_dir():
        return 0
    kept = {layout.DRAFT_POINTER_FILENAME, layout.DRAFT_SESSION_DIR_NAME,
            layout.DRAFT_RUN_FILENAME + ".lock"}
    total = 0
    for entry in folder.iterdir():
        if entry.name in kept or entry.is_symlink():
            continue
        files = [entry] if entry.is_file() else entry.rglob("*")
        total += sum(f.stat().st_size for f in files if f.is_file())
    return total


def _versions_line(labels: list[str] | None, outputs_only: bool) -> list[str]:
    """The Keeps line naming the method's registered versions; none without any.

    Args:
        labels: The registered version labels, oldest first; ``None`` when
            the version list cannot be read (the line is then the generic one).
        outputs_only: Delete outputs (the versions are named) or Remove draft.
    """
    if labels is None:
        return ["registered versions and their recorded runs"]
    if not labels:
        return []
    if not outputs_only:
        return ["registered versions and their recorded runs"]
    if len(labels) == 1:
        return [f"registered version v{labels[0]} and its recorded runs"]
    return [f"registered versions v{labels[0]}..v{labels[-1]} and their recorded runs"]


def _plan_lists(module: str, method: str, outputs_only: bool, root: Path,
                own: dict | None, later: list[str], pipelines: list[str],
                source: str) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """The plan's Deletes and Keeps lines and its hint, worded for the user.

    Args:
        module: The draft's module.
        method: The draft's method.
        outputs_only: Delete outputs instead of Remove draft.
        root: The project root.
        own: The draft's own draft-run record, if it has one.
        later: The later drafts whose results go, by name.
        pipelines: The pipelines whose draft results go, by name.
        source: The method folder the draft points at.

    Returns:
        ``(deletes, keeps, hint)``; all empty when Delete outputs deletes
        nothing.
    """
    from ..registration import read_version_list

    if outputs_only and own is None:
        return (), (), ""
    labels: list[str] | None
    try:
        labels = [str(e.label) for e in read_version_list(root, module, method) or []]
    except ValueError:
        labels = None  # unreadable: keep the generic versions line
    size = size_label(_results_size(module, method, root)) if own is not None else ""
    deletes: list[str] = []
    if outputs_only:
        assert own is not None
        steps = len(_record_steps(own))
        deletes.append(f"the draft run of {draft_run_time(own)} and its results "
                       f"({size}, {steps} step{'' if steps == 1 else 's'})")
    elif own is not None:
        deletes.append(f"the draft and everything wfc keeps for it: its link to "
                       f"your method folder, its draft-run record of "
                       f"{draft_run_time(own)} and its outputs ({size})")
    else:
        deletes.append("the draft and everything wfc keeps for it: its link to "
                       "your method folder (it has no draft run)")
    if later:
        deletes.append(f"results of later drafts made from it: {', '.join(later)}")
    if pipelines:
        shown = f"draft results shown in pipelines: {', '.join(pipelines)}"
        deletes.append(shown if outputs_only else f"{shown} (they read 'draft removed')")
    if outputs_only:
        keeps = ["the draft itself (its script and method.yaml) — run it again any time",
                 *_versions_line(labels, True), f"your method folder {source}"]
        return tuple(deletes), tuple(keeps), HINT_REMOVE_INSTEAD
    keeps = [f"your method folder and every file the draft used {source} "
             f"(never touched — start a draft from it again any time)",
             *_versions_line(labels, False)]
    return tuple(deletes), tuple(keeps), HINT_OUTPUTS_INSTEAD


def _delete_token(module: str, method: str, outputs_only: bool,
                  cleared: dict[_Key, dict], pipelines: list[str]) -> str:
    """A sha256 of what a delete clears: the draft, the mode, every cleared record."""
    body = {
        "draft": [module, method], "outputs_only": outputs_only,
        "cleared": [[*key, cleared[key]] for key in sorted(cleared)],
        "pipelines": sorted(pipelines),
    }
    joined = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _plan(module: str, method: str, outputs_only: bool,
          root: Path) -> tuple[DraftDeletePlan, dict[_Key, dict], dict[_Key, dict]]:
    """The plan, with the seed and dependent records the apply half clears."""
    from ..registration import DraftNotFoundError, read_draft

    pointer = read_draft(module, method, root)
    if pointer is None:
        raise DraftNotFoundError(module, method)
    seeds = _seed(module, method, root)
    dependents = _dependents(seeds, root)
    cleared = {**seeds, **dependents}
    names: dict[str, str] = {}
    for record in cleared.values():
        pid = str(record.get("pipeline_id"))
        names.setdefault(pid, str(record.get("pipeline_name") or pid))
    pipelines: list[PlannedPipeline] = []
    with get_session() as session:
        for pid in sorted(names):
            row = session.get(PipelineRun, pid)
            if row is not None:
                pipelines.append(PlannedPipeline(pid, row.name or names[pid]))
    own = seeds.get((module, method))
    folder = layout.draft_dir(root, module, method)
    try:
        shown = folder.relative_to(root).as_posix()
    except ValueError:
        shown = folder.as_posix()
    later = [qualified_draft(*key) for key in sorted(dependents)]
    deletes, keeps, hint = _plan_lists(module, method, outputs_only, root, own, later,
                                       [p.name for p in pipelines], str(pointer.folder))
    plan = DraftDeletePlan(
        module=module, method=method, outputs_only=outputs_only, folder=shown,
        source=str(pointer.folder),
        draft_run=draft_run_label(own) if own is not None else None,
        later_drafts=tuple(ClearedDraft(qualified_draft(*key), draft_run_label(record))
                           for key, record in sorted(dependents.items())),
        pipelines=tuple(pipelines),
        token=_delete_token(module, method, outputs_only, cleared,
                            [p.pipeline_id for p in pipelines]),
        deletes=deletes, keeps=keeps, hint=hint,
    )
    return plan, seeds, dependents


@task(purpose="Plan Remove draft or Delete outputs before anything is deleted: "
              "the draft folder (or its draft run and results), every later "
              "draft's results computed from it, the pipelines whose cleared "
              "line changes, and a token naming them; worded for the user as "
              "Deletes and Keeps lines (the results' size among them, the "
              "registered versions kept) and a hint naming the other mode",
      inputs="the draft's module and method, outputs_only, project root",
      outputs="a DraftDeletePlan with its deletes, keeps, hint, message, the "
              "words said once it is applied (done_message) and token",
      critical="reads only: nothing is deleted, cleared or written")
def plan_draft_delete(module: str, method: str, *, outputs_only: bool = False,
                      root: Path | str | None = None) -> DraftDeletePlan:
    """Plan a delete: what it removes, stated before any write, with its token.

    The plan carries what it clears as data (the draft run, the later
    drafts, the pipelines) and the same worded for the user: ``deletes``
    (with the size of the draft-run record and results), ``keeps`` (the
    method folder, the draft itself under Delete outputs, and the
    registered versions with their recorded runs; a generic versions line
    when the version list cannot be read) and ``hint``, the other mode for
    a user who wanted it. ``message`` puts them together; ``done_message``
    is what is said once the plan is applied.

    ``wfc delete-draft`` prints the plan's message and asks; the canvas's
    two delete routes return it from their dry run. Either then applies
    with :func:`remove_draft_and_clear` or
    :func:`delete_draft_outputs_and_clear`, passing the plan's token, and
    shows the applied plan's ``done_message``.

    Args:
        module: The draft's module.
        method: The draft's method.
        outputs_only: Plan Delete outputs (the draft is kept) instead of
            Remove draft.
        root: The project root; the resolved one when omitted.

    Returns:
        The :class:`DraftDeletePlan`.

    Raises:
        DraftNotFoundError: The method has no draft.
    """
    root = Path(root) if root is not None else get_project_root()
    plan, _seeds, _dependents_ = _plan(module, method, outputs_only, root)
    return plan


def _still_planned(module: str, method: str, outputs_only: bool, root: Path,
                   planned: str) -> tuple[DraftDeletePlan, dict[_Key, dict],
                                          dict[_Key, dict]]:
    """A fresh plan and its records when its token is *planned*; else refuse."""
    plan, seeds, dependents = _plan(module, method, outputs_only, root)
    if plan.token != planned:
        action = ("delete the outputs of" if outputs_only
                  else "remove the draft of")
        raise DraftDeletePlanChangedError(
            f"Cannot {action} {qualified_draft(module, method)}: what it deletes "
            f"changed after it was planned (a draft ran, or its results were "
            f"cleared). Nothing was deleted. Plan it again (`wfc delete-draft`, "
            f"or delete again in the canvas) to see what it deletes now.")
    return plan, seeds, dependents


@workflow(purpose="Delete a draft's outputs as planned (Registration's Delete "
                  "outputs, the pointer kept) and clear the later drafts' "
                  "results computed from them; record 'outputs deleted' and "
                  "store each touched pipeline's status",
          inputs="the draft's module and method, the plan's token, project root",
          outputs="the plan it applied, whose done_message says what was deleted",
          critical="a token a fresh plan does not have deletes nothing")
def delete_draft_outputs_and_clear(module: str, method: str,
                                   root: Path | str | None = None, *,
                                   planned: str) -> DraftDeletePlan:
    """Delete outputs as planned, clearing as d-16 requires.

    Args:
        module: The draft's module.
        method: The draft's method.
        root: The project root; the resolved one when omitted.
        planned: The token of the plan the caller showed and the user
            confirmed (:func:`plan_draft_delete` with ``outputs_only=True``).

    Returns:
        The plan it applied; its ``done_message`` is what the CLI prints and
        the canvas route returns after the delete.

    Raises:
        DraftNotFoundError: The method has no draft.
        DraftDeletePlanChangedError: A fresh plan's token is not *planned*.
    """
    from ..registration import delete_draft_outputs

    root = Path(root) if root is not None else get_project_root()
    口 = Step(step_num=1, name="Plan again and check the token",
             purpose="The draft's run and every later draft's computed from it, "
                     "as the confirmed plan named them",
             critical="a fresh plan that differs refuses before any delete")
    plan, seeds, dependents = _still_planned(module, method, True, root, planned)

    口 = Step(step_num=2, name="Delete the outputs and the dependents' results",
             purpose="Registration's Delete outputs keeps the pointer")
    delete_draft_outputs(module, method, root)
    _clear_dependents(dependents, root)

    口 = AutoStep(step_num=3, name="Record the clearing on each pipeline")
    settle_cleared_pipelines({**seeds, **dependents}, REASON_OUTPUTS_DELETED, root)
    return plan


@workflow(purpose="Remove a draft as planned (Registration's Remove draft: the "
                  "draft folder goes) and clear the later drafts' results "
                  "computed from it; record 'removed' and store each touched "
                  "pipeline's status",
          inputs="the draft's module and method, the plan's token, project root",
          outputs="the draft folder gone; the plan it applied, whose "
                  "done_message says so",
          critical="a token a fresh plan does not have deletes nothing")
def remove_draft_and_clear(module: str, method: str,
                           root: Path | str | None = None, *,
                           planned: str) -> DraftDeletePlan:
    """Remove a draft as planned, clearing as d-16 requires.

    Args:
        module: The draft's module.
        method: The draft's method.
        root: The project root; the resolved one when omitted.
        planned: The token of the plan the caller showed and the user
            confirmed (:func:`plan_draft_delete`).

    Returns:
        The plan it applied; its ``done_message`` is what the CLI prints and
        the canvas route returns after the delete.

    Raises:
        DraftNotFoundError: The method has no draft.
        DraftDeletePlanChangedError: A fresh plan's token is not *planned*.
    """
    from ..registration import remove_draft

    root = Path(root) if root is not None else get_project_root()
    口 = Step(step_num=1, name="Plan again and check the token",
             purpose="The draft's run and every later draft's computed from it, "
                     "as the confirmed plan named them",
             critical="a fresh plan that differs refuses before any delete")
    plan, seeds, dependents = _still_planned(module, method, False, root, planned)

    口 = Step(step_num=2, name="Remove the draft and the dependents' results",
             purpose="Registration's Remove draft deletes the draft folder")
    remove_draft(module, method, root)
    _clear_dependents(dependents, root)

    口 = AutoStep(step_num=3, name="Record the clearing on each pipeline")
    settle_cleared_pipelines({**seeds, **dependents}, REASON_REMOVED, root)
    return plan


@workflow(purpose="Register a draft (Registration's register_draft: the ordinary "
                  "plan and apply, then the draft folder goes) and clear the "
                  "later drafts' results computed from it; record 'registered "
                  "as vN' and store each touched pipeline's status",
          inputs="the draft's module and method, the confirmation, project root",
          outputs="the MethodRegistration",
          critical="a refused registration keeps the draft and clears nothing")
def register_draft_and_clear(module: str, method: str, *, assume_yes: bool = False,
                             planned: str | None = None,
                             root: Path | str | None = None):
    """Register a draft, clearing as d-16 requires.

    Args:
        module: The draft's module.
        method: The draft's method.
        assume_yes: As :func:`~wfc.registration.register_draft` takes it.
        planned: As :func:`~wfc.registration.register_draft` takes it.
        root: The project root; the resolved one when omitted.

    Returns:
        The :class:`~wfc.registration.method.MethodRegistration`.

    Raises:
        DraftNotFoundError: The method has no draft.
        ValueError: Any refusal registration makes; nothing is cleared.
    """
    from ..registration import register_draft

    root = Path(root) if root is not None else get_project_root()
    口 = Step(step_num=1, name="Read what is cleared",
             purpose="The draft's run and every later draft's computed from it")
    seeds = _seed(module, method, root)
    dependents = _dependents(seeds, root)

    口 = AutoStep(step_num=2, name="Register the draft")
    registration = register_draft(module, method, assume_yes=assume_yes,
                                  planned=planned, root=root)

    口 = Step(step_num=3, name="Clear the dependents and record it",
             purpose="The draft folder went with the registration; the later "
                     "drafts' results go, and each pipeline reads 'registered "
                     "as vN'")
    _clear_dependents(dependents, root)
    settle_cleared_pipelines({**seeds, **dependents},
                             registered_reason(registration.label), root)
    return registration


__all__ = [
    "REASON_OUTPUTS_DELETED",
    "REASON_REMOVED",
    "ClearedDraft",
    "DraftDeletePlan",
    "DraftDeletePlanChangedError",
    "PlannedPipeline",
    "delete_draft_outputs_and_clear",
    "plan_draft_delete",
    "register_draft_and_clear",
    "registered_reason",
    "remove_draft_and_clear",
    "replaced_reason",
    "settle_cleared_pipelines",
    "settle_replacement",
]
