"""The input-identity fetch beside ``pre_run`` -- Execution's witnesses.

``wfc.execution.claim.input_fingerprint_from_rows`` is the one reader of Run
and Sample rows for the input fingerprint: it reads a parsed parent entry's
run row and a sample by name, refuses an input that resolves to no row,
resolves an entry that names no output to the upstream's one recorded output,
and hands the slot-qualified identities to ``wfc.identity``. The tests here
assert what it reads, through rows built from the models, against literal
digests -- the same part spellings the pure witnesses in
``tests/test_identity.py`` pin at the function. They are Execution's tests:
the claim phase owns the fetch.
"""

import hashlib

import pytest
from sqlmodel import select

from axiom_annotations import workflow

from wfc.execution.claim import input_fingerprint_from_rows
from wfc.execution.parents import ParentEntry
from wfc.persistence import get_session, Method, Module, Run, RunOutput, Sample


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _seed_method() -> int:
    with get_session() as session:
        mod = Module(name="fetch_mod")
        session.add(mod)
        session.commit()
        session.refresh(mod)
        meth = Method(name="fetch_meth", module_id=mod.id, env="container:demo")
        session.add(meth)
        session.commit()
        session.refresh(meth)
        return meth.id  # type: ignore[return-value]


@workflow(
    purpose="The fetch reads an upstream Run row's cache key into "
            "key:<input>:<source>:<cache_key>, renders a row with no cache key as "
            "the legacy-run sentinel, refuses an entry naming a run with no row, "
            "and never reads RunOutput.content_hash"
)
def test_fetch_reads_run_rows(tmp_project):
    method_id = _seed_method()
    key = "0123456789abcdef" * 4
    with get_session() as session:
        keyed = Run(method_id=method_id, sample="s", status="completed", cache_key=key)
        unkeyed = Run(method_id=method_id, sample="s", status="completed")
        session.add(keyed)
        session.add(unkeyed)
        session.commit()
        session.refresh(keyed)
        session.refresh(unkeyed)
        keyed_id, unkeyed_id = keyed.id, unkeyed.id
        session.add(RunOutput(
            run_id=keyed_id, output_name="out.parquet", slot="merged",
            artifact_path="/fake/out.parquet", artifact_type="module_file",
            content_hash="f" * 32,
        ))
        session.commit()

    keyed_entry = ParentEntry("data", "merged", keyed_id)
    assert input_fingerprint_from_rows([keyed_entry], step="n1") \
        == _sha(f"key:data:merged:{key}")
    assert input_fingerprint_from_rows(
        [ParentEntry("data", "merged", unkeyed_id)], step="n1",
    ) == _sha(f"key:data:merged:legacy-run-{unkeyed_id}")

    # An entry naming a run with no row is refused, naming the step, the slot
    # and the run -- a skipped input would leave the key blind to it.
    with pytest.raises(ValueError) as exc:
        input_fingerprint_from_rows(
            [keyed_entry, ParentEntry("ref", "merged", 999_999)], step="n1",
        )
    assert "999999" in str(exc.value) and "n1" in str(exc.value)

    # No inputs at all is the empty part list -- the digest stays total; the
    # refusal is the caller's.
    assert input_fingerprint_from_rows([], step="n1") == _sha("")

    # RunOutput.content_hash is archival-only: dropping it leaves the key untouched.
    with get_session() as session:
        ro = session.exec(select(RunOutput).where(RunOutput.run_id == keyed_id)).first()
        ro.content_hash = None
        session.add(ro)
        session.commit()
    assert input_fingerprint_from_rows([keyed_entry], step="n1") \
        == _sha(f"key:data:merged:{key}")


@workflow(
    purpose="An entry that names no output resolves to the upstream's one "
            "recorded output, so the two spellings of one wiring give one "
            "fingerprint; an upstream with several recorded outputs, or none, "
            "is refused naming the run and its outputs"
)
def test_unnamed_source_slot_resolves_to_the_one_recorded_output(tmp_project):
    method_id = _seed_method()
    key = "abc" * 21 + "d"
    with get_session() as session:
        single = Run(method_id=method_id, sample="s", status="completed", cache_key=key)
        several = Run(method_id=method_id, sample="s", status="completed", cache_key=key)
        bare = Run(method_id=method_id, sample="s", status="completed", cache_key=key)
        session.add_all([single, several, bare])
        session.commit()
        for run in (single, several, bare):
            session.refresh(run)
        single_id, several_id, bare_id = single.id, several.id, bare.id
        session.add(RunOutput(
            run_id=single_id, output_name="merged.csv", slot="merged",
            artifact_path="/fake/merged.csv", artifact_type="module_file",
        ))
        session.add_all([
            RunOutput(run_id=several_id, output_name="merged.csv", slot="merged",
                      artifact_path="/fake/merged.csv", artifact_type="module_file"),
            RunOutput(run_id=several_id, output_name="qc.csv", slot="qc",
                      artifact_path="/fake/qc.csv", artifact_type="module_file"),
        ])
        session.commit()

    # `data:<id>` and `data:merged:<id>` are two spellings of one wiring.
    assert input_fingerprint_from_rows(
        [ParentEntry("data", None, single_id)], step="n1",
    ) == input_fingerprint_from_rows(
        [ParentEntry("data", "merged", single_id)], step="n1",
    ) == _sha(f"key:data:merged:{key}")

    for run_id in (several_id, bare_id):
        with pytest.raises(ValueError) as exc:
            input_fingerprint_from_rows(
                [ParentEntry("data", None, run_id)], step="n1",
            )
        assert str(run_id) in str(exc.value)


@workflow(
    purpose="The fetch reads a Sample row by name into hash:<input>:<md5>, "
            "refuses a name with no row, and — as a backstop, since pipeline "
            "start already refused it — raises rather than keying a row that "
            "carries no content hash"
)
def test_fetch_reads_sample_rows(tmp_project):
    with get_session() as session:
        hashed = Sample(
            name="hashed", source_path="/x.csv",
            registered_path="data/samples/hashed/x.csv", file_type="csv",
            file_size=100, file_mtime=1.0, content_hash="a" * 32,
        )
        malformed = Sample(
            name="malformed", source_path="/malformed.csv",
            registered_path="data/samples/malformed/malformed.csv",
            file_type="csv", file_size=500, file_mtime=42.0, content_hash=None,
        )
        session.add_all([hashed, malformed])
        session.commit()

    assert input_fingerprint_from_rows([], [("raw", "hashed")], step="n1") \
        == _sha("hash:raw:" + "a" * 32)

    # A row with no content hash has no weaker identity to be keyed on. It
    # is refused at pipeline start (load_sample_hashes); reaching here means
    # a caller bypassed that, and the alphabet still will not invent a part.
    with pytest.raises(ValueError, match="no content hash"):
        input_fingerprint_from_rows([], [("raw", "malformed")], step="n1")

    # The input slot is part of the identity: the same sample read into a
    # different slot is a different fingerprint.
    assert input_fingerprint_from_rows([], [("raw", "hashed")], step="n1") \
        != input_fingerprint_from_rows([], [("ref", "hashed")], step="n1")

    # A name with no row is refused, naming the step and the sample.
    with pytest.raises(ValueError) as exc:
        input_fingerprint_from_rows(
            [], [("raw", "hashed"), ("ref", "nosuch")], step="n1",
        )
    assert "nosuch" in str(exc.value) and "n1" in str(exc.value)
