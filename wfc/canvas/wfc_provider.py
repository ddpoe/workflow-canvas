"""
WFC Data Provider
=================

Reads wfc's SQLite database (.wfc/wfc.db) and converts run data
to the format expected by the Workflow Canvas history view.

Structure mapping:
- Module (wfc) = Module (canvas)  — e.g., data_preprocessing, data_labeling
- Method (wfc) = Method (canvas)  — e.g., ploidy_filtering, binary_labeling
- Run (wfc)    = Run (canvas)     — individual executions with params, metrics, artifacts
- RunInput.source_run_id = parentRunId  — lineage linking between runs
- Run.sample = dataSource              — the sample identifier
"""

import os
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from sqlmodel import Session, select

from wfc.database import build_engine, make_sqlite_url, ensure_schema
from wfc.models import (
    Module, Method, Run, RunInput, RunOutput, RunAnnotation, Sample,
)


# ---------------------------------------------------------------------------
# Pipeline Variables (Track 2): server-side substitution of {$var: name}
# refs in pipeline JSON before the dict is enriched and handed to the engine.
# ---------------------------------------------------------------------------


class UnknownVariableError(KeyError):
    """Raised when a pipeline contains a {$var: name} ref but ``variables``
    has no entry for ``name``. Caller (canvas server) translates to HTTP 400.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _is_var_ref(v: Any) -> bool:
    """Return True iff ``v`` is a ``{"$var": <str>}`` whole-value ref."""
    return (
        isinstance(v, dict)
        and len(v) == 1
        and "$var" in v
        and isinstance(v["$var"], str)
    )


def _substitute(v: Any, variables: Dict[str, Any]) -> Any:
    """Replace a single value if it is a var ref; otherwise return as-is.

    One-pass: if the substituted value itself is a var ref, raise. Cycle-
    safety per pitch edge case 9.
    """
    if not _is_var_ref(v):
        return v
    name = v["$var"]
    if name not in variables:
        raise UnknownVariableError(name)
    resolved = variables[name]
    if _is_var_ref(resolved):
        raise ValueError(
            f"Variable '{name}' resolves to another $var ref; nested refs are "
            "not allowed (one-pass substitution)."
        )
    return resolved


def resolve_variables(pipeline: Dict[str, Any]) -> Dict[str, Any]:
    """Substitute ``{"$var": name}`` refs in a pipeline dict with literals.

    Pure walker over ``nodes[].params`` and ``param_sets[node_id][variant]``.
    Whole-value dict splice — does NOT recurse into nested keys of a
    user-supplied dict literal that happens to contain ``$var`` somewhere
    deeper. Raises :class:`UnknownVariableError` when a referenced name
    is missing from ``pipeline['variables']``.

    Args:
        pipeline: Pipeline JSON dict. May contain a top-level ``variables``
            mapping name -> {type, value} or name -> raw value. Only the
            ``value`` is substituted; ``type`` is metadata.

    Returns:
        A NEW pipeline dict with ``variables`` removed and all refs
        replaced by their resolved literals. Input is not mutated.

    Raises:
        UnknownVariableError: a ref names a variable not in ``variables``.
        ValueError: a variable's value is itself a ``{$var}`` ref.
    """
    out = dict(pipeline)  # shallow copy
    raw_vars = out.pop("variables", None) or {}

    # Normalize variables to {name: value}: accept either {value, type}
    # or a bare value.
    variables: Dict[str, Any] = {}
    for name, entry in raw_vars.items():
        if isinstance(entry, dict) and "value" in entry:
            variables[name] = entry["value"]
        else:
            variables[name] = entry

    # Walk node params (top-level params dict per node).
    new_nodes = []
    for node in out.get("nodes", []) or []:
        nd = dict(node)
        params = nd.get("params") or {}
        if isinstance(params, dict):
            new_params = {k: _substitute(v, variables) for k, v in params.items()}
            nd["params"] = new_params
        new_nodes.append(nd)
    out["nodes"] = new_nodes

    # Walk param_sets[node_id][variant_name][param_name].
    ps = out.get("param_sets")
    if isinstance(ps, dict):
        new_ps: Dict[str, Any] = {}
        for node_id, variants in ps.items():
            if not isinstance(variants, dict):
                new_ps[node_id] = variants
                continue
            new_variants: Dict[str, Any] = {}
            for vname, vparams in variants.items():
                if not isinstance(vparams, dict):
                    new_variants[vname] = vparams
                    continue
                new_variants[vname] = {
                    k: _substitute(v, variables) for k, v in vparams.items()
                }
            new_ps[node_id] = new_variants
        out["param_sets"] = new_ps

    return out


@dataclass
class WfcRun:
    """Represents a wfc run in workflow canvas format."""
    id: str  # wfc uses int IDs, converted to string for canvas
    module: str
    method: str
    version: str = "1.0.0"
    timestamp: float = 0  # Unix timestamp in ms
    duration: float = 0  # Duration in seconds
    status: str = "unknown"
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    dataSource: str = ""  # sample name
    # Full lineage: every upstream run that fed this one, in slot order.
    # A method node with fan-in (multiple input slots from different
    # parents) contributes one entry per slot. Previously the provider
    # kept only a single scalar ``parentRunId`` by accident of a LEFT JOIN
    # + dict-overwrite; that flattened the DAG into an arbitrary tree and
    # made paths-view report one terminal per missing back-link. Storing
    # the full list lets the frontend walk the real DAG.
    parentRunIds: List[str] = field(default_factory=list)
    # Slot-aware view of the same data. ``slot`` is the method input name
    # (``experiment_config``, ``corrected_dir``, …) and ``sourceRunId``
    # is the run that produced it. Order matches ``parentRunIds``.
    parents: List[Dict[str, str]] = field(default_factory=list)
    experimentId: str = ""  # pipeline_id
    runName: str = ""
    user: str = ""
    favorite: bool = False
    nid: str = ""  # Node ID: auto-versioned (v1, v2...) or custom label
    tags: List[str] = field(default_factory=list)
    archivedAt: Optional[float] = None  # Unix ms; None = live (not archived)
    # Samples bundled into a collapsed fan-in run. Empty for normal per-sample
    # runs; populated when dataSource == "__all__" so the UI can show the real
    # sample list instead of the "__all__" sentinel.
    bundledSamples: List[str] = field(default_factory=list)
    # wfc-specific fields
    pipelineId: Optional[str] = None
    # Human-readable pipeline name from the Builder toolbar at submission
    # time, read from the pipeline record on disk. None for legacy or
    # unnamed pipelines.
    pipelineName: Optional[str] = None
    scriptPath: Optional[str] = None
    # Populated for runs that ended in failure. Both NULL for successful
    # and in-progress runs.
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    # Causality link for rows with status='cancelled': ID of the failed
    # run whose subtree caused this target to be skipped. NULL on
    # executed rows. Stored as string to match the ``parentRunId``
    # convention on the frontend (all run IDs are strings canvas-side).
    cancelledDueToRunId: Optional[str] = None
    # For cache-hit audit rows: the original run whose outputs were reused.
    # NULL for fresh executions. Surfaced in RunDetailPanel so users can
    # see "Cached from #N" and click through to the source.
    cacheSourceRunId: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class WfcProvider:
    """
    Provider for reading wfc's SQLite database.

    Reads the project structure:
    {project_root}/
        .wfc/
            wfc.db          ← SQLite database
        .runs/
            {id:08d}/      ← Run archive directories
                meta.json
                output.parquet / output.csv / etc.
        data/
            samples/
                {name}/    ← Registered sample files
    """

    def __init__(self, project_root: str):
        """
        Initialize provider with path to wfc project root.

        Parameters
        ----------
        project_root : str
            Absolute path to the wfc project directory (contains .wfc/ and .runs/)
        """
        self.project_root = Path(project_root)
        self.db_path = self.project_root / ".wfc" / "wfc.db"

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"wfc database not found: {self.db_path}\n"
                f"Expected a wfc project at: {project_root}"
            )

        self._runs: Dict[str, WfcRun] = {}
        self._modules: Dict[int, str] = {}  # id → name
        self._methods: Dict[int, Dict[str, Any]] = {}  # id → {name, module_id, script_path}
        self._loaded = False

    def _build_engine(self):
        """Build a ``db_path``-bound SQLAlchemy engine and additively backfill it.

        The provider reads through its own per-load engine (not the global
        ``get_engine()``) so it can be pointed at any ``project_root`` across
        ``/api/wfc/load`` calls without coupling to global-engine ordering. The
        engine is built through ``wfc.database.build_engine`` (the centralized
        factory) and run through ``ensure_schema`` so an older on-disk schema is
        upgraded to the current models before any ORM read. Callers must
        ``dispose()`` the returned engine.

        Note: ``ensure_schema`` needs write access, so the provider can no
        longer open the DB strictly ``mode=ro`` (the canvas already writes the
        same DB, so this is acceptable).

        Returns:
            A backfilled SQLAlchemy ``Engine`` bound to ``self.db_path``.
        """
        from sqlmodel import SQLModel

        engine = build_engine(make_sqlite_url(self.db_path))
        # Same two-step guarantee as wfc.database.get_engine: ensure_schema adds
        # newly-introduced columns to existing tables; create_all then builds any
        # wholly-missing tables (e.g. ``run_annotations`` / ``samples`` on an old
        # DB) so every ORM ``select`` below has a table to read.
        ensure_schema(engine)
        SQLModel.metadata.create_all(engine)
        return engine

    def _load_bundled_samples(self, pipeline_id: str) -> List[str]:
        """Return the sample list bundled into a fan-in collapsed pipeline.

        Collapsed runs carry sample="__all__" in the DB; the real sample
        list lives on the originating input_selector node's ``samples``
        array when ``fan_mode == "in"``. Reads the pipeline.json emitted
        by _enrich_pipeline on execution. Returns [] if the file is
        missing, malformed, or contains no fan-in selector.
        """
        pipeline_json = (
            self.project_root / ".runs" / "pipelines" / pipeline_id / "pipeline.json"
        )
        if not pipeline_json.exists():
            return []
        try:
            raw = json.loads(pipeline_json.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        for node in raw.get("nodes", []):
            if (
                node.get("type") == "input_selector"
                and node.get("fan_mode") == "in"
            ):
                samples = node.get("samples", [])
                if isinstance(samples, list):
                    return [str(s) for s in samples]
        return []

    def _load_pipeline_name(self, pipeline_id: str) -> Optional[str]:
        """Return the pipeline name recorded at submission time.

        The name lives in ``pipeline.editable.json`` (which preserves the
        user-submitted shape) and, for newer runs, in ``pipeline.json``
        too. Returns None when neither file carries a usable name —
        legacy and unnamed pipelines.
        """
        base = self.project_root / ".runs" / "pipelines" / pipeline_id
        for filename in ("pipeline.editable.json", "pipeline.json"):
            path = base / filename
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            name = raw.get("name")
            if isinstance(name, str) and name.strip():
                return name
        return None

    def _iso_to_epoch_ms(self, iso_str: Optional[str]) -> float:
        """Convert an ISO datetime string to Unix epoch milliseconds."""
        if not iso_str:
            return 0
        try:
            # Handle various datetime formats from SQLite
            for fmt in [
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z",
            ]:
                try:
                    dt = datetime.strptime(iso_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.timestamp() * 1000
                except ValueError:
                    continue
            return 0
        except Exception:
            return 0

    @staticmethod
    def _coerce_json_obj(value: Any) -> Any:
        """Normalise a JSON column value to the shape the canvas expects.

        The ORM deserializes JSON columns, so ``value`` is usually already a
        Python object (``dict`` / ``list`` / ``None``). This mirrors the raw
        reader's tolerance: a populated object passes through unchanged; a
        ``None`` (stored JSON ``null`` or unset column) stays ``None``; a stray
        JSON string is parsed; anything unparseable degrades to ``{}`` rather
        than raising.

        Args:
            value: The deserialized (or raw) JSON column value.

        Returns:
            The object as-is when it is a dict/list/None, the parsed value when
            it is a JSON string, or ``{}`` on a parse failure.
        """
        if value is None or isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
        return value

    def _to_epoch_ms(self, value: Any) -> float:
        """Convert a timestamp to Unix epoch milliseconds, accepting either shape.

        The raw sqlite3 reader received timestamps as ISO **strings** and ran
        them through :meth:`_iso_to_epoch_ms`. The SQLModel ORM hands back the
        same columns as Python ``datetime`` objects. This helper accepts both so
        ``timestamp`` / ``duration`` / ``archivedAt`` produce identical epoch-ms
        regardless of which read path supplied the value — a naive ``datetime``
        is treated as UTC, exactly as :meth:`_iso_to_epoch_ms` does for a naive
        ISO string.

        Args:
            value: A ``datetime``, an ISO datetime string, or ``None``.

        Returns:
            Epoch milliseconds as a float; ``0`` for ``None`` / unparseable input.
        """
        if value is None:
            return 0
        if isinstance(value, datetime):
            dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000
        return self._iso_to_epoch_ms(value)

    def load(self) -> None:
        """Load all runs from the wfc database.

        Builds into local dicts and swaps them in at the end: endpoints run
        in FastAPI's threadpool and ``get_all_runs`` reloads on every call,
        so concurrent readers must only ever observe a complete registry —
        old or new — never a cleared/partial one.
        """
        runs: Dict[str, WfcRun] = {}
        modules: Dict[int, str] = {}
        methods: Dict[int, Dict[str, Any]] = {}

        engine = self._build_engine()
        try:
            with Session(engine) as session:
                # Load modules
                for module in session.exec(select(Module)):
                    modules[module.id] = module.name

                # Load methods. Columns come straight from the model — no
                # env/env_strategy probing; ``ensure_schema`` guarantees the
                # ``env`` column exists on the on-disk schema.
                for method in session.exec(select(Method)):
                    methods[method.id] = {
                        "name": method.name,
                        "module_id": method.module_id,
                        "script_path": method.script_path,
                        "env": method.env,
                    }

                # Aggregate every ``run_inputs`` row up front so a run with
                # multiple input slots (fan-in) carries all its parents, not
                # just the "last one wins" result of a LEFT JOIN. ``input_name``
                # is the slot the parent filled — the frontend uses it to
                # label per-slot parent chips in RunDetailPanel. Ordered by id
                # to keep the full parent list in stable slot order.
                parents_by_run: Dict[str, List[Dict[str, str]]] = {}
                for ri in session.exec(select(RunInput).order_by(RunInput.id)):
                    if ri.source_run_id is None:
                        continue
                    rid = str(ri.run_id)
                    parents_by_run.setdefault(rid, []).append({
                        "slot": ri.input_name or "upstream",
                        "sourceRunId": str(ri.source_run_id),
                    })

                for run_row in session.exec(select(Run)):
                    method_info = methods.get(run_row.method_id, {})
                    module_name = modules.get(
                        method_info.get("module_id", -1), "unknown"
                    )
                    method_name = method_info.get("name", "unknown")

                    # JSON columns are deserialized by the ORM. They may be a
                    # dict (populated), None (stored JSON null / unset), or a
                    # malformed value; tolerate non-dicts exactly as the raw
                    # reader did (it returned the parsed value as-is).
                    params = self._coerce_json_obj(run_row.params)
                    metrics = self._coerce_json_obj(run_row.metrics)

                    # Calculate timing (datetime objects from the ORM; the
                    # helper also accepts ISO strings from a legacy/raw shape).
                    started_ms = self._to_epoch_ms(run_row.started_at)
                    finished_ms = self._to_epoch_ms(run_row.finished_at)
                    duration = (
                        (finished_ms - started_ms) / 1000.0
                        if finished_ms > started_ms else 0
                    )

                    # Map status
                    status = run_row.status or "unknown"
                    if status == "completed":
                        status = "success"

                    # Full parent list assembled above from run_inputs.
                    run_id_str = str(run_row.id)
                    parents_list = parents_by_run.get(run_id_str, [])
                    parent_run_ids = [p["sourceRunId"] for p in parents_list]

                    # Build run name: method/sample for readability
                    sample = run_row.sample or ""
                    run_name = f"{method_name}/{sample}" if sample else method_name

                    # Normalise causality/audit FKs to string so frontend
                    # consumers never see a mixed int/str union (parentRunId is
                    # string; these mirror that).
                    cancelled_due = (
                        str(run_row.cancelled_due_to_run_id)
                        if run_row.cancelled_due_to_run_id is not None else None
                    )
                    cache_source = (
                        str(run_row.cache_source_run_id)
                        if run_row.cache_source_run_id is not None else None
                    )

                    run = WfcRun(
                        id=str(run_row.id),
                        module=module_name,
                        method=method_name,
                        version="1.0.0",
                        timestamp=started_ms,
                        duration=duration,
                        status=status,
                        inputs=params,
                        outputs={},
                        metrics=metrics,
                        dataSource=sample,
                        parentRunIds=parent_run_ids,
                        parents=parents_list,
                        experimentId=run_row.pipeline_id or "",
                        runName=run_name,
                        user="",
                        favorite=False,
                        nid=run_row.nid or "",  # placeholder; computed below
                        pipelineId=run_row.pipeline_id,
                        scriptPath=method_info.get("script_path"),
                        error_message=run_row.error_message,
                        error_traceback=run_row.error_traceback,
                        cancelledDueToRunId=cancelled_due,
                        cacheSourceRunId=cache_source,
                    )

                    runs[run.id] = run

                # Load outputs for each run
                for out in session.exec(select(RunOutput)):
                    run_id = str(out.run_id)
                    if run_id in runs:
                        run = runs[run_id]
                        name = out.output_name or "output"
                        run.outputs[name] = out.artifact_path or ""

                # Load user annotations (favorite / tags / archived). The
                # ``run_annotations`` table and its ``archived_at`` column are
                # guaranteed present by ``ensure_schema`` + ``create_all`` — no
                # table-exists or column probing needed.
                for ann in session.exec(select(RunAnnotation)):
                    rid = str(ann.run_id)
                    if rid not in runs:
                        continue
                    run = runs[rid]
                    run.favorite = bool(ann.favorite)
                    raw_tags = ann.tags
                    if raw_tags:
                        try:
                            parsed = (
                                json.loads(raw_tags)
                                if isinstance(raw_tags, str) else raw_tags
                            )
                            if isinstance(parsed, list):
                                run.tags = [str(t) for t in parsed]
                        except (json.JSONDecodeError, TypeError):
                            pass
                    run.archivedAt = self._to_epoch_ms(ann.archived_at) or None
        finally:
            engine.dispose()

        # Resolve bundled sample lists for collapsed fan-in runs. A run with
        # sample="__all__" was produced by a step downstream of an
        # input_selector(fan_mode="in"); the actual sample list is stored in
        # the pipeline.json at .runs/pipelines/<pipeline_id>/pipeline.json.
        # Cache per pipeline_id so we only read each file once.
        pipeline_samples_cache: Dict[str, List[str]] = {}
        for run in runs.values():
            if run.dataSource != "__all__" or not run.pipelineId:
                continue
            if run.pipelineId not in pipeline_samples_cache:
                pipeline_samples_cache[run.pipelineId] = self._load_bundled_samples(run.pipelineId)
            samples = pipeline_samples_cache[run.pipelineId]
            if samples:
                run.bundledSamples = list(samples)

        # Resolve pipeline display names from the on-disk pipeline record,
        # cached per pipeline_id like the bundled-samples pass above.
        pipeline_name_cache: Dict[str, Optional[str]] = {}
        for run in runs.values():
            if not run.pipelineId:
                continue
            if run.pipelineId not in pipeline_name_cache:
                pipeline_name_cache[run.pipelineId] = self._load_pipeline_name(run.pipelineId)
            run.pipelineName = pipeline_name_cache[run.pipelineId]

        # Compute NID auto-versions for runs without a custom nid.
        # Group runs by (sample, method_name), sort by timestamp, and
        # assign v1, v2, v3... to each run.  Runs with a custom nid
        # (non-empty string) keep their value; runs without get the
        # auto-version but still occupy a version slot.
        from collections import defaultdict
        groups: Dict[tuple, List[WfcRun]] = defaultdict(list)
        for run in runs.values():
            key = (run.dataSource, run.method)
            groups[key].append(run)

        for _key, group_runs in groups.items():
            group_runs.sort(key=lambda r: r.timestamp)
            for i, run in enumerate(group_runs, start=1):
                if not run.nid:
                    run.nid = f"v{i}"
                # else: keep custom nid as-is

        # Atomic swap: publish the complete new registries in one step each.
        self._modules = modules
        self._methods = methods
        self._runs = runs
        self._loaded = True
        print(
            f"WfcProvider: Loaded {len(self._modules)} modules, "
            f"{len(self._methods)} methods, {len(self._runs)} runs"
        )

    def get_all_runs(self) -> List[Dict[str, Any]]:
        """Get all runs in workflow canvas format."""
        self.load()
        return [run.to_dict() for run in self._runs.values()]

    def get_experiments(self) -> List[Dict[str, Any]]:
        """Get pipeline executions (analogous to MLflow experiments)."""
        if not self._loaded:
            self.load()

        # Group by pipeline_id
        pipelines: Dict[str, List[WfcRun]] = {}
        for run in self._runs.values():
            pid = run.pipelineId or "no_pipeline"
            if pid not in pipelines:
                pipelines[pid] = []
            pipelines[pid].append(run)

        return [
            {
                "id": pid,
                "name": f"Pipeline {pid[:8]}" if pid != "no_pipeline" else "Ad-hoc Runs",
                "module": "mixed",
                "runCount": len(runs),
                "creationTime": min(r.timestamp for r in runs) if runs else 0,
            }
            for pid, runs in pipelines.items()
        ]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific run by ID."""
        if not self._loaded:
            self.load()
        run = self._runs.get(run_id)
        return run.to_dict() if run else None

    def get_run_tree(self, root_run_id: str) -> List[Dict[str, Any]]:
        """Get a run and all its descendants."""
        if not self._loaded:
            self.load()

        result = []
        root = self._runs.get(root_run_id)
        if root:
            result.append(root.to_dict())

        # Visited set is required now that parents form a DAG — a
        # descendant reached via two different parent paths (diamond)
        # must appear exactly once.
        visited: set[str] = {root_run_id} if root else set()

        def find_children(parent_id: str):
            children = [
                r for r in self._runs.values()
                if parent_id in r.parentRunIds and r.id not in visited
            ]
            for child in children:
                visited.add(child.id)
                result.append(child.to_dict())
                find_children(child.id)

        find_children(root_run_id)
        return result

    def get_cancelled_descendants(self, run_id: str) -> List[Dict[str, Any]]:
        """Return runs cancelled because this run (or its subtree) failed.

        A direct, non-recursive query against ``cancelled_due_to_run_id`` —
        the pipeline-end walk collapses the chain so every cancelled row
        already points at the *root* failed run, not an intermediate.
        """
        if not self._loaded:
            self.load()
        return [
            r.to_dict()
            for r in self._runs.values()
            if r.cancelledDueToRunId == run_id
        ]

    def get_lineage(self, run_id: str) -> Dict[str, Any]:
        """Get the full lineage (ancestors and descendants) of a run."""
        if not self._loaded:
            self.load()

        ancestors = []
        descendants = []

        # Walk up ancestors through the full DAG. A node with multiple
        # parents contributes every ancestor chain; ``visited`` guards
        # against cycles and against re-listing a shared ancestor
        # reached via different slots.
        visited_up: set[str] = {run_id}
        start = self._runs.get(run_id)
        frontier = list(start.parentRunIds) if start else []
        while frontier:
            pid = frontier.pop(0)
            if pid in visited_up:
                continue
            visited_up.add(pid)
            parent = self._runs.get(pid)
            if not parent:
                continue
            ancestors.append(parent.to_dict())
            for gp in parent.parentRunIds:
                if gp not in visited_up:
                    frontier.append(gp)

        # Walk down descendants with the same DAG-safe logic as
        # get_run_tree — diamond descendants appear once.
        visited_down: set[str] = {run_id}

        def find_children(parent_id: str):
            children = [
                r for r in self._runs.values()
                if parent_id in r.parentRunIds and r.id not in visited_down
            ]
            for child in children:
                visited_down.add(child.id)
                descendants.append(child.to_dict())
                find_children(child.id)

        find_children(run_id)

        run = self._runs.get(run_id)
        return {
            "run": run.to_dict() if run else None,
            "ancestors": ancestors,
            "descendants": descendants,
        }

    def get_modules(self) -> List[str]:
        """Get list of unique module names."""
        if not self._loaded:
            self.load()
        return list(set(self._modules.values()))

    def get_samples_detail(self) -> List[Dict[str, Any]]:
        """Get detailed info for all registered samples.

        Returns:
            List of dicts with name, file_type, registered_path, file_size,
            and registered_at for each sample.
        """
        if not self._loaded:
            self.load()
        try:
            engine = self._build_engine()
            try:
                with Session(engine) as session:
                    samples = session.exec(
                        select(Sample).order_by(Sample.name)
                    ).all()
                    return [
                        {
                            "name": s.name,
                            "file_type": s.file_type,
                            "registered_path": s.registered_path,
                            "file_size": s.file_size,
                            "registered_at": self._registered_at_str(s.registered_at),
                        }
                        for s in samples
                    ]
            finally:
                engine.dispose()
        except Exception:
            return []

    @staticmethod
    def _registered_at_str(value: Any) -> Any:
        """Render ``registered_at`` as the SQLite text shape the raw reader emitted.

        The raw sqlite3 reader returned ``registered_at`` as the literal text
        SQLite stored (``"YYYY-MM-DD HH:MM:SS.ffffff"``). The ORM hands back a
        ``datetime``; format it identically so the samples-detail payload is
        unchanged. Non-datetime values (already a string, or ``None``) pass
        through.

        Args:
            value: A ``datetime``, an ISO string, or ``None``.

        Returns:
            The microsecond-precision SQLite text form, or ``value`` unchanged.
        """
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S.%f")
        return value

    def get_completed_runs(self) -> List[Dict[str, Any]]:
        """Get completed runs with their output slots.

        Returns:
            List of dicts with id, method, module, sample, params,
            output_slots, pipeline_id, and finished_at for each
            completed run.
        """
        if not self._loaded:
            self.load()
        result = []
        for run in self._runs.values():
            if run.status != "success":
                continue
            result.append({
                "id": run.id,
                "method": run.method,
                "module": run.module,
                "sample": run.dataSource,
                "params": run.inputs,
                "output_slots": list(run.outputs.keys()),
                "pipeline_id": run.pipelineId or "",
                "finished_at": "",
            })
        return result

    def get_methods(self) -> List[Dict[str, Any]]:
        """Get all registered methods with their module info."""
        if not self._loaded:
            self.load()
        return [
            {
                "name": m["name"],
                "module": self._modules.get(m["module_id"], "unknown"),
                "script_path": m.get("script_path"),
                "env": m.get("env"),
            }
            for m in self._methods.values()
        ]

    def _resolved_outputs(self, run_id) -> List[tuple]:
        """Resolve a run's ``RunOutput`` rows to local cache paths (ADR-018).

        Reads the run's ``RunOutput`` rows through the provider's own
        ``db_path``-bound engine and resolves each named row via the shared
        ``wfc.cli.resolve_output`` with ``pull=False`` — GUI resolution is
        local-cache-only and never blocks an HTTP request on a remote pull.
        Rows that cannot be resolved (unnamed, un-archived NULL
        ``content_hash``, or missing from the local cache) are skipped with
        a log line rather than failing the caller.

        Args:
            run_id: Run id (string, as used throughout the provider).

        Returns:
            List of ``(RunOutput, cache_path)`` tuples for resolvable rows.
        """
        from wfc.cli import ResolveOutputError, resolve_output

        try:
            rid = int(run_id)
        except (ValueError, TypeError):
            return []

        results: List[tuple] = []
        engine = self._build_engine()
        try:
            with Session(engine) as session:
                rows = session.exec(
                    select(RunOutput).where(RunOutput.run_id == rid)
                ).all()
                for ro in rows:
                    if not ro.output_name:
                        print(
                            f"[wfc_provider] run {rid}: skipping unnamed "
                            f"output row id={ro.id}",
                            file=sys.stderr,
                        )
                        continue
                    try:
                        cache_path, _ = resolve_output(
                            rid,
                            ro.output_name,
                            pull=False,
                            project_dir=self.project_root,
                            session=session,
                        )
                    except ResolveOutputError as exc:
                        print(
                            f"[wfc_provider] run {rid}: skipping output "
                            f"'{ro.output_name}': {exc}",
                            file=sys.stderr,
                        )
                        continue
                    results.append((ro, cache_path))
        finally:
            engine.dispose()
        return results

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        """List top-level artifacts for a run from the DVC cache (ADR-018).

        One row per archived ``RunOutput``: file outputs are returned as
        ``type='file'`` rows named ``output_name`` + original suffix;
        directory outputs are returned as ``type='dir'`` rows carrying a
        descendant-file ``count``, a summed ``size``, and one-level
        ``children`` for the expand-in-place UI. Outputs that are
        un-archived or missing from the local cache are skipped (logged),
        never raised through the endpoint.
        """
        from wfc.cli import _output_export_name

        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        artifacts: List[Dict[str, Any]] = []

        for ro, cache_path in self._resolved_outputs(run_id):
            if cache_path.is_dir():
                count = 0
                total = 0
                for child in cache_path.rglob("*"):
                    if child.is_file():
                        count += 1
                        total += child.stat().st_size
                # Direct children (one level) for the expand-in-place UI.
                # Shallow by design — deep dirs still only show the first
                # tier, matching the flat-list rendering in RunDetailPanel.
                direct_children: List[Dict[str, Any]] = []
                try:
                    for child in sorted(
                        cache_path.iterdir(),
                        key=lambda p: (not p.is_dir(), p.name.lower()),
                    ):
                        if child.name.startswith("."):
                            continue
                        if child.is_file():
                            direct_children.append({
                                "name": child.name,
                                "size": child.stat().st_size,
                            })
                        elif child.is_dir():
                            direct_children.append({
                                "name": child.name + "/",
                                "size": 0,
                            })
                except OSError:
                    pass
                artifacts.append(
                    {
                        "name": ro.output_name + "/",
                        "type": "dir",
                        "size": total,
                        "count": count,
                        "is_image": False,
                        "extension": "",
                        "children": direct_children,
                    }
                )
            else:
                name = _output_export_name(ro)
                ext = Path(name).suffix.lower()
                artifacts.append(
                    {
                        "name": name,
                        "type": "file",
                        "size": cache_path.stat().st_size,
                        "is_image": ext in image_extensions,
                        "extension": ext.lstrip("."),
                    }
                )

        # Same presentation order as the pre-ADR-018 lister: directories
        # first, then case-insensitive by name.
        artifacts.sort(key=lambda a: (a["type"] != "dir", a["name"].lower()))
        return artifacts

    def get_artifacts(self, run_ids: Optional[List[str]] = None, extensions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Collect artifact file paths for the given runs (or all runs if None).

        Args:
            run_ids: List of run IDs to search (None = all loaded runs).
            extensions: File extensions to include, without dot, e.g. ['csv', 'json'].
                        None means include all file types.

        Returns a list of dicts:
            { run_id, run_name, method, artifact_name, file_path, extension, size_bytes }
        """
        if not self._loaded:
            self.load()

        from wfc.cli import _output_export_name

        target_ids = run_ids if run_ids else list(self._runs.keys())
        ext_set = {f'.{e.lstrip(".").lower()}' for e in extensions} if extensions else None
        results = []

        for rid in target_ids:
            run = self._runs.get(rid)
            if not run:
                continue

            for ro, cache_path in self._resolved_outputs(rid):
                if cache_path.is_dir():
                    # ADR-018 stores directory outputs as real directories —
                    # enumerate member files so the zip keeps per-file entries.
                    for member in cache_path.rglob("*"):
                        if not member.is_file():
                            continue
                        if ext_set is not None and member.suffix.lower() not in ext_set:
                            continue
                        rel = member.relative_to(cache_path).as_posix()
                        results.append(
                            {
                                "run_id": rid,
                                "run_name": run.runName or rid,
                                "method": run.method,
                                "artifact_name": f"{ro.output_name}/{rel}",
                                "file_path": str(member),
                                "extension": member.suffix.lstrip(".").lower(),
                                "size_bytes": member.stat().st_size,
                            }
                        )
                else:
                    artifact_name = _output_export_name(ro)
                    # Filter on the ACTUAL file suffix (the bare output_name
                    # may carry none; the cache entry itself is a hash name).
                    suffix = Path(artifact_name).suffix.lower()
                    if ext_set is not None and suffix not in ext_set:
                        continue
                    results.append(
                        {
                            "run_id": rid,
                            "run_name": run.runName or rid,
                            "method": run.method,
                            "artifact_name": artifact_name,
                            "file_path": str(cache_path),
                            "extension": suffix.lstrip("."),
                            "size_bytes": cache_path.stat().st_size,
                        }
                    )

        return results

    def get_csv_artifacts(self, run_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Backward-compatible alias: collect only CSV artifacts."""
        return self.get_artifacts(run_ids, extensions=['csv'])

    def get_artifact_path(self, run_id: str, artifact_name: str) -> Optional[Path]:
        """Resolve an artifact display name to its local cache path (ADR-018).

        ``artifact_name`` is the name this provider hands out elsewhere:
        ``output_name`` + original suffix for file outputs, or
        ``output_name`` / ``output_name/<member path>`` for directory
        outputs. Resolution is local-cache-only; unknown names return None.
        """
        from wfc.cli import _output_export_name

        requested = artifact_name.replace("\\", "/").strip("/")

        for ro, cache_path in self._resolved_outputs(run_id):
            if cache_path.is_dir():
                if requested == ro.output_name:
                    return cache_path
                if requested.startswith(ro.output_name + "/"):
                    member = cache_path / requested[len(ro.output_name) + 1:]
                    if member.exists():
                        return member
            else:
                if requested == _output_export_name(ro):
                    return cache_path
        return None
