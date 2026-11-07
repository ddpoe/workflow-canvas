"""Canvas API route tests: a run's artifact listing and file, and the export and preview pair.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``artifacts-routes`` and
``artifacts-export-preview``. Every output is archived into the project's
real local cache, so the routes resolve what production resolves.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from axiom_annotations import workflow
from sqlmodel import Session

from tests.fixtures.fakes import bind_provider
from wfc.canvas import state as canvas_state
from wfc.canvas.wfc_provider import WfcProvider
from wfc.layout import run_archive_dir
from wfc.persistence import Method, Module, Run, RunOutput
from wfc.storage import archive_outputs

A_TABLE = b"a,b\n1,2\n"
A_STATS = b'{"n": 1}'
B_TABLE = b"a,b\n3,4\n5,6\n"


@pytest.fixture
def archived_runs(canvas_db, tmp_project, monkeypatch):
    """Two completed runs of one method on one sample, their outputs archived, and a bound provider.

    Run ``a`` writes ``table`` (csv) and ``stats`` (json); run ``b`` writes
    only ``table``, so its export entry clashes with ``a``'s. The sample name
    ``s:1`` carries a character a zip entry cannot hold.

    Returns:
        ``(provider, a_id, b_id)`` with the run ids as strings.
    """
    # Stays on typed rows: ``run_name`` is ``method/sample``, and every
    # character the export sanitizes (``routes/artifacts._sanitize``) is
    # one Windows refuses in a directory name, so no run over ``s:1`` can
    # stage or materialize its sample through the route on this OS. The
    # literal sample name is the sanitization claim's own input.
    outputs = {
        "a": {"table": ("a_table.csv", A_TABLE), "stats": ("a_stats.json", A_STATS)},
        "b": {"table": ("b_table.csv", B_TABLE)},
    }
    run_ids: dict[str, int] = {}
    with Session(canvas_db) as session:
        mod = Module(name="features")
        session.add(mod)
        session.flush()
        meth = Method(name="regionprops", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        for label, slots in outputs.items():
            run = Run(method_id=meth.id, sample="s:1", status="completed")
            session.add(run)
            session.flush()
            run_ids[label] = run.id
            # Where collect writes a run's outputs: its run archive.
            staging = run_archive_dir(tmp_project, run.id)
            staging.mkdir(parents=True)
            for slot, (filename, content) in slots.items():
                (staging / filename).write_bytes(content)
                session.add(RunOutput(run_id=run.id, slot=slot, output_name=slot,
                                      artifact_path=str(staging / filename),
                                      artifact_type="method_file"))
        session.commit()
    for rid in run_ids.values():
        archive_outputs(tmp_project, run_id=rid)

    provider = WfcProvider(str(tmp_project))
    bind_provider(monkeypatch, provider)
    return provider, str(run_ids["a"]), str(run_ids["b"])


@workflow(
    purpose="A run's artifact listing route answers the provider's listing, the "
            "file route serves an artifact's bytes, and an unknown artifact name "
            "is 404",
)
def test_artifact_listing_and_file_routes_serve_the_cache(canvas_client, archived_runs):
    provider, a_id, _ = archived_runs

    listing = canvas_client.get(f"/api/wfc/run/{a_id}/artifacts")
    served = canvas_client.get(f"/api/wfc/run/{a_id}/artifact/table.csv")
    unknown = canvas_client.get(f"/api/wfc/run/{a_id}/artifact/missing.csv")

    assert listing.status_code == 200, listing.text
    assert {a["name"] for a in listing.json()} == {"table.csv", "stats.json"}
    assert listing.json() == provider.list_artifacts(a_id)
    assert served.status_code == 200, served.text
    assert served.content == A_TABLE
    assert unknown.status_code == 404, unknown.text


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
def typed_outputs_run(canvas_db, tmp_project, monkeypatch):
    """One completed run with a top-level csv output and a folder output, archived.

    The folder ``figs`` holds a png, an svg and a parquet file. A top-level
    file output's cache entry has no extension, so only the displayed name
    can say what it is.

    Returns:
        The run id as a string.
    """
    with Session(canvas_db) as session:
        mod = Module(name="plots")
        session.add(mod)
        session.flush()
        meth = Method(name="draw", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        run = Run(method_id=meth.id, sample="s1", status="completed")
        session.add(run)
        session.flush()
        staging = run_archive_dir(tmp_project, run.id)
        figs = staging / "figs"
        figs.mkdir(parents=True)
        (staging / "out_table.csv").write_bytes(A_TABLE)
        (figs / "plot.png").write_bytes(PNG_BYTES)
        (figs / "logo.svg").write_bytes(SVG_BYTES)
        (figs / "cells.parquet").write_bytes(b"PAR1")
        session.add(RunOutput(run_id=run.id, slot="table", output_name="table",
                              artifact_path=str(staging / "out_table.csv"),
                              artifact_type="method_file"))
        session.add(RunOutput(run_id=run.id, slot="figs", output_name="figs",
                              artifact_path=str(figs),
                              artifact_type="method_directory"))
        session.commit()
        run_id = run.id
    archive_outputs(tmp_project, run_id=run_id)
    bind_provider(monkeypatch, WfcProvider(str(tmp_project)))
    return str(run_id)


@pytest.mark.parametrize(
    "path, content_type, body",
    [
        pytest.param("table.csv", "text/csv; charset=utf-8", A_TABLE, id="top-level-file"),
        pytest.param("figs/plot.png", "image/png", PNG_BYTES, id="folder-member"),
        pytest.param("figs/logo.svg", "image/svg+xml", SVG_BYTES, id="svg"),
        pytest.param("figs/cells.parquet", "application/octet-stream", b"PAR1",
                     id="download-only"),
    ],
)
@workflow(
    purpose="The file route types an artifact from its displayed name, never its "
            "cache entry, names it for a save, and sandboxes an SVG so its script "
            "cannot run",
)
def test_file_route_types_the_displayed_name_and_sandboxes_svg(
    canvas_client, typed_outputs_run, path, content_type, body
):
    resp = canvas_client.get(f"/api/wfc/run/{typed_outputs_run}/artifact/{path}")

    assert resp.status_code == 200, resp.text
    assert resp.content == body
    assert resp.headers["content-type"] == content_type
    # The browser is held to that type, never sniffing another.
    assert resp.headers["x-content-type-options"] == "nosniff"
    # Shown in the tab, saved under the displayed name.
    assert resp.headers["content-disposition"] == (
        f'inline; filename="{path.rsplit("/", 1)[-1]}"')
    csp = resp.headers.get("content-security-policy")
    assert csp == ("sandbox" if path.endswith(".svg") else None)


def test_every_viewable_extension_has_its_own_media_type():
    """A name some viewer opens never falls back to octet-stream; the rest do."""
    from wfc.canvas.previews import media_type, viewer_kind
    from wfc.canvas.previews import (BROWSER_IMAGE_EXTENSIONS, CONVERTED_IMAGE_EXTENSIONS,
                                     JSON_EXTENSIONS, PDF_EXTENSIONS, TABLE_EXTENSIONS,
                                     TEXT_EXTENSIONS)

    viewable = (BROWSER_IMAGE_EXTENSIONS | CONVERTED_IMAGE_EXTENSIONS | TABLE_EXTENSIONS
                | JSON_EXTENSIONS | TEXT_EXTENSIONS | PDF_EXTENSIONS)
    for ext in viewable:
        for name in (f"x.{ext}", f"dir/X.{ext.upper()}"):
            assert viewer_kind(name) is not None
            assert media_type(name) != "application/octet-stream", name
    images = {ext for ext in viewable if viewer_kind(f"x.{ext}") == "image"}
    assert all(media_type(f"x.{ext}").startswith("image/") for ext in images)
    text = TEXT_EXTENSIONS
    assert all(media_type(f"x.{ext}") == "text/plain" for ext in text)
    for name in ("cells.parquet", "adata.h5ad", "page.html", "LICENSE", ".env"):
        assert media_type(name) == "application/octet-stream", name


@pytest.mark.parametrize(
    "file_types, expected_entries",
    [
        pytest.param(None, {"table.csv": A_TABLE, "stats.json": A_STATS,
                            "table_{b8}.csv": B_TABLE}, id="all-files"),
        pytest.param(["csv"], {"table.csv": A_TABLE, "table_{b8}.csv": B_TABLE},
                     id="csv-only"),
    ],
)
@workflow(
    purpose="The export zip names each entry method/run name/artifact with unsafe "
            "characters replaced, suffixes a clashing entry with the run id's "
            "first eight characters, and honors the file-type filter",
)
def test_export_zip_names_sanitized_entries_and_suffixes_clashes(
    canvas_client, archived_runs, file_types, expected_entries
):
    _, a_id, b_id = archived_runs

    resp = canvas_client.post("/api/wfc/export-artifacts",
                              json={"run_ids": [a_id, b_id], "file_types": file_types})

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    prefix = "regionprops/regionprops_s_1/"
    assert entries == {
        prefix + name.format(b8=b_id[:8]): content
        for name, content in expected_entries.items()
    }


@workflow(purpose="The export route answers 404 when the file-type filter matches nothing")
def test_export_refuses_a_filter_matching_nothing(canvas_client, archived_runs):
    _, a_id, b_id = archived_runs

    resp = canvas_client.post("/api/wfc/export-artifacts",
                              json={"run_ids": [a_id, b_id], "file_types": ["png"]})

    assert resp.status_code == 404, resp.text


@workflow(
    purpose="The export preview counts the files, runs and methods, sums the size "
            "overall and by extension, sorts the methods and lists every file",
)
def test_export_preview_counts_sizes_and_lists_files(canvas_client, archived_runs):
    _, a_id, b_id = archived_runs

    resp = canvas_client.post("/api/wfc/preview-artifacts", json={"run_ids": [a_id, b_id]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {k: v for k, v in body.items() if k != "files"} == {
        "total_count": 3,
        "run_count": 2,
        "method_count": 1,
        "total_size_bytes": len(A_TABLE) + len(A_STATS) + len(B_TABLE),
        "methods": ["regionprops"],
        "by_type": [
            {"ext": "csv", "count": 2, "size_bytes": len(A_TABLE) + len(B_TABLE)},
            {"ext": "json", "count": 1, "size_bytes": len(A_STATS)},
        ],
    }
    run_name = "regionprops/s:1"
    assert sorted(body["files"], key=lambda f: (f["size_bytes"], f["artifact"])) == [
        {"method": "regionprops", "run_name": run_name, "artifact": "stats.json",
         "ext": "json", "size_bytes": len(A_STATS)},
        {"method": "regionprops", "run_name": run_name, "artifact": "table.csv",
         "ext": "csv", "size_bytes": len(A_TABLE)},
        {"method": "regionprops", "run_name": run_name, "artifact": "table.csv",
         "ext": "csv", "size_bytes": len(B_TABLE)},
    ]
