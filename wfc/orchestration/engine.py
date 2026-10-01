"""The hand-off: run a loaded pipeline through Snakemake and report the outcome.

One workflow, :func:`invoke_engine`: emit the Snakefile for the pipeline,
write it (to the pipeline's log directory unless the caller names a path),
launch Snakemake with the launching interpreter, register the live process
with the caller's registry, wait, and report an :class:`EngineOutcome`: the
exit code, the captured stderr when output was captured, and the failure
message text. The message is this unit's (it names the engine); what to do
about a failure is the caller's -- Execution's lifecycle raises it.

This module imports the emitter beside it and the standard library. It
calls nothing in Execution and touches no database.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from axiom_annotations import AutoStep, Step, workflow

from ..graph import PipelineDef
from .snakemake import generate_snakefile


class EngineOutcome(NamedTuple):
    """What one engine run ended with.

    Attributes:
        exit_code: Snakemake's exit code (0: every scheduled job completed).
        stderr: The captured stderr log's content when the caller captured
            output; ``None`` otherwise.
        message: The failure message on a non-zero exit: the engine's name,
            the exit code, the log directory, then the captured stderr on a
            new line when output was captured. ``None`` on exit 0.
    """

    exit_code: int
    stderr: str | None
    message: str | None


@workflow(
    purpose="Hand a loaded pipeline to Snakemake: emit and write its Snakefile, "
            "launch the engine, register the live process, wait, and report "
            "the outcome",
    inputs="PipelineDef, project root, the launch's pipeline id, log dir and "
           "frozen document, the samples' content hashes, cores, an optional "
           "Snakefile path, the capture and keep-going switches, an optional "
           "process registry",
    outputs="EngineOutcome: exit code, captured stderr, failure message",
)
def invoke_engine(
    pipeline: PipelineDef,
    project_root: str,
    *,
    pipeline_id: str,
    log_dir: Path,
    frozen_doc: Path,
    sample_hashes: dict[str, str] | None = None,
    cores: int = 4,
    snakefile_path: str | None = None,
    capture_output: bool = False,
    keep_going: bool = False,
    process_registry: Callable[[subprocess.Popen], None] | None = None,
) -> EngineOutcome:
    """Run one pipeline through Snakemake and report how it ended.

    Args:
        pipeline: The loaded pipeline the Snakefile is emitted for.
        project_root: The resolved wfc project root: the engine's cwd and
            the root the emitted file anchors at.
        pipeline_id: The launch's pipeline id, forwarded to the file and to
            the engine's environment.
        log_dir: The pipeline's log directory: where the Snakefile goes by
            default, where captured output is written, and what the failure
            message names.
        frozen_doc: The frozen (substituted) pipeline document; the emitted
            rules hand it to ``wfc run-step`` as ``WFC_PIPELINE_JSON``.
        sample_hashes: The samples' content hashes the composer read, for
            the restore rule.
        cores: Snakemake's ``--cores``.
        snakefile_path: Where to write the Snakefile. The caller's path wins
            when given; otherwise ``<log_dir>/Snakefile``.
        capture_output: When True, redirect the engine's stdout and stderr
            to ``stdout.log`` and ``stderr.log`` under ``log_dir``.
        keep_going: When True, pass ``--keep-going`` so a failed job does
            not cancel the jobs that do not depend on it.
        process_registry: Optional callback handed the live ``Popen`` so an
            in-process caller can cancel it; an error it raises is reported
            on stderr, not raised.

    Returns:
        The engine's outcome; ``message`` is ``None`` on exit 0.
    """
    口 = AutoStep(step_num=1, name="Emit the Snakefile")
    # The generated rules hand `wfc run-step` no --params: it reads the
    # node's params from WFC_PIPELINE_JSON, so that must be the frozen
    # (substituted) document, never the caller's file with its refs.
    content = generate_snakefile(
        pipeline, project_root, pipeline_id=pipeline_id,
        pipeline_json_path=str(frozen_doc.resolve()),
        sample_hashes=sample_hashes,
    )

    口 = Step(step_num=2, name="Write the Snakefile",
             purpose="Serialize the emitted text to the caller's path when "
                     "given, else to <log_dir>/Snakefile",
             inputs="Emitted Snakefile text",
             outputs="The Snakefile on disk at sf_path")
    sf_path = Path(snakefile_path) if snakefile_path else log_dir / "Snakefile"
    sf_path.write_text(content, encoding="utf-8")
    print(f"Snakefile written to {sf_path}")

    口 = Step(step_num=3, name="Launch Snakemake",
             purpose="Spawn `<this interpreter> -m snakemake --cores N "
                     "--snakefile <sf_path> [--keep-going]` in the project "
                     "root, with the pipeline's id and log dir (and "
                     "DATABASE_URL when set) in its environment; redirect its "
                     "output to the log files when the caller captures",
             inputs="sf_path, project root, cores, the switches",
             outputs="The live Popen",
             critical="Popen + wait, never subprocess.run: the caller's "
                      "registry must be able to terminate the live process")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # Pass pipeline log dir so generated Snakefile can find it
    env["WFC_PIPELINE_ID"] = pipeline_id
    env["WFC_PIPELINE_LOG_DIR"] = str(log_dir)
    # Explicitly forward DATABASE_URL so isolated test databases (set via
    # monkeypatch.setenv) are always seen by Snakemake worker processes even
    # when the env-var inheritance chain is broken (e.g. CI, --forked workers).
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        env["DATABASE_URL"] = db_url
    # The launching interpreter runs Snakemake, so the {sys.executable} the
    # generated rules and handlers name is this same program: the run needs
    # no `snakemake` or `wfc` on PATH.
    argv = [
        sys.executable, "-m", "snakemake", "--cores", str(cores),
        "--snakefile", str(sf_path),
    ]
    if keep_going:
        argv.append("--keep-going")
    # Capture stdout/stderr to log files only when requested (canvas server).
    # CLI users get normal terminal output (capture_output=False by default).
    stderr_log = log_dir / "stderr.log"
    with contextlib.ExitStack() as logs:
        out_f = err_f = None
        if capture_output:
            out_f = logs.enter_context(
                open(log_dir / "stdout.log", "w", encoding="utf-8"))
            err_f = logs.enter_context(open(stderr_log, "w", encoding="utf-8"))
        proc = subprocess.Popen(
            argv, cwd=project_root, env=env, stdout=out_f, stderr=err_f,
        )

        口 = Step(step_num=4, name="Register the live process",
                 purpose="Hand the Popen to the caller's registry (the canvas "
                         "cancel endpoint's handle); a registry that raises "
                         "is reported on stderr and the run goes on")
        if process_registry is not None:
            try:
                process_registry(proc)
            except Exception as reg_exc:
                print(
                    f"[run_pipeline] process_registry raised: {reg_exc}",
                    file=sys.stderr,
                )

        口 = Step(step_num=5, name="Wait for the engine",
                 purpose="Block until Snakemake exits; the log files stay "
                         "open until then")
        proc.wait()
        exit_code = proc.returncode

    口 = Step(step_num=6, name="Report the outcome",
             purpose="Read the captured stderr when output was captured; on a "
                     "non-zero exit build the failure message (the engine's "
                     "name, the exit code, the log directory, then the "
                     "captured stderr on a new line)",
             outputs="EngineOutcome")
    stderr = None
    if capture_output:
        stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
    message = None
    if exit_code != 0:
        message = f"Snakemake pipeline failed (exit {exit_code}). See logs in {log_dir}."
        if stderr is not None:
            message = f"{message}\n{stderr}"
    return EngineOutcome(exit_code=exit_code, stderr=stderr, message=message)
