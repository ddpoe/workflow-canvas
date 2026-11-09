---
name: using-wfc
description: Use when working in a Workflow Canvas (wfc) project or with the `wfc` command, or asking how wfc works (projects, modules, methods, samples, envs, drafts and versions, caching, provenance, reproducibility), such as setting up a project, registering an environment, sample or method, running a pipeline, reading runs and their outputs, sharing a project, or working out why a wfc command or run failed. To turn a script into a wfc method or change a registered one, use the creating-a-wfc-method skill; to turn a whole analysis (several steps) into a pipeline, use converting-an-analysis-to-a-wfc-pipeline.
---

# Using wfc

Workflow Canvas (`wfc`) runs computational pipelines reproducibly: each step runs in a container, its result is cached by everything that went into it, and every run records exactly which code, data and environment made it. You drive it from the shell with `wfc <command>`, and from a browser with `wfc canvas`.

## The model

- **Project**: a folder holding `.wfc/`, where wfc keeps everything it manages. It is a git repository; wfc commits to it when you register things and never pushes.
- **Module**: a named group of methods, registered from a folder with a `module.yaml`.
- **Method**: one step of a pipeline, a folder holding a script (Python, R or bash) and a `method.yaml` that declares its inputs, outputs, parameters and env.
- **Sample**: a registered input file or folder. wfc keeps a copy, so the original can move. A sample name is registered once: for changed data, register it under a new name and pick that sample in the pipeline.
- **Env**: a registered container image a method runs in, built from a conda, pixi or uv environment, or your own image.
- **Pipeline**: a JSON document wiring samples into methods, usually built in the canvas and run with `wfc run-pipeline` or from the canvas.
- **Run**: one step run on one sample, recorded with its code version, inputs, parameters and outputs.
- **Draft**: a method folder tried before it is registered. `wfc try` runs it on a sample, and a draft runs in the canvas like any method. Nothing a draft does is recorded or cached. A draft keeps one draft run: the results of every node that ran it in its latest pipeline (or `wfc try`). Running it in another pipeline replaces them all, so run every node and sample you want to compare in one pipeline, or register the method to keep results.

The working order is: **try or draft, then register, then run, then share.**

Answer a question about how wfc works from this model and the docs page it touches. Once the user asks you to bring a script in, or to change a method, switch to the **creating-a-wfc-method** skill; for a whole analysis with several steps (a folder of notebooks and scripts, or one large notebook), switch to **converting-an-analysis-to-a-wfc-pipeline**.

## Commands

Run `wfc <command> --help` for every flag. `wfc --help` does not list some hidden commands: generated pipelines call `run-step`, `restore-sample` and `fail_pipeline`, and `pipeline-summary` is hidden too. Never run those yourself, and skip `seed`, which is retired.

| Command | What it does |
|---|---|
| `wfc init` | Makes a folder into a wfc project |
| `wfc doctor` | Checks git, Docker, the archive and the project, one line each, with the fix for each failure |
| `wfc demo` | Adds a runnable demo pipeline to a project and opens the canvas |
| `wfc register-env` | Builds and registers an env's container image |
| `wfc list-envs`, `show-env`, `delete-env` | List, show or remove registered envs |
| `wfc push-env` | Pushes env images to the project's registry, for sharing |
| `wfc register-module` | Registers a module from its `module.yaml` |
| `wfc try` | Runs a method folder on a sample as a draft; records nothing |
| `wfc register-method` | Registers a method folder as a new method or a new version |
| `wfc register-sample` | Registers a data file or folder as a sample |
| `wfc canvas` | Starts the canvas web app (default `http://127.0.0.1:8500`) |
| `wfc check-pipeline` | Checks a pipeline file without running it: the canvas Validate's verdict plus the load's, no Docker needed |
| `wfc run-pipeline` | Runs a pipeline JSON file |
| `wfc run-method` | Runs one registered method on one sample, recorded like any pipeline; `--params` sets its parameters as JSON, `--name` names the saved pipeline |
| `wfc delete-draft` | Removes a method's draft (wfc's record of it, its draft run and results; never the method folder), or with `--outputs-only` only its draft run; prints what goes and asks first |
| `wfc list-methods`, `list-modules`, `list-samples` | List what is registered |
| `wfc show-run` | Shows one run's detail, or its log with `--log stderr` or `--log stdout` |
| `wfc runs` | Lists runs and draft steps, with the run ids other commands take |
| `wfc export` | Copies a run's or a draft step's outputs out of wfc; `--all --dest <dir>` creates the folder, while `--slot <slot> --dest <dir>` with a folder that doesn't exist writes the file under that name; `--path` prints where an output is stored |
| `wfc export-run` | Writes runs and everything above them into a zip anyone can rerun without wfc |
| `wfc cancel` | Cancels a running pipeline |
| `wfc shell`, `jupyter`, `exec` | Open a shell, Jupyter, or run one command in an env's container |
| `wfc cache` | `prune`, `push` and `archive` the stored outputs |
| `wfc verify` | Re-hashes everything wfc stores and lists what is not as recorded |
| `wfc share` | Lists what a collaborator would be missing, with the command that fixes each item |

## Rules that matter

- **If you cannot run commands** (a chat with no access to the user's computer or project), say so once, then answer from this skill and the docs. Ask the user for `wfc --version` when the answer depends on it, and give each command for the user to run. When they need files (a method folder, `module.yaml`, a pipeline file), write them in full: as one zip laid out as the project expects if this chat can create files for download, otherwise each in its own block headed with its path. You can still help register an env from its package list (`conda env export` and `pip freeze`, or `pixi.lock`) by giving the commands. Never say a command ran, or that a file works, when you did not see its output.
- **Methods run in a Linux container, not on this machine.** A path, a package or a tool that exists only on the host is not there when a step runs. Everything a method uses comes from its env or its declared inputs.
- **Outputs must be declared.** Every output a method saves needs a name under `outputs:` in `method.yaml`, and the script saves each with `ctx.save_artifact("<name>", path)`. Registration refuses a mismatch either way.
- **Inputs are read-only.** A method writes into its run folder (`ctx.workdir`), never beside its inputs.
- **Registering adds a version and a git commit.** So try a method with `wfc try`, or as a draft in the canvas, until it works, then register it once.
- **wfc commits only what it registers, and never pushes.** The user's own code (such as `src/`), pipeline files and data are theirs to commit, and nothing reaches a collaborator until they `git push`; `wfc share` lists the rest of what a collaborator needs.
- **Never edit wfc's records under `.wfc/` by hand**: the registry, `envs.json`, drafts, runs and `wfc.db`. Change them through wfc's commands; method code is edited in your own folder (by convention `src/<module>/<method>/`) and registered again. The one file there you edit is the project config, `.wfc/wf-canvas.toml`, as its reference page describes.
- **Read state through wfc, not its internals.** `wfc runs`, `wfc list-envs`, `wfc show-env`, `wfc share`, `wfc doctor` and the canvas answer most questions; don't query `wfc.db` or wfc's own JSON files directly. A run's log files are the exception (see below).
- **Confirming commands need `--yes` in a script.** `wfc register-method`, `wfc init` in an existing project and the other commands that ask before changing something refuse when there is no terminal, which is the case in an agent's shell, and say to pass `--yes`. Each prints what it will do before refusing, so running it once without `--yes` shows the plan and changes nothing; for `wfc register-method`, tell the user what the method's `wfc try` said registering adds instead of starting it. Run a confirming command with `--yes` only once the user has agreed.
- **Test in a throwaway project.** To check how wfc behaves before answering, make a scratch project with its own archive (`wfc init --dir <scratch> --archive <scratch>-archive`) rather than registering or running anything in the user's project or archive. A question the docs or `--help` answer needs no test.
- **`wfc doctor` first.** For any problem, run `wfc doctor` before anything else; it names what is missing and how to fix it.

## When a run fails

1. `wfc doctor`, to rule out setup (Docker, git, the archive).
2. `wfc runs` (or `wfc runs --method <module>.<method>`) gives the failed run's id.
3. Read that run's log with `wfc show-run --run <run id> --log stderr` (or `--log stdout`); `wfc show-run --run <run id>` alone shows the run's detail. In the canvas, the run's detail panel shows the same log. Never edit anything under `.wfc/`.
4. Most failures are in the method script or its data. Give the user the whole fix: edit the method folder, `wfc try` it, `wfc register-method` it again (a new version), then rerun the pipeline (`wfc run-pipeline --pipeline <file>`, the same `wfc run-method` command for a one-method run, or Run in the canvas). A pipeline node with no version pinned runs the newest version.

## Setting up a project

1. `wfc init --dir <folder>`, then work from inside the folder. In a terminal it asks where to keep the archive, the backup copy of data and results (default `~/.wfc/archives/<project>`); in an agent's shell it takes the default without asking, so ask the user first and pass `--archive <path>` for another location.
2. `wfc doctor`. Every line should say `OK`; fix what doesn't (often Docker not running) and run it again.
3. Register an env. Pick the form that matches what the user has:
   - a conda env on this machine: `wfc register-env <name> conda:<conda-env>`
   - a pixi project: `wfc register-env <name> pixi:<project>:<env>`, or `--backend pixi --from pixi.lock`
   - a uv project: `wfc register-env <name> --backend uv --from <project-dir>`
   - an image of their own: `wfc register-env <name> --backend byo --image docker://<ref>`

   Say how long it takes before starting: a conda env from Windows or macOS takes a few minutes when it is small (about 5 minutes) and longer when it is large, most of it solving the env again for Linux and building its image (in 0.8.0b2 and earlier, 30 to 60 minutes), so start it in the background. Use the env the user already has and never modify it; for a new env, recommend pixi (a `pixi.lock` that includes `linux-64` skips the solve) or, for Python-only work, uv. While it builds, follow `wfc register-env`'s own output: when it prints phase lines, a heartbeat line means it is still working. After an interrupted registration, run `wfc list-envs`; when its footer reports leftovers and `wfc prune-envs --help` works (not in 0.8.0b2), suggest `wfc prune-envs`, and pass `--yes` only after the user agrees. Never give the user raw Docker commands.

   A Python method needs `wfc-client` installed in the env; when the user's env lacks it, make a copy with `wfc-client` added and register the copy, as the **creating-a-wfc-method** skill describes. An R or bash method's env is registered with `--interpreter` set to the interpreter's path inside the container (for example `/opt/conda/bin/Rscript` or `/bin/bash`), since the default is Python.
4. `wfc register-module --name <module> --module-dir src/<module>`, with a one-line `module.yaml` (`description: ...`) in that folder.
5. Register the data a method will run on: `wfc register-sample --name <name> --source <file or folder>`. Trying a method needs a registered sample.
6. To bring a script in as a method, use the **creating-a-wfc-method** skill.

## Reading the docs

Read the documentation for the installed version. With network access, run `wfc --version`:

- a release such as `0.8.0` reads `https://workflow-canvas.readthedocs.io/en/v<version>/`
- a pre-release, with `a`, `b` or `rc` in the version (such as `0.8.0b1`), reads `https://workflow-canvas.readthedocs.io/en/latest/`

Useful pages under that root: `tutorials/getting-started.html`, `how-to/build-your-own-pipeline.html` (the steps for your own analysis, each linking its page), `how-to/write-a-method.html`, `tutorials/drafting-a-method.html` (with `how-to/draft-from-the-command-line.html` and `how-to/draft-in-the-canvas.html`), `tutorials/authoring-a-method-script.html`, `tutorials/registering-an-environment.html`, `reference/command-line.html` (every command, one page per group), `reference/method-yaml-schema.html`, `reference/method-script-contract.html`, `how-to/canvas-hub.html`, `how-to/cli-hub.html` (which links every command-line task page, such as `how-to/cli-run.html`, `how-to/cli-runs.html` and `how-to/cli-export.html`), `how-to/sharing-a-project.html`. Page names can't be guessed: start from a hub page rather than trying a URL. Docs for 0.7 and earlier have the command reference at `reference/cli-reference.html`, and the canvas and command-line guides at `how-to/canvas.html` and `how-to/run-and-inspect-results.html`.

If a page is missing or there is no network, work from this skill and `wfc <command> --help`, which always matches the installed version. Shortly after a pre-release, `/en/latest/` can still show the previous release's pages; when a page disagrees with `--help` or with what the installed wfc prints, the installed wfc is right, and say so to the user.

## Known issues in 0.8.0

These are known wfc bugs in 0.8.0, planned to be fixed in 0.8.1 unless the entry says otherwise. When `wfc --version` is 0.8.0 (or a 0.8.0 pre-release), use the workaround and tell the user you are doing so; when it is 0.8.1 or later, try the plain way first.

- **`wfc check-pipeline` refuses a node with two inputs** (`only one edge per slot is supported`) when links name the slot with `target_slot`. Name each link's slots with `sourceHandle` and `targetHandle`.
- **A draft fails with `ModuleNotFoundError` or a missing file** for a helper listed outside the method folder (`helpers: [../shared/x.py]`). While it is a draft, keep a copy of the helper in the method folder.
- **A pipeline node with `"version": "draft"` and a relative `"folder"` is refused.** Leave `"folder"` out.
- **A node left at `"version": "draft"` runs the draft without a warning.** Before a final run, search the pipeline file for `"version": "draft"` and remove each one.
- **After a `wfc try` is killed (a closed terminal, a crash), the next try or run of that draft is refused as still in progress** (`… draft run is still in progress in pipeline <name>: wait for it to end, or cancel it, then run again.`), and `wfc cancel` does not close it. `wfc delete-draft <module>.<method> --outputs-only` removes that stuck draft run and its results and keeps the draft; ask the user first, since it shows what it deletes.
- **A pipeline that runs drafts reports `Total runs: 0`** although its steps ran: they are draft runs, listed by `wfc runs` and the canvas History.
- **`wfc run-pipeline` with uncommitted changes starts, then fails each step** (`Working tree has uncommitted changes to tracked files — commit before running.`) and leaves failed runs in History. Commit first; `wfc doctor` says whether there are uncommitted changes.
- **`wfc show-run` can give an archived output a path that does not exist**, and a cached run's log paths do not exist. `wfc export --run <id> --slot <slot> --path` gives the real file; a cached run's logs are those of the run it reused.
- **Registering a cloned conda env from Windows or macOS fails** (`Bad expected access`). Never copy an env with `conda create --clone`; build the copy from the packages it asked for, as the **creating-a-wfc-method** skill describes.
- **`wfc register-env --dry-run` refuses an env the user already has** (`conda:<env>`). Register it; it shows each phase and keeps a log.
- **A step that reads several runs through references runs once per sample** when those runs were made for different samples: History shows two identical runs of the step, the second `⟳ CACHED`. Nothing is computed twice; tell the user they can ignore the extra run.
- **A step can't combine the outputs of a step that runs once per sample** (`… is sample-collapsed … a collapsed step cannot consume per-sample outputs of an upstream method step.`). Run the per-sample steps as one pipeline, then combine their runs in a second pipeline with a run-reference node for each run. Planned for 0.9.0.

## If wfc itself seems wrong

Check the known issues above first. If it matches one, it is a known bug: use its workaround, say so, and don't offer to file it.

Only wfc's own behaviour counts as a wfc bug: a wfc traceback or crash, a result that contradicts the docs or `--help` for the installed version, or an error that doesn't say what to do. An error from the user's script, Docker not running, or an env that isn't registered is setup: say so and point to `wfc doctor`. Something unintuitive that works as documented is a rough edge, not a bug.

Before calling it a bug, reproduce it once, check the docs for the installed version, and search the existing issues (`gh issue list --repo ddpoe/workflow-canvas --search "<words>"`, when `gh` is available).

Don't interrupt the task for it. If you work around a wfc bug, say that you are doing so; never work around one silently. At the end, add one short block:

> **I think I found a wfc bug:** `<command>` did `<what happened>`; I expected `<what the docs or --help say>`.

Then offer to draft an issue with the wfc version (`wfc --version`), the OS, the command, its output and what was expected, the body ending with "Reported by an agent using the wfc skill." File it only when the user says yes: `gh issue create --repo ddpoe/workflow-canvas`, or, without `gh`, a prefilled link `https://github.com/ddpoe/workflow-canvas/issues/new?title=<title>&body=<body>` with both parts URL-encoded.
