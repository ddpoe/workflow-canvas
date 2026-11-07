"""The Canvas API's views of drafts, draft runs and draft steps.

Every rule stays in its owning unit; this module only reads them and spells
the answers in the API's shapes (``wfc.canvas.models``):

- Registration owns drafts: the pointer (``read_draft`` / ``list_drafts``),
  its status against the folder (``draft_status``: method.yaml changed,
  files updated, missing, what registering adds), the read-only re-read
  (``reread_draft``) and the Registration check (``check_draft``).
- Execution owns why a draft node cannot run or validate
  (``wfc.execution.validation.draft_refusals``, the draft preflight).
- Execution owns draft runs: ``draft-run.json`` (``read_draft_run``), the
  draft string (``spell_draft_ref`` / ``resolve_draft_step``) and parent
  entries (``parse_parent_entries``).
- Persistence holds each pipeline's ``pipeline_runs`` row, whose status
  Execution's status helper stores.
- Graph names a pipeline's draft-mode steps and their owners
  (``draft_owners``).

A draft step's status is said in the run rows' words: ``draft-run.json``'s
``completed`` reads ``success``; a step still ``running`` in a pipeline whose
stored status is no longer ``running`` reads ``cancelled``, as does a
draft-mode step the frozen document expects that never started once the
pipeline ended (``pending`` while it runs).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import col, select

from .. import layout
from ..persistence import PipelineRun, get_session
from .previews import viewer_kind

#: ``draft-run.json``'s step statuses in the run rows' words.
_STEP_STATUS = {"completed": "success", "failed": "failed", "running": "running",
                "cancelled": "cancelled"}
#: The stored pipeline status while it runs.
_RUNNING = "running"


# --- small readers ----------------------------------------------------------------


def pipeline_rows(pipeline_ids: set[str] | None = None) -> dict[str, PipelineRun]:
    """The ``pipeline_runs`` rows by pipeline id (all of them, or those named).

    Args:
        pipeline_ids: Only these pipelines; ``None`` reads every row.

    Returns:
        ``{pipeline_id: row}``; a pipeline without a row is absent.
    """
    with get_session() as session:
        query = select(PipelineRun)
        if pipeline_ids is not None:
            if not pipeline_ids:
                return {}
            query = query.where(col(PipelineRun.pipeline_id).in_(sorted(pipeline_ids)))
        rows = session.exec(query).all()
        for row in rows:
            session.expunge(row)
    return {row.pipeline_id: row for row in rows}


def _running(pipeline_id: str | None, rows: dict[str, PipelineRun]) -> bool:
    row = rows.get(str(pipeline_id)) if pipeline_id else None
    return row is not None and row.status == _RUNNING


def _epoch_ms(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        return int(datetime.fromisoformat(str(stamp)).timestamp() * 1000)
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _frozen_document(root: Path, pipeline_id: str | None) -> dict | None:
    if not pipeline_id:
        return None
    path = layout.pipeline_doc_path(root, pipeline_id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _version_label(root: Path, module: str, method: str, identity: Any) -> int | None:
    """The label of a registered version named by its content identity."""
    from ..registration import MalformedMethodRecordError, read_version_list

    try:
        entries = read_version_list(root, module, method) or []
    except (MalformedMethodRecordError, ValueError, OSError):
        return None
    return next((e.label for e in entries if e.identity == identity), None)


def contract_out(contract: dict | None) -> dict[str, Any]:
    """A contract ``parse_method_yaml`` read, as ``DraftContract``."""
    contract = contract or {}
    return {"inputs": contract.get("inputs") or {},
            "outputs": contract.get("outputs") or {},
            "params_schema": contract.get("params") or {},
            "executor": contract.get("executor") or "python"}


def check_out(check) -> dict[str, Any]:
    """A Registration check as ``DraftCheck``."""
    return {"ok": check.ok,
            "lines": [{"ok": line.ok, "text": line.text} for line in check.lines]}


# --- drafts -----------------------------------------------------------------------


def _draft_check(pointer, root: Path):
    from ..registration import check_draft

    return check_draft(pointer.module, pointer.method, root)


def draft_entry(pointer, root: Path, rows: dict[str, PipelineRun] | None = None,
                check=None) -> dict[str, Any]:
    """One draft as the Registry's Drafts group lists it (``DraftEntry``).

    Args:
        pointer: The draft's pointer.
        root: The project root.
        rows: The ``pipeline_runs`` rows already read, if any.
        check: The draft's Registration check, when already made.

    Returns:
        The entry: its kind and registers-as now, stale / files updated,
        the check and its failures, the stored contract and the latest draft
        run in brief.
    """
    from ..execution.draft import read_draft_run
    from ..registration import draft_status

    status = draft_status(pointer, root)
    check = check if check is not None else _draft_check(pointer, root)
    record = read_draft_run(pointer.module, pointer.method, root)
    if rows is None:
        rows = pipeline_rows({str(record.get("pipeline_id"))} if record else set())
    return {
        "module": pointer.module, "method": pointer.method, "kind": status.kind,
        "folder": str(pointer.folder),
        "env": (pointer.contract or {}).get("env"),
        "newestLabel": status.newest_label, "registersAs": status.registers_as,
        "filesUpdated": status.files_updated, "stale": status.stale,
        "valid": check.ok, "reasons": check.failures,
        "contract": contract_out(pointer.contract),
        "check": check_out(check),
        "draftRun": draft_run_summary(record, rows) if record else None,
    }


def palette_draft(pointer, root: Path) -> dict[str, Any]:
    """One draft in the Builder palette's Drafts group (``PaletteDraft``).

    The contract is Registration's read-only re-read of ``method.yaml`` now
    (nothing is written), so a node dropped or refreshed from the palette
    takes the contract a run reads; the check is that re-read's.
    """
    from ..registration import draft_status, reread_draft

    status = draft_status(pointer, root)
    contract: dict = pointer.contract or {}
    try:
        reread = reread_draft(pointer.module, pointer.method, root, write=False)
    except ValueError:
        check = _draft_check(pointer, root)  # the folder is gone: the check says so
    else:
        check, contract = reread.check, reread.contract or contract
    return {
        "kind": status.kind, "folder": str(pointer.folder),
        **contract_out(contract),
        "description": f"{pointer.module} — {pointer.method} (draft)",
        "env": contract.get("env"), "valid": check.ok, "reasons": check.failures,
    }


def snapshot_entry(module: str, method: str, root: Path) -> dict[str, Any]:
    """One draft folder against its pointer's stat snapshot (``SnapshotCheckEntry``).

    A changed ``method.yaml`` is reported as ``contractChanged``, never as a
    refusal: Run and Validate read it again.
    """
    from ..registration import draft_run_refusal, draft_status, read_draft

    out: dict[str, Any] = {"module": module, "method": method}
    try:
        pointer = read_draft(module, method, root)
    except ValueError as exc:
        return {**out, "state": "invalid", "message": str(exc)}
    if pointer is None:
        return {**out, "state": "missing",
                "message": draft_run_refusal(module, method, None, root)}
    status = draft_status(pointer, root)
    out |= {"filesUpdated": status.files_updated, "contractChanged": status.stale}
    if status.missing:
        return {**out, "state": "missing", "contractChanged": False,
                "message": draft_run_refusal(module, method, None, root)}
    check = _draft_check(pointer, root)
    if not check.ok:
        return {**out, "state": "invalid", "message": "; ".join(check.failures)}
    return {**out, "state": "current"}


# --- draft steps --------------------------------------------------------------------


def _all_draft_runs(root: Path) -> list[dict]:
    """Every draft's latest draft run record (drafts whose pointer reads)."""
    from ..execution.draft import read_draft_run
    from ..registration import list_drafts

    try:
        pointers = list_drafts(root)
    except ValueError:
        pointers = []
    records = []
    for pointer in pointers:
        record = read_draft_run(pointer.module, pointer.method, root)
        if record:
            records.append({**record, "module": pointer.module,
                            "method": pointer.method})
    return records


def _step_status(step: dict, running: bool) -> str:
    status = _STEP_STATUS.get(str(step.get("status")), "failed")
    if status == "running" and not running:
        return "cancelled"
    return status


def _parents(step: dict) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """A step's parent entries: recorded run ids, draft strings and the input lines."""
    from ..execution.parents import parse_parent_entries

    try:
        entries = parse_parent_entries(step.get("inputs") or [],
                                       step=str(step.get("node_id")), allow_drafts=True)
    except ValueError:
        return [], [], []
    runs, drafts, inputs = [], [], []
    for entry in entries:
        if entry.draft is not None:
            drafts.append(entry.draft)
            inputs.append({"slot": entry.input_slot, "source": "draft",
                           "draftString": entry.draft, "sourceSlot": entry.source_slot})
        else:
            runs.append(str(entry.run_id))
            inputs.append({"slot": entry.input_slot, "source": "run",
                           "runId": str(entry.run_id), "sourceSlot": entry.source_slot})
    return runs, drafts, inputs


def _step_out(record: dict, step: dict, running: bool, root: Path,
              document: dict | None) -> dict[str, Any]:
    from ..contracts import DRAFT_PIN
    from ..execution.draft import spell_draft_ref

    owner_module, owner_method = str(record["module"]), str(record["method"])
    node_id, sample = str(step.get("node_id")), str(step.get("sample"))
    module, method = str(step.get("module")), str(step.get("method"))
    is_draft = step.get("version") == DRAFT_PIN
    started, finished = _epoch_ms(step.get("started_at")), _epoch_ms(step.get("finished_at"))
    runs, drafts, _inputs = _parents(step)
    node = _document_node(document, node_id)
    return {
        "draftString": spell_draft_ref(owner_module, owner_method, node_id, sample),
        "module": module, "method": method, "nodeId": node_id, "sample": sample,
        "draft": f"{owner_module}.{owner_method}", "isDraft": is_draft,
        "status": _step_status(step, running),
        "timestamp": started,
        "duration": ((finished - started) / 1000
                     if started is not None and finished is not None else None),
        "pipelineId": str(record.get("pipeline_id")),
        "pipelineName": record.get("pipeline_name"),
        "nid": (node or {}).get("label") or None,
        "version": (None if is_draft else
                    _version_label(root, module, method, step.get("version"))),
        "sizeBytes": step.get("size"),
        "parentRunIds": runs, "parentDraftSteps": drafts,
        "error_message": step.get("error_message"),
    }


def _document_node(document: dict | None, node_id: str) -> dict | None:
    if not document:
        return None
    from ..graph import document_node

    return document_node(document, node_id)


def _record_steps(record: dict) -> list[dict]:
    return [step for by_sample in (record.get("steps") or {}).values()
            for step in by_sample.values() if isinstance(step, dict)]


def draft_steps(root: Path, pipeline_id: str | None = None,
                rows: dict[str, PipelineRun] | None = None) -> list[dict[str, Any]]:
    """Every draft step that still exists, as ``DraftStep``.

    Args:
        root: The project root.
        pipeline_id: Only the draft steps of this pipeline.
        rows: The ``pipeline_runs`` rows already read, if any.

    Returns:
        The steps, by draft, then node, then sample.
    """
    records = [r for r in _all_draft_runs(root)
               if pipeline_id is None or str(r.get("pipeline_id")) == pipeline_id]
    if rows is None:
        rows = pipeline_rows({str(r.get("pipeline_id")) for r in records})
    out: list[dict[str, Any]] = []
    documents: dict[str, dict | None] = {}
    for record in records:
        pid = str(record.get("pipeline_id"))
        if pid not in documents:
            documents[pid] = _frozen_document(root, pid)
        running = _running(pid, rows)
        steps = sorted(_record_steps(record),
                       key=lambda s: (str(s.get("node_id")), str(s.get("sample"))))
        out.extend(_step_out(record, step, running, root, documents[pid])
                   for step in steps)
    return out


def _resolved(ref: str, root: Path) -> tuple[Any, dict, dict]:
    """``resolve_draft_step`` with the record's draft named on it."""
    from ..execution.draft import resolve_draft_step

    parsed, record, step = resolve_draft_step(ref, root)
    return parsed, {**record, "module": parsed.module, "method": parsed.method}, step


def _result_dir(parsed, step: dict, root: Path) -> Path:
    return layout.draft_result_dir(root, parsed.module, parsed.method,
                                   str(step.get("node_id")), str(step.get("sample")))


def _artifacts(folder: Path) -> list[dict[str, Any]]:
    """List a draft step's files with the viewer each opens in.

    The viewer comes from the classifier the run listing uses, so the two
    listings agree on every name.

    Args:
        folder: The draft step's results folder.

    Returns:
        One ``{"name", "size", "viewer", "extension"}`` per file, by name;
        dotfiles and ``_``-prefixed files are left out.
    """
    files = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith((".", "_")):
                continue
            files.append({"name": path.name, "size": path.stat().st_size,
                          "viewer": viewer_kind(path.name),
                          "extension": path.suffix.lower()})
    return files


def _step_log(folder: Path) -> str | None:
    parts = []
    for name in ("stdout.log", "stderr.log"):
        path = folder / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n--- STDERR ---\n".join(p for p in parts if p) if parts else None


def draft_step_detail(ref: str, root: Path) -> dict[str, Any]:
    """History's run panel for one draft step (``DraftStepDetail``).

    Args:
        ref: The draft string.
        root: The project root.

    Returns:
        The step with its version text, folder, params, metrics, inputs,
        outputs, files and log.

    Raises:
        DraftRefError: The string misses a part, or names no draft step now.
    """
    parsed, record, step = _resolved(ref, root)
    pid = str(record.get("pipeline_id"))
    document = _frozen_document(root, pid)
    out = _step_out(record, step, _running(pid, pipeline_rows({pid})), root, document)
    _runs, _drafts, inputs = _parents(step)
    fed = {line["slot"] for line in inputs}
    if document:
        from ..graph import inbound_wiring

        for wire in inbound_wiring(document, out["nodeId"]):
            if wire.source_kind == "input_selector" and wire.target_slot not in fed:
                inputs.append({"slot": wire.target_slot, "source": "sample",
                               "sample": out["sample"]})
    folder = _result_dir(parsed, step, root)
    draft_folder = layout.draft_dir(root, parsed.module, parsed.method)
    outputs = []
    for slot, relpath in (step.get("outputs") or {}).items():
        path = draft_folder / str(relpath)
        outputs.append({"slot": slot, "filename": path.name,
                        "sizeBytes": path.stat().st_size if path.is_file() else None})
    return {
        "step": out,
        "versionText": "DRAFT" if out["isDraft"] else (
            f"v{out['version']}" if out["version"] is not None else "?"),
        "folder": str(record.get("folder") or ""),
        "keptUntil": f"{parsed.method}'s draft runs again",
        "params": step.get("params") or {}, "metrics": step.get("metrics") or {},
        "inputs": inputs, "outputs": outputs,
        "artifacts": _artifacts(folder), "log": _step_log(folder),
    }


def draft_step_file(ref: str, name: str, root: Path) -> Path:
    """One file of a draft step's folder.

    Raises:
        DraftRefError: The string names no draft step now.
        FileNotFoundError: No such file in the step's folder (a name that
            leaves the folder is refused the same way).
    """
    parsed, _record, step = _resolved(ref, root)
    folder = _result_dir(parsed, step, root).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise FileNotFoundError(f"The draft step {ref} has no file '{name}'.")
    return path


# --- draft runs ---------------------------------------------------------------------


def _run_status(statuses: list[str]) -> str:
    """A draft run's status from its steps': running, failed, cancelled, else succeeded."""
    for word, said in (("running", "running"), ("pending", "running"),
                       ("failed", "failed"), ("cancelled", "cancelled")):
        if word in statuses:
            return said
    return "succeeded"


def draft_run_summary(record: dict, rows: dict[str, PipelineRun]) -> dict[str, Any]:
    """A draft's latest draft run in brief (``DraftRunSummary``)."""
    pid = str(record.get("pipeline_id"))
    running = _running(pid, rows)
    statuses = [_step_status(step, running) for step in _record_steps(record)]
    return {"startedAt": str(record.get("started_at") or ""), "pipelineId": pid,
            "pipelineName": record.get("pipeline_name"),
            "status": _run_status(statuses) if statuses else (
                "running" if running else "cancelled"),
            "running": running}


def _expected_steps(document: dict | None, owner_node_ids: set[str]) -> dict[str, dict]:
    """The draft-mode nodes the frozen document gives a draft, with their samples."""
    if not document:
        return {}
    from ..graph import document_node, draft_owners

    try:
        owners = draft_owners(document)
    except (ValueError, KeyError, TypeError):
        return {}
    return {node_id: document_node(document, node_id) or {}
            for node_id, owner in owners.items() if owner in owner_node_ids}


def draft_run_detail(module: str, method: str, root: Path) -> dict[str, Any]:
    """The latest draft run of a draft (``DraftRunDetail``).

    Raises:
        LookupError: Why there is none: no draft, or no draft run.
    """
    from ..contracts import DRAFT_PIN
    from ..execution.draft import read_draft_run, spell_draft_ref
    from ..graph import is_draft_node
    from ..registration import DraftNotFoundError, read_draft

    if read_draft(module, method, root) is None:
        raise DraftNotFoundError(module, method)
    record = read_draft_run(module, method, root)
    if record is None:
        raise LookupError(f"{module}.{method} has no draft run: it has not run, or "
                          f"its outputs were deleted.")
    record = {**record, "module": module, "method": method}
    pid = str(record.get("pipeline_id"))
    running = _running(pid, pipeline_rows({pid}))
    document = _frozen_document(root, pid)
    by_node = record.get("steps") or {}
    own = {node_id for node_id, by_sample in by_node.items()
           if any(s.get("version") == DRAFT_PIN for s in by_sample.values())}
    if document:
        own |= {str(n.get("id")) for n in document.get("nodes") or []
                if isinstance(n, dict) and is_draft_node(n)
                and (n.get("module"), n.get("method")) == (module, method)}
    expected = _expected_steps(document, own)
    samples = sorted({str(s.get("sample")) for s in _record_steps(record)})
    nodes: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    total = 0
    for node_id in sorted(set(by_node) | set(expected)):
        by_sample = by_node.get(node_id) or {}
        node = expected.get(node_id) or {}
        first: dict[str, Any] = next(iter(by_sample.values()), {})
        steps: list[dict[str, Any]] = []
        for sample in sorted(set(by_sample) | set(samples)):
            step = by_sample.get(sample)
            status = (_step_status(step, running) if step is not None
                      else ("pending" if running else "cancelled"))
            size = (step or {}).get("size")
            steps.append({"sample": sample, "status": status, "sizeBytes": size,
                          "draftString": spell_draft_ref(module, method, node_id, sample)})
            tally[status] = tally.get(status, 0) + 1
            total += int(size or 0)
        node_statuses = [s["status"] for s in steps]
        nodes.append({
            "nodeId": node_id,
            "module": str(first.get("module") or node.get("module") or ""),
            "method": str(first.get("method") or node.get("method") or ""),
            "isDraft": node_id in own,
            "status": next((w for w in ("running", "pending", "failed", "cancelled")
                            if w in node_statuses), "success"),
            "sizeBytes": sum(int(s["sizeBytes"] or 0) for s in steps),
            "steps": steps,
        })
    statuses = [s["status"] for n in nodes for s in n["steps"]]
    return {
        "module": module, "method": method,
        "startedAt": str(record.get("started_at") or ""), "pipelineId": pid,
        "pipelineName": record.get("pipeline_name"), "samples": samples,
        "status": _run_status(statuses) if statuses else (
            "running" if running else "cancelled"),
        "tally": tally, "totalBytes": total,
        "keptUntil": f"{method}'s draft runs again", "nodes": nodes,
    }


def draft_run_zip(module: str, method: str, root: Path, node: str | None = None,
                  sample: str | None = None) -> io.BytesIO:
    """The draft run's result folders as a zip, ``<node>/<sample>/<file>``.

    Raises:
        LookupError: The draft has no draft run, or none of its steps match.
    """
    from ..execution.draft import read_draft_run

    record = read_draft_run(module, method, root)
    if record is None:
        raise LookupError(f"{module}.{method} has no draft run to download.")
    steps = [s for s in _record_steps(record)
             if (node is None or str(s.get("node_id")) == node)
             and (sample is None or str(s.get("sample")) == sample)]
    if not steps:
        raise LookupError(f"{module}.{method}'s draft run has no step for "
                          f"node {node or 'any'}, sample {sample or 'any'}.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for step in steps:
            node_id, step_sample = str(step.get("node_id")), str(step.get("sample"))
            folder = layout.draft_result_dir(root, module, method, node_id, step_sample)
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{node_id}/{step_sample}/"
                                   f"{path.relative_to(folder).as_posix()}")
    buf.seek(0)
    return buf


# --- History's pipelines -------------------------------------------------------------

#: The cleared reason's leading words, as Execution writes them, to the API's reason.
_CLEARED_REASONS = (("replaced", "replaced"), ("outputs deleted", "outputs_deleted"),
                    ("registered", "registered"), ("removed", "removed"))


def _cleared_parts(reason: str, drafts: list[str]) -> list[tuple[str, str]]:
    """``(draft, reason)`` per cleared draft, from a stored cleared reason.

    One draft's pipeline stores the bare reason; a pipeline with more than
    one draft stores ``<module>.<method>: <reason>`` per cleared draft,
    joined by ``; `` (Execution's spelling).
    """
    parts: list[tuple[str, str]] = []
    for part in reason.split("; "):
        draft, sep, why = part.partition(": ")
        if sep and "." in draft and " " not in draft:
            parts.append((draft, why.strip()))
    if parts:
        return parts
    return [(draft, reason) for draft in drafts[:1]]


def _cleared_draft(draft: str, why: str, at: str, row: PipelineRun,
                   root: Path | None) -> dict[str, Any] | None:
    lowered = why.lower()
    kind = next((said for word, said in _CLEARED_REASONS if lowered.startswith(word)), None)
    if kind is None:
        return None
    registered = re.search(r"registered as v(\d+)", lowered)
    entry: dict[str, Any] = {"draft": draft, "reason": kind, "at": at,
                             "registeredAs": int(registered.group(1)) if registered else None}
    if kind == "replaced" and root is not None:
        from ..execution.draft import read_draft_run

        module, _, method = draft.partition(".")
        record = read_draft_run(module, method, root) or {}
        replacer = record.get("pipeline_id")
        if replacer and str(replacer) != row.pipeline_id:
            entry["replacedByPipelineId"] = str(replacer)
    return entry


def pipeline_cleared(row: PipelineRun, remaining: int = 0,
                     root: Path | None = None) -> dict[str, Any] | None:
    """The greyed line of a pipeline whose draft steps were cleared (``PipelineCleared``).

    Execution writes ``cleared_reason`` in words ("replaced by the draft run
    of 14:31", "outputs deleted", "registered as v4", "removed"), per draft
    when the pipeline ran more than one; the line says how many steps went
    and why, and each cleared draft is listed with its reason.

    Args:
        row: The pipeline's ``pipeline_runs`` row.
        remaining: How many of its draft steps still exist (another draft's).
        root: The project root, to name the pipeline that replaced a draft run.

    Returns:
        The line, or None when nothing was cleared.
    """
    reason = (row.cleared_reason or "").strip()
    if not reason:
        return None
    count = max(int(row.draft_step_count or 0) - remaining, 0)
    noun = "draft step" if count == 1 else "draft steps"
    at = _iso(row.cleared_at) or ""
    drafts = [entry for draft, why in _cleared_parts(reason, list(row.drafts or []))
              if (entry := _cleared_draft(draft, why, at, row, root)) is not None]
    return {"count": count, "text": f"{count} {noun} cleared: {reason}.", "drafts": drafts}


def pipeline_draft_fields(row: PipelineRun, steps: list[dict[str, Any]],
                          root: Path | None = None) -> dict[str, Any]:
    """A pipeline summary's draft fields: mode, drafts, step count, steps, cleared line.

    The stored row says whether the pipeline ran in draft mode and how many
    draft-mode steps it ran; the steps that still exist come from the draft
    runs. A row written before its draft fields were stored is read from its
    draft steps.

    Args:
        row: The pipeline's ``pipeline_runs`` row.
        steps: Its draft steps that still exist (``DraftStep``).
        root: The project root, to name the pipeline that replaced a draft run.
    """
    drafts = list(row.drafts or [])
    for step in steps:
        if step["draft"] not in drafts:
            drafts.append(step["draft"])
    return {
        "name": row.name, "startedAt": _iso(row.started_at),
        "draftMode": bool(row.draft_mode or steps), "drafts": drafts,
        "draftStepCount": max(int(row.draft_step_count or 0), len(steps)),
        "draftSteps": steps,
        "cleared": pipeline_cleared(row, len(steps), root),
    }
