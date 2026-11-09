"""Dispatch phase: assemble the container invocation and run it.

Writes the run context and WFC_* env, resolves the node's container image
(manifest lookup + per-method escape hatch), builds the ``docker run`` argv
with host→container path translation, records that invocation on the run
row (container-side values only) and runs the method subprocess.

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

from axiom_annotations import AutoStep, Step, task

from .. import layout
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


def _changed_since_claim_message(module_name: str, method_name: str,
                                 detail: str) -> str:
    """The ``changed-since-claim`` error message for a claimed version that no longer resolves.

    Args:
        module_name: The method's module name.
        method_name: The method's name.
        detail: Why the claimed version cannot be launched.

    Returns:
        The message: what changed, the detail, and to run the step again.
    """
    return (f"method '{module_name}/{method_name}' (or its env) was registered "
            f"again, or its registered copy changed, after this step was "
            f"claimed: {detail} No container was started. Run the step again.")


@task(purpose="Pre-launch check: what dispatch is about to launch is what the "
              "run row was claimed under -- the verify rule answers the run's "
              "version for the claimed version identity, the resolver's "
              "script is the claimed script, and the version's recorded env "
              "fingerprint is the run row's",
      inputs="module and method names, the claimed script, the claimed "
             "version identity, the run id, the project root",
      outputs="None when unchanged, else the changed-since-claim error message",
      critical="Reads the snapshot once, right before the launch; a "
               "difference is refused, never re-claimed (a re-claim would "
               "write a second run row)")
def _changed_since_claim(module_name: str, method_name: str, script: Path,
                         version: str | None, run_id: int,
                         project_root: Path) -> str | None:
    """Check the version about to launch is the one the step was claimed under.

    The verify rule runs again for the claimed version identity: its
    version row must be the run row's ``version_id``, the resolver's script
    the one the claim handed to dispatch, and the env fingerprint the
    version recorded the run row's ``env_fingerprint``. A version list
    rewritten or a tree git no longer holds is refused here, before any
    container starts.

    Args:
        module_name: The method's module name.
        method_name: The method's name.
        script: The script the claim resolved, on the host.
        version: The version identity the claim resolved; ``None`` is the
            newest.
        run_id: The claimed run's ID.
        project_root: The project root.

    Returns:
        ``None`` when the code is unchanged; otherwise the error message for
        the ``changed-since-claim`` ending.
    """
    from ..persistence import Run, get_session
    from ..registration import (
        MalformedMethodRecordError,
        UnregisteredSnapshotError,
        verify_registered_snapshot,
    )

    with get_session() as session:
        run = session.get(Run, run_id)
        claimed_version = run.version_id if run is not None else None
        claimed_env_fp = run.env_fingerprint if run is not None else None
    head = (f"method '{module_name}/{method_name}' (or its env) was registered "
            f"again, or its registered copy changed, after this step was "
            f"claimed")
    tail = "No container was started. Run the step again."
    try:
        registered = verify_registered_snapshot(module_name, method_name,
                                                project_root, version=version)
    except (MalformedMethodRecordError, UnregisteredSnapshotError) as exc:
        return _changed_since_claim_message(module_name, method_name, str(exc))
    if registered.version_id != claimed_version:
        return (f"{head}: the step was claimed under version "
                f"{claimed_version}, and the registered copy is now version "
                f"{registered.version_id}. {tail}")
    if registered.code.script.resolve() != script.resolve():
        return (f"{head}: the step was claimed to run {script}, and the "
                f"registered script is now {registered.code.script}. {tail}")
    env_fp = registered.code.entry.env_fingerprint
    if env_fp != claimed_env_fp:
        return (f"{head}: the step was claimed in an env with fingerprint "
                f"{claimed_env_fp}, and version v{registered.code.entry.label} "
                f"records env '{registered.code.entry.env_name}' with "
                f"fingerprint {env_fp}. {tail}")
    return None


#: The bundle role of each fixed row of Layout's mount table, by container
#: path. A row not named here is the version's code copy.
_MOUNT_ROLES = {layout.WORK_MOUNT: "project", layout.DVC_CACHE_MOUNT: "cache"}
#: The role of the version's code copy row.
_CODE_COPY_ROLE = "code"


def _invocation_record(*, image_ref: str, interpreter: str, script: str,
                       mounts, copy_writable: bool, env_flags: list[str],
                       uid: int, gid: int, gpus: bool, cmd: list[str]) -> dict:
    """Compose the invocation record from the values dispatch hands the launch.

    Every value is container-side, taken as dispatch built it: no host path
    is kept. A mount is its container path, its role in a bundle and whether
    Docker was handed it read-only (a backend that cannot bind read-only
    binds the code copy read-write, and the record says so).

    Args:
        image_ref: The image reference handed to Docker.
        interpreter: The image's interpreter, a container path.
        script: The script path inside the container.
        mounts: Layout's mount table for the run, in bind order.
        copy_writable: Whether the code copy was bound read-write.
        env_flags: The ``-e NAME=VALUE`` pairs forwarded to the container.
        uid: The user id handed to ``--user``.
        gid: The group id handed to ``--user``.
        gpus: Whether ``--gpus all`` was given.
        cmd: The assembled argv, read for its working directory.

    Returns:
        The record: ``image_ref``, ``interpreter``, ``script``, ``mounts``
        (``container``, ``role``, ``read_only``), ``env``, ``user``,
        ``gpus``, ``workdir`` and ``wfc_version``.
    """
    from ..release import installed_wfc_version

    env: dict[str, str] = {}
    for flag, pair in zip(env_flags[::2], env_flags[1::2]):
        if flag == "-e":
            name, _, value = pair.partition("=")
            env[name] = value
    workdir = cmd[cmd.index("-w") + 1] if "-w" in cmd else None
    return {
        "image_ref": image_ref,
        "interpreter": interpreter,
        "script": script,
        "mounts": [
            {"container": m.container,
             "role": _MOUNT_ROLES.get(m.container, _CODE_COPY_ROLE),
             "read_only": bool(m.read_only and not copy_writable)}
            for m in mounts
        ],
        "env": env,
        "user": f"{uid}:{gid}",
        "gpus": bool(gpus),
        "workdir": workdir,
        "wfc_version": installed_wfc_version(),
    }


@task(purpose="Write the run's invocation record onto its row, through "
              "Persistence's write transaction, before the launch",
      inputs="the run id and the composed invocation record",
      outputs="the run row's invocation column set",
      critical="Runs before the method starts, so a run whose method fails "
               "keeps its record; a write that fails stops the launch, so "
               "every launched run has one")
def _record_invocation(run_id: int, invocation: dict) -> None:
    """Store the invocation record on the run row.

    Args:
        run_id: The claimed run's ID.
        invocation: The record :func:`_invocation_record` composed.

    Raises:
        LookupError: The run row is gone.
    """
    from ..persistence import Run, write_transaction

    with write_transaction() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"run {run_id} has no row to record its invocation on")
        run.invocation = invocation
        session.add(run)


@task(purpose="Dispatch phase: write run context and env, resolve the container "
              "image, build the docker argv, check the code is what the "
              "step was claimed under, record the invocation on the run row, "
              "and run the method subprocess",
      inputs="resolved node config, run id, slot paths from materialize",
      outputs="success, or the failure ending (no-container / slurm / "
              "script-missing / changed-since-claim / launch-failure / "
              "method-failed) with its error")
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
    code_dir: str | None = None,
    version: str | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Run the node's method in an ephemeral local Docker container.

    Args:
        node_id: The node being executed.
        sample: Sample identifier.
        variant: Parameter variant name.
        method_name: Resolved method name.
        script_path: The script the claim resolved inside the method's
            registered snapshot (Registration's resolver).
        module_name: Resolved module name.
        params: Resolved parameter dict.
        pipeline_id: Pipeline execution ID.
        pipeline_json: Path to the pipeline JSON file, or ``None``.
        run_id: The registered run's ID.
        slot_paths: Materialized slot→paths map.
        slot_outputs: Declared output filenames per slot (run context).
        slot_types: Declared output types per slot (run context).
        code_dir: The version's extracted copy the claim resolved, holding
            its ``method.yaml``; bound read-only into the container at its
            own ``/work`` path. ``None`` means the script's own directory
            and no copy bind.
        version: The version identity the claim resolved; its list entry
            names the env and the image the run launches. ``None`` is the
            newest.
        notes: Warnings materialize raised for this run (a repaired sample
            copy's); they head the run's ``stderr.log``, the step log.

    Returns:
        ``{"ok": True}`` when the method exits 0. Otherwise ``{"ok": False,
        "ending": <kind>, "error_message": ..., "error_traceback": ...}``
        where the ending kind is one of ``"no-container"``, ``"slurm"``,
        ``"script-missing"``, ``"changed-since-claim"`` (the method was
        registered again, or its registered copy changed, after the claim;
        no container starts), ``"launch-failure"`` (the invocation record
        could not be written or the step log could not be started fresh,
        so no launch was tried and the dict carries
        ``"launch_attempted": False``; or the launch itself raised),
        ``"method-failed"``.
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

    # The step's WFC_* values, host-side (WFC_INPUT_PATHS keeps every slot a
    # list). The container's variables are built from them by the one
    # builder at the argv step; the host process that launches the
    # container gets the same values untranslated.
    from .container_vars import container_variables, step_variables
    step_env = step_variables(
        run_id=run_id, run_dir=str(run_dir.resolve()), sample=sample,
        params=params, node_id=node_id, pipeline_id=pipeline_id,
        variant=variant, slot_paths=slot_paths)
    wfc_env = os.environ.copy()

    口 = Step(step_num=2, name="Resolve container image",
             purpose="Read the version's list entry: its env (through the one "
                     "grammar, checked against the manifest) and "
                     "the image reference it recorded, which is what launches "
                     "(a claimed version the list no longer holds ends "
                     "changed-since-claim); read executor and gpus from the "
                     "copy's method.yaml, and enforce container-only execution")
    from ..contracts import declared_names, parse_method_yaml
    from ..registration import version_entry
    from .node_env import resolve_node_env

    project_root = get_project_root()

    # Re-parse method.yaml (cheap) for ``executor`` and ``gpus`` without
    # threading the contract dict from the claim phase. It is the snapshot's
    # method.yaml: the directory the claim's resolver answered with.
    method_executor = "local"
    method_gpus = False
    declared: dict | None = None
    contract_dir = (Path(code_dir) if code_dir is not None
                    else Path(script_path).resolve().parent)
    try:
        contract = parse_method_yaml(contract_dir)
        if contract is not None:
            method_executor = contract.get("executor") or "local"
            method_gpus = bool(contract.get("gpus", False))
            declared = declared_names(contract.get("inputs"), contract.get("outputs"))
    except Exception:
        pass
    # The launching host process: its own environment plus the step's
    # variables, host-side (no PATH: the host's own stays).
    wfc_env.update(container_variables(step=step_env, declared=declared))

    # The node's env, by the rule the pre-flights share: the env the method
    # is registered in, the one its cache key is over. A document's env and
    # the registered copy's ``env:`` are not read. Execution is
    # container-only: there is no host-Python fallback and no default env
    # (see the no-container ending below, reached when the registered env
    # no longer has a container record).
    from ..registration import UnknownVersionError
    try:
        entry = version_entry(module_name, method_name, version,
                              root=Path(project_root))
        entry_error = ""
    except UnknownVersionError as exc:
        # The claimed version no longer resolves: the version list was
        # rewritten after the claim. What was claimed cannot be launched.
        changed = _changed_since_claim_message(module_name, method_name, str(exc))
        return {"ok": False, "ending": "changed-since-claim",
                "error_message": changed, "error_traceback": changed}
    except ValueError as exc:
        entry, entry_error = None, str(exc)
    env_value = entry.env_name if entry is not None else ""
    node_env = resolve_node_env(env_value, project_root)
    env_name = node_env.name
    env_error = node_env.error or entry_error

    # The name is retained for the interpreter at the argv-build step, which
    # Environments answers for the version's own image (env name and the
    # env_fingerprint the version recorded). No recursive-dispatch guard is
    # needed: the container runs the method script directly under the
    # image's interpreter — there is no in-container wfc entrypoint at all,
    # so recursion is impossible by construction. Dispatch never builds or
    # pulls: the pre-flights made the image runnable (pulled and checked it
    # if it was missing) before any claim, and docker run carries
    # --pull never.
    container_image_ref: str | None = None
    env_record = node_env.record
    if env_record is not None and entry is not None:
        from ..environments import version_run_ref
        # What Docker is handed: the ref the pre-flight returned for the
        # image the VERSION recorded (a local env's bare image ID when the
        # daemon holds it, else the registry ref it was pulled by), never a
        # recomputed image ID and not the manifest's current image.
        container_image_ref = version_run_ref(
            env_name, entry.env_fingerprint, Path(project_root),
            image=entry.image_ref or env_record.container)

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
               f"Build the env with `wfc register-env {env_label}`, or "
               f"register method '{module_name}/{method_name}' again with "
               f"an env that is built."
        )
        return {"ok": False, "ending": "no-container",
                "error_message": error_msg, "error_traceback": error_msg}

    口 = Step(step_num=3, name="Build docker run command",
             purpose="Verify the method script exists on the host, and assemble "
                     "the docker run argv: bind mounts, image ref, the "
                     "env-interpreter + script inner command, and -e WFC_* "
                     "forwarding with host→container path translation")

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
    # docker. Fail fast with an actionable message instead. The claim
    # hands over the resolver's absolute snapshot script; a relative path
    # is anchored at project_root, not the cwd.
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

    from ..environments import CaptureError, version_bin_dir, version_interpreter
    if entry is None:
        # A container node is dispatched only with its version entry; this
        # names the gap instead of an AttributeError below.
        raise RuntimeError(f"node '{node_id}': no method version entry to "
                           f"read the env image's interpreter from")
    # Interpreter: Environments answers for the version's (env,
    # env_fingerprint), from that image's captures; the env's current record
    # is not read for it, so a version pinned to an older image keeps its
    # interpreter after the env is re-registered. The pre-flights refused a
    # version with no captures; one gone since ends here, never falling
    # back to a bare ``python``.
    try:
        env_python = version_interpreter(env_name, entry.env_fingerprint,
                                         project_root)
        env_bin = version_bin_dir(env_name, entry.env_fingerprint, project_root)
    except CaptureError as exc:
        error_msg = (
            f"node '{node_id}': version v{entry.label} of method "
            f"'{module_name}/{method_name}' runs env '{env_name}' image "
            f"{entry.env_fingerprint}, whose interpreter cannot be read: "
            f"{exc}. No container was started. Register the method again "
            f"with `wfc register-method` (module '{module_name}', method "
            f"'{method_name}') to make a version that runs the env's current "
            f"image."
        )
        return {"ok": False, "ending": "no-container",
                "error_message": error_msg, "error_traceback": error_msg}

    def _claim_ties(invocation: dict) -> dict | None:
        """The run row's two ties at the launch: the claimed code, the invocation record.

        Immediately before the launch (and after script-missing, which keeps
        its own ending), refuse to run code other than what the run row was
        claimed under; then write the invocation record, so a method that
        fails keeps it. A record that cannot be written stops the launch.
        """
        changed = _changed_since_claim(module_name, method_name, _script_host,
                                       version, run_id, Path(project_root))
        if changed is not None:
            return {"ok": False, "ending": "changed-since-claim",
                    "error_message": changed, "error_traceback": changed}
        try:
            _record_invocation(run_id, invocation)
        except Exception as exc:
            message = (f"could not record run {run_id}'s invocation, so the "
                       f"method was not launched: {exc}")
            return {"ok": False, "ending": "launch-failure",
                    "launch_attempted": False, "error_message": message,
                    "error_traceback": _tb.format_exc()}
        return None

    口 = AutoStep(step_num=4, name="Launch the method in its container")
    launched = launch_container(
        image_ref=container_image_ref, interpreter=env_python, env_bin=env_bin,
        script=_script_host, project_root=Path(project_root), run_dir=run_dir,
        step_env=step_env, declared=declared, host_env=wfc_env,
        code_dir=code_dir, gpus=method_gpus, notes=notes,
        before_launch=_claim_ties, copy_entry=entry)
    return launched


@task(purpose="Launch a method in its container and report how it ended: build "
              "the docker argv over Layout's mount table with every container "
              "variable from the one builder, hand the invocation to the "
              "caller's pre-launch check, start the step log, run (stopping "
              "the run's labelled containers when the wait is interrupted), "
              "re-check a writable code copy and read the exit",
      inputs="image ref, interpreter and env bin, the host script, the run "
             "directory, the step's WFC_* values, the declared names, the code "
             "copy, the step log's warnings, an optional pre-launch check",
      outputs="success, or the failure ending (changed-since-claim / "
              "launch-failure / method-failed, or the check's own) with its error",
      critical="Knows no run row: a recorded run's ties to its row (the claim "
               "check and the invocation record) arrive as the pre-launch "
               "check, so the draft step shares this launch without them; the "
               "step's WFC_PIPELINE_ID and WFC_RUN_ID only label its container")
def launch_container(*, image_ref: str, interpreter: str, env_bin: str | None,
                     script: Path, project_root: Path, run_dir: Path,
                     step_env: dict, declared: dict | None, host_env: dict,
                     code_dir: str | None = None, gpus: bool = False,
                     notes: list[str] | None = None,
                     before_launch=None, copy_entry=None) -> dict:
    """Run a method script in its env's container, writing the step's logs to *run_dir*.

    Args:
        image_ref: The image reference handed to Docker.
        interpreter: The image's interpreter, a container path.
        env_bin: The image's ``bin`` directory for ``PATH``, or ``None``.
        script: The method script on the host, under the project root.
        project_root: The project root, bound at ``/work``.
        run_dir: Where ``stdout.log`` and ``stderr.log`` go (the step's
            output directory).
        step_env: The step's WFC_* values, host-side (``step_variables``).
        declared: The method's declared names, or ``None``.
        host_env: The environment of the host process that runs Docker.
        code_dir: The code copy bound read-only at its own ``/work`` path,
            or ``None``.
        gpus: Whether to pass ``--gpus all``.
        notes: Warnings that head the step log.
        before_launch: ``before_launch(invocation) -> dict | None``, called
            with the invocation record right before the launch; a dict it
            returns is the ending and nothing starts.
        copy_entry: The version's list entry, for dropping a written copy's
            stamp; ``None`` for a copy no version owns.

    Returns:
        ``{"ok": True}`` when the method exits 0, else the failure ending.
    """
    import traceback as _tb

    from ..environments import (
        accepts_read_only_binds,
        build_docker_command,
        container_labels,
        stop_containers,
    )
    from .container_vars import container_variables
    from .container_vars import env_flags as _env_flags

    口 = Step(step_num=1, name="Build the docker run command",
             purpose="Bind mounts from Layout's table (the code copy read-only "
                     "when the backend allows it), the interpreter + translated "
                     "script inner command, and every container variable from "
                     "the one builder as -e flags")
    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"
    # UID/GID: ``os.getuid()`` doesn't exist on Windows. Docker Desktop
    # on Windows/macOS ignores ``--user`` anyway; on Linux stock Docker
    # this is what keeps bind-mount writes from landing as root:root.
    _uid = getattr(os, "getuid", lambda: 0)()
    _gid = getattr(os, "getgid", lambda: 0)()
    dvc_cache_dir = layout.dvc_cache_dir(project_root)
    mounts = layout.mount_table(project_root, dvc_cache_dir, code_copy=code_dir)
    copy_writable = code_dir is not None and not accepts_read_only_binds("docker")
    # The script runs directly under the image's interpreter (a container
    # path, never translated); the script itself is translated through the
    # same table the binds are rendered from.
    script_in_container = layout.translate_host_path(str(script), mounts)
    # The container says whose it is (project, pipeline, run; a draft step's
    # run is its draft ref), so a stop finds it. Labels reach neither the
    # invocation record nor any cache key.
    labels = container_labels(project_root,
                              pipeline_id=step_env.get("WFC_PIPELINE_ID"),
                              run_id=step_env.get("WFC_RUN_ID"))
    cmd = build_docker_command(
        image_ref=image_ref, project_root=project_root,
        dvc_cache_dir=dvc_cache_dir,
        inner_argv=[interpreter, script_in_container],
        uid=_uid, gid=_gid, gpus=gpus, code_copy=code_dir, labels=labels,
    )
    # Nothing else from the host (PYTHONPATH in particular) crosses the
    # container barrier. A backend that cannot bind the copy read-only binds
    # it read-write; the interpreter is told not to write bytecode there,
    # and the copy is re-checked after the run.
    container_env = container_variables(
        step=step_env, declared=declared, env_bin=env_bin, mounts=mounts,
        write_bytecode=not copy_writable)
    env_flags = _env_flags(container_env)
    # Splice -e flags right after "docker run --rm" for readability.
    rm_idx = cmd.index("--rm") + 1
    cmd = cmd[:rm_idx] + env_flags + cmd[rm_idx:]
    # What the launch is handed, container-side, from the values above.
    invocation = _invocation_record(
        image_ref=image_ref, interpreter=interpreter,
        script=script_in_container, mounts=mounts,
        copy_writable=copy_writable, env_flags=env_flags,
        uid=_uid, gid=_gid, gpus=gpus, cmd=cmd,
    )

    口 = Step(step_num=2, name="The caller's pre-launch check",
             purpose="A recorded run checks its code is what it was claimed "
                     "under and records the invocation on its row; an ending "
                     "it returns stops the launch")
    if before_launch is not None:
        refused = before_launch(invocation)
        if refused is not None:
            return refused

    口 = Step(step_num=3, name="Run method subprocess",
             purpose="Start stderr.log (the step log) fresh with materialize's "
                     "warnings (a log that cannot be started fails the step "
                     "as a launch failure), then run the assembled docker "
                     "command with stdout/stderr tee'd to the step's logs")
    copy_before: dict[str, str] = {}
    if copy_writable:
        from ..registration import copy_file_digests
        assert code_dir is not None  # copy_writable implies a copy
        copy_before = copy_file_digests(Path(code_dir))
    try:
        noted = _start_step_log(stderr_log, notes)
    except OSError as exc:
        message = (f"could not start the step log {stderr_log}, so the method "
                   f"was not launched: {exc}")
        return {"ok": False, "ending": "launch-failure", "launch_attempted": False,
                "error_message": message, "error_traceback": _tb.format_exc()}
    try:
        result = _run_method_subprocess(
            cmd, cwd=str(project_root), env=host_env,
            stdout_log=stdout_log, stderr_log=stderr_log,
        )
    except BaseException as exc:
        # Ctrl-C (or anything else) ending the wait leaves the container
        # running: Docker does not stop it when its client goes. Stop what
        # carries this run's labels, then let an interrupt through as it was.
        口 = AutoStep(step_num=4, name="Stop the run's containers")
        stopped = stop_containers(labels)
        interrupted = not isinstance(exc, Exception)
        if stopped.error is not None and interrupted:
            print(f"[wfc] run {step_env.get('WFC_RUN_ID')}'s container may still "
                  f"be running: {stopped.error}", file=sys.stderr)
        if interrupted:
            raise
        return {"ok": False, "ending": "launch-failure",
                "error_message": str(exc), "error_traceback": _tb.format_exc()}

    口 = AutoStep(step_num=5, name="Re-check a writable copy after the run")
    written = _copy_written_during_run(copy_before, code_dir, copy_entry,
                                       project_root, writable=copy_writable)
    if written is not None:
        return {"ok": False, "ending": "method-failed",
                "error_message": written, "error_traceback": written}

    口 = Step(step_num=6, name="Read the exit",
             purpose="On a non-zero exit, lift the method's real error from "
                     "what it wrote after wfc's warnings")
    if result.returncode != 0:
        stderr_text: str | None = None
        if stderr_log.exists():
            try:
                stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stderr_text = None
        if stderr_text is not None and noted:
            # The error is the method's own: never a warning wfc wrote.
            stderr_text = stderr_text[len(noted):]
        error_msg, error_tb = interpret_method_exit(result.returncode, stderr_text)
        return {"ok": False, "ending": "method-failed",
                "error_message": error_msg, "error_traceback": error_tb}
    return {"ok": True}


@task(purpose="The read-write fallback's post-run re-check: a run whose "
              "backend could not bind the version's copy read-only fails when "
              "it changed any file there, naming the files, and the copy's "
              "stamp is dropped so its next use re-extracts it",
      inputs="the copy's file digests taken before the launch, the copy, the "
             "version's list entry, the project root, whether the copy was "
             "bound writable",
      outputs="None when nothing changed (or the copy was read-only), else "
              "the method-failed error message")
def _copy_written_during_run(before: dict[str, str], code_dir: str | None,
                             entry, project_root, *,
                             writable: bool) -> str | None:
    """Name the files a run changed in a copy it could write to.

    Args:
        before: :func:`~wfc.registration.copy_file_digests` of the copy,
            taken right before the launch.
        code_dir: The version's extracted copy.
        entry: The version's list entry.
        project_root: The project root.
        writable: Whether the copy was bound read-write (the backend
            declares no read-only binds).

    Returns:
        ``None`` when the copy was read-only or nothing in it changed;
        otherwise the error message naming the changed files.
    """
    if not writable or code_dir is None:
        return None
    from ..registration import changed_copy_files, copy_file_digests, drop_copy_stamp

    changed = changed_copy_files(before, copy_file_digests(Path(code_dir)))
    if not changed:
        return None
    if entry is not None:
        drop_copy_stamp(entry, Path(project_root))
    return (f"The run changed files in its method's code copy {code_dir}, "
            f"which the backend could not bind read-only: "
            f"{', '.join(changed)}. A method must not write into its code "
            f"directory; the copy will be extracted again before its next use.")


def _start_step_log(stderr_log: Path, notes: list[str] | None) -> str:
    """Start a run's step log with materialize's warnings, before the launch.

    ``stderr.log`` is written fresh here, holding only the warnings (or
    nothing), and the method's stderr is appended after them while it
    runs, so the log never has to be rewritten.

    Args:
        stderr_log: The run's ``stderr.log``.
        notes: The warnings, one per line; none starts an empty log.

    Returns:
        The text written ahead of the method's (empty when none was).

    Raises:
        OSError: The log could not be written fresh; the caller fails the
            step rather than launch a method that would append to whatever
            the log already held.
    """
    head = "".join(f"{note}\n" for note in notes or ())
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.write_text(head, encoding="utf-8")
    return head


def _run_method_subprocess(
    cmd: list,
    *,
    cwd: str,
    env: dict,
    stdout_log: Path,
    stderr_log: Path,
):
    """Run the method's ``docker run`` command, tee'ing its output.

    Stdout and stderr go to per-run log files AND the parent process's std
    streams.

    Execution is container-only, so ``cmd`` is always the
    assembled ``docker run ...`` argv (there is no host-Python path). Per-run
    logs land at ``stdout_log`` / ``stderr_log`` (typically inside
    ``.wfc/runs/<run_id>/``); ``stdout_log`` is written fresh and the
    method's stderr is appended to ``stderr_log``, which dispatch has
    already started with wfc's warnings.  Output is also forwarded to the parent's
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

    # stderr.log is appended to: dispatch started it with wfc's warnings.
    with open(stdout_log, "w", encoding="utf-8") as out_f, \
         open(stderr_log, "a", encoding="utf-8") as err_f:
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
