"""
Workflow Canvas -- FastAPI backend (wfc-native).

All data comes from the live wfc SQLite database -- no mock data, no MLflow.

Endpoints
---------
GET  /                            Serve the canvas SPA
GET  /api/modules                 All modules + methods + slot contracts (live DB)
POST /api/workflow/validate       Validate a workflow graph against the DB
POST /api/workflow/run            Submit a workflow for execution
POST /api/workflow/save           Save a workflow definition

POST /api/wfc/load                 (Re)load WfcProvider from a project path
POST /api/wfc/refresh              Reload from current project_root
GET  /api/wfc/status               Provider status
GET  /api/wfc/runs                 All wfc runs in canvas format
GET  /api/wfc/experiments          Pipelines as experiment groups
GET  /api/wfc/run/{id}             Single run
GET  /api/wfc/lineage/{id}         Full ancestor + descendant lineage
GET  /api/wfc/tree/{id}            Run + all descendants
GET  /api/wfc/modules              Module names (from provider cache)
GET  /api/wfc/methods              Method list
GET  /api/wfc/run/{id}/artifacts   List artifacts in a run archive dir
GET  /api/wfc/run/{id}/artifact/*  Serve an artifact file

POST /api/wfc/export-artifacts     Zip download (filtered by type)
POST /api/wfc/preview-artifacts    Export preview (counts + sizes)
POST /api/wfc/export-csvs          Zip download (CSV only)
POST /api/wfc/preview-csvs         Preview CSVs
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from ..contracts import output_slot_filename, validate_output_slot_type
from ..database import get_session
from ..models import Method, MethodContract, Module, Run, RunOutput
from .wfc_provider import WfcProvider

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static" / "dist"

# The frontend bundle lives in `static/dist/` after `npm run build`. In
# dev/test environments where the bundle has not been built, fall back to
# creating a minimal placeholder so `StaticFiles` can mount and tests can
# import `wfc.canvas.server` without a prior build step. Production
# deployments should always ship a built bundle; this is purely a
# graceful-degradation fallback.
if not _STATIC_DIR.exists():
    try:
        _STATIC_DIR.mkdir(parents=True, exist_ok=True)
        _placeholder = _STATIC_DIR / "index.html"
        if not _placeholder.exists():
            _placeholder.write_text(
                "<!doctype html><title>wfc canvas</title>"
                "<p>Frontend bundle not built. Run <code>npm run build</code> "
                "under <code>wfc/canvas/static/</code>.</p>",
                encoding="utf-8",
            )
    except OSError:
        pass

# ---------------------------------------------------------------------------
# Provider state
# ---------------------------------------------------------------------------

_wfc_provider: Optional[WfcProvider] = None

# ---------------------------------------------------------------------------
# Active job tracking (single pipeline at a time)
# ---------------------------------------------------------------------------

_active_jobs: Dict[str, Dict[str, Any]] = {}


def _import_run_pipeline():
    """Lazy import of run_pipeline from wfc.cli.

    Module-level reference so tests can mock ``wfc.canvas.server.run_pipeline_fn``
    and have the mock remain active during background thread execution.
    """
    from ..cli import run_pipeline
    return run_pipeline


def _import_fail_pipeline():
    """Lazy import of fail_pipeline from wfc.cli."""
    from ..cli import fail_pipeline
    return fail_pipeline


# Callable references -- overridden in tests via mock
run_pipeline_fn = _import_run_pipeline
fail_pipeline_fn = _import_fail_pipeline

def _enrich_pipeline(pipeline: "PipelineInput") -> Dict[str, Any]:
    """Enrich a PipelineJSON payload with script paths and slot_outputs from the DB.

    The canvas sends minimal node data (id, method, module, params).
    This function looks up each method's contract to add script_path
    and slot_outputs so load_pipeline() has everything it needs.

    Args:
        pipeline: The PipelineInput model from the canvas frontend.

    Returns:
        A dict with ``nodes``, ``links``, and ``samples`` keys matching
        the PipelineJSON format that ``load_pipeline()`` expects.
    """
    contract_map: Dict[str, Dict[str, Any]] = {}
    with get_session() as session:
        modules_db = {m.name: m for m in session.exec(select(Module)).all()}
        for mod in modules_db.values():
            for meth in mod.methods:
                mc = meth.contract
                contract_map[f"{mod.name}.{meth.name}"] = {
                    "output_slots": mc.output_slots if mc else {},
                    "script_path": meth.script_path,
                    "env": meth.env,
                }

    nodes = []
    for node in pipeline.nodes:
        node_type = node.type or "method"

        # System nodes pass through without method enrichment
        if node_type in ("input_selector", "run_reference"):
            node_dict: Dict[str, Any] = {
                "id": node.id,
                "type": node_type,
                "method": "",
                "module": "",
                "params": node.params,
            }
            if node_type == "input_selector":
                node_dict["samples"] = node.samples or []
                node_dict["source"] = node.source or "registered"
                # Preserve fan_mode through to the engine; default "out"
                # keeps per-sample semantics intact for legacy pipelines.
                node_dict["fan_mode"] = node.fan_mode or "out"
            elif node_type == "run_reference":
                node_dict["run_id"] = node.run_id
                # output_slot is legacy (pre-multi-output): only emit when
                # set so the engine doesn't see a spurious null and skip its
                # per-link source_slot resolution path.
                if node.output_slot:
                    node_dict["output_slot"] = node.output_slot
            if node.label:
                node_dict["label"] = node.label
            nodes.append(node_dict)
            continue

        key = f"{node.module or ''}.{node.method}"
        info = contract_map.get(key, {})
        output_slots = info.get("output_slots", {})
        script_path = info.get("script_path", f"methods/{node.method}/{node.method}.py")

        slot_outputs: Dict[str, str] = {}
        slot_types: Dict[str, str] = {}
        for slot_name, slot_spec in output_slots.items():
            raw_type = slot_spec.get("type") if isinstance(slot_spec, dict) else slot_spec
            # Defensive backstop: a DB contract that predates registration-time
            # validation still fails loud here rather than misnaming its file.
            # The slot `type` IS the file extension (verbatim, dotted) or a
            # `dir`/`directory` marker normalised to canonical `directory`.
            canonical_type = validate_output_slot_type(slot_name, raw_type)
            slot_outputs[slot_name] = output_slot_filename(slot_name, canonical_type)
            # ADR-010: emit parallel slot_types so both snakemake_gen and
            # run_step can consult the contract-declared type as the single
            # source of truth for directory-slot detection.
            slot_types[slot_name] = canonical_type

        node_dict = {
            "id": node.id,
            "method": node.method,
            "module": node.module or "",
            "script": script_path or f"methods/{node.method}/{node.method}.py",
            "params": node.params,
            "slot_outputs": slot_outputs,
            "slot_types": slot_types,
            "env": info.get("env"),
        }
        # Pass custom NID label through to pipeline JSON if set
        if node.label:
            node_dict["label"] = node.label
        nodes.append(node_dict)

    links = []
    for link in pipeline.links:
        l: Dict[str, str] = {"source": link.source, "target": link.target}
        if link.sourceHandle:
            l["source_slot"] = link.sourceHandle
        if link.targetHandle:
            l["target_slot"] = link.targetHandle
        links.append(l)

    result: Dict[str, Any] = {
        "nodes": nodes,
        "links": links,
        "samples": pipeline.samples,
    }
    # Pass through parameter-sweep fields when the canvas has authored any.
    # Both map 1:1 onto the engine's pipeline JSON schema — no transformation.
    if pipeline.param_sets:
        result["param_sets"] = pipeline.param_sets
    if pipeline.explicit_combos:
        result["explicit_combos"] = pipeline.explicit_combos
    return result


def _require_provider() -> WfcProvider:
    if _wfc_provider is None:
        raise HTTPException(
            status_code=400,
            detail="No wfc project configured. Call POST /api/wfc/load first.",
        )
    return _wfc_provider


# ---------------------------------------------------------------------------
# Lifespan -- auto-load provider from cwd on startup
# ---------------------------------------------------------------------------


def _switch_db(project_root: Path) -> None:
    """Update DATABASE_URL env var and reset the SQLAlchemy engine to point at
    a different project's wfc.db.  Called on auto-load and on POST /api/wfc/load."""
    from ..database import reset_engine
    new_url = f"sqlite:///{project_root / '.wfc' / 'wfc.db'}"
    os.environ["DATABASE_URL"] = new_url
    reset_engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _wfc_provider
    # Prefer explicit project root from CLI (--project-root) over the resolved
    # workflow-canvas root.  Never use Path.cwd() directly — cwd may shift or
    # be rewritten (e.g. C:\Windows on Snakemake subprocess spawns).
    from ..database import project_root as _resolve_project_root
    env_root = os.environ.get("WFC_CANVAS_PROJECT_ROOT")
    if env_root:
        project_root = Path(env_root)
    else:
        try:
            project_root = _resolve_project_root()
        except RuntimeError:
            project_root = Path.cwd()
    db_path = project_root / ".wfc" / "wfc.db"
    if db_path.exists():
        try:
            _wfc_provider = WfcProvider(str(project_root))
            _wfc_provider.load()
            _switch_db(project_root)
            print(f"[canvas] Auto-loaded wfc project from {project_root}")
        except Exception as exc:  # pragma: no cover
            print(f"[canvas] Warning: auto-load failed: {exc}")
    else:
        print(
            f"[canvas] No .wfc/wfc.db found in {project_root} "
            "-- use POST /api/wfc/load to configure"
        )
    yield


app = FastAPI(title="Workflow Canvas", lifespan=lifespan)


# =============================================================================
# Pydantic models
# =============================================================================


class PipelineNode(BaseModel):
    id: str
    type: Optional[str] = "method"  # "method" | "input_selector" | "run_reference"
    method: str = ""
    module: Optional[str] = None
    params: Dict[str, Any] = {}
    position: Optional[Dict[str, float]] = None
    label: Optional[str] = None  # custom NID from canvas node
    samples: Optional[List[str]] = None  # input_selector: selected samples
    run_id: Optional[str] = None  # run_reference: selected run ID
    output_slot: Optional[str] = None  # run_reference: selected output slot
    source: Optional[str] = None  # input_selector: source type
    fan_mode: Optional[str] = None  # input_selector: "out" (default, per-sample) | "in" (bundle)


class PipelineLink(BaseModel):
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class PipelineInput(BaseModel):
    name: Optional[str] = None
    nodes: List[PipelineNode]
    links: List[PipelineLink] = []
    samples: List[str] = []
    # Parameter-sweep fields carried through to the engine (wfc/snakemake_gen.py).
    # The canvas compiles its richer authoring state (sample_overrides, etc.)
    # into these two fields before POSTing — see frontend pipeline.ts.
    param_sets: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
    explicit_combos: Optional[List[Dict[str, Any]]] = None
    # When true, pass --keep-going to Snakemake so a failure in one job
    # doesn't cancel independent jobs. Useful for fan-out pipelines where
    # one bad sample shouldn't block the rest. Default off (fail-fast).
    keep_going: bool = False
    # Track 2: pipeline variables shelf. Maps variable name -> {type, value}
    # (or bare value for back-compat). Substituted server-side at /run via
    # wfc_provider.resolve_variables before _enrich_pipeline. The pre-
    # substitution form is also persisted as pipeline.editable.json so
    # History "Open in canvas" can rehydrate variables and bind chips.
    variables: Optional[Dict[str, Any]] = None


class WorkflowResponse(BaseModel):
    status: str
    job_id: str
    message: str


# Per-node state row returned by /api/workflow/status. Captured as a typed
# Pydantic model (rather than a bare ``Dict[str, Any]``) so the OpenAPI
# schema names every field the frontend may consume — ADR-015 Phase D
# Layer 1 makes the bridge function ``runStatusToNodeState`` consume the
# generated TS type, so any rename/removal here surfaces as a TS compile
# error.
class NodeRunState(BaseModel):
    status: str
    error: Optional[str] = None
    error_run_id: Optional[str] = None
    error_sample: Optional[str] = None
    tally: Optional[Dict[str, int]] = None
    run_ids: Optional[List[str]] = None
    upstream_node_id: Optional[str] = None
    upstream_run_id: Optional[str] = None
    cancelled_due_to_run_id: Optional[str] = None
    # Cache-hit surface (ADR-015 Phase D Bug 5 / Bug 4 Path A). Set when
    # the most-recent Run row for this method has ``cache_source_run_id``
    # populated (the cache-hit audit row). Frontend uses these to emit
    # CACHE_HIT into the per-node state machine and to render the
    # cache-hit banner in InspectorPanel without spawning a streaming
    # actor for a run that has nothing to stream.
    cache_hit: Optional[bool] = None
    original_run_id: Optional[str] = None
    cache_key: Optional[str] = None
    # ADR-018: per-node async push state. Always present (never absent on
    # the wire) so the frontend can read them without missing-field branches.
    # ``push_state`` is the aggregated state for this node's outputs:
    #   ``deferred`` -- no remote configured
    #   ``pending`` / ``in_flight`` -- at least one output still draining
    #   ``failed`` -- at least one output exhausted retries
    #   ``pushed`` -- all outputs successfully pushed
    # Counts are summed across all RunOutput rows for the node within the
    # current pipeline_id.
    push_state: str = "deferred"
    push_pending_count: int = 0
    push_failed_count: int = 0


class WorkflowStatusResponse(BaseModel):
    job_id: str
    overall_status: str
    steps: Dict[str, str]
    node_states: Dict[str, NodeRunState]
    thread_alive: bool
    log: str
    error: Optional[Any] = None


class WfcConfig(BaseModel):
    project_root: str


class ExportCSVsRequest(BaseModel):
    run_ids: Optional[List[str]] = None


class ExportArtifactsRequest(BaseModel):
    run_ids: Optional[List[str]] = None
    file_types: Optional[List[str]] = None


# =============================================================================
# Modules endpoint -- live DB query, no provider required
# =============================================================================


@app.get("/api/modules")
def get_modules():
    """Return modules as a nested dict keyed by module/method name for the builder UI.

    Shape: {moduleName: {description, methods: {methodName: {inputs, outputs, ...}}}}
    This matches what nodes.js / populateModulePalette() expect.
    """
    with get_session() as session:
        modules = session.exec(select(Module)).all()
        result: Dict[str, Any] = {}
        for mod in modules:
            methods_dict: Dict[str, Any] = {}
            for meth in mod.methods:
                mc: Optional[MethodContract] = meth.contract
                methods_dict[meth.name] = {
                    "inputs":        mc.input_slots   if mc else {},
                    "outputs":       mc.output_slots  if mc else {},
                    "version":       "1.0.0",
                    "description":   f"{mod.name} — {meth.name}",
                    "params_schema": mc.params_schema if mc else {},
                    "env":           meth.env,
                    "executor":      mc.executor      if mc else "python",
                }
            result[mod.name] = {
                "description": mod.description or mod.name,
                "methods": methods_dict,
            }
    return result


# =============================================================================
# Registry endpoints -- onboarding/registry UI (design_handoff_onboarding)
# =============================================================================


@app.get("/api/registry/modules")
def get_registry_modules():
    """List registered modules for the Registry tab.

    Shape matches design_handoff_onboarding/ENDPOINTS.md §2.1 (subset):
    `{modules: [{name, description, contracts[], methods, source}]}`.
    `color` is intentionally omitted -- the frontend assigns it from the
    `MOD_COLORS` cycle by registration order.
    """
    with get_session() as session:
        modules = session.exec(select(Module)).all()
        out: List[Dict[str, Any]] = []
        for mod in modules:
            contracts = [
                {
                    "type": c.contract_type,
                    "name": c.name,
                    "value_type": c.value_type,
                    "required": c.required,
                }
                for c in mod.contracts
            ]
            out.append({
                "name": mod.name,
                "description": mod.description or "",
                "contracts": contracts,
                "methods": len(mod.methods),
                "source": f"modules/{mod.name}/module.yaml",
            })
    return {"modules": out}


@app.get("/api/registry/methods")
def get_registry_methods():
    """List registered methods for the Registry tab.

    Shape (subset of ENDPOINTS.md §2.2): `{methods: [{name, module, env,
    validated, runCount, source}]}`.

    `validated` replaces the handoff's `status: "ok"|"stale"|"broken"` with
    a `bool | null` sourced from the dryRun cache (null = never checked).
    """
    from ..models import Run

    with get_session() as session:
        run_counts: Dict[int, int] = {}
        for row in session.exec(select(Run.method_id)).all():
            run_counts[row] = run_counts.get(row, 0) + 1

        modules = session.exec(select(Module)).all()
        out: List[Dict[str, Any]] = []
        for mod in modules:
            for meth in mod.methods:
                out.append({
                    "name": meth.name,
                    "module": mod.name,
                    "env": meth.env,
                    "validated": None,
                    "runCount": run_counts.get(meth.id, 0),
                    "source": f"methods/{meth.name}/method.yaml",
                })
    return {"methods": out}


# ---- Method validation (env + import check) --------------------------------
#
# The design handoff's `status: "ok"|"stale"|"broken"` enum is replaced with
# `validated: bool | null`, populated from an in-memory cache keyed on
# `(method_id, script_fingerprint)`. POST /api/registry/methods/validate
# runs the check on demand; GET /api/registry/methods reads the cache.

_method_validate_cache: Dict[tuple, Dict[str, Any]] = {}


def _default_run_import_check(python_bin: str, script_path: str):
    """Load the script as a non-`__main__` module under the declared env.

    Uses importlib.util so any ``if __name__ == "__main__":`` guard inside the
    script does NOT fire — we want import-time side effects only (failed
    imports, syntax errors), not execution of the method's main() body.

    Returns (returncode, stdout, stderr). Overridable via
    ``_run_import_check_fn`` module-level attribute for tests.
    """
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('_wfc_validate_mod', {script_path!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['_wfc_validate_mod'] = mod\n"
        "spec.loader.exec_module(mod)\n"
    )
    result = subprocess.run(
        [python_bin, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    return (result.returncode, result.stdout, result.stderr)


_run_import_check_fn = _default_run_import_check


def _script_fingerprint(script_path: Path) -> Optional[str]:
    if not script_path.exists():
        return None
    return hashlib.sha256(script_path.read_bytes()).hexdigest()


def _project_root() -> Path:
    env_root = os.environ.get("WFC_CANVAS_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    return Path.cwd()


class MethodValidateRequest(BaseModel):
    module: str
    method: str


_LANG_BY_EXT: Dict[str, str] = {
    ".py": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "shell",
    ".txt": "text",
}


@app.get("/api/registry/methods/{module_name}/{method_name}/detail")
def get_registry_method_detail(module_name: str, method_name: str):
    """Read-only view of a method's directory + its parsed contract.

    Lists every file in the method dir (no recursion) with its content and a
    language hint for syntax highlighting. Returns the DB-parsed contract
    (input_slots, output_slots, params_schema) alongside so the frontend can
    render it as structured tables without re-parsing YAML.
    """
    with get_session() as session:
        mod = session.exec(select(Module).where(Module.name == module_name)).first()
        if mod is None:
            raise HTTPException(status_code=404, detail=f"Module not found: {module_name}")
        meth = next((m for m in mod.methods if m.name == method_name), None)
        if meth is None:
            raise HTTPException(
                status_code=404,
                detail=f"Method not found: {module_name}.{method_name}",
            )
        contract = meth.contract
        contract_out = {
            "input_slots":   contract.input_slots   if contract else {},
            "output_slots":  contract.output_slots  if contract else {},
            "params_schema": contract.params_schema if contract else {},
            "executor":      contract.executor      if contract else "python",
        }
        script_rel = meth.script_path or f"methods/{meth.name}/{meth.name}.py"

    # Method dir = parent dir of the script path. Guard against DB poisoning:
    # the resolved dir must stay under the project root.
    project_root = _project_root().resolve()
    method_dir = (project_root / script_rel).parent.resolve()
    try:
        method_dir.relative_to(project_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"method directory escapes project root (path traversal): {script_rel}",
        )
    if not method_dir.is_dir():
        return {"files": [], "contract": contract_out}

    files: List[Dict[str, Any]] = []
    for entry in sorted(method_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith(".") or entry.name.endswith(".pyc"):
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # skip binaries / unreadable
        files.append({
            "name": entry.name,
            "language": _LANG_BY_EXT.get(entry.suffix, "text"),
            "content": content,
        })
    return {"files": files, "contract": contract_out}


@app.post("/api/registry/methods/validate")
def validate_registry_method(req: MethodValidateRequest):
    """Re-validate an existing method's env+import. Caches by fingerprint."""
    with get_session() as session:
        mod = session.exec(select(Module).where(Module.name == req.module)).first()
        if mod is None:
            raise HTTPException(status_code=404, detail=f"Module not found: {req.module}")
        meth = next((m for m in mod.methods if m.name == req.method), None)
        if meth is None:
            raise HTTPException(
                status_code=404,
                detail=f"Method not found: {req.module}.{req.method}",
            )
        method_id = meth.id
        script_rel = meth.script_path or f"methods/{meth.name}/{meth.name}.py"
        env_spec = meth.env

    script_path = _project_root() / script_rel
    fingerprint = _script_fingerprint(script_path)

    pre_checks: List[Dict[str, Any]] = []
    if fingerprint is None:
        pre_checks.append({
            "status": "fail",
            "label": "script file exists",
            "detail": f"not found: {script_rel}",
        })
        return {"validated": False, "preChecks": pre_checks}

    cache_key = (method_id, fingerprint)
    if cache_key in _method_validate_cache:
        return _method_validate_cache[cache_key]

    python_bin = sys.executable
    rc, stdout, stderr = _run_import_check_fn(python_bin, str(script_path))

    validated = rc == 0
    pre_checks.append({
        "status": "ok" if validated else "fail",
        "label": "script imports under env",
        "detail": (stderr or "").strip()[:500] if not validated else f"env={env_spec}",
    })

    response = {"validated": validated, "preChecks": pre_checks}
    _method_validate_cache[cache_key] = response
    return response


# ---- Registry writes (module / method / sample) ----------------------------
#
# Route handlers wrap the existing wfc.register / wfc.cli registration helpers.
# Errors from the Python API are mapped to HTTP status codes:
#   FileNotFoundError       -> 404
#   ValueError              -> 400
#   DvcNotConfiguredError   -> 409
# Each hook is exposed as a module-level callable so tests can patch it.


def _default_register_sample(*args, **kwargs):
    from ..cli import register_sample
    return register_sample(*args, **kwargs)


_register_sample_fn = _default_register_sample


def _default_register_module(*args, **kwargs):
    from ..register import register_module
    return register_module(*args, **kwargs)


_register_module_fn = _default_register_module


class ContractSpec(BaseModel):
    type: str  # "output" | "metric" | "input" | "param"
    name: str
    value_type: Optional[str] = None
    required: bool = True


class ModuleRegisterRequest(BaseModel):
    name: str
    description: Optional[str] = None
    folder: Optional[str] = None
    contracts: List[ContractSpec] = []


def _module_pre_checks(req: ModuleRegisterRequest) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    with get_session() as session:
        existing = session.exec(
            select(Module).where(Module.name == req.name)
        ).first()
        if existing is not None:
            checks.append({
                "status": "fail",
                "label": "name available",
                "detail": f"already registered as {req.name}",
            })
        else:
            checks.append({"status": "ok", "label": "name available"})
    checks.append({"status": "ok", "label": "request parses"})
    return checks


@app.post("/api/registry/modules")
def register_module_endpoint(req: ModuleRegisterRequest, dryRun: bool = False):
    """Wrap ``wfc.register.register_module`` with optional dry-run preflight."""
    checks = _module_pre_checks(req)
    ok = all(c["status"] != "fail" for c in checks)

    if dryRun or not ok:
        return {"ok": ok, "preChecks": checks}

    try:
        _register_module_fn(
            name=req.name,
            description=req.description,
            contracts=[c.model_dump() for c in req.contracts],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "preChecks": checks, "module": {"name": req.name}}


@app.get("/api/registry/samples")
def get_registry_samples():
    """List registered samples for the Registry tab.

    Shape (subset of ENDPOINTS.md §2.4): `{samples: [{name, source, size,
    hash, pushed, runCount, registered_at}]}`.

    `pushed` is `True` when a DVC content hash is present (indicates the sample
    is in the DVC cache and pushable to the remote); `False` otherwise.
    """
    from ..models import Run, Sample

    with get_session() as session:
        run_counts: Dict[str, int] = {}
        for row in session.exec(select(Run.sample)).all():
            if row:
                run_counts[row] = run_counts.get(row, 0) + 1

        samples = session.exec(select(Sample).order_by(Sample.name)).all()
        out: List[Dict[str, Any]] = [
            {
                "name": s.name,
                "source": s.source_path,
                "size": s.file_size,
                "hash": s.content_hash,
                "pushed": s.content_hash is not None,
                "runCount": run_counts.get(s.name, 0),
                "registered_at": s.registered_at.isoformat() if s.registered_at else None,
                "file_type": s.file_type,
            }
            for s in samples
        ]
    return {"samples": out}


# =============================================================================
# Envs registry endpoints
# =============================================================================
#
# Surfaces env specs referenced by registered methods plus the per-env
# env_fingerprint history captured at pre_run time by the env-fingerprint-
# provenance PEV cycle (2026-04-18). The snapshot endpoint lets the user
# refresh env content on demand without creating a Run row.

_HEX32 = set("0123456789abcdef")


def _valid_md5(md5: str) -> bool:
    """Return True if ``md5`` is a 32-char lowercase hex string."""
    return len(md5) == 32 and all(c in _HEX32 for c in md5)


def _env_record_for_spec(spec: str, project_dir: Optional[Path] = None):
    """Resolve a ``Method.env`` spec to its registered ``EnvRecord``.

    Normalizes the spec to the bare manifest name — strips a ``container:``
    prefix, a ``docker://`` scheme, and a trailing ``@sha256:<hex>`` digest —
    then looks it up in ``.wfc/envs.json``. Typed (``pixi:``/``conda:``) or
    otherwise-unmatched specs that have no manifest entry resolve to
    ``(None, None)`` gracefully, so callers can surface an honest "not
    captured" state rather than erroring.

    Args:
        project_dir: Project root containing ``.wfc/``. Defaults to the
            canvas project root.
        spec: The ``Method.env`` value (e.g. ``container:demo``).

    Returns:
        ``(record, backend)`` — the :class:`wfc.envs.EnvRecord` and its
        ``backend`` string, or ``(None, None)`` when no manifest entry
        matches.
    """
    from .. import envs as envs_mod

    if project_dir is None:
        project_dir = _project_root()

    name = spec
    if name.startswith("container:"):
        name = name[len("container:"):]
    if name.startswith("docker://"):
        name = name[len("docker://"):]
    idx = name.rfind("@sha256:")
    if idx >= 0:
        name = name[:idx]

    try:
        record = envs_mod.get(name, project_dir)
    except Exception:
        record = None
    if record is None:
        return None, None
    return record, record.backend


def _read_env_blob_text(md5: str, project_root: Path) -> str:
    """Read a DVC-cached env-content blob by md5, with a path-traversal guard.

    Shared read path for ``GET .../blob/<md5>`` and ``GET .../packages``.

    Args:
        md5: 32-char lowercase hex content hash.
        project_root: Canvas project root (containing ``.dvc/``).

    Returns:
        The decoded blob text.

    Raises:
        HTTPException: 400 on malformed md5 or attempted path traversal,
            404 when the blob is absent from the local cache.
    """
    if not _valid_md5(md5):
        raise HTTPException(status_code=400, detail="malformed md5 (expect 32 lowercase hex)")

    cache_root = (project_root.resolve() / ".dvc" / "cache" / "files" / "md5").resolve()
    blob_path = (cache_root / md5[:2] / md5[2:]).resolve()

    # Path-traversal guard: the resolved path must stay under cache_root.
    try:
        blob_path.relative_to(cache_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal rejected")

    if not blob_path.is_file():
        raise HTTPException(status_code=404, detail=f"blob not found: {md5}")

    return blob_path.read_text(encoding="utf-8")


@app.get("/api/registry/envs")
def get_registry_envs():
    """List distinct env specs referenced by registered methods.

    One row per ``Method.env`` value. Aggregates method names, total run
    count, and most-recent run timestamp (``Run.started_at``) across all
    methods sharing that env, and resolves the registered env's ``backend``
    plus a ``has_packages`` flag (True when a ``source_fingerprint`` was
    captured at registration — i.e. a package list is available to attempt).
    It does not guarantee a non-empty result: ``GET .../packages`` parses that
    blob and may return an empty list if the captured blob carries no
    recognizable packages (e.g. an older capture).
    """
    project_root = _project_root()
    with get_session() as session:
        # Methods grouped by env spec.
        methods = session.exec(select(Method)).all()
        modules_by_id: Dict[int, str] = {
            m.id: m.name for m in session.exec(select(Module)).all()
        }

        by_env: Dict[str, Dict[str, Any]] = {}
        method_ids_by_env: Dict[str, List[int]] = {}
        for meth in methods:
            spec = meth.env
            row = by_env.setdefault(
                spec,
                {
                    "spec": spec,
                    "methods": [],
                    "backend": None,
                    "has_packages": False,
                    "last_run_at": None,
                    "run_count": 0,
                },
            )
            mod_name = modules_by_id.get(meth.module_id, "?")
            row["methods"].append(f"{mod_name}.{meth.name}")
            method_ids_by_env.setdefault(spec, []).append(meth.id)

        # Aggregate Run stats per env spec.
        for spec, method_ids in method_ids_by_env.items():
            runs = session.exec(
                select(Run).where(Run.method_id.in_(method_ids))
            ).all()
            row = by_env[spec]
            row["run_count"] = len(runs)
            started = [r.started_at for r in runs if r.started_at is not None]
            if started:
                row["last_run_at"] = max(started).isoformat()

        # Resolve backend + package-capture state from the env manifest.
        for spec, row in by_env.items():
            record, backend = _env_record_for_spec(spec, project_root)
            row["backend"] = backend
            row["has_packages"] = bool(record and record.source_fingerprint)

        # Sorted: most-recently-run first, then alphabetical for ties.
        envs = sorted(
            by_env.values(),
            key=lambda r: (r["last_run_at"] is None, -(len(r["methods"])), r["spec"]),
        )
    return {"envs": envs}


@app.get("/api/registry/envs/{spec:path}/packages")
def get_registry_env_packages(spec: str):
    """Installed-package list for a registered pixi/conda env.

    Resolves *spec* to its :class:`wfc.envs.EnvRecord`, reads the captured
    ``source_fingerprint`` blob from the DVC cache, and parses it into a
    sorted, de-duplicated, source-tagged package list via
    :func:`wfc.env_packages.parse_packages`.

    Honest empty state: a byo env, an env that never staged source content,
    or an unmatched spec returns ``captured: false`` with ``packages: []`` —
    never a fabricated list.

    Response::

        {"spec": "container:demo",
         "backend": "pixi" | "conda" | "byo" | null,
         "captured": true,
         "packages": [{"name": ..., "version": ..., "source": "pixi"}, ...]}
    """
    from ..env_packages import parse_packages

    project_root = _project_root()
    record, backend = _env_record_for_spec(spec, project_root)

    if record is None or not record.source_fingerprint:
        return {"spec": spec, "backend": backend, "captured": False, "packages": []}

    blob = _read_env_blob_text(record.source_fingerprint, project_root)
    packages = parse_packages(blob, backend)
    return {"spec": spec, "backend": backend, "captured": True, "packages": packages}


class RegisterEnvRequest(BaseModel):
    """Body for ``POST /api/envs``. See :func:`wfc.envs.register`.

    No ``push`` field — ADR-019's 2026-05-17 amendment defers
    registry push to v1.x. The canvas surface mirrors the CLI flags.
    """

    name: str
    backend: str
    source: Dict[str, Any] = {}
    base_image: Optional[str] = None
    force: bool = False


@app.post("/api/envs")
def post_envs(req: RegisterEnvRequest):
    """Register a container env via :func:`wfc.envs.register`.

    Body shape::

        {"name": "image-io",
         "backend": "pixi" | "conda" | "byo",
         "source": {...},                  # per-backend payload
         "base_image": "..." | null,       # optional, not valid for byo
         "force": false}

    Returns the persisted :class:`EnvRecord` as JSON (the manifest record
    dict plus a ``name`` key so the caller doesn't have to track it).
    """
    from .. import envs as envs_mod

    project_root = _project_root()
    try:
        record = envs_mod.register(
            name=req.name,
            backend=req.backend,
            source=req.source,
            base_image=req.base_image,
            force=req.force,
            project_dir=project_root,
        )
    except FileExistsError as exc:
        # 409 Conflict: env exists and force=False.
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        # 400: no .wfc/ directory at project root.
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        # 400: bad input (unknown backend, missing image, invalid ref).
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        # docker subprocess failure (build / pull / inspect). When the daemon
        # is simply down, reframe into the friendly kind-tagged payload (D-6)
        # so the canvas shows a clean readiness card instead of a raw 500.
        from ..preflight import check_docker
        docker_check = check_docker()
        if docker_check.status == "fail":
            raise HTTPException(
                status_code=409, detail=_readiness_payload(docker_check)
            )
        # Genuine build/pull failure with a live daemon — keep the raw 500.
        raise HTTPException(status_code=500, detail=str(exc))

    payload = record.to_dict()
    payload["name"] = req.name
    return payload


@app.get("/api/registry/envs/blob/{md5}")
def get_registry_env_blob(md5: str):
    """Read an env-content blob from the DVC content-addressed cache.

    Returns the raw blob as ``text/plain`` so the frontend can render it
    directly in a code panel. Shares its read path (and path-traversal
    guard) with ``GET .../packages`` via :func:`_read_env_blob_text`.
    """
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(_read_env_blob_text(md5, _project_root()))


def _default_register_method(*args, **kwargs):
    from ..register import register_method
    return register_method(*args, **kwargs)


_register_method_fn = _default_register_method


class MethodRegisterRequest(BaseModel):
    directory: str
    module: str
    method_name: Optional[str] = None


def _method_register_pre_checks(req: MethodRegisterRequest) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    # --- directory ---
    directory = (req.directory or "").strip()
    if not directory:
        checks.append({
            "status": "fail",
            "label": "directory provided",
            "detail": "required — pick a folder containing method.yaml",
        })
    else:
        method_dir = (_project_root() / directory).resolve()
        if not method_dir.exists():
            checks.append({
                "status": "fail",
                "label": "directory exists",
                "detail": f"not found: {directory}",
            })
        elif not method_dir.is_dir():
            checks.append({
                "status": "fail",
                "label": "directory is a folder",
                "detail": f"{directory} is a file, not a directory",
            })
        else:
            checks.append({"status": "ok", "label": "directory exists"})
            yaml_path = method_dir / "method.yaml"
            if yaml_path.is_file():
                checks.append({"status": "ok", "label": "method.yaml present"})
            else:
                checks.append({
                    "status": "fail",
                    "label": "method.yaml present",
                    "detail": f"missing method.yaml in {directory}",
                })

    # --- module ---
    module = (req.module or "").strip()
    if not module:
        checks.append({
            "status": "fail",
            "label": "module selected",
            "detail": "required — choose the module this method belongs to",
        })
    else:
        with get_session() as session:
            mod = session.exec(select(Module).where(Module.name == module)).first()
            if mod is None:
                checks.append({
                    "status": "fail",
                    "label": "module registered",
                    "detail": f"'{module}' is not a registered module",
                })
            else:
                checks.append({"status": "ok", "label": "module registered"})
    return checks


@app.post("/api/registry/methods")
def register_method_endpoint(req: MethodRegisterRequest, dryRun: bool = False):
    """Wrap ``wfc.register.register_method`` with optional dry-run preflight."""
    checks = _method_register_pre_checks(req)
    ok = all(c["status"] != "fail" for c in checks)

    if dryRun or not ok:
        return {"ok": ok, "preChecks": checks}

    try:
        _register_method_fn(
            method_dir=Path(req.directory),
            module_name=req.module,
            method_name=req.method_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "preChecks": checks, "method": {"name": req.method_name or Path(req.directory).name}}


@app.get("/api/fs/browse")
def fs_browse(path: str = ""):
    """List directory contents under the project root.

    ``path`` is relative to the project root. Defaults to the root itself.
    Rejects any path that resolves outside the project root. Safe for a
    localhost single-user dev tool: the server is already scoped to the
    project the user chose to launch it against.
    """
    project_root = _project_root().resolve()
    target = (project_root / path).resolve()
    try:
        target.relative_to(project_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"path escapes project root (path traversal): {path}",
        )
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {path}")

    entries: List[Dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            entries.append({"name": entry.name, "kind": "dir"})
        elif entry.is_file():
            try:
                size = entry.stat().st_size
            except OSError:
                size = None
            entries.append({"name": entry.name, "kind": "file", "size": size})
    return {"path": path, "entries": entries}


@app.get("/api/project/status")
def get_project_status():
    """Onboarding-shaped project status.

    Subset of ENDPOINTS.md §1.1 — reports whether a project is loaded,
    its root, and quick counts. Always 200, never 404.
    """
    from ..models import Sample

    if _wfc_provider is None:
        return {"initialized": False, "path": None}

    with get_session() as session:
        module_count = len(session.exec(select(Module)).all())
        methods_total = sum(
            len(mod.methods) for mod in session.exec(select(Module)).all()
        )
        sample_count = len(session.exec(select(Sample)).all())

    return {
        "initialized": True,
        "path": str(_wfc_provider.project_root),
        "modules": module_count,
        "methods": methods_total,
        "samples": sample_count,
        "dbPath": str(Path(_wfc_provider.project_root) / ".wfc" / "wfc.db"),
        "tomlPath": str(Path(_wfc_provider.project_root) / ".wfc" / "wf-canvas.toml"),
    }


class SampleRegisterRequest(BaseModel):
    name: str
    source: str
    registration_mode: Optional[str] = "copy"


@app.post("/api/registry/samples")
def register_sample_endpoint(req: SampleRegisterRequest):
    """Wrap ``wfc.cli.register_sample`` behind an HTTP endpoint."""
    from ..provenance import DvcNotConfiguredError

    try:
        _register_sample_fn(
            name=req.name,
            source_path=Path(req.source),
            registration_mode=req.registration_mode or "copy",
        )
    except DvcNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "sample": {"name": req.name}}


# =============================================================================
# Canvas SPA
# =============================================================================


@app.get("/")
def root():
    """Serve the main SPA page."""
    return FileResponse(_STATIC_DIR / "index.html")


# =============================================================================
# Workflow builder endpoints
# =============================================================================


@app.post("/api/workflow/validate")
def validate_workflow(pipeline: PipelineInput):
    """Validate a pipeline graph against registered methods in the DB."""
    errors: List[str] = []
    warnings: List[str] = []

    if not pipeline.nodes:
        errors.append("Pipeline has no nodes")
        return {"valid": False, "errors": errors, "warnings": warnings}

    with get_session() as session:
        modules_db = {m.name: m for m in session.exec(select(Module)).all()}
        # Eagerly load methods and contracts within the session
        method_lookup: Dict[str, Any] = {}
        for mod in modules_db.values():
            for meth in mod.methods:
                key = f"{mod.name}.{meth.name}"
                method_lookup[key] = {
                    "contract": meth.contract,
                    "module": mod.name,
                    "method": meth.name,
                }

    for node in pipeline.nodes:
        # System nodes (input_selector, run_reference) are not registered
        # methods — skip method validation for them.
        node_type = getattr(node, "type", None) or "method"
        if node_type in ("input_selector", "run_reference"):
            continue
        mod_name = node.module or ""
        key = f"{mod_name}.{node.method}"
        info = method_lookup.get(key)
        if info is None:
            errors.append(f"Unknown method: {mod_name}.{node.method!r}")
            continue
        mc = info["contract"]
        if mc and mc.input_slots:
            for slot_name, slot_spec in mc.input_slots.items():
                if slot_spec.get("required", True):
                    connected = any(
                        l.target == node.id and l.targetHandle == slot_name
                        for l in pipeline.links
                    )
                    if not connected:
                        warnings.append(
                            f"{node.method}: required input slot {slot_name!r} is not connected"
                        )

    # Input-slot uniqueness: a single input slot on a method node must not
    # have more than one incoming edge. Multi-edge-per-slot has never worked
    # end-to-end (the engine hard-wires the sample axis per upstream), so
    # reject it at validate time instead of letting Snakemake fail silently.
    slot_edges: Dict[tuple, List[str]] = {}
    for lnk in pipeline.links:
        key = (str(lnk.target), str(lnk.targetHandle or ""))
        slot_edges.setdefault(key, []).append(str(lnk.source))
    for (tgt, slot), sources in slot_edges.items():
        if len(sources) > 1:
            slot_label = slot or "(default)"
            errors.append(
                f"Input slot '{slot_label}' on node '{tgt}' has "
                f"{len(sources)} incoming edges; only one edge per slot is "
                f"supported. Sources: {', '.join(sources)}."
            )

    # Fan-in shape checks (single-selector fan-in cycle): reject shapes the
    # engine does not yet support so the user sees a clear message instead
    # of a cryptic Snakemake failure.
    #
    # 1. Any input_selector with fan_mode="in" must have at least one sample.
    # 2. A method node whose upstreams include a fan-in selector must have
    #    exactly one upstream (the selector) -- multi-selector or
    #    mixed-kind upstreams on a fan-in consumer are out of scope.
    fan_in_selectors: Dict[str, "PipelineNode"] = {}
    for node in pipeline.nodes:
        node_type = getattr(node, "type", None) or "method"
        if node_type == "input_selector" and (node.fan_mode or "out") == "in":
            fan_in_selectors[str(node.id)] = node
            if not (node.samples or []):
                errors.append(
                    f"Input selector '{node.id}' has fan_mode='in' but no "
                    f"samples selected. Fan-in requires at least one sample."
                )

    if fan_in_selectors:
        # Group links by target for multi-upstream detection.
        upstreams_by_target: Dict[str, List[str]] = {}
        for lnk in pipeline.links:
            upstreams_by_target.setdefault(str(lnk.target), []).append(str(lnk.source))

        for node in pipeline.nodes:
            node_type = getattr(node, "type", None) or "method"
            if node_type != "method":
                continue
            tgt_id = str(node.id)
            ups = upstreams_by_target.get(tgt_id, [])
            fan_in_ups = [u for u in ups if u in fan_in_selectors]
            if fan_in_ups and len(ups) > 1:
                selector_id = fan_in_ups[0]
                errors.append(
                    f"Fan-in mode on '{selector_id}' is only supported when "
                    f"it is the sole upstream of its consumer. Consumer "
                    f"'{tgt_id}' has {len(ups)} upstreams."
                )

    # Structural check: method nodes cannot be DAG roots (no incoming edges).
    # Only system nodes (input_selector, run_reference) are valid roots.
    incoming = {str(l.target) for l in pipeline.links}
    for node in pipeline.nodes:
        node_type = getattr(node, "type", None) or "method"
        if node_type in ("input_selector", "run_reference"):
            continue  # system nodes are valid roots
        node_id = str(node.id)
        if node_id not in incoming:
            errors.append(
                f"Method node '{node_id}' cannot be a pipeline root. "
                f"Use an input_selector or run_reference system node as the root "
                f"and connect it to this method node."
            )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _classify_pipeline_error(exc: Exception) -> Dict[str, Any]:
    """Map a pre-run exception to a structured payload for the canvas UI.

    The UI shows the raw message by default (it's already human-readable —
    ``pixi_lock_section`` / ``DirtyRepositoryError`` / ``capture_env_content``
    build full sentences).  ``kind`` lets the frontend pick an icon/color or
    inline action (e.g. a "Commit changes" button for dirty_repo).  ``hint``
    is an optional follow-up sentence separated from the main message.
    """
    from ..version import DirtyRepositoryError

    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, DirtyRepositoryError):
        return {
            "kind": "dirty_repo",
            "message": message,
            "hint": "Commit or stash your changes, then click Run again.",
        }
    # pixi env-spec parse error from capture_env_content — malformed env: field.
    if isinstance(exc, ValueError) and "Malformed pixi env spec" in message:
        return {
            "kind": "env_spec",
            "message": message,
            "hint": "Fix the method's env: field (e.g. method.yaml).",
        }
    # Lock missing / project dir missing — pixi install hasn't been run.
    if isinstance(exc, FileNotFoundError) and "pixi.lock" in message:
        return {"kind": "env_lock_missing", "message": message}
    # Env name not in lock (3-segment spec pointed at a non-existent env).
    if isinstance(exc, KeyError) and "no env named" in message:
        return {"kind": "env_name_missing", "message": message}
    # Module/method lookup miss from pre_run (wfc/cli.py).
    if isinstance(exc, ValueError) and "not found" in message.lower():
        return {"kind": "not_found", "message": message}
    return {"kind": "unknown", "message": message}


def _readiness_payload(check) -> Dict[str, Any]:
    """Build a kind-tagged payload from a not-ready preflight CheckResult.

    Maps a failing ``check_docker`` / ``check_git`` result to the same
    ``{kind, message, hint}`` shape the frontend already renders for pre-run
    errors (cycle decision D-6). The new kinds are ``not_runnable_docker`` and
    ``not_runnable_git``.

    Args:
        check: A :class:`wfc.preflight.CheckResult` with a non-``ok`` status.

    Returns:
        ``{"kind": ..., "message": ..., "hint": ...}``.
    """
    return {
        "kind": f"not_runnable_{check.name}",
        "message": check.message,
        "hint": check.fix_hint,
    }


@app.post("/api/workflow/run")
def run_workflow(pipeline: PipelineInput):
    """Submit a pipeline for execution via Snakemake.

    Enriches the PipelineJSON with script paths and slot_outputs from the DB,
    writes it to a temp file, and spawns a background thread running
    ``run_pipeline()``. Returns the pipeline_id as the job_id immediately.

    Rejects empty pipelines with HTTP 400.  Before spawning the run thread it
    pre-flights Docker and git (cycle decision D-6, 3-lite): a not-ready
    environment is rejected with HTTP 409 carrying a kind-tagged
    ``{kind, message, hint}`` payload (the same shape the frontend renders for
    pre-run errors), so no orphan run is started.  Multiple pipelines may run
    concurrently — each gets its own pipeline_id-scoped workspace.
    """
    if not pipeline.nodes:
        raise HTTPException(status_code=400, detail="Pipeline has no nodes")

    # Resolve the canvas project_root BEFORE the readiness gate so git readiness
    # is probed against the canvas project, not the server process cwd.
    from ..database import project_root as _resolve_project_root
    env_root = os.environ.get("WFC_CANVAS_PROJECT_ROOT")
    if env_root:
        project_root = env_root
    else:
        try:
            project_root = str(_resolve_project_root())
        except RuntimeError:
            project_root = str(Path.cwd())

    # 3-lite run-readiness gate (D-6): reject at submission BEFORE spawning the
    # run thread when Docker is down or git is not ready. Reuses wfc/preflight.py
    # so the canvas gate and `wfc doctor` share one readiness definition.
    # git readiness is scoped to the resolved project_root (NOT the process cwd).
    from ..preflight import check_docker, check_git
    docker_check = check_docker()
    if docker_check.status == "fail":
        raise HTTPException(status_code=409, detail=_readiness_payload(docker_check))
    git_check = check_git(project_root)
    if git_check.status == "fail":
        raise HTTPException(status_code=409, detail=_readiness_payload(git_check))

    # Track 2: substitute pipeline variables BEFORE _enrich_pipeline so the
    # executor (snakemake_gen) sees only literal values. Build a dict shape
    # matching what resolve_variables expects.
    from .wfc_provider import resolve_variables, UnknownVariableError
    pre_sub_dict: Dict[str, Any] = {
        "name": pipeline.name,
        "nodes": [n.model_dump(exclude_none=True) for n in pipeline.nodes],
        "links": [l.model_dump(exclude_none=True) for l in pipeline.links],
        "samples": pipeline.samples,
    }
    if pipeline.param_sets:
        pre_sub_dict["param_sets"] = pipeline.param_sets
    if pipeline.explicit_combos:
        pre_sub_dict["explicit_combos"] = pipeline.explicit_combos
    if pipeline.variables:
        pre_sub_dict["variables"] = pipeline.variables

    try:
        substituted = resolve_variables(pre_sub_dict)
    except UnknownVariableError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown pipeline variable: '{exc.name}'",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Patch the validated pipeline's node params and param_sets in place
    # so _enrich_pipeline sees post-substitution literals. We do this on
    # the model objects (Pydantic v2 allows assignment) to keep the
    # downstream contract unchanged.
    sub_nodes = substituted.get("nodes", [])
    for orig, sub in zip(pipeline.nodes, sub_nodes):
        if isinstance(sub, dict) and "params" in sub:
            orig.params = sub["params"]
    if "param_sets" in substituted:
        pipeline.param_sets = substituted["param_sets"]

    # Enrich nodes with script paths and slot_outputs from DB
    pipeline_json = _enrich_pipeline(pipeline)
    pipeline_id = str(uuid.uuid4())

    pipeline_dir = Path(project_root) / ".runs" / "pipelines" / pipeline_id
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    pipeline_path = pipeline_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps(pipeline_json, indent=2), encoding="utf-8")

    # Track 2: persist the pre-substitution form so History "Open in canvas"
    # can rehydrate the Pipeline Variables panel and per-row binding chips.
    # Writes the user-submitted shape (with `variables` block + `{$var}`
    # refs intact). Falls back to pipeline.json for legacy runs that lack
    # this sidecar.
    editable_path = pipeline_dir / "pipeline.editable.json"
    try:
        editable_path.write_text(
            json.dumps(pre_sub_dict, indent=2, default=str), encoding="utf-8"
        )
    except OSError:
        pass  # Sidecar is best-effort; submission still proceeds.

    # Build step mapping: node_id -> method_name (for frontend status tracking)
    step_map = {n.id: n.method for n in pipeline.nodes}

    def _on_process_started(proc):
        # Hot-write the live Popen handle into the active-jobs registry so
        # the cancel endpoint can locate the subprocess and terminate its
        # process tree (Phase D Pass 2: real backend cancel).
        try:
            _active_jobs[pipeline_id]["proc"] = proc
        except Exception:
            pass  # Race with cancel-after-completion; ignore.

    def _is_cancelled():
        return bool(_active_jobs.get(pipeline_id, {}).get("cancel_requested"))

    def _run_in_background():
        """Execute the pipeline in a background thread."""
        try:
            _rp = run_pipeline_fn()
            _rp(
                pipeline_path=str(pipeline_path),
                project_root=project_root,
                pipeline_id=pipeline_id,
                capture_output=True,
                keep_going=pipeline.keep_going,
                process_registry=_on_process_started,
                is_cancelled=_is_cancelled,
            )
        except Exception as exc:
            # If the cancel endpoint already requested cancellation, suppress
            # the orphan-failure flip — cancel_pipeline owns the row state.
            if not _active_jobs.get(pipeline_id, {}).get("cancel_requested"):
                try:
                    _fp = fail_pipeline_fn()
                    _fp(pipeline_id)
                except Exception:
                    pass  # Best-effort cleanup
            _active_jobs[pipeline_id]["error"] = _classify_pipeline_error(exc)

    thread = threading.Thread(target=_run_in_background, daemon=True)
    _active_jobs[pipeline_id] = {
        "thread": thread,
        "pipeline_id": pipeline_id,
        "step_map": step_map,
        "log_dir": str(pipeline_dir),
        "error": None,
        "proc": None,
        "cancel_requested": False,
    }
    thread.start()

    return {
        "status": "submitted",
        "job_id": pipeline_id,
        "message": f"Pipeline '{pipeline.name or 'unnamed'}' submitted ({len(pipeline.nodes)} nodes)",
        "step_map": step_map,
    }


@app.post("/api/workflow/cancel/{job_id}")
def cancel_workflow(job_id: str):
    """Cancel an in-flight pipeline (ADR-015 Phase D Pass 2).

    Terminates the live Snakemake subprocess (and its descendants) and
    flips any ``running`` rows for this pipeline to ``cancelled`` with
    ``error_message="Cancelled by user"``.  Idempotent: cancelling a
    pipeline whose process has already exited returns 200 with a no-op
    indication.  Returns 404 for unknown job_ids.
    """
    if job_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    job_info = _active_jobs[job_id]
    proc = job_info.get("proc")

    # Mark cancellation requested BEFORE killing the subprocess so the
    # background thread's exception handler suppresses its orphan-failure
    # flip (which would otherwise race cancel_pipeline and overwrite
    # ``cancelled`` rows with ``failed``).
    job_info["cancel_requested"] = True

    # Helper to call cancel_pipeline (lazy-imported to mirror run_pipeline_fn).
    def _cancel_rows():
        try:
            from ..cli import cancel_pipeline as _cp
            _cp(job_id)
        except Exception:
            pass  # Best-effort.

    # Already terminal or never-launched -> idempotent no-op.
    if proc is None or proc.poll() is not None:
        _cancel_rows()
        return {"status": "cancelled", "job_id": job_id, "noop": True}

    # Live subprocess -> terminate the process tree.
    try:
        import psutil as _psutil
        try:
            parent = _psutil.Process(proc.pid)
            children = parent.children(recursive=True)
        except _psutil.NoSuchProcess:
            children = []
            parent = None

        # Polite terminate first, then SIGKILL stragglers.
        for p in children:
            try:
                p.terminate()
            except _psutil.NoSuchProcess:
                pass
        if parent is not None:
            try:
                parent.terminate()
            except _psutil.NoSuchProcess:
                pass

        gone, alive = _psutil.wait_procs(
            ([parent] if parent is not None else []) + list(children),
            timeout=2.0,
        )
        for p in alive:
            try:
                p.kill()
            except _psutil.NoSuchProcess:
                pass
    except Exception:
        # Belt-and-braces: even if psutil failed, fall back to
        # subprocess-level kill so the row-flip below is meaningful.
        try:
            proc.kill()
        except Exception:
            pass

    _cancel_rows()
    return {"status": "cancelled", "job_id": job_id, "noop": False}


@app.get("/api/workflow/status/{job_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(job_id: str):
    """Return per-step and overall status for a running or completed pipeline.

    Queries the ``runs`` table by ``pipeline_id`` and derives overall status.
    Also returns whether the background thread is still alive and any captured
    log output.

    Args:
        job_id: The pipeline_id returned by the run endpoint.

    Returns:
        JSON with ``job_id``, ``overall_status``, ``steps``, ``thread_alive``,
        ``log``, and ``error`` fields.
    """
    if job_id not in _active_jobs:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    job_info = _active_jobs[job_id]
    thread = job_info.get("thread")
    thread_alive = thread.is_alive() if thread else False
    log_dir = job_info.get("log_dir")
    job_error = job_info.get("error")

    # Tally per-sample statuses per method so the canvas can show a
    # "mixed" aggregate when fan-out samples diverge (one failed, rest
    # succeeded, etc.). A flat last-write-wins dict would hide this.
    from collections import defaultdict as _defaultdict
    method_tallies: Dict[str, Dict[str, int]] = _defaultdict(
        lambda: {"running": 0, "completed": 0, "failed": 0}
    )
    # Also collect run_ids per method so the canvas can point the Output tab
    # at a specific Run row for streaming. Ordered newest-first by started_at
    # so run_ids[0] is the most recent attempt.
    method_run_ids: Dict[str, List[Any]] = _defaultdict(list)
    # Most-recent failed/cancelled run per method so the canvas can show a
    # one-liner on the node without the user having to open the log stream.
    # run_message tail is capped so the Inspector panel stays compact.
    _ERROR_MESSAGE_CAP = 600
    method_error: Dict[str, Dict[str, Any]] = {}
    # Cache-hit detection (ADR-015 Phase D / ADR-016 Bug 4+5).  A method
    # is rendered as a cache hit when its most-recent Run row carries
    # ``cache_source_run_id`` (the audit row written by the engine when
    # cache reuse skipped real execution).  Captured per-method so the
    # frontend can short-circuit to the `cached` substate without
    # spawning a streaming actor that would otherwise strand on
    # `connecting`.
    method_cache_hit: Dict[str, Dict[str, Any]] = {}
    # ADR-018: per-method push aggregates. Keyed by method name; each entry
    # holds counts of pending/in_flight/pushed/failed across all RunOutput
    # rows belonging to runs in this pipeline. ``has_any`` distinguishes
    # "no outputs yet, no remote configured -> deferred" from "all rows are
    # in the deferred terminal" (also deferred for display purposes).
    method_push_counts: Dict[str, Dict[str, int]] = {}
    with get_session() as session:
        runs = session.exec(
            select(Run)
            .where(Run.pipeline_id == job_id)
            .order_by(Run.started_at.desc())
        ).all()
        # Build run_id -> method-name map for the push aggregate join below.
        run_id_to_method: Dict[int, str] = {}
        for r in runs:
            if r.id is None:
                continue
            m = r.method
            mkey = m.name if m else f"method_{r.method_id}"
            run_id_to_method[r.id] = mkey
        if run_id_to_method:
            outputs = session.exec(
                select(RunOutput).where(
                    RunOutput.run_id.in_(list(run_id_to_method.keys()))  # type: ignore[union-attr]
                )
            ).all()
            for ro in outputs:
                mkey = run_id_to_method.get(ro.run_id)
                if not mkey:
                    continue
                bucket = method_push_counts.setdefault(
                    mkey,
                    {"pending": 0, "in_flight": 0, "pushed": 0, "failed": 0, "deferred": 0},
                )
                status = ro.push_status or "deferred"
                if status not in bucket:
                    bucket[status] = 0
                bucket[status] += 1
        for run in runs:
            method = run.method
            key = method.name if method else f"method_{run.method_id}"
            status = run.status or "unknown"
            if status not in method_tallies[key]:
                method_tallies[key][status] = 0
            method_tallies[key][status] += 1
            method_run_ids[key].append(str(run.id))
            # Most-recent first: capture cache-hit info from the first
            # row we see for each method.  ``cache_source_run_id`` is
            # the audit-row signal that this run reused another run's
            # outputs.
            if key not in method_cache_hit and run.cache_source_run_id is not None:
                method_cache_hit[key] = {
                    "original_run_id": str(run.cache_source_run_id),
                    "cache_key": run.cache_key or "",
                }
            # Record the newest failure per method.  Iteration is
            # started_at-DESC so the first hit is the most recent.
            if key not in method_error and status in ("failed", "cancelled"):
                raw_msg = (run.error_message or "").strip()
                if raw_msg:
                    msg = raw_msg
                    if len(msg) > _ERROR_MESSAGE_CAP:
                        msg = msg[:_ERROR_MESSAGE_CAP].rstrip() + "…"
                    method_error[key] = {
                        "message": msg,
                        "run_id": str(run.id),
                        "sample": run.sample,
                        "status": status,
                    }

    def _aggregate(tally: Dict[str, int]) -> str:
        """Collapse a per-sample tally into a single display state."""
        if tally.get("running", 0) > 0:
            return "running"
        completed = tally.get("completed", 0)
        failed = tally.get("failed", 0)
        cancelled = tally.get("cancelled", 0)
        if completed > 0 and failed > 0:
            return "mixed"
        if failed > 0:
            return "failed"
        if completed > 0:
            return "completed"
        # No running, no failures, no completions — if any rows exist
        # they're cancelled. Without this branch the node collapses to
        # "unknown" and overall_status falls through to the "running"
        # fallback, leaving a fully-cancelled pipeline reporting
        # in-flight forever.
        if cancelled > 0:
            return "cancelled"
        return "unknown"

    method_status: Dict[str, str] = {
        name: _aggregate(t) for name, t in method_tallies.items()
    }

    def _push_aggregate(bucket: Dict[str, int]) -> str:
        """Collapse a push-status bucket into a single display state.

        ADR-018: any failed row dominates; otherwise any in-flight is
        ``in_flight``; otherwise any pending is ``pending``; otherwise
        any pushed (and nothing else) is ``pushed``; otherwise ``deferred``.
        Mirrors the same dominance ordering as the run-status aggregate.
        """
        if bucket.get("failed", 0) > 0:
            return "failed"
        if bucket.get("in_flight", 0) > 0:
            return "in_flight"
        if bucket.get("pending", 0) > 0:
            return "pending"
        if bucket.get("pushed", 0) > 0 and (
            bucket.get("pending", 0) + bucket.get("in_flight", 0) + bucket.get("failed", 0)
        ) == 0:
            return "pushed"
        return "deferred"

    # Build node_states keyed by canvas node ID using the step_map
    # (step_map is {node_id: method_name}, stored when the pipeline was submitted).
    # This handles cache hits (where the reused run has a different pipeline_id)
    # and multiple nodes using the same method.
    step_map: Dict[str, str] = job_info.get("step_map", {})
    node_states: Dict[str, Dict[str, Any]] = {}
    for node_id, method_name in step_map.items():
        if not method_name:
            continue  # skip system nodes
        status = method_status.get(method_name)
        if status:
            tally = method_tallies[method_name]
            entry: Dict[str, Any] = {
                "status": status,
                "tally": dict(tally),
                # run_ids newest-first so consumers can treat run_ids[0] as
                # "most recent attempt". Empty list when no Run rows exist
                # yet (pending / cache-hit-only nodes).
                "run_ids": list(method_run_ids.get(method_name, [])),
            }
            # Per-node error surface (ADR-004 error persistence).  Attached for
            # ``failed`` and ``mixed`` states so the Inspector can show the
            # newest failure without the user having to open the log stream;
            # also included for the rare ``completed`` state where an earlier
            # attempt carried an error_message (edge case, near-zero cost).
            err = method_error.get(method_name)
            if err and status in ("failed", "mixed"):
                entry["error"] = err["message"]
                entry["error_run_id"] = err["run_id"]
                if err.get("sample"):
                    entry["error_sample"] = err["sample"]
            # Cache-hit surface (ADR-015 Phase D).  Attached whenever the
            # method's most-recent Run row carries ``cache_source_run_id``,
            # regardless of the aggregated ``status`` above — typically
            # ``completed`` for a clean cache hit.  Frontend reads these
            # via ``runStatusToNodeState`` and emits CACHE_HIT into the
            # per-node state machine.
            ch = method_cache_hit.get(method_name)
            if ch is not None:
                entry["cache_hit"] = True
                entry["original_run_id"] = ch["original_run_id"]
                if ch.get("cache_key"):
                    entry["cache_key"] = ch["cache_key"]
            # ADR-018: per-method push aggregates. Always present (US-5).
            push_bucket = method_push_counts.get(method_name, {})
            entry["push_state"] = _push_aggregate(push_bucket)
            entry["push_pending_count"] = (
                push_bucket.get("pending", 0) + push_bucket.get("in_flight", 0)
            )
            entry["push_failed_count"] = push_bucket.get("failed", 0)
            node_states[node_id] = entry

    # Method nodes not yet in the DB haven't started — mark them pending
    # so the overall status doesn't flip to "completed" prematurely.
    for node_id, method_name in step_map.items():
        if method_name and node_id not in node_states:
            # ADR-018: push fields must always be present, even before any
            # Run row exists for this node. Defaults to deferred + zeros.
            node_states[node_id] = {
                "status": "pending",
                "push_state": "deferred",
                "push_pending_count": 0,
                "push_failed_count": 0,
            }

    # If thread is dead and we still have pending nodes with no error,
    # they were likely cache hits — mark them completed.
    if not thread_alive and not job_error:
        for node_id, ns in node_states.items():
            if ns["status"] == "pending":
                # Preserve push-state fields (ADR-018) when promoting to
                # completed -- they were defaulted on the pending entry.
                node_states[node_id] = {
                    "status": "completed",
                    "push_state": ns.get("push_state", "deferred"),
                    "push_pending_count": ns.get("push_pending_count", 0),
                    "push_failed_count": ns.get("push_failed_count", 0),
                }

    # Derive overall status from node states. "mixed" is a terminal state
    # for a node — its per-sample runs finished but with partial failure.
    # At the pipeline level that maps to `completed_with_failures` when
    # nothing is still running; during execution it stays `running`.
    statuses = [ns["status"] for ns in node_states.values()] if node_states else []
    if not statuses:
        overall = "pending"
    elif any(s == "running" for s in statuses):
        overall = "running"
    elif any(s == "failed" for s in statuses):
        # Any fully-failed node fails the pipeline (same as before).
        # Failure dominates cancellation — if anything genuinely errored
        # before the cancel landed, surface that.
        overall = "failed"
    elif any(s == "cancelled" for s in statuses):
        # Nothing running, nothing failed, at least one cancelled node.
        # The polling actor reads this as a terminal status and stops
        # the loop; without this branch the chain falls through to the
        # "running" fallback below.
        overall = "cancelled"
    elif any(s == "mixed" for s in statuses):
        # Nothing running, nothing fully failed, but some node saw partial
        # failures across its fan-out samples. Keep-going enabled this.
        overall = "completed_with_failures"
    elif all(s == "completed" for s in statuses):
        overall = "completed"
    else:
        overall = "running"

    # If thread is dead and no runs exist or overall is pending,
    # and there was an error, mark as failed
    if not thread_alive and job_error and overall == "pending":
        overall = "failed"

    # Read log files if available
    log_content = ""
    if log_dir:
        log_path = Path(log_dir)
        stdout_log = log_path / "stdout.log"
        stderr_log = log_path / "stderr.log"
        if stdout_log.exists():
            log_content += stdout_log.read_text(encoding="utf-8", errors="replace")
        if stderr_log.exists():
            stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
            if stderr_text:
                log_content += "\n--- STDERR ---\n" + stderr_text

    return {
        "job_id": job_id,
        "overall_status": overall,
        "steps": method_status,
        "node_states": node_states,
        "thread_alive": thread_alive,
        "log": log_content,
        "error": job_error,
    }


@app.post("/api/workflow/save")
def save_workflow(workflow: Dict[str, Any]):
    """Save a workflow definition.

    Note: ``Workflow`` was previously declared as the pydantic class for
    this body, but the symbol was never defined in this module — this
    endpoint has been a runtime no-op behind that broken reference.  The
    untyped ``Dict[str, Any]`` keeps the endpoint reachable and lets
    FastAPI's OpenAPI generation succeed, which in turn unblocks the
    ADR-015 Phase D codegen pipeline.  Callers should treat the body as
    free-form JSON until/unless this endpoint is properly typed.
    """
    name = workflow.get("name", "")
    return {"status": "saved", "name": name}


# =============================================================================
# WFC Provider -- configure / reload
# =============================================================================


@app.post("/api/wfc/load")
def load_wfc_data(config: WfcConfig):
    """Load (or reload) the wfc provider from a project path.

    Also updates DATABASE_URL and resets the SQLAlchemy engine so that
    GET /api/modules queries this project's DB, not the server's launch-cwd DB.
    """
    global _wfc_provider
    try:
        _wfc_provider = WfcProvider(config.project_root)
        _wfc_provider.load()
        # Point the SQLAlchemy session at the loaded project's DB
        _switch_db(Path(config.project_root))
        return {
            "status": "loaded",
            "path": config.project_root,
            "modules": len(_wfc_provider.get_modules()),
            "methods": len(_wfc_provider.get_methods()),
            "runs": len(_wfc_provider.get_all_runs()),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/wfc/refresh")
def refresh_wfc_data():
    """Reload wfc data from the current project root."""
    prov = _require_provider()
    try:
        prov.load()
        return {
            "status": "refreshed",
            "path": str(prov.project_root),
            "modules": len(prov.get_modules()),
            "runs": len(prov.get_all_runs()),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =============================================================================
# WFC History endpoints
# =============================================================================


@app.get("/api/wfc/status")
def get_wfc_status():
    if _wfc_provider is None:
        return {"loaded": False, "path": None}
    return {
        "loaded": True,
        "path": str(_wfc_provider.project_root),
        "modules": len(_wfc_provider.get_modules()),
        "runs": len(_wfc_provider.get_all_runs()),
    }


@app.get("/api/wfc/runs")
def get_wfc_runs():
    # Plain `def` (not `async def`) so FastAPI dispatches to a threadpool.
    # The provider method is sync (DB + filesystem I/O); under `async def`
    # it would block the event loop and queue every other request behind
    # it — felt most painfully by the history tab when the user clicks
    # between runs while a slow /artifacts call is in flight.
    return _require_provider().get_all_runs()


@app.get("/api/wfc/experiments")
def get_wfc_experiments():
    return _require_provider().get_experiments()


@app.get("/api/wfc/run/{run_id}")
def get_wfc_run(run_id: str):
    run = _require_provider().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


@app.get("/api/wfc/lineage/{run_id}")
def get_wfc_lineage(run_id: str):
    return _require_provider().get_lineage(run_id)


@app.get("/api/wfc/tree/{run_id}")
def get_wfc_run_tree(run_id: str):
    return _require_provider().get_run_tree(run_id)


@app.get("/api/wfc/modules")
def get_wfc_modules():
    return _require_provider().get_modules()



@app.get("/api/wfc/methods")
def get_wfc_methods():
    return _require_provider().get_methods()


@app.get("/api/wfc/samples")
def get_wfc_samples():
    """Return detailed info for all registered samples."""
    return _require_provider().get_samples_detail()


@app.get("/api/wfc/completed-runs")
def get_wfc_completed_runs():
    """Return completed runs with output slots for Run Reference nodes."""
    return _require_provider().get_completed_runs()


@app.get("/api/wfc/run/{run_id}/artifacts")
def list_wfc_artifacts(run_id: str):
    # Plain `def` — `list_artifacts` walks the run dir and stats every
    # descendant file to compute per-directory totals. On a big run this
    # takes long enough to freeze the history tab; under `async def` it
    # also blocked every other request until it finished.
    return _require_provider().list_artifacts(run_id)


@app.get("/api/wfc/run/{run_id}/cancelled-descendants")
def get_wfc_cancelled_descendants(run_id: str):
    """Return runs cancelled because ``run_id`` (or its subtree) failed."""
    return _require_provider().get_cancelled_descendants(run_id)


# ---------------------------------------------------------------------------
# Track 1: column_of_input output column resolution
# ---------------------------------------------------------------------------


@app.get("/api/contracts/{method_full}/output_columns")
def get_output_columns(method_full: str, slot: str, params: Optional[str] = None,
                       run_id: Optional[str] = None):
    """Resolve declared output columns for a method's slot.

    The canvas inspector calls this for params declared with
    ``column_of_input: <slot>`` to populate a dropdown of candidate column
    names. Reuses ``wfc/contracts.py::resolve_columns`` against the upstream
    method's contract.

    Args:
        method_full: ``module.method`` (e.g. ``data_preprocessing.regionprops``).
        slot: Output slot name to resolve columns for.
        params: JSON-encoded dict of upstream node's current canvas params
            (used by ``from_params`` expansion). Optional; missing/empty
            falls back to ``{}``.
        run_id: When the upstream is a ``run_reference`` node, pass the
            referenced run id; the endpoint then uses Run.params from the
            DB as the resolution context.

    Returns:
        ``{strict, from_params, patterns, all}`` where ``all`` is the
        union of resolved column names produced by ``resolve_columns``.
        ``patterns`` are returned literally (resolution requires a CSV;
        not done here per the no-introspection constraint).
    """
    import json as _json
    from ..contracts import resolve_columns

    parsed_params: Dict[str, Any] = {}
    if run_id:
        # Look up Run.params in the wfc DB for run_reference upstream.
        provider = _require_provider()
        from ..models import Run
        with get_session() as session:
            try:
                rid = int(run_id)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id}")
            run = session.get(Run, rid)
            if not run:
                raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
            parsed_params = run.params or {}
            method_full = f"{run.module}.{run.method}"
    elif params:
        try:
            parsed_params = _json.loads(params)
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}")

    if "." not in method_full:
        raise HTTPException(status_code=400, detail="method must be 'module.method'")
    module_name, method_name = method_full.split(".", 1)

    with get_session() as session:
        modules_db = {m.name: m for m in session.exec(select(Module)).all()}
        mod = modules_db.get(module_name)
        if not mod:
            raise HTTPException(status_code=404, detail=f"Module {module_name} not found")
        meth = next((m for m in mod.methods if m.name == method_name), None)
        if not meth:
            raise HTTPException(status_code=404, detail=f"Method {method_full} not found")
        mc = meth.contract
        output_slots = mc.output_slots if mc else {}
        slot_spec = output_slots.get(slot, {})
        if not isinstance(slot_spec, dict):
            slot_spec = {}
        cols_spec = slot_spec.get("columns") or {}

    strict = list(cols_spec.get("strict", []) or [])
    from_params_specs = list(cols_spec.get("from_params", []) or [])
    patterns = list(cols_spec.get("patterns", []) or [])
    all_cols = sorted(resolve_columns(cols_spec, parsed_params))

    return {
        "strict": strict,
        "from_params": from_params_specs,
        "patterns": patterns,
        "all": all_cols,
    }


# ---------------------------------------------------------------------------
# Track 2: pre-substitution editable sidecar for History "Open in canvas"
# ---------------------------------------------------------------------------


@app.get("/api/workflow/{pipeline_id}/editable")
def get_pipeline_editable(pipeline_id: str):
    """Return the pre-substitution pipeline JSON (with variables + {$var}).

    Falls back to ``pipeline.json`` (post-substitution) for legacy runs that
    were submitted before the editable sidecar existed.
    """
    provider = _require_provider()
    base_dir = provider.project_root / ".runs" / "pipelines" / pipeline_id
    editable = base_dir / "pipeline.editable.json"
    fallback = base_dir / "pipeline.json"
    src = editable if editable.exists() else fallback
    if not src.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Pipeline editable form not found for {pipeline_id}",
        )
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline editable form for {pipeline_id} unreadable: {exc}",
        )


# ---------------------------------------------------------------------------
# Load-in-Canvas endpoints (Actions 1 and 2)
# ---------------------------------------------------------------------------


@app.get("/api/pipelines/{pipeline_id}/document")
def get_pipeline_document(pipeline_id: str):
    """Return the literal ``pipeline.json`` written at submission time.

    Reads ``<project_root>/.runs/pipelines/<pipeline_id>/pipeline.json`` —
    the same file ``WfcProvider._load_bundled_samples`` consumes for fan-in
    sample resolution. Returns the parsed JSON document as-is so the
    canvas can hand it to ``loadPipeline()`` without transformation.

    404 when the file does not exist (the pipeline was authored but
    never reached the snake-gen / run-generation stage).
    """
    provider = _require_provider()
    pipeline_json = (
        provider.project_root / ".runs" / "pipelines" / pipeline_id / "pipeline.json"
    )
    if not pipeline_json.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Pipeline document not found for {pipeline_id}",
        )
    try:
        return json.loads(pipeline_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline document for {pipeline_id} is unreadable: {exc}",
        )


@app.get("/api/runs/{run_id}/lineage-pipeline")
def get_run_lineage_pipeline(run_id: str):
    """Return a synthesized literal-only lineage pipeline for ``run_id``.

    Walks ``parentRunIds`` from the clicked run back to roots — through
    pipeline boundaries — and synthesizes a flat literal-only pipeline
    JSON suitable for the canvas's ``loadPipeline()``. See
    ``wfc.canvas.lineage_synthesizer`` for the algorithm.

    Status codes:
      - 200 with synthesized JSON on success
      - 404 if the run id is unknown
      - 422 if synthesis fails (cycle defense, malformed ancestor chain)
    """
    from .lineage_synthesizer import (
        synthesize_lineage_pipeline,
        LineageSynthesisError,
    )

    provider = _require_provider()
    if provider.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    try:
        return synthesize_lineage_pipeline(provider, run_id)
    except LineageSynthesisError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# SSE log streaming — /api/wfc/run/{run_id}/stream-logs
# ---------------------------------------------------------------------------

_LOG_TERMINAL_STATUSES = {"completed", "success", "failed", "cancelled"}
_LOG_LIVE_POLL_SECONDS = 0.2
_LOG_LIVE_MAX_WALL_SECONDS = 60 * 60


def _log_tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` newline-separated lines from ``path`` via seek-from-end.

    Reads backward in 8 KiB chunks; never slurps the full file.
    """
    if not path.exists() or n <= 0:
        return []
    chunk_size = 8192
    buf = bytearray()
    newlines = 0
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and newlines <= n:
            read = min(chunk_size, pos)
            pos -= read
            f.seek(pos)
            chunk = f.read(read)
            newlines += chunk.count(b"\n")
            buf[:0] = chunk
    text = bytes(buf).decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def _log_read_full_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _log_map_terminal_status(db_status: str) -> str:
    return "success" if db_status == "completed" else db_status


def _log_sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/api/wfc/run/{run_id}/stream-logs")
async def stream_run_logs(run_id: str, full: int = 0, tail: int = 500):
    """Stream a run's stdout/stderr as SSE events, ending with a `terminal` event.

    - Terminal runs (status in {completed, success, failed, cancelled}):
      read the on-disk log files, emit each line as a `stdout`/`stderr`
      event, then emit the `terminal` event and close. ``?full=1`` returns
      the whole file; default tails the last ``tail`` lines (default 500).
    - Running runs: poll the log files every ~200 ms, emit new lines as they
      appear, stop when the DB status transitions off ``running`` and emit a
      final `terminal` event.
    """
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    with get_session() as session:
        row = session.exec(select(Run).where(Run.id == rid)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    status = row.status or "unknown"
    error_message = row.error_message
    error_traceback = row.error_traceback

    run_dir = _project_root() / ".runs" / f"{rid:08d}"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    is_terminal = status in _LOG_TERMINAL_STATUSES

    async def stream():
        if is_terminal:
            out_lines = (
                _log_read_full_lines(stdout_path)
                if full
                else _log_tail_lines(stdout_path, tail)
            )
            for line in out_lines:
                yield _log_sse({"type": "stdout", "data": line})
            err_lines = (
                _log_read_full_lines(stderr_path)
                if full
                else _log_tail_lines(stderr_path, tail)
            )
            for line in err_lines:
                yield _log_sse({"type": "stderr", "data": line})
            terminal_payload: Dict[str, Any] = {
                "type": "terminal",
                "status": _log_map_terminal_status(status),
            }
            if error_message is not None:
                terminal_payload["error_message"] = error_message
            if error_traceback is not None:
                terminal_payload["error_traceback"] = error_traceback
            yield _log_sse(terminal_payload)
            return

        # Live run: emit a tail snapshot first, then incremental new bytes.
        for line in _log_tail_lines(stdout_path, tail):
            yield _log_sse({"type": "stdout", "data": line})
        for line in _log_tail_lines(stderr_path, tail):
            yield _log_sse({"type": "stderr", "data": line})
        stdout_offset = stdout_path.stat().st_size if stdout_path.exists() else 0
        stderr_offset = stderr_path.stat().st_size if stderr_path.exists() else 0

        elapsed = 0.0
        while True:
            with get_session() as session:
                cur = session.exec(select(Run).where(Run.id == rid)).first()
            cur_status = cur.status if cur is not None else "unknown"

            for path, kind, offset_name in (
                (stdout_path, "stdout", "stdout_offset"),
                (stderr_path, "stderr", "stderr_offset"),
            ):
                if not path.exists():
                    continue
                size = path.stat().st_size
                offset = stdout_offset if kind == "stdout" else stderr_offset
                if size <= offset:
                    continue
                with path.open("rb") as f:
                    f.seek(offset)
                    chunk = f.read(size - offset)
                if kind == "stdout":
                    stdout_offset = size
                else:
                    stderr_offset = size
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    if line:
                        yield _log_sse({"type": kind, "data": line})

            if cur_status != "running":
                payload: Dict[str, Any] = {
                    "type": "terminal",
                    "status": _log_map_terminal_status(cur_status),
                }
                if cur is not None and cur.error_message is not None:
                    payload["error_message"] = cur.error_message
                if cur is not None and cur.error_traceback is not None:
                    payload["error_traceback"] = cur.error_traceback
                yield _log_sse(payload)
                return

            await asyncio.sleep(_LOG_LIVE_POLL_SECONDS)
            elapsed += _LOG_LIVE_POLL_SECONDS
            if elapsed >= _LOG_LIVE_MAX_WALL_SECONDS:
                yield _log_sse({"type": "terminal", "status": cur_status})
                return

    return StreamingResponse(stream(), media_type="text/event-stream")


class RunPatchRequest(BaseModel):
    """Partial update for a Run's user-editable metadata.

    All fields are optional; missing fields are left unchanged. `nid`
    writes through to `Run.nid` (provenance table); the rest upsert a
    `RunAnnotation` row.

    `archived` is a bool at the API boundary and maps to the nullable
    `archived_at` timestamp column: ``true`` sets it to now, ``false``
    clears it.
    """
    nid: Optional[str] = None
    favorite: Optional[bool] = None
    tags: Optional[List[str]] = None
    archived: Optional[bool] = None


@app.patch("/api/wfc/run/{run_id}")
def patch_wfc_run(run_id: str, patch: RunPatchRequest):
    """Partial-update a run's user-editable metadata."""
    from sqlmodel import select
    from wfc.database import get_session
    from wfc.models import Run, RunAnnotation

    try:
        rid_int = int(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid run id: {run_id}")

    with get_session() as session:
        run = session.get(Run, rid_int)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        if patch.nid is not None:
            run.nid = patch.nid or None  # empty string clears the override
            session.add(run)

        if (
            patch.favorite is not None
            or patch.tags is not None
            or patch.archived is not None
        ):
            ann = session.exec(
                select(RunAnnotation).where(RunAnnotation.run_id == rid_int)
            ).first()
            if ann is None:
                ann = RunAnnotation(run_id=rid_int)
            if patch.favorite is not None:
                ann.favorite = patch.favorite
            if patch.tags is not None:
                ann.tags = list(patch.tags)
            if patch.archived is not None:
                ann.archived_at = datetime.now(timezone.utc) if patch.archived else None
            ann.updated_at = datetime.now(timezone.utc)
            session.add(ann)

        session.commit()

    # Invalidate the in-memory cache so the next /api/wfc/runs call reflects
    # the change. Skip silently if no provider is configured (e.g. during
    # tests or before a project is loaded) — the DB write already landed.
    try:
        _require_provider()._loaded = False
    except HTTPException:
        pass
    return {"ok": True}


@app.get("/api/wfc/run/{run_id}/artifact/{artifact_path:path}")
def get_wfc_artifact(run_id: str, artifact_path: str):
    file_path = _require_provider().get_artifact_path(run_id, artifact_path)
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_path}")
    return FileResponse(file_path)


# =============================================================================
# Artifact export helpers
# =============================================================================


def _sanitize(name: str) -> str:
    for ch in ['<', '>', ':', '"', '|', '?', '*', '\\', '/']:
        name = name.replace(ch, '_')
    return name.strip().strip('.')


def _build_artifact_zip(artifacts: list) -> io.BytesIO:
    buf = io.BytesIO()
    seen: set = set()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in artifacts:
            method = _sanitize(item['method'])
            run_name = _sanitize(item['run_name'])
            aname = _sanitize(item['artifact_name'])
            zip_path = f"{method}/{run_name}/{aname}"
            if zip_path in seen:
                base, _, ext = aname.rpartition('.')
                zip_path = f"{method}/{run_name}/{base}_{item['run_id'][:8]}.{ext}"
            seen.add(zip_path)
            if os.path.exists(item['file_path']):
                zf.write(item['file_path'], zip_path)
    buf.seek(0)
    return buf


def _build_preview_response(artifacts: list) -> dict:
    methods: set = set()
    runs: set = set()
    total_size = 0
    by_type: dict = {}
    for item in artifacts:
        methods.add(item['method'])
        runs.add(item['run_id'])
        fp = item.get('file_path', '')
        if fp and os.path.exists(fp):
            size = item.get('size_bytes', os.path.getsize(fp))
            total_size += size
            ext = item.get('extension', item.get('artifact_name', '').rsplit('.', 1)[-1].lower())
            entry = by_type.setdefault(ext, {'count': 0, 'size_bytes': 0})
            entry['count'] += 1
            entry['size_bytes'] += size
    return {
        "total_count": len(artifacts),
        "run_count": len(runs),
        "method_count": len(methods),
        "total_size_bytes": total_size,
        "methods": sorted(methods),
        "by_type": [
            {"ext": e, "count": d["count"], "size_bytes": d["size_bytes"]}
            for e, d in sorted(by_type.items())
        ],
        "files": [
            {
                "method": a['method'],
                "run_name": a['run_name'],
                "artifact": a['artifact_name'],
                "ext": a.get('extension', a.get('artifact_name', '').rsplit('.', 1)[-1].lower()),
                "size_bytes": a.get('size_bytes', 0),
            }
            for a in artifacts
        ],
    }


@app.post("/api/wfc/export-artifacts")
def export_wfc_artifacts(request: ExportArtifactsRequest):
    """Zip download of wfc run artifacts, optionally filtered by file type."""
    prov = _require_provider()
    artifacts = prov.get_artifacts(request.run_ids, extensions=request.file_types)
    if not artifacts:
        raise HTTPException(status_code=404, detail="No matching files found.")
    buf = _build_artifact_zip(artifacts)
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="wfc_export_{ts}.zip"'},
    )


@app.post("/api/wfc/preview-artifacts")
def preview_wfc_artifacts(request: ExportArtifactsRequest):
    """Preview what would be exported (all file types) without downloading."""
    return _build_preview_response(_require_provider().get_artifacts(request.run_ids))


@app.post("/api/wfc/export-csvs")
def export_wfc_csvs(request: ExportCSVsRequest):
    """Zip download of CSV artifacts from wfc runs."""
    prov = _require_provider()
    artifacts = prov.get_csv_artifacts(request.run_ids)
    if not artifacts:
        raise HTTPException(status_code=404, detail="No CSV files found.")
    buf = _build_artifact_zip(artifacts)
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="wfc_export_{ts}.zip"'},
    )


@app.post("/api/wfc/preview-csvs")
def preview_wfc_csvs(request: ExportCSVsRequest):
    """Preview CSV export (counts + sizes) without downloading."""
    return _build_preview_response(_require_provider().get_csv_artifacts(request.run_ids))


# =============================================================================
# Static files -- must come last (acts as catch-all for the SPA)
# =============================================================================

app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
