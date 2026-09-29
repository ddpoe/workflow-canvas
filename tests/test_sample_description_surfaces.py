"""Both registration surfaces carry a sample's description onto its row.

Tier 2. The CLI is driven through its real entry point over a real project
with a local DVC remote; the canvas route through the app's test client with
real registration behind it, and the sample list the canvas reads back
through a real ``WfcProvider`` over the same project.
"""
from __future__ import annotations

from axiom_annotations import workflow
from sqlmodel import select

from wfc.persistence import Sample, get_session
from tests.fixtures.fakes.server_state import bind_provider
from tests.fixtures.routes import canvas_client, sample_source_dir


@workflow(purpose="POST /api/registry/samples stores a directory sample's "
                  "description, and the canvas sample list serves it with "
                  "the directory's file count; a refused registration is a "
                  "400 with no row")
def test_register_route_stores_the_description_the_sample_list_serves(
        tmp_project, monkeypatch):
    from wfc.canvas.wfc_provider import WfcProvider

    src = sample_source_dir(tmp_project) / "plate"
    (src / "sub").mkdir(parents=True)
    (src / "a.csv").write_text("id\n1\n")
    (src / "sub" / "b.csv").write_text("id\n2\n")
    client = canvas_client(tmp_project, monkeypatch)

    resp = client.post("/api/registry/samples", json={
        "name": "plate", "source": str(src),
        "description": "plate 3, both reads"})

    assert resp.status_code == 200, resp.text
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    listed = client.get("/api/wfc/samples").json()
    [plate] = [s for s in listed if s["name"] == "plate"]
    assert plate["description"] == "plate 3, both reads"
    assert plate["file_count"] == 2
    assert plate["file_type"] == "directory"

    empty = sample_source_dir(tmp_project) / "empty"
    empty.mkdir()
    resp = client.post("/api/registry/samples", json={
        "name": "empty", "source": str(empty)})

    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]
    with get_session() as session:
        assert session.exec(
            select(Sample).where(Sample.name == "empty")).first() is None


@workflow(purpose="wfc register-sample --manifest stores the manifest's "
                  "description on the sample row, and an unknown manifest key "
                  "is refused by name with exit 1 and no row")
def test_cli_register_sample_manifest_stores_the_description(tmp_project):
    from wfc.cli import cli_main

    src_dir = sample_source_dir(tmp_project) / "s"
    src_dir.mkdir(parents=True)
    source = src_dir / "s.csv"
    source.write_text("id,value\n1,2\n")
    manifest = src_dir / "sample.yaml"
    manifest.write_text("description: two columns from plate 3\n")

    rc = cli_main(["register-sample", "--name", "s", "--source", str(source),
                   "--manifest", str(manifest)])

    assert rc == 0
    with get_session() as session:
        row = session.exec(select(Sample).where(Sample.name == "s")).one()
    assert row.description == "two columns from plate 3"

    bad = src_dir / "bad.yaml"
    bad.write_text("description: x\nowner: me\n")
    rc = cli_main(["register-sample", "--name", "t", "--source", str(source),
                   "--manifest", str(bad)])

    assert rc == 1
    with get_session() as session:
        assert session.exec(select(Sample).where(Sample.name == "t")).first() is None
