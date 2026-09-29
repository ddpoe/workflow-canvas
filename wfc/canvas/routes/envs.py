"""Environment routes: the env list, a registered env's packages, and env blobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ...persistence import get_session
from ...persistence import Method, Module, Run
from ..state import _server_project_root

router = APIRouter()


# =============================================================================
# Envs registry endpoints
# =============================================================================
#
# Surfaces env specs referenced by registered methods with their run stats
# and registered backend, a registered env's captured package list, and the
# env-content blobs in the DVC cache.

def _env_blob_text(md5: str, project_root: Path) -> str:
    """Read an env-content blob through Storage, turning its refusals into HTTP errors.

    Shared by ``GET .../blob/<md5>`` and ``GET .../packages``. The detail
    strings are Storage's error messages, unchanged.

    Args:
        md5: 32-char lowercase hex content hash.
        project_root: Canvas project root (containing ``.dvc/``).

    Returns:
        The decoded blob text.

    Raises:
        HTTPException: 400 on a malformed md5 or an attempted path
            traversal, 404 when the blob is absent from the local cache.
    """
    from ...storage import EnvBlobNotFoundError, InvalidEnvBlobHashError, read_env_content

    try:
        return read_env_content(md5, project_root)
    except InvalidEnvBlobHashError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EnvBlobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/registry/envs")
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
    from ...environments import env_record_for_spec

    project_root = _server_project_root()
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
            record, backend = env_record_for_spec(spec, project_root)
            row["backend"] = backend
            row["has_packages"] = bool(record and record.source_fingerprint)

        # Sorted: most-recently-run first, then alphabetical for ties.
        envs = sorted(
            by_env.values(),
            key=lambda r: (r["last_run_at"] is None, -(len(r["methods"])), r["spec"]),
        )
    return {"envs": envs}


@router.get("/api/registry/envs/{spec:path}/packages")
def get_registry_env_packages(spec: str):
    """Installed-package list for a registered pixi/conda env.

    Resolves *spec* to its :class:`wfc.environments.EnvRecord`, reads the captured
    ``source_fingerprint`` blob from the DVC cache, and parses it into a
    sorted, de-duplicated, source-tagged package list via
    :func:`wfc.environments.parse_packages`.

    Honest empty state: a byo env, an env that never staged source content,
    or an unmatched spec returns ``captured: false`` with ``packages: []`` —
    never a fabricated list.

    Response::

        {"spec": "demo",
         "backend": "pixi" | "conda" | "byo" | null,
         "captured": true,
         "packages": [{"name": ..., "version": ..., "source": "pixi"}, ...]}
    """
    from ...environments import env_record_for_spec, parse_packages

    project_root = _server_project_root()
    record, backend = env_record_for_spec(spec, project_root)

    if record is None or not record.source_fingerprint:
        return {"spec": spec, "backend": backend, "captured": False, "packages": []}

    blob = _env_blob_text(record.source_fingerprint, project_root)
    packages = parse_packages(blob, backend)
    return {"spec": spec, "backend": backend, "captured": True, "packages": packages}


@router.get("/api/registry/envs/blob/{md5}")
def get_registry_env_blob(md5: str):
    """Read an env-content blob from the DVC content-addressed cache.

    Returns the raw blob as ``text/plain`` so the frontend can render it
    directly in a code panel. Shares its read path (and path-traversal
    guard) with ``GET .../packages`` via :func:`_env_blob_text`, which maps
    the errors of :func:`wfc.storage.read_env_content` to 400 and 404.
    """
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(_env_blob_text(md5, _server_project_root()))
