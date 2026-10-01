"""Workflow Canvas -- FastAPI backend.

All data comes from the live wfc SQLite database.

This module is the app: ``app``, ``lifespan``, the router assembly, the SPA
index and the static mount. The routes live in ``wfc/canvas/routes/``; the
route table is in ``docs/system/canvas-api.json`` and each route's
contract in ``docs/system/canvas-api/catalog.json``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import layout
from . import state
from .routes import archive, artifacts, builder, cache_status, envs, history, logs, project, registry, runs
from .state import _server_project_root
from .wfc_provider import WfcProvider

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static" / "dist"

# The frontend bundle lives in `static/dist/` after `npm run build`. In
# dev/test environments the bundle may be absent; the StaticFiles mount below
# passes check_dir=False so importing `wfc.canvas.server` never requires a
# build step (and never mutates the source tree). SPA requests then 404 until
# a build exists; production deployments always ship a built bundle.


# ---------------------------------------------------------------------------
# Lifespan -- auto-load provider from cwd on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the provider for the resolved project root at server startup."""
    # The project this server serves is the canonical resolver's answer.
    # No resolvable project means no server: the resolver's error propagates
    # and startup fails, rather than a server that silently serves whatever
    # directory the process sits in (cwd may shift or be rewritten, e.g.
    # C:\Windows on Snakemake subprocess spawns).
    project_root = _server_project_root()
    db_path = layout.db_path(project_root)
    if db_path.exists():
        try:
            state._wfc_provider = WfcProvider(str(project_root))
            state._wfc_provider.load()
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
# Routers -- include order is dispatch order: the run router must stay ahead
# of the history router so
# GET /api/workflow/status/{job_id} matches before
# GET /api/workflow/{pipeline_id}/editable. No prefix or tags: either would
# change the committed OpenAPI snapshot.
# =============================================================================

app.include_router(builder.router)
app.include_router(registry.router)
app.include_router(envs.router)
app.include_router(runs.router)
app.include_router(cache_status.router)
app.include_router(project.router)
app.include_router(history.router)
app.include_router(artifacts.router)
app.include_router(archive.router)
app.include_router(logs.router)


# =============================================================================
# Canvas SPA
# =============================================================================


@app.get("/")
def root():
    """Serve the main SPA page."""
    return FileResponse(_STATIC_DIR / "index.html")


# =============================================================================
# Static files -- must come last (acts as catch-all for the SPA)
# =============================================================================

app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True, check_dir=False), name="static")
