"""wfc CLI: the command-line verbs.

Pipeline verbs, invoked as ``python -m wfc <verb>``. A generated Snakefile's
rules delegate to ``run-step``; its ``onerror`` handler is what calls
``fail_pipeline``:
  register_run       — insert a runs row (status='running'), print run ID
  complete_run       — mark completed, refresh the collected outputs' records (cache is authoritative)
  check_cache        — print the run id the claim phase would reuse for a step (identical fingerprint), or NONE
  finalize_pipeline  — log successful pipeline completion
  fail_pipeline      — mark in-flight runs as failed
  resolve_input      — given a run ID, print the cache path for its output
  lookup_run         — (legacy) find most-recent completed run for method+sample
"""

import argparse
import json
import os
import sys
from pathlib import Path

from . import layout
from .persistence import project_root as get_project_root

# =============================================================================
# Helpers
# =============================================================================


def _not_runnable_message(piece: str, reason: str, hint: str = "") -> str:
    """Format the one-door run-readiness failure message for the CLI.

    Args:
        piece: The failing run-requirement (``"git"`` / ``"docker"``).
        reason: A plain-language reason for the failure.
        hint: Optional fix hint to append.

    Returns:
        A multi-line message pointing the user at ``wfc doctor``.
    """
    lines = [f"This project isn't ready to run — {reason}"]
    if hint:
        lines.append(f"  {hint}")
    lines.append("Run `wfc doctor` to see all run-readiness checks.")
    return "\n".join(lines)


# =============================================================================
# Env-manifest CLI helpers
# =============================================================================

def _docker_gate() -> str | None:
    """The Docker pre-gate ``register-env`` and ``run-pipeline`` ask first.

    ``docker build`` would otherwise fail with a raw subprocess error, so a
    failing daemon probe is reframed into the one-door not-runnable message.

    Returns:
        The message to print when the Docker probe fails, else ``None``.
    """
    from .execution.readiness import check_docker

    dock = check_docker()
    if dock.status == "fail":
        return _not_runnable_message("docker", dock.message, dock.fix_hint)
    return None


def _cli_register_env(args) -> int:
    """``wfc register-env``: turn the parsed arguments into values for the verb.

    The body, with its three input modes and the order of its refusals, is
    :func:`wfc.environments.verbs.register_env`; the Docker probe is passed
    in as :func:`_docker_gate`. With ``--force`` (a rebuild of an existing
    env) the methods whose newest version uses the env are read from
    Registration here and passed in as a value, so Environments never
    imports Registration. A failed read (a missing or uninitialized
    database) counts as no dependants: the rebuild still works and the
    listing is best-effort. ``delete-env`` differs: it refuses when it
    cannot read the version lists.

    Every version recording the env, with the image it recorded, is read
    from the committed version lists and passed in the same way: when the
    rebuilt image is one a version records and the interpreter differs,
    Environments refuses. An unreadable list is passed on as the error,
    and such an interpreter change is refused (fail closed).

    Args:
        args: The parsed ``register-env`` arguments.

    Returns:
        The verb's exit code.
    """
    from .environments import verbs

    dependants: list[str] = []
    recording: list[tuple[str, str]] = []
    recording_error: str | None = None
    if args.force and not args.dry_run:
        from .registration import (
            methods_whose_newest_version_uses_env,
            version_fingerprints_recording_env,
        )
        try:
            dependants = methods_whose_newest_version_uses_env(args.name)
        except Exception:
            dependants = []
        try:
            recording = version_fingerprints_recording_env(args.name)
        except ValueError as exc:
            recording_error = str(exc)
        except RuntimeError:
            pass  # no project: the verb reports it

    return verbs.register_env(
        args.name,
        spec=getattr(args, "spec", None),
        backend=args.backend,
        from_path=getattr(args, "from_path", None),
        image=args.image,
        base_image=args.base_image,
        force=args.force,
        dry_run=args.dry_run,
        interpreter_path=getattr(args, "interpreter_path", None),
        python_path=getattr(args, "python_path", None),
        docker_gate=_docker_gate,
        dependants=dependants,
        groups=getattr(args, "groups", None) or (),
        extras=getattr(args, "extras", None) or (),
        recording_versions=recording,
        recording_read_error=recording_error,
        no_push=getattr(args, "no_push", False),
    )


def _cli_push_env(args) -> int:
    """``wfc push-env``: read the versions, show the plan, confirm, apply.

    The body is Environments' (:func:`wfc.environments.plan_push` and
    :func:`wfc.environments.apply_push`). The images every method version
    records are read here from Registration's committed version lists and
    handed in as values, so Environments never imports Registration; an
    unreadable list refuses, since the worklist would be incomplete. The
    plan is printed before anything is written and confirmed through
    :mod:`wfc.confirm` only when it writes (a base or a push record).

    Args:
        args: The parsed ``push-env`` arguments.

    Returns:
        0 when every image needed is in the registry; 1 on a refusal, a
        declined or unconfirmable plan, a failed push or commit, or an image
        that is not on this machine.
    """
    from .confirm import ConfirmationRequired, confirm
    from .environments import (
        PushRefused,
        apply_push,
        plan_push,
        pushable_envs,
        unpushable_lines,
    )
    from .registration import version_fingerprints_recording_env

    if (args.name is None) == (not args.all_envs):
        print("ERROR: wfc push-env needs an env name or --all"
              + (", not both." if args.name else
                 ": `wfc push-env <name>` pushes one env's images, "
                 "`wfc push-env --all` every env's."), file=sys.stderr)
        return 1
    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    try:
        names = [args.name] if args.name else pushable_envs(project_dir)
        recorded = {name: version_fingerprints_recording_env(name, project_dir)
                    for name in names}
        plan = plan_push(project_dir, args.name, recorded=recorded,
                         registry=args.registry)
    except (PushRefused, ValueError) as exc:
        print(f"ERROR: {exc}\nNothing was pushed.", file=sys.stderr)
        return 1

    print(plan.message)
    if not plan.writes:
        for line in unpushable_lines(plan):
            print(line, file=sys.stderr)
        return 1 if plan.unreachable else 0
    try:
        if not confirm("Proceed?", assume_yes=args.assume_yes,
                       refusal=ConfirmationRequired(
                           "wfc push-env: pushing needs confirmation and there "
                           "is no terminal to ask on. Nothing was pushed or "
                           "written. Re-run with --yes to push as stated above.")):
            print("Nothing was pushed.")
            return 1
    except ConfirmationRequired as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return apply_push(plan).exit_code


#: The guide page that matches GitHub's package pages to what wfc records.
GITHUB_IMAGES_GUIDE_URL = ("https://workflow-canvas.readthedocs.io/en/latest/"
                           "how-to/see-your-images-on-github.html")


def _shares_to_ghcr(project_dir) -> bool:
    """Whether the project's images go, or would go, to GHCR.

    The registry base decides, else the base the project's GitHub remote
    proposes: the same target ``wfc share``'s packages row reads.
    """
    from .environments import package_name, project_github_repository, registry_base

    try:
        base = registry_base(project_dir)
    except (FileNotFoundError, ValueError):
        return False
    if not base:
        repository = project_github_repository(project_dir)
        base = repository.base if repository is not None else None
    if not base:
        return False
    return package_name(base, "x") is not None


def _cli_share(args) -> int:
    """``wfc share``: print what a collaborator will be missing; change nothing.

    The rows are :func:`wfc.execution.share_checks`', rendered through
    :func:`wfc.preflight.render_health_table` under their own heading. When
    the images go to GHCR, a last line names the guide page that matches
    GitHub's package pages to what wfc records.
    Nothing is written, committed or pushed.

    Args:
        args: The parsed ``share`` arguments (none).

    Returns:
        0 whenever there is a project (the rows only warn); 1 outside one.
    """
    from .execution import share_checks
    from .preflight import render_health_table

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    print(render_health_table(share_checks(project_dir),
                              heading="Sharing this project:"))
    if _shares_to_ghcr(project_dir):
        print(f"\nSee your images on GitHub: {GITHUB_IMAGES_GUIDE_URL}")
    return 0


#: ``wfc runs``' column headings, in print order.
RUNS_COLUMNS = ("RUN", "PIPELINE", "NODE", "METHOD", "SAMPLE", "STATUS")


def _cli_runs(args) -> int:
    """``wfc runs``: print each run's id, pipeline, node, method, sample and status.

    The rows are Persistence's (:func:`wfc.persistence.list_runs`), oldest
    first, as a table padded to its widest cell; an empty column value
    prints as ``-``. Nothing is written, so nothing is confirmed.

    Args:
        args: The parsed ``runs`` arguments (``pipeline_id``, ``method``).

    Returns:
        0 whenever there is a project, with or without runs; 1 outside one
        or when ``--method`` is not module-qualified.
    """
    from .persistence import list_runs

    try:
        get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    try:
        listings = list_runs(args.pipeline_id, args.method)
    except ValueError as exc:
        print(f"ERROR: --method: {exc}", file=sys.stderr)
        return 1
    if not listings:
        scope = []
        if args.pipeline_id:
            scope.append(f"in pipeline {args.pipeline_id}")
        if args.method:
            scope.append(f"of method {args.method}")
        print(f"No runs {' and '.join(scope)}." if scope else "No runs.")
        return 0
    rows = [RUNS_COLUMNS] + [
        tuple(str(value) if value not in (None, "") else "-"
              for value in (r.run_id, r.pipeline_id, r.node_id, r.method,
                            r.sample, r.status))
        for r in listings
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(RUNS_COLUMNS))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
              .rstrip())
    return 0


def _cancel_plan_lines(pipeline_id: str, record, running) -> list[str]:
    """What ``wfc cancel`` says it will stop, before it asks.

    Args:
        pipeline_id: The pipeline.
        record: Its ``PipelineRecord``, or None when it has none.
        running: Its running rows, as ``RunListing`` values.

    Returns:
        The lines: the pipeline, its running steps, the engine. The engine
        is said to be stopped only while the recorded process is alive
        (Execution's ``probe_process``); otherwise it is said to be gone.
    """
    from .execution import ProcessState, probe_process

    started = ""
    if record is not None:
        started = (" (started from the canvas)" if record.entry_point == "canvas"
                   else " (started by wfc run-pipeline)")
    lines = [f"wfc cancel stops pipeline {pipeline_id}{started}."]
    if running:
        lines.append(f"Running steps ({len(running)}):")
        lines += [f"  run {r.run_id}: {r.node_id or '-'} on {r.sample or '-'} "
                  f"({r.method or 'no method'})" for r in running]
    else:
        lines.append("Running steps: none.")
    if record is None:
        lines.append("Engine: none recorded (the pipeline has no record).")
    elif record.engine is None:
        lines.append("Engine: not started yet.")
    elif probe_process(record.engine) is ProcessState.ALIVE:
        lines.append(f"Engine: process {record.engine.pid}, with every process it "
                     f"started, will be stopped.")
    else:
        lines.append(f"Engine: process {record.engine.pid} is already gone; "
                     f"nothing is stopped.")
    lines.append("Its running steps are cancelled, and every step that has not "
                 "run is recorded as cancelled by you.")
    return lines


def _cancel_result_lines(outcome) -> list[str]:
    """What ``wfc cancel`` says it did.

    Args:
        outcome: The ``CancelOutcome`` of the cancel.

    Returns:
        The lines: the rows cancelled, the engine, and who records the rest.
    """
    lines = [f"Cancelled pipeline {outcome.pipeline_id}: {outcome.runs_cancelled} "
             f"running step(s) cancelled."]
    if outcome.engine_killed:
        lines.append("The engine was stopped.")
    elif outcome.had_record:
        lines.append("No engine was stopped: none was running as recorded.")
    if outcome.owner_alive:
        lines.append("Its owner is still running: it records the remaining steps "
                     "as cancelled as it ends.")
    elif outcome.close_out is not None:
        who = ("Its owner is gone" if outcome.had_record
               else "No process was running it")
        lines.append(f"{who}, so wfc cancel recorded the remaining steps: "
                     f"{outcome.close_out.rows_written} record(s) written.")
    return lines


def _cli_cancel(args) -> int:
    """``wfc cancel <pipeline id>``: say what it stops, confirm, then cancel.

    The cancel is Execution's (:func:`wfc.execution.cancel_recorded_pipeline`).
    The pipeline's record and running rows are read first only to name the
    pipeline, its running steps and its engine; a pipeline with neither is
    refused before anything is asked. The question goes through
    :mod:`wfc.confirm` (``--yes`` answers yes); a no changes nothing.

    Args:
        args: The parsed ``cancel`` arguments (``pipeline_id``,
            ``assume_yes``).

    Returns:
        0 when the pipeline was cancelled; 1 outside a project, for an
        unknown pipeline, on a declined or unconfirmable cancel, or when the
        record cannot be read or written.
    """
    from .confirm import ConfirmationRequired, confirm
    from .execution import (
        PipelineRecordError,
        UnknownPipelineError,
        cancel_recorded_pipeline,
        read_pipeline_record,
    )
    from .persistence import list_runs

    pipeline_id = args.pipeline_id
    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    try:
        record = read_pipeline_record(project_dir, pipeline_id)
    except PipelineRecordError as exc:
        print(f"ERROR: {exc}\nNothing was cancelled.", file=sys.stderr)
        return 1
    running = [r for r in list_runs(pipeline_id) if r.status == "running"]
    if record is None and not running:
        print(f"ERROR: pipeline {pipeline_id} has no record and no running step: "
              f"there is nothing to cancel. `wfc runs --pipeline {pipeline_id}` "
              f"lists its runs.", file=sys.stderr)
        return 1

    print("\n".join(_cancel_plan_lines(pipeline_id, record, running)))
    try:
        if not confirm("Proceed?", assume_yes=args.assume_yes,
                       refusal=ConfirmationRequired(
                           "wfc cancel: cancelling needs confirmation and there "
                           "is no terminal to ask on. Nothing was cancelled. "
                           "Re-run with --yes to cancel as stated above.")):
            print("Nothing was cancelled.")
            return 1
    except ConfirmationRequired as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        outcome = cancel_recorded_pipeline(project_dir, pipeline_id)
    except UnknownPipelineError as exc:
        # The pipeline ended between the question and the cancel.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except PipelineRecordError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\n".join(_cancel_result_lines(outcome)))
    return 0


def _export_plan_lines(plan) -> list[str]:
    """The plan ``wfc export-run`` prints: its steps, substitutions and pulls."""
    asked = ", ".join(str(r) for r in plan.requested)
    lines = [f"The bundle holds {len(plan.steps)} step(s) for run(s) {asked}:"]
    for step in plan.steps:
        version = f" v{step.version_label}" if step.version_label is not None else ""
        image = (step.pullable_ref or step.image or "no image recorded")
        # Under --with-image the bundle carries the image, so it is not local only.
        local = ("" if step.pullable_ref
                 else " (saved into the bundle)" if plan.with_image else " (local only)")
        lines.append(f"  {step.number}. run {step.run_id}: {step.label}{version}, "
                     f"sample {step.sample or '-'}, image {image}{local}")
    lines += [f"Run {hit} reused run {original} (same cache key), so run "
              f"{original} is the step in this bundle."
              for hit, original in plan.substitutions]
    if plan.archived:
        lines.append(f"Archived {plan.archived} output(s) that had no hash yet.")
    if plan.pulls:
        lines.append(f"{len(plan.pulls)} cache entr{'y' if len(plan.pulls) == 1 else 'ies'}"
                     f" will be pulled back from the remote.")
    return lines


def _cli_export_run(args) -> int:
    """``wfc export-run``: print the plan, refuse or confirm, then write the zip.

    The body is Run bundle's (:func:`wfc.run_bundle.plan_export` and
    :func:`wfc.run_bundle.write_bundle`). The plan is printed first. Any
    refusal, an ``--out`` that names a directory among them, is listed
    with every other one and nothing is written. A step whose image was
    never pushed is warned about and confirmed through :mod:`wfc.confirm`
    (``--yes`` answers yes); the plan's archive pass completes hashes the
    runs already owe and asks nothing. Writing prints one line per step,
    then the bundle's size.

    Args:
        args: The parsed ``export-run`` arguments.

    Returns:
        0 when the bundle is written; 1 outside a project, on any refusal,
        on a declined or unconfirmable plan, or when the zip cannot be
        written.
    """
    from .confirm import ConfirmationRequired, confirm
    from .run_bundle import ExportRefusedError, out_refusal, plan_export, write_bundle

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    plan = plan_export(args.run_ids, project_dir=project_dir,
                       with_outputs=args.with_outputs, with_image=args.with_image)
    print("\n".join(_export_plan_lines(plan)))

    reasons = [r.reason for r in plan.refusals]
    refused_out = out_refusal(args.out)
    if refused_out is not None:
        reasons.append(refused_out)
    if reasons:
        print("ERROR: the bundle cannot be complete:", file=sys.stderr)
        for reason in reasons:
            print(f"  - {reason}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 1

    if plan.local_only:
        print("\n".join(f"WARNING: {line}" for line in plan.warning_lines))
        try:
            if not confirm("Proceed?", assume_yes=args.assume_yes,
                           refusal=ConfirmationRequired(
                               "wfc export-run: a bundle that points at an image "
                               "only this machine holds needs confirmation, and "
                               "there is no terminal to ask on. Nothing was "
                               "written. Re-run with --yes to export as stated "
                               "above.")):
                print("Nothing was written.")
                return 1
        except ConfirmationRequired as exc:
            print(str(exc), file=sys.stderr)
            return 1

    def report(part: str) -> None:
        if part.startswith("steps/"):
            print(f"Wrote {part}", flush=True)

    try:
        result = write_bundle(plan, args.out, project_dir=project_dir,
                              progress_fn=report)
    except ExportRefusedError as exc:
        print("ERROR: the bundle cannot be complete:", file=sys.stderr)
        for reason in exc.reasons:
            print(f"  - {reason}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: could not write {args.out}: {exc}\nNothing was written.",
              file=sys.stderr)
        return 1
    print(f"Wrote {result.path} ({result.size:,} bytes, {result.steps} step(s)).")
    return 0


# =============================================================================
# argparse entry-point
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the low-level ``workflow-canvas`` verbs."""
    parser = argparse.ArgumentParser(prog="workflow-canvas", description="Workflow Canvas CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- register_run --
    reg = sub.add_parser("register_run", help="Register a new run (status=running)")
    reg.add_argument("--method", required=True)
    reg.add_argument("--module", required=True, help="Module that owns this method")
    reg.add_argument("--sample", required=True)
    reg.add_argument("--params", default=None, help="JSON string of parameters")
    reg.add_argument("--parent-run-id", action="append", default=None,
                             help="One parent entry per use: input:output:run (e.g. data:merged:412) "
                             "or input:run. Every entry names the input slot it feeds. "
                             "Repeat the flag for each parent; fan-in is repeated "
                             "entries with the same input.")
    reg.add_argument("--nf-process-name", default=None)
    reg.add_argument("--pipeline-id", default=None, help="Pipeline execution ID (UUID)")

    # -- pre_run --
    pr = sub.add_parser("pre_run",
                         help="Versioned pre-run hook: git check + cache lookup + run registration")
    pr.add_argument("--method", required=True)
    pr.add_argument("--module", required=True)
    pr.add_argument("--sample", required=True)
    pr.add_argument("--params", default=None, help="JSON string of parameters")
    pr.add_argument("--parent-run-id", action="append", default=None,
                             help="One parent entry per use: input:output:run (e.g. data:merged:412) "
                             "or input:run. Every entry names the input slot it feeds. "
                             "Repeat the flag for each parent; fan-in is repeated "
                             "entries with the same input.")
    pr.add_argument("--pipeline-id", default=None)
    pr.add_argument("--nf-process-name", default=None)
    pr.add_argument("--git-commit", default=None,
                    help="Pre-computed commit SHA (skips git check; for testing).")

    # -- complete_run --
    comp = sub.add_parser("complete_run", help="Mark a run as completed")
    comp.add_argument("--run-id", type=int, required=True)
    comp.add_argument("--status", default="completed")
    comp.add_argument("--output", nargs="*", default=[], help="Output file paths (in archive)")
    comp.add_argument("--metrics", default=None, help="JSON string of metrics")
    comp.add_argument("--error", default=None, help="Error message for failed runs")
    comp.add_argument("--traceback", default=None, dest="traceback_str",
                       help="Error traceback for failed runs")

    # -- check_cache --
    cache = sub.add_parser("check_cache", help="Check for cached identical run")
    cache.add_argument("--method", required=True)
    cache.add_argument("--module", required=True,
                       help="Module the method is registered in. Required: a method "
                            "name alone does not identify a method, and the cache "
                            "key is over <module>.<method>.")
    cache.add_argument("--sample", required=True)
    cache.add_argument("--params", default=None, help="JSON string of parameters")
    cache.add_argument("--parent-run-id", action="append", default=None,
                             help="One parent entry per use: input:output:run (e.g. data:merged:412) "
                             "or input:run. Every entry names the input slot it feeds. "
                             "Repeat the flag for each parent; fan-in is repeated "
                             "entries with the same input.")


    # -- restore-sample --
    rss = sub.add_parser("restore-sample", help="Restore a sample from DVC cache to .wfc/workspace/samples/")
    rss.add_argument("--name", required=True, help="Sample identifier")
    rss.add_argument("--hash", default=None, dest="content_hash",
                     help="Expected content hash (default: the registered hash)")

    # -- finalize_pipeline --
    finalize = sub.add_parser("finalize_pipeline", help="Log successful pipeline completion")
    finalize.add_argument("--pipeline-id", required=True)

    # -- fail_pipeline --
    fail = sub.add_parser("fail_pipeline", help="Mark in-flight runs as failed")
    fail.add_argument("--pipeline-id", required=True)

    # -- resolve_input --
    resolve = sub.add_parser("resolve_input", help="Get archived output path for a run")
    resolve.add_argument("--run-id", type=int, required=True)

    # -- lookup_run (legacy) --
    look = sub.add_parser("lookup_run", help="(Legacy) Find most-recent completed run")
    look.add_argument("--method", required=True)
    look.add_argument("--module", required=True)
    look.add_argument("--sample", required=True)
    look.add_argument("--nf-process-name", default=None)

    # -- init --
    init_p = sub.add_parser("init", help="Scaffold a new wfc project directory")
    init_p.add_argument("--dir", default=".", help="Target directory (default: current directory)")
    init_p.add_argument("--git", action="store_true",
                        help=argparse.SUPPRESS)
    init_p.add_argument("--archive", default=None, metavar="PATH",
                        help="Archive location, without prompting: a directory "
                             "path or a DVC remote URL such as s3://bucket/path "
                             "(default: ~/.wfc/archives/<project>)")
    init_p.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Accept every default and apply the listed changes "
                             "without asking (for scripts and CI)")

    # -- doctor --
    sub.add_parser(
        "doctor",
        help="Check run-readiness (git / DVC / Docker / samples). Exits "
             "non-zero on any fail.",
    )

    # -- seed (retired) --
    sub.add_parser(
        "seed",
        help="(retired) Replaced by `wfc demo` — prints a pointer and exits "
             "non-zero",
    )

    # -- demo --
    dm = sub.add_parser(
        "demo",
        help="Scaffold and serve a runnable demo pipeline in an initialised "
             "project (tear it down with `wfc demo --remove`)",
    )
    dm.add_argument("--dir", dest="demo_dir", default=None,
                    help="Initialised project directory (default: current directory)")
    dm.add_argument("--port", type=int, default=8500,
                    help="Canvas port (default: 8500)")
    dm.add_argument("--no-open", action="store_true", dest="no_open",
                    help="Scaffold and serve without opening a browser")
    dm.add_argument("--force", action="store_true",
                    help="Replace an existing demo")
    dm.add_argument("--remove", action="store_true", dest="remove",
                    help="Remove every demo-owned entity, run, and file")
    dm.add_argument("--purge-image", action="store_true", dest="purge_image",
                    help="With --remove: also delete the local/wfc-demo-env "
                         "Docker image")
    dm.add_argument("--yes", action="store_true", dest="assume_yes",
                    help="With --remove: skip the confirmation prompt")

    # -- register-sample --
    rs = sub.add_parser(
        "register-sample",
        help="Register a data sample: store its content in the DVC cache and "
             "record where a restore puts it",
    )
    rs.add_argument("--name", required=True, help="Sample identifier (e.g. CFPAC_ERKi)")
    rs.add_argument("--source", required=True,
                    help="Path to the source data file or directory; its content "
                         "is cached, and the source itself is left in place")
    rs.add_argument("--manifest", default=None,
                    help="Optional YAML file describing the sample; its one "
                         "allowed key is 'description'. Metadata only: it never "
                         "changes the sample's identity")

    # -- register-module --
    rm = sub.add_parser("register-module", help="Create or update a module in the database")
    rm.add_argument("--name", required=True, help="Module name (e.g. data_preprocessing)")
    rm.add_argument("--description", default=None, help="Human-readable description")
    rm.add_argument("--contracts", default=None,
                    help='Contracts: path to a JSON file, or inline JSON string. '
                         'Optional if --module-dir has module.yaml. '
                         'Example: [{"type":"output","name":"x","value_type":".parquet"}]')
    rm.add_argument("--module-dir", default=None,
                    help="Directory holding the module's module.yaml, e.g. "
                         "src/my_analysis (default: modules/<name>/)")

    # -- register-method --
    rme = sub.add_parser("register-method", help="AST-scan method script and register method + params")
    rme.add_argument("method_dir", help="Path to the method directory containing the method script")
    rme.add_argument("--module", required=True, help="Module name this method belongs to")
    rme.add_argument("--name", default=None, help="Method name (defaults to directory name)")
    rme.add_argument("--script", default=None,
                     help="Script file name in the method directory (default: the "
                          "method.yaml script: key, else the one <name>.py, .R, .r "
                          "or .sh file)")
    rme.add_argument("--yes", action="store_true", dest="assume_yes",
                     help="Register a new method or add a version without "
                          "asking (for scripts and CI); the message saying "
                          "what it does still prints")

    # -- run-pipeline --
    rp = sub.add_parser("run-pipeline", help="Generate Snakefile and run the pipeline")
    rp.add_argument("--pipeline", required=True, help="Path to the pipeline JSON file")
    rp.add_argument("--project-root", default=None,
                    help="Project directory (default: the project containing the "
                         "current directory)")
    rp.add_argument("--wfc-root", default=None,
                    help=argparse.SUPPRESS)
    rp.add_argument("--cores", type=int, default=4, help="Number of jobs Snakemake runs at once (default: 4)")
    rp.add_argument("--snakefile", default=None,
                    help="Where to write the Snakefile (default: the pipeline's log "
                         "directory, <project-root>/.wfc/runs/pipelines/<id>/Snakefile)")
    rp.add_argument("--archive", action="store_true", default=True, dest="archive",
                    help="Archive outputs after pipeline completion (default: on)")
    rp.add_argument("--no-archive", action="store_false", dest="archive",
                    help="Skip output archiving; archive later with "
                         "wfc cache archive")
    rp.add_argument("--keep-going", action="store_true", default=False, dest="keep_going",
                    help="Pass --keep-going to Snakemake: a failed job doesn't "
                         "cancel independent jobs (useful for fan-out pipelines)")

    # -- canvas --
    cv = sub.add_parser("canvas", help="Launch the workflow canvas web UI")
    cv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    cv.add_argument("--port", type=int, default=8500, help="Bind port (default: 8500)")
    cv.add_argument("--reload", action="store_true",
                    help="Restart the server when wfc's source changes (for wfc development)")
    cv.add_argument("--project-root", default=None,
                    help="Project directory to serve (default: the project "
                         "containing the current directory). It must contain "
                         ".wfc/wfc.db.")

    # -- run-step --
    rs_cmd = sub.add_parser("run-step", help="Execute a single pipeline step end-to-end")
    rs_cmd.add_argument("--node-id", required=True, help="Unique node identity in the pipeline")
    rs_cmd.add_argument("--sample", required=True, help="Sample identifier")
    rs_cmd.add_argument("--variant", default="default", help="Parameter variant name")
    rs_cmd.add_argument("--method", default=None, help="Method name (inline fallback)")
    rs_cmd.add_argument("--module", default=None, help="Module name (inline fallback)")
    rs_cmd.add_argument("--params", default=None, help="JSON string of parameters")
    rs_cmd.add_argument("--parent-run-id", action="append", default=None,
                             help="One parent entry per use: input:output:run (e.g. data:merged:412) "
                             "or input:run. Every entry names the input slot it feeds. "
                             "Repeat the flag for each parent; fan-in is repeated "
                             "entries with the same input.")
    rs_cmd.add_argument("--pipeline-id", default=None, help="Pipeline execution ID")
    rs_cmd.add_argument("--pipeline-json", default=None, help="Path to pipeline JSON file")
    rs_cmd.add_argument("--git-commit", default=None, help="Pre-computed git commit SHA")
    rs_cmd.add_argument("--ref-input", action="append", default=None,
                        help="Static ref input as 'label=path' from run_reference nodes. "
                             "Repeatable. Orchestrator resolves these; run-step stays "
                             "topology-agnostic.")
    rs_cmd.add_argument("--collapsed-sample", action="append", default=None,
                        help="For collapsed-fan-in roots invoked with --sample __all__, "
                             "the bundled sample identities (one flag per sample). "
                             "Repeatable. The runtime walks .wfc/workspace/samples/<s>/ per name "
                             "and accumulates the per-sample data files into the fan-in "
                             "slot. Order is preserved.")

    # -- pipeline-summary --
    ps_cmd = sub.add_parser("pipeline-summary", help="Aggregate outcome sidecars into summary")
    ps_cmd.add_argument("--pipeline-id", required=True, help="Pipeline execution ID")

    # -- list-envs --
    sub.add_parser(
        "list-envs",
        help="List container envs in .wfc/envs.json",
    )

    # -- show-env --
    se_cmd = sub.add_parser(
        "show-env",
        help="Print full record for a container env",
    )
    se_cmd.add_argument("name", help="Env name (key in .wfc/envs.json)")

    # -- delete-env --
    de_cmd = sub.add_parser(
        "delete-env",
        help="Remove a container env from .wfc/envs.json (registry tag untouched)",
    )
    de_cmd.add_argument("name", help="Env name to delete")
    de_cmd.add_argument(
        "--force", action="store_true",
        help=(
            "Skip the confirmation prompt. An env that a method version "
            "uses is refused even with --force."
        ),
    )

    # -- register-env --
    from .environments.backends import BACKEND_NAMES
    re_cmd = sub.add_parser(
        "register-env",
        help="Build a container image for an env and register it in "
             ".wfc/envs.json. Accepts a positional typed-spec "
             "(conda:<env> / pixi:<proj>:<env>) to capture from a live "
             "env, --from <path> to copy a source file, or "
             "--backend byo --image for a bring-your-own image.",
        epilog=(
            "Capturing from a live env (conda:<env>, pixi:<proj>:<env>) "
            "records the env's current state — including any ad-hoc "
            "`pip install` mutations on top of the conda/pixi env. "
            "Inspect the captured package list in the canvas env-detail "
            "panel (or in .wfc/captures/<name>/<fingerprint>/packages.lock) "
            "before relying on the image for downstream runs."
        ),
    )
    re_cmd.add_argument("name", help="Env name (key in .wfc/envs.json)")
    re_cmd.add_argument(
        "spec", nargs="?", default=None,
        help="Local env to capture: conda:<env>, pixi:<name> or "
             "pixi:<proj>:<env>. Cannot be combined with --backend or "
             "--from.",
    )
    re_cmd.add_argument(
        "--backend", default=None,
        choices=list(BACKEND_NAMES),
        help="Build backend. Required with --from and for byo; inferred "
             "from spec when capturing a local env.",
    )
    re_cmd.add_argument(
        "--from", dest="from_path", default=None, metavar="PATH",
        help="Lock file (pixi.lock) or conda explicit list to build "
             "from, or a uv project directory or its uv.lock. Requires "
             "--backend.",
    )
    re_cmd.add_argument(
        "--group", dest="groups", action="append", default=None,
        metavar="GROUP",
        help="A uv project's dependency group to install (repeatable). "
             "Only named groups are installed: the project's default "
             "groups are not. The dev group is refused (the export "
             "passes --no-dev). Valid with --backend uv only.",
    )
    re_cmd.add_argument(
        "--extra", dest="extras", action="append", default=None,
        metavar="EXTRA",
        help="A uv project's extra to install (repeatable). Valid with "
             "--backend uv only.",
    )
    re_cmd.add_argument("--image", default=None,
                        help="docker:// reference for --backend byo")
    re_cmd.add_argument("--base-image", default=None,
                        help="Override the default base image for this env")
    re_cmd.add_argument(
        "--interpreter", dest="interpreter_path", default=None, metavar="PATH",
        help="Path of the interpreter inside the container that runs "
             "method scripts, e.g. /opt/conda/bin/Rscript for R, /bin/bash "
             "for bash, or the Python of a byo image whose Python is not on "
             "PATH (default: the env's Python for pixi and conda, 'python' "
             "for byo).",
    )
    re_cmd.add_argument(
        "--python", dest="python_path", default=None, metavar="PATH",
        help="Alias for --interpreter. Pass only one of the two.",
    )
    re_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Write the Dockerfile to .wfc/build/<name>/Dockerfile and "
             "stop without running Docker. Use it with --from.",
    )
    re_cmd.add_argument(
        "--force", action="store_true",
        help="Replace an existing env of the same name (without it, "
             "registering an existing name is an error).",
    )
    re_cmd.add_argument(
        "--no-push", dest="no_push", action="store_true",
        help="Keep the image on this machine even when the project has a "
             "registry ([registry] url); `wfc push-env <name>` pushes it "
             "later. Without it the image is pushed before the env is "
             "recorded.",
    )

    # -- push-env --
    pe_cmd = sub.add_parser(
        "push-env",
        help="Push an env's images to the project's registry: its current "
             "image and every image a method version records, when they are "
             "on this machine and not yet pushed. The first run sets the "
             "registry ([registry] url) and commits it.",
    )
    pe_cmd.add_argument("name", nargs="?", default=None,
                        help="Env name (key in .wfc/envs.json)")
    pe_cmd.add_argument("--all", dest="all_envs", action="store_true",
                        help="Push the images of every env instead of one")
    pe_cmd.add_argument(
        "--registry", default=None, metavar="URL",
        help="The registry base to set on the first run (for example "
             "ghcr.io/<owner>/<repo>), when the git remote is not on GitHub "
             "or another base is wanted. Once a base is set it cannot be "
             "changed here.",
    )
    pe_cmd.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Push and write as stated without asking (for "
                             "scripts and CI)")

    # -- share --
    sub.add_parser(
        "share",
        help="List what a collaborator would be missing before you share the "
             "project (git remote, registry login and base, unpushed images, "
             "unconnected packages, method versions that cannot run), each "
             "with the command that fixes it. Changes nothing.",
    )

    # -- dev-loop commands --
    # Three convenience verbs that launch an ephemeral container of the
    # env's image with the same bind-mount layout wfc run-step uses. The
    # ephemeral-container reminder is in each subparser's epilog so it
    # surfaces in --help output.
    _EPHEMERAL_REMINDER = (
        "Note: the container is spawned fresh per invocation; changes "
        "made during the session, including packages installed via pip, "
        "do not persist into pipeline runs."
    )

    # -- jupyter --
    jup_cmd = sub.add_parser(
        "jupyter",
        help="Launch Jupyter Lab in an ephemeral container of the env's image",
        epilog=_EPHEMERAL_REMINDER,
    )
    jup_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")
    jup_cmd.add_argument(
        "--port", type=int, default=None,
        help="Host port for Jupyter (default: the first free port "
             "from 8888 to 8999)",
    )

    # -- shell --
    sh_cmd = sub.add_parser(
        "shell",
        help="Drop into an interactive shell in an ephemeral container",
        epilog=_EPHEMERAL_REMINDER,
    )
    sh_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")

    # -- exec --
    ex_cmd = sub.add_parser(
        "exec",
        help="Run a command in an ephemeral container of the env's image",
        epilog=_EPHEMERAL_REMINDER,
    )
    ex_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")
    ex_cmd.add_argument(
        "cmd", nargs=argparse.REMAINDER,
        help="Command to run inside the container (everything after <env>)",
    )

    # -- cache --
    cache_cmd = sub.add_parser("cache", help="Cache management commands")
    cache_sub = cache_cmd.add_subparsers(dest="cache_command", required=True)

    prune_cmd = cache_sub.add_parser(
        "prune",
        help="Remove old run archives and optionally DVC local cache entries",
    )
    prune_cmd.add_argument(
        "--all", action="store_true", dest="prune_all",
        help="Remove every run directory, not only unreferenced ones "
             "(--include-local already removes every cache entry but the "
             "unpushed ones a record names)",
    )
    prune_cmd.add_argument(
        "--include-local", action="store_true",
        help="Also prune the DVC local cache (.dvc/cache/): an entry a record "
             "names is kept until it is pushed; nothing unpushed is removed",
    )
    prune_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting",
    )
    prune_cmd.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt and the archive check. It never "
             "removes a cache entry that was not pushed (run `wfc cache "
             "push` first).",
    )

    # -- cache push (push every waiting entry now) --
    cache_sub.add_parser(
        "push",
        help="Push every output, sample and blob not yet on the remote now, "
             "and report each failure",
    )

    # -- cache archive (deferred output archiving) --
    archive_cmd = cache_sub.add_parser(
        "archive",
        help="Hash and cache un-archived outputs from completed runs",
    )
    archive_cmd.add_argument(
        "--run-id", type=int, default=None,
        help="Archive only outputs from a specific run ID",
    )

    # -- verify (re-hash everything wfc holds) --
    verify_cmd = sub.add_parser(
        "verify",
        help="Re-hash every sample, output, cache object, checkout and blob "
             "wfc holds and list each that is not what wfc recorded",
        epilog=(
            "Exits non-zero when anything is wrong, including after --repair "
            "when something could not be repaired. --repair restores a "
            "sample's copy from its cache entry, rebuilds a checkout and "
            "pulls a pushed object again; it lists what only a re-run or a "
            "re-registration repairs."
        ),
    )
    verify_cmd.add_argument(
        "--repair", action="store_true",
        help="Put back what wfc can restore exactly, and list the rest",
    )
    verify_cmd.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Print the report as JSON, problems grouped by kind",
    )

    # -- runs (list the project's runs) --
    runs_cmd = sub.add_parser(
        "runs",
        help="List every run: run id, pipeline, node, method, sample and status",
    )
    runs_cmd.add_argument(
        "--pipeline", default=None, metavar="ID", dest="pipeline_id",
        help="List only this pipeline's runs",
    )
    runs_cmd.add_argument(
        "--method", default=None, metavar="MODULE.METHOD",
        help="List only this method's runs, named with its module "
             "(module.method)",
    )

    # -- cancel (stop a running pipeline) --
    cancel_cmd = sub.add_parser(
        "cancel",
        help="Cancel a running pipeline, whichever process runs it: stop its "
             "engine, cancel its running steps and record every step that "
             "has not run as cancelled",
        epilog=(
            "The pipeline, its running steps and the engine to stop are "
            "printed, then confirmed; --yes answers yes. A pipeline with no "
            "record and no running step is refused."
        ),
    )
    cancel_cmd.add_argument(
        "pipeline_id", metavar="PIPELINE_ID",
        help="The pipeline to cancel (`wfc runs` lists each run's pipeline)",
    )
    cancel_cmd.add_argument(
        "--yes", action="store_true", dest="assume_yes",
        help="Cancel as stated without asking (for scripts and CI)",
    )

    # -- export (run-output export) --
    export_cmd = sub.add_parser(
        "export",
        help="Export a run's archived output(s) out of managed storage",
        epilog=(
            "Destination semantics: if DEST is an existing directory the "
            "file lands inside it under its original name; an existing "
            "destination file is refused unless --force is given. "
            "With --all, DEST must be a directory: each output lands under "
            "its file name (a directory output under its directory name), "
            "and outputs of the run that share a file name land in their "
            "slot folders (<DEST>/<slot>/<file name>). Exported copies are "
            "writable; --path prints the read-only local path instead of "
            "copying (a file's cache entry, or a directory output's checkout "
            "under the project). Run `wfc export --run <id>` with no --slot to list "
            "the run's output slots and their file names."
        ),
    )
    export_cmd.add_argument(
        "--run", type=int, required=True, dest="run_id", metavar="ID",
        help="Run ID to export from (`wfc runs` lists them)",
    )
    export_cmd.add_argument(
        "--slot", default=None, metavar="SLOT",
        help="Output slot to export (omit to list the run's output slots "
             "and file names)",
    )
    export_cmd.add_argument(
        "--dest", default=None, metavar="DEST",
        help="Destination file or directory. An existing directory "
             "receives the file under its own name",
    )
    export_cmd.add_argument(
        "--all", action="store_true", dest="export_all",
        help="Export every output of the run into DEST (a directory)",
    )
    export_cmd.add_argument(
        "--path", action="store_true", dest="path_only",
        help="Print the resolved read-only local path instead of copying "
             "(with --all: one 'name<TAB>path' line per output)",
    )
    export_cmd.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing destination file",
    )

    # -- export-run (a run bundle anyone can rerun) --
    export_run_cmd = sub.add_parser(
        "export-run",
        help="Export runs and every run above them as a zip anyone can rerun "
             "without wfc",
        epilog=(
            "The plan is printed first. If anything stops the bundle from "
            "being complete, every reason is listed and nothing is written. "
            "A step whose image was never pushed to a registry is asked "
            "about: the bundle would point at an image only this machine "
            "holds. --yes answers yes."
        ),
    )
    export_run_cmd.add_argument(
        "--run", type=int, action="append", required=True, dest="run_ids",
        metavar="ID",
        help="A run to export, with every run above it; repeat for more "
             "(`wfc runs` lists them)",
    )
    export_run_cmd.add_argument(
        "--out", required=True, metavar="FILE.zip",
        help="The zip file to write (not a directory)",
    )
    export_run_cmd.add_argument(
        "--with-outputs", action="store_true", dest="with_outputs",
        help="Embed every output's bytes, not only their hashes",
    )
    export_run_cmd.add_argument(
        "--with-image", action="store_true", dest="with_image",
        help="Save each step's image into the bundle",
    )
    export_run_cmd.add_argument(
        "--yes", action="store_true", dest="assume_yes",
        help="Skip the confirmation prompt for images only this machine holds",
    )

    return parser


def cli_main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the selected verb, returning its exit code."""
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    if args.command == "register_run":
        params = json.loads(args.params) if args.params else None
        from .execution.claim import register_run
        run_id = register_run(
            method_name=args.method,
            module_name=args.module,
            sample=args.sample,
            params=params,
            parent_run_ids=args.parent_run_id,
            nf_process_name=args.nf_process_name,
            pipeline_id=args.pipeline_id,
        )
        print(run_id)
        return 0

    elif args.command == "pre_run":
        params = json.loads(args.params) if args.params else None
        from .execution.claim import pre_run
        try:
            flag, run_id = pre_run(
                method_name=args.method,
                module_name=args.module,
                sample=args.sample,
                params=params,
                parent_run_ids=args.parent_run_id,
                pipeline_id=args.pipeline_id,
                nf_process_name=args.nf_process_name,
                git_commit=args.git_commit,
            )
        except Exception as exc:  # DirtyRepositoryError, ValueError, etc.
            # Reframe ONLY the git-run-gate shapes into the one-door message.
            # Unrelated exceptions still surface raw (no blanket-catch).
            from .version import DirtyRepositoryError
            msg = str(exc)
            if isinstance(exc, DirtyRepositoryError) or (
                isinstance(exc, RuntimeError) and "git rev-parse HEAD failed" in msg
            ):
                print(_not_runnable_message("git", msg), file=sys.stderr)
                return 1
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"{flag}:{run_id}")
        return 0

    elif args.command == "complete_run":
        metrics = json.loads(args.metrics) if args.metrics else None
        from .execution.record import complete_run
        complete_run(
            run_id=args.run_id,
            status=args.status,
            output_files=args.output,
            metrics=metrics,
            error_message=args.error,
            error_traceback=args.traceback_str,
        )
        return 0

    elif args.command == "check_cache":
        params = json.loads(args.params) if args.params else None
        from .execution.claim import candidate_cache_key, lookup_cache_hit
        try:
            cache_key = candidate_cache_key(
                method_name=args.method,
                module_name=args.module,
                sample=args.sample,
                params=params,
                parent_run_ids=args.parent_run_id,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        run_id = lookup_cache_hit(
            args.method, args.module, args.sample, cache_key
        )
        print(run_id if run_id is not None else "NONE")
        return 0

    elif args.command == "finalize_pipeline":
        from .execution.lifecycle import finalize_pipeline
        finalize_pipeline(pipeline_id=args.pipeline_id)
        return 0

    elif args.command == "fail_pipeline":
        from .execution.lifecycle import fail_pipeline
        fail_pipeline(pipeline_id=args.pipeline_id)
        return 0

    elif args.command == "resolve_input":
        from .storage import InputUnavailableError, ResolveOutputError, resolve_input
        try:
            path = resolve_input(run_id=args.run_id)
        except InputUnavailableError:
            # The resolver's FAIL on stderr already names the path and the re-run.
            return 1
        except ResolveOutputError as exc:
            # Any other refusal (a malformed cache entry, say) carries Storage's
            # own message, naming the repair.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if path is None:
            print("ERROR: no output found for run", file=sys.stderr)
            return 1
        print(path)
        return 0

    elif args.command == "lookup_run":
        from .execution.claim import lookup_run
        run_id = lookup_run(args.method, args.module, args.sample,
                            nf_process_name=args.nf_process_name)
        if run_id is None:
            print("ERROR: no completed run found", file=sys.stderr)
            return 1
        print(run_id)
        return 0

    elif args.command == "init":
        from .init import InitConfirmationRequired, init_project
        target = Path(args.dir).resolve()
        try:
            created = init_project(
                target,
                init_git=args.git,
                archive=args.archive,
                assume_yes=args.assume_yes,
            )
        except ValueError as exc:
            # A rejected --archive value (file:// / unknown scheme /
            # missing DVC plugin, or one an existing config contradicts)
            # — refuse before any scaffolding.
            print(f"wfc init: {exc}", file=sys.stderr)
            return 1
        except InitConfirmationRequired as exc:
            # An existing project needs changes and nothing can confirm
            # them: nothing was changed.
            print(str(exc), file=sys.stderr)
            return 1
        if not created:
            # The user declined the listed changes; nothing was changed.
            return 0
        print(f"Initialized wfc project at {target}")
        for name, was_created in created.items():
            icon = "+" if was_created else "."
            print(f"  {icon} {name}")
        # init exits 0 whenever scaffolding succeeded, regardless of
        # git/Docker readiness. `wfc doctor` is the run-readiness gate.
        return 0

    elif args.command == "doctor":
        from .execution.readiness import (
            default_project_dir,
            env_image_checks,
            run_all_checks,
        )
        from .preflight import render_health_table
        results = run_all_checks(default_project_dir())
        print(render_health_table(results))
        # The env-image rows (they only warn) under their own heading.
        images = env_image_checks(default_project_dir())
        # A demo an older wfc made: one warn row, from the demo itself.
        from .demo.scaffold import demo_schema_check
        demo_row = demo_schema_check(default_project_dir())
        if demo_row is not None:
            images.append(demo_row)
        print(render_health_table(images, heading="Env images:"))
        results += images
        # Non-zero on any FAIL (WARN does not flip the gate).
        any_fail = any(r.status == "fail" for r in results)
        return 1 if any_fail else 0

    elif args.command == "seed":
        from .seed import seed
        return seed()

    elif args.command == "demo":
        from .demo.scaffold import DemoError, run_demo

        try:
            if args.remove:
                from .demo.remove import remove_demo

                return remove_demo(
                    target_dir=args.demo_dir,
                    purge_image=args.purge_image,
                    assume_yes=args.assume_yes,
                )
            return run_demo(
                target_dir=args.demo_dir,
                port=args.port,
                no_open=args.no_open,
                force=args.force,
            )
        except DemoError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    elif args.command == "register-sample":
        from .registration import register_sample
        source = Path(args.source).resolve()
        try:
            registered = register_sample(
                name=args.name,
                source_path=source,
                manifest_path=(Path(args.manifest).resolve()
                               if args.manifest else None),
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Registered sample '{args.name}' -> {registered}")
        return 0

    elif args.command == "restore-sample":
        from .storage import restore_sample
        restore_sample(
            name=args.name,
            content_hash=args.content_hash,
        )
        return 0

    elif args.command == "register-module":
        from .registration import register_module
        contracts = None
        if args.contracts is not None:
            # Accept a file path or inline JSON string
            contracts_path = Path(args.contracts)
            if contracts_path.exists():
                contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
            else:
                contracts = json.loads(args.contracts)
        module_dir = Path(args.module_dir) if args.module_dir else None
        # Auto-discover module_dir from modules/{name} if not provided
        if module_dir is None:
            candidate = Path(layout.MODULES_DIR_NAME) / args.name
            if candidate.is_dir():
                module_dir = candidate
        try:
            register_module(
                name=args.name,
                contracts=contracts,
                description=args.description,
                module_dir=module_dir,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0

    elif args.command == "register-method":
        from .confirm import ConfirmationRequired, confirm
        from .registration import (
            apply_method_registration,
            plan_method_registration,
        )
        try:
            # Registration states what it will do before writing anything;
            # a new method or a new version is applied only once confirmed.
            plan = plan_method_registration(
                method_dir=Path(args.method_dir),
                module_name=args.module,
                method_name=args.name,
                script_name=args.script,
            )
            print(plan.message)
            if plan.adds_version and not confirm(
                    "Proceed?", assume_yes=args.assume_yes,
                    refusal=ConfirmationRequired(
                        "wfc register-method: registration needs confirmation "
                        "and there is no terminal to ask on. Nothing was "
                        "registered. Re-run with --yes to register as stated "
                        "above.")):
                print("Nothing was registered.")
                return 1
            apply_method_registration(plan)
        except ConfirmationRequired as exc:
            print(str(exc), file=sys.stderr)
            return 1
        # FileNotFoundError covers script discovery misses (probe found no
        # candidate; explicit script:/--script names a missing file) — a
        # user input problem, not a crash.
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0

    elif args.command == "run-pipeline":
        # Docker-down pre-gate, as run-step has: without it the env
        # pre-flight's image probe is the first thing to meet a stopped
        # daemon.
        docker_down = _docker_gate()
        if docker_down:
            print(docker_down, file=sys.stderr)
            return 1
        from .execution import run_pipeline
        from .execution.validation import PipelineRefusedError
        from .registration import MalformedSampleError, UnreachableSampleError
        try:
            run_pipeline(
                pipeline_path=args.pipeline,
                project_root=args.project_root,
                wfc_root=args.wfc_root,
                cores=args.cores,
                snakefile_path=args.snakefile,
                archive=args.archive,
                keep_going=args.keep_going,
            )
        except PipelineRefusedError as exc:
            # The validator's verdict, the one the canvas gives the same
            # document: one refusal per line, nothing was started.
            print("ERROR: the pipeline document is refused:", file=sys.stderr)
            for refusal in exc.refusals:
                print(f"  {refusal}", file=sys.stderr)
            return 1
        except (UnreachableSampleError, MalformedSampleError) as exc:
            # The whole point of the pipeline-start preflight is that the
            # user reads one message instead of N job logs. A traceback
            # would put the message at the bottom of a stack, so this is
            # the one refusal shape the verb prints itself.
            #
            # MalformedSampleError comes from the load one step EARLIER
            # than the preflight (the composer's sample-hash read), so a
            # hashless row never reaches the preflight at all. Both are
            # "the pipeline was not started, here is the repair", so both
            # get the same delivery.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            # run_pipeline raises RuntimeError for a failed pipeline -- most
            # often an engine that exited non-zero, after the pipeline-end
            # walk recorded what failed and the summary was printed -- and a
            # RuntimeError from anywhere beneath it is a failed run too. The
            # message carries the cause, so the user reads it as one line
            # rather than at the bottom of a traceback.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0

    elif args.command == "run-step":
        # Docker-down pre-gate: execution is container-only, so a stopped
        # daemon would otherwise surface as a raw `docker run` subprocess
        # error. Reframe that specific shape into the one-door message.
        from .execution.readiness import check_docker
        _dock = check_docker()
        if _dock.status == "fail":
            print(_not_runnable_message("docker", _dock.message, _dock.fix_hint),
                  file=sys.stderr)
            return 1
        params = json.loads(args.params) if args.params else None
        from .execution import run_step
        rc = run_step(
            node_id=args.node_id,
            sample=args.sample,
            variant=args.variant,
            method_name=args.method,
            module_name=args.module,
            params=params,
            parent_run_ids=args.parent_run_id,
            pipeline_id=args.pipeline_id,
            pipeline_json=args.pipeline_json,
            git_commit=args.git_commit,
            ref_inputs=args.ref_input,
            collapsed_samples=args.collapsed_sample,
        )
        return rc

    elif args.command == "pipeline-summary":
        from .execution.lifecycle import pipeline_summary
        return pipeline_summary(pipeline_id=args.pipeline_id)

    elif args.command == "list-envs":
        # Registration reads the versions recording each env, so the
        # REGISTRY column counts older images as the canvas chip does.
        from .environments import verbs
        from .registration import version_fingerprints_recording_env
        return verbs.list_envs(versions_of=version_fingerprints_recording_env)

    elif args.command == "show-env":
        from .environments import verbs
        from .registration import version_fingerprints_recording_env
        return verbs.show_env(args.name,
                              versions_of=version_fingerprints_recording_env)

    elif args.command == "delete-env":
        # Registration reads every version that records this env from the
        # committed version lists; Environments refuses while any does. An
        # unreadable list is passed on as the error, and the verb refuses.
        from .environments import verbs
        from .registration import versions_recording_env
        references: list[str] = []
        read_error: str | None = None
        try:
            references = versions_recording_env(args.name)
        except ValueError as exc:
            read_error = str(exc)
        except RuntimeError:
            pass  # no project: the verb reports it
        return verbs.delete_env(args.name, references=references,
                                read_error=read_error, force=args.force)

    elif args.command == "register-env":
        return _cli_register_env(args)

    elif args.command == "push-env":
        return _cli_push_env(args)

    elif args.command == "share":
        return _cli_share(args)

    elif args.command == "jupyter":
        from .environments import dev_loop
        return dev_loop.jupyter(args.env, port=args.port)

    elif args.command == "shell":
        from .environments import dev_loop
        return dev_loop.shell(args.env)

    elif args.command == "exec":
        from .environments import dev_loop
        return dev_loop.exec_(args.env, args.cmd or [])

    elif args.command == "cache":
        if args.cache_command == "prune":
            from .storage import cache_prune
            env_hashes: set[str] = set()
            if args.include_local:
                # The env manifest names env blobs no run may name yet;
                # Storage never reads it, so its hashes are handed in.
                from .environments import load_manifest
                try:
                    manifest = load_manifest(get_project_root())
                except ValueError as exc:
                    print(f"ERROR: cannot read the env manifest, so its env "
                          f"blobs cannot be kept: {exc}", file=sys.stderr)
                    return 1
                for rec in manifest.get("envs", {}).values():
                    for key in ("env_fingerprint", "source_fingerprint"):
                        if rec.get(key):
                            env_hashes.add(rec[key])
            return cache_prune(
                prune_all=args.prune_all,
                include_local=args.include_local,
                dry_run=args.dry_run,
                force=args.force,
                env_hashes=env_hashes,
            )
        elif args.cache_command == "push":
            from .storage import NoRemoteConfiguredError, cache_push

            def _announce(queued):
                print(f"Pushing {queued['outputs']} output(s), "
                      f"{queued['samples']} sample(s) and {queued['blobs']} "
                      f"blob(s) to the remote.")
            try:
                report = cache_push(get_project_root(), announce=_announce)
            except NoRemoteConfiguredError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            print(f"Pushed {report.pushed} of {sum(report.queued.values())} "
                  f"entries; {len(report.failures)} failed.")
            for f in report.failures:
                print(f"  {f.content_hash}  {f.kind}  {f.reason.splitlines()[0]}")
            return 1 if report.failures else 0
        elif args.cache_command == "archive":
            from .storage import cache_archive
            return cache_archive(run_id=args.run_id)
        return 1

    elif args.command == "verify":
        from .storage import verify_project
        report = verify_project(get_project_root(), repair=args.repair)
        if args.as_json:
            print(json.dumps(report.as_dict(), indent=2))
        else:
            print("\n".join(report.lines()))
        return 0 if report.ok else 1

    elif args.command == "runs":
        return _cli_runs(args)

    elif args.command == "cancel":
        return _cli_cancel(args)

    elif args.command == "export-run":
        return _cli_export_run(args)

    elif args.command == "export":
        slot = args.slot
        dest = args.dest
        if args.export_all and slot is not None:
            parser.error("--all exports every output; do not pass --slot")
        if args.path_only and dest is not None:
            parser.error("--path cannot be combined with --dest")
        if not args.path_only and slot is not None and dest is None:
            parser.error(
                "--dest required (or use --path to print the cache path)"
            )
        if args.export_all and not args.path_only and dest is None:
            parser.error("--all requires --dest DIR (or --path)")
        if (slot is None and not args.export_all
                and (dest is not None or args.path_only or args.force)):
            parser.error(
                "--slot or --all required with --dest, --path or --force "
                "(with --run alone the run's outputs are listed)"
            )
        from .export import export_output
        return export_output(
            run_id=args.run_id,
            slot=slot,
            dest=dest,
            export_all=args.export_all,
            path_only=args.path_only,
            force=args.force,
        )

    elif args.command == "canvas":
        try:
            import uvicorn
        except ImportError:
            print(
                "ERROR: uvicorn is required to run the canvas server.\n"
                "Install it with: pip install 'uvicorn[standard]'",
                file=sys.stderr,
            )
            return 1
        # Resolve the project before binding: --project-root becomes the
        # canonical override, and the canonical resolver validates it (or
        # walks up from the cwd). No project means no server — exit with the
        # marker-naming error rather than serve the cwd.
        if args.project_root:
            os.environ[layout.ROOT_ENV_VAR] = str(Path(args.project_root).resolve())
        try:
            proj = get_project_root()
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        os.environ[layout.ROOT_ENV_VAR] = str(proj)
        if args.project_root:
            db = layout.db_path(proj)
            if not db.exists():
                print(f"ERROR: No {layout.db_relpath()} found in {proj}", file=sys.stderr)
                return 1
        print(f"Starting Workflow Canvas at http://{args.host}:{args.port}")
        uvicorn.run(
            "wfc.canvas.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    return 1
