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
    preserves="argv, env, a real local run of the script the argv launches "
              "(its /work path translated back to the host, so a run executes "
              "the version's extracted copy) under the WFC_* environment "
              "dispatch built, and the real "
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
      outputs="record.dispatch_cmd, record.dispatch_env and "
              "record.step_log_at_launch (the step log as it stood when the "
              "process started), plus the real output files the node's "
              "script writes",
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

    from wfc.layout.mounts import WORK_MOUNT

    generated = project.method_scripts.get(step.method_name)
    node_id, target_sample, _variant = record.target
    spec = project.scenario.node_by_id(node_id)

    def launched_script(cmd) -> Path:
        # The script the argv names, as the container would see it under the
        # project mount; the generated script only when the argv names none.
        prefix = WORK_MOUNT + "/"
        suffix = generated.suffix if generated is not None else ".py"
        for arg in cmd:
            if arg.startswith(prefix) and arg.endswith(suffix):
                return Path(project.root) / arg[len(prefix):]
        return generated

    def fake_subprocess(cmd, *, cwd, env, stdout_log, stderr_log):
        script = launched_script(cmd)
        record.dispatch_cmd = list(cmd)
        record.dispatch_env = dict(env)
        # The step log as the method process would find it at its start.
        if Path(stderr_log).exists():
            record.step_log_at_launch = Path(stderr_log).read_text(encoding="utf-8")
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
        # Appended, as the real launch does: dispatch started the step log
        # with wfc's warnings before the launch.
        with open(stderr_log, "a", encoding="utf-8") as err_f:
            err_f.write(proc.stderr or "")
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

    def execute(self, *a, **kw):
        pass

    def flush(self):
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
    monkeypatch.setattr(phase["claim"], "get_session", lambda: _NullSession())
    for name in ("collect", "record"):
        monkeypatch.setattr(phase[name], "write_transaction", lambda: _NullSession())


@fake(
    boundary="wfc.execution.run_step.run_claim -- wrapped to call the real "
             "claim through, then unlink the snapshot script it resolved, "
             "so the file is gone by dispatch",
    preserves="everything: the real claim (the resolver, the verify rule, "
              "the row and the cache key), dispatch's host-side existence "
              "check and the failed record tail",
    not_proven="that a script can vanish between claim and dispatch in a "
               "real run; only what dispatch does when it has",
    backed_by="undrivable: the claim's resolver refuses a missing script, "
              "so the only real route to dispatch's check is the file going "
              "between two phases of one run_step call, a race no "
              "production path sequences",
)
def lose_claimed_script(monkeypatch) -> None:
    """Make the claimed script (in the version's extracted copy) vanish
    between claim and dispatch.

    The resolver refuses a missing script at the claim, so dispatch's own
    host-side check is reached only when the file goes after the claim
    resolved it. The wrapper calls the real claim and, on a successful
    claim, deletes the script it names.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    import importlib

    orchestrator = importlib.import_module("wfc.execution.run_step")
    real_claim = orchestrator.run_claim

    def claim_then_lose_the_script(*args, **kwargs):
        claim = real_claim(*args, **kwargs)
        if claim.get("ok"):
            Path(claim["script_path"]).unlink()
        return claim

    monkeypatch.setattr(orchestrator, "run_claim", claim_then_lose_the_script)


@fake(
    boundary="wfc.execution.claim.compose_cache_key -- wrapped to compose the "
             "real key, then run a caller-given production registration "
             "before pre_run's verify step reads the snapshot",
    preserves="everything: the real key recipe, a real register_method "
              "(copy, commit, version row) and the real verify rule; only "
              "the moment the registration lands is chosen",
    not_proven="that a registration can interleave with a claim in a real "
               "run; only what the claim does when one has",
    backed_by="undrivable: the window is between two reads inside one "
              "pre_run call, a race no production path sequences",
)
def register_during_claim(monkeypatch, register) -> None:
    """Land a registration between the claim's key composition and its verify.

    The claim composes its cache key over the registered snapshot (pre_run
    step 3) and then verifies the snapshot (step 4). A registration landing
    between the two would key the run on one version's code and record
    another. The wrapper composes the real key and then calls *register*,
    which performs a production registration, once.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        register: A no-argument callable that registers the method again
            through production; called after the first key composition.
    """
    import importlib

    claim_mod = importlib.import_module("wfc.execution.claim")
    real_compose = claim_mod.compose_cache_key
    pending = [register]

    def compose_then_register(*args, **kwargs):
        composed = real_compose(*args, **kwargs)
        if pending:
            pending.pop()()
        return composed

    monkeypatch.setattr(claim_mod, "compose_cache_key", compose_then_register)


@fake(
    boundary="wfc.execution.run_step.run_claim -- wrapped to call the real "
             "claim through, then run a caller-given production registry "
             "change (a method registration, or an env's removal) once, "
             "before dispatch launches the claimed script",
    preserves="everything: the real claim (the resolver, the verify rule, "
              "the row and the cache key), a real register_method (copy, "
              "commit, version row) or delete_env, dispatch's env "
              "resolution and pre-launch check, and the failed record "
              "tail; only the moment the change lands is chosen",
    not_proven="that a registration can interleave with one run_step call "
               "in a real run; only what dispatch does when one has",
    backed_by="undrivable: the window is between two phases of one "
              "run_step call, a race no production path sequences",
)
def register_after_claim(monkeypatch, register) -> None:
    """Land a registration between a step's claim and its dispatch.

    The claim records the run under the version it verified; a registration
    landing before dispatch launches would run other code under that
    version and key. The wrapper calls the real claim and, on a successful
    claim, calls *register* (a production registration) once.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        register: A no-argument callable that changes the registry through
            production (registers the method again, or removes its env);
            called after the first successful claim.
    """
    import importlib

    orchestrator = importlib.import_module("wfc.execution.run_step")
    real_claim = orchestrator.run_claim
    pending = [register]

    def claim_then_register(*args, **kwargs):
        claim = real_claim(*args, **kwargs)
        if claim.get("ok") and pending:
            pending.pop()()
        return claim

    monkeypatch.setattr(orchestrator, "run_claim", claim_then_register)


@fake(
    boundary="wfc.execution.claim.HEARTBEAT_INTERVAL_SECONDS / "
             "STALL_WINDOW_SECONDS -- the claim heartbeat's interval and the "
             "stall window a waiting claim allows a still heartbeat",
    preserves="everything: the real claim, the running-claim index, the "
              "wait, the heartbeat thread and the compare-and-set take-over; "
              "only the two durations are shorter",
    not_proven="the production durations (5 s, 60 s); only the rule they time",
    backed_by="undrivable: the production window is a minute of wall time "
              "per abandoned claim, which no unit test waits out",
)
def short_claim_stall_window(monkeypatch, *, interval: float = 0.05,
                             window: float = 0.5) -> None:
    """Shorten the claim heartbeat's interval and the stall window.

    A test that abandons a held claim (the harness stops a run after its
    claim phase, as a killed process would) and then claims the same key
    again waits one stall window before the second claim takes the first
    over; this keeps that wait short.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        interval: Seconds between heartbeat advances.
        window: Seconds of still heartbeat before a waiter takes over.
    """
    import importlib

    claim_mod = importlib.import_module("wfc.execution.claim")
    monkeypatch.setattr(claim_mod, "HEARTBEAT_INTERVAL_SECONDS", interval)
    monkeypatch.setattr(claim_mod, "STALL_WINDOW_SECONDS", window)


@fake(
    boundary="psutil.Process.terminate / kill -- the signals the cancel "
             "operation sends an engine's process tree",
    preserves="everything else: the identity probe, the tree walk, the row "
              "flip and the record's removal; only the two signals raise "
              "AccessDenied, as a process owned by another user does",
    not_proven="a real permission refusal by the operating system",
    backed_by="undrivable: the test user owns every process it can start, so "
              "no real kill of one is refused",
)
def deny_process_kill(monkeypatch) -> None:
    """Make every terminate and kill of a process raise ``psutil.AccessDenied``.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
    """
    import psutil

    def _denied(proc):
        raise psutil.AccessDenied(proc.pid)

    monkeypatch.setattr(psutil.Process, "terminate", _denied)
    monkeypatch.setattr(psutil.Process, "kill", _denied)


@fake(
    boundary="wfc.canvas.routes.logs._LOG_LIVE_MAX_WALL_SECONDS -- how long a "
             "live log stream polls before it ends on its own",
    preserves="everything: the snapshot, the polling, the event ids and the "
              "terminal event; only the stream's wall-time guard is shorter",
    not_proven="the production guard (one hour)",
    backed_by="undrivable: the test client reads a response whole, so a live "
              "stream must end by itself, and the production guard is an hour",
)
def short_log_stream_wall(monkeypatch, seconds: float) -> None:
    """Shorten the live log stream's wall-time guard.

    Args:
        monkeypatch: An active ``pytest.MonkeyPatch``.
        seconds: How long a live stream polls before it ends.
    """
    import importlib

    logs_mod = importlib.import_module("wfc.canvas.routes.logs")
    monkeypatch.setattr(logs_mod, "_LOG_LIVE_MAX_WALL_SECONDS", seconds)
