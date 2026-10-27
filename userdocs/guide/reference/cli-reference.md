<!-- generated from pm_mvp::docs/consumer/reference/cli-reference @ 1a3d062c50bc; do not edit -->

# CLI Reference

## Overview

Every command is run as `wfc <command>` (or `python -m wfc <command>`). `wfc <command> --help` prints the same flags listed here.

Commands find the project by walking up from the current directory to the nearest folder that contains `.wfc/wf-canvas.toml`, so you can run them from any subdirectory of a project. Set `WFC_PROJECT_ROOT` to name the project explicitly (see [Environment Variables](#environment-variables)).

`wfc --help` also lists a few commands this page does not cover, such as `run-step` and `pipeline-summary`. Generated pipelines call those; you do not run them by hand.

Exit codes: `0` means success, `1` means the command refused or failed and printed why, and `2` means the arguments were invalid.

## Project Commands

### wfc init

Make a directory into a wfc project, then run the other commands from inside it:

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
```

`wfc init`:

1. Creates `.wfc/`, where wfc keeps everything it manages. It does not create a place for your own code; the suggested place is `src/<module>/<method>/` (see [Project Anatomy](../explanation/project-anatomy.md)).
2. Writes `.wfc/wf-canvas.toml` with a `[dvc]` section naming the output archive (see [wf-canvas.toml](wf-canvas-toml.md)), and creates the project database `.wfc/wfc.db`.
3. Adds wfc's entries to `.gitignore` and sets up DVC for the archive.
4. Runs `git init` if the directory is not a repository yet, and commits the files it wrote. On Windows it also sets `core.longpaths` in the new repository, so git works in a deeply nested project folder. If git has no global identity, the commit uses `wfc <wfc@wfc>`; change it with `git config user.name` / `user.email`. The repository is local only; wfc never pushes.
5. Prints a health table (the same checks as `wfc doctor`).

The archive location is where copies of your run outputs are kept. `wfc init` asks for it, with `~/.wfc/archives/<project>` as the default. Give a directory path, or a DVC remote URL such as `s3://bucket/path` (the matching DVC plugin must be installed, for example `pip install 'dvc[s3]'`).

In an existing project, `wfc init` lists what it would add and asks once before changing anything (in a script, pass `--yes`). When nothing is missing it says the project is already current and changes nothing. It never overwrites a config key you already set.

A missing git or Docker does not stop `wfc init`: the files are still written, and the health table shows what to install. Install it, re-run `wfc init` to fill in what is missing, then run `wfc doctor`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: init
   :nodescription:
   :noepilog:
   :nodefault:
```

The `.wfc/wfc.db` database indexes everything in the archive and is not tracked in git. Back up the `.wfc/` directory to keep archived outputs recoverable.

### wfc doctor

Check whether the project is ready to run. `wfc doctor` prints a health table with one row per check:

- **git**: git is installed, the project is a repository with a commit, and the working tree is clean.
- **dvc**: an archive is configured, set up in DVC, and reachable.
- **docker**: `docker` is installed and the daemon is running.
- **samples**: every registered sample's content is still in the local cache or the archive.

Each check reports `ok`, `warn` or `fail`, with a fix hint under any row that is not `ok`. `wfc doctor` exits 1 if any check fails (a `warn` does not), so it works as a pre-run check in scripts and CI. It takes no arguments.

A second table, **Env images**, follows the first. Its rows only warn, never fail:

- **registry**: with a registry set, the registry answers and every env image on this machine is pushed. It also warns about an image that `wfc push-env` cannot push because its env name is one no registry accepts.
- **versions**: every method version whose image is neither on this machine nor in the registry, with the commands that make it runnable again.
- **demo**: appears only for a demo made by an older wfc; run `wfc demo --force`.

### wfc demo

Add a complete, runnable demo pipeline to an initialised project and open it in the Canvas. `wfc demo` builds a small container environment, registers a module with five methods and three samples, writes `demo-pipeline.json`, and opens the Canvas with that pipeline loaded. Press Run to execute it.

Run `wfc init` first; `wfc demo` also needs Docker running. If a check fails, it exits 1 and changes nothing. The demo's environment is `wfc-demo-env`. `wfc demo` never pushes its image, even when the project has a registry; the output names `wfc push-env wfc-demo-env`. If a demo is already present, pass `--force` to remove it, its runs included, and build it again. A demo made by an older wfc is refused until you run `wfc demo --force`, and `wfc doctor` warns about it. The demo records its version in `.wfc/demo.json`.

`wfc demo --remove` deletes exactly what the demo added (its module, methods, samples, environment, runs, files and pipeline) and leaves everything you registered yourself. It lists what it will remove and asks first unless you pass `--yes`. Demo entries use the `__demo__` name prefix, and the environment is `wfc-demo-env`; you cannot use either for your own names.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: demo
   :nodescription:
   :noepilog:
   :nodefault:
```

For a walkthrough see [Exploring the Demo](../tutorials/wfc-demo.md).

### wfc canvas

Start the Canvas web UI for the current project and print its address.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: canvas
   :nodescription:
   :noepilog:
   :nodefault:
```

For a first-project walkthrough, see [Getting Started](../tutorials/getting-started.md).

## Registration Commands

The examples use the suggested layout: your code under `src/<module>/<method>/`, with each module's `module.yaml` in `src/<module>/`, and commands run from the project root.

### wfc register-module

Create a module, or update an existing one, with its output contracts. Contracts come from `--contracts` when given; otherwise from `module.yaml` in `--module-dir`. Without `--module-dir`, wfc looks only in `modules/<name>/`, so pass `--module-dir` for a module under `src/`. A `module.yaml` inside the repository is committed to git.

```bash
wfc register-module --name my_analysis --module-dir src/my_analysis
```

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-module
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-method

`wfc register-method <method-dir> --module <module>`, where `<method-dir>` is the method's directory and `<module>` is the registered module it joins.

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

Register a method from its directory: find the script, scan a Python script for its parameters, read `method.yaml`, check it against the module's contracts, copy the method into `.wfc/registry/methods/<module>/<name>/` (wfc's registered copy), and commit that copy to git. Your source directory is not committed. Other files in the source directory are not copied: the command prints a "Not registered" list naming them, with the advice to register data files as samples and wire them as inputs. Before it writes anything, the command prints whether the method is new (registers it as v1), changed since its newest version (adds the next version, naming what changed) or the same as it (no version is added). When a version would be added it asks `Proceed? [y/N]`; any answer but `y` prints `Nothing was registered.` and exits with an error. `--yes` skips the question, and with no terminal and no `--yes` a new version is refused. Earlier versions stay registered and runnable. After you edit a method's script or `method.yaml`, run `wfc register-method` again. A method name can be used in more than one module.

The script is the one named by `--script`, else by the `script:` key in `method.yaml`, else the single `<name>.py`, `.R`, `.r` or `.sh` file in the directory. If the script cannot be found or the check fails, it prints an `ERROR:` line and registers nothing.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-method
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc register-sample

Register a data file or directory as a sample. wfc hashes the content, copies it into the project's DVC cache (the source stays where it is) and records it; the sample is pushed to the archive when one is reachable. Pipelines restore it into `.wfc/workspace/samples/<name>/` when a run needs it. A sample name can be registered once.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-sample
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc restore-sample

Copy a registered sample from the cache into `.wfc/workspace/samples/<name>/`, pulling it from the archive if it is not in the local cache. A file that is already present with the right content is left alone; a changed one is replaced. Pipelines run this for you before a step reads a sample.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: restore-sample
   :nodescription:
   :noepilog:
   :nodefault:
```

See [Registration](../how-to/registration.md) for walkthroughs of all four.


## Container Env Commands

Container environments are Docker images that methods run in. They are recorded in `.wfc/envs.json`, and a method names one with the `env:` key in its `method.yaml`.

### wfc register-env

`wfc register-env <name> [<spec>] [flags]`, where `<name>` is the name methods use for the env and `<spec>` (optional) is a local env to capture.

Build a Docker image for an environment, pin it by digest, and record it in `.wfc/envs.json`. Docker must be running (except with `--dry-run`). Choose one of four sources:

- **Capture a local env** (positional `spec`): wfc reads the package list of an env on your machine (a conda env's explicit list, or a pixi env's `pixi.lock` and `pixi.toml`, plus a `pip freeze`) and builds an image with the same packages, including anything you added with `pip install`. On Windows or macOS, a conda env is solved for `linux-64` with your installed versions pinned; wfc prints a warning and keeps both package lists under `.wfc/captures/<name>/<fingerprint>/` (what the image installed in `packages.lock`, what your env had under `host/`). `conda:<env>` looks under `[conda] root`, or the conda base found by `conda info --base`. `pixi:<name>` and `pixi:<proj>:<env>` look under `[pixi] root`, then in the project's own `.pixi/envs/`. See [wf-canvas.toml](wf-canvas-toml.md).
- **Build from a file** (`--backend pixi|conda --from PATH`): build from a checked-in `pixi.lock` (with the `pixi.toml` next to it, if there is one) or a conda explicit list.
- **Build from a uv project** (`--backend uv --from PATH`): `PATH` is a uv project directory or its `uv.lock`. wfc installs the locked dependencies without the `dev` group; `--group` and `--extra` (each repeatable) add a named dependency group or extra. uv must be on your `PATH` and the project needs a `.python-version` file. The env runs `/opt/venv/bin/python`.
- **Use an existing image** (`--backend byo --image docker://...`): wfc pulls the image and records its digest without building anything. If older method versions must stay runnable, give each rebuild of the image its own tag; a tag you push over leaves the older versions without their image, and a run on one is refused.

With `--force`, `register-env` replaces an env that is already registered, then lists the methods whose newest version uses it and tells you to register them again with `wfc register-method`.

When the project has a registry base (`[registry] url` in [wf-canvas.toml](wf-canvas-toml.md)), `register-env` pushes the image before it records the env. A failed push records nothing. Pass `--no-push` to keep the image on this machine; `wfc push-env` pushes it later. The image is pushed under the env's name, which a registry accepts only as lower-case letters and digits joined by `.`, `_`, `__` or `-`, starting and ending with a letter or digit. With a registry set, `register-env` refuses any other name before it builds anything and says how to fix it: register the image under a valid name, or pass `--no-push`. See [Share a Project](../how-to/sharing-a-project.md).

The image does not need wfc installed. wfc runs the method script directly with the env's interpreter (see `--interpreter`).

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: register-env
   :nodescription:
   :noepilog:
   :nodefault:
```

**Examples:**

```bash
# Capture a local conda env named cell_pose
wfc register-env cell_pose conda:cell_pose

# Capture env "hello" of the pixi project "wcia"
wfc register-env analysis pixi:wcia:hello

# Build from a checked-in lock file
wfc register-env analysis --backend pixi --from envs/analysis/pixi.lock

# Build from a uv project, with a dependency group and an extra
wfc register-env analysis --backend uv --from envs/analysis --group plots --extra gpu

# Use a pre-built image, with an explicit interpreter
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img@sha256:... --interpreter /usr/bin/python3
```

To see what went into an image, open the Canvas **Registry → Envs** tab and expand the env: its **Packages** panel lists each `name==version` with its source (conda, pixi or pip). A `byo` image has no package list.

wfc does not rewrite a capture folder. If its files were changed outside wfc, `register-env` stops and says so; register the env again under a new name, set `env: <new-name>` in the method's `method.yaml`, and run `wfc register-method`.

### wfc list-envs

Print a table of the registered envs with columns NAME, BACKEND, CONTAINER (the digest-pinned image), BUILT AT and REGISTRY. REGISTRY says where the env's image is, in the words of the canvas Registry chip: `pushed`, `not pushed`, `no registry`, `cannot push`, `missing`, `unknown` (Docker is not answering) or `own registry` (a `byo` image in its own registry), followed by `N older not pushed` or `N older missing` when older method versions need images. It never asks the registry. Takes no arguments.

### wfc show-env

`wfc show-env <name>`, where `<name>` is the env's name. Print one env's record: `name`, `backend`, `source`, `container`, `python` (the interpreter path), `env_fingerprint`, `source_fingerprint`, `built_from_lock` and `built_at`. It ends with the registry lines: `registry_status` (the REGISTRY word of `wfc list-envs`), `registry_base`, `pushed_ref` and `pushed_digest` for an image wfc pushed or `own_registry` for a `byo` registry image, and a `registry_note` for each line the canvas chip's tooltip shows, which names the command that fixes it.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: show-env
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc push-env

`wfc push-env <name>` or `wfc push-env --all`, where `<name>` is the env's name. Push the env's images to the project's registry: its current image and every image a method version records, when they are on this machine and not yet pushed. It shows what it will push and asks before it does; `--yes` answers for you. The first run sets the registry base: with a GitHub remote it proposes `ghcr.io/<owner>/<repo>`, otherwise pass `--registry <url>`. After you confirm, it writes `[registry] url` to `.wfc/wf-canvas.toml` and commits it. The base is set once: a later `--registry` naming another base is refused. An image whose env name no registry accepts is listed as `cannot push` with the reason and the fixes (register the image again under a valid name, or `wfc delete-env` when nothing needs it); the other images are still pushed. It exits with 1 when an image is listed as `cannot push`, including one that is no longer on this machine.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: push-env
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc share

`wfc share`. Print what a collaborator would be missing, as a table headed `Sharing this project:` with one row per check: git remote, registry login, registry base, unpushed images, packages not connected to the repository, and method versions that cannot run. Each row that warns names the command that fixes it; when `gh` is logged in without a packages scope, the packages row names the `gh auth refresh --hostname github.com --scopes write:packages` command that adds it. When the images go to GHCR, a last line links [See Your Images on GitHub](../how-to/see-your-images-on-github.md). It changes nothing and takes no arguments. See [Share a Project](../how-to/sharing-a-project.md).

### wfc delete-env

`wfc delete-env <name> [--force]`, where `<name>` is the env's name. Remove an env from `.wfc/envs.json`, together with its `.wfc/captures/<name>/` folder, in one commit. It refuses while any version of any method uses the env, naming those versions, and `--force` does not override that; register those methods against another env first. It asks for confirmation unless you pass `--force`. The Docker image itself is not deleted.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: delete-env
   :nodescription:
   :noepilog:
   :nodefault:
```

See [Registering an Environment](../tutorials/registering-an-environment.md) for a walkthrough.

## Dev-Loop Commands

These commands start a fresh container from a registered env's image, with the project mounted at `/work` as the working directory, the same layout a pipeline step runs in. Use them to try a method interactively in its real environment.

Each container is removed when the command ends. Anything you change inside it, including packages you `pip install`, does not carry into pipeline runs. To change an env's packages, update the env and run `wfc register-env` again with `--force`. To change a method, edit its script in your project, then run `wfc register-method` again.

### wfc jupyter

`wfc jupyter <env> [--port PORT]`, where `<env>` is the env's name. Start Jupyter Lab in the env's container. Open the `http://127.0.0.1:<port>/?token=...` URL that Jupyter prints.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: jupyter
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc shell

`wfc shell <env>`, where `<env>` is the env's name. Open an interactive shell (`bash`, or `sh` if the image has no bash) in the env's container.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: shell
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc exec

`wfc exec <env> <cmd...>`, where `<env>` is the env's name and `<cmd...>` is the command to run and its arguments. Everything after the env name is passed through as the command. Output can be piped or redirected, for example `wfc exec myenv cat file.txt > out.txt`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: exec
   :nodescription:
   :noepilog:
   :nodefault:
```


## Pipeline Commands

### wfc run-pipeline

Run a pipeline file from the command line. This is the same run the Canvas Run button starts. wfc generates a Snakefile from the pipeline, runs it with Snakemake, and then archives the outputs unless you pass `--no-archive`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: run-pipeline
   :nodescription:
   :noepilog:
   :nodefault:
```

When every step succeeds, the command exits 0. When a step fails, it prints the pipeline summary and then one `ERROR: <message>` line naming the failure, and exits 1. If the pipeline cannot start (for example, a sample it reads cannot be found, or Docker is not running), it stops with an error that says why and exits 1 without running anything.

### wfc cancel

Stop a running pipeline by its ID, whether it was started from the Canvas or with `wfc run-pipeline`. It lists the pipeline and its running steps and asks before stopping it; `--yes` skips the question. It refuses an ID wfc has no record of.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cancel
   :nodescription:
   :noepilog:
   :nodefault:
```

## Cache and Export Commands

### wfc cache archive

Archive run outputs that are not archived yet: hash each file, copy it into the DVC cache, and record it. Use it after `wfc run-pipeline --no-archive`. It prints progress per file and saves each one as it finishes, so after an interruption, running it again archives only what is left.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache archive
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cache prune

Free disk space. By default, `wfc cache prune` removes run directories under `.wfc/runs/` that no recorded output needs. It lists what it will delete and asks before deleting.

Before deleting anything it checks that the archive is reachable, and stops if it is not. Runs with outputs that are not archived yet are skipped with a warning; run `wfc cache archive` first. With `--include-local`, entries that have not been pushed to the archive are kept, whether or not an archive is configured and even with `--force`; prune counts them and names `wfc cache push`. Entries the archive already holds are removed, and are pulled back when something reads them.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache prune
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cache push

Push every output, sample and environment or pipeline-document blob that is not on the archive yet, now. Pushing otherwise happens only while a pipeline runs, so use this after registering a sample or an environment between runs, after an archive location is configured, or after a push failed. It needs a configured archive location and stops, changing nothing, when there is none. It states how many entries it will push, retries the entries whose earlier attempts ran out, lists every entry it could not push with the reason, and exits 1 if any failed. It asks no confirmation: it only copies bytes already in the cache and removes nothing.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache push
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc verify

Check that what wfc holds is what it recorded. `wfc verify` re-hashes every restored sample copy, cache entry, checkout and stored blob, and each output not yet archived, and lists each one that changed, grouped by kind. It exits 1 when anything is wrong and 0 when everything matches, and `--json` prints the report as JSON for scripts.

`wfc verify --repair` puts back what wfc can restore exactly: a changed sample copy, a changed checkout, and a changed cache entry that was pushed (it is fetched again). It lists the rest with what to do: a changed output (run its step again), a changed cache entry that was never pushed, a lost cache entry, and a checkout it cannot rebuild. It still exits 1 while any of those remain.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: verify
   :nodescription:
   :noepilog:
   :nodefault:
```

Before a step reads a sample, an output or a cache entry, wfc already checks it against what it recorded and repairs or refuses a changed one; see the `[integrity]` setting in [Reference: wf-canvas.toml](wf-canvas-toml.md).

### wfc export

`wfc export --run <run-id> --slot <slot> --dest <dest>`, where `--run` is the run to export from, `--slot` is the output slot, and `--dest` is where to write the copy. For example, `wfc export --run 412 --slot masks --dest results/masks.png`. Leave out `--slot` to list the run's outputs.

Copy a run's output out of the cache into a file you own, or print where it is (for example, `p=$(wfc export --run 412 --slot masks --path)`). Cache files are read-only (see [Storage & Provenance](../explanation/storage-and-provenance.md)); the exported copy is writable. For a run that reused a cached result, the original run's output is exported.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: export
   :nodescription:
   :noepilog:
   :nodefault:
```

If any requested output cannot be exported, nothing is written and the command exits 1 with the reason.

### wfc runs

List every run in the project, one line each: run ID, pipeline, node, method, sample and status. The METHOD column shows the method and version the run ran, such as `__demo__.__demo__plot v1`. Use it to find the run ID that `wfc export` and `wfc export-run` take.

`--pipeline ID` lists only that pipeline's runs. `--method MODULE.METHOD` lists only the runs of that method, named with its module (`__demo__.__demo__plot`). The two can be combined.

```bash
wfc runs --method __demo__.__demo__plot
```

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: runs
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc export-run

Write runs, and every run above them, as a zip that anyone with Python 3.11 and Docker can rerun with `python rerun.py`, without wfc. The command prints the plan first. If anything stops the bundle from being complete, it lists every reason and writes nothing. A step whose image was never pushed to a registry is asked about, unless `--with-image` is given; `--yes` answers yes. For the steps to follow and what `DIFFERENT` means, see [Share runs as a bundle anyone can rerun](../how-to/run-and-inspect-results.md#share-runs-as-a-bundle-anyone-can-rerun).

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: export-run
   :nodescription:
   :noepilog:
   :nodefault:
```

## Environment Variables

None of these is required; each overrides a default.

| Variable | Effect |
|---|---|
| `WFC_PROJECT_ROOT` | The project directory to use instead of walking up from the current directory. It must contain `.wfc/wf-canvas.toml`. |
| `DATABASE_URL` | The database to read and write instead of the project's `.wfc/wfc.db`. `wfc demo` refuses to run when it names another project's database. |
| `WFC_GIT_COMMIT_TIMEOUT` | Seconds a `git commit` made by wfc may take before it is stopped (default `300`). Raise it if your git hooks are slow. Must be a positive number. |
