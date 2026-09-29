"""Canvas API route tests: the method registration and the Registry tab's sample list.

Catalog cases in ``docs/system/canvas-api/catalog.json``: ``registry-method-write``
and ``registry-samples-list``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from axiom_annotations import workflow
from sqlmodel import Session

from tests.fixtures.fakes import stub_registry_seam
from wfc.canvas.routes import registry as registry_routes
from wfc.persistence import Method, Module, Run, Sample

MODULE = "demo"


@pytest.fixture
def registration_calls(canvas_db, tmp_project, monkeypatch):
    """A registered module, three candidate directories, and a recording registration seam.

    Lays out ``good/`` (a folder with ``method.yaml``), ``no_yaml/`` (a folder
    without one) and ``a_file.txt`` under the project root, and replaces
    ``_register_method_fn`` with a recorder.

    Returns:
        The list each registration call's keyword arguments are appended to.
        A test that needs registration to raise sets ``calls.raises``.
    """
    with Session(canvas_db) as session:
        session.add(Module(name=MODULE))
        session.commit()
    (tmp_project / "good").mkdir()
    (tmp_project / "good" / "method.yaml").write_text("name: good\n", encoding="utf-8")
    (tmp_project / "no_yaml").mkdir()
    (tmp_project / "a_file.txt").write_text("not a folder\n", encoding="utf-8")

    class _Calls(list):
        raises: Exception | None = None

    calls = _Calls()

    def fake_register_method(**kwargs):
        calls.append(kwargs)
        if calls.raises is not None:
            raise calls.raises

    stub_registry_seam(monkeypatch, "_register_method_fn", fake_register_method)
    return calls


@pytest.mark.parametrize(
    "request_body, failing_label, detail",
    [
        pytest.param({"directory": "missing", "module": MODULE}, "directory exists",
                     "not found: missing", id="missing-directory"),
        pytest.param({"directory": "a_file.txt", "module": MODULE}, "directory is a folder",
                     "a_file.txt is a file, not a directory", id="a-file"),
        pytest.param({"directory": "no_yaml", "module": MODULE}, "method.yaml present",
                     "missing method.yaml in no_yaml", id="no-method-yaml"),
        pytest.param({"directory": "good", "module": "ghost"}, "module registered",
                     "'ghost' is not a registered module", id="unregistered-module"),
    ],
)
@workflow(
    purpose="A method registration whose pre-check fails answers 200 with ok false "
            "and the failing check's label and detail, and registers nothing",
)
def test_method_registration_stops_at_a_failing_pre_check(
    canvas_client, registration_calls, request_body, failing_label, detail
):
    resp = canvas_client.post("/api/registry/methods", json=request_body)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert {"status": "fail", "label": failing_label, "detail": detail} in body["preChecks"]
    assert registration_calls == []


PASSING_CHECKS = [
    {"status": "ok", "label": "directory exists"},
    {"status": "ok", "label": "method.yaml present"},
    {"status": "ok", "label": "module registered"},
]


@pytest.mark.parametrize(
    "query, raises, expected_status, expected_body, registers",
    [
        pytest.param("?dryRun=true", None, 200,
                     {"ok": True, "preChecks": PASSING_CHECKS}, False, id="dry-run"),
        pytest.param("", None, 200,
                     {"ok": True, "preChecks": PASSING_CHECKS, "method": {"name": "good"}},
                     True, id="write"),
        pytest.param("", FileNotFoundError("no script"), 404, {"detail": "no script"},
                     True, id="registration-not-found"),
        pytest.param("", ValueError("bad contract"), 400, {"detail": "bad contract"},
                     True, id="registration-invalid"),
    ],
)
@workflow(
    purpose="A method registration that passes its pre-checks returns them without "
            "writing on a dry run, otherwise calls registration and names the "
            "method, and maps registration's FileNotFoundError to 404 and "
            "ValueError to 400",
)
def test_method_registration_dry_runs_or_writes_after_passing_pre_checks(
    canvas_client, registration_calls, query, raises, expected_status, expected_body,
    registers,
):
    registration_calls.raises = raises

    resp = canvas_client.post(f"/api/registry/methods{query}",
                              json={"directory": "good", "module": MODULE})

    assert resp.status_code == expected_status, resp.text
    assert resp.json() == expected_body
    expected_calls = (
        [{"method_dir": Path("good"), "module_name": MODULE, "method_name": None}]
        if registers else []
    )
    assert registration_calls == expected_calls


@workflow(
    purpose="The Registry tab's sample list is sorted by name, counts each sample's "
            "runs, marks a sample pushed only when it has a content hash, and "
            "renders registered_at in ISO form",
)
def test_registry_sample_list_is_sorted_with_run_counts_and_push_state(
    canvas_client, canvas_db
):
    registered = datetime(2026, 1, 2, 3, 4, 5)
    with Session(canvas_db) as session:
        mod = Module(name=MODULE)
        session.add(mod)
        session.flush()
        meth = Method(name="clean", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.flush()
        session.add_all([
            Sample(name="beta", source_path="/raw/beta.csv",
                   registered_path="data/samples/beta/beta.csv", file_type="csv",
                   file_size=20, content_hash="0" * 32, registered_at=registered),
            Sample(name="alpha", source_path="/raw/alpha.tif",
                   registered_path="data/samples/alpha/alpha.tif", file_type="tif",
                   file_size=10, registered_at=registered),
            Run(method_id=meth.id, sample="alpha", status="completed"),
            Run(method_id=meth.id, sample="alpha", status="failed"),
            Run(method_id=meth.id, sample="beta", status="completed"),
        ])
        session.commit()

    resp = canvas_client.get("/api/registry/samples")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"samples": [
        {"name": "alpha", "source": "/raw/alpha.tif", "size": 10, "hash": None,
         "pushed": False, "runCount": 2, "registered_at": "2026-01-02T03:04:05",
         "file_type": "tif"},
        {"name": "beta", "source": "/raw/beta.csv", "size": 20, "hash": "0" * 32,
         "pushed": True, "runCount": 1, "registered_at": "2026-01-02T03:04:05",
         "file_type": "csv"},
    ]}
