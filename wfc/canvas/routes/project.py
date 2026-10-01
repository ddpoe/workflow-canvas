"""Project routes: load, refresh and report the history provider."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state
from ..state import _require_provider, _server_project_root
from ..wfc_provider import WfcProvider

router = APIRouter()


class WfcConfig(BaseModel):
    project_root: str


# =============================================================================
# WFC Provider -- configure / reload
# =============================================================================


def _names_served_project(requested: str, served: Path) -> bool:
    """True when ``requested`` names the project this server serves.

    Compared as resolved paths, case-folded where the platform folds case, so
    a relative spelling, a trailing separator or a different case on Windows
    still names the served project.

    Args:
        requested: The project path a caller named, in any spelling.
        served: The served project's root.

    Returns:
        Whether both paths resolve to the same directory.
    """
    try:
        resolved = Path(requested).resolve()
    except (OSError, RuntimeError):
        return False
    return os.path.normcase(str(resolved)) == os.path.normcase(str(served.resolve()))


@router.post("/api/wfc/load")
def load_wfc_data(config: WfcConfig):
    """Reload the history provider for the project this canvas serves.

    A canvas serves the one project it was launched in. Naming that project,
    in any spelling that resolves to it, reloads the provider. Naming any
    other project is refused with 409 and changes nothing: the canvas keeps
    serving its project, and the message says to start a canvas in the other.
    """
    served = _server_project_root()
    if not _names_served_project(config.project_root, served):
        raise HTTPException(
            status_code=409,
            detail=(
                f"This canvas serves the project at {served}. To work with "
                f"{config.project_root}, start a canvas there (run `wfc canvas` "
                f"from inside that project)."
            ),
        )
    try:
        state._wfc_provider = WfcProvider(str(served))
        state._wfc_provider.load()
        return {
            "status": "loaded",
            "path": str(served),
            "modules": len(state._wfc_provider.get_modules()),
            "methods": len(state._wfc_provider.get_methods()),
            "runs": len(state._wfc_provider.get_all_runs()),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/wfc/refresh")
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# =============================================================================
# WFC History endpoints
# =============================================================================


@router.get("/api/wfc/status")
def get_wfc_status():
    if state._wfc_provider is None:
        return {"loaded": False, "path": None}
    return {
        "loaded": True,
        "path": str(state._wfc_provider.project_root),
        "modules": len(state._wfc_provider.get_modules()),
        "runs": len(state._wfc_provider.get_all_runs()),
    }
