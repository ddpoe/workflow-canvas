"""Canvas pre-wiring + inline-image render gate (US-1 / US-2).

- ``GET /api/pipelines/demo`` returns ``<project_root>/demo-pipeline.json``
  and 404s when absent (inert in a normal project).
- ``is_image`` truth table on the artifact-listing layer that the frontend
  inline-preview gate consumes: true ONLY for the browser-renderable set
  (png/jpg/jpeg/gif/svg/webp); false for pdf/tif/tiff and directory
  artifacts, which keep their download-link rows.
"""
from __future__ import annotations

import json

import pytest
from axiom_annotations import workflow
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine


def test_demo_pipeline_endpoint_404_then_serves(tmp_path, monkeypatch):
    """Tier 1: 404 without a scaffolded demo; parsed JSON once present."""
    import wfc.canvas.server as server

    class _StubProvider:
        project_root = str(tmp_path)

    monkeypatch.setattr(server, "_wfc_provider", _StubProvider())

    with pytest.raises(HTTPException) as exc:
        server.get_demo_pipeline()
    assert exc.value.status_code == 404

    doc = {"name": "demo", "nodes": [], "links": [], "samples": []}
    (tmp_path / "demo-pipeline.json").write_text(json.dumps(doc))
    assert server.get_demo_pipeline() == doc


@workflow(purpose="is_image is true for exactly the browser-renderable set and "
                  "false for pdf/tif/tiff and directory artifacts")
def test_is_image_truth_table(tmp_path, monkeypatch):
    from wfc.canvas.wfc_provider import WfcProvider

    project_root = tmp_path
    (project_root / ".wfc").mkdir()
    db_url = f"sqlite:///{project_root / '.wfc' / 'wfc.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)

    renderable = ["png", "jpg", "jpeg", "gif", "svg", "webp"]
    non_renderable = ["pdf", "tif", "tiff", "csv"]

    staging = project_root / "staging"
    dir_art = staging / "figdir"
    dir_art.mkdir(parents=True)
    (dir_art / "inner.png").write_bytes(b"\x89PNGxxxx")
    for ext in renderable + non_renderable:
        (staging / f"art.{ext}").write_bytes(b"content-" + ext.encode())

    from wfc.models import Method, Module, Run, RunOutput
    with Session(engine) as session:
        module = Module(name="m", description="x")
        session.add(module); session.commit(); session.refresh(module)
        method = Method(name="meth", module_id=module.id, env="container:e")
        session.add(method); session.commit(); session.refresh(method)
        run = Run(method_id=method.id, status="completed")
        session.add(run); session.commit(); session.refresh(run)
        for ext in renderable + non_renderable:
            session.add(RunOutput(
                run_id=run.id, output_name=f"art_{ext}",
                artifact_path=str(staging / f"art.{ext}"),
                artifact_type="method_file",
            ))
        session.add(RunOutput(
            run_id=run.id, output_name="figdir",
            artifact_path=str(dir_art), artifact_type="method_directory",
        ))
        session.commit()
        run_id = run.id
    engine.dispose()

    from wfc.provenance import archive_outputs
    archive_outputs(project_root, run_id=run_id)

    provider = WfcProvider(str(project_root))
    artifacts = provider.list_artifacts(str(run_id))
    by_ext = {a["extension"]: a["is_image"] for a in artifacts if a["type"] == "file"}

    for ext in renderable:
        assert by_ext[ext] is True, f"{ext} should be inline-renderable"
    for ext in non_renderable:
        assert by_ext[ext] is False, f"{ext} must keep the download-link row"

    dir_entry = next(a for a in artifacts if a["type"] == "dir")
    assert dir_entry["is_image"] is False
