"""list_artifacts must include direct children on directory entries.

The history-tab expand-in-place UI keys off ``artifact.children``; without
it the caret flips but the dropdown renders nothing.

Seeding goes through Run/RunOutput rows + the real archive pass (ADR-018:
the cache is authoritative — ``list_artifacts`` resolves outputs from the
DVC cache, not from ``.runs/`` archive directories).
"""

from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from wfc.canvas.wfc_provider import WfcProvider


def test_directory_artifact_has_direct_children(tmp_path, monkeypatch):
    """A directory artifact reports one-level-deep children with size."""
    project_root = tmp_path
    # WfcProvider requires a wfc project layout; seed the minimum.
    (project_root / ".wfc").mkdir()
    db_url = f"sqlite:///{project_root / '.wfc' / 'wfc.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)

    # Stage outputs: a top-level file + a directory with mixed children:
    # file + subdir + dotfile (skipped in the children listing).
    staging = project_root / "staging"
    sub = staging / "tiles"
    (sub / "nested").mkdir(parents=True)
    (sub / "tile_0.png").write_bytes(b"\x89PNG" + b"x" * 16)
    (sub / "tile_1.png").write_bytes(b"\x89PNG" + b"y" * 20)
    (sub / ".hidden").write_text("ignored")
    (staging / "summary.txt").write_text("hi")

    from wfc.models import Method, Module, Run, RunOutput
    with Session(engine) as session:
        module = Module(name="m", description="x")
        session.add(module)
        session.commit()
        session.refresh(module)
        method = Method(name="meth", module_id=module.id, env="container:demo")
        session.add(method)
        session.commit()
        session.refresh(method)
        run = Run(method_id=method.id, status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(RunOutput(
            run_id=run.id, output_name="tiles",
            artifact_path=str(sub), artifact_type="method_directory",
        ))
        session.add(RunOutput(
            run_id=run.id, output_name="summary",
            artifact_path=str(staging / "summary.txt"),
            artifact_type="method_file",
        ))
        session.commit()
        run_id = run.id
    engine.dispose()

    # Real archive pass: content hashes + cache entries, as a pipeline run
    # would leave them.
    from wfc.provenance import archive_outputs
    archive_outputs(project_root, run_id=run_id)

    provider = WfcProvider(str(project_root))
    artifacts = provider.list_artifacts(str(run_id))

    dir_entry = next(a for a in artifacts if a["type"] == "dir")
    assert dir_entry["name"] == "tiles/"
    # count is recursive over all files (including dotfiles) — that's the
    # badge on the collapsed dir header and matches pre-existing behaviour.
    assert dir_entry["count"] >= 2
    assert "children" in dir_entry

    child_names = [c["name"] for c in dir_entry["children"]]
    # Dotfile skipped; nested subdir surfaces with trailing slash; files keep bare name.
    assert "tile_0.png" in child_names
    assert "tile_1.png" in child_names
    assert "nested/" in child_names
    assert ".hidden" not in child_names

    # File sizes populated for leaf files
    tile_0 = next(c for c in dir_entry["children"] if c["name"] == "tile_0.png")
    assert tile_0["size"] > 0
