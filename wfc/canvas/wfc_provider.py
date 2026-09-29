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
from contextlib import contextmanager
from pathlib import Path

from .. import layout
from ..contracts import COLLAPSED_SAMPLE
from ..lineage.relation import cancelled_descendants, upstreams
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from sqlmodel import Session, select

from wfc.persistence import get_engine
from wfc.persistence import (
    Module, Method, Run, RunInput, RunOutput, RunAnnotation, Sample,
)


class ArtifactPathRefusedError(ValueError):
    """An artifact name that is not a path inside one of the run's outputs.

    Attributes:
        name: The requested artifact name.
        reason: Why it was refused.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"artifact path {name!r} is refused: {reason}")


def _refuse_unsafe_artifact_name(name: str) -> None:
    """Refuse an artifact name that could step outside its output.

    Checked on the name as the route hands it over (already URL-decoded
    once), before any output is looked at.

    Args:
        name: The requested artifact name.

    Raises:
        ArtifactPathRefusedError: The name holds a backslash, is absolute or
            names a drive, has a ``..`` segment, or still carries a
            percent-escape (an encoded traversal).
    """
    from pathlib import PurePosixPath, PureWindowsPath
    from urllib.parse import unquote

    if "\\" in name:
        reason = "a backslash is not a path separator here"
    elif PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive:
        reason = "an absolute or drive path is not inside an output"
    elif ".." in name.split("/"):
        reason = "a '..' segment steps outside the output"
    elif unquote(name) != name:
        reason = "it still carries a percent-escape after decoding"
    else:
        return
    raise ArtifactPathRefusedError(name, reason)


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
    # parents) contributes one entry per slot, so the frontend can walk the
    # real DAG.
    parentRunIds: List[str] = field(default_factory=list)
    # Slot-aware view of the same data. ``slot`` is the method input name
    # (``experiment_config``, ``corrected_dir``, …), ``sourceRunId`` is the
    # run that produced it and ``sourceSlot`` is the output slot of that run
    # the input consumed (None when the input record names none). Order
    # matches ``parentRunIds``.
    parents: List[Dict[str, Optional[str]]] = field(default_factory=list)
    # The samples this run read, as recorded at the claim: ``slot`` is the
    # method input the sample arrived on, ``sample`` the sample's name, in
    # record order. Sample reads are not parents: they never appear in
    # ``parents``, ``parentRunIds`` or ``upstreamRunIds``. Empty for a run
    # that read no sample or whose reads were never recorded.
    sampleInputs: List[Dict[str, str]] = field(default_factory=list)
    experimentId: str = ""  # pipeline_id
    runName: str = ""
    user: str = ""
    favorite: bool = False
    nid: str = ""  # Node ID: auto-versioned (v1, v2...) or custom label
    tags: List[str] = field(default_factory=list)
    archivedAt: Optional[float] = None  # Unix ms; None = live (not archived)
    # Samples bundled into a collapsed fan-in run. Empty for normal per-sample
    # runs; populated when dataSource == COLLAPSED_SAMPLE so the UI can show
    # the real sample list instead of the collapsed-sample sentinel.
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
    # Resolved upstreams from the lineage relation: the input edges in slot
    # order, then ``cacheSourceRunId`` for a cache-hit row. The History
    # tab's views walk this field instead of re-deriving the rule.
    upstreamRunIds: List[str] = field(default_factory=list)

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
        self.db_path = layout.db_path(self.project_root)

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"wfc database not found: {self.db_path}\n"
                f"Expected a wfc project at: {project_root}"
            )

        self._runs: Dict[str, WfcRun] = {}
        self._modules: Dict[int, str] = {}  # id → name
        self._methods: Dict[int, Dict[str, Any]] = {}  # id → {name, module_id, script_path}
        self._loaded = False

    @contextmanager
    def _session(self):
        """Open a Persistence session on this project's database.

        The provider holds no engine of its own: it reads through the process
        engine, which Persistence binds by the URL rule. When that engine is
        bound to any database other than ``<project_root>/.wfc/wfc.db``, the
        provider would answer for the wrong project, so it refuses instead.

        Yields:
            A session on the process engine.

        Raises:
            RuntimeError: The process engine is bound to another database.
        """
        engine = get_engine()
        bound = engine.url
        if not self._names_file(bound.database, self.db_path):
            raise RuntimeError(
                f"The process database is {bound.render_as_string(hide_password=True)}, "
                f"not this project's {self.db_path}. Bind "
                f"{layout.database_url(self.project_root)} before loading the "
                f"history of {self.project_root}."
            )
        with Session(engine) as session:
            yield session

    @staticmethod
    def _names_file(bound: Optional[str], expected: Path) -> bool:
        """Return whether a bound SQLite database path names ``expected``.

        Args:
            bound: The engine URL's database path, or ``None`` for an
                in-memory database.
            expected: The database file the provider reads.

        Returns:
            True when both resolve to the same file.
        """
        if not bound:
            return False

        def _norm(p) -> str:
            return os.path.normcase(os.path.realpath(str(p)))

        return _norm(bound) == _norm(expected)

    def _load_bundled_samples(self, pipeline_id: str) -> List[str]:
        """Return the sample list bundled into a fan-in collapsed pipeline.

        Collapsed runs carry sample=COLLAPSED_SAMPLE in the DB; the real sample
        list lives on the originating input_selector node's ``samples``
        array when ``fan_mode == "in"``. Reads the pipeline.json the run
        route writes at submission. Returns [] if the file is
        missing, malformed, or contains no fan-in selector.
        """
        pipeline_json = layout.pipeline_doc_path(self.project_root, pipeline_id)
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

        The name is read from ``pipeline.editable.json`` (which preserves
        the user-submitted shape), then from ``pipeline.json``. Returns
        None when neither file carries a usable name.
        """
        base = layout.pipeline_run_dir(self.project_root, pipeline_id)
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
        Python object (``dict`` / ``list`` / ``None``). A populated object
        passes through unchanged; a
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

        The SQLModel ORM hands back timestamp columns as Python ``datetime``
        objects; an ISO **string** goes through :meth:`_iso_to_epoch_ms`. Both
        shapes give the same epoch-ms for ``timestamp`` / ``duration`` /
        ``archivedAt`` — a naive ``datetime`` is treated as UTC, exactly as
        :meth:`_iso_to_epoch_ms` does for a naive ISO string.

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

        with self._session() as session:
            # Load modules
            for module in session.exec(select(Module)):
                modules[module.id] = module.name

            # Load methods. Columns come straight from the model;
            # ``ensure_schema`` guarantees the ``env`` column exists on the
            # on-disk schema.
            for method in session.exec(select(Method)):
                methods[method.id] = {
                    "name": method.name,
                    "module_id": method.module_id,
                    "script_path": method.script_path,
                    "env": method.env,
                }

            # Aggregate every ``run_inputs`` row up front, ordered by id to
            # keep each list in stable slot order. A row is one of three
            # kinds:
            # - a source run: a parent. A run with several input slots
            #   (fan-in) carries all its parents. ``input_name`` is the slot
            #   the parent filled, which labels per-slot parent chips in
            #   RunDetailPanel; ``source_slot`` is the parent's output slot
            #   it consumed, which lineage synthesis wires the edge from.
            # - a sample name: a sample read, reported as the slot it
            #   arrived on and the sample's name.
            # - neither: a parent whose run was deleted (``wfc demo
            #   --remove`` nulls the source), skipped.
            parents_by_run: Dict[str, List[Dict[str, Optional[str]]]] = {}
            samples_by_run: Dict[str, List[Dict[str, str]]] = {}
            for ri in session.exec(select(RunInput).order_by(RunInput.id)):
                rid = str(ri.run_id)
                if ri.source_run_id is not None:
                    parents_by_run.setdefault(rid, []).append({
                        "slot": ri.input_name or "upstream",
                        "sourceRunId": str(ri.source_run_id),
                        "sourceSlot": ri.source_slot,
                    })
                elif ri.sample_name is not None:
                    samples_by_run.setdefault(rid, []).append({
                        "slot": ri.input_name,
                        "sample": ri.sample_name,
                    })

            for run_row in session.exec(select(Run)):
                method_info = methods.get(run_row.method_id, {})
                module_name = modules.get(
                    method_info.get("module_id", -1), "unknown"
                )
                method_name = method_info.get("name", "unknown")

                # JSON columns are deserialized by the ORM. They may be a
                # dict (populated), None (stored JSON null / unset), or a
                # malformed value; a non-dict passes through as parsed.
                params = self._coerce_json_obj(run_row.params)
                metrics = self._coerce_json_obj(run_row.metrics)

                # Calculate timing (datetime objects from the ORM; the
                # helper also accepts ISO strings).
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
                    sampleInputs=samples_by_run.get(run_id_str, []),
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

            # Load outputs for each run, keyed by the slot each one fills. A
            # record that names no slot is malformed and is not listed; its
            # run's readers report it when its outputs are read.
            for out in session.exec(select(RunOutput)):
                run_id = str(out.run_id)
                if run_id in runs and out.slot:
                    runs[run_id].outputs[out.slot] = out.artifact_path or ""

            # Load user annotations (favorite / tags / archived). The
            # ``run_annotations`` table and its ``archived_at`` column are
            # guaranteed present by ``ensure_schema`` + ``create_all``.
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

        # Resolve each run's upstreams through the lineage relation, so the
        # runs payload carries the one answer every view reads.
        for run in runs.values():
            run.upstreamRunIds = upstreams(run)

        # Resolve bundled sample lists for collapsed fan-in runs. A run with
        # sample=COLLAPSED_SAMPLE was produced by a step downstream of an
        # input_selector(fan_mode="in"); the actual sample list is stored in
        # the pipeline.json at .runs/pipelines/<pipeline_id>/pipeline.json.
        # Cache per pipeline_id so we only read each file once.
        pipeline_samples_cache: Dict[str, List[str]] = {}
        for run in runs.values():
            if run.dataSource != COLLAPSED_SAMPLE or not run.pipelineId:
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

    def run_records(self) -> Dict[str, WfcRun]:
        """Return the loaded run records by id, loading them first if needed.

        Lineage synthesis reads these records.
        """
        if not self._loaded:
            self.load()
        return self._runs

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific run by ID."""
        if not self._loaded:
            self.load()
        run = self._runs.get(run_id)
        return run.to_dict() if run else None

    def get_cancelled_descendants(self, run_id: str) -> List[Dict[str, Any]]:
        """Return runs cancelled because this run (or its subtree) failed.

        The rule is the lineage package's :func:`cancelled_descendants`, a
        filter on each run's cancellation pointer.
        """
        if not self._loaded:
            self.load()
        runs = self._runs
        return [runs[rid].to_dict() for rid in cancelled_descendants(runs, run_id)]

    def get_modules(self) -> List[str]:
        """Get list of unique module names."""
        if not self._loaded:
            self.load()
        return list(set(self._modules.values()))

    def get_samples_detail(self) -> List[Dict[str, Any]]:
        """Get detailed info for all registered samples.

        Returns:
            List of dicts with name, file_type, registered_path, file_size,
            registered_at, description (``None`` when none was given) and
            file_count (a directory's member count; ``None`` for a file)
            for each sample.
        """
        if not self._loaded:
            self.load()
        try:
            with self._session() as session:
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
                        "description": s.description,
                        "file_count": s.file_count,
                    }
                    for s in samples
                ]
        except Exception:
            return []

    @staticmethod
    def _registered_at_str(value: Any) -> Any:
        """Render ``registered_at`` in the text shape SQLite stores.

        The ORM hands back a ``datetime``; the samples-detail payload carries
        it as SQLite's stored text (``"YYYY-MM-DD HH:MM:SS.ffffff"``).
        Non-datetime values (already a string, or ``None``) pass through.

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

    def _named_outputs(self, run_id) -> List[tuple]:
        """Resolve a run's outputs to local cache paths, each with its name.

        Storage holds the rules. :func:`wfc.storage.provider_outputs`
        enumerates the run's records, following a cache-hit audit row one hop
        to its source run, raises when a record is malformed, names every
        record it found, and resolves each from the local cache only,
        skipping the ones it cannot resolve with a log line (the GUI never
        blocks an HTTP request on a remote pull). The naming covers the run's
        whole set of records, so the listing, the zip and the served file all
        call an output what ``wfc export --all`` calls it.

        Args:
            run_id: Run id (string, as used throughout the provider).

        Returns:
            List of ``(name, RunOutput, cache_path)`` tuples for resolvable
            records.

        Raises:
            wfc.storage.ResolveOutputError: The run has a malformed record,
                or its outputs cannot be named apart.
        """
        from wfc.storage import provider_outputs

        try:
            rid = int(run_id)
        except (ValueError, TypeError):
            return []

        with self._session() as session:
            return provider_outputs(
                rid, project_dir=self.project_root, session=session
            )

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        """List top-level artifacts for a run from the DVC cache.

        One row per archived ``RunOutput``, named by
        :func:`wfc.storage.output_export_names` (the file name, or
        ``<slot>/<file name>`` when two outputs share one): file outputs are
        returned as ``type='file'`` rows; directory outputs are returned as
        ``type='dir'`` rows carrying a descendant-file ``count``, a summed
        ``size``, and one-level ``children`` for the expand-in-place UI.
        Outputs that are un-archived or missing from the local cache are
        skipped (logged). A run with a malformed record raises.

        Raises:
            wfc.storage.ResolveOutputError: The run has a malformed record,
                or its outputs cannot be named apart.
        """
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        artifacts: List[Dict[str, Any]] = []

        for name, ro, cache_path in self._named_outputs(run_id):
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
                        "name": name + "/",
                        "type": "dir",
                        "size": total,
                        "count": count,
                        "is_image": False,
                        "extension": "",
                        "children": direct_children,
                    }
                )
            else:
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

        # Presentation order: directories first, then case-insensitive by
        # name.
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

        Raises:
            wfc.storage.ResolveOutputError: A requested run has a malformed
                record, or its outputs cannot be named apart.
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

            for name, ro, cache_path in self._named_outputs(rid):
                if cache_path.is_dir():
                    # Directory outputs are stored as real directories —
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
                                "artifact_name": f"{name}/{rel}",
                                "file_path": str(member),
                                "extension": member.suffix.lstrip(".").lower(),
                                "size_bytes": member.stat().st_size,
                            }
                        )
                else:
                    artifact_name = name
                    # Filter on the name's suffix (the cache entry itself is
                    # a hash name and carries none).
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

    def get_artifact_path(self, run_id: str, artifact_name: str) -> Optional[Path]:
        """Resolve an artifact display name to its local cache path.

        ``artifact_name`` is the name this provider hands out elsewhere,
        from the same naming (:meth:`_named_outputs`): the output's name for
        a file output, or the name / ``<name>/<member path>`` for a directory
        output. Resolution is local-cache-only; unknown names return None.

        A member path must stay inside its output: ``..`` segments, an
        absolute or drive path, a backslash separator, a percent-escape the
        route's decoding left behind, and a member that resolves outside the
        output (a symlink out) are refused, never rewritten.

        Raises:
            ArtifactPathRefusedError: The name is not a path inside an
                output.
            wfc.storage.ResolveOutputError: The run has a malformed record,
                or its outputs cannot be named apart.
        """
        _refuse_unsafe_artifact_name(artifact_name)
        requested = artifact_name

        for name, ro, cache_path in self._named_outputs(run_id):
            if cache_path.is_dir():
                if requested == name:
                    return cache_path
                if requested.startswith(name + "/"):
                    member = cache_path / requested[len(name) + 1:]
                    if not member.exists():
                        continue
                    root = cache_path.resolve()
                    if not member.resolve().is_relative_to(root):
                        raise ArtifactPathRefusedError(
                            artifact_name,
                            f"it resolves outside the output '{name}'")
                    return member
            else:
                if requested == name:
                    return cache_path
        return None
