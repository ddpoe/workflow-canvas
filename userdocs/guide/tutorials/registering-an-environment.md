<!-- generated from pm_mvp::docs.consumer.tutorials.registering-an-environment @ ae639db80ee5; do not edit -->

# Tutorial: Registering an Environment

## Overview

Every method in a pipeline runs inside its own container environment. That isolation is what makes results reproducible: a method always sees exactly the dependencies you declared for it, never whatever happens to be installed on the machine that launched the run, and never the dependencies of a *different* method in the same pipeline. Two methods that need conflicting versions of the same library coexist happily because each runs in a separate container.

This tutorial walks the whole flow end to end:

1. **Declare and build** an environment with `wfc register-env`, choosing a backend that says where its package list comes from.
2. **Reference it** from a method by naming it in the method's `method.yaml` under the `env:` key.
3. **Develop against it** interactively with `wfc jupyter`, `wfc shell`, and `wfc exec`.

A few things to keep in mind before you start. Docker is a hard requirement: there is no host-Python execution path, so a method cannot run until you have built a container env for it and Docker is running. An environment is built once and reused; you only rebuild when its dependencies change. And your environment contains only *your* dependencies plus Python — Workflow Canvas itself is never installed into it.

If you have not set a project up yet, run `wfc init` first (see [[getting-started]]); it scaffolds a runnable project so you have somewhere to register environments and methods. Then come back here.

## Why environments exist

Reproducibility and isolation are the two problems an environment solves.

**Reproducibility.** When `wfc register-env` builds an image, it resolves and records the image's content digest. From then on the method runs against that exact, digest-pinned image — not a floating tag that silently changes when an upstream base image is republished. A run you do today and a run someone else does next month against the same env get bit-for-bit the same software stack.

**Isolation.** Each method names its own env, so a pipeline can mix a method that needs an old NumPy with one that needs a brand-new PyTorch without either stepping on the other. There is no shared, mutable "project Python" they all draw from.

Because the environment is the software half of what makes a step reproducible, it also feeds the cache: when Workflow Canvas decides whether a step can be skipped, the environment's fingerprint is one of the inputs to that decision. Change the env and dependent steps re-run. You can read more about that in [[caching-and-reproducibility]].

## Choosing a backend

`wfc register-env <name>` builds a container image and writes a manifest entry to `.wfc/envs.json`. The backend you choose tells the builder *where the package list comes from*; it does not change the fact that the result is always a container image.

| Backend | Where packages come from |
|---|---|
| `pixi` | A pixi project's locked environment (`pixi.lock` + `pixi.toml`). |
| `conda` | A conda environment's explicit package list. |
| `byo` | Bring your own pre-built image, referenced by digest with `--image docker://...@sha256:...`. No build happens — Workflow Canvas just records the reference. |

The simplest path is to capture from an environment you already have running locally:

```bash
# Capture from a live conda env named cell_pose
wfc register-env cell_pose conda:cell_pose

# Capture from a pixi project's env
wfc register-env analysis pixi:wcia:hello
```

When you pass a positional spec like `conda:cell_pose` or `pixi:wcia:hello`, the CLI resolves that live env, reads its package list (plus a `pip freeze` to catch any ad-hoc installs you layered on top), stages everything into the build context, and infers the backend from the prefix. The captured package list is stored on the manifest record, and you can read it back in the canvas **Envs** tab — expand an env to see its installed `name==version` packages, tagged by source (conda/pixi/pip).

If you would rather build from a checked-in lock file than from whatever is currently installed, use file-mode and name the backend explicitly:

```bash
# Build from a lock file under version control
wfc register-env analysis --backend pixi --from envs/analysis/pixi.lock

# Bring your own image, pinned by digest
wfc register-env vendor --backend byo --image docker://ghcr.io/org/img@sha256:...
```

The input modes are mutually exclusive: a positional spec, `--from`, and a bare `--backend` are three different sources, and combining them errors before Docker is ever invoked. Pass `--force` to overwrite an env that already exists under the same name. To preview the generated Dockerfile without running a build, add `--dry-run` — it writes the Dockerfile to `.wfc/build/<name>/Dockerfile` and exits without touching Docker.

The `pixi`, `conda`, and `byo` words name *build backends only*. They are not values you can put in a method's `env:` field — see the next section for what goes there.

For the complete flag table (`--image`, `--base-image`, and the rest) and the companion `wfc list-envs` / `wfc show-env` / `wfc delete-env` commands, see [[cli-reference]].

## Referencing an env from a method

Once an env is built, a method opts into it by naming it in `method.yaml`. There are exactly three valid forms for the `env:` value:

| `env:` value | Meaning |
|---|---|
| `<name>` | The env registered under this name in `.wfc/envs.json`. The everyday form. |
| `container:<name>` | Identical to the bare name; the `container:` prefix is accepted purely for readability. |
| `container:docker://<ref>@sha256:<hex>` | A per-method escape hatch that pins a specific image by digest, with no manifest lookup. Use this for a one-off bring-your-own image. |

```yaml
# method.yaml
inputs:
  images:
    type: directory
outputs:
  features:
    type: .csv
env: cell_pose
```

**A method with no `env:` field is an error.** There is no default environment and nothing is inherited — every method must explicitly name a built container env. The older runtime specs `inherit`, `pixi:<name>`, and `conda:<name>` are no longer accepted as `env:` values; if you write one, registration fails. (Those same words still name *build backends* for `wfc register-env`, which is a different thing — declaring how to build an image, not which image a method runs in.)

The env must exist *before* you register the method. Registration validates that the named env is present in `.wfc/envs.json` and holds a digest-pinned image record, and fails fast if it does not:

```bash
wfc register-env cell_pose conda:cell_pose      # build the env first
wfc register-method modules/segmentation/segment --module segmentation
```

One more invariant worth internalizing: your environment never contains Workflow Canvas. Whatever backend you build with, the image holds only your declared dependencies plus Python. Your method reaches the framework through environment variables and files the runner sets up (`WFC_RUN_DIR`, `WFC_INPUT_PATHS`, `WFC_PARAMS`, and friends), not through an import. If you want the `@wfc.method` decorator ergonomics, add the small pure-stdlib `wfc-client` package to your env's dependencies like any other library — even then, the full framework stays out of your environment. See [[authoring-a-method-script]] for both styles.

## The ephemeral-container model

Understanding how containers are used at run time is what makes day-to-day work feel cheap rather than slow.

Every step runs in a **fresh** container. The runner does the equivalent of `docker run --rm` for each step: a new container is started from your env's digest-pinned image, the step executes, and the container is thrown away. Nothing carries over from one step to the next, and nothing carries over between runs.

Three consequences fall out of that, and they are the practical things to remember:

- **In-session `pip install`s do not persist.** If you open a shell into the env and `pip install something`, that package lives only in that throwaway container. The next pipeline step starts clean from the image and will not see it. To make a dependency permanent, add it to the env's source and rebuild with `wfc register-env ... --force`.
- **Your scripts are bind-mounted, not baked into the image.** The project directory is mounted into the container at run time, so the container always sees the script files as they are on disk *right now*. Editing a method script is free — save the file and the next step picks it up. You do **not** rebuild the image to change code.
- **You rebuild only when dependencies change.** Because code is mounted and the image holds only dependencies, the image stays valid as long as your declared packages stay the same. Add, remove, or bump a dependency and you rebuild; touch only your own `.py` files and you do not.

This split — dependencies baked into the image, code mounted live — is the whole reason the loop is fast: the expensive thing (building the dependency stack) happens once, and the thing you do constantly (editing code) costs nothing.

## Requesting GPUs

If a method needs a GPU, set `gpus: true` in its `method.yaml`:

```yaml
# method.yaml
env: deep-learning
gpus: true
```

At dispatch time the runner injects `--gpus all` into the `docker run` invocation for that step, exposing the host's GPUs to the container. This requires the host to have working GPU support for Docker (the NVIDIA container runtime); the flag only hands the GPUs through — it does not install drivers. Methods that omit `gpus` (or set it to `false`) run without GPU access, which is the default.

## Working inside an environment

While you are developing a method it helps to get *inside* its environment interactively, with the exact image and the exact `/work` bind-mount that production runs use. Three commands give you that, each launching an ephemeral container of the env's image:

- **`wfc jupyter <env>`** — launches Jupyter Lab inside the container. The host URL with its access token is printed on startup; open it in a browser. Pass `--port` to pin the host port, otherwise the first free port in 8888–8999 is chosen for you.
- **`wfc shell <env>`** — drops you into an interactive shell inside the container (`bash` if available, otherwise `sh`). Good for poking around the filesystem or trying a command.
- **`wfc exec <env> <cmd...>`** — runs a single command in the container and returns. The output streams back, so it composes with pipes and redirects: `wfc exec myenv cat /work/notes.txt > notes.txt`.

All three honor the same ephemeral rule as pipeline steps: each invocation is a fresh container, and anything you install inside it (a `pip install`, a scratch file outside `/work`) vanishes when the session ends. Use them to *explore and test*; make changes permanent by editing your declared dependencies and rebuilding the env, or by editing your bind-mounted scripts on disk.

## Docker readiness and getting runnable

Because execution is container-only, Docker is not optional — a method cannot run at all without it. There is no host-Python fallback to dodge this with. If the Docker daemon is missing or stopped, the failure surfaces when you try to build or run: `wfc register-env` and `wfc run-step` exit with an actionable error rather than silently degrading.

`wfc init` is what gets a fresh project to a runnable state before you start building envs: it scaffolds the project layout, the local database, and configuration. Run it first in a new project (see [[getting-started]]). `wfc doctor` pre-flights the three things a project needs to run — git, the DVC output archive, and Docker readiness — and reports them in one place, so "why won't this run?" has a single door. Run it any time: it prints a health table and exits non-zero if anything is broken, which makes it equally useful at your terminal and as a CI gate. Build- and run-time commands still surface their own actionable errors when the Docker daemon is missing or stopped.

One note on durability worth knowing early: the run outputs that get archived are stored as content-addressed blobs, and the index that maps those blobs back to meaningful results lives in `.wfc/wfc.db`. That database is deliberately *not* tracked in git (it is mutable state, not source). So if you care about recovering archived outputs later, back up the `.wfc/` directory — the blobs alone are not interpretable without the index.

## Next steps

You now have the full environment loop: build an env with `wfc register-env`, point a method at it via `env:` in `method.yaml`, develop against it with `wfc jupyter` / `wfc shell` / `wfc exec`, and rebuild only when dependencies change.

Where to go next:

- **[[authoring-a-method-script]]** — write the method that runs in this environment, in either the `@wfc.method` decorator style or the plain env-var + file contract.
- **[[cli-reference]]** — the complete flag tables for `register-env`, `list-envs`, `show-env`, `delete-env`, and the dev-loop commands.
- **[[caching-and-reproducibility]]** — how the environment's fingerprint feeds the cache and decides when a step re-runs.
- **[[getting-started]]** — if you have not yet scaffolded a project with `wfc init`.
