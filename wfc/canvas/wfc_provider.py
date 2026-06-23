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
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


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

    def _get_connection(self) -> sqlite3.Connection:
        """Open a read-only SQLite connection."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

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

    def load(self) -> None:
        """Load all runs from the wfc database."""
        self._runs = {}
        self._modules = {}
        self._methods = {}

        conn = self._get_connection()
        try:
            # Load modules
            for row in conn.execute("SELECT id, name FROM modules"):
                self._modules[row["id"]] = row["name"]

            # Load methods — detect whether the column is 'env' (new) or 'env_strategy' (legacy)
            method_cols = {r["name"] for r in conn.execute("PRAGMA table_info(methods)")}
            env_col = "env" if "env" in method_cols else "env_strategy"
            for row in conn.execute(f"SELECT id, name, module_id, script_path, {env_col} FROM methods"):
                self._methods[row["id"]] = {
                    "name": row["name"],
                    "module_id": row["module_id"],
                    "script_path": row["script_path"],
                    "env": row[env_col],
                }

            # Load runs with lineage and outputs
            # Detect optional columns (added in later schema versions)
            cursor = conn.execute("PRAGMA table_info(runs)")
            run_columns = {row["name"] for row in cursor}
            has_metrics = "metrics" in run_columns
            has_error_message = "error_message" in run_columns
            has_error_traceback = "error_traceback" in run_columns
            has_cancelled_due = "cancelled_due_to_run_id" in run_columns
            has_cache_source = "cache_source_run_id" in run_columns

            metrics_col = ", r.metrics" if has_metrics else ""
            err_msg_col = ", r.error_message" if has_error_message else ""
            err_tb_col = ", r.error_traceback" if has_error_traceback else ""
            cancelled_due_col = (
                ", r.cancelled_due_to_run_id" if has_cancelled_due else ""
            )
            cache_source_col = (
                ", r.cache_source_run_id" if has_cache_source else ""
            )
            # Aggregate every ``run_inputs`` row up front so a run with
            # multiple input slots (fan-in) carries all its parents, not
            # just the "last one wins" result of a LEFT JOIN. ``input_name``
            # is the slot the parent filled — the frontend uses it to
            # label per-slot parent chips in RunDetailPanel.
            parents_by_run: Dict[str, List[Dict[str, str]]] = {}
            for ri_row in conn.execute(
                "SELECT run_id, source_run_id, input_name FROM run_inputs "
                "ORDER BY id"
            ):
                if ri_row["source_run_id"] is None:
                    continue
                rid = str(ri_row["run_id"])
                parents_by_run.setdefault(rid, []).append({
                    "slot": ri_row["input_name"] or "upstream",
                    "sourceRunId": str(ri_row["source_run_id"]),
                })

            runs_sql = f"""
                SELECT
                    r.id,
                    r.method_id,
                    r.params,
                    r.sample,
                    r.status,
                    r.pipeline_id,
                    r.nf_process_name,
                    r.started_at,
                    r.finished_at
                    {metrics_col}
                    {err_msg_col}
                    {err_tb_col}
                    {cancelled_due_col}
                    {cache_source_col},
                    r.nid
                FROM runs r
            """
            for row in conn.execute(runs_sql):
                method_info = self._methods.get(row["method_id"], {})
                module_name = self._modules.get(
                    method_info.get("module_id", -1), "unknown"
                )
                method_name = method_info.get("name", "unknown")

                # Parse JSON fields
                params = {}
                if row["params"]:
                    try:
                        params = json.loads(row["params"]) if isinstance(row["params"], str) else row["params"]
                    except (json.JSONDecodeError, TypeError):
                        params = {}

                metrics = {}
                if has_metrics and row["metrics"]:
                    try:
                        raw = row["metrics"]
                        metrics = json.loads(raw) if isinstance(raw, str) else raw
                    except (json.JSONDecodeError, TypeError):
                        metrics = {}

                # Calculate timing
                started_ms = self._iso_to_epoch_ms(row["started_at"])
                finished_ms = self._iso_to_epoch_ms(row["finished_at"])
                duration = (finished_ms - started_ms) / 1000.0 if finished_ms > started_ms else 0

                # Map status
                status = row["status"] or "unknown"
                if status == "completed":
                    status = "success"

                # Full parent list assembled above from run_inputs.
                run_id_str = str(row["id"])
                parents_list = parents_by_run.get(run_id_str, [])
                parent_run_ids = [p["sourceRunId"] for p in parents_list]

                # Build run name: method/sample for readability
                sample = row["sample"] or ""
                run_name = f"{method_name}/{sample}" if sample else method_name

                # Read raw nid from DB (None if value is NULL)
                raw_nid = row["nid"]

                err_msg = row["error_message"] if has_error_message else None
                err_tb = row["error_traceback"] if has_error_traceback else None
                cancelled_due_raw = (
                    row["cancelled_due_to_run_id"] if has_cancelled_due else None
                )
                # Normalise to string so frontend consumers never have to
                # deal with a mixed int/str union (parentRunId is string;
                # cancelledDueToRunId mirrors that).
                cancelled_due = (
                    str(cancelled_due_raw) if cancelled_due_raw is not None else None
                )
                cache_source_raw = (
                    row["cache_source_run_id"] if has_cache_source else None
                )
                cache_source = (
                    str(cache_source_raw) if cache_source_raw is not None else None
                )

                run = WfcRun(
                    id=str(row["id"]),
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
                    experimentId=row["pipeline_id"] or "",
                    runName=run_name,
                    user="",
                    favorite=False,
                    nid=raw_nid or "",  # placeholder; computed below
                    pipelineId=row["pipeline_id"],
                    scriptPath=method_info.get("script_path"),
                    error_message=err_msg,
                    error_traceback=err_tb,
                    cancelledDueToRunId=cancelled_due,
                    cacheSourceRunId=cache_source,
                )

                self._runs[run.id] = run

            # Load outputs for each run
            for row in conn.execute(
                "SELECT run_id, output_name, artifact_path, artifact_type FROM run_outputs"
            ):
                run_id = str(row["run_id"])
                if run_id in self._runs:
                    run = self._runs[run_id]
                    name = row["output_name"] or "output"
                    run.outputs[name] = row["artifact_path"] or ""

            # Load user annotations (favorite / tags). Table may not exist
            # on databases that predate the RunAnnotation migration; skip
            # gracefully in that case.
            ann_tables = {
                r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "run_annotations" in ann_tables:
                # archived_at is nullable and may be absent on older DBs that
                # predate the archive migration; detect the column before
                # selecting it.
                ann_cols = {
                    r["name"] for r in conn.execute("PRAGMA table_info(run_annotations)")
                }
                has_archived = "archived_at" in ann_cols
                archived_sel = ", archived_at" if has_archived else ""
                for row in conn.execute(
                    f"SELECT run_id, favorite, tags{archived_sel} FROM run_annotations"
                ):
                    rid = str(row["run_id"])
                    if rid not in self._runs:
                        continue
                    run = self._runs[rid]
                    run.favorite = bool(row["favorite"])
                    raw_tags = row["tags"]
                    if raw_tags:
                        try:
                            parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                            if isinstance(parsed, list):
                                run.tags = [str(t) for t in parsed]
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if has_archived:
                        run.archivedAt = self._iso_to_epoch_ms(row["archived_at"]) or None

        finally:
            conn.close()

        # Resolve bundled sample lists for collapsed fan-in runs. A run with
        # sample="__all__" was produced by a step downstream of an
        # input_selector(fan_mode="in"); the actual sample list is stored in
        # the pipeline.json at .runs/pipelines/<pipeline_id>/pipeline.json.
        # Cache per pipeline_id so we only read each file once.
        pipeline_samples_cache: Dict[str, List[str]] = {}
        for run in self._runs.values():
            if run.dataSource != "__all__" or not run.pipelineId:
                continue
            if run.pipelineId not in pipeline_samples_cache:
                pipeline_samples_cache[run.pipelineId] = self._load_bundled_samples(run.pipelineId)
            samples = pipeline_samples_cache[run.pipelineId]
            if samples:
                run.bundledSamples = list(samples)

        # Compute NID auto-versions for runs without a custom nid.
        # Group runs by (sample, method_name), sort by timestamp, and
        # assign v1, v2, v3... to each run.  Runs with a custom nid
        # (non-empty string) keep their value; runs without get the
        # auto-version but still occupy a version slot.
        from collections import defaultdict
        groups: Dict[tuple, List[WfcRun]] = defaultdict(list)
        for run in self._runs.values():
            key = (run.dataSource, run.method)
            groups[key].append(run)

        for _key, group_runs in groups.items():
            group_runs.sort(key=lambda r: r.timestamp)
            for i, run in enumerate(group_runs, start=1):
                if not run.nid:
                    run.nid = f"v{i}"
                # else: keep custom nid as-is

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
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT name, file_type, registered_path, file_size, registered_at "
                "FROM samples ORDER BY name"
            ).fetchall()
            conn.close()
            return [
                {
                    "name": row["name"],
                    "file_type": row["file_type"],
                    "registered_path": row["registered_path"],
                    "file_size": row["file_size"],
                    "registered_at": row["registered_at"],
                }
                for row in rows
            ]
        except Exception:
            return []

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
                "env": m.get("env", "inherit"),
            }
            for m in self._methods.values()
        ]

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        """List top-level artifacts for a run.

        Files in the root of the run's archive directory are returned as
        ``type='file'`` rows. Subdirectories are returned as ``type='dir'``
        rows carrying a descendant-file ``count`` and a summed ``size``.
        The caller can drill into a directory by requesting its contents
        via a future per-path endpoint; this keeps the response shallow
        and paginatable regardless of how deep the run archive gets.
        """
        run_dir = self.project_root / ".runs" / f"{int(run_id):08d}"
        if not run_dir.exists():
            return []

        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        artifacts: List[Dict[str, Any]] = []

        for item in sorted(run_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.is_file():
                ext = item.suffix.lower()
                artifacts.append(
                    {
                        "name": item.name,
                        "type": "file",
                        "size": item.stat().st_size,
                        "is_image": ext in image_extensions,
                        "extension": ext.lstrip("."),
                    }
                )
            elif item.is_dir():
                count = 0
                total = 0
                for child in item.rglob("*"):
                    if child.is_file():
                        count += 1
                        total += child.stat().st_size
                # Direct children (one level) for the expand-in-place UI.
                # Shallow by design — deep dirs still only show the first
                # tier, matching the flat-list rendering in RunDetailPanel.
                direct_children: List[Dict[str, Any]] = []
                try:
                    for child in sorted(
                        item.iterdir(),
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
                        "name": item.name + "/",
                        "type": "dir",
                        "size": total,
                        "count": count,
                        "is_image": False,
                        "extension": "",
                        "children": direct_children,
                    }
                )

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

        target_ids = run_ids if run_ids else list(self._runs.keys())
        ext_set = {f'.{e.lstrip(".").lower()}' for e in extensions} if extensions else None
        results = []

        for rid in target_ids:
            run = self._runs.get(rid)
            if not run:
                continue

            try:
                run_dir = self.project_root / ".runs" / f"{int(rid):08d}"
            except (ValueError, TypeError):
                continue

            if not run_dir.exists():
                continue

            for item in run_dir.rglob("*"):
                if not item.is_file():
                    continue
                if ext_set is not None and item.suffix.lower() not in ext_set:
                    continue
                rel_path = item.relative_to(run_dir)
                results.append(
                    {
                        "run_id": rid,
                        "run_name": run.runName or rid,
                        "method": run.method,
                        "artifact_name": str(rel_path),
                        "file_path": str(item),
                        "extension": item.suffix.lstrip(".").lower(),
                        "size_bytes": item.stat().st_size,
                    }
                )

        return results

    def get_csv_artifacts(self, run_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Backward-compatible alias: collect only CSV artifacts."""
        return self.get_artifacts(run_ids, extensions=['csv'])

    def get_artifact_path(self, run_id: str, artifact_name: str) -> Optional[Path]:
        """Get the full path to an artifact file in the run's archive directory."""
        try:
            run_dir = self.project_root / ".runs" / f"{int(run_id):08d}"
        except (ValueError, TypeError):
            return None

        if not run_dir.exists():
            return None

        # Normalize path separators
        artifact_name = artifact_name.replace("/", os.sep).replace("\\", os.sep)
        artifact_path = run_dir / artifact_name

        if artifact_path.exists():
            return artifact_path
        return None
