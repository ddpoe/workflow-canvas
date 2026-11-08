<!-- generated from pm_mvp::docs/consumer/reference/known-issues @ 2cdbab7b4b0b; do not edit -->

# Known Issues

## Reference: Known Issues

These are known bugs in wfc 0.8.0 and its pre-releases, each with a way around it. They are planned to be fixed in 0.8.1.

If an AI agent is driving wfc with the wfc plugin's skills, it knows these and works around them for you.

## A pipeline that reads an earlier pipeline's runs fails before any step runs

You see `MissingInputException in rule <step>`, naming files under `.wfc/runs/<id>/_workdir/`, for a pipeline whose run reference nodes point at an earlier pipeline's runs. It happens once the earlier pipeline's outputs are archived, which `wfc run-pipeline` does by default.

Run the first pipeline with `wfc run-pipeline --no-archive`, then the pipeline that reads its runs, then `wfc cache archive`. To run the second pipeline again later, export the outputs it reads (`wfc export`), register them as samples and feed it from those; wfc then no longer records which runs made them.

## A reference to a cached run is refused

A run reference to a run that was served from the cache (`⟳ CACHED` in History) is refused: `Reference '<name>' is invalid for run <id> (<method>); re-run that step and select the new run.` Running the step again gives another cached run, so reference the run it reused instead: `wfc show-run --run <id>` says `cache hit of run N`.

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
