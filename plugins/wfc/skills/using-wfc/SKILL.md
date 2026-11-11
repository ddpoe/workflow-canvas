---
name: using-wfc
description: Use when working in a Workflow Canvas (wfc) project or with the `wfc` command, when installing wfc or what it needs (Python 3.12, Docker, git), when `wfc` is not found or `wfc doctor` reports a failure, or asking how wfc works (projects, modules, methods, samples, envs, drafts and versions, caching, provenance, reproducibility), such as setting up a project, registering an environment, sample or method, running a pipeline, reading runs and their outputs, sharing a project, or working out why a wfc command or run failed. To turn a script into a wfc method or change a registered one, use the creating-a-wfc-method skill; to turn a whole analysis (several steps) into a pipeline, use converting-an-analysis-to-a-wfc-pipeline.
---

# Using wfc

Workflow Canvas (`wfc`) runs computational pipelines reproducibly: each step runs in a container, its result is cached by everything that went into it, and every run records exactly which code, data and environment made it. You drive it from the shell with `wfc <command>`, and from a browser with `wfc canvas`.

## Check the versions

These skills are written for **wfc 0.8.0.post1**. At the start of a task, run `wfc --version` (or ask the user for it) and compare:

- **`wfc` is not found** (`command not found`, `not recognized`): follow **When `wfc` is not found** below before anything else. It is usually installed where this shell doesn't look.
- **The same version** (or a pre-release of it): carry on.
- **A newer wfc**: these skills may not know its new commands, or that a known issue below is fixed. Tell the user once that the wfc plugin looks out of date, and give the update for their agent. In Claude Code: `/plugin marketplace update workflow-canvas`, then `/plugin`, the Installed tab, wfc, Update now, then `/reload-plugins` (from a shell: `claude plugin marketplace update workflow-canvas` and `claude plugin update wfc@workflow-canvas`). In Codex: `codex plugin marketplace upgrade workflow-canvas`, then a new thread. Update it only when the user says yes, and then only with the shell form. Until it is updated, trust `wfc <command> --help` and the docs for the installed version over these skills.
- **An older patch of the same release** (the first two numbers match, such as 0.8.0 when these skills name 0.8.1): carry on, the commands are the same. Mention once that a newer patch is out, with the upgrade line below.
- **An older wfc**: commands these skills describe may not exist yet. Suggest an upgrade in the environment wfc is installed in: `python -m pip install -U workflow-canvas` with that environment's Python, or `uv pip install -U workflow-canvas` from the project folder when its `.venv` has no pip (uv makes one without it unless asked). Run it only when the user agrees; until then, use only what `wfc <command> --help` lists. When the version doesn't move, that environment's Python is not 3.12 (**When `wfc` is not found**, step 2): the fix is a new environment (step 5 there), made with the old one deactivated first.

## The model

- **Project**: a folder holding `.wfc/`, where wfc keeps everything it manages. It is a git repository; wfc commits to it when you register things and never pushes.
- **Module**: a named group of methods, registered from a folder with a `module.yaml`.
- **Method**: one step of a pipeline, a folder holding a script (Python, R or bash) and a `method.yaml` that declares its inputs, outputs, parameters and env.
- **Sample**: a registered input file or folder. wfc keeps a copy, so the original can move. A sample name is registered once: for changed data, register it under a new name and pick that sample in the pipeline.
- **Env**: a registered container image a method runs in, built from a conda, pixi or uv environment, or your own image. Its record is committed with the project; the image stays on the computer that built it until `wfc push-env`.
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

- **If you cannot run commands** (a chat with no access to the user's computer or project), say so once, then answer from this skill and the docs. Ask the user for `wfc --version` when the answer depends on it, and give each command for the user to run. When they need files (a method folder, `module.yaml`, a pipeline file), write them in full: as one zip laid out as the project expects if this chat can create files for download, otherwise each in its own block headed with its path. You can still help register an env from its package list (`conda env export` and `pip freeze`, or `pixi.lock`) by giving the commands. A user who doesn't have wfc yet gets steps 2 and 4 to 7 of **When `wfc` is not found** as one checklist in one reply, written for their system (tell it from the paths and errors they pasted, and ask only when you can't): what wfc needs, the link for each piece, the install commands and the check. Don't hold a step back until they report, don't ask where the project or the archive should go, and never offer to run a command. When they paste an error, name its cause, give the fix and stop there; the rest of the setup is for when they ask. Never say a command ran, or that a file works, when you did not see its output.
- **Methods run in a Linux container, not on this machine.** A path, a package or a tool that exists only on the host is not there when a step runs. Everything a method uses comes from its env or its declared inputs.
- **Outputs must be declared.** Every output a method saves needs a name under `outputs:` in `method.yaml`, and the script saves each with `ctx.save_artifact("<name>", path)`. Registration refuses a mismatch either way.
- **Inputs are read-only.** A method writes into its run folder (`ctx.workdir`), never beside its inputs.
- **Registering adds a version and a git commit.** So try a method with `wfc try`, or as a draft in the canvas, until it works, then register it once.
- **wfc commits only what it registers, and never pushes.** The user's own code (such as `src/`), pipeline files and data are theirs to commit, and nothing reaches a collaborator until they `git push`; `wfc share` lists the rest of what a collaborator needs. When you commit those files for the user and git has no name and email yet, ask for theirs; wfc's own `wfc <wfc@wfc>` is for wfc's commits only.
- **Never edit wfc's records under `.wfc/` by hand**: the registry, `envs.json`, drafts, runs and `wfc.db`. Change them through wfc's commands; method code is edited in your own folder (by convention `src/<module>/<method>/`) and registered again. The one file there you edit is the project config, `.wfc/wf-canvas.toml`, as its reference page describes.
- **Read state through wfc, not its internals.** `wfc runs`, `wfc list-envs`, `wfc show-env`, `wfc share`, `wfc doctor` and the canvas answer most questions; don't query `wfc.db` or wfc's own JSON files directly. A run's log files are the exception (see below).
- **Confirming commands need `--yes` in a script.** `wfc register-method`, `wfc init` in an existing project and the other commands that ask before changing something refuse when there is no terminal, which is the case in an agent's shell, and say to pass `--yes`. Each prints what it will do before refusing, so running it once without `--yes` shows the plan and changes nothing; for `wfc register-method`, tell the user what the method's `wfc try` said registering adds instead of starting it. Run a confirming command with `--yes` only once the user has agreed.
- **Test in a throwaway project.** To check how wfc behaves before answering, make a scratch project with its own archive (`wfc init --dir <scratch> --archive <scratch>-archive`) rather than registering or running anything in the user's project or archive. A question the docs or `--help` answer needs no test.
- **`wfc doctor` first.** For any problem, run `wfc doctor` before anything else; it names what is missing and how to fix it. Read all of its output, and all of `wfc init`'s: don't cut either with `tail`, `head` or `grep`.
- **`wfc canvas` and `wfc demo` run until they are stopped.** Start one only when the user asks, in the background, and tell them its address and that it stays up until stopped. When port 8500 is taken, often by another project's canvas, pass `--port <number>`; `wfc demo` only finds out after it has added the demo to the project.
- **Do what was asked, then stop.** When the problem is fixed or the setup is done, say what the next step would be and wait. Don't go on to register an env or a method nobody asked for.
- **Talk about wfc, not about these skills.** The user doesn't see them: don't name a skill or quote its rules to the user.

## When a run fails

When the user reports a failure that `wfc runs` doesn't show, it happened before anything was recorded, and that is setup: run `wfc doctor`, and ask the user what they ran and what it printed.

1. `wfc doctor`, to rule out setup (Docker, git, the archive).
2. `wfc runs` (or `wfc runs --method <module>.<method>`) gives the failed run's id.
3. Read that run's log with `wfc show-run --run <run id> --log stderr` (or `--log stdout`); `wfc show-run --run <run id>` alone shows the run's detail. In the canvas, the run's detail panel shows the same log. Never edit anything under `.wfc/`.
4. Most failures are in the method script or its data. Give the user the whole fix: edit the method folder, `wfc try` it, `wfc register-method` it again (a new version), then rerun the pipeline (`wfc run-pipeline --pipeline <file>`, the same `wfc run-method` command for a one-method run, or Run in the canvas). A pipeline node with no version pinned runs the newest version.

## When `wfc` is not found

`wfc: command not found` (or `not recognized`) in your shell usually means wfc is installed in an environment the shell hasn't activated, not that it is missing. It says nothing about the user's own terminal either. Tell them wfc isn't active in the shell you run commands in, not that it is missing from their PATH, and when a user tells you wfc isn't working, ask what they ran and what it printed before you name a cause.

Find wfc before you install anything, and never install a second copy. Throughout, leave the user's PATH, shell profile and system settings alone, and look only in the project folder, the folders above it and the user's home folder unless they agree to a wider search. In a chat where you can't run commands, follow the first bullet of **Rules that matter** instead of steps 1 and 3.

1. **Look for it**, and stop at the first place where `--version` answers:
   - what you saved in an earlier session (step 6)
   - a project's own environment, `.venv/Scripts/wfc.exe` (Windows) or `.venv/bin/wfc` (macOS, Linux): in the folder you are in, the folders directly inside it, its parent folders, and the project folder if the user named one
   - another environment: `conda env list`, then `conda run -n <env> wfc --version` for an env named `wfc` or one the user names (when `conda` itself isn't found, look for `envs/wfc` under `~/miniforge3`, `~/miniconda3` or `~/anaconda3`); `uv tool list`; `pipx list`

   Found: write down where it is before anything else (step 6, first bullet). Then use it by its full path, or as `conda run -n <env> wfc ...`, for the rest of the session; nothing needs activating. Give the user step 6's activate line in your reply, and go back to **Check the versions**.

   Not found: before deciding it isn't installed, ask the user once whether wfc is already on this computer and where their project folder is. wfc is usually in that folder's `.venv`.

2. **Check what the computer has.** Install nothing yet.

   | wfc needs | Check with |
   |---|---|
   | Python 3.12, exactly | `uv --version` (uv brings its own Python 3.12); without uv, `py -0` on Windows or `python3.12 --version`; `conda --version` |
   | git | `git --version` |
   | Docker, installed and running | `docker --version`, then `docker info` |

   Check the interpreter you would install with, not `python --version` alone. On Python 3.13 or later pip refuses wfc (`No matching distribution found for workflow-canvas`); on 3.11 it installs wfc 0.5.0, an old release, and says nothing.

   A `docker` that isn't found may still be installed: Docker Desktop puts it on the PATH of terminals opened after the install. Ask the user before sending them to install it. When it is installed, they open Docker Desktop and start you again from a new terminal. Don't put Docker's folder on a PATH, yours or theirs: report what `wfc doctor` says in the shell as it is, and wait for the new terminal.

3. **Tell the user what you found, and ask everything in one message**:
   - what the computer has and what is missing, as a short list, with the link for each missing piece (step 4). Say that wfc needs Python 3.12 exactly and a Docker that is running.
   - where the project should live and what to call it, unless they already said. Suggest a new folder under their Documents. The folder you were started in is not an answer by itself: when it is Downloads, the Desktop, the home folder or a folder of unrelated files, say so and ask, even when the user said "here". It has to be on a local disk: wfc can't run a project on a network or mapped drive, though its archive can live on one.
   - where the archive, wfc's backup copy of data and results, should go; the default is `~/.wfc/archives/<project>`
   - that you will install wfc into an environment of its own in that folder, and whether to go ahead

   Create and install nothing until they answer. Naming a folder is not a yes to the install.

4. **The user installs what is missing** from these links, and you wait:
   - no uv, no Python 3.12 and no conda: uv, `https://docs.astral.sh/uv/getting-started/installation/`. It needs no administrator rights and downloads Python 3.12 itself. Don't send the user to python.org: its download button installs a newer Python, which wfc can't use. A user who would rather have conda gets Miniforge, `https://conda-forge.org/download/`.
   - git: `https://git-scm.com/install/`
   - Docker: Docker Desktop on Windows (`https://docs.docker.com/desktop/setup/install/windows-install/`) and macOS (`https://docs.docker.com/desktop/setup/install/mac-install/`), Docker Engine on Linux (`https://docs.docker.com/engine/install/`). It has to be running, not only installed; on Windows its first start may need an administrator, to turn on WSL 2.

   You run an installer only after the user says to. Show the exact command first (such as `winget install --id astral-sh.uv -e`, `brew install uv` or `sudo apt install git`), say what it installs, and wait for a yes. When the command is declined or fails, give the link and wait; don't retry it or try another route. For Docker Desktop give the link even when asked to install it: its installer needs the user at the screen for its terms, and sometimes an administrator and a restart. Never add a flag that accepts a licence for them (`--accept-package-agreements`, `--accept-license`): Docker Desktop's terms, which need a paid subscription in larger organisations, are theirs to accept. When an install needs administrator rights the user doesn't have, say so and stop; that is for whoever manages the computer. Don't suggest a substitute for Docker.

5. **Install wfc into the project folder**, with the first of these the computer has:
   - **uv**, from inside the project folder:

     ```
     uv venv --python 3.12 --seed
     uv pip install workflow-canvas
     ```

     Its `.venv` keeps itself out of git, and `--seed` gives it the pip an upgrade uses.
   - **Python 3.12 without uv**: `py -3.12 -m venv .venv` (Windows) or `python3.12 -m venv .venv`, then `.venv/Scripts/python -m pip install workflow-canvas` (`.venv/bin/python` on macOS and Linux). Put a `.venv/` line in the folder's `.gitignore` before `wfc init`, which commits that file.
   - **conda only**: `conda create -y -n wfc python=3.12`, then `conda run -n wfc python -m pip install workflow-canvas`. Every project on the computer shares this env.

   Never install wfc into conda's base env or the system Python. Check it with `wfc --version`, by its full path.

6. **Save where wfc is and tell the user how to run it**, as soon as `wfc --version` answers:
   - If you have a memory folder or a notes file that outlasts this session, write this there now: the project folder, and how you run wfc there (`<project>/.venv/Scripts/wfc.exe`, or `conda run -n wfc wfc`), in the line you will read first next time, not behind a pointer. Save it even when nothing else about the session is worth keeping: your next session starts in a shell that can't find wfc either. Never record it in `.wfc/wf-canvas.toml` or another file git tracks: an uncommitted change there makes every run fail, and the path is true on this computer only.
   - Give the user the line that activates wfc in their own terminal: `.venv\Scripts\activate` (Windows), `source .venv/bin/activate` (macOS, Linux) or `conda activate <env>`. On Windows, PowerShell may refuse it (`running scripts is disabled on this system`). That is PowerShell's script policy, not a broken install: `.venv\Scripts\wfc <command>` runs wfc with nothing activated, Command Prompt activates with `.venv\Scripts\activate.bat`, and the user can allow scripts for their own account with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, which is their choice to make and theirs to run.

7. **Set up the project** as below (`wfc init`, then `wfc doctor`), and finish by telling the user what `wfc doctor` reported. Have git installed before `wfc init`. When `wfc init` already ran without git, install git and run `wfc init` again; `wfc doctor` then says the repository has no commits, and that first commit is made by hand: `git add -A`, then `git commit -m "wfc init"`, with a `.venv/` line in `.gitignore` first if `git status` lists the `.venv`. A commit made by hand needs a name and an email, which a newly installed git doesn't have (wfc's own commits don't need them): ask the user for theirs and set them in the project (`git config user.name "<name>"`, `git config user.email "<email>"`).

## Setting up a project

1. Agree on the folder first (**When `wfc` is not found**, step 3): ask unless the user named it. Then `wfc init --dir <folder>`, and work from inside the folder. In a terminal it asks where to keep the archive, the backup copy of data and results (default `~/.wfc/archives/<project>`); in an agent's shell it takes the default without asking, so ask the user first and pass `--archive <path>` for another location.
2. `wfc doctor`, inside the project. Every line should say `OK`; fix what doesn't (often Docker not running) and run it again. Outside a project it starts with a database warning and reports `No wfc project here` on most lines; only its git and docker lines mean anything there.
3. Register an env, once there is a method to run in it. Give it a name no other project on this computer uses, such as `<project>-<env>` (see Known issues). Pick the form that matches what the user has:
   - a conda env on this machine: `wfc register-env <name> conda:<conda-env>`
   - a pixi project: `wfc register-env <name> pixi:<project>:<env>`, or `--backend pixi --from pixi.lock`
   - a uv project: `wfc register-env <name> --backend uv --from <project-dir>`. The folder needs a `uv.lock` and a `.python-version`; a new one is `uv init --bare --python 3.12 <folder>`, then inside it `uv python pin 3.12` and `uv add wfc-client` with the method's packages. Without `--python 3.12`, uv ties the project to whichever Python it finds first, and a newer one refuses the pin.
   - an image of their own: `wfc register-env <name> --backend byo --image docker://<ref>`

   Say how long it takes before starting: a small uv env takes about a minute; a conda env from Windows or macOS takes a few minutes when it is small (about 5 minutes) and longer when it is large, most of it solving the env again for Linux and building its image (in 0.8.0b2 and earlier, 30 to 60 minutes), so start it in the background. Use the env the user already has and never modify it; for a new env, recommend pixi (a `pixi.lock` that includes `linux-64` skips the solve) or, for Python-only work, uv. While it builds, follow `wfc register-env`'s own output: when it prints phase lines, a heartbeat line means it is still working. After an interrupted registration, run `wfc list-envs`; when its footer reports leftovers and `wfc prune-envs --help` works (not in 0.8.0b2), suggest `wfc prune-envs`, and pass `--yes` only after the user agrees. Never give the user raw Docker commands.

   A Python method needs `wfc-client` installed in the env; when the user's env lacks it, make a copy with `wfc-client` added and register the copy, as the **creating-a-wfc-method** skill describes. An R or bash method's env is registered with `--interpreter` set to the interpreter's path inside the container (for example `/opt/conda/bin/Rscript` or `/bin/bash`), since the default is Python.
4. `wfc register-module --name <module> --module-dir src/<module>`, with a one-line `module.yaml` (`description: ...`) in that folder. Agree the module's name with the user first: a registered module can't be removed.
5. Register the data a method will run on: `wfc register-sample --name <name> --source <file or folder>`. Trying a method needs a registered sample.
6. To bring a script in as a method, use the **creating-a-wfc-method** skill.

## Reading the docs

Read the documentation for the installed version. With network access, run `wfc --version`:

- a release such as `0.8.0` reads `https://workflow-canvas.readthedocs.io/en/v<version>/`
- a pre-release, with `a`, `b` or `rc` in the version (such as `0.8.0b1`), reads `https://workflow-canvas.readthedocs.io/en/latest/`

The install page is the exception: a user without wfc has no version, so always give `https://workflow-canvas.readthedocs.io/en/stable/install.html`.

Useful pages under that root: `tutorials/getting-started.html`, `how-to/build-your-own-pipeline.html` (the steps for your own analysis, each linking its page), `how-to/write-a-method.html`, `tutorials/drafting-a-method.html` (with `how-to/draft-from-the-command-line.html` and `how-to/draft-in-the-canvas.html`), `tutorials/authoring-a-method-script.html`, `tutorials/registering-an-environment.html`, `reference/command-line.html` (every command, one page per group), `reference/method-yaml-schema.html`, `reference/method-script-contract.html`, `how-to/canvas-hub.html`, `how-to/cli-hub.html` (which links every command-line task page, such as `how-to/cli-run.html`, `how-to/cli-runs.html` and `how-to/cli-export.html`), `how-to/sharing-a-project.html`. Page names can't be guessed: start from a hub page rather than trying a URL. Docs for 0.7 and earlier have the command reference at `reference/cli-reference.html`, and the canvas and command-line guides at `how-to/canvas.html` and `how-to/run-and-inspect-results.html`.

If a page is missing or there is no network, work from this skill and `wfc <command> --help`, which always matches the installed version. Shortly after a pre-release, `/en/latest/` can still show the previous release's pages; when a page disagrees with `--help` or with what the installed wfc prints, the installed wfc is right, and say so to the user.

## Known issues in 0.8.0

These are known wfc bugs in 0.8.0 and its post-release 0.8.0.post1, planned to be fixed in 0.8.1 unless the entry says otherwise. When `wfc --version` is 0.8.0 or 0.8.0.post1 (or a 0.8.0 pre-release), use the workaround and tell the user you are doing so; when it is 0.8.1 or later, try the plain way first.

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
- **Two projects on one computer that register an env under the same name lose each other's image** (`wfc list-envs` shows `missing`; `env '<name>': its image sha256:… is not on this machine and was never pushed to a registry`). Give every env a name of its own, such as `<project>-<env>`; `wfc demo`'s env and a tutorial's `first-pipeline` are the same name in every project. A project that lost its image registers the env again under a name of its own, points its methods' `env:` at it and registers them again; the old name would take the image from the other project. No release is planned for this one.
- **A step that reads several runs through references runs once per sample** when those runs were made for different samples: History shows two identical runs of the step, the second `⟳ CACHED`. Nothing is computed twice; tell the user they can ignore the extra run.
- **A step can't combine the outputs of a step that runs once per sample** (`… is sample-collapsed … a collapsed step cannot consume per-sample outputs of an upstream method step.`). Run the per-sample steps as one pipeline, then combine their runs in a second pipeline with a run-reference node for each run. Planned for 0.9.0.

## If wfc itself seems wrong

Check the known issues above first. If it matches one, it is a known bug: use its workaround, say so, and don't offer to file it.

Only wfc's own behaviour counts as a wfc bug: a wfc traceback or crash, a result that contradicts the docs or `--help` for the installed version, or an error that doesn't say what to do. An error from the user's script, Docker not running, or an env that isn't registered is setup: say so and point to `wfc doctor`. Something unintuitive that works as documented is a rough edge, not a bug.

Before calling it a bug, reproduce it once, check the docs for the installed version, and search the existing issues (`gh issue list --repo ddpoe/workflow-canvas --search "<words>"`, when `gh` is available).

Don't interrupt the task for it. If you work around a wfc bug, say that you are doing so; never work around one silently. At the end, add one short block:

> **I think I found a wfc bug:** `<command>` did `<what happened>`; I expected `<what the docs or --help say>`.

Then offer to draft an issue with the wfc version (`wfc --version`), the OS, the command, its output and what was expected, the body ending with "Reported by an agent using the wfc skill." File it only when the user says yes: `gh issue create --repo ddpoe/workflow-canvas`, or, without `gh`, a prefilled link `https://github.com/ddpoe/workflow-canvas/issues/new?title=<title>&body=<body>` with both parts URL-encoded.
