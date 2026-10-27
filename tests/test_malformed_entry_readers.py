"""Every reader of an archived output refuses a malformed cache entry by name.

A malformed cache entry is a directory tree stored at an address without
the ``.dir`` suffix. Storage's shape check refuses it with the re-run
sentence (``output_repair``); because that refusal is a
``ResolveOutputError``, each reader that reports Storage's refusals reports
this one too, instead of ending in a traceback or skipping the output.

The run is produced by production's phases (``completed_run``) and archived
by the production archive pass. Declared fixture deviation: ``_lay_tree_at``
lays the legacy directory shape at the output's unsuffixed address, which
production no longer writes.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from axiom_annotations import workflow
from sqlmodel import select

from tests.fixtures.fakes import bind_provider
from tests.fixtures.routes import canvas_client, completed_run


def _lay_tree_at(project: Path, run_id: int) -> str:
    """Declared deviation: the legacy directory shape at the output's unsuffixed address."""
    from wfc import layout
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        (row,) = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
    assert row.content_hash and not row.content_hash.endswith(".dir"), row
    address = layout.dvc_cache_entry(project, row.content_hash)
    os.chmod(address, stat.S_IREAD | stat.S_IWRITE)
    address.unlink()
    address.mkdir()
    (address / "a.csv").write_text("old shape\n")
    return row.slot


@workflow(purpose="A malformed cache entry under an archived output is refused "
                  "with the re-run sentence and no traceback by wfc export (one "
                  "slot, --all and --path), by wfc export-run, by the canvas "
                  "artifact listing and zip (a 409, not a skipped output) and by "
                  "the resolve_input CLI")
def test_every_reader_refuses_a_malformed_entry_naming_the_re_run(
    cli, tmp_project, monkeypatch, tmp_path
):
    from wfc.canvas.wfc_provider import WfcProvider
    from wfc.storage import archive_outputs

    run_id = completed_run(tmp_project, monkeypatch=monkeypatch, method="shaped",
                           outputs={"table": ".csv"}).run_id
    archive_outputs(tmp_project, run_id=run_id)
    slot = _lay_tree_at(tmp_project, run_id)
    re_run = f"Re-run run {run_id} to produce the output again."

    dest = tmp_path / "out"
    for argv in (
        ("export", "--run", str(run_id), "--slot", slot, "--dest", str(dest)),
        ("export", "--run", str(run_id), "--slot", slot, "--path"),
        ("export", "--run", str(run_id), "--all", "--dest", str(dest)),
        ("resolve_input", "--run-id", str(run_id)),
    ):
        result = cli(*argv)
        assert result.returncode == 1, f"{argv} -> {result!r}"
        assert "malformed cache entry" in result.stderr, argv
        assert re_run in result.stderr, argv
        assert result.stdout == "", argv
    assert not dest.exists()

    # wfc export-run prints its plan on stdout, then lists the refusal.
    bundle = tmp_path / "bundle.zip"
    result = cli("export-run", "--run", str(run_id), "--out", str(bundle), "--yes")
    assert result.returncode == 1, result
    assert "malformed cache entry" in result.stderr
    assert re_run in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not bundle.exists()

    client = canvas_client(tmp_project, monkeypatch)
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    listing = client.get(f"/api/wfc/run/{run_id}/artifacts")
    zipped = client.post("/api/wfc/export-artifacts", json={"run_ids": [str(run_id)]})
    for response in (listing, zipped):
        assert response.status_code == 409, response.text
        assert re_run in response.json()["detail"]
        assert "malformed cache entry" in response.json()["detail"]

    # The tree is left as it was: nothing converts a malformed entry.
    from wfc import layout
    from wfc.persistence import RunOutput, get_session

    with get_session() as session:
        (row,) = session.exec(select(RunOutput).where(RunOutput.run_id == run_id)).all()
    tree = layout.dvc_cache_entry(tmp_project, row.content_hash)
    assert (tree / "a.csv").read_text() == "old shape\n"
    shutil.rmtree(tree, ignore_errors=True)
