"""The engine hand-off's own witness.

``invoke_engine`` is called directly, with ``subprocess.Popen`` stubbed, over
an empty pipeline the Graph unit loads (no database: the hand-off reads
none). It emits and writes the Snakefile, launches the engine, registers the
live process, waits, and returns an ``EngineOutcome``. It raises nothing and
calls no lifecycle function: the failure message is its text; what to do
about it is the caller's.
"""
from __future__ import annotations

import sys

from axiom_annotations import workflow

from tests.fixtures.fakes import fake_engine_process, stub_record_writers

from wfc.graph.load import load_pipeline
from wfc.orchestration import EngineOutcome, invoke_engine

EMPTY_DOCUMENT = {"nodes": [], "links": [], "samples": []}
ENGINE_STDERR = "MissingInputException in rule all\n"


def _launch_dir(tmp_path):
    """An empty pipeline, its log directory and a frozen document to point at."""
    pipeline = load_pipeline(EMPTY_DOCUMENT, contract_map={}, reference_outputs={})
    log_dir = tmp_path / ".runs" / "pipelines" / "pipe-handoff"
    log_dir.mkdir(parents=True)
    frozen = log_dir / "pipeline.json"
    frozen.write_text("{}", encoding="utf-8")
    return pipeline, log_dir, frozen



@workflow(
    purpose="Called directly with Popen stubbed to exit 1 and output captured, "
            "the hand-off returns an outcome carrying the exit code, the "
            "captured stderr and the failure message, raises nothing, and "
            "calls no lifecycle function (Tier 2)",
)
def test_failed_run_returns_the_outcome_without_raising(tmp_path, monkeypatch):
    pipeline, log_dir, frozen = _launch_dir(tmp_path)
    spawned: dict = {}
    registered: list = []
    finished: list[str] = []

    def launch(argv, **kwargs):
        spawned["argv"] = argv
        spawned.update(kwargs)
        # What the engine would have written to the captured stderr log.
        kwargs["stderr"].write(ENGINE_STDERR)

    stub_record_writers(
        monkeypatch,
        finish_failed=lambda *a, **k: finished.append("failed"),
        finish_succeeded=lambda *a, **k: finished.append("succeeded"),
    )
    with fake_engine_process(returncode=1, launch=launch) as popen:
        outcome = invoke_engine(
            pipeline, str(tmp_path), pipeline_id="pipe-handoff", log_dir=log_dir,
            frozen_doc=frozen, sample_hashes={}, cores=2, capture_output=True,
            keep_going=True, process_registry=registered.append,
        )

    assert isinstance(outcome, EngineOutcome)
    assert outcome.exit_code == 1
    assert outcome.stderr == ENGINE_STDERR
    assert outcome.message.startswith("Snakemake pipeline failed (exit 1)")
    assert str(log_dir) in outcome.message
    assert outcome.message.endswith("\n" + ENGINE_STDERR)
    assert finished == []

    assert popen.call_count == 1
    (proc,) = registered
    assert proc.returncode == 1
    assert proc.wait.call_count == 1
    assert spawned["argv"][:3] == [sys.executable, "-m", "snakemake"]
    assert spawned["argv"][3:] == [
        "--cores", "2", "--snakefile", str(log_dir / "Snakefile"), "--keep-going",
    ]
    assert spawned["cwd"] == str(tmp_path)
    assert spawned["env"]["WFC_PIPELINE_ID"] == "pipe-handoff"
    assert spawned["env"]["WFC_PIPELINE_LOG_DIR"] == str(log_dir)
    assert 'PIPELINE_ID = "pipe-handoff"' in (log_dir / "Snakefile").read_text(encoding="utf-8")


@workflow(
    purpose="On exit 0 with output left on the terminal, the outcome carries "
            "the exit code and no message; the caller's Snakefile path wins "
            "over the log directory (Tier 2)",
)
def test_completed_run_carries_no_message(tmp_path):
    pipeline, log_dir, frozen = _launch_dir(tmp_path)
    custom = tmp_path / "custom.smk"

    with fake_engine_process() as popen:
        outcome = invoke_engine(
            pipeline, str(tmp_path), pipeline_id="pipe-handoff", log_dir=log_dir,
            frozen_doc=frozen, snakefile_path=str(custom),
        )

    assert outcome == EngineOutcome(exit_code=0, stderr=None, message=None)
    argv = popen.call_args[0][0]
    assert argv[3:] == ["--cores", "4", "--snakefile", str(custom)]
    assert popen.call_args[1]["stdout"] is None
    assert custom.exists()
    assert not (log_dir / "Snakefile").exists()
