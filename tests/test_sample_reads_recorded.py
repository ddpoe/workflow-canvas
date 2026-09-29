"""The claim records every sample a step reads, beside its parent rows.

A sample row is a ``run_inputs`` row with no source run: ``input_name`` is
the slot the sample was read into, ``sample_name`` and ``content_hash`` say
which sample and which content. The claim writes one per ``(slot, sample)``
the step's cache key is over, on a new run and on a cache-hit audit row.
"""

from sqlmodel import select

from tests.fixtures.routes import completed_run
from wfc.contracts.vocabulary import COLLAPSED_SAMPLE
from wfc.execution.claim import pre_run
from wfc.persistence import RunInput, Sample, get_session


def _input_rows(run_id: int) -> list[tuple]:
    """Return a run's input rows as ``(slot, source run, sample, hash)``."""
    with get_session() as session:
        rows = session.exec(
            select(RunInput).where(RunInput.run_id == run_id)
        ).all()
        return sorted(
            (r.input_name, r.source_run_id, r.sample_name, r.content_hash)
            for r in rows
        )


def _content_hash(sample_name: str) -> str:
    """Return a registered sample's content hash."""
    with get_session() as session:
        return session.exec(
            select(Sample).where(Sample.name == sample_name)
        ).one().content_hash


def test_root_run_and_its_cache_hit_audit_record_the_sample_read(
        tmp_project, monkeypatch):
    """A root step records the sample it read, with the slot and the hash its
    key is over; the audit row of a later cache hit records the same read."""
    first = completed_run(tmp_project, monkeypatch=monkeypatch,
                          method="sr_root", module="sr_mod", sample="sr_a")
    expected = [("data", None, "sr_a", _content_hash("sr_a"))]
    assert _input_rows(first.run_id) == expected

    again = completed_run(tmp_project, monkeypatch=monkeypatch,
                          method="sr_root", module="sr_mod", sample="sr_a")
    assert again.run_id != first.run_id
    assert again.run_row["cache_source_run_id"] == first.run_id
    assert _input_rows(again.run_id) == expected


def test_collapsed_root_records_one_sample_row_per_bundled_sample(
        tmp_project, monkeypatch):
    """A collapsed fan-in root records each bundled sample in the bundle
    slot, each with its own content hash, and no parent row."""
    completed_run(tmp_project, monkeypatch=monkeypatch, method="sr_bundle",
                  module="sr_mod", sample="sr_a", samples=["sr_a", "sr_b"])

    flag, run_id = pre_run(
        method_name="sr_bundle", module_name="sr_mod",
        sample=COLLAPSED_SAMPLE, collapsed_samples=["sr_b", "sr_a"],
        selector_slot="data", git_commit="c" * 40,
    )

    assert flag == "NEW"
    assert _input_rows(run_id) == [
        ("data", None, "sr_a", _content_hash("sr_a")),
        ("data", None, "sr_b", _content_hash("sr_b")),
    ]


def test_step_fed_by_an_upstream_records_only_the_parent_row(
        tmp_project, monkeypatch):
    """A step whose only input is an upstream run reads no sample, so its
    one row names the upstream and carries no sample."""
    upstream = completed_run(tmp_project, monkeypatch=monkeypatch,
                             method="sr_up", module="sr_mod", sample="sr_a",
                             outputs={"result": ".csv"})
    completed_run(tmp_project, monkeypatch=monkeypatch, method="sr_down",
                  module="sr_mod", sample="sr_a")

    flag, run_id = pre_run(
        method_name="sr_down", module_name="sr_mod", sample="sr_a",
        parent_run_ids=[f"data:result:{upstream.run_id}"], git_commit="c" * 40,
    )

    assert flag == "NEW"
    assert _input_rows(run_id) == [("data", upstream.run_id, None, None)]
