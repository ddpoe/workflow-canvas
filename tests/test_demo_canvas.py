"""Canvas pre-wiring + the viewer classifier both artifact listings carry.

- ``GET /api/pipelines/demo`` returns ``<project_root>/demo-pipeline.json``
  and 404s when absent (inert in a normal project).
- ``viewer`` table: every name gets exactly one viewer kind (or none), and
  the run listing and the draft-step listing give any name the same one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from axiom_annotations import workflow
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from tests.fixtures.fakes import bind_provider


def test_demo_pipeline_endpoint_404_then_serves(tmp_path, monkeypatch):
    """Tier 1: 404 without a scaffolded demo; parsed JSON once present."""
    import wfc.canvas.routes.history as history_routes
    import wfc.canvas.state as state

    class _StubProvider:
        project_root = str(tmp_path)

    bind_provider(monkeypatch, _StubProvider())

    with pytest.raises(HTTPException) as exc:
        history_routes.get_demo_pipeline()
    assert exc.value.status_code == 404

    doc = {"name": "demo", "nodes": [], "links": [], "samples": []}
    (tmp_path / "demo-pipeline.json").write_text(json.dumps(doc))
    assert history_routes.get_demo_pipeline() == doc


#: Every extension the viewer classifier knows, with its kind, plus names it
#: must leave download only. Typed out, never computed from the classifier.
_VIEWER_TABLE = {
    **{f"art.{e}": "image" for e in (
        "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "avif",
        "tif", "tiff", "jp2", "j2k", "ppm", "pgm", "pnm")},
    "art.csv": "table", "art.tsv": "table",
    "art.json": "json",
    **{f"art.{e}": "text" for e in (
        "txt", "log", "md", "yaml", "yml", "toml", "py", "sh", "r", "ini", "cfg")},
    "art.pdf": "pdf",
    # Case-insensitive: the displayed name's extension decides.
    "UPPER.TIFF": "image", "Mixed.CsV": "table", "notes.R": "text",
    # Download only: unknown and binary types, no extension, a dotfile.
    "art.parquet": None, "art.h5ad": None, "art.bin": None, "art.zip": None,
    "README": None, ".env": None,
}


@workflow(purpose="Every displayed name gets exactly one viewer kind (image, "
                  "table, json, text, pdf or none), case-insensitively from its "
                  "extension, and a run listing and a draft-step listing holding "
                  "that name give it the same viewer; a folder output gets none")
def test_viewer_classifier_agrees_across_both_listings(tmp_path, monkeypatch):
    from wfc.canvas.draft_views import _artifacts
    from wfc.canvas.previews import viewer_kind
    from wfc.canvas.wfc_provider import WfcProvider

    project_root = tmp_path
    (project_root / ".wfc").mkdir()
    db_url = f"sqlite:///{project_root / '.wfc' / 'wfc.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from wfc.persistence import reset_engine
    reset_engine()
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)

    names = list(_VIEWER_TABLE)

    from wfc.layout import run_archive_dir
    from wfc.persistence import Method, Module, Run, RunOutput
    with Session(engine) as session:
        module = Module(name="m", description="x")
        session.add(module); session.commit(); session.refresh(module)
        method = Method(name="meth", module_id=module.id, env="container:e")
        session.add(method); session.commit(); session.refresh(method)
        run = Run(method_id=method.id, status="completed")
        session.add(run); session.commit(); session.refresh(run)
        # Where collect writes a run's outputs: its run archive.
        staging = run_archive_dir(project_root, run.id)
        dir_art = staging / "figdir"
        dir_art.mkdir(parents=True)
        (dir_art / "inner.png").write_bytes(b"\x89PNGxxxx")
        for i, name in enumerate(names):
            (staging / name).write_bytes(b"content-" + name.encode())
            session.add(RunOutput(
                run_id=run.id, slot=f"out_{i}", output_name=f"out_{i}",
                artifact_path=str(staging / name),
                artifact_type="method_file",
            ))
        session.add(RunOutput(
            run_id=run.id, slot="figdir", output_name="figdir",
            artifact_path=str(dir_art), artifact_type="method_directory",
        ))
        session.commit()
        run_id = run.id
    engine.dispose()

    from wfc.storage import archive_outputs
    archive_outputs(project_root, run_id=run_id)

    provider = WfcProvider(str(project_root))
    artifacts = provider.list_artifacts(str(run_id))
    # A display name may be ``<slot>/<file>``; the file decides the viewer.
    run_viewers = {a["name"].rsplit("/", 1)[-1]: a["viewer"]
                   for a in artifacts if a["type"] == "file"}

    # The draft-step listing over a folder holding the same names. Its own
    # rule leaves dotfiles out, so .env is compared on the run listing only.
    draft_folder = tmp_path / "draft-step"
    draft_folder.mkdir()
    for name in names:
        (draft_folder / name).write_bytes(b"x")
    draft_viewers = {a["name"]: a["viewer"] for a in _artifacts(draft_folder)}

    for i, name in enumerate(names):
        expected = _VIEWER_TABLE[name]
        assert viewer_kind(name) == expected, name
        # The run listing displays an output as its name plus the file's
        # suffix (a dotfile has none), and classifies that displayed name.
        displayed = f"out_{i}{Path(name).suffix}"
        assert run_viewers.get(displayed, "absent") == expected, (
            f"run listing: {displayed} in {sorted(run_viewers)}")
        if not name.startswith("."):
            assert draft_viewers[name] == expected, f"draft listing: {name}"

    dir_entry = next(a for a in artifacts if a["type"] == "dir")
    assert dir_entry["viewer"] is None
    inner = next(c for c in dir_entry["children"] if c["name"] == "inner.png")
    assert (inner["extension"], inner["viewer"]) == ("png", "image")
