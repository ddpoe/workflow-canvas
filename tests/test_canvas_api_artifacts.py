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
    staging = tmp_project / "staging"
    staging.mkdir()
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
