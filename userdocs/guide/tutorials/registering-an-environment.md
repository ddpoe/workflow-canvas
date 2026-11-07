<!-- generated from pm_mvp::docs/consumer/tutorials/registering-an-environment @ 89c809f43234; do not edit -->

# Register an Environment

## Overview

Every method runs inside a container built from an environment (env) you register. The env holds your method's dependencies; each method names its own env, so two methods that need conflicting library versions can sit in the same pipeline.

This tutorial walks the flow end to end:

1. **Build** an env with `wfc register-env`.
2. **Reference it** from a method's `method.yaml` under `env:`.
3. **Work inside it** with `wfc jupyter`, `wfc shell` and `wfc exec`.

You need Docker installed and running.

If you have not set up a project yet, create one first (see [Your First Pipeline](getting-started.md)): `wfc init --dir wfc_project_root`, then `cd wfc_project_root`. Every command on this page runs from the project root.


## Why environments exist

An env gives each method a fixed, isolated software stack.

**Fixed.** `wfc register-env` records exactly which image it built, and every run of a method uses that image. A run today and a run next month against the same env use the same software.

**Isolated.** Each method names its own env, so one method can use an old NumPy and another a new PyTorch in the same pipeline.

wfc also checks the env when it decides whether a step can reuse an earlier result: a method registered against a rebuilt env runs again. See [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).


## Building an env

`wfc register-env <name>` builds a container image and registers it under `<name>`. The backend says where the package list comes from:

| Backend | Where packages come from |
|---|---|
| `conda` | A conda environment's explicit package list. |
| `pixi` | A pixi project's lock file (`pixi.lock`, plus `pixi.toml` when it sits beside the lock). |
| `uv` | A uv project directory, or its `uv.lock`. |
| `byo` | An image you already have ("bring your own"). Nothing is built; wfc records the image by its digest. |

There are three ways to give it a source.

**Capture an env you already have installed.** Name it with a typed spec; the backend follows from the prefix:

```bash
wfc register-env cell_pose conda:cell_pose
wfc register-env hello pixi:myproject:hello
```

The command is `wfc register-env <name> <spec>`: `<name>` is the name methods will use for the env, and `<spec>` is the installed env to capture (`conda:<env>` or `pixi:<project>:<env>`).

wfc reads the env's package list and its `pip freeze`, so packages you added with `pip install` are included.

Images run Linux. When you capture a conda env on Windows or macOS, wfc installs the Linux builds of the same package versions and prints a warning that they can differ from yours. If a package has no Linux build at that version, the command stops and names it.

**Build from a uv project.** Point `--from` at a uv project directory or its `uv.lock`:

```bash
wfc register-env analysis --backend uv --from envs/analysis
wfc register-env analysis --backend uv --from envs/analysis --group plots --extra gpu
```

wfc installs the project's locked dependencies without the `dev` group. Add `--group` or `--extra` (each repeatable) to install a named dependency group or extra; only the groups you name are installed. uv must be on your `PATH` and the project needs a `.python-version` file.

**Build from a lock file** under version control:

```bash
wfc register-env default --backend pixi --from envs/analysis/pixi.lock
wfc register-env analysis --backend conda --from envs/analysis/explicit-list.txt
```

**Use an existing image:**

```bash
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img:1.4
```

wfc downloads the image if you don't have it and records exactly which image it is.

When the project has an image registry, a server that stores container images for collaborators, `register-env` uploads the image there so a collaborator can download it. Pass `--no-push` to keep the image on this machine; `wfc push-env <name>` pushes it later. With a registry set, an env's name must use lower-case letters, digits and `.`, `_` or `-`. [Share a Project](../how-to/sharing-a-project.md) covers the registry setup.

On success the command prints the recorded image reference. Check your envs with `wfc list-envs` and `wfc show-env <name>`.

**Pixi env names.** For the pixi backend, the name you give `register-env` is the pixi environment wfc installs from the lock, so it must be an environment the lock defines. Most pixi projects have one, called `default`. If the name does not match, the command stops before building:

```text
ERROR: pixi.lock has no environment named 'analysis' — it has: default. ...
```

**Rebuilding.** Add `--force` to replace an existing env of the same name, for example after you change its dependencies. Each method version keeps running the image it was registered with, so earlier versions stay runnable. To run a method on the rebuilt image, register the method again with `wfc register-method`.

**Removing.** `wfc delete-env <name>` removes an env. It refuses while any method version uses the env, and lists those versions.

**Interpreter.** wfc runs method scripts with the env's Python; for a `byo` image, that is the first `python` on the image's `PATH`. Pass `--interpreter <path>` to use a different one.

The words `conda`, `pixi` and `byo` name build backends only. A method refers to an env by its name, as the next section shows. For every `register-env` flag and the `list-envs`, `show-env` and `delete-env` commands, see the [Environment and Dev-Loop Commands](../reference/cli-environments.md).

The canvas shows an env's installed packages: open the **Registry** tab, choose **Envs**, and expand an env. The list shows envs that registered methods use; conda, pixi and uv envs list their packages, and `byo` envs have no package list.

### R and bash environments

Methods can be R or bash scripts. Build the env as usual and set `--interpreter`, since the recorded default is Python:

| Language | Build | Interpreter |
|---|---|---|
| R | pixi project with conda-forge `r-base`, `r-jsonlite` and your packages, registered from its lock file | `--interpreter /opt/.pixi/envs/<name>/bin/Rscript` |
| R | conda env from conda-forge with `r-base`, `r-jsonlite` and your packages, captured with `conda:<env>` | `--interpreter /opt/conda/bin/Rscript` |
| R | `byo` with an image built from a versioned [rocker](https://rocker-project.org/) `r-ver` image, with `jsonlite` added | `--interpreter Rscript` |
| bash | any backend | `--interpreter /bin/bash` |

```bash
wfc register-env default --backend pixi --from r-env/pixi.lock \
  --interpreter /opt/.pixi/envs/default/bin/Rscript

wfc register-env r-analysis conda:r-analysis \
  --interpreter /opt/conda/bin/Rscript

wfc register-env r-analysis --backend byo \
  --image docker://local/r-analysis:1 --interpreter Rscript
```

[Write a Method in R](../how-to/write-an-r-method.md) builds each kind of env and runs an R method end to end.


## Referencing an env from a method

A method names its env in `method.yaml` with the `env:` key, using the name you registered:

```yaml
# src/segmentation/segment/method.yaml
inputs:
  images:
    type: directory
outputs:
  features:
    type: .csv
env: cell_pose
```

`env:` is required; there is no default env.

Build the env before you register the method. Registration checks that the env is registered; if it is not, registration stops and lists the envs you have. From the project root:

```bash
wfc register-env cell_pose conda:cell_pose
wfc register-method src/segmentation/segment --module segmentation
```

`wfc register-method <method-dir> --module <module>` takes the method's directory (here under `src/<module>/<method>/`) and the module it belongs to; [Register a Module](../how-to/register-a-module.md) covers registering the module first.

For a Python method that uses the `@wfc.method` decorator, add the `wfc-client` package to your env like any other library. See [Write a Method in Python](authoring-a-method-script.md) for the decorator and the [Method Script Contract](../reference/method-script-contract.md) for the variables.


## The ephemeral-container model

Every step runs in a fresh container started from the env's image and removed when the step ends. Nothing carries over between steps or runs.

- **Packages installed inside a container do not last.** A `pip install` in a shell session is gone when the container exits. To add a dependency, add it to the env's source and rebuild with `wfc register-env <name> ... --force`.
- **Your project directory is mounted into the container at `/work`.** Your scripts are not baked into the image, so changing code needs no rebuild: edit the script, then run `wfc register-method` again (see [Write a Method](../how-to/write-a-method.md)).
- **Rebuild only when dependencies change.** The image holds dependencies; your code stays on disk.

Each version records the image of its env. If Docker no longer has that image, wfc pulls it from the registry when it was pushed (see [Share a Project](../how-to/sharing-a-project.md)); an image that was never pushed, and is gone, leaves a run on that version refused until you register the method again. If a version's env is a container image you build yourself, give each rebuild its own image tag: when a rebuild reuses a tag, the older version's image is gone.


## Requesting GPUs

If a method needs a GPU, set `gpus: true` in its `method.yaml`:

```yaml
# method.yaml
env: deep-learning
gpus: true
```

Each step of that method then runs with the host's GPUs available. The host needs working GPU support for Docker, such as the NVIDIA container runtime. Methods without `gpus: true` run without GPU access.


## Working inside an environment

To work inside an env while you develop a method, start a container of its image with your project mounted at `/work`, the same way pipeline steps run:

- **`wfc jupyter <env>`** starts Jupyter Lab in the container. Open the URL with its token that Jupyter prints. The env must include `jupyterlab`.
- **`wfc shell <env>`** opens an interactive shell.
- **`wfc exec <env> <cmd...>`**, where `<env>` is the env's name and `<cmd...>` is the command and its arguments, runs one command and returns its output, so it works with pipes and redirects: `wfc exec myenv cat /work/notes.txt > notes.txt`.

Each command starts a fresh container. Anything you install, or write outside `/work`, is gone when it exits.


## Checking Docker

Methods run only in containers, so Docker must be installed and running. If it is not, `wfc register-env` and pipeline runs stop with an error that says so.

If an env's image has been deleted from Docker, wfc downloads it again from the registry. If it was never pushed, the run stops and the error names the command that rebuilds it. Push images to a registry with `wfc push-env`; see [Share a Project](../how-to/sharing-a-project.md).

`wfc doctor` checks that Docker and the rest of the project are ready; see [Check that everything is ready](getting-started.md#2-check-that-everything-is-ready).


## Next steps

You can now build an env with `wfc register-env`, point a method at it with `env:` in `method.yaml`, work inside it with `wfc jupyter`, `wfc shell` and `wfc exec`, and rebuild it with `--force` when dependencies change.

Where to go next:

- **[Write a Method in Python](authoring-a-method-script.md)**: write the method that runs in this env.
- **[Build Your Own Pipeline](../how-to/build-your-own-pipeline.md)**: register the method and the data it runs on.
- **[Environment and Dev-Loop Commands](../reference/cli-environments.md)**: every flag for `register-env`, `list-envs`, `show-env`, `delete-env` and the dev-loop commands.
- **[Caching & Reproducibility](../explanation/caching-and-reproducibility.md)**: how the env feeds each step's cache key.
