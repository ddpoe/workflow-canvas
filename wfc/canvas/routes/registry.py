"""Registry routes: modules, methods, samples, and the project file browser."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ...persistence import Module, Run, get_session
from ..state import _server_project_root

router = APIRouter()


# =============================================================================
# Registry endpoints -- the onboarding/registry UI
# =============================================================================


@router.get("/api/registry/modules")
def get_registry_modules():
    """List registered modules for the Registry tab.

    Shape: `{modules: [{name, description, contracts[], methods, source}]}`.
    `color` is intentionally omitted -- the frontend assigns it from the
    `MOD_COLORS` cycle by registration order.
    """
    with get_session() as session:
        modules = session.exec(select(Module)).all()
        out: list[dict[str, Any]] = []
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


@router.get("/api/registry/methods")
def get_registry_methods():
    """List registered methods for the Registry tab.

    Shape: `{methods: [{name, module, env, validated, runCount, source}]}`.

    `validated` is a `bool | null` (null = never checked); this list does not
    read the validation cache, so it is always null.
    """
    with get_session() as session:
        run_counts: dict[int, int] = {}
        for row in session.exec(select(Run.method_id)).all():
            run_counts[row] = run_counts.get(row, 0) + 1

        modules = session.exec(select(Module)).all()
        out: list[dict[str, Any]] = []
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
# A method's validation state is `validated: bool | null`, populated from an
# in-memory cache keyed on
# `(method_id, script_fingerprint)`. POST /api/registry/methods/validate
# runs the check on demand and reads the cache; GET /api/registry/methods
# does not read it, so the list's `validated` is always null.

_method_validate_cache: dict[tuple, dict[str, Any]] = {}


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


def _script_fingerprint(script_path: Path) -> str | None:
    if not script_path.exists():
        return None
    return hashlib.sha256(script_path.read_bytes()).hexdigest()


class MethodValidateRequest(BaseModel):
    module: str
    method: str


_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".R": "r",
    ".r": "r",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "shell",
    ".txt": "text",
}


@router.get("/api/registry/methods/{module_name}/{method_name}/detail")
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
    project_root = _server_project_root().resolve()
    method_dir = (project_root / script_rel).parent.resolve()
    try:
        method_dir.relative_to(project_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"method directory escapes project root (path traversal): {script_rel}",
        ) from exc
    if not method_dir.is_dir():
        return {"files": [], "contract": contract_out}

    files: list[dict[str, Any]] = []
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


@router.post("/api/registry/methods/validate")
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

    script_path = _server_project_root() / script_rel
    fingerprint = _script_fingerprint(script_path)

    pre_checks: list[dict[str, Any]] = []
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
# Route handlers wrap the wfc.registration helpers.
# Errors from the Python API are mapped to HTTP status codes:
#   FileNotFoundError       -> 404
#   ValueError              -> 400
#   DvcNotConfiguredError   -> 409
# Each hook is exposed as a module-level callable so tests can patch it.


def _default_register_sample(*args, **kwargs):
    from ...registration import register_sample
    return register_sample(*args, **kwargs)


_register_sample_fn = _default_register_sample


def _default_register_module(*args, **kwargs):
    from ...registration import register_module
    return register_module(*args, **kwargs)


_register_module_fn = _default_register_module


class ContractSpec(BaseModel):
    type: str  # "output" | "metric" | "input" | "param"
    name: str
    value_type: str | None = None
    required: bool = True


class ModuleRegisterRequest(BaseModel):
    name: str
    description: str | None = None
    folder: str | None = None
    contracts: list[ContractSpec] = []


def _module_pre_checks(req: ModuleRegisterRequest) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
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


@router.post("/api/registry/modules")
def register_module_endpoint(req: ModuleRegisterRequest, dryRun: bool = False):
    """Wrap ``wfc.registration.register_module`` with optional dry-run preflight."""
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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "preChecks": checks, "module": {"name": req.name}}


@router.get("/api/registry/samples")
def get_registry_samples():
    """List registered samples for the Registry tab.

    Shape: `{samples: [{name, source, size, hash, pushed, runCount,
    registered_at}]}`.

    `pushed` is `True` when a DVC content hash is present (indicates the sample
    is in the DVC cache and pushable to the remote); `False` otherwise.
    """
    from ...persistence import Sample

    with get_session() as session:
        run_counts: dict[str, int] = {}
        for row in session.exec(select(Run.sample)).all():
            if row:
                run_counts[row] = run_counts.get(row, 0) + 1

        samples = session.exec(select(Sample).order_by(Sample.name)).all()
        out: list[dict[str, Any]] = [
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


def _default_register_method(*args, **kwargs):
    from ...registration import register_method
    return register_method(*args, **kwargs)


_register_method_fn = _default_register_method


class MethodRegisterRequest(BaseModel):
    directory: str
    module: str
    method_name: str | None = None


def _method_register_pre_checks(req: MethodRegisterRequest) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # --- directory ---
    directory = (req.directory or "").strip()
    if not directory:
        checks.append({
            "status": "fail",
            "label": "directory provided",
            "detail": "required — pick a folder containing method.yaml",
        })
    else:
        method_dir = (_server_project_root() / directory).resolve()
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


@router.post("/api/registry/methods")
def register_method_endpoint(req: MethodRegisterRequest, dryRun: bool = False):
    """Wrap ``wfc.registration.register_method`` with optional dry-run preflight."""
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
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "preChecks": checks, "method": {"name": req.method_name or Path(req.directory).name}}


@router.get("/api/fs/browse")
def fs_browse(path: str = ""):
    """List directory contents under the project root.

    ``path`` is relative to the project root. Defaults to the root itself.
    Rejects any path that resolves outside the project root. Safe for a
    localhost single-user dev tool: the server is already scoped to the
    project the user chose to launch it against.
    """
    project_root = _server_project_root().resolve()
    target = (project_root / path).resolve()
    try:
        target.relative_to(project_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"path escapes project root (path traversal): {path}",
        ) from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {path}")

    entries: list[dict[str, Any]] = []
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


class SampleRegisterRequest(BaseModel):
    name: str
    source: str
    registration_mode: str | None = "copy"
    description: str | None = None


@router.post("/api/registry/samples")
def register_sample_endpoint(req: SampleRegisterRequest):
    """Wrap ``wfc.registration.register_sample`` behind an HTTP endpoint."""
    from ...storage import DvcNotConfiguredError

    try:
        _register_sample_fn(
            name=req.name,
            source_path=Path(req.source),
            registration_mode=req.registration_mode or "copy",
            description=req.description,
        )
    except DvcNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "sample": {"name": req.name}}
