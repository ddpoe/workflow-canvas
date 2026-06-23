"""list_artifacts must include direct children on directory entries.

The history-tab expand-in-place UI keys off ``artifact.children``; without
it the caret flips but the dropdown renders nothing.
"""

from pathlib import Path

from sqlmodel import SQLModel, create_engine

from wfc.canvas.wfc_provider import WfcProvider


def test_directory_artifact_has_direct_children(tmp_path, monkeypatch):
    """A directory artifact reports one-level-deep children with size."""
    project_root = tmp_path
    # WfcProvider requires a wfc project layout; seed the minimum.
    (project_root / ".wfc").mkdir()
    create_engine(f"sqlite:///{project_root / '.wfc' / 'wfc.db'}")
    db_url = f"sqlite:///{project_root / '.wfc' / 'wfc.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from wfc.database import reset_engine
    reset_engine()
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)

    runs_dir = project_root / ".runs" / "00000042"
    runs_dir.mkdir(parents=True)

    # Top-level file
    (runs_dir / "summary.txt").write_text("hi")
    # Directory with mixed children: file + subdir + dotfile (skipped)
    sub = runs_dir / "tiles"
    sub.mkdir()
    (sub / "tile_0.png").write_bytes(b"\x89PNG" + b"x" * 16)
    (sub / "tile_1.png").write_bytes(b"\x89PNG" + b"y" * 20)
    (sub / ".hidden").write_text("ignored")
    (sub / "nested").mkdir()

    provider = WfcProvider(str(project_root))
    artifacts = provider.list_artifacts("42")

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
