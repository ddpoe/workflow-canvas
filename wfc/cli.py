"""
wfc CLI: the command-line verbs.

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

def _cli_register_env(args) -> int:
    """``wfc register-env``: turn the parsed arguments into values for the verb.

    The body, with its three input modes, is
    :func:`wfc.environments.verbs.register_env`.

    Args:
        args: The parsed ``register-env`` arguments.

    Returns:
        The verb's exit code.
    """
    from .environments import verbs

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
    )


# =============================================================================
# argparse entry-point
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
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
    rss = sub.add_parser("restore-sample", help="Restore a sample from DVC cache to data/samples/")
    rss.add_argument("--name", required=True, help="Sample identifier")
    rss.add_argument("--hash", default=None, dest="content_hash",
                     help="Expected content hash (optional; looked up from DB if omitted)")

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
    look.add_argument("--sample", required=True)
    look.add_argument("--nf-process-name", default=None)

    # -- init --
    init_p = sub.add_parser("init", help="Scaffold a new wfc project directory")
    init_p.add_argument("--dir", default=".", help="Target directory (default: current dir)")
    init_p.add_argument("--git", action="store_true",
                        help="(no-op) git is initialized by default.")
    init_p.add_argument("--archive", default=None, metavar="PATH",
                        help="DVC archive location (a directory path, or a "
                             "DVC remote URL like s3://). "
                             "Default: ~/.wfc/archives/<project>")
    init_p.add_argument("--yes", action="store_true", dest="assume_yes",
                        help="Run non-interactively, accepting all defaults")

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
                    help="Existing initialised project directory (default: cwd)")
    dm.add_argument("--port", type=int, default=8500,
                    help="Canvas port (default: 8500)")
    dm.add_argument("--no-open", action="store_true", dest="no_open",
                    help="Scaffold and serve without opening a browser")
    dm.add_argument("--force", action="store_true",
                    help="Re-register over an existing demo")
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
                    help="Path to module directory containing module.yaml (contracts loaded from file)")

    # -- register-method --
    rme = sub.add_parser("register-method", help="AST-scan method script and register method + params")
    rme.add_argument("method_dir", help="Path to the method directory containing the method script")
    rme.add_argument("--module", required=True, help="Module name this method belongs to")
    rme.add_argument("--name", default=None, help="Method name (defaults to directory name)")
    rme.add_argument("--script", default=None,
                     help="Script filename to scan (default: {method_name}.py)")

    # -- run-pipeline --
    rp = sub.add_parser("run-pipeline", help="Generate Snakefile and run the pipeline")
    rp.add_argument("--pipeline", required=True, help="Path to the pipeline JSON file")
    rp.add_argument("--project-root", default=None,
                    help="wfc project directory (git repo with method commits). Defaults to cwd.")
    rp.add_argument("--wfc-root", default=None,
                    help="Unused; accepted for compatibility. The generated file "
                         "finds wfc through the interpreter that runs Snakemake.")
    rp.add_argument("--cores", type=int, default=4, help="Snakemake cores (default: 4)")
    rp.add_argument("--snakefile", default=None,
                    help="Where to write the Snakefile (default: the pipeline's log "
                         "directory, <project-root>/.runs/pipelines/<id>/Snakefile)")
    rp.add_argument("--archive", action="store_true", default=True, dest="archive",
                    help="Archive outputs after pipeline completion (default: on)")
    rp.add_argument("--no-archive", action="store_false", dest="archive",
                    help="Skip output archiving after pipeline completion")
    rp.add_argument("--keep-going", action="store_true", default=False, dest="keep_going",
                    help="Pass --keep-going to Snakemake: a failed job doesn't "
                         "cancel independent jobs (useful for fan-out pipelines)")

    # -- canvas --
    cv = sub.add_parser("canvas", help="Launch the workflow canvas web UI")
    cv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    cv.add_argument("--port", type=int, default=8500, help="Bind port (default: 8500)")
    cv.add_argument("--reload", action="store_true", help="Enable auto-reload (development mode)")
    cv.add_argument("--project-root", default=None,
                    help="Path to wfc project directory (default: cwd). "
                         "The directory must contain .wfc/wfc.db.")

    # -- run-step --
    rs_cmd = sub.add_parser("run-step", help="Execute a single pipeline step end-to-end")
    rs_cmd.add_argument("--node-id", required=True, help="Unique node identity in the pipeline")
    rs_cmd.add_argument("--sample", required=True, help="Sample identifier")
    rs_cmd.add_argument("--variant", default="default", help="Parameter variant name")
    rs_cmd.add_argument("--method", default=None, help="Method name (inline fallback)")
    rs_cmd.add_argument("--module", default=None, help="Module name (inline fallback)")
    rs_cmd.add_argument("--script", default=None, help="Path to method script (inline fallback)")
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
                             "Repeatable. The runtime walks data/samples/<s>/ per name "
                             "and accumulates the per-sample data files into the fan-in "
                             "slot. Order is preserved.")

    # -- pipeline-summary --
    ps_cmd = sub.add_parser("pipeline-summary", help="Aggregate outcome sidecars into summary")
    ps_cmd.add_argument("--pipeline-id", required=True, help="Pipeline execution ID")

    # -- list-envs --
    le_cmd = sub.add_parser(
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
            "Skip the confirmation prompt "
            "(the warn-on-reference listing still prints)."
        ),
    )

    # -- register-env --
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
            "panel (or via GET /api/registry/envs/blob/<md5>) before "
            "relying on the image for downstream runs."
        ),
    )
    re_cmd.add_argument("name", help="Env name (key in .wfc/envs.json)")
    re_cmd.add_argument(
        "spec", nargs="?", default=None,
        help="Optional typed env spec to capture from a live env "
             "(conda:<env>, pixi:<name>, pixi:<proj>:<env>). Mutually "
             "exclusive with --backend and --from.",
    )
    re_cmd.add_argument(
        "--backend", default=None,
        choices=["pixi", "conda", "byo"],
        help="Build backend. Inferred from positional typed-spec when "
             "present; required for --from (pixi|conda) and byo mode.",
    )
    re_cmd.add_argument(
        "--from", dest="from_path", default=None, metavar="PATH",
        help="File-mode: copy this file into the build context under "
             "the generator's expected filename (explicit-list.txt for "
             "conda, pixi.lock for pixi). Requires explicit --backend.",
    )
    re_cmd.add_argument("--image", default=None,
                        help="docker:// reference for --backend byo")
    re_cmd.add_argument("--base-image", default=None,
                        help="Override the default base image for this env")
    re_cmd.add_argument(
        "--interpreter", dest="interpreter_path", default=None, metavar="PATH",
        help="Container-side path of the env's interpreter, recorded "
             "verbatim in .wfc/envs.json and used by `wfc run-step` to "
             "launch method scripts (e.g. '/opt/conda/bin/Rscript' for R, "
             "'/bin/bash' for bash, or a python path for byo images whose "
             "Python is not on PATH). Never validated against the host — "
             "the path is resolved inside the container at run time "
             "(default: computed per backend; byo defaults to 'python').",
    )
    re_cmd.add_argument(
        "--python", dest="python_path", default=None, metavar="PATH",
        help="Alias for --interpreter (kept for compatibility). Pass only "
             "one of the two.",
    )
    re_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Write the Dockerfile to .wfc/build/<name>/Dockerfile and "
             "exit; do NOT invoke docker. Requires --from file mode for "
             "pixi/conda; live-env capture (positional typed specs) is "
             "not supported under --dry-run.",
    )
    re_cmd.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing manifest entry for <name>. "
             "Default behavior is to error if the env is already "
             "registered.",
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
        help="Host port to forward to the container's 8888. "
             "Default: autopick the first free port in 8888-8999 "
             "(port 8000 is always skipped due to local conflicts).",
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
        help="Remove all run archives regardless of reference status",
    )
    prune_cmd.add_argument(
        "--include-local", action="store_true",
        help="Also prune the DVC local cache (.dvc/cache/) for unreferenced hashes",
    )
    prune_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting",
    )
    prune_cmd.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt AND prune unpushed cache entries. "
             "Without it, a hash whose Sample/RunOutput row has no pushed_at "
             "is kept: its bytes never reached the archive, so deleting it "
             "is permanent loss. With it, those entries are deleted too.",
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
            "under the project). Run `wfc export <run-id>` with no slot to list the "
            "run's output slots and their file names."
        ),
    )
    export_cmd.add_argument("run_id", type=int, help="Run ID to export from")
    export_cmd.add_argument(
        "slot", nargs="?", default=None,
        help="Output slot to export (omit to list the run's output slots)",
    )
    export_cmd.add_argument(
        "dest", nargs="?", default=None,
        help="Destination file or directory for the exported copy",
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

    return parser


def cli_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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
        from .storage import InputUnavailableError, resolve_input
        try:
            path = resolve_input(run_id=args.run_id)
        except InputUnavailableError:
            # The resolver's FAIL on stderr already names the path and the re-run.
            return 1
        if path is None:
            print("ERROR: no output found for run", file=sys.stderr)
            return 1
        print(path)
        return 0

    elif args.command == "lookup_run":
        from .execution.claim import lookup_run
        run_id = lookup_run(args.method, args.sample, nf_process_name=args.nf_process_name)
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
        from .execution.readiness import run_all_checks
        from .preflight import render_health_table
        from .execution.readiness import default_project_dir
        results = run_all_checks(default_project_dir())
        print(render_health_table(results))
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
        from .registration import register_method
        try:
            register_method(
                method_dir=Path(args.method_dir),
                module_name=args.module,
                method_name=args.name,
                script_name=args.script,
            )
        # FileNotFoundError covers script discovery misses (probe found no
        # candidate; explicit script:/--script names a missing file) — a
        # user input problem, not a crash.
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0

    elif args.command == "run-pipeline":
        from .execution import run_pipeline
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
            script_path=args.script,
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
        from .environments import verbs
        return verbs.list_envs()

    elif args.command == "show-env":
        from .environments import verbs
        return verbs.show_env(args.name)

    elif args.command == "delete-env":
        # Warn-on-reference: Registration reads the methods whose env names
        # this one. A failed read (a missing or uninitialized DB) counts as
        # no references: deletion still works on the manifest, and the
        # warning is best-effort.
        from .environments import verbs
        from .registration import methods_referencing_env
        try:
            references = methods_referencing_env(args.name)
        except Exception:
            references = []
        return verbs.delete_env(args.name, references=references, force=args.force)

    elif args.command == "register-env":
        # Docker-down pre-gate: `docker build` would otherwise fail with a raw
        # subprocess error. Reframe that specific shape into the one-door message.
        # SKIP the gate on --dry-run: dry-run renders the Dockerfile WITHOUT
        # touching Docker (it must succeed daemon-up or daemon-down).
        if not getattr(args, "dry_run", False):
            from .execution.readiness import check_docker
            _dock = check_docker()
            if _dock.status == "fail":
                print(_not_runnable_message("docker", _dock.message, _dock.fix_hint),
                      file=sys.stderr)
                return 1
        return _cli_register_env(args)

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
            return cache_prune(
                prune_all=args.prune_all,
                include_local=args.include_local,
                dry_run=args.dry_run,
                force=args.force,
            )
        elif args.cache_command == "archive":
            from .storage import cache_archive
            return cache_archive(run_id=args.run_id)
        return 1

    elif args.command == "export":
        slot = args.slot
        dest = args.dest
        if args.export_all:
            # `wfc export 412 --all ./out` binds ./out to the slot
            # positional — rebind it as the destination.
            if slot is not None and dest is None:
                slot, dest = None, slot
            elif slot is not None:
                parser.error(
                    "--all exports every output; do not pass a slot"
                )
        if args.path_only and dest is not None:
            parser.error("--path cannot be combined with a destination")
        if not args.path_only and slot is not None and dest is None:
            parser.error(
                "destination required (or use --path to print the cache path)"
            )
        if args.export_all and not args.path_only and dest is None:
            parser.error("--all requires a destination directory (or --path)")
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
