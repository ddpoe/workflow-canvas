"""Registry routes: modules, methods, samples, and the project file browser."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import col, select

from ...execution import run_tallies
from ...persistence import Module, get_session
from ...registration import (
    ColorCheck,
    DraftNotFoundError,
    DraftRefusedError,
    ListedModule,
    ListedVersion,
    MalformedMethodRecordError,
    ModuleColorRefusedError,
    UnknownVersionError,
    add_draft,
    assign_module_colors,
    check_color,
    check_draft,
    list_drafts,
    list_methods,
    list_modules,
    list_samples,
    listed_module,
    method_contract,
    plan_draft,
    read_draft,
    resolve_method_code,
    set_module_color,
    sync_version_rows,
    update_draft,
)
from .. import draft_views
from ..models import (
    ColorCheckRequest,
    ColorCheckResult,
    ColorRefusalBody,
    DraftActionResponse,
    DraftAddRequest,
    DraftAddResponse,
    DraftCheck,
    DraftDetail,
    DraftRunDetail,
    DraftsResponse,
    DraftUpdateResponse,
    ModuleColorPatch,
    RegistryModule,
    RegistryModulesResponse,
    SnapshotCheckRequest,
    SnapshotCheckResponse,
)
from ..state import _server_project_root

router = APIRouter()


# =============================================================================
# Registry endpoints -- the onboarding/registry UI
# =============================================================================


@router.get("/api/registry/modules", response_model=RegistryModulesResponse)
def get_registry_modules():
    """List registered modules for the Registry tab.

    Shape: `{modules: [{name, description, contracts[], methods, source,
    color}]}`. `color` is the module's stored colour (`#rrggbb`), the one
    every view shows. The modules are Registration's listing
    (:func:`~wfc.registration.list_modules`).
    """
    # A module with no stored colour (registered before colours were stored)
    # is given its default first, in registration order, in one commit; the
    # listing then reads the store.
    with get_session() as session:
        names = [mod.name for mod in
                 session.exec(select(Module).order_by(col(Module.id))).all()]
    _module_colors(names)
    try:
        listed = list_modules(_server_project_root().resolve())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"modules": [_module_out(mod) for mod in listed]}


def _module_out(mod: ListedModule) -> dict[str, Any]:
    """One listed module as the Registry's Modules tab spells it."""
    return {
        "name": mod.name,
        "description": mod.description,
        "contracts": [
            {
                "type": c.type,
                "name": c.name,
                "value_type": c.value_type,
                "required": c.required,
            }
            for c in mod.contracts
        ],
        "methods": mod.methods,
        "source": mod.source,
        "color": mod.color,
    }


def _module_colors(names: list[str]) -> dict[str, str]:
    """Every module's stored colour, storing (and committing) any missing one first.

    Args:
        names: The registered modules' names in registration order.

    Raises:
        HTTPException: 500 when the store is malformed or git refused its commit.
    """
    try:
        return assign_module_colors(_server_project_root().resolve(), names).colors
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _color_check_out(check: ColorCheck) -> dict[str, Any]:
    """A colour check as the API spells it (``ColorCheckResult``)."""
    return {"ok": check.ok, "color": check.color, "failedTest": check.failed_test,
            "reserved": check.reserved, "message": check.message,
            "suggested": check.suggested, "suggestedName": check.suggested_name}


@router.patch("/api/registry/modules/{name}", response_model=RegistryModule,
              responses={400: {"model": ColorRefusalBody}})
def patch_registry_module(name: str, patch: ModuleColorPatch):
    """Set a module's colour (its own commit); null resets it to the default.

    A colour the colour check refuses is a 400 whose detail is the check's
    result, and nothing is written.
    """
    # An unknown module is a 404; a value that is not a hex colour is a 400.
    with get_session() as session:
        modules = session.exec(select(Module).order_by(col(Module.id))).all()
        mod = next((m for m in modules if m.name == name), None)
        if mod is None:
            raise HTTPException(status_code=404, detail=f"Module not found: {name}")
        _module_colors([m.name for m in modules])
        try:
            written = set_module_color(_server_project_root().resolve(), name, patch.color)
        except ModuleColorRefusedError as exc:
            raise HTTPException(status_code=400,
                                detail=_color_check_out(exc.check)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _module_out(listed_module(mod, written.colors[name]))


@router.post("/api/registry/modules/color-check", response_model=ColorCheckResult)
def check_module_color(req: ColorCheckRequest):
    """Check a colour against the colour standard's six tests; nothing is written."""
    # A value that is not a hex colour is a 400 naming it.
    try:
        return _color_check_out(check_color(req.color))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- Drafts ----------------------------------------------------------------
#
# A draft points a module's method name at a method folder on disk. Each
# route calls Registration's draft functions; the draft run is Execution's.

def _draft_pointer(module_name: str, method_name: str, root: Path):
    """The draft's pointer, or a 404 saying the method has no draft.

    Raises:
        HTTPException: 404 with no draft; 500 for a pointer wfc did not write.
    """
    try:
        pointer = read_draft(module_name, method_name, root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if pointer is None:
        raise HTTPException(status_code=404,
                            detail=str(DraftNotFoundError(module_name, method_name)))
    return pointer


@router.get("/api/registry/drafts", response_model=DraftsResponse)
def get_registry_drafts():
    """List every draft, each with its Registration check and latest draft run."""
    root = _server_project_root().resolve()
    try:
        pointers = list_drafts(root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rows = draft_views.pipeline_rows()
    return {"drafts": [draft_views.draft_entry(p, root, rows) for p in pointers]}


@router.post("/api/registry/drafts", response_model=DraftAddResponse)
def add_registry_draft(req: DraftAddRequest, dryRun: bool = False):
    """Add a method folder as a draft of a module.

    The dry run states the kind (a DRAFT version of the module's method of
    the folder's name, or a DRAFT method) and the dialog's sentence, writing
    nothing. A folder whose method already has a draft from another folder
    is a 400 naming that folder.
    """
    root = _server_project_root().resolve()
    try:
        plan = plan_draft(req.module, req.folder, root)
        pointer = None if dryRun else add_draft(req.module, req.folder, root)
    except DraftRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "module": plan.module, "method": plan.method,
            "kind": plan.kind, "message": plan.message,
            "newestLabel": plan.newest_label, "registersAs": plan.registers_as,
            "draft": draft_views.draft_entry(pointer, root) if pointer else None}


@router.post("/api/registry/drafts/snapshot-check", response_model=SnapshotCheckResponse)
def check_draft_snapshots(req: SnapshotCheckRequest):
    """Check each draft's folder against its pointer's stat snapshot; nothing is written."""
    root = _server_project_root().resolve()
    return {"drafts": [draft_views.snapshot_entry(ref.module, ref.method, root)
                       for ref in req.drafts]}


@router.get("/api/registry/drafts/{module_name}/{method_name}/detail",
            response_model=DraftDetail)
def get_registry_draft_detail(module_name: str, method_name: str):
    """A draft's files and contract for its expanded row."""
    root = _server_project_root().resolve()
    pointer = _draft_pointer(module_name, method_name, root)
    contract = pointer.contract or {}
    script = str(contract.get("script") or "")
    files: list[dict[str, Any]] = []
    if pointer.folder.is_dir():
        # The main script first (the one method.yaml names, else the
        # method's name), then the rest by name.
        main = {script, f"{method_name}.py", f"{method_name}.R"}
        for entry in sorted(pointer.folder.iterdir(),
                            key=lambda p: (p.name not in main, p.name)):
            if not entry.is_file() or entry.name.startswith(".") \
                    or entry.name.endswith(".pyc"):
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            files.append({"name": entry.name,
                          "language": _LANG_BY_EXT.get(entry.suffix, "text"),
                          "content": content})
    return {"draft": draft_views.draft_entry(pointer, root), "files": files,
            "contract": draft_views.contract_out(contract),
            "env": contract.get("env")}


@router.post("/api/registry/drafts/{module_name}/{method_name}/update",
             response_model=DraftUpdateResponse)
def update_registry_draft(module_name: str, method_name: str):
    """Update: Registration's writing re-read of the draft, with no run.

    The contract read now is stored on the pointer; the entry carries the
    re-read's own Registration check. Nothing is registered.
    """
    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    try:
        update = update_draft(module_name, method_name, root)
    except DraftRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": draft_views.draft_entry(update.pointer, root, check=update.check),
            "changed": update.changed, "message": update.message}


@router.get("/api/registry/drafts/{module_name}/{method_name}/check",
            response_model=DraftCheck)
def check_registry_draft(module_name: str, method_name: str):
    """The draft's Registration check: every line ✓ or ✗; nothing is written."""
    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    return draft_views.check_out(check_draft(module_name, method_name, root))


@router.delete("/api/registry/drafts/{module_name}/{method_name}",
               response_model=DraftActionResponse)
def remove_registry_draft(module_name: str, method_name: str, dryRun: bool = False,
                          planned: str | None = None):
    """Remove draft: delete its draft folder (pointer and draft run); the method folder is untouched.

    As method registration's route: the dry run returns Execution's delete
    plan (what goes, the text ``wfc delete-draft`` prints) and its token,
    deleting nothing. The real request sends the token back as ``planned``
    and deletes only when a fresh plan has the same token; a different one
    is a 409 having deleted nothing, and a request with no token is a 400.
    """
    from ...execution.draft_clearing import remove_draft_and_clear

    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    if dryRun:
        return _delete_plan_out(module_name, method_name, root, outputs_only=False)
    token = _require_delete_token(planned)
    # The one call site of Remove draft.
    applied = _apply_delete(remove_draft_and_clear, module_name, method_name, root,
                            token)
    return {"ok": True, "message": applied.done_message}


def _delete_plan_out(module_name: str, method_name: str, root: Path, *,
                     outputs_only: bool) -> dict[str, Any]:
    """A delete route's dry run: Execution's plan, its message and its token."""
    from ...execution.draft_clearing import plan_draft_delete

    plan = plan_draft_delete(module_name, method_name, outputs_only=outputs_only,
                             root=root)
    return {"ok": True, "message": plan.message, "plan": {
        "module": plan.module, "method": plan.method, "outputsOnly": plan.outputs_only,
        "folder": plan.folder, "source": plan.source, "draftRun": plan.draft_run,
        "laterDrafts": [{"draft": d.draft, "run": d.run} for d in plan.later_drafts],
        "pipelines": [{"pipelineId": p.pipeline_id, "name": p.name}
                      for p in plan.pipelines],
        "deletes": list(plan.deletes), "keeps": list(plan.keeps), "hint": plan.hint,
        "message": plan.message, "token": plan.token}}


def _require_delete_token(planned: str | None) -> str:
    """A delete without its plan's token is a 400; nothing is deleted.

    Args:
        planned: The token the request sent, if any.

    Returns:
        The token.
    """
    if not planned:
        raise HTTPException(
            status_code=400,
            detail="A delete applies the plan its dry run states: plan the delete "
                   "first (dryRun=true) and send its token as `planned`. Nothing "
                   "was deleted.")
    return planned


def _apply_delete(apply, module_name: str, method_name: str, root: Path,
                  planned: str):
    """Apply a planned delete; a plan that changed since its dry run is a 409."""
    from ...execution.draft_clearing import DraftDeletePlanChangedError

    try:
        return apply(module_name, method_name, root, planned=planned)
    except DraftDeletePlanChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/registry/drafts/{module_name}/{method_name}/draft-run",
            response_model=DraftRunDetail)
def get_registry_draft_run(module_name: str, method_name: str):
    """The draft's latest draft run; a 404 says why there is none."""
    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    try:
        return draft_views.draft_run_detail(module_name, method_name, root)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/registry/drafts/{module_name}/{method_name}/draft-run/zip")
def download_registry_draft_run(module_name: str, method_name: str,
                                node: str | None = None, sample: str | None = None):
    """Download the draft run's outputs as a zip.

    Args:
        module_name: The draft's module.
        method_name: The draft's method.
        node: Only this node's steps.
        sample: Only this sample's steps.
    """
    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    try:
        buf = draft_views.draft_run_zip(module_name, method_name, root, node, sample)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"{method_name}_draft_run.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/registry/drafts/{module_name}/{method_name}/draft-run",
               response_model=DraftActionResponse)
def delete_registry_draft_run(module_name: str, method_name: str, dryRun: bool = False,
                              planned: str | None = None):
    """Delete outputs: remove the draft run, keeping the draft.

    The dry run and the token as :func:`remove_registry_draft` takes them,
    over the Delete outputs plan.
    """
    from ...execution.draft_clearing import delete_draft_outputs_and_clear

    root = _server_project_root().resolve()
    _draft_pointer(module_name, method_name, root)
    if dryRun:
        return _delete_plan_out(module_name, method_name, root, outputs_only=True)
    token = _require_delete_token(planned)
    # The one call site of Delete outputs.
    applied = _apply_delete(delete_draft_outputs_and_clear, module_name, method_name,
                            root, token)
    return {"ok": True, "message": applied.done_message}


@router.get("/api/registry/methods")
def get_registry_methods():
    """List registered methods for the Registry tab.

    Shape: `{methods: [{name, module, env, runCount, source, versions}]}`.

    `source` is the project-relative `method.yaml` of the method's
    registered copy, as the code resolver finds it, or null when the
    resolver refuses the record. `versions` lists every version in label
    order (:func:`_version_out`); the method's `env` is its newest
    version's. A version is named by its identity in the detail and export
    routes; the label is for display. The methods are Registration's
    listing (:func:`~wfc.registration.list_methods`); the run counts are
    Execution's run tallies (:func:`~wfc.execution.run_tallies`), merged on
    the listed row ids.
    """
    listed = list_methods(_server_project_root().resolve())
    tallies = run_tallies()
    return {"methods": [
        {
            "name": meth.name,
            "module": meth.module,
            "env": meth.env,
            "runCount": tallies.for_method(meth.method_id).count,
            "versions": [_version_out(v, tallies.for_version(v.version_id).count)
                         for v in meth.versions],
            "source": meth.source,
        }
        for meth in listed
    ]}


def _version_out(version: ListedVersion, run_count: int) -> dict[str, Any]:
    """One listed version as the Registry spells it.

    Args:
        version: The listed version (Registration's listing).
        run_count: The runs that used the version.

    Returns:
        ``{label, identity, registered_at, env, runCount, newest, commit,
        envFingerprint, image, codeTree}``.
    """
    return {
        "label": version.label,
        "identity": version.identity,
        "registered_at": version.registered_at,
        "env": version.env,
        "runCount": run_count,
        "newest": version.newest,
        "commit": version.commit,
        "envFingerprint": version.env_fingerprint,
        "image": version.image,
        "codeTree": version.code_tree,
    }


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
    version_out = _version_out(
        ListedVersion.from_entry(code.entry, code.version_id, code.git_commit,
                                 code.entry.label == newest.label),
        run_tallies().for_version(code.version_id).count)
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
    is in the DVC cache and pushable to the remote); `False` otherwise. The
    samples are Registration's listing
    (:func:`~wfc.registration.list_samples`); the run counts are
    Execution's run tallies (:func:`~wfc.execution.run_tallies`), merged by
    sample name.
    """
    listed = list_samples(_server_project_root().resolve())
    tallies = run_tallies()
    return {"samples": [
        {
            "name": s.name,
            "source": s.source,
            "size": s.size,
            "hash": s.hash,
            "pushed": s.pushed,
            "runCount": tallies.for_sample(s.name).count,
            "registered_at": s.registered_at,
            "file_type": s.file_type,
        }
        for s in listed
    ]}


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


def _draft_of(req: MethodRegisterRequest):
    """The draft whose folder the request registers, or None.

    Registering a draft's own folder under its module and method registers
    the draft (Registration's ``register_draft``), which then removes it.
    """
    method = req.method_name or Path(req.directory).name
    root = _server_project_root().resolve()
    try:
        pointer = read_draft(req.module, method, root)
    except ValueError:
        return None
    folder = Path(req.directory)
    folder = (folder if folder.is_absolute() else root / folder).resolve()
    return pointer if pointer is not None and pointer.folder == folder else None


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
    draft = _draft_of(req)
    try:
        if draft is not None:
            # The one call site of Register for a draft: the same plan and
            # apply, then the draft (pointer, draft run) goes.
            from ...execution.draft_clearing import register_draft_and_clear
            registration = register_draft_and_clear(
                draft.module, draft.method, assume_yes=True, planned=req.plan_token)
        else:
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
