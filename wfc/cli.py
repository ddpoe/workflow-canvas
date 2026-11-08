"""wfc CLI: the command-line verbs.

``wfc --help`` lists the user's verbs. Three hidden verbs keep their argv
for a generated Snakefile: its rules delegate to ``run-step``, a restore
rule calls ``restore-sample`` and its ``onerror`` handler calls
``fail_pipeline``. ``pipeline-summary`` and the retired ``seed`` are hidden
too. Every verb body parses, calls one owner function, renders and exits.
"""

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Callable
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


def _asker(*, assume_yes: bool, refusal: str, declined: str):
    """The question a unit's verb body asks, put through :mod:`wfc.confirm`.

    ``delete-env`` and ``cache prune`` run in units that never import the
    CLI, so the question arrives as this callable. It answers True for
    ``--yes`` or a yes at the terminal. On a no it prints *declined*; with
    no terminal and no ``--yes`` it prints *refusal* (which names ``--yes``)
    on stderr. Both answer False, and the verb changes nothing.

    Args:
        assume_yes: ``--yes``: answer yes without asking.
        refusal: The no-terminal message: what was not done, and to re-run
            with ``--yes``.
        declined: What to print when the answer is no.

    Returns:
        A callable taking the question (without ``[y/N]``) and returning
        whether to go ahead.
    """
    from .confirm import ConfirmationRequired, confirm

    def ask(question: str) -> bool:
        try:
            if confirm(question, assume_yes=assume_yes,
                       refusal=ConfirmationRequired(refusal)):
                return True
        except ConfirmationRequired as exc:
            print(str(exc), file=sys.stderr)
            return False
        print(declined)
        return False

    return ask


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


def _leftover_inputs(project_dir):
    """What the leftovers search needs that Environments may not read itself.

    Execution's liveness (which also holds the project's pipeline ids) and
    the images every method version records, read from Registration's
    committed version lists as ``wfc push-env --all`` reads them.

    Args:
        project_dir: The project root.

    Returns:
        ``(liveness, recorded, unread)``: ``recorded`` is ``None`` and
        ``unread`` says why when a version list cannot be read.
    """
    from .environments import pushable_envs
    from .execution import run_liveness
    from .registration import version_fingerprints_recording_env

    liveness = run_liveness(project_dir)
    try:
        recorded = {name: version_fingerprints_recording_env(name, project_dir)
                    for name in pushable_envs(project_dir)}
    except ValueError as exc:
        return liveness, None, str(exc)
    return liveness, recorded, None


def _leftover_counts(project_dir):
    """``wfc list-envs``' footer counts: what there is to stop, over Execution's liveness.

    Stop-only: no method version list is read and Docker is asked for no
    image listing and no disk usage.
    """
    from .environments import leftover_counts
    from .execution import run_liveness

    liveness = run_liveness(project_dir)
    return leftover_counts(project_dir, run_running=liveness,
                           pipeline_ids=liveness.pipeline_ids, stop_only=True)


def _cli_prune_envs(args) -> int:
    """``wfc prune-envs``: print the plan, confirm, stop what it lists.

    The plan and the apply are Environments' (:func:`wfc.environments.plan_prune`,
    :func:`wfc.environments.apply_prune`); Execution's liveness, the project's
    pipeline ids, its crashed pipelines, the runs a gone ``wfc try`` left,
    the liveness's second look (the heartbeat guard) and the images method
    versions record are passed in as values. Nothing to stop prints the plan
    and asks nothing; ``--dry-run``
    prints it and stops. Otherwise ``Proceed?`` goes through
    :mod:`wfc.confirm`: no terminal and no ``--yes`` refuses naming
    ``--yes``, and a no (or Ctrl-C at the question) stops nothing. Ctrl-C
    during the apply ends with one line: the prune was interrupted, what is
    still there to stop (counted again, stop-only, with
    :func:`wfc.environments.leftover_counts`; a second Ctrl-C leaves the
    count out) and ``wfc prune-envs`` to finish.

    Args:
        args: The parsed ``prune-envs`` arguments (``dry_run``, ``assume_yes``).

    Returns:
        0 when nothing was to be stopped or everything listed was stopped; 1
        outside a project, on a declined or unconfirmable plan, or when
        something could not be stopped; 130 when Ctrl-C interrupted the apply.
    """
    from dataclasses import replace

    from .confirm import ConfirmationRequired, confirm
    from .environments import apply_prune, leftover_counts, plan_prune

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    liveness, recorded, unread = _leftover_inputs(project_dir)
    # The liveness the apply re-checks with: the second look once it is
    # taken, so a Ctrl-C recount counts as the apply did.
    used = [liveness]

    def second_look():
        looked = liveness.second_look()
        used[0] = looked
        return looked

    plan = plan_prune(project_dir, run_running=liveness,
                      pipeline_ids=liveness.pipeline_ids, recorded=recorded,
                      crashed_pipelines=liveness.crashed_pipelines,
                      dead_tries=liveness.dead_draft_steps,
                      second_look=second_look)
    if unread is not None:
        plan = replace(plan, unused_images=None)
    print("\n".join(plan.lines()))
    if liveness.error is not None:
        print(f"This project's runs could not be read ({liveness.error}), so every "
              f"run counts as still running and no run container is listed.")
    if unread is not None:
        print(f"Env images were not counted: a method version list could not be "
              f"read ({unread}).")
    if args.dry_run:
        print("Dry run: nothing was stopped.")
        return 0
    if not plan.to_clear:
        return 0
    try:
        if not confirm("Proceed?", assume_yes=args.assume_yes,
                       refusal=ConfirmationRequired(
                           "wfc prune-envs: stopping needs confirmation and there "
                           "is no terminal to ask on. Nothing was stopped. "
                           "Re-run with --yes to stop what is listed above.")):
            print("Nothing was stopped.")
            return 1
    except ConfirmationRequired as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nNothing was stopped.")
        return 1
    try:
        result = apply_prune(plan)
    except KeyboardInterrupt:
        # Ctrl-C part way through the apply: count again what is still there,
        # with the plan's own inputs. A second Ctrl-C (or a count that fails)
        # ends without a count.
        try:
            left = leftover_counts(project_dir, run_running=used[0],
                                   pipeline_ids=liveness.pipeline_ids, stop_only=True)
        except (KeyboardInterrupt, Exception):  # noqa: BLE001 - the line still prints
            left = None
        if left is None:
            still = "what is left was not counted"
        elif left.containers is None:
            parts = left.phrases()
            still = ("Docker could not be reached, so its containers were not counted"
                     + ("; still there: " + ", ".join(parts) if parts else ""))
        else:
            parts = left.phrases()
            still = ("still there: " + ", ".join(parts)) if parts else "nothing is left to stop"
        print(f"Stopped: the prune was interrupted; {still}. Run `wfc prune-envs` "
              f"to finish.", file=sys.stderr)
        return 130  # the shell's exit status for Ctrl-C
    print("\n".join(result.lines()))
    return result.exit_code


#: The guide page that matches GitHub's package pages to what wfc records.
_GITHUB_IMAGES_GUIDE_PAGE = "how-to/see-your-images-on-github.html"


def github_images_guide_url(version: str | None) -> str:
    """The guide page for GitHub's package pages, in the docs of *version*'s channel.

    A pre-release (or a version that cannot be read) reads the latest docs,
    a release the stable ones, so the page matches the wfc that prints it.

    Args:
        version: The installed wfc version, or ``None``.

    Returns:
        The page's URL.
    """
    from packaging.version import InvalidVersion, Version

    try:
        release = version is not None and not Version(version).is_prerelease
    except InvalidVersion:
        release = False
    channel = "stable" if release else "latest"
    return (f"https://workflow-canvas.readthedocs.io/en/{channel}/"
            f"{_GITHUB_IMAGES_GUIDE_PAGE}")


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
    :func:`wfc.preflight.render_health_table` under their own heading, then
    what sharing does not carry (runs, samples and outputs stay in the
    local ``wfc.db``) and the command that hands runs over (``wfc
    export-run``). When the images go to GHCR, a last line names the guide
    page that matches GitHub's package pages to what wfc records, in the
    docs of the installed version's channel. ``--json`` prints the same as
    one object. Nothing is written, committed or pushed.

    Args:
        args: The parsed ``share`` arguments (``as_json``).

    Returns:
        0 whenever there is a project (the rows only warn); 1 outside one.
    """
    from . import cli_render as render
    from .execution import share_checks
    from .preflight import render_health_table
    from .version import installed_wfc_version

    try:
        project_dir = get_project_root()
    except RuntimeError as exc:
        return render.fail(f"No wfc project found: {exc}", as_json=args.as_json)
    rows = share_checks(project_dir)
    guide = (github_images_guide_url(installed_wfc_version())
             if _shares_to_ghcr(project_dir) else None)
    if args.as_json:
        render.print_json({"ok": True,
                           "checks": [render.check_item(row) for row in rows],
                           "notes": list(render.SHARE_NOTES),
                           "images_guide_url": guide})
        return 0
    print(render_health_table(rows, heading="Sharing this project:"))
    print("\n" + "\n".join(render.SHARE_NOTES))
    if guide:
        print(f"\nSee your images on GitHub: {guide}")
    return 0


#: ``wfc runs``' column headings, in print order.
RUNS_COLUMNS = ("RUN", "PIPELINE", "NODE", "METHOD", "SAMPLE", "STATUS")


def _run_argument(text: str) -> int | str:
    """``--run``'s value: a run id as an integer, any other text as written.

    A draft string passes through as text, for the verb to refuse or resolve.
    """
    try:
        return int(text)
    except ValueError:
        return text.strip()


def _cli_runs(args) -> int:
    """``wfc runs``: print each run's id, pipeline, node, method, sample and status.

    The rows are Persistence's (:func:`wfc.persistence.list_runs`), oldest
    first, each pipeline named by its name, else its id (``--pipeline``
    takes the id), then one row per draft step that exists, its draft string in
    RUN (Execution's :func:`wfc.execution.draft.list_draft_steps`), as a table padded to its widest cell; an
    empty column value prints as ``-``. ``--json`` prints the same rows as
    items. Nothing is written, so nothing is confirmed.

    Args:
        args: The parsed ``runs`` arguments (``pipeline_id``, ``method``,
            ``as_json``).

    Returns:
        0 whenever there is a project, with or without runs; 1 outside one,
        when ``--method`` is not module-qualified, or when the draft store
        cannot be read.
    """
    from . import cli_render as render
    from .execution.draft import list_draft_steps
    from .persistence import list_runs

    try:
        get_project_root()
    except RuntimeError as exc:
        return render.fail(f"No wfc project found: {exc}", as_json=args.as_json)
    try:
        listings = list_runs(args.pipeline_id, args.method)
    except ValueError as exc:
        return render.fail(f"--method: {exc}", as_json=args.as_json)
    try:
        draft_steps = list_draft_steps(args.pipeline_id, args.method)
    except (OSError, ValueError) as exc:
        return render.fail(f"Cannot read the draft store: {exc}",
                           as_json=args.as_json)
    items = [{"run": r.run_id, "draft": False, "pipeline_id": r.pipeline_id,
              "pipeline_name": r.pipeline_name, "node_id": r.node_id,
              "method": r.method, "sample": r.sample, "status": r.status}
             for r in listings]
    items += [{"run": d.run, "draft": True, "pipeline_id": d.pipeline_id,
               "pipeline_name": d.pipeline_name, "node_id": d.node_id,
               "method": d.method, "sample": d.sample, "status": d.status}
              for d in draft_steps]
    if args.as_json:
        render.print_items(items)
        return 0
    if not items:
        scope = []
        if args.pipeline_id:
            scope.append(f"in pipeline {args.pipeline_id}")
        if args.method:
            scope.append(f"of method {args.method}")
        print(f"No runs {' and '.join(scope)}." if scope else "No runs.")
        return 0
    render.print_table(RUNS_COLUMNS, (
        (i["run"], i["pipeline_name"] or i["pipeline_id"], i["node_id"],
         i["method"], i["sample"], i["status"]) for i in items))
    return 0


def _cli_list(args) -> int:
    """``wfc list-methods`` / ``list-modules`` / ``list-samples``: what the Registry lists.

    Each reads Registration's listing function (the one the canvas's
    registry routes read) and prints a table, or ``--json`` items.

    Args:
        args: The parsed arguments (``command``, ``as_json``).

    Returns:
        0 whenever there is a project; 1 outside one or when the colour
        store is malformed.
    """
    from . import cli_render as render
    from .registration import list_methods, list_modules, list_samples

    kind = args.command.removeprefix("list-")
    listers = {"methods": (list_methods, render.METHOD_COLUMNS, render.method_row),
               "modules": (list_modules, render.MODULE_COLUMNS, render.module_row),
               "samples": (list_samples, render.SAMPLE_COLUMNS, render.sample_row)}
    lister, columns, row = listers[kind]
    try:
        listed = lister(get_project_root())
    except RuntimeError as exc:
        return render.fail(f"No wfc project found: {exc}", as_json=args.as_json)
    except ValueError as exc:
        return render.fail(str(exc), as_json=args.as_json)
    if args.as_json:
        render.print_items(listed)
    elif not listed:
        print(f"No {kind} registered.")
    else:
        render.print_table(columns, (row(entry) for entry in listed))
    return 0


def _cli_show_run(args) -> int:
    """``wfc show-run``: one run's status, metrics, outputs, error and stderr tail.

    Execution's :func:`wfc.execution.run_detail` reads a recorded run or a
    draft step; ``--log stdout|stderr`` prints the whole log Execution's
    :func:`wfc.execution.run_log` reads instead, and ``--json`` the detail as
    one object.

    Args:
        args: The parsed arguments (``run``, ``log``, ``as_json``).

    Returns:
        0 when the run was read; 1 when it names no run, there is no
        project, or the asked-for log does not exist.
    """
    from . import cli_render as render
    from .execution import RunDetailError, run_detail, run_log

    if args.log:
        try:
            text = run_log(args.run, args.log)
        except RuntimeError as exc:
            return render.fail(f"No wfc project found: {exc}", as_json=False)
        except RunDetailError as exc:
            return render.fail(str(exc), as_json=False)
        sys.stdout.write(text)
        return 0
    try:
        detail = run_detail(args.run)
    except RuntimeError as exc:
        return render.fail(f"No wfc project found: {exc}", as_json=args.as_json)
    except RunDetailError as exc:
        return render.fail(str(exc), as_json=args.as_json)
    if args.as_json:
        render.print_json({"ok": True, **detail.as_dict()})
    else:
        print("\n".join(render.show_run_lines(detail)))
    return 0


def _bind_project_root(project_root: str | None) -> None:
    """Make ``--project-root`` the project this process works in, before any database read.

    The database, the contract map and every child process (the engine's
    steps) follow the process's project root, so a command given
    ``--project-root`` names it as ``WFC_PROJECT_ROOT`` and drops the
    process's cached root and engine; the next read resolves (and checks)
    the named project. Without ``--project-root`` nothing changes: the
    project is the one ``WFC_PROJECT_ROOT`` names or the one containing the
    current directory.

    Args:
        project_root: The ``--project-root`` argument, or None.
    """
    from .persistence import reset_engine

    if project_root:
        os.environ[layout.ROOT_ENV_VAR] = str(Path(project_root).resolve())
        reset_engine()


def _cli_check_pipeline(args) -> int:
    """``wfc check-pipeline``: the verdict on a pipeline file, without running it.

    Execution's :func:`wfc.execution.validation.check_pipeline` reads the
    file as ``run-pipeline`` reads it and gives canvas Validate's verdict
    plus the load's; no Docker, nothing written. Each warning is a
    ``WARNING:`` line and each error (draft refusals among them) an
    ``ERROR:`` line on stderr; stdout says whether it passes, or under
    ``--json`` is the verdict with ``ok``.

    Args:
        args: The parsed arguments (``pipeline``, ``project_root``, ``as_json``).

    The checks run in order: the file first (a missing or unreadable file
    is refused wherever wfc runs), then the project (the file may live
    anywhere; ``--project-root`` names the project as ``wfc canvas``'s
    does, otherwise it is the one containing the current directory), then
    the check itself, whose errors print in their own words.

    Returns:
        0 when the document passes (warnings or not); 1 on an error, a file
        that cannot be read, an unreachable database, or outside a project.
    """
    from . import cli_render as render
    from .execution import PipelineDocumentError
    from .execution.prepare import read_pipeline_document
    from .execution.validation import check_pipeline
    from .persistence import project_root as get_project_root

    try:
        read_pipeline_document(args.pipeline)
    except PipelineDocumentError as exc:
        return render.fail(str(exc), as_json=args.as_json)
    _bind_project_root(args.project_root)
    try:
        root = get_project_root()
    except RuntimeError as exc:
        return render.fail(
            f"No wfc project found: {exc}\nThe pipeline file can be anywhere; "
            f"wfc checks it against a project. Run this command from inside the "
            f"project's folder, or pass --project-root <project folder>.",
            as_json=args.as_json)
    try:
        verdict = check_pipeline(args.pipeline, root)
    except PipelineDocumentError as exc:
        return render.fail(str(exc), as_json=args.as_json)
    except RuntimeError as exc:
        # DatabaseUnreachableError among them: its own words, no prefix.
        return render.fail(str(exc), as_json=args.as_json)
    for line in render.verdict_lines(verdict):
        print(line, file=sys.stderr)
    if args.as_json:
        render.print_json({"ok": verdict["valid"], **verdict})
    elif verdict["valid"]:
        print(f"{args.pipeline} passes the check; `wfc run-pipeline --pipeline "
              f"{args.pipeline}` runs it.")
    else:
        print(f"{args.pipeline} is refused: {len(verdict['errors'])} error(s).")
    return 0 if verdict["valid"] else 1


def _saved_line(saved) -> str:
    """The line ``run-method`` prints before it runs."""
    if saved.reused:
        return f"Running {saved.path} (already saved with this document)."
    return f"Saved the pipeline to {saved.path}; running it."


def _params_arg(parser: argparse.ArgumentParser, text: str | None) -> dict | None:
    """Parse ``run-method --params``: a JSON object, or a usage error naming the flag.

    Args:
        parser: The parser, for the usage error.
        text: The flag's value, or ``None`` when it was not given.

    Returns:
        The params, or ``None`` when the flag was not given.
    """
    if text is None:
        return None

    def not_json(constant: str):
        raise ValueError(f"{constant} is not a JSON value")

    try:
        params = json.loads(text, parse_constant=not_json)
    except ValueError as exc:
        parser.error(f"--params is not JSON ({exc}); give a JSON object of "
                     f"parameter values, e.g. --params '{{\"n\": 3}}'")
    if not isinstance(params, dict):
        parser.error("--params must be a JSON object of parameter values, e.g. "
                     "--params '{\"n\": 3}'")
    return params


def _cli_run_method(parser: argparse.ArgumentParser, args) -> int:
    """``wfc run-method``: run one registered method on one sample.

    Runs :func:`wfc.execution.run_one_method` with the params and the name
    given, which checks and saves the one-step document (its path printed
    first) and runs it as any pipeline. Refusals and a failed run print as
    ``run-pipeline``'s do (:func:`_launch`).

    Args:
        parser: The parser, for ``--params``'s usage error.
        args: The parsed ``run-method`` arguments.

    Returns:
        0 when the method ran; 1 when Docker is down, the run is refused, or
        it failed.
    """
    params = _params_arg(parser, args.params)

    def launch() -> None:
        from .execution import run_one_method
        run_one_method(
            args.method, args.sample, params=params, name=args.name, save=args.save,
            root=Path(args.project_root).resolve() if args.project_root else None,
            cores=args.cores, snakefile_path=args.snakefile,
            archive=args.archive, keep_going=args.keep_going,
            on_saved=lambda saved: print(_saved_line(saved), flush=True))

    return _launch(args, launch)


def _cli_run_pipeline(args) -> int:
    """``wfc run-pipeline``: run a pipeline file through :func:`wfc.execution.run_pipeline`.

    Refusals and a failed run print as :func:`_launch` says.

    Args:
        args: The parsed ``run-pipeline`` arguments.

    Returns:
        0 when the pipeline ran; 1 when Docker is down, the document is
        refused, or the run failed.
    """
    def launch() -> None:
        from .execution import run_pipeline
        run_pipeline(
            pipeline_path=args.pipeline,
            project_root=args.project_root,
            cores=args.cores,
            snakefile_path=args.snakefile,
            archive=args.archive,
            keep_going=args.keep_going,
        )

    return _launch(args, launch)


def _launch(args, launch: Callable[[], None]) -> int:
    """Bind the project, gate on Docker, run *launch*, and print how it ended.

    The two run verbs share it. A refused document (``PipelineDocumentError``)
    prints through ``document_refusal_lines``; a sample the preflight cannot
    reach and a failed run are one ``ERROR:`` line each.

    Args:
        args: The parsed arguments (``project_root`` read).
        launch: Runs the pipeline.

    Returns:
        0 when it ran; 1 when Docker is down, it was refused, or it failed.
    """
    from . import cli_render as render

    # One project per process: --project-root is the process's project
    # before anything reads a database or the engine's steps inherit it.
    _bind_project_root(args.project_root)
    # Docker-down pre-gate, as run-step has: without it the env pre-flight's
    # image probe is the first thing to meet a stopped daemon.
    docker_down = _docker_gate()
    if docker_down:
        print(docker_down, file=sys.stderr)
        return 1
    from .execution import PipelineDocumentError
    from .registration import MalformedSampleError, UnreachableSampleError
    try:
        launch()
    except PipelineDocumentError as exc:
        # Every refusal of the document (the file, its variables, the
        # validator, the load, the one-method run's own): nothing started.
        for line in render.document_refusal_lines(exc):
            print(line, file=sys.stderr)
        return 1
    except (UnreachableSampleError, MalformedSampleError) as exc:
        # The pipeline-start preflight's point is one message instead of N
        # job logs; MalformedSampleError comes from the composer's
        # sample-hash read one step earlier. Both: not started, here is the
        # repair.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        # A failed pipeline (most often an engine that exited non-zero,
        # after the pipeline-end walk recorded what failed and the summary
        # printed), or a RuntimeError from beneath it: one line, not a
        # traceback.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _cli_doctor(args) -> int:
    """``wfc doctor``: the run-readiness checks, then the env-image rows.

    Args:
        args: The parsed arguments (``as_json``).

    Returns:
        1 when any check fails (a warning does not), else 0.
    """
    from . import cli_render as render
    from .demo.scaffold import demo_schema_check
    from .execution.readiness import default_project_dir, env_image_checks, run_all_checks
    from .preflight import render_health_table

    results = run_all_checks(default_project_dir())
    # The env-image rows under their own heading: the registry and versions
    # rows only warn; the bind row fails when a step's container would see
    # the project or the cache empty.
    images = env_image_checks(default_project_dir())
    # A demo an older wfc made: one warn row, from the demo itself.
    demo_row = demo_schema_check(default_project_dir())
    if demo_row is not None:
        images.append(demo_row)
    # Non-zero on any FAIL (WARN does not flip the gate).
    any_fail = any(r.status == "fail" for r in results + images)
    if args.as_json:
        render.print_json({"ok": not any_fail,
                           "checks": [render.check_item(r) for r in results],
                           "env_images": [render.check_item(r) for r in images]})
    else:
        print(render_health_table(results))
        print(render_health_table(images, heading="Env images:"))
    return 1 if any_fail else 0


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
    if outcome.ended_before:
        return [f"Pipeline {outcome.pipeline_id} ended before the cancel reached it: "
                f"nothing was left to cancel. `wfc runs --pipeline "
                f"{outcome.pipeline_id}` lists how each step ended."]
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

class _VersionAction(argparse.Action):
    """``--version``: print ``wfc <version>`` and exit 0.

    The version is read when the flag is given, not when the parser is
    built, so other commands never look it up (nor log the missing-metadata
    warning ``installed_wfc_version`` gives a run record).
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        from .version import installed_wfc_version

        print(f"wfc {installed_wfc_version() or 'unknown'}")
        parser.exit()


def _dev_loop_variables(env_name: str) -> dict[str, str]:
    """Build a dev-loop container's variables with Execution's one builder.

    ``PYTHONUNBUFFERED=1``, and the env's own ``bin`` first on ``PATH`` (from
    the env's current image, as Environments reads it), so ``python`` in the
    container is the interpreter a step of that env runs under. The dev loop
    is handed the values; Environments never imports Execution. When the
    project or the env cannot be read the ``bin`` is left out: the dev loop
    then refuses the env itself, naming why.

    Args:
        env_name: The env the dev-loop command launches.

    Returns:
        ``{name: value}`` for :func:`wfc.environments.dev_loop.shell`,
        ``exec_`` and ``jupyter``.
    """
    from .environments import CaptureError, version_bin_dir
    from .environments import get as get_env
    from .execution.container_vars import container_variables

    env_bin: str | None = None
    try:
        root = get_project_root()
        record = get_env(env_name, root)
        if record is not None:
            env_bin = version_bin_dir(env_name, record.env_fingerprint, root)
    except (RuntimeError, ValueError, CaptureError):
        env_bin = None
    return container_variables(env_bin=env_bin)


# =============================================================================
# Trying a method folder: wfc try, wfc shell|jupyter --method
# =============================================================================


class _TryRefused(Exception):
    """A try or a wired session is refused before anything runs; the message says why."""


def _method_input_flags(*, try_verb: bool = False) -> argparse.ArgumentParser:
    """The one parser of a tried method's inputs, shared by try, shell and jupyter.

    Args:
        try_verb: ``wfc try``'s flags: ``--module`` and ``--sample`` are
            required (the usage line shows them so). A session needs them
            only with ``--method``, so there they are optional.

    Returns:
        A parent parser: ``--module``, ``--sample``, ``--params`` and
        ``--parent-run-id`` as ``wfc run-step`` spells them, a draft string
        allowed as an entry's run part.
    """
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--module", required=try_verb, default=None,
                       help="The module the method belongs to (required"
                            + ("" if try_verb else " with --method")
                            + "); the folder becomes that module's draft")
    flags.add_argument("--sample", required=try_verb, default=None,
                       help=("The sample the step runs as (required); "
                             if try_verb else "The sample ")
                            + "read into the one input no parent entry feeds")
    flags.add_argument("--params", default=None, help="JSON string of parameters")
    flags.add_argument("--parent-run-id", action="append", default=None,
                       help="One parent entry per use: input:output:run or "
                            "input:run; the run part is a run id or a draft "
                            "string (draft:<module>.<method>/<node>@<sample>, "
                            "as `wfc runs` lists it)")
    return flags


class _MethodInputs:
    """A method folder with its inputs, as the shared flags gave them.

    Attributes:
        folder: The method folder, absolute.
        method: The method's name (the folder's name).
        module: The module.
        contract: ``method.yaml`` as Contracts parsed it.
        env: The env ``method.yaml`` names (registered).
        env_record: That env's registered record.
        params: The parameters.
        entries: The parent entries as typed.
        parsed: The parsed entries.
        sample: The sample, or ``None``.
    """

    def __init__(self, args, root: Path) -> None:
        from .contracts import parse_method_yaml
        from .environments import get as get_env
        from .execution.parents import parse_parent_entries

        self.folder = Path(args.method_dir).resolve()
        self.method = self.folder.name
        if not args.module:
            raise _TryRefused("--module is required: the folder becomes a draft "
                              "of a method in that module.")
        self.module = args.module
        try:
            contract = parse_method_yaml(self.folder)
        except (ValueError, OSError) as exc:
            raise _TryRefused(f"{self.folder}/method.yaml cannot be read: {exc}") from exc
        if contract is None:
            raise _TryRefused(f"{self.folder} has no method.yaml; pick the method "
                              f"folder that holds it.")
        self.contract = contract
        self.env = str(contract.get("env") or "")
        try:
            record = get_env(self.env, root) if self.env else None
        except ValueError as exc:
            raise _TryRefused(str(exc)) from exc
        if record is None:
            raise _TryRefused(
                f"{self.folder.name}'s method.yaml names env '{self.env}', which is "
                f"not registered. Register it first (`wfc register-env "
                f"{self.env or '<name>'} --help`); nothing was added or run.")
        self.env_record = record
        try:
            self.params = json.loads(args.params) if args.params else {}
        except ValueError as exc:
            raise _TryRefused(f"--params is not JSON: {exc}") from exc
        if not isinstance(self.params, dict):
            raise _TryRefused("--params must be a JSON object of parameter values.")
        self.entries = list(args.parent_run_id or [])
        try:
            self.parsed = parse_parent_entries(self.entries, step=self.method,
                                               allow_drafts=True)
        except ValueError as exc:
            raise _TryRefused(str(exc)) from exc
        self.sample = args.sample


def _use_draft(inputs: _MethodInputs, root: Path):
    """Use the method's draft, adding the folder as it when it has none; say which.

    Returns:
        The draft's pointer.

    Raises:
        _TryRefused: The module is not registered, or the method's draft is
            another folder's (named).
    """
    from .registration import DraftRefusedError, add_draft, plan_draft

    try:
        plan = plan_draft(inputs.module, inputs.folder, root)
    except DraftRefusedError as exc:
        raise _TryRefused(str(exc)) from exc
    qualified = f"{inputs.module}.{inputs.method}"
    if plan.existing is not None:
        print(f"Uses {qualified}'s draft, from {plan.folder}.")
        return plan.existing
    print(f"Adds {plan.folder} as the draft of {qualified}. {plan.message}")
    try:
        return add_draft(inputs.module, inputs.folder, root)
    except DraftRefusedError as exc:
        raise _TryRefused(str(exc)) from exc


def _cli_try(args) -> int:
    """``wfc try``: run a method folder as its module's draft, on a sample or runs' outputs.

    The folder is the method's draft (added as it when the method has none;
    refused when the draft is another folder's). Execution's try launch
    (``run_try``) runs it as a one-step pipeline named ``try-<method>``;
    this verb parses the flags, states first what it does (nothing is
    asked: a draft and its draft run are personal scratch, never recorded)
    and prints the outcome with an export command per output.

    The sample feeds the one input no parent entry feeds, as Contracts'
    one-step document decides (several candidates are refused naming them).
    Under ``--json`` the outcome is one object on stdout and the lines
    saying what it does go to stderr.

    Args:
        args: The parsed ``try`` arguments.

    Returns:
        0 when the step completed (an export command per declared output
        printed), 1 when it was refused or failed.
    """
    from . import cli_render as render
    from .execution.draft import draft_run_label, read_draft_run, spell_draft_ref
    from .execution.try_run import TryRefusedError, TryRequest, run_try

    as_json = args.as_json
    try:
        root = get_project_root()
    except RuntimeError as exc:
        return render.fail(f"No wfc project found: {exc}", as_json=as_json)
    # Under --json stdout holds the one object: every line the try prints on
    # the way (the draft added, the units it calls) goes to stderr.
    lines_to = contextlib.redirect_stdout(sys.stderr) if as_json else contextlib.nullcontext()
    try:
        with lines_to:
            inputs = _MethodInputs(args, root)
            docker_down = _docker_gate()
            if docker_down:
                raise _TryRefused(docker_down)
            pointer = _use_draft(inputs, root)
            previous = read_draft_run(inputs.module, inputs.method, root)

            def _replaces() -> None:
                if previous is not None:
                    print(f"Replaces {draft_run_label(previous)}.", flush=True)

            outcome = run_try(TryRequest(
                module=inputs.module, method=inputs.method,
                folder=Path(pointer.folder), params=inputs.params,
                sample=inputs.sample, parent_entries=inputs.entries),
                root, on_start=_replaces)
    except (_TryRefused, TryRefusedError) as exc:
        return render.fail(str(exc), as_json=as_json)
    if outcome.refusal is not None:
        return render.fail(outcome.refusal, as_json=as_json)
    ref = spell_draft_ref(inputs.module, inputs.method, inputs.method, inputs.sample)
    if outcome.returncode != 0:
        failed = (f"The try of {inputs.module}.{inputs.method} failed; nothing "
                  f"was recorded.")
        print(failed, file=sys.stderr)
        if as_json:
            render.print_json({"ok": False, "error": failed, "run": ref})
        return 1
    exports = render.try_exports(ref, inputs.contract.get("outputs") or {})
    if as_json:
        render.print_json({"ok": True, "module": inputs.module,
                           "method": inputs.method, "sample": inputs.sample,
                           "run": ref, "outputs": exports})
        return 0
    print(f"Tried {inputs.module}.{inputs.method} on {inputs.sample}: {ref}. "
          f"Nothing was recorded.")
    print("Export its outputs with:")
    for export in exports:
        print(f"  {export['export']}")
    return 0


def _cli_delete_draft(args) -> int:
    """``wfc delete-draft``: print Execution's delete plan, ask, apply it with its token.

    Remove draft (the draft folder goes) or, with ``--outputs-only``,
    Delete outputs (the draft is kept). The plan is printed first; the
    question is asked through :func:`_asker` (``--yes`` answers it; with no
    terminal and no ``--yes`` nothing is deleted). The apply half refuses
    when what it deletes changed since the plan.

    Args:
        args: The parsed ``delete-draft`` arguments.

    Returns:
        0 when deleted (or nothing to delete), 1 when refused or not confirmed.
    """
    from .execution.draft_clearing import (
        DraftDeletePlanChangedError,
        delete_draft_outputs_and_clear,
        plan_draft_delete,
        remove_draft_and_clear,
    )
    from .registration import DraftNotFoundError

    module, _, method = args.method.partition(".")
    if not module or not method:
        print(f"ERROR: name the draft's method as <module>.<method>, not "
              f"{args.method!r}.", file=sys.stderr)
        return 1
    try:
        root = get_project_root()
        plan = plan_draft_delete(module, method, outputs_only=args.outputs_only,
                                 root=root)
    except RuntimeError as exc:
        print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
        return 1
    except DraftNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(plan.message)
    if plan.nothing_to_delete:
        return 0
    ask = _asker(
        assume_yes=args.assume_yes, declined="Nothing was deleted.",
        refusal=(f"wfc delete-draft: deleting from the draft of {args.method} "
                 "needs confirmation and there is no terminal to ask on. "
                 "Nothing was deleted. Re-run with --yes to delete as listed "
                 "above."))
    if not ask("Proceed?"):
        return 1
    apply = delete_draft_outputs_and_clear if args.outputs_only else remove_draft_and_clear
    try:
        applied = apply(module, method, root, planned=plan.token)
    except (DraftDeletePlanChangedError, DraftNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(applied.done_message)
    return 0


def _session_inputs(inputs: _MethodInputs, root: Path) -> dict[str, list[str]]:
    """Each input's host paths for a wired session: parent entries, then the sample.

    The sample feeds the one input no parent entry feeds, by Contracts'
    rule (the one ``wfc try`` uses).

    Raises:
        _TryRefused: An entry or the sample cannot be read, or the sample
            could feed several inputs (named).
    """
    from .execution.draft import draft_step_output
    from .storage import ResolveOutputError, restore_sample, sample_data_path

    paths: dict[str, list[str]] = {}
    for entry in inputs.parsed:
        if entry.draft is not None:
            found = draft_step_output(entry.draft, entry.source_slot, root)
            if found.path is None:
                raise _TryRefused(f"input '{entry.input_slot}' from {entry.draft}: "
                                  f"{found.refusal}")
            located = found.path
        else:
            from .storage import resolve_input
            try:
                located = resolve_input(run_id=entry.run_id, slot=entry.source_slot)
            except (ResolveOutputError, Exception) as exc:  # noqa: BLE001
                raise _TryRefused(f"input '{entry.input_slot}' from run "
                                  f"{entry.run_id}: {exc}") from exc
            if not located:
                raise _TryRefused(f"input '{entry.input_slot}': run {entry.run_id} "
                                  f"has no such output.")
        paths.setdefault(entry.input_slot, []).append(str(located))
    slot = None
    if inputs.sample:
        from .contracts import SampleInputError, sample_input
        from .execution.try_run import FEED_THE_OTHERS
        try:
            slot = sample_input(f"{inputs.module}.{inputs.method}",
                                inputs.contract.get("inputs"),
                                {entry.input_slot for entry in inputs.parsed})
        except SampleInputError as exc:
            raise _TryRefused(f"{exc} {FEED_THE_OTHERS}") from exc
    if slot is not None:
        try:
            restore_sample(inputs.sample, project_root=root)
            data = sample_data_path(root, inputs.sample)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - named for the user
            raise _TryRefused(f"sample '{inputs.sample}' cannot be read: {exc}") from exc
        paths.setdefault(slot, []).append(str(data))
    return paths


def _session_variables(args, root: Path) -> tuple[str, dict[str, str]]:
    """The env and the variables of a wired ``wfc shell|jupyter --method`` session.

    The env comes from ``method.yaml``. The session's run folder is the
    draft's session scratch (emptied now, never a draft run); the step's
    ``WFC_*`` values, the declared names, ``PYTHONUNBUFFERED`` and the env's
    ``bin`` come from Execution's one builder, with container paths, so
    ``RunContext()`` constructs in the container.

    Returns:
        ``(env name, variables)``.

    Raises:
        _TryRefused: Why the session cannot start (nothing was started).
    """
    import shutil

    from .contracts import declared_names
    from .environments import version_bin_dir
    from .execution.container_vars import container_variables, step_variables

    inputs = _MethodInputs(args, root)
    pointer = _use_draft(inputs, root)
    session = layout.draft_session_dir(root, pointer.module, pointer.method)
    if session.exists():
        shutil.rmtree(session)
    session.mkdir(parents=True)
    print(f"Outputs go to {session}, emptied at each session start; they are "
          f"never a draft run.")
    slot_paths = _session_inputs(inputs, root)
    env_bin = version_bin_dir(inputs.env, inputs.env_record.env_fingerprint, root)
    step = step_variables(run_id="session", run_dir=str(session.resolve()),
                          sample=inputs.sample or "", params=inputs.params,
                          node_id=inputs.method, pipeline_id="session",
                          variant="default", slot_paths=slot_paths)
    declared = declared_names(inputs.contract.get("inputs"),
                              inputs.contract.get("outputs"))
    mounts = layout.mount_table(root, layout.dvc_cache_dir(root))
    return inputs.env, container_variables(step=step, declared=declared,
                                           env_bin=env_bin, mounts=mounts)


def _cli_dev_session(args, kind: str) -> int:
    """``wfc shell`` / ``wfc jupyter``: an env, or a method folder wired as a step.

    ``<env>`` alone is unchanged. ``--method <dir> --module <m>`` takes
    ``wfc try``'s input flags and gives the container a step's variables.

    Args:
        args: The parsed arguments.
        kind: ``"shell"`` or ``"jupyter"``.

    Returns:
        The container's exit code, or 1 when refused.
    """
    from .environments import dev_loop

    if (args.env is None) == (args.method_dir is None):
        print(f"ERROR: wfc {kind} takes an env name or --method <dir> --module "
              f"<module>, not both.", file=sys.stderr)
        return 1
    if args.method_dir is None:
        if any((args.module, args.sample, args.params, args.parent_run_id)):
            print("ERROR: --module, --sample, --params and --parent-run-id need "
                  "--method <dir>.", file=sys.stderr)
            return 1
        env, variables = args.env, _dev_loop_variables(args.env)
    else:
        try:
            root = get_project_root()
            env, variables = _session_variables(args, root)
        except RuntimeError as exc:
            print(f"ERROR: No wfc project found: {exc}", file=sys.stderr)
            return 1
        except _TryRefused as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if kind == "jupyter":
        return dev_loop.jupyter(env, port=args.port, variables=variables)
    return dev_loop.shell(env, variables=variables)


class _HelpFormatter(argparse.HelpFormatter):
    """Leaves hidden verbs out of ``wfc --help``.

    A hidden verb is added with ``help=argparse.SUPPRESS``. On Python 3.12
    argparse still lists it (with ``==SUPPRESS==`` as its help) and names it
    in the usage line's ``{...}``; this formatter drops it from both. Its
    parser and argv are unchanged: a generated Snakefile still calls it.
    """

    @staticmethod
    def _shown(choice: argparse.Action) -> bool:
        return choice.help is not argparse.SUPPRESS

    def _iter_indented_subactions(self, action):
        for subaction in super()._iter_indented_subactions(action):
            if self._shown(subaction):
                yield subaction

    def _metavar_formatter(self, action, default_metavar):
        if isinstance(action, argparse._SubParsersAction) and action.metavar is None:
            names = [choice.dest for choice in action._choices_actions
                     if self._shown(choice)]
            text = "{" + ",".join(names) + "}"
            return lambda tuple_size: (text,) * tuple_size
        return super()._metavar_formatter(action, default_metavar)


class _Parser(argparse.ArgumentParser):
    """Leaves hidden verbs out of argparse's "invalid choice" message.

    The message lists the verbs ``wfc --help`` lists; a hidden verb still
    parses, as :class:`_HelpFormatter` leaves it.
    """

    def _check_value(self, action, value):
        if (isinstance(action, argparse._SubParsersAction)
                and action.choices is not None and value not in action.choices):
            shown = [choice.dest for choice in action._choices_actions
                     if choice.help is not argparse.SUPPRESS]
            raise argparse.ArgumentError(
                action, f"invalid choice: {value!r} (choose from {', '.join(shown)})")
        super()._check_value(action, value)


def _describe_verbs(sub: argparse._SubParsersAction) -> None:
    """Give each listed verb's parser its list line as its description.

    So ``wfc <verb> --help`` opens with what ``wfc --help`` says about it.
    A verb that sets its own description keeps it.

    Args:
        sub: The subparsers action whose verbs to describe.
    """
    for choice in sub._choices_actions:
        verb = sub.choices[choice.dest]
        if verb.description is None and choice.help is not argparse.SUPPRESS:
            verb.description = choice.help


def _json_flag(cmd: argparse.ArgumentParser,
               text: str = "Print one JSON object instead of text") -> None:
    """Give a reading verb ``--json`` (``args.as_json``).

    Args:
        cmd: The verb's parser.
        text: The flag's help.
    """
    cmd.add_argument("--json", action="store_true", dest="as_json", help=text)


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the ``wfc`` verbs."""
    parser: argparse.ArgumentParser = _Parser(
        prog="wfc",
        description="wfc: register methods, samples and envs, run pipelines "
                    "and read their runs. Run `wfc <command> --help` for a "
                    "command's options.",
        formatter_class=_HelpFormatter,
    )
    parser.add_argument(
        "--version", action=_VersionAction, dest="version",
        help="Print the installed wfc version and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- restore-sample (hidden: a generated Snakefile calls it) --
    rss = sub.add_parser(
        "restore-sample", help=argparse.SUPPRESS,
        description="Restore a sample from the DVC cache to "
                    ".wfc/workspace/samples/",
    )
    rss.add_argument("--name", required=True, help="Sample identifier")
    rss.add_argument("--hash", default=None, dest="content_hash",
                     help="Expected content hash (default: the registered hash)")

    # -- fail_pipeline (hidden: a generated Snakefile's onerror calls it) --
    fail = sub.add_parser("fail_pipeline", help=argparse.SUPPRESS,
                          description="Mark a pipeline's in-flight runs as failed")
    fail.add_argument("--pipeline-id", required=True)

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
    doctor_cmd = sub.add_parser(
        "doctor",
        help="Check run-readiness: git, DVC, Docker, the project's disk, "
             "samples and views; then the env images (registry, runnable "
             "versions), whether a step's container sees the project, and "
             "whether an earlier run or crash left containers or env builds "
             "running (`wfc prune-envs` stops them). Exits non-zero on any "
             "fail.",
    )
    _json_flag(doctor_cmd)

    # -- seed (retired, hidden) --
    sub.add_parser(
        "seed", help=argparse.SUPPRESS,
        description="(retired) Replaced by `wfc demo`: prints a pointer and "
                    "exits non-zero",
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
                    help="With --remove: remove without asking. With no "
                         "terminal and no --yes, nothing is removed")

    # -- register-sample --
    rs = sub.add_parser(
        "register-sample",
        help="Register a data sample: store its content in the DVC cache and "
             "record where a restore puts it",
        epilog=("A sample name is registered once. To use a corrected file, "
                "register it under a new name and point the Input Selector "
                "at that name."),
    )
    rs.add_argument("--name", required=True,
                    help="Sample identifier (e.g. CFPAC_ERKi); a name is "
                         "registered once")
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

    # -- run-pipeline and run-method: one launch, two ways to name it --
    def launch_switches(verb: argparse.ArgumentParser) -> None:
        verb.add_argument("--project-root", default=None,
                          help="Project directory (default: the project containing "
                               "the current directory)")
        verb.add_argument("--cores", type=int, default=4,
                          help="Number of jobs Snakemake runs at once (default: 4)")
        verb.add_argument("--snakefile", default=None,
                          help="Where to write the Snakefile (default: the "
                               "pipeline's log directory, "
                               "<project-root>/.wfc/runs/pipelines/<id>/Snakefile)")
        verb.add_argument("--archive", action="store_true", default=True,
                          dest="archive",
                          help="Archive outputs after pipeline completion "
                               "(default: on)")
        verb.add_argument("--no-archive", action="store_false", dest="archive",
                          help="Skip output archiving; archive later with "
                               "wfc cache archive")
        verb.add_argument("--keep-going", action="store_true", default=False,
                          dest="keep_going",
                          help="Pass --keep-going to Snakemake: a failed job "
                               "doesn't cancel independent jobs (useful for "
                               "fan-out pipelines)")

    rp = sub.add_parser("run-pipeline", help="Run a pipeline file")
    rp.add_argument("--pipeline", required=True,
                    help="Path to the pipeline JSON file")
    launch_switches(rp)

    rm = sub.add_parser(
        "run-method",
        help="Run one registered method on one sample, with the params and the "
             "name you give it")
    rm.add_argument("method",
                    help="The registered method, as <module>.<method>")
    rm.add_argument("--sample", required=True,
                    help="The registered sample it runs on")
    rm.add_argument("--params", default=None,
                    help="A JSON object of the method's parameter values, e.g. "
                         "'{\"n\": 3}'")
    rm.add_argument("--name", default=None,
                    help="The pipeline's name, and its file pipelines/<name>.json "
                         "(default: pipelines/<module>.<method>-<sample>.json, "
                         "with a fingerprint of --params before .json when "
                         "params are given)")
    rm.add_argument("--save", default=None,
                    help="Where to save the pipeline file instead; a relative "
                         "path resolves against the project root. With --name, "
                         "the name is the document's only. A different file "
                         "already there is refused, never overwritten")
    launch_switches(rm)

    # -- check-pipeline --
    cp = sub.add_parser(
        "check-pipeline",
        help="Check a pipeline file without running it: canvas Validate's "
             "verdict plus the load's, no Docker needed")
    cp.add_argument("--pipeline", required=True, help="Path to the pipeline JSON file")
    cp.add_argument("--project-root", default=None,
                    help="Project directory (default: the project containing the "
                         "current directory)")
    _json_flag(cp, "Print the verdict (ok, valid, errors, warnings, "
                   "draftRefusals) as one JSON object")

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
    rs_cmd = sub.add_parser("run-step", help=argparse.SUPPRESS,
                            description="Execute a single pipeline step end-to-end "
                                        "(a generated Snakefile's rules call it)")
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
    rs_cmd.add_argument("--draft", action="store_true",
                        help="Run the step as a draft step (a draft, or a step "
                             "downstream of one): nothing is claimed, recorded or "
                             "cached; results go to the draft's folder. Parent "
                             "entries may name a draft step (draft:<module>.<method>/"
                             "<node>@<sample>).")

    # -- pipeline-summary --
    ps_cmd = sub.add_parser("pipeline-summary", help=argparse.SUPPRESS,
                            description="Count a pipeline's run rows into its summary")
    ps_cmd.add_argument("--pipeline-id", required=True, help="Pipeline execution ID")

    # -- list-envs --
    _json_flag(sub.add_parser(
        "list-envs",
        help="List container envs in .wfc/envs.json",
    ))

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
        "--yes", "--force", action="store_true", dest="assume_yes",
        help=(
            "Delete without asking (--force is accepted as the same flag). "
            "With no terminal and no --yes, nothing is deleted. An env that "
            "a method version uses is refused even with --yes."
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
            "records the env's current state, including any ad-hoc "
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
             "from, an env's own packages.lock (its pip packages, after "
             "the pip marker, are installed too), or a uv project "
             "directory or its uv.lock. Requires --backend.",
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
             "on this machine and not yet pushed, or pushed to another "
             "registry base. The first run sets the registry ([registry] url) "
             "and commits it.",
    )
    pe_cmd.add_argument("name", nargs="?", default=None,
                        help="Env name (key in .wfc/envs.json)")
    pe_cmd.add_argument("--all", dest="all_envs", action="store_true",
                        help="Push the images of every env instead of one")
    pe_cmd.add_argument(
        "--registry", default=None, metavar="URL",
        help="The registry base to set on the first run (for example "
             "ghcr.io/<owner>/<repo>), when the git remote is not on GitHub "
             "or another base is wanted. Naming another base than the one "
             "set switches the project to it, after you confirm; "
             "`wfc push-env --all` then pushes every image there.",
    )
    pe_cmd.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Push and write as stated without asking (for "
                             "scripts and CI)")

    # -- prune-envs --
    pr_cmd = sub.add_parser(
        "prune-envs",
        help="Stop what an earlier run or crash left running for this "
             "project: run containers whose run has ended and env builds no "
             "wfc command is waiting for. Lists them and asks first; never "
             "touches a run that is still running or another project's. "
             "Counts the env images nothing uses and the build cache, and "
             "removes neither.",
    )
    pr_cmd.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Print what would be stopped and stop nothing")
    pr_cmd.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Stop what is listed without asking. With no "
                             "terminal and no --yes, nothing is stopped.")

    # -- share --
    share_cmd = sub.add_parser(
        "share",
        help="List what a collaborator needs before you share the project "
             "through git (git remote, registry login and base, unpushed "
             "images, unconnected packages, method versions that cannot "
             "run), each with the command that fixes it. Changes nothing.",
        epilog=(
            "Runs, samples and outputs stay in this project's local wfc.db: "
            "a collaborator registers the samples again from the data. To "
            "hand over runs with their outputs, use `wfc export-run` (with "
            "--with-image for an image that is not pushed)."
        ),
    )
    _json_flag(share_cmd)

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
    _SESSION_NOTE = (
        " With --method <dir> --module <m> the env is the one method.yaml "
        "names and the container gets a step's inputs, parameters and run "
        "folder (the draft's session scratch, emptied at each start), so "
        "RunContext() works; the folder becomes the module's draft."
    )
    method_inputs = _method_input_flags()

    # -- jupyter --
    jup_cmd = sub.add_parser(
        "jupyter", parents=[method_inputs],
        help="Launch Jupyter Lab in an ephemeral container of the env's image",
        epilog=_EPHEMERAL_REMINDER + _SESSION_NOTE,
    )
    jup_cmd.add_argument("env", nargs="?", default=None,
                         help="Env name (key in .wfc/envs.json)")
    jup_cmd.add_argument("--method", dest="method_dir", default=None, metavar="DIR",
                         help="A method folder to wire the session to")
    jup_cmd.add_argument(
        "--port", type=int, default=None,
        help="Host port for Jupyter (default: the first free port "
             "from 8888 to 8999)",
    )

    # -- shell --
    sh_cmd = sub.add_parser(
        "shell", parents=[method_inputs],
        help="Drop into an interactive shell in an ephemeral container",
        epilog=_EPHEMERAL_REMINDER + _SESSION_NOTE,
    )
    sh_cmd.add_argument("env", nargs="?", default=None,
                        help="Env name (key in .wfc/envs.json)")
    sh_cmd.add_argument("--method", dest="method_dir", default=None, metavar="DIR",
                        help="A method folder to wire the session to")

    # -- try --
    try_cmd = sub.add_parser(
        "try", parents=[_method_input_flags(try_verb=True)],
        help="Run a method folder, registered or not, as its module's draft "
             "in its env's container; nothing is recorded or cached",
        epilog=(
            "The folder becomes the method's draft (or is its draft already); "
            "a draft from another folder is refused. The result is that "
            "draft's draft run, a one-step pipeline named try-<method>, kept "
            "until the draft runs again. Parent entries take run ids or draft "
            "strings. The command ends with an export command per output."
        ),
    )
    try_cmd.add_argument("method_dir", metavar="METHOD_DIR",
                         help="The method folder (holds method.yaml)")
    _json_flag(try_cmd, "Print the outcome as one JSON object (the lines saying "
                        "what it does go to stderr)")

    # -- delete-draft --
    dd_cmd = sub.add_parser(
        "delete-draft",
        help="Remove a method's draft (its draft folder, draft run and "
             "results), or with --outputs-only only its draft run; prints "
             "what goes and asks first",
        epilog=(
            "Later drafts' results computed from it are cleared too, and the "
            "pipelines whose draft results change are named. The method "
            "folder is never touched. With no terminal and no --yes nothing "
            "is deleted."
        ),
    )
    dd_cmd.add_argument("method", metavar="MODULE.METHOD",
                        help="The draft's method, as <module>.<method>")
    dd_cmd.add_argument("--outputs-only", action="store_true", dest="outputs_only",
                        help="Delete only the draft run and its results; keep "
                             "the draft")
    dd_cmd.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Delete as printed without asking (for scripts "
                             "and CI)")

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
        help="Remove old run archives, unused code copies and the canvas's "
             "preview cache (.wfc/cache/previews/, converted again on demand), "
             "and optionally DVC local cache entries",
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
        "--yes", action="store_true", dest="assume_yes",
        help="Answer yes to the confirmation prompt. With no terminal and "
             "no --yes, nothing is removed.",
    )
    prune_cmd.add_argument(
        "--force", action="store_true",
        help="Prune even when the archive is unreachable, without asking. "
             "It never removes a cache entry that was not pushed (run `wfc "
             "cache push` first).",
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
    _json_flag(runs_cmd)

    # -- list-methods / list-modules / list-samples (what the Registry lists) --
    for verb, what in (("list-methods", "List every registered method: its newest "
                                        "version, version count, env and source"),
                       ("list-modules", "List every registered module: its method "
                                        "count, contracts, colour and description"),
                       ("list-samples", "List every registered sample: its type, "
                                        "size, registration time and source")):
        _json_flag(sub.add_parser(verb, help=what))

    # -- show-run (one run's detail) --
    show_run_cmd = sub.add_parser(
        "show-run",
        help="Show one run: status, method, sample, metrics, outputs, error "
             "and the last 40 lines of its stderr",
    )
    show_run_cmd.add_argument(
        "--run", required=True, metavar="RUN",
        help="A run id, or a draft string (draft:<module>.<method>/<node>@"
             "<sample>) for a draft step (`wfc runs` lists both)",
    )
    show_run_cmd.add_argument(
        "--log", choices=("stdout", "stderr"), default=None,
        help="Print that whole log instead",
    )
    _json_flag(show_run_cmd)

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
        "--run", required=True, dest="run_id", metavar="RUN",
        help="A run id, or a draft string (draft:<module>.<method>/<node>@"
             "<sample>) for a draft step's outputs while its draft run "
             "exists (`wfc runs` lists both)",
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
    _json_flag(export_cmd, "With --path: print the paths as one JSON object "
                           "(each output's slot, name and path)")

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
        "--run", type=_run_argument, action="append", required=True,
        dest="run_ids", metavar="ID",
        help="A run to export, with every run above it; repeat for more "
             "(`wfc runs` lists them). A draft string is refused: a draft "
             "step is not recorded",
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

    _describe_verbs(sub)
    _describe_verbs(cache_sub)
    return parser


def _utf8_streams() -> None:
    """Set stdout and stderr to UTF-8, replacing what cannot be encoded, line-buffered.

    A Windows pipe (Git Bash, a captured subprocess) otherwise encodes as
    cp1252 and buffers stdout in blocks, so a context line printed on stdout
    could arrive after the error it explains. Only a stream that supports
    ``reconfigure`` is changed: a test's ``StringIO`` capture is left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def cli_main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the selected verb, returning its exit code."""
    _utf8_streams()
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    if args.command == "fail_pipeline":
        from .execution.lifecycle import fail_pipeline
        fail_pipeline(pipeline_id=args.pipeline_id)
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
        return _cli_doctor(args)

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
        return _cli_run_pipeline(args)

    elif args.command == "run-method":
        return _cli_run_method(parser, args)

    elif args.command == "check-pipeline":
        return _cli_check_pipeline(args)

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
            draft=args.draft,
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
        if not args.as_json:
            # The footer's leftover counts: Environments' search over
            # Execution's liveness, passed in as a value.
            return verbs.list_envs(versions_of=version_fingerprints_recording_env,
                                   leftovers=_leftover_counts)
        from . import cli_render as render
        try:
            listing = verbs.env_listing(
                get_project_root(), versions_of=version_fingerprints_recording_env)
        except RuntimeError as exc:
            return render.fail(f"No wfc project found: {exc}", as_json=True)
        except ValueError as exc:
            return render.fail(str(exc), as_json=True)
        render.print_items(listing)
        return 0

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
        ask = _asker(
            assume_yes=args.assume_yes, declined="Aborted.",
            refusal=(f"wfc delete-env: deleting env {args.name!r} needs "
                     f"confirmation and there is no terminal to ask on. "
                     f"Nothing was deleted. Re-run with --yes to delete it."))
        return verbs.delete_env(args.name, references=references,
                                read_error=read_error, ask=ask)

    elif args.command == "register-env":
        return _cli_register_env(args)

    elif args.command == "push-env":
        return _cli_push_env(args)

    elif args.command == "prune-envs":
        return _cli_prune_envs(args)

    elif args.command == "share":
        return _cli_share(args)

    elif args.command == "jupyter":
        return _cli_dev_session(args, "jupyter")

    elif args.command == "shell":
        return _cli_dev_session(args, "shell")

    elif args.command == "try":
        return _cli_try(args)

    elif args.command == "delete-draft":
        return _cli_delete_draft(args)

    elif args.command == "exec":
        from .environments import dev_loop
        return dev_loop.exec_(args.env, args.cmd or [],
                              variables=_dev_loop_variables(args.env))

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
                ask=_asker(
                    assume_yes=args.assume_yes, declined="Aborted.",
                    refusal=("wfc cache prune: pruning needs confirmation and "
                             "there is no terminal to ask on. Nothing was "
                             "removed. Re-run with --yes to prune as listed "
                             "above.")),
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

    elif args.command in ("list-methods", "list-modules", "list-samples"):
        return _cli_list(args)

    elif args.command == "show-run":
        if args.log and args.as_json:
            parser.error("--log prints a whole log; it does not combine with --json")
        return _cli_show_run(args)

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
        if args.as_json and not args.path_only:
            parser.error("--json goes with --path (a copy changes the project)")
        from .execution.draft import is_draft_ref
        if args.as_json:
            from . import cli_render as render
            from .export import ExportPathError, export_paths
            run: int | str = (args.run_id if is_draft_ref(args.run_id)
                              else _run_argument(args.run_id))
            if not isinstance(run, int) and not is_draft_ref(run):
                parser.error(f"--run takes a run id or a draft string "
                             f"(draft:<module>.<method>/<node>@<sample>), not "
                             f"'{args.run_id}'")
            try:
                paths = export_paths(run, slot, export_all=args.export_all)
            except ExportPathError as exc:
                return render.fail(str(exc), as_json=True)
            except RuntimeError as exc:
                return render.fail(f"No wfc project found: {exc}", as_json=True)
            render.print_items(paths)
            return 0
        if is_draft_ref(args.run_id):
            from .export import export_draft_output
            return export_draft_output(
                ref=args.run_id, slot=slot, dest=dest,
                export_all=args.export_all, path_only=args.path_only,
                force=args.force)
        run_id = _run_argument(args.run_id)
        if not isinstance(run_id, int):
            parser.error(f"--run takes a run id or a draft string "
                         f"(draft:<module>.<method>/<node>@<sample>), not "
                         f"'{args.run_id}'")
        from .export import export_output
        return export_output(
            run_id=run_id,
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
        _bind_project_root(args.project_root)
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
