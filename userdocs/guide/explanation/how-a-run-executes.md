<!-- generated from pm_mvp::docs.consumer.explanation.how-a-run-executes @ 4ea0c0309173; do not edit -->

# How a Run Executes

## How a run executes

This page explains what actually happens between the moment you click **Run** (or call `wfc run-pipeline`) and the moment your outputs land in the cache. No single command or doc shows the whole picture, so this is the synthesis: the trip from a pipeline you drew on the canvas to a set of reproducible results.

There are three ideas to hold onto, and the rest of this page just unpacks them:

1. **Snakemake runs the show.** wfc compiles your pipeline into a Snakefile and hands it to Snakemake, which figures out the order, the parallelism, and which steps can be skipped.
2. **Each step is one self-contained `wfc run-step` unit.** A single step checks the cache, resolves its container environment, sets a handful of environment variables, runs your method as a subprocess, and archives whatever it produced.
3. **The boundary between wfc and your method is just environment variables and files.** wfc never imports your code and your method never imports wfc. That clean line is what lets a method be written in any language and rerun years later.

If you only remember one thing: a run is a tree of small, cacheable, language-agnostic steps, orchestrated by Snakemake and isolated in containers. The sections below walk each layer from the outside in.

For how to *write* the method that runs inside a step, see [Authoring a Method Script](../tutorials/authoring-a-method-script.md). For *why* a step re-runs (or doesn't), and how outputs are tracked across runs, see [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).

## Snakemake is the orchestrator

When you run a pipeline, wfc does not execute your steps itself. It **compiles your pipeline into a Snakefile** — a Snakemake workflow definition — and then invokes Snakemake to execute it. Snakemake is the single orchestration engine: it resolves the dependency graph, runs independent steps in parallel up to the core count you allow, and decides what needs to run versus what is already up to date.

What compilation produces:

- One rule per node in your pipeline, plus a top-level rule that pulls the whole graph together.
- Expansion of every sample × variant combination into concrete targets, so a pipeline drawn once over five samples becomes the right number of real steps.
- A small preamble that anchors the project root and per-pipeline logging, so every step lands on the correct project files no matter where Snakemake spawns it.

The generated Snakefile is an **intermediate artifact — do not hand-edit it.** It is regenerated from your pipeline definition on every run, so any manual change would be silently overwritten. Think of it the way you think of compiler output: the pipeline you drew (the JSON) is the source of truth, the Snakefile is the compiled form. If you want to change execution behavior, change the pipeline, not the Snakefile.

This "Snakemake as the sole orchestrator" decision (see ADR-001) is why wfc gets DAG resolution, parallelism, and dependency-aware skipping for free instead of maintaining a bespoke engine. Snakemake must be installed in the environment that launches the run.

At the end of the run, wfc reconciles the database against what was actually scheduled: steps that never ran because an upstream step failed are recorded as **cancelled** (and tagged with the run that caused it), rather than left as silent gaps. So a pipeline that fails partway leaves an honest, complete record of every step's fate.

## The per-step unit: wfc run-step

Every rule in the generated Snakefile boils down to a single shell command: `wfc run-step` for that node. That keeps the Snakefile tiny and pushes all the real execution logic into one well-defined unit. Understanding `wfc run-step` is understanding how *any* step executes, regardless of orchestrator.

A single `wfc run-step` invocation runs this lifecycle (see ADR-008 for the full protocol):

1. **Look up the step's config** — which method, which inputs, which params, from the frozen pipeline definition for this run.
2. **Cache check.** wfc computes the step's cache key and asks: has this exact work been done before? If yes, it restores the prior outputs into the workspace and records the step as cached — your method never runs. (What goes into that key, and why a step is or isn't a cache hit, is covered in [Caching & Reproducibility](../explanation/caching-and-reproducibility.md).)
3. **Resolve the container environment.** Each method declares an environment by name; wfc resolves it to a built container image. The method runs *inside* that image, isolated from wfc and from every other method's dependencies. Docker must be available — host execution is not supported.
4. **Set the `WFC_*` environment variables** describing this run: where to write outputs, the resolved input file paths, the params, the run/sample/node/pipeline identifiers.
5. **Run your method as a subprocess** inside its container. wfc captures stdout and stderr into the run logs. Your method does its work and exits.
6. **Archive the outputs.** On a clean exit, wfc collects the declared outputs, hashes them into the content-addressed cache, and records the run — including its lineage back to the upstream runs that fed it. A non-zero exit, or a missing required output, fails the step.

Because this whole lifecycle lives behind one command, the orchestrator only ever needs to say "run this step" — all the cache, isolation, capture, and archiving behavior is identical whether the step was launched by Snakemake, re-run by hand, or driven by a future executor. A real end-to-end example exercising this path over a large fan-out/fan-in pipeline is the L2 siRNA pipeline run.

## The wfc ↔ method boundary: env vars + files

Step 4 above is the heart of the design, so it deserves its own section. The entire interface between wfc and your method is **environment variables going in and files coming out.** Nothing else crosses the line. wfc does not import your code, and your method does not need to import wfc.

**Going in**, wfc sets these before launching your process:

| Variable | Meaning |
|---|---|
| `WFC_RUN_DIR` | Directory to write your declared outputs into. |
| `WFC_INPUT_PATHS` | JSON `{slot: [paths]}` — the resolved input files for each input slot. |
| `WFC_PARAMS` | JSON `{name: value}` — params from your `method.yaml` defaults merged with pipeline overrides. |
| `WFC_RUN_ID` / `WFC_SAMPLE` / `WFC_NODE_ID` / `WFC_PIPELINE_ID` / `WFC_VARIANT` | Identifiers for this specific run. |

**Coming back**, your method's contract is simple: read those variables, write each declared output into `WFC_RUN_DIR`, print anything you like to stdout/stderr (wfc captures it), and **exit 0 on success, non-zero on failure.** That exit code is how wfc knows whether the step worked.

Because this contract is *only* env vars and files, **a method can be written in any language** — Python, R, bash, a compiled binary — as long as it honors the protocol. It is also what makes a recorded run reproducible indefinitely: there is no client version baked into the contract, just plain variables and files.

**Two ways to write to that contract.** For Python authors, the shipped `wfc-client` package is the ergonomic layer: `import wfc_client as wfc`, decorate your function with `@wfc.method`, use the `ctx` object (`ctx.input(...)`, `ctx.params`, `ctx.run_dir`, `ctx.save_artifact(...)`, `ctx.log_metric(...)`), and call `wfc.run()`. `wfc-client` is pure-stdlib and only records metadata — it never copies or reads your data bytes — and on exit it writes a single `_wfc_results.json` manifest listing your outputs and metrics. Underneath, that is exactly the env-var + file contract. You can also write straight to the contract with zero dependencies (not even `wfc-client`) in any language; if you skip the manifest, wfc just scans `WFC_RUN_DIR` for the output filenames your `method.yaml` declares.

Either way, the floor is the same: the `WFC_*` variables and your output files. The `wfc-client` decorator is convenience sitting on top of a contract that never changes. The authoring side of this — the decorator surface and the raw contract — is covered end-to-end in [Authoring a Method Script](../tutorials/authoring-a-method-script.md).

## Where to go next

You now have the end-to-end picture: Snakemake compiles and orchestrates, each `wfc run-step` is a self-contained cache-check → isolate → run → archive unit, and the wfc↔method boundary is nothing but environment variables and files.

From here:

- **Write the code that runs inside a step** — [Authoring a Method Script](../tutorials/authoring-a-method-script.md) covers the `wfc-client` decorator and the raw env-var + file contract in depth.
- **Understand why a step re-runs or is skipped** — [Caching & Reproducibility](../explanation/caching-and-reproducibility.md) explains the cache key and how lineage links a run back to its inputs.
- **Inspect a run after it finishes** — [Run & Inspect Results](../how-to/run-and-inspect-results.md) walks through finding a run's outputs, status, and history (use the *Open pipeline in Canvas* action to load it visually).
- **See the whole flow on a worked example first** — [Getting Started](../tutorials/getting-started.md) runs a real pipeline end to end.
