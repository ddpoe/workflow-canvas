"""Registry routes: modules, methods, samples, and the project file browser."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ...persistence import Module, Run, get_session
from ...registration import (
    MalformedMethodRecordError,
    UnknownVersionError,
    method_contract,
    resolve_method_code,
    sync_version_rows,
)
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

    Shape: `{methods: [{name, module, env, runCount, source, versions}]}`.

    `source` is the project-relative `method.yaml` of the method's
    registered copy, as the code resolver finds it, or null when the
    resolver refuses the record. `versions` lists every version in label
    order (:func:`_version_out`); the method's `env` is its newest
    version's. A version is named by its identity in the detail and export
    routes; the label is for display.
    """
    with get_session() as session:
        run_counts: dict[int, int] = {}
        version_runs = _version_run_counts(session)
        for method_id in session.exec(select(Run.method_id)).all():
            run_counts[method_id] = run_counts.get(method_id, 0) + 1

        modules = session.exec(select(Module)).all()
        out: list[dict[str, Any]] = []
        for mod in modules:
            for meth in mod.methods:
                out.append({
                    "name": meth.name,
                    "module": mod.name,
                    "env": meth.env,
                    "runCount": run_counts.get(meth.id, 0),
                })
    root = _server_project_root().resolve()
    for row in out:
        row["versions"] = _listed_versions(row["module"], row["name"], root,
                                           version_runs)
        if row["versions"]:
            row["env"] = row["versions"][-1]["env"]
        row["source"] = _registered_yaml(row["module"], row["name"], root)
    return {"methods": out}


def _version_run_counts(session) -> dict[int, int]:
    """Run count per ``MethodVersion`` row id."""
    counts: dict[int, int] = {}
    for version_id in session.exec(select(Run.version_id)).all():
        if version_id is not None:
            counts[version_id] = counts.get(version_id, 0) + 1
    return counts


def _version_out(entry, git_commit: str | None, run_count: int,
                 newest: bool) -> dict[str, Any]:
    """One version as the Registry names it.

    Args:
        entry: The version-list entry.
        git_commit: The registration commit the version's row records, or
            ``None`` for a version this database did not register (it
            arrived by a pull or a fresh clone).
        run_count: The runs that used the version.
        newest: Whether it is the newest version.

    Returns:
        ``{label, identity, registered_at, env, runCount, newest, commit,
        envFingerprint, image, codeTree}``.
    """
    return {
        "label": entry.label,
        "identity": entry.identity,
        "registered_at": entry.registered_at,
        "env": entry.env_name,
        "runCount": run_count,
        "newest": newest,
        "commit": git_commit,
        "envFingerprint": entry.env_fingerprint,
        "image": entry.image_ref,
        "codeTree": entry.tree_hash,
    }


def _listed_versions(module: str, method: str, root: Path,
                     version_runs: dict[int, int]) -> list[dict[str, Any]]:
    """Every version of a method for the Registry listing, in label order.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The resolved project root.
        version_runs: Run count per ``MethodVersion`` row id.

    Returns:
        One :func:`_version_out` per version-list entry; none for a method
        with no version list.
    """
    try:
        versions = sync_version_rows(module, method, root)
    except MalformedMethodRecordError:
        return []
    newest = len(versions) - 1
    return [
        _version_out(v.entry, v.git_commit, version_runs.get(v.version_id, 0),
                     index == newest)
        for index, v in enumerate(versions)
    ]


def _registered_yaml(module: str, method: str, root: Path) -> str | None:
    """Return the project-relative method.yaml of a method's registered copy.

    Args:
        module: The method's module name.
        method: The method's name.
        root: The resolved project root.

    Returns:
        The POSIX path, or ``None`` when the resolver refuses the record.
    """
    try:
        code = resolve_method_code(module, method, root)
    except MalformedMethodRecordError:
        return None
    return (code.snapshot_dir.resolve() / "method.yaml").relative_to(root).as_posix()


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
def get_registry_method_detail(module_name: str, method_name: str,
                               version: str | None = None):
    """Read-only view of one version of a method: its files, contract and env.

    Lists every file at the top of the version's extracted copy,
    ``.wfc/runs/code/<tree hash>/`` (no recursion), with its content and a
    language hint for syntax highlighting: the method's main script first,
    then the rest by name. Returns the version's DB-parsed contract
    (input_slots, output_slots, params_schema, executor) alongside so the
    frontend can render it as structured tables without re-parsing YAML,
    and the version itself (:func:`_version_out`, with its run count).

    Args:
        module_name: The method's module.
        method_name: The method's name.
        version: The version's content identity (what a pin holds); absent
            is the newest version. A version no entry carries is a 404.
    """
    method_name = _registered_method_name(module_name, method_name)
    root = _server_project_root().resolve()
    try:
        contract = method_contract(module_name, method_name, version, root)
        # The resolver extracts the version's copy and refuses a record that
        # does not lead to a script inside it (no list, a tree git no longer
        # holds).
        code = resolve_method_code(module_name, method_name, root, version=version)
    except UnknownVersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MalformedMethodRecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    contract_out = {
        "input_slots":   contract.input_slots   if contract else {},
        "output_slots":  contract.output_slots  if contract else {},
        "params_schema": contract.params_schema if contract else {},
        "executor":      contract.executor      if contract else "python",
    }
    newest = sync_version_rows(module_name, method_name, root)[-1].entry
    with get_session() as session:
        run_count = _version_run_counts(session).get(code.version_id, 0)
    version_out = _version_out(code.entry, code.git_commit, run_count,
                               code.entry.label == newest.label)
    method_dir = code.code_dir
    script = code.script.resolve()

    files: list[dict[str, Any]] = []
    # The main script first, then the rest by name.
    for entry in sorted(method_dir.iterdir(),
                        key=lambda p: (p.resolve() != script, p.name)):
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
    return {"files": files, "contract": contract_out, "env": code.entry.env_name,
            "version": version_out}


def _registered_method_name(module_name: str, method_name: str) -> str:
    """The registered method's name, or a 404 naming the module or method.

    Raises:
        HTTPException: 404 when no module or no method of it has that name.
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
        return meth.name


@router.get("/api/registry/methods/{module_name}/{method_name}/export")
def export_registry_method(module_name: str, method_name: str,
                           version: str | None = None):
    """Download one version of a method's script and contract as a zip.

    The zip, ``<method>_v<label>.zip``, holds every file of the version's
    registered copy as Registration resolves it (``resolve_method_code``),
    subfolders included, under their copy-relative paths. The copy's
    verified stamp sits beside the copy, not in it, so it is never in the
    zip. Unzipped and edited, the folder registers as a new version.

    Args:
        module_name: The method's module.
        method_name: The method's name.
        version: The version's content identity; absent is the newest
            version. A version no entry carries is a 404 naming it.
    """
    method_name = _registered_method_name(module_name, method_name)
    root = _server_project_root().resolve()
    try:
        code = resolve_method_code(module_name, method_name, root, version=version)
    except UnknownVersionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MalformedMethodRecordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(code.code_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(code.code_dir).as_posix())
    buf.seek(0)
    filename = f"{method_name}_v{code.entry.label}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    # The dry run's plan token; the Register POST sends it back so only
    # the plan the user saw is applied. Unused by the dry run itself.
    plan_token: str | None = None


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
    """Wrap ``wfc.registration.register_method`` with optional dry-run preflight.

    The dry run also plans the registration (``plan_method_registration``,
    nothing written) and returns the plan's message, the same text ``wfc
    register-method`` prints: registers v1, adds a version naming what
    changed, or matches the newest version. Beside it the plan carries its
    ``label`` (the version it adds, or the newest it matches), the
    ``newestLabel`` before registering, its ``changes`` and ``holds`` as
    Registration states them (the data the message is rendered from), and
    its ``token``: the identity of what it would write (code fingerprint,
    env fingerprint, newest label). A refusal the plan makes (a folder that
    does not clearly hold one method among them) is a failed pre-check
    whose detail says what wfc expects.
    The Register click is the confirmation of that plan: the real request
    sends the token back as ``plan_token`` and registration applies only
    when a fresh plan has the same token, the rule ``wfc register-method``
    applies between its question and its write. A different token (the
    source, the env or the version list changed since the dry run) is a 409
    having changed nothing; a request with no token is a 400.
    """
    from ...registration import RegistrationPlanChangedError

    checks = _method_register_pre_checks(req)
    ok = all(c["status"] != "fail" for c in checks)

    if not ok:
        return {"ok": ok, "preChecks": checks}
    if dryRun:
        from ...registration import plan_method_registration
        try:
            plan = plan_method_registration(
                method_dir=Path(req.directory),
                module_name=req.module,
                method_name=req.method_name,
            )
        except (FileNotFoundError, ValueError) as exc:
            checks.append({"status": "fail", "label": "registration plan",
                           "detail": str(exc)})
            return {"ok": False, "preChecks": checks}
        return {"ok": True, "preChecks": checks,
                "plan": {"outcome": plan.outcome, "message": plan.message,
                         "label": plan.label, "newestLabel": plan.newest_label,
                         "changes": plan.changes, "holds": plan.holds,
                         "token": plan.token}}

    if not req.plan_token:
        raise HTTPException(
            status_code=400,
            detail="Register applies the plan its check states: check the "
                   "registration first. Nothing was registered.")
    try:
        registration = _register_method_fn(
            method_dir=Path(req.directory),
            module_name=req.module,
            method_name=req.method_name,
            assume_yes=True,
            planned=req.plan_token,
        )
    except RegistrationPlanChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "preChecks": checks,
        "method": {"name": req.method_name or Path(req.directory).name},
        "notCopied": list(registration.not_copied),
        "advice": registration.advice,
        "message": registration.message,
    }


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
