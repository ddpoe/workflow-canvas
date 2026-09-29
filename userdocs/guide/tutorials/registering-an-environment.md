<!-- generated from pm_mvp::docs.consumer.tutorials.registering-an-environment @ eceeb6e5726e; do not edit -->

# Tutorial: Registering an Environment

## Overview

Every method runs inside a container built from an environment (env) you register. The env holds your method's dependencies; each method names its own env, so two methods that need conflicting library versions can sit in the same pipeline.

This tutorial walks the flow end to end:

1. **Build** an env with `wfc register-env`.
2. **Reference it** from a method's `method.yaml` under `env:`.
3. **Work inside it** with `wfc jupyter`, `wfc shell` and `wfc exec`.

You need Docker installed and running: methods run only in containers. An env is built once and reused; you rebuild it only when its dependencies change. The env holds your dependencies and nothing from Workflow Canvas itself.

If you have not set up a project yet, create one first (see [Getting Started](getting-started.md)): `wfc init --dir wfc_project_root`, then `cd wfc_project_root`. Every command on this page runs from the project root.

## Why environments exist

An env gives each method a fixed, isolated software stack.

**Fixed.** `wfc register-env` records the built image by its content digest, and every run of a method uses that exact image. A run today and a run next month against the same env use the same software.

**Isolated.** Each method names its own env, so one method can use an old NumPy and another a new PyTorch in the same pipeline.

The env is also part of each step's cache key: rebuild an env and the steps that use it run again instead of reusing earlier results. See [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).

## Building an env

`wfc register-env <name>` builds a container image and records it in `.wfc/envs.json` under `<name>`. The backend says where the package list comes from:

| Backend | Where packages come from |
|---|---|
| `conda` | A conda environment's explicit package list. |
| `pixi` | A pixi project's lock file (`pixi.lock`, plus `pixi.toml` when it sits beside the lock). |
| `byo` | An image you already have ("bring your own"). Nothing is built; wfc records the image by its digest. |

There are three ways to give it a source.

**Capture an env you already have installed.** Name it with a typed spec; the backend follows from the prefix:

```bash
wfc register-env cell_pose conda:cell_pose
wfc register-env hello pixi:myproject:hello
```

The command is `wfc register-env <name> <spec>`: `<name>` is the name methods will use for the env, and `<spec>` is the installed env to capture (`conda:<env>` or `pixi:<project>:<env>`).

wfc reads the env's package list and its `pip freeze`, so packages you added with `pip install` are included. Conda envs are found under your conda installation (or the `[conda] root` in `wf-canvas.toml`). A pixi spec `pixi:<project>:<env>` is found under the `[pixi] root` directory in `wf-canvas.toml`, where pixi keeps each project's envs; for a pixi project whose envs live in its own folder, build from its lock file instead.

Images run Linux, and a conda capture copies the exact packages installed on your computer, so capture conda envs on Linux. On Windows or macOS, build from a pixi lock file whose project includes the `linux-64` platform, as in [Your First Pipeline](getting-started.md).

**Build from a lock file** under version control:

```bash
wfc register-env default --backend pixi --from envs/analysis/pixi.lock
wfc register-env analysis --backend conda --from envs/analysis/explicit-list.txt
```

**Use an existing image:**

```bash
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img:1.4
```

wfc pulls the image if it is not already local and records it by digest.

On success the command prints the recorded image reference. Check your envs with `wfc list-envs` and `wfc show-env <name>`.

**Pixi env names.** For the pixi backend, the name you give `register-env` is the pixi environment wfc installs from the lock, so it must be an environment the lock defines. Most pixi projects have one, called `default`. If the name does not match, the command stops before building:

```text
ERROR: pixi.lock has no environment named 'analysis' — it has: default. ...
```

**Rebuilding.** Add `--force` to replace an existing env of the same name, for example after you change its dependencies. Methods that use the env pick up the new image at their next run; you do not register them again.

**Previewing.** Add `--dry-run` to a `--from` command to write the generated Dockerfile to `.wfc/build/<name>/Dockerfile` without building.

**Interpreter.** wfc records which interpreter runs your method scripts. Conda and pixi envs record their env's Python; a `byo` env uses `python` from the image's `PATH`. Pass `--interpreter <path>` to set a different one, for example `--interpreter /opt/venv/bin/python`.

The words `conda`, `pixi` and `byo` name build backends only. A method refers to an env by its name, as the next section shows. For every `register-env` flag and the `list-envs`, `show-env` and `delete-env` commands, see the [CLI Reference](../reference/cli-reference.md).

The canvas shows an env's installed packages: open the **Registry** tab, choose **Envs**, and expand an env. The list shows envs that registered methods use; conda and pixi envs list their packages, and `byo` envs have no package list.

### R and bash environments

Methods can be R or bash scripts. Build the env as usual and set `--interpreter`, since the recorded default is Python:

| Language | Build | Interpreter |
|---|---|---|
| R | conda or pixi env with conda-forge `r-base` and your packages | `--interpreter /opt/conda/bin/Rscript` (conda), or `/opt/.pixi/envs/<name>/bin/Rscript` (pixi) |
| R | `byo` with a date-pinned [rocker](https://rocker-project.org/) image | `--interpreter Rscript` |
| bash | any backend | `--interpreter /bin/bash` |

```bash
wfc register-env r-analysis conda:r-analysis --interpreter /opt/conda/bin/Rscript

wfc register-env r-analysis --backend byo \
  --image docker://rocker/r-ver:4.3.2 --interpreter Rscript
```

If you use system R with `install.packages()`, move your package list to conda-forge: `dplyr` becomes `r-dplyr`, and Bioconductor packages are available as `bioconductor-*`. Create a conda env with them and capture it with the first command above.

Capture records what conda or pixi installed plus the `pip freeze`. Packages installed another way, such as `install.packages()` inside the live env, are not in the image. With a `byo` image, everything in the image is what runs.

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

`env:` is required, and its value is an env name only (letters, digits, `_` and `-`). There is no default env.

Build the env before you register the method. Registration checks that the name is in `.wfc/envs.json`; if it is not, registration stops and lists the envs you have. From the project root:

```bash
wfc register-env cell_pose conda:cell_pose
wfc register-method src/segmentation/segment --module segmentation
```

`wfc register-method <method-dir> --module <module>` takes the method's directory (here under `src/<module>/<method>/`) and the module it belongs to; [Registering Modules, Methods, and Samples](../how-to/registration.md) covers registering the module first.

Your method reaches Workflow Canvas through environment variables and files set up for each run (`WFC_RUN_DIR`, `WFC_INPUT_PATHS`, `WFC_PARAMS` and others), not through an import. To use the `@wfc.method` decorator, add the small `wfc-client` package to your env like any other library. See [Authoring a Method Script](authoring-a-method-script.md) for both styles.


## The ephemeral-container model

Every step runs in a fresh container started from the env's image and removed when the step ends. Nothing carries over between steps or runs.

- **Packages installed inside a container do not last.** A `pip install` in a shell session is gone when the container exits. To add a dependency, add it to the env's source and rebuild with `wfc register-env <name> ... --force`.
- **Your project directory is mounted into the container at `/work`.** Your scripts are not baked into the image, so changing code needs no rebuild: edit the script, then run `wfc register-method` again (see [Registering Modules, Methods, and Samples](../how-to/registration.md)).
- **Rebuild only when dependencies change.** The image holds dependencies; your code stays on disk.

## Requesting GPUs

If a method needs a GPU, set `gpus: true` in its `method.yaml`:

```yaml
# method.yaml
env: deep-learning
gpus: true
```

Each step of that method then runs with the host's GPUs available (`docker run --gpus all`). The host needs working GPU support for Docker, such as the NVIDIA container runtime. Methods without `gpus: true` run without GPU access.

## Working inside an environment

To work inside an env while you develop a method, start a container of its image with your project mounted at `/work`, the same way pipeline steps run:

- **`wfc jupyter <env>`** starts Jupyter Lab in the container. Open the URL with its token that Jupyter prints. Use `--port` to pick the host port; otherwise the first free port from 8888 to 8999 is used. The env must include `jupyterlab`.
- **`wfc shell <env>`** opens an interactive shell (`bash`, or `sh` if the image has no bash).
- **`wfc exec <env> <cmd...>`**, where `<env>` is the env's name and `<cmd...>` is the command and its arguments, runs one command and returns its output, so it works with pipes and redirects: `wfc exec myenv cat /work/notes.txt > notes.txt`.

Each command starts a fresh container. Anything you install, or write outside `/work`, is gone when it exits. To add a dependency, add it to the env's source and rebuild with `--force`. To change a method, edit its script in your project, then run `wfc register-method` again.

## Checking Docker

Methods run only in containers, so Docker must be installed and running. If it is not, `wfc register-env` and pipeline runs stop with an error that says so.

`wfc doctor` checks what a project needs to run — git, the DVC archive, Docker and your registered samples — and prints a table. It exits non-zero if anything fails, so you can also use it in CI.

## Next steps

You can now build an env with `wfc register-env`, point a method at it with `env:` in `method.yaml`, work inside it with `wfc jupyter`, `wfc shell` and `wfc exec`, and rebuild it with `--force` when dependencies change.

Where to go next:

- **[Authoring a Method Script](authoring-a-method-script.md)**: write the method that runs in this env.
- **[Registering Modules, Methods, and Samples](../how-to/registration.md)**: register the method and the data it runs on.
- **[CLI Reference](../reference/cli-reference.md)**: every flag for `register-env`, `list-envs`, `show-env`, `delete-env` and the dev-loop commands.
- **[Caching & Reproducibility](../explanation/caching-and-reproducibility.md)**: how the env feeds each step's cache key.
