"""The committed API contract agrees with the Canvas API server.

The frontend's generated types are built from a committed OpenAPI snapshot,
not from a live server. This module fails the default suite when the snapshot
no longer matches the document the app builds, so a route, parameter or model
change cannot reach main without regenerating the contract.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from axiom_annotations import workflow

from wfc.canvas.server import app

#: The dump script, loaded by path (its file name is not an importable name).
DUMP_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "wfc" / "canvas" / "static" / "scripts" / "dump-openapi.py"
)

REGENERATE = (
    "The committed API contract is out of date with the server. Regenerate it:\n"
    "  poetry run python wfc/canvas/static/scripts/dump-openapi.py\n"
    "  npm --prefix wfc/canvas/static run codegen\n"
    "and commit openapi.snapshot.json and src/lib/types/api.ts together."
)


def _load_dump_script():
    """Import the dump script as a module so its serializer can be reused.

    Returns:
        The loaded module, exposing ``render_snapshot`` and ``SNAPSHOT_PATH``.
    """
    spec = importlib.util.spec_from_file_location("_wfc_dump_openapi", DUMP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _name_drift(label: str, live: dict, committed: dict) -> list[str]:
    """Describe the names present on only one side, for the failure message."""
    added = sorted(set(live) - set(committed))
    removed = sorted(set(committed) - set(live))
    lines = []
    if added:
        lines.append(f"  {label} in the app, not the snapshot: {added}")
    if removed:
        lines.append(f"  {label} in the snapshot, not the app: {removed}")
    return lines


@workflow(
    purpose=(
        "Verify that the committed OpenAPI snapshot is exactly the document the "
        "Canvas API app builds, serialized by the dump script's own serializer"
    )
)
def test_committed_contract_matches_the_app():
    dump = _load_dump_script()
    live_spec = app.openapi()
    live_text = dump.render_snapshot(live_spec)
    committed_text = dump.SNAPSHOT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")

    if live_text != committed_text:
        committed_spec = json.loads(committed_text)
        detail = _name_drift("paths", live_spec.get("paths", {}), committed_spec.get("paths", {}))
        detail += _name_drift(
            "schemas",
            live_spec.get("components", {}).get("schemas", {}),
            committed_spec.get("components", {}).get("schemas", {}),
        )
        if not detail:
            detail = ["  same path and schema names; a parameter, field or response differs"]
        raise AssertionError(REGENERATE + "\n" + "\n".join(detail))
