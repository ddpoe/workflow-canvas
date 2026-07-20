"""Tier 2/3 tests: wfc run-step container dispatch (ADR-019 Cycle D).

Covers US-1 (container dispatch), US-3 (SLURM carve-out), and the
Snakefile-generator regression invariant (generator emits ``python -m wfc
run-step`` only — container wrapping happens inside run-step, not in the
generated shell line).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_annotations import workflow


VALID_DIGEST = "a" * 64
CONTAINER_REF_BARE = f"ghcr.io/dante/image-io@sha256:{VALID_DIGEST}"
CONTAINER_REF_DOCKER = f"docker://{CONTAINER_REF_BARE}"


def _setup_project(tmp_path: Path) -> Path:
    """Create a minimal wfc project with a container-env manifest and one
    method dir whose method.yaml binds env=image-io."""
    (tmp_path / ".wfc").mkdir()
    # Empty wf-canvas.toml (config_path.exists() needs to be true for the
    # env_name resolution branch to fire).
    (tmp_path / ".wfc" / "wf-canvas.toml").write_text(
        "[project]\nname=\"t\"\n[database]\nurl=\"sqlite:///:memory:\"\n"
    )
    # Container env in manifest.
    (tmp_path / ".wfc" / "envs.json").write_text(json.dumps({
        "schema_version": 1,
        "envs": {
            "image-io": {
                "backend": "pixi",
                "source": "pixi.toml",
                "container": CONTAINER_REF_DOCKER,
                "env_fingerprint": "f" * 64,
                "built_from_lock": "pixi.lock",
                "built_at": "2026-05-17T00:00:00Z",
            }
        },
    }))
    return tmp_path


def _write_method(project_dir: Path, method_name: str, *, gpus: bool = False,
                  executor: str = "local") -> Path:
    method_dir = project_dir / "methods" / method_name
    method_dir.mkdir(parents=True)
    (method_dir / f"{method_name}.py").write_text("# stub\n")
    (method_dir / "method.yaml").write_text(
        f"executor: {executor}\nenv: image-io\ngpus: {'true' if gpus else 'false'}\n"
    )
    return method_dir


def _make_pipeline(project_dir: Path, method_name: str) -> Path:
    pj = project_dir / "pipeline.json"
    pj.write_text(json.dumps({
        "nodes": [
            {
                "id": "n1",
                "method": method_name,
                "module": "test",
                "env": "image-io",
                "script": f"methods/{method_name}/{method_name}.py",
            }
        ],
        "links": [],
        "param_sets": {},
    }))
    return pj


def _patch_pre_run_and_subprocess(monkeypatch, tmp_path):
    # wfc.database.project_root() resolves from cwd or WFC_PROJECT_ROOT; we
    # point it at the tmp project so runs_dir() etc. don't blow up.
    monkeypatch.setenv("WFC_PROJECT_ROOT", str(tmp_path))
    """Stub out pre_run, complete_run, get_project_root, get_session, and
    _run_method_subprocess; capture the cmd argv."""
    import subprocess as _sp
    from wfc import cli as cli_mod

    captured = {}

    def fake_pre_run(**kwargs):
        return "NEW", 42

    def fake_complete_run(**kwargs):
        return None

    def fake_run_method_subprocess(cmd, *, cwd, env, stdout_log, stderr_log):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["cwd"] = cwd
        # Emit metrics.json so the post-subprocess collect doesn't fail —
        # actually the run will skip output collection because the pipeline
        # JSON has no slot_outputs declared. Just return success.
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr(cli_mod, "pre_run", fake_pre_run)
    monkeypatch.setattr(cli_mod, "complete_run", fake_complete_run)
    monkeypatch.setattr(cli_mod, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli_mod, "_run_method_subprocess", fake_run_method_subprocess)
    monkeypatch.setattr(cli_mod, "resolve_input", lambda **kw: None)

    # Make get_session a no-op context manager so the RunOutput write path
    # doesn't blow up on the in-memory DB absence.
    class _NullSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def exec(self, *a, **kw):
            class _R:
                def first(self): return None
            return _R()
        def add(self, *a, **kw): pass
        def commit(self): pass
    monkeypatch.setattr(cli_mod, "get_session", lambda: _NullSession())
    # PushStatus + RunOutput live import paths — short-circuit.
    monkeypatch.setattr(
        "wfc.remote.has_remote_configured", lambda p: False, raising=False
    )

    return captured


@workflow(purpose="run_step with container env dispatches via docker run --rm "
                  "--user with WFC_* env vars forwarded as -e flags")
def test_run_step_container_dispatch_docker_argv(tmp_path, monkeypatch):
    proj = _setup_project(tmp_path)
    method_dir = _write_method(proj, "ml_train")
    pj = _make_pipeline(proj, "ml_train")

    # Override the node's script so it points at the method we just made.
    pipeline = json.loads(pj.read_text())
    pipeline["nodes"][0]["script"] = str(method_dir / "ml_train.py")
    pj.write_text(json.dumps(pipeline))

    captured = _patch_pre_run_and_subprocess(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(proj / "ref.txt")],
    )

    # Return code may be non-zero due to post-dispatch output collection
    # (no real artifacts were written by the stubbed subprocess), but we
    # only care that the container dispatch happened with the correct argv.
    assert "cmd" in captured, f"_run_method_subprocess was not called (rc={rc})"
    cmd = captured["cmd"]
    # Docker run shape
    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    assert "--rm" in cmd
    assert "--user" in cmd
    # Image ref present (digest-pinned, no docker:// prefix in argv).
    assert CONTAINER_REF_BARE in cmd
    # Inner argv runs the method script DIRECTLY under the env's resolved
    # interpreter — no `-m wfc`, no in-container wfc entrypoint at all. The
    # outer host run-step owns run-state; the image needs nothing
    # wfc-related (thin-container dispatch cutover). The fixture env record
    # declares backend=pixi with no recorded `python`, so the resolver
    # falls back to the pixi per-backend default for env name "image-io".
    image_idx = cmd.index(CONTAINER_REF_BARE)
    inner = cmd[image_idx + 1:]
    assert inner == [
        "/image-io/envs/default/bin/python",
        "/work/methods/ml_train/ml_train.py",
    ], f"inner argv must be [<env-python>, <script-in-container>]; got {inner!r}"
    assert "-m" not in cmd and "wfc" not in cmd, (
        "Inner argv must NOT dispatch through `python -m wfc` — registered "
        "env images contain nothing wfc-related."
    )
    assert "run-step" not in inner, (
        "Inner argv must NOT recursively call wfc run-step — that regenerates "
        "run_id and breaks host-side output collection."
    )
    # WFC_* env vars are forwarded via -e flags.
    flag_pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "-e"]
    forwarded_keys = {pair[1].split("=", 1)[0] for pair in flag_pairs}
    assert "WFC_RUN_ID" in forwarded_keys
    assert "WFC_NODE_ID" in forwarded_keys
    assert "WFC_VARIANT" in forwarded_keys
    # PYTHONPATH is NOT forwarded into the container.
    assert "PYTHONPATH" not in forwarded_keys
    # No --gpus flag (method.yaml gpus=false default).
    assert "--gpus" not in cmd


@workflow(purpose="method.yaml gpus: true plumbs through to docker --gpus all; "
                  "absent/false → no --gpus flag")
def test_run_step_gpu_flag_routing(tmp_path, monkeypatch):
    proj = _setup_project(tmp_path)
    method_dir = _write_method(proj, "ml_gpu", gpus=True)
    pj = _make_pipeline(proj, "ml_gpu")

    pipeline = json.loads(pj.read_text())
    pipeline["nodes"][0]["script"] = str(method_dir / "ml_gpu.py")
    pj.write_text(json.dumps(pipeline))

    captured = _patch_pre_run_and_subprocess(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(proj / "ref.txt")],
    )
    assert "cmd" in captured, f"_run_method_subprocess was not called (rc={rc})"
    cmd = captured["cmd"]
    assert "--gpus" in cmd
    assert cmd[cmd.index("--gpus") + 1] == "all"


@workflow(purpose="executor=slurm + container env → run_step exits non-zero "
                  "with 'out of scope for v1' on stderr (US-3)")
def test_run_step_slurm_executor_carve_out_error(tmp_path, monkeypatch, capsys):
    proj = _setup_project(tmp_path)
    method_dir = _write_method(proj, "ml_cluster", executor="slurm")
    pj = _make_pipeline(proj, "ml_cluster")
    pipeline = json.loads(pj.read_text())
    pipeline["nodes"][0]["script"] = str(method_dir / "ml_cluster.py")
    pj.write_text(json.dumps(pipeline))

    _patch_pre_run_and_subprocess(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(proj / "ref.txt")],
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "out of scope for v1" in err


def test_run_step_translates_wfc_paths_for_container(tmp_path, monkeypatch):
    """WFC_RUN_DIR and WFC_INPUT_PATHS are translated from host paths to
    container paths before being forwarded via ``-e`` flags. Host paths
    inside ``project_root`` rewrite to ``/work/...`` (POSIX); host paths
    inside the DVC cache rewrite to ``/dvc-cache/...``. Out-of-bounds
    paths are passed through unchanged.

    Regression: PYTHONPATH must remain absent from forwarded env vars.
    """
    # wfc.database caches project_root() at module level; clear so our
    # fresh tmp_path is honoured (other tests in this file get away
    # without this because they don't assert on the run_dir path itself).
    from wfc.database import reset_engine
    reset_engine()

    proj = _setup_project(tmp_path)
    method_dir = _write_method(proj, "ml_paths")
    pj = _make_pipeline(proj, "ml_paths")

    pipeline = json.loads(pj.read_text())
    pipeline["nodes"][0]["script"] = str(method_dir / "ml_paths.py")
    pj.write_text(json.dumps(pipeline))

    # Put an input file under project_root so WFC_INPUT_PATHS will contain
    # at least one path that should translate to /work/...
    in_dir = proj / "data" / "samples" / "s1"
    in_dir.mkdir(parents=True)
    input_file = in_dir / "input.txt"
    input_file.write_text("payload")

    captured = _patch_pre_run_and_subprocess(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(input_file)],
    )
    assert "cmd" in captured, f"_run_method_subprocess was not called (rc={rc})"
    cmd = captured["cmd"]
    flag_pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "-e"]
    forwarded = {pair[1].split("=", 1)[0]: pair[1].split("=", 1)[1] for pair in flag_pairs}

    # WFC_RUN_DIR is rewritten to /work/... (the run_dir lives under
    # project_root at .runs/<run_id>/...).
    assert "WFC_RUN_DIR" in forwarded
    assert forwarded["WFC_RUN_DIR"].startswith("/work"), (
        f"WFC_RUN_DIR should be translated to /work/...; got {forwarded['WFC_RUN_DIR']!r}"
    )
    # No backslashes (POSIX form).
    assert "\\" not in forwarded["WFC_RUN_DIR"]

    # WFC_INPUT_PATHS is JSON; each path under project_root should be
    # rewritten to /work/... (the input file we wrote lives under proj).
    assert "WFC_INPUT_PATHS" in forwarded
    decoded = json.loads(forwarded["WFC_INPUT_PATHS"])
    # Collect path-like leaf values regardless of dict vs list shape.
    leaves: list[str] = []
    if isinstance(decoded, dict):
        for v in decoded.values():
            if isinstance(v, list):
                leaves.extend(v)
            elif isinstance(v, str):
                leaves.append(v)
    elif isinstance(decoded, list):
        leaves = [p for p in decoded if isinstance(p, str)]
    assert leaves, f"expected at least one path leaf in WFC_INPUT_PATHS, got {decoded!r}"
    work_leaves = [p for p in leaves if p.startswith("/work")]
    assert work_leaves, (
        f"expected at least one WFC_INPUT_PATHS entry rewritten to /work/...; "
        f"got {leaves!r}"
    )
    # No backslashes anywhere in the translated payload.
    for p in leaves:
        assert "\\" not in p, f"path leaf still contains backslashes: {p!r}"

    # Regression: PYTHONPATH still absent from forwarded -e flags.
    assert "PYTHONPATH" not in forwarded


@workflow(purpose="run_step with a nonexistent method script fails host-side "
                  "with a clear error BEFORE any docker invocation (US-5)")
def test_run_step_missing_script_fails_before_docker(tmp_path, monkeypatch, capsys):
    proj = _setup_project(tmp_path)
    method_dir = _write_method(proj, "ml_ghost")
    pj = _make_pipeline(proj, "ml_ghost")

    # Point the node's script at a file that does NOT exist.
    pipeline = json.loads(pj.read_text())
    pipeline["nodes"][0]["script"] = str(method_dir / "nope.py")
    pj.write_text(json.dumps(pipeline))

    captured = _patch_pre_run_and_subprocess(monkeypatch, proj)

    from wfc.cli import run_step
    rc = run_step(
        node_id="n1",
        sample="s1",
        variant="default",
        pipeline_json=str(pj),
        pipeline_id="p1",
        ref_inputs=["data=" + str(proj / "ref.txt")],
    )
    assert rc == 1
    # No docker command was ever assembled/run.
    assert "cmd" not in captured, (
        f"docker must not be invoked for a missing script; got {captured.get('cmd')!r}"
    )
    err = capsys.readouterr().err
    assert "method script not found" in err
    assert "nope.py" in err


def test_snakefile_generator_emits_python_dash_m_wfc_for_container_env(tmp_path):
    """Generator regression: container-env methods still get a plain
    ``python -m wfc run-step`` shell line. Container wrapping happens
    inside run-step, not in the generated Snakefile."""
    from wfc.snakemake_gen import StepDef, _generate_rule

    step = StepDef(
        method_name="ml_train",
        module_name="test",
        script_path="methods/ml_train/ml_train.py",
        params={},
        node_id="n1",
        env="image-io",   # container env name registered in .wfc/envs.json
    )
    lines = _generate_rule(step, {"n1": step}, pipeline_id="p1")
    shell_block = "\n".join(lines)
    assert "python -m wfc run-step" in shell_block or "{sys.executable} -m wfc run-step" in shell_block
    assert "docker run" not in shell_block
    assert "apptainer exec" not in shell_block
