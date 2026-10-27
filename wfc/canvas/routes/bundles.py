"""Run bundle route: export runs and every run above them as one rerunnable zip.

One route serves History's Export run and Export runs. A dry run
(``?dryRun=true``) returns Run bundle's plan: its steps, every refusal, the
images only this machine holds, and the plan's opaque token. The download
sends the token back and receives the zip the facade writes. The route only
calls the :mod:`wfc.run_bundle` facade; it assembles nothing itself.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ...run_bundle import REFUSED_TEXT, ExportRefusedError, plan_export, write_bundle
from ..state import _server_project_root

router = APIRouter()


class ExportRunsRequest(BaseModel):
    """The runs to export and the bundle's options.

    Attributes:
        run_ids: The runs asked for; every run above them is added.
        with_outputs: Carry the outputs' bytes (``--with-outputs``).
        with_image: Carry each step's image (``--with-image``).
        plan_token: The dry run's token; the download applies only that plan.
            Unused by the dry run itself.
    """

    run_ids: list[str]
    with_outputs: bool = False
    with_image: bool = False
    plan_token: str | None = None


def _run_ids(raw: list[str]) -> list[int]:
    """The request's run ids as run numbers, or a 400 naming the bad one."""
    if not raw:
        raise HTTPException(status_code=400, detail="Choose at least one run to export.")
    ids: list[int] = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"{value!r} is not a run id.") from None
    return ids


def _refused(reasons: list[str]) -> HTTPException:
    """A 409 listing every reason the bundle cannot be complete."""
    return HTTPException(status_code=409,
                         detail={"message": REFUSED_TEXT, "reasons": reasons})


@router.post("/api/wfc/export-runs")
def export_runs(req: ExportRunsRequest, dryRun: bool = False):
    """Plan, or download, the run bundle of the requested runs.

    The dry run plans the export (:func:`wfc.run_bundle.plan_export`: the
    selection, Storage's archive pass over unarchived outputs, every
    refusal, the local-only images) and returns ``{"plan": ...}``, the
    plan's ``to_dict`` with its ``token``. It writes no bundle and answers
    200 even when the plan is refused (``ok`` false).

    The download is the confirmation of that plan: it sends the token back
    as ``plan_token``, plans again, and writes the bundle only when the
    fresh plan has the same token, the rule ``wfc export-run`` applies
    between its question and its write. A request with no token is a 400; a
    refused plan is a 409 listing every reason; a different token (the runs,
    the options or what they hold changed since the dry run) is a 409. The
    zip is written to a temporary file, streamed, and removed afterwards.
    """
    run_ids = _run_ids(req.run_ids)
    if not dryRun and not req.plan_token:
        raise HTTPException(
            status_code=400,
            detail="Export downloads the plan a dry run states: dry run the export "
                   "first. Nothing was written.")
    root = _server_project_root()
    plan = plan_export(run_ids, project_dir=root, with_outputs=req.with_outputs,
                       with_image=req.with_image)
    if dryRun:
        return {"plan": plan.to_dict()}

    if not plan.ok:
        raise _refused([r.reason for r in plan.refusals])
    if plan.token != req.plan_token:
        raise HTTPException(status_code=409, detail={
            "message": "The export changed since the dry run: the runs, the options "
                       "or what they hold are not what it stated. Dry run it again. "
                       "Nothing was written.",
            "reasons": []})

    scratch = Path(tempfile.mkdtemp(prefix="wfc-export-runs-"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = f"wfc_runs_{'-'.join(str(r) for r in plan.requested)}_{stamp}.zip"
    try:
        result = write_bundle(plan, scratch / name, project_dir=root)
    except ExportRefusedError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        raise _refused(list(exc.reasons)) from exc
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return FileResponse(
        result.path, media_type="application/zip", filename=name,
        background=BackgroundTask(shutil.rmtree, scratch, ignore_errors=True))
