"""The composer's degradation policy: one session, one failure before the launch.

The composer (``load_pipeline_from_document``) opens one database session for
all its reads: the referenced runs' outputs and sample, the contract map and
the samples' content hashes. Two outcomes are pinned here:

- an unreachable database fails the load with one typed error that names the
  database with its credentials masked, before ``prepare_launch`` and before
  any engine process is spawned;
- a query that fails against a reachable database propagates as itself, not
  as "database unavailable".

The test environment carries no Postgres driver, so the unreachable database
is SQLite under a directory that does not exist, and the credential masking is
asserted on the error over a URL of the other shape the URL rule admits.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy.exc
from axiom_annotations import workflow
from sqlalchemy import text

from tests.fixtures.fakes import fake_engine_process, stub_pipeline_stage
from wfc.persistence import Sample, get_session, reset_engine
from wfc.execution import DatabaseUnreachableError, load_pipeline_from_document

#: A URL the rule admits (``DATABASE_URL`` is taken verbatim) that carries a
#: password; the error must name the host and database and not the password.
URL_WITH_PASSWORD = "postgresql+psycopg://wfc:s3cret@127.0.0.1:1/wfc"


def _document(with_reference: bool) -> dict:
    """One selector feeding one root method, optionally with a run_reference.

    Args:
        with_reference: Add a ``run_reference`` node feeding the method.
    """
    nodes = [
        {"id": "sel", "type": "input_selector", "fan_mode": "out",
         "samples": ["s1"]},
        {"id": "qc", "type": "method", "method": "qc", "module": "demo",
         "script": "methods/qc/qc.py", "env": "container:demo", "params": {}},
    ]
    links = [{"source": "sel", "target": "qc", "target_slot": "data"}]
    if with_reference:
        nodes.append({"id": "ref", "type": "run_reference", "run_id": "1"})
        links.append({"source": "ref", "target": "qc",
                      "source_slot": "table", "target_slot": "baseline"})
    return {"nodes": nodes, "links": links, "samples": ["s1"]}


@pytest.fixture
def unreachable_database(tmp_path, monkeypatch):
    """A ``DATABASE_URL`` nothing answers: SQLite under a missing directory."""
    url = f"sqlite:///{tmp_path.as_posix()}/no-such-dir/x.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    yield url
    reset_engine()


@workflow(
    purpose="With the database unreachable, run_pipeline's load fails with one "
            "typed error naming the database (credentials masked) before "
            "prepare_launch runs and before any engine process is spawned "
            "(Tier 2)",
)
def test_unreachable_database_fails_the_load_before_the_launch(
    unreachable_database, tmp_path, monkeypatch,
):
    from wfc.execution import run_pipeline

    doc_path = tmp_path / "pipeline.json"
    doc_path.write_text(json.dumps(_document(with_reference=True)),
                        encoding="utf-8")

    prepared = []
    stub_pipeline_stage(monkeypatch,
                        prepare_launch=lambda *a, **k: prepared.append(a))
    with fake_engine_process() as popen:
        with pytest.raises(DatabaseUnreachableError) as caught:
            run_pipeline(pipeline_path=str(doc_path), project_root=str(tmp_path),
                         pipeline_id="pipe-unreachable")

    assert prepared == []
    assert popen.call_count == 0
    message = str(caught.value)
    assert "no-such-dir/x.db" in message
    assert "not launched" in message
    assert isinstance(caught.value.__cause__, sqlalchemy.exc.OperationalError)

    masked = str(DatabaseUnreachableError(URL_WITH_PASSWORD))
    assert "s3cret" not in masked
    assert "127.0.0.1:1/wfc" in masked


@workflow(
    purpose="With a reachable database whose samples table has been dropped, "
            "the composer's load reports the query's own error, not an "
            "unreachable database (Tier 2)",
)
def test_failing_query_reports_itself(wfc_root):
    with get_session() as session:
        session.execute(text(f"DROP TABLE {Sample.__tablename__}"))
        session.commit()

    with pytest.raises(sqlalchemy.exc.OperationalError, match="no such table"):
        load_pipeline_from_document(_document(with_reference=False))
