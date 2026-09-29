"""Dispatch phase: assemble the container invocation and run it.

Writes the run context and WFC_* env, resolves the node's container image
(manifest lookup + per-method escape hatch), builds the ``docker run`` argv
with host→container path translation, and runs the method subprocess.

Every failure here reports its ending to the orchestrator instead of writing
records inline; the record tail composes the writes. The SLURM carve-out is
an exit-WITHOUT-record ending (its composition is "write nothing").

The method launch (``_run_method_subprocess``, the tee'd ``docker run``)
lives here.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import layout

from axiom_annotations import task, Step
from ..persistence import project_root as get_project_root


def interpret_method_exit(returncode: int, stderr_text: str | None) -> tuple[str, str]:
    """Interpret a non-zero method exit into the error to surface.

    Pure function: given the subprocess exit status and the captured stderr
    text, derive the structured ``error_message`` / ``error_traceback`` pair.
    Lifts the real error from the stderr tail so the fields carry the actual
    exception line (or whatever the method printed last) instead of an
    uninformative "Method exited with code N" — the latter is what compact
    summary surfaces would otherwise show.

    Args:
        returncode: The method subprocess's non-zero exit code.
        stderr_text: Full text of the per-run stderr log, or ``None`` when
            the log could not be read.

    Returns:
        ``(error_message, error_traceback)`` — the traceback is the last
        100 stderr lines; the message is the final non-blank line when it
        looks like a Python ``ExceptionClass: message`` tail, else the
        generic exit-code message.
    """
    error_msg = f"Method exited with code {returncode}"
    error_tb = ""
    if stderr_text is not None:
        error_tb = "".join(stderr_text.splitlines(keepends=True)[-100:])
        last_line = next(
            (ln.strip() for ln in reversed(error_tb.splitlines()) if ln.strip()),
            "",
        )
        # Python tracebacks end with "<ExceptionClass>: <message>".
        head, sep, _ = last_line.partition(": ")
        if sep and head and " " not in head:
            error_msg = last_line
    return error_msg, error_tb


@task(purpose="Dispatch phase: write run context and env, resolve the container "
              "image, build the docker argv, and run the method subprocess",
      inputs="resolved node config, run id, slot paths from materialize",
      outputs="success, or the failure ending (no-container / slurm / "
              "script-missing / launch-failure / method-failed) with its error")
def run_dispatch(
    node_id: str,
    sample: str,
    variant: str,
    method_name: str,
    script_path: str,
    module_name: str,
    params: dict,
    pipeline_id: str,
    pipeline_json: str | None,
    run_id: int,
    slot_paths: dict,
    slot_outputs: dict,
    slot_types: dict,
) -> dict:
    """Run the node's method in an ephemeral local Docker container.

    Args:
        node_id: The node being executed.
        sample: Sample identifier.
        variant: Parameter variant name.
        method_name: Resolved method name.
        script_path: Resolved method script path.
        module_name: Resolved module name.
        params: Resolved parameter dict.
        pipeline_id: Pipeline execution ID.
        pipeline_json: Path to the pipeline JSON file, or ``None``.
        run_id: The registered run's ID.
        slot_paths: Materialized slot→paths map.
        slot_outputs: Declared output filenames per slot (run context).
        slot_types: Declared output types per slot (run context).

    Returns:
        ``{"ok": True}`` when the method exits 0. Otherwise ``{"ok": False,
        "ending": <kind>, "error_message": ..., "error_traceback": ...}``
        where the ending kind is one of ``"no-container"``, ``"slurm"``,
        ``"script-missing"``, ``"launch-failure"``, ``"method-failed"``.
    """
    import traceback as _tb


    run_dir = layout.run_archive_dir(get_project_root(), run_id)

    口 = Step(step_num=1, name="Write run context and env",
             purpose="Write _run_context.json and assemble the WFC_* env dict, "
                     "including WFC_INPUT_PATHS for fan-in slots")
    # Write _run_context.json
    context = {
        "run_id": int(run_id), "run_dir": str(run_dir), "sample": sample,
        "slot_paths": slot_paths, "params": params,
        "method_name": method_name, "module_name": module_name,
        "slot_outputs": slot_outputs,
        "slot_types": slot_types,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_run_context.json").write_text(json.dumps(context, indent=2, default=str))

    # Set WFC_* env vars
    wfc_env = os.environ.copy()
    wfc_env.update({
        "WFC_RUN_ID": str(run_id),
        "WFC_RUN_DIR": str(run_dir.resolve()),
        "WFC_SAMPLE": sample,
        "WFC_PARAMS": json.dumps(params),
        "WFC_NODE_ID": node_id,
        "WFC_PIPELINE_ID": pipeline_id,
        "WFC_VARIANT": variant,
    })
    # Fan-in: expose all parent outputs grouped by slot so method.py can
    # load multi-input dicts via WFC_INPUT_PATHS.
    if slot_paths:
        wfc_env["WFC_INPUT_PATHS"] = json.dumps(slot_paths)

    wfc_env["PYTHONUNBUFFERED"] = "1"

    口 = Step(step_num=2, name="Resolve container image",
             purpose="Resolve the node's env name through the one grammar "
                     "(the document's value, or the method's own declaration "
                     "when the invocation carries no document), look up its "
                     "built image in the manifest, read executor and gpus, "
                     "and enforce container-only execution")
    from ..contracts import WFC_ENV_VARS, parse_env_spec, parse_method_yaml

    project_root = get_project_root()

    # The document's value for this node, when the invocation carries a
    # document. Execution is container-only: there is no host-Python
    # fallback and no default env — every node must name a built container
    # env (see the no-container ending below).
    document_env: str | None = None
    if pipeline_json and Path(pipeline_json).exists():
        raw_pj = json.loads(Path(pipeline_json).read_text())
        nm = {str(n["id"]): n for n in raw_pj["nodes"]}
        mm = {n["method"]: n for n in raw_pj["nodes"] if n.get("method")}
        nc = nm.get(node_id) or mm.get(node_id) or {}
        document_env = nc.get("env", nc.get("env_strategy", "")) or ""

    # Re-parse method.yaml (cheap) for ``executor`` and ``gpus`` without
    # threading the contract dict from the claim phase — and, for an
    # inline-args invocation with no document, for the env the method
    # itself declares. A document that names an env nothing built still
    # ends no-container: the declaration is read only when there is no
    # document at all.
    method_executor = "local"
    method_gpus = False
    declared_env = ""
    try:
        contract = parse_method_yaml(Path(script_path).resolve().parent)
        if contract is not None:
            method_executor = contract.get("executor") or "local"
            method_gpus = bool(contract.get("gpus", False))
            declared_env = contract.get("env") or ""
    except Exception:
        pass

    env_value = document_env if document_env is not None else declared_env
    env_name = ""
    env_error = ""
    if env_value:
        try:
            env_name = parse_env_spec(env_value)
        except ValueError as exc:
            env_error = str(exc)

    # Manifest lookup by the parsed name. The record and the name are
    # retained for interpreter resolution at the argv-build step
    # (resolve_env_python: recorded field -> per-backend default -> bare
    # "python"). No recursive-dispatch guard is needed: the container runs
    # the method script directly under the env's own interpreter — there is
    # no in-container wfc entrypoint at all, so recursion is impossible by
    # construction.
    container_image_ref: str | None = None
    env_record = None
    lookup_name = env_name
    if env_name:
        try:
            from ..environments import get as _envs_get, strip_docker_scheme
            record = _envs_get(env_name, project_root)
            if record is not None and getattr(record, "container", ""):
                env_record = record
                # Strip docker:// prefix; the docker CLI accepts the bare
                # registry/repo@digest form. (Apptainer wants docker://;
                # build_apptainer_command re-prefixes.)
                container_image_ref = strip_docker_scheme(record.container)
        except Exception:
            container_image_ref = None
            env_record = None

    # Container-only enforcement: if no container image resolved (a value
    # outside the grammar, an unknown env name, or a non-container env
    # record), there is NO host-Python fallback — fail loudly. The record
    # tail flips the run to failed and writes the outcome sidecar so
    # pipeline-summary aggregation and the run row stay consistent.
    if container_image_ref is None:
        env_label = env_name or env_value or "<none>"
        error_msg = (
            f"node '{node_id}' env '{env_label}' does not resolve to a built "
            f"container image. "
        ) + (
            env_error
            or f"Execution is container-only: there is no host-Python fallback. "
               f"Build the env first with `wfc register-env {env_label}` and "
               f"declare `env: {env_label}` in method.yaml / the pipeline node."
        )
        return {"ok": False, "ending": "no-container",
                "error_message": error_msg, "error_traceback": error_msg}

    口 = Step(step_num=3, name="Build docker run command",
             purpose="Set up per-run log paths, verify the method script exists on "
                     "the host, and assemble the docker run argv: bind mounts, image "
                     "ref, the env-interpreter + script inner command, and -e WFC_* "
                     "forwarding with host→container path translation")
    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"

    # SLURM carve-out: dispatch is local-only, so the slurm executor
    # ends here without starting anything. Exit-WITHOUT-record: no row
    # flip, no outcome.
    if method_executor == "slurm":
        return {"ok": False, "ending": "slurm",
                "error_message": "cluster Apptainer dispatch (executor=slurm) "
                                 "is not supported",
                "error_traceback": None}

    # Host-side pre-flight: the method script must exist BEFORE any
    # container starts. The container runs the script directly (no
    # in-container wfc to validate it), so a missing script would
    # otherwise surface as an opaque interpreter error from inside
    # docker. Fail fast with an actionable message instead. Relative
    # script paths (e.g. pipeline-JSON "methods/<m>/<m>.py") are
    # anchored at project_root, not the cwd.
    _script_host = Path(script_path)
    if not _script_host.is_absolute():
        _script_host = Path(project_root) / _script_host
    if not _script_host.resolve().is_file():
        error_msg = (
            f"method script not found: {script_path} "
            f"(node '{node_id}', method '{method_name}'). "
            f"Check the method's script path before dispatch — "
            f"no container was started."
        )
        return {"ok": False, "ending": "script-missing",
                "error_message": error_msg, "error_traceback": error_msg}

    from ..environments import build_docker_command

    # UID/GID: ``os.getuid()`` doesn't exist on Windows. Docker Desktop
    # on Windows/macOS ignores ``--user`` anyway; on Linux stock Docker
    # this is what keeps bind-mount writes from landing as root:root.
    _uid = getattr(os, "getuid", lambda: 0)()
    _gid = getattr(os, "getgid", lambda: 0)()

    # DVC cache dir: <project_root>/.dvc/cache. The helper mounts it at
    # /dvc-cache inside the container so input paths that point into the
    # content-addressed store resolve there as they do on the host.
    dvc_cache_dir = layout.dvc_cache_dir(project_root)
    mounts = layout.mount_table(project_root, dvc_cache_dir)

    # Inner command: the method script runs DIRECTLY under the env's
    # recorded interpreter — the image needs nothing wfc-related. The
    # outer host run-step remains sole owner of run-state (DB rows,
    # cache keys, output collection); the container's only job is to
    # execute the script with the WFC_* env vars forwarded below.
    #
    # Interpreter resolution (wfc.environments.resolve_env_python): the env
    # record's ``python`` field wins; records without it fall back to
    # the per-backend default; no record (escape hatch) means bare
    # ``python``. The resolved value is a CONTAINER path — it must NOT
    # go through _translate_host_path below.
    #
    # Script-path translation: host ``<project_root>/methods/<m>/<m>.py``
    # becomes ``/work/methods/<m>/<m>.py`` because ``/work`` is the
    # bind-mount of ``project_root``. If the script lives outside
    # project_root (unusual; an inline-fallback path), pass it through
    # as-is and rely on the caller having bind-mounted it.
    script_in_container = layout.translate_host_path(str(_script_host), mounts)

    from ..environments import resolve_env_python
    env_python = resolve_env_python(lookup_name, env_record)
    inner_argv = [env_python, script_in_container]

    cmd = build_docker_command(
        image_ref=container_image_ref,
        project_root=project_root,
        dvc_cache_dir=dvc_cache_dir,
        run_step_argv=inner_argv,
        uid=_uid,
        gid=_gid,
        gpus=method_gpus,
    )

    # Forward WFC_* env vars via -e flags. Do NOT forward PYTHONPATH
    # into the container (host venv leakage is the entire reason for
    # the container barrier).
    #
    # Path translation: WFC_RUN_DIR is a host absolute path, and
    # WFC_INPUT_PATHS is a JSON dict/list of host absolute paths. The
    # in-container method script reads these env vars; the project tree
    # is bind-mounted at ``/work`` and the DVC cache at ``/dvc-cache``,
    # so host paths under those roots must be rewritten before
    # forwarding (otherwise the container hits nonexistent paths).
    # Translation is Layout's one function over the same mount table the
    # argv builder rendered its binds from.
    env_flags: list[str] = []
    for k in WFC_ENV_VARS:
        if k not in wfc_env:
            continue
        value = wfc_env[k]
        if k == "WFC_RUN_DIR":
            value = layout.translate_host_path(value, mounts)
        elif k == "WFC_INPUT_PATHS":
            # JSON dict/list of paths -- decode, translate each leaf, re-encode.
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                translated: dict = {}
                for slot, entry in decoded.items():
                    if isinstance(entry, list):
                        translated[slot] = [
                            layout.translate_host_path(p, mounts) if isinstance(p, str) else p
                            for p in entry
                        ]
                    elif isinstance(entry, str):
                        translated[slot] = layout.translate_host_path(entry, mounts)
                    else:
                        translated[slot] = entry
                value = json.dumps(translated)
            elif isinstance(decoded, list):
                value = json.dumps([
                    layout.translate_host_path(p, mounts) if isinstance(p, str) else p
                    for p in decoded
                ])
            # else: leave value as-is (unparseable -- preserve original).
        env_flags.extend(["-e", f"{k}={value}"])
    # Splice -e flags right after "docker run --rm" for readability.
    # Find the position after the last "-v ... -w /work -v ..." bind
    # block but before --gpus / image. Simplest: insert after "--rm".
    rm_idx = cmd.index("--rm") + 1
    cmd = cmd[:rm_idx] + env_flags + cmd[rm_idx:]

    口 = Step(step_num=4, name="Run method subprocess",
             purpose="Run the assembled docker command with stdout/stderr tee'd to "
                     "per-run logs; on a non-zero exit, lift the real error from "
                     "stderr.log and report the method-failed ending")
    # Run the assembled ``docker run`` command.  stdout/stderr are tee'd to
    # per-run log files (.runs/<run_id>/{stdout,stderr}.log) while also being
    # forwarded to the parent's std streams so Snakemake's pipeline-level
    # capture still sees method output.
    try:
        result = _run_method_subprocess(
            cmd,
            cwd=str(project_root),
            env=wfc_env,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
    except Exception as exc:
        # Subprocess launch failure
        return {"ok": False, "ending": "launch-failure",
                "error_message": str(exc), "error_traceback": _tb.format_exc()}

    if result.returncode != 0:
        # Lift the real error from stderr.log so the
        # structured error_message / error_traceback fields carry the actual
        # ValueError (or whatever) instead of "Method exited with code N".
        stderr_text: str | None = None
        if stderr_log.exists():
            try:
                stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stderr_text = None
        error_msg, error_tb = interpret_method_exit(result.returncode, stderr_text)
        return {"ok": False, "ending": "method-failed",
                "error_message": error_msg, "error_traceback": error_tb}

    return {"ok": True}


def _run_method_subprocess(
    cmd: list,
    *,
    cwd: str,
    env: dict,
    stdout_log: Path,
    stderr_log: Path,
):
    """Run the method's ``docker run`` command, tee'ing stdout/stderr to
    per-run log files AND the parent process's std streams.

    Execution is container-only, so ``cmd`` is always the
    assembled ``docker run ...`` argv (there is no host-Python path). Per-run
    logs land at ``stdout_log`` / ``stderr_log`` (typically inside
    ``.runs/<run_id>/``).  Output is also forwarded to the parent's
    stdout/stderr so Snakemake's pipeline-level capture and live CLI
    users still see output in real time.

    Returns a CompletedProcess so callers read ``.returncode``.
    ``stdout``/``stderr`` on the result are
    always ``None`` — read the per-run log files instead.
    """
    import subprocess as _sp
    import threading

    stdout_log.parent.mkdir(parents=True, exist_ok=True)

    proc = _sp.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
        # Child output includes docker passthrough, which is UTF-8 regardless
        # of the Windows codepage; cp1252 decode crashes the pump thread.
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def _pump(src, log_file, parent_stream):
        try:
            for line in iter(src.readline, ""):
                log_file.write(line)
                log_file.flush()
                try:
                    parent_stream.write(line)
                    parent_stream.flush()
                except Exception:
                    pass  # parent stream may be closed/redirected; logs still captured
        finally:
            try:
                src.close()
            except Exception:
                pass

    with open(stdout_log, "w", encoding="utf-8") as out_f, \
         open(stderr_log, "w", encoding="utf-8") as err_f:
        t_out = threading.Thread(
            target=_pump, args=(proc.stdout, out_f, sys.stdout), daemon=True
        )
        t_err = threading.Thread(
            target=_pump, args=(proc.stderr, err_f, sys.stderr), daemon=True
        )
        t_out.start()
        t_err.start()
        proc.wait()
        t_out.join()
        t_err.join()

    return _sp.CompletedProcess(
        args=cmd, returncode=proc.returncode, stdout=None, stderr=None
    )
