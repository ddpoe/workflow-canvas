"""Environment routes: the env list and a registered env's packages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from ...persistence import Method, Module, Run, get_session
from ..state import _server_project_root

router = APIRouter()


# =============================================================================
# Envs registry endpoints
# =============================================================================
#
# Surfaces env specs referenced by registered methods with their run stats
# and registered backend, and a registered env's package list, read from the
# image's committed captures folder.

@router.get("/api/registry/envs")
def get_registry_envs():
    """List distinct env specs referenced by registered methods.

    One row per ``Method.env`` value. Aggregates method names, total run
    count, and most-recent run timestamp (``Run.started_at``) across all
    methods sharing that env, and resolves the registered env's ``backend``
    plus a ``has_packages`` flag: whether the captures folder of the env's
    current image holds a package list (a byo image's holds none). It does
    not verify the list: ``GET .../packages`` does, and refuses one whose
    bytes no longer match the record.

    Each row's ``registry`` says where the env's images are, read from the
    captures folders and the local daemon, never the registry: ``state``
    (``pushed``, ``not_pushed``, ``none`` for no registry, ``cannot_push``
    for an image on this machine whose env name no registry accepts,
    ``missing``, or ``unknown`` when Docker does not answer), ``base``,
    ``older_not_pushed`` (older images method versions record that are not
    pushed), ``older_missing`` (versions whose older image is gone),
    ``recreate`` (the command that recreates the image), ``own_registry``
    (for a byo image pulled from its own registry, that ref; else ``null``)
    and ``reason`` (for ``cannot_push``, why the name cannot be pushed and
    the fixes; else ``null``). It is ``null`` for an env with no record.
    """
    from dataclasses import asdict

    from ...contracts import parse_env_spec
    from ...environments import env_record_for_spec, has_package_list, registry_chip
    from ...registration import version_fingerprints_recording_env

    project_root = _server_project_root()
    with get_session() as session:
        # Methods grouped by env spec.
        methods = session.exec(select(Method)).all()
        modules_by_id: dict[int, str] = {
            m.id: m.name for m in session.exec(select(Module)).all()
        }

        by_env: dict[str, dict[str, Any]] = {}
        method_ids_by_env: dict[str, list[int]] = {}
        for meth in methods:
            spec = meth.env
            row = by_env.setdefault(
                spec,
                {
                    "spec": spec,
                    "methods": [],
                    "backend": None,
                    "has_packages": False,
                    "registry": None,
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
                select(Run).where(col(Run.method_id).in_(method_ids))
            ).all()
            row = by_env[spec]
            row["run_count"] = len(runs)
            started = [r.started_at for r in runs if r.started_at is not None]
            if started:
                row["last_run_at"] = max(started).isoformat()

        # Resolve the backend from the env manifest and the package-list
        # state from the current image's captures folder.
        for spec, row in by_env.items():
            record, backend = env_record_for_spec(spec, project_root)
            row["backend"] = backend
            row["has_packages"] = record is not None and has_package_list(
                parse_env_spec(spec), record.env_fingerprint, project_root
            )
            if record is not None:
                name = parse_env_spec(spec)
                try:
                    versions = version_fingerprints_recording_env(name, project_root)
                except ValueError:
                    versions = []
                row["registry"] = asdict(registry_chip(
                    name, project_root, container=record.container,
                    fingerprint=record.env_fingerprint, versions=versions))

        # Sorted: most-recently-run first, then alphabetical for ties.
        envs = sorted(
            by_env.values(),
            key=lambda r: (r["last_run_at"] is None, -(len(r["methods"])), r["spec"]),
        )
    return {"envs": envs}


@router.get("/api/registry/envs/{spec:path}/packages")
def get_registry_env_packages(spec: str):
    """Installed-package list for a registered env.

    Resolves *spec* to its :class:`wfc.environments.EnvRecord`, asks
    Environments for the package list of the env's current image
    (:func:`wfc.environments.verified_package_list`, read from the committed
    captures folder and checked against the record's ``source_fingerprint``),
    and parses it into a sorted, de-duplicated, source-tagged package list
    via :func:`wfc.environments.parse_packages`.

    Honest empty state: a byo env, an env that never staged source content,
    or an unmatched spec returns ``captured: false`` with ``packages: []`` —
    never a fabricated list. A captures folder changed outside wfc is a
    409: a package list that is missing or whose bytes no longer match the
    record, or an ``image.json`` that is missing or unreadable. Its detail
    names the file and the remedy register-env's refusal names for the
    same folder (:func:`wfc.environments.capture_remedy`): register the env
    again under a new name.

    Response::

        {"spec": "demo",
         "backend": "pixi" | "conda" | "uv" | "byo" | null,
         "captured": true,
         "packages": [{"name": ..., "version": ..., "source": "pixi"}, ...]}
    """
    from ...contracts import parse_env_spec
    from ...environments import (
        CaptureError,
        capture_remedy,
        env_record_for_spec,
        image_info,
        parse_packages,
        verified_package_list,
    )

    project_root = _server_project_root()
    record, backend = env_record_for_spec(spec, project_root)

    if record is None or not record.source_fingerprint:
        return {"spec": spec, "backend": backend, "captured": False, "packages": []}

    # Environments reads the captures folder and verifies its bytes; a
    # missing or drifted package list is refused naming capture_remedy. The
    # image's Python minor (uv) picks which marker-guarded lines it
    # installed; a missing or unreadable image.json is refused with the same
    # remedy, whatever the backend.
    env = parse_env_spec(spec)
    try:
        blob = verified_package_list(env, project_root)
    except CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        minor = image_info(env, record.env_fingerprint, project_root).python_minor
    except CaptureError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc}. "
                   f"{capture_remedy(env, record.env_fingerprint)}",
        ) from exc
    packages = parse_packages(blob or "", record.backend, python_minor=minor,
                              env_name=env)
    return {"spec": spec, "backend": backend, "captured": True, "packages": packages}
