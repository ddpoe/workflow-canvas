"""Dump the FastAPI OpenAPI spec to a snapshot JSON file.

ADR-015 Phase D Layer 1.  This script captures the OpenAPI schema for
the canvas FastAPI app at ``wfc.canvas.server`` so the frontend codegen
step (``openapi-typescript``) can run offline against a checked-in
snapshot rather than requiring a live FastAPI process at build time.

Usage:
    poetry run python wfc/canvas/static/scripts/dump-openapi.py

The snapshot is written to
``wfc/canvas/static/scripts/openapi.snapshot.json``.

The frontend ``codegen`` npm script consumes this snapshot to produce
``src/lib/types/api.ts``; that generated file is committed to the repo
(Architect decision D-3) so PR diffs reveal contract drift.
"""

from __future__ import annotations

import json
from pathlib import Path

# Importing the FastAPI app triggers full route registration, including
# Pydantic model schema construction.  Any model that fails to build a
# JSON schema here is a contract bug — fix it before re-running codegen.
from wfc.canvas.server import app


def main() -> None:
    spec = app.openapi()
    out = Path(__file__).resolve().parent / "openapi.snapshot.json"
    out.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    schemas = sorted(spec.get("components", {}).get("schemas", {}).keys())
    print(f"Wrote {out} ({len(schemas)} schemas)")
    # Sanity prints — surface the load-bearing schema names so a missing
    # one is loud during codegen rather than as a TS compile error
    # later.
    for required in ("NodeRunState", "WorkflowStatusResponse", "WorkflowResponse"):
        marker = "ok" if required in schemas else "MISSING"
        print(f"  {marker}: {required}")


if __name__ == "__main__":
    main()
