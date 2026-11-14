<!-- generated from pm_mvp::docs/consumer/reference/known-issues @ 3a5c5c358604; do not edit -->

# Known Issues

## Reference: Known Issues

These are known bugs in wfc 0.8.0, its post-release 0.8.0.post1 and its pre-releases, each with a way around it. Each is planned to be fixed in 0.8.1 unless its entry says otherwise.

:::{admonition} An AI agent can do this with you
:class: agent-tip

An agent with the wfc plugin knows these issues. When your wfc version has one, it uses the workaround and tells you it is doing so; see [Work with an AI Agent](../tutorials/work-with-an-ai-agent.md).
:::

## A step that reads several runs through references runs once per sample

When a node reads earlier runs through run references, and those runs were made for different samples, the node runs once for each of those samples, each time over all the referenced runs. History shows two identical runs of the step; the second is `⟳ CACHED`, so nothing is computed or stored twice.

You can ignore the extra run.

## A step can't combine the outputs of a step that runs once per sample

You see `Method node '<node>' (method=<method>) is sample-collapsed … a collapsed step cannot consume per-sample outputs of an upstream method step.` when a node fed by a fan-in input selector is also wired to a method that runs once per sample. The pipeline is refused before any step runs. Wired to the per-sample method alone, the node runs once per sample instead, so nothing is combined.

Run the per-sample steps as one pipeline, then combine their runs in a second pipeline with a run-reference node for each run, as step 8 of [Convert an Analysis into a Pipeline](../tutorials/convert-an-analysis.md#convert-it-by-hand) does. This one is planned for 0.9.0.

## wfc check-pipeline refuses a node with two inputs

You see `Input slot '<slot>' on node '<node>' has 2 incoming edges; only one edge per slot is supported.` when the links name the target's slot with `target_slot`, the key the [Pipeline Document](pipeline-document.md) describes, although `wfc run-pipeline` runs the pipeline.

Name each link's slots with `sourceHandle` and `targetHandle` instead, the keys the canvas writes; wfc reads them the same way.

## A draft can't find a helper kept outside its method folder

A draft fails with `ModuleNotFoundError` (or a missing file) for a helper its `method.yaml` lists outside the method folder, such as `helpers: [../shared/x.py]`. The registered method finds it.

While the method is a draft, keep a copy of the helper in the method folder ([method.yaml Schema](method-yaml-schema.md)).

## A draft node with a relative folder is refused

You see `the node runs the draft from <folder>, but <method>'s draft is from <absolute folder>. Pick the method again in the Builder…` for a pipeline node with `"version": "draft"` and a relative `"folder"`.

Leave `"folder"` out: the node then runs the method's current draft.

## A node left on its draft runs the draft without a warning

A node with `"version": "draft"` keeps running the draft after the method is registered, and nothing says so.

Before a final run, search the pipeline file for `"version": "draft"` and remove each one.

## After a wfc try is killed, the draft is refused as still in progress

When a `wfc try` is killed (a closed terminal, a crash), the next try or run of that draft is refused: `<module>.<method>'s draft run is still in progress in pipeline <name>: wait for it to end, or cancel it, then run again.` Cancelling does not help.

`wfc delete-draft <module>.<method> --outputs-only` removes that stuck draft run and its results and keeps the draft; it shows what it deletes and asks first.

## A pipeline that runs drafts reports Total runs: 0

When a pipeline runs drafts, the summary `wfc run-pipeline` prints says `Total runs: 0`. The steps did run, as draft runs: `wfc runs` and the canvas History show them.

## wfc run-pipeline with uncommitted changes starts, then fails its steps

Each step fails with `Working tree has uncommitted changes to tracked files — commit before running.` and is left as a failed or cancelled run in History.

Commit first; `wfc doctor` says whether you have uncommitted changes. Running from the canvas is not affected: it refuses before anything starts.

## wfc show-run lists a path that does not exist

`wfc show-run` gives an archived output's location as `local`, at a path that does not exist, and for a cached run it gives log paths that do not exist.

`wfc export --run <id> --slot <slot> --path` gives the real file. For a cached run, read the logs of the run it reused.

## Registering a cloned conda env from Windows or macOS fails

Building the env's image stops with `critical libmamba Bad expected access` when the env was made with `conda create --clone`.

Make the copy from the packages you installed in it instead: `conda env export -n <env> --from-history` lists them and `conda list -n <env>` their versions; create the new env with those versions.

## wfc register-env --dry-run refuses an env you already have

`wfc register-env <name> conda:<env> --dry-run` is refused. Register the env instead: a small env takes a few minutes, and `wfc register-env` shows each step as it goes and keeps a log under `.wfc/logs/register-env/`.

## Two projects that register an environment under the same name lose each other's image

`wfc list-envs` shows `missing` for an environment, or a command stops with `env '<name>': its image sha256:… is not on this machine and was never pushed to a registry, so it cannot be pulled`, after another project on the same computer registered an environment with the same name. Its build replaced the image yours used. The `first-pipeline` environment of [Your First Pipeline](../tutorials/getting-started.md) and the demo's environment are named the same in every project.

Give each project's environments a name of their own, such as `<project>-<env>`. A project that has already lost an image registers that environment again under such a name, points its methods' `env:` at the new name, and registers those methods again. Registering it again under the old name would take the image from the other project. No release is planned for this fix.
