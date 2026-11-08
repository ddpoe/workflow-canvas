<!-- generated from pm_mvp::docs/consumer/reference/cli-environments @ 23c0d5f6e1ba; do not edit -->

# Environment and Dev-Loop Commands

## Container Env Commands

Container environments are Docker images that methods run in. They are recorded in `.wfc/envs.json`, and a method names one with the `env:` key in its `method.yaml`.

### wfc register-env

`wfc register-env <name> [<spec>] [flags]`, where `<name>` is the name methods use for the env and `<spec>` (optional) is a local env to capture.

Build a Docker image for an environment, pin it by digest, and record it in `.wfc/envs.json`. Docker must be running (except with `--dry-run`). Choose one of four sources:

- **Capture a local env** (positional `spec`): wfc reads the package list of an env on your machine (a conda env's explicit list, or a pixi env's `pixi.lock` and `pixi.toml`, plus a `pip freeze`) and builds an image with the same packages, including anything you added with `pip install`. On Windows or macOS, a conda env is rebuilt for Linux with your installed versions; wfc prints a warning and keeps both package lists so you can compare them. `conda:<env>` looks under `[conda] root`, or the conda base found by `conda info --base`. `pixi:<name>` and `pixi:<proj>:<env>` look under `[pixi] root`, then in the project's own `.pixi/envs/`. See [wf-canvas.toml](wf-canvas-toml.md).
- **Build from a file** (`--backend pixi|conda --from PATH`): build from a checked-in `pixi.lock` (with the `pixi.toml` next to it, if there is one) or a conda explicit list.
- **Build from a uv project** (`--backend uv --from PATH`): `PATH` is a uv project directory or its `uv.lock`. wfc installs the locked dependencies without the `dev` group; `--group` and `--extra` (each repeatable) add a named dependency group or extra. uv must be on your `PATH` and the project needs a `.python-version` file.
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

Print a table of the registered envs with columns NAME, BACKEND, CONTAINER (the digest-pinned image), BUILT AT and REGISTRY. REGISTRY says where the env's image is, as the canvas Registry tab shows it: `pushed`, `not pushed`, `no registry`, `cannot push`, `missing`, `unknown` (Docker is not answering) or `own registry` (a `byo` image in its own registry), followed by `N older not pushed` or `N older missing` when older method versions need images. Takes no arguments.

### wfc show-env

`wfc show-env <name>`, where `<name>` is the env's name. Print one env's record: `name`, `backend`, `source`, `container`, `python` (the interpreter path), `env_fingerprint`, `source_fingerprint`, `built_from_lock` and `built_at`. It ends with the registry lines: `registry_status` (the REGISTRY word of `wfc list-envs`), `registry_base`, `pushed_ref` and `pushed_digest` for an image wfc pushed or `own_registry` for a `byo` registry image, and a `registry_note` for each problem, naming the command that fixes it.

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

`wfc push-env <name>` or `wfc push-env --all`, where `<name>` is the env's name. Push the env's images to the project's registry: its current image and every image a method version records, when they are on this machine and not yet pushed, or pushed to another registry. It shows what it will push and asks before it does; `--yes` answers for you. The first run sets the registry base: with a GitHub remote it proposes `ghcr.io/<owner>/<repo>`, otherwise pass `--registry <url>`. After you confirm, it writes `[registry] url` to `.wfc/wf-canvas.toml` and commits it. A later `--registry` naming another base switches the project to it after you confirm: the new base is committed with the push records, and `wfc push-env --all` then pushes every image there, pulling one that is not on this machine from the old registry by its digest. See [Share a Project](../how-to/sharing-a-project.md). An image whose env name no registry accepts is listed as `cannot push` with the reason and the fixes (register the image again under a valid name, or `wfc delete-env` when nothing needs it); the other images are still pushed. It exits with 1 when an image is listed as `cannot push`, including one that is no longer on this machine.

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

`wfc share`. Print what a collaborator would be missing, as a table headed `Sharing this project:` with one row per check: git remote, registry login, registry base, unpushed images, packages not connected to the repository, and method versions that cannot run. With no git remote, the packages row links each package's settings page, where you give a recipient access under **Manage access**, and names the `read:packages` login the recipient needs. Each row that warns names the command that fixes it. When the images go to GHCR, a last line links [See Your Images on GitHub](../how-to/see-your-images-on-github.md). It does not check the archive; `wfc doctor` does. It changes nothing and takes no arguments. See [Share a Project](../how-to/sharing-a-project.md).

### wfc delete-env

`wfc delete-env <name> [--yes]`, where `<name>` is the env's name. Remove an env from `.wfc/envs.json`, together with its `.wfc/captures/<name>/` folder, in one commit. It refuses while any version of any method uses the env, naming those versions, and `--yes` does not override that; register those methods against another env first. It asks for confirmation; `--yes` answers yes (`--force` does the same). With no terminal (a script or CI job) and no `--yes`, it deletes nothing and tells you to pass `--yes`. The Docker image itself is not deleted.

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

See [Register an Environment](../tutorials/registering-an-environment.md) for a walkthrough.

`wfc register-env` prints a line when each phase starts and ends, with the time it took (the capture and each call it makes, the build, the copy-out, the record, the push and the commit), and a line every half minute or so while a long phase runs without a terminal. It keeps a log of the registration at `.wfc/logs/register-env/<env>-<UTC time>.log`: those lines, every command wfc ran and Docker's full build output. The log's path is printed at the end and named in any error; send it with an issue. The folder is ignored by git. Ctrl-C during the capture, the build or the copy-out stops the build, prints one line and exits 130, and records nothing.

`wfc list-envs` ends with a line when an earlier run or crash left something running for this project (run containers of ended runs, env builds nothing is waiting for), giving the counts and naming `wfc prune-envs`; it prints none when it cannot reach Docker.

`wfc prune-envs [--dry-run] [--yes]` stops what an earlier run or crash left running for this project: run containers whose run has ended (or whose pipeline crashed, or whose `wfc try` is gone), containers that were created and never started, and env builds no wfc command is waiting for. It prints the list first and asks `Proceed?`; `--dry-run` prints it and stops nothing; with no terminal and no `--yes` it stops nothing. It never touches a run that is still running or another project's containers. It also counts the env images no method version uses and the build cache, and removes neither. Ctrl-C part way through stops with one line saying what is still there and exits 130.

## Dev-Loop Commands

These commands start a fresh container from a registered env's image, with the project mounted at `/work` as the working directory, the same layout a pipeline step runs in. Use them to try a method interactively in its real environment.

Each container is removed when the command ends. Anything you change inside it, including packages you `pip install`, does not carry into pipeline runs. To change an env's packages, update the env and run `wfc register-env` again with `--force`. To change a method, edit its script in your project, then run `wfc register-method` again.

### wfc jupyter

`wfc jupyter <env> [--port PORT]`, where `<env>` is the env's name. Start Jupyter Lab in the env's container. Open the `http://127.0.0.1:<port>/?token=...` URL that Jupyter prints.

With `--method DIR --module M` instead of an env name, the session runs in the env the method's `method.yaml` names and gets the inputs, parameters and run folder a step of that method gets, so `RunContext()` works inside it. The folder becomes the module's draft of that method. `wfc shell` takes the same flags.

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

`wfc shell <env>`, where `<env>` is the env's name. Open an interactive shell (`bash`, or `sh` if the image has no bash) in the env's container. `--method DIR --module M` sets it up for a method, as for `wfc jupyter`.

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

### wfc try

`wfc try METHOD_DIR --module M --sample S [--params JSON] [--parent-run-id ENTRY ...]`, where `METHOD_DIR` is the method folder (the one holding `method.yaml`), `--module` is the registered module it belongs to and `--sample` is the sample the step runs as. Both flags are required. Run a method folder, registered or not, in its env's container, on a sample or on other runs' outputs. Each `--parent-run-id` takes a run id or a draft string.

Nothing is registered or recorded: no run, cache entry or archive entry is added. The folder becomes the module's draft of that method, and its outputs stay in the draft's folder until the draft runs again. The command ends with a `wfc export` line for each output; name a destination folder in place of `<dir>`.

The registration checks run on the draft's current `method.yaml` before anything starts. A run that fails a check is refused with the check named, and the draft's previous run and outputs stay in place.

The sample feeds the one input no `--parent-run-id` feeds: the single required one, or, when none is required, the single optional one. If several inputs could take the sample, `wfc try` refuses and names them; feed all but one with `--parent-run-id`. `--json` prints the outcome as one object (`ok`, `module`, `method`, `sample`, `run` and an `outputs` list with each slot's export command); the lines saying what the command does go to stderr.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: try
   :nodescription:
   :noepilog:
   :nodefault:
```
