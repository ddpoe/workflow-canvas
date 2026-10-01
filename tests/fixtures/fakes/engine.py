"""Engine boundary: the method process, the Snakemake spawn and the phases.

Three levels of substitution, from the smallest outward: the container launch
inside dispatch (the harness's stub rung, which runs the node's script as a
real local subprocess), the Snakemake process itself (a ``Popen`` stand-in
that never spawns), and the phase functions ``run_step`` sequences (stubbed
only by tests about dispatch's own control flow).
"""

from __future__ import annotations

import subprocess as _sp
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from axiom_annotations import task

from ._entry import fake

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tests.harness.observe import TargetRun


@fake(
    boundary="wfc.execution.dispatch._run_method_subprocess -- the container "
             "launch, the one substitution the harness's stub rung makes",
    preserves="argv, env, a real local run of the node's generated script "
              "under the WFC_* environment dispatch built, and the real "
              "output files it writes; run-context write, image resolution, "
              "argv build and path translation all run for real",
    not_proven="runtime image resolution or the docker argv executing",
    backed_by="pm_mvp::tests.integration.test_containerized_step_runs::"
              "test_run_step_in_container_writes_output_to_host",
)
@task(purpose="Replace the container launch with a real local subprocess at "
              "the dispatch boundary — the one substitution the stub rung "
              "makes, and the only thing separating it from the engine rung",
      inputs="The built project, the step definition, and the fidelity rung",
      outputs="record.dispatch_cmd and record.dispatch_env, plus the real "
              "output files the node's script writes",
      critical="Everything else in dispatch runs for real — run-context "
               "write, container-image resolution, argv build, path "
               "translation. Producers write real files and readers read "
               "them, so the substitution is the process boundary only, never "
               "the data. ENGINE installs nothing at all")
@contextmanager
def stub_method_process(project, step, record: TargetRun, monkeypatch,
                        fidelity: str):
    """Fake the method process at the dispatch boundary only (stub rung).

    Producers write real files and readers read them: the replacement is a
    real local subprocess running the node's generated script under the
    ``WFC_*`` environment the dispatch phase built. Everything else in
    dispatch — run-context write, container-image resolution, argv build,
    path translation — runs for real.

    Args:
        project: The built project.
        step: The step definition being run.
        record: The target's execution record.
        monkeypatch: An active ``pytest.MonkeyPatch``.
        fidelity: ``STUB`` installs the stub; ``ENGINE`` installs nothing.

    Yields:
        None.
    """
    from tests.harness.drivers import STUB

    if fidelity != STUB:
        yield
        return

    from wfc.execution import dispatch as _dispatch

    script = project.method_scripts.get(step.method_name)
    node_id, target_sample, _variant = record.target
    spec = project.scenario.node_by_id(node_id)

    def fake_subprocess(cmd, *, cwd, env, stdout_log, stderr_log):
        record.dispatch_cmd = list(cmd)
        record.dispatch_env = dict(env)
        # The launch refusing is a boundary event, not something the
        # script can play: raise here, after dispatch has built the argv
        # and before any process exists, exactly where a real launch would
        # have raised.
        behavior = spec.behavior_for(env.get("WFC_SAMPLE", target_sample),
                                     env.get("WFC_VARIANT", _variant))
        if behavior.launch_error is not None:
            raise OSError(behavior.launch_error)
        proc = _sp.run(
            [sys.executable, str(script)],
            cwd=cwd, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        Path(stdout_log).parent.mkdir(parents=True, exist_ok=True)
        Path(stdout_log).write_text(proc.stdout or "", encoding="utf-8")
        Path(stderr_log).write_text(proc.stderr or "", encoding="utf-8")
        return _sp.CompletedProcess(args=cmd, returncode=proc.returncode,
                                    stdout=None, stderr=None)

    with monkeypatch.context() as mp:
        mp.setattr(_dispatch, "_run_method_subprocess", fake_subprocess)
        yield


@fake(
    boundary="subprocess.Popen (the Snakemake spawn in run_pipeline) and "
             "wfc.orchestration.engine.generate_snakefile",
    preserves="run_pipeline's own load, freeze, launch preparation and exit "
              "handling around the spawn; the process reports the given exit "
              "status",
    not_proven="Snakefile content or the engine's exit handling",
    backed_by="pm_mvp::tests.integration.test_containerized_pipeline_runs::"
              "test_two_node_container_pipeline_propagates_input_paths",
)
def mocked_snakemake(returncode: int):
    """Return the two patches that stand in for a Snakemake invocation.

    ``run_pipeline`` uses ``subprocess.Popen`` + ``.wait()`` (so the canvas
    cancel endpoint can SIGTERM the live process), so the fake covers Popen
    rather than ``subprocess.run``; ``generate_snakefile`` is stubbed to a
    placeholder body since the engine itself is not being exercised. A true
    external edge -- the process spawn -- and nothing deeper: the run state
    Snakemake would have left behind is produced by running the scenario's
    targets, never faked.

    Args:
        returncode: The exit status the fake Snakemake process reports.

    Returns:
        ``(generate_snakefile_patch, popen_patch)``, to be entered together.
    """
    fake_proc = MagicMock()
    fake_proc.wait.return_value = returncode
    fake_proc.returncode = returncode
    return (
        patch("wfc.orchestration.engine.generate_snakefile", return_value="# fake"),
        patch("subprocess.Popen", return_value=fake_proc),
    )


@fake(
    boundary="subprocess.Popen -- the engine process alone; Snakefile "
             "generation, the load, the freeze and launch preparation run "
             "for real",
    preserves="the argv and kwargs the launch passes (readable off the "
              "patch's call_args), the exit status, and -- with "
              "only_snakemake -- every non-snakemake command, which reaches "
              "the real Popen so a readiness probe can still shell out",
    not_proven="that the engine runs, or what it would have written",
    backed_by="pm_mvp::tests.integration.test_containerized_pipeline_runs::"
              "test_two_node_container_pipeline_propagates_input_paths",
)
def fake_engine_process(*, returncode: int = 0, launch=None,
                        only_snakemake: bool = False):
    """Return a ``subprocess.Popen`` patch whose process never spawns.

    Args:
        returncode: What ``wait()`` returns and ``returncode`` reports.
        launch: Optional ``launch(argv, **popen_kwargs)`` hook run at spawn
            time -- to write into the ``stderr`` handle the engine would
            have written to, say. Its return value is ignored.
        only_snakemake: When True, only an argv whose program is
            ``snakemake`` gets the fake; every other command goes to the
            real ``Popen``.

    Returns:
        The patch context. Enter it with ``with``; ``as popen`` gives the
        mock whose ``call_args`` carries the launch's argv and kwargs.
    """
    proc = MagicMock()
    proc.wait.return_value = returncode
    proc.returncode = returncode
    real_popen = _sp.Popen

    def spawn(cmd, *args, **kwargs):
        argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
        # The engine launches as ``<interpreter> -m snakemake``; a bare
        # ``snakemake`` program counts too.
        is_engine = bool(argv) and (argv[0] == "snakemake"
                                    or list(argv[1:3]) == ["-m", "snakemake"])
        if only_snakemake and not is_engine:
            return real_popen(cmd, *args, **kwargs)
        if launch is not None:
            launch(argv, **kwargs)
        return proc

    return patch("subprocess.Popen", side_effect=spawn)


@fake(
    boundary="wfc.execution.pipeline.prepare_launch and "
             "load_pipeline_from_document -- run_pipeline's load stage",
    preserves="everything before the stage: variable substitution, the "
              "database reachability check, the document read",
    not_proven="that the load produces a launchable pipeline; a stubbed "
               "loader's refusal is the test's own, not the loader's",
    backed_by="pm_mvp::tests.test_run_pipeline_variables::"
              "test_run_pipeline_substitutes_variables_before_the_load",
)
def stub_pipeline_stage(monkeypatch, *, prepare_launch=None,
                        load_document=None) -> None:
    """Replace one or both functions of run_pipeline's load stage.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        prepare_launch: Replacement for ``prepare_launch`` (a recorder that
            must never be reached, say). ``None`` leaves it real.
        load_document: Replacement for ``load_pipeline_from_document``
            (a callable raising the refusal under test). ``None`` leaves
            it real.
    """
    from wfc.execution import pipeline as pipeline_mod

    if prepare_launch is not None:
        monkeypatch.setattr(pipeline_mod, "prepare_launch", prepare_launch)
    if load_document is not None:
        monkeypatch.setattr(pipeline_mod, "load_pipeline_from_document",
                            load_document)


@fake(
    boundary="wfc.execution.record.complete_run / _write_outcome and "
             "wfc.execution.lifecycle.finish_failed / finish_succeeded -- "
             "the record phase's writers",
    preserves="whatever the replacement delegates to: a counting wrapper "
              "keeps the write, a refusing one keeps nothing",
    not_proven="that the run row and its outcome sidecar were written",
    backed_by="pm_mvp::tests.integration.test_containerized_step_runs::"
              "test_run_step_in_container_writes_output_to_host",
)
def stub_record_writers(monkeypatch, *, complete_run=None, write_outcome=None,
                        finish_failed=None, finish_succeeded=None) -> None:
    """Replace any of the four record-phase writers.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        complete_run: Replacement for ``record.complete_run``.
        write_outcome: Replacement for ``record._write_outcome``.
        finish_failed: Replacement for ``lifecycle.finish_failed``.
        finish_succeeded: Replacement for ``lifecycle.finish_succeeded``.
    """
    from wfc.execution import lifecycle, record

    for module, attr, replacement in (
        (record, "complete_run", complete_run),
        (record, "_write_outcome", write_outcome),
        (lifecycle, "finish_failed", finish_failed),
        (lifecycle, "finish_succeeded", finish_succeeded),
    ):
        if replacement is not None:
            monkeypatch.setattr(module, attr, replacement)


class _NullSession:
    """A session that finds nothing and writes nothing."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def exec(self, *a, **kw):
        class _R:
            def first(self):
                return None
        return _R()

    def add(self, *a, **kw):
        pass

    def commit(self):
        pass


@fake(
    boundary="the phase modules' claim (pre_run), record (complete_run), "
             "resolve_input, get_project_root and get_session -- everything "
             "run_step's phases read or write, stubbed together",
    preserves="dispatch's own control flow up to and including container "
              "resolution, over a project directory with no database",
    not_proven="anything a phase would have read from or written to the "
               "database; the run reaches the container-resolution branch "
               "and nothing else is real",
    backed_by="pm_mvp::tests.integration.test_containerized_step_runs::"
              "test_run_step_in_container_writes_output_to_host",
)
def stub_runtime_phases(monkeypatch, project_dir: Path) -> None:
    """Stub the phases around dispatch so run_step reaches container resolution.

    The justification: a test about dispatch refusing an env with no
    container record wants the refusal's message and exit code, and the
    claim and record phases on either side would need a database to say
    anything at all.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        project_dir: The project root every phase's ``get_project_root``
            reports.
    """
    import importlib

    from wfc.persistence import reset_engine

    monkeypatch.setenv("WFC_PROJECT_ROOT", str(project_dir))
    reset_engine()

    resolve_mod = importlib.import_module("wfc.storage.resolve")
    from wfc.execution import claim as claim_mod, record as record_mod

    phase = {name: importlib.import_module(f"wfc.execution.{name}")
             for name in ("claim", "materialize", "dispatch", "collect",
                          "record", "run_step")}
    monkeypatch.setattr(claim_mod, "pre_run", lambda **kw: ("NEW", 42))
    monkeypatch.setattr(record_mod, "complete_run", lambda **kw: None)
    for mod in phase.values():
        monkeypatch.setattr(mod, "get_project_root", lambda: project_dir)
    monkeypatch.setattr(resolve_mod, "resolve_input", lambda **kw: None)
    for name in ("claim", "collect", "record"):
        monkeypatch.setattr(phase[name], "get_session", lambda: _NullSession())
