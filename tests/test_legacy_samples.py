"""A sample row carrying no content hash is a malformed record, and is refused.

A ``Sample`` row with no ``content_hash`` is content-addressed storage
with no address: there is nothing to key a run on and nothing to restore
its bytes by. wfc neither converts such a row nor degrades around it —
the refusal names the sample and the one command that fixes it, and the
sample is re-registered. No migration, no conversion pass, no dual lookup.

This module asserts the two moments that refusal splits into — the earliest
one that sees the whole sample set (``load_sample_hashes``, at pipeline
start) and the restore itself — plus the emitted rule's ``hash_arg``, whose
flag-not-value construction is what keeps a malformed row's argv parseable
long enough for the refusal to be readable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from axiom_annotations import workflow

# seed_sample_row, not create_sample_csv, deliberately: these tests are about
# the malformed NULL-content_hash row, a deviation state production cannot
# produce — constructing it directly is the only way to reach the path.
from tests.conftest import seed_sample_row
from tests.fixtures.fakes import stub_pipeline_stage, stub_transport


def _hash_arg_lambda(snakefile: str, hashes: dict[str, str]):
    """Recover the restore rule's ``hash_arg`` params callable from the text.

    The emitted lambda is what actually decides the flag at run time, so the
    assertion reads it rather than a copy of it.

    Args:
        snakefile: The generated Snakefile text.
        hashes: The ``SAMPLE_HASHES`` table the rule closes over.

    Returns:
        The callable, taking a ``wildcards`` object with a ``sample``.
    """
    line = next(
        ln for ln in snakefile.splitlines() if ln.strip().startswith("hash_arg=")
    )
    return eval(line.strip()[len("hash_arg="):], {"SAMPLE_HASHES": hashes})


@workflow(purpose="A Sample row with no content hash is a malformed record: the "
                  "composer's table refuses it at pipeline start naming the "
                  "sample and the re-registration command, restore_sample "
                  "refuses the same row and writes no readiness sentinel, and "
                  "the table the emitter does get carries only real hashes, so "
                  "every emitted rule's argv names one")
def test_a_sample_row_with_no_content_hash_is_refused(tmp_project, capsys):
    from wfc import layout
    from wfc.graph import PipelineDef, StepDef
    from wfc.orchestration import generate_snakefile
    from wfc.persistence import get_session
    from wfc.registration.sample_hashes import load_sample_hashes
    from wfc.storage.restore import restore_sample

    seed_sample_row("modern_s")
    seed_sample_row("legacy_s", content_hash="")

    # 1. Pipeline start refuses. This is the earliest moment that sees the
    #    whole sample set, which is why the refusal lives here rather than
    #    per-target inside pre_run.
    with get_session() as session:
        with pytest.raises(ValueError) as exc:
            load_sample_hashes(["modern_s", "legacy_s"], session)
    message = str(exc.value)
    assert "legacy_s" in message
    assert "malformed record" in message
    assert "wfc register-sample --name legacy_s" in message

    # A set with no malformed row still loads, and every entry is a real hash.
    with get_session() as session:
        hashes = load_sample_hashes(["modern_s"], session)
    assert hashes["modern_s"] and "legacy_s" not in hashes

    # 2. The emitted rule builds the whole --hash FLAG rather than
    #    interpolating its value, so no argv can collapse to a bare --hash.
    pipeline = PipelineDef(
        steps=[StepDef(method_name="root", module_name="m",
                       script_path="methods/root/root.py", params={},
                       node_id="root", env="container:base",
                       selector_slot="data")],
        samples=["modern_s"],
        param_sets={},
    )
    snakefile = generate_snakefile(
        pipeline, project_root=str(tmp_project), pipeline_id="pipe-legacy",
        sample_hashes=hashes,
    )
    assert "rule restore_sample:" in snakefile
    hash_arg = _hash_arg_lambda(snakefile, hashes)
    assert hash_arg(SimpleNamespace(sample="modern_s")) == (
        "--hash " + hashes["modern_s"]
    )

    # 3. The restore refuses the same row and leaves no readiness sentinel:
    #    the run stops rather than proceeding over a sample wfc cannot
    #    materialize.
    with pytest.raises(SystemExit) as exit_info:
        restore_sample("legacy_s", project_root=tmp_project)
    assert exit_info.value.code == 1

    assert not layout.sample_ready_sentinel(tmp_project, "legacy_s").exists()
    err = capsys.readouterr().err
    assert "ERROR" in err and "legacy_s" in err, err
    assert "malformed record" in err, err
    assert "wfc register-sample --name legacy_s" in err, err


@workflow(purpose="`wfc run-pipeline` delivers the malformed-record refusal as "
                  "the verb's own one-line message and a non-zero return, not "
                  "as an exception escaping to the top level")
def test_run_pipeline_delivers_the_malformed_record_refusal_as_a_message(
    tmp_project, monkeypatch, capsys
):
    """The refusal's shape is the point, not just its text.

    ``load_sample_hashes`` runs inside step 1's load, one step BEFORE the
    sample-content preflight, so a hashless row never reaches the preflight
    at all. The verb catches ``UnreachableSampleError`` there and nothing
    else, so this refusal escaped ``cli_main`` and reached the user at the
    bottom of a stack — the one delivery the preflight was written to avoid.
    Returning 1 rather than raising is what a console entry point turns into
    a bare message and an exit code.
    """
    from wfc.cli import cli_main
    from wfc.persistence import get_session
    from wfc.registration import MalformedSampleError
    from wfc.registration.sample_hashes import load_sample_hashes

    seed_sample_row("legacy_s", content_hash="")

    # The refusal the composer's load actually raises, built by production
    # code over a real malformed row; only its arrival at the verb is staged.
    # Reaching it through a real document would need a registered method and
    # module, which is a registration test, not a delivery one.
    with get_session() as session:
        with pytest.raises(MalformedSampleError) as exc:
            load_sample_hashes(["legacy_s"], session)
    refusal = exc.value

    def _raise_on_load(document):
        raise refusal

    stub_pipeline_stage(monkeypatch, load_document=_raise_on_load)

    document = tmp_project / "legacy_pipeline.json"
    document.write_text(
        '{"name": "legacy", "nodes": [], "links": [], "samples": ["legacy_s"]}',
        encoding="utf-8",
    )

    rc = cli_main(["run-pipeline", "--pipeline", str(document),
                   "--project-root", str(tmp_project)])

    assert rc == 1, "the verb must report the refusal, not raise through it"
    err = capsys.readouterr().err
    assert "ERROR" in err and "legacy_s" in err, err
    assert "malformed record" in err, err
    assert "wfc register-sample --name legacy_s" in err, err
    assert "Traceback" not in err, err


@workflow(purpose="restore_sample tells 'the content is nowhere' apart from "
                  "'the archive could not be reached', and prescribes "
                  "re-registration only for the first")
def test_restore_tells_an_unreachable_archive_apart_from_missing_content(
    tmp_project, monkeypatch, capsys
):
    """An unreachable remote is not evidence that the bytes are gone.

    ``pull_cache`` returns ``False`` with only a stderr WARNING when no remote
    is configured, when DVC reports failures and on any exception. The restore
    discarded that answer, so a merely unreachable archive produced the
    message that says the content is in neither the cache nor the archive and
    told the user to re-register from a source they may no longer have.
    """
    from wfc.storage.restore import restore_sample

    seed_sample_row("absent_s", content_hash="f" * 32)

    # 1. The archive answered, and the content still is not here: the bytes
    #    really are nowhere, and re-registering is the repair.
    stub_transport(monkeypatch, pull_cache=lambda md5s, project_dir: True)
    with pytest.raises(SystemExit) as exit_info:
        restore_sample("absent_s", project_root=tmp_project)
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "nowhere wfc can reach" in err, err
    assert "wfc register-sample --name absent_s --source" in err, err

    # 2. The archive could NOT be asked. Same local state, different fact —
    #    and the repair is to fix the remote, not to re-register.
    stub_transport(monkeypatch, pull_cache=lambda md5s, project_dir: False)
    with pytest.raises(SystemExit) as exit_info:
        restore_sample("absent_s", project_root=tmp_project)
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "could not be reached" in err, err
    assert "nowhere wfc can reach" not in err, err
    assert "wfc register-sample" not in err, err
