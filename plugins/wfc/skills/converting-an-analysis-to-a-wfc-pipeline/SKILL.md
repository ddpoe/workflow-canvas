---
name: converting-an-analysis-to-a-wfc-pipeline
description: Use when a user hands over a whole analysis to become a Workflow Canvas (wfc) pipeline - a folder or project of notebooks and/or scripts, possibly with helpers, configs and data, or one large notebook that does several steps - such as "here is my analysis", "convert my notebooks to wfc", "make this project a wfc pipeline", "port our lab's analysis into workflow canvas". Plans the conversion with the user (which parts belong in wfc now, the steps, modules, envs, samples and wiring) for their approval, drafts every method and the whole pipeline before anything is registered, compares the results with the original's, registers only when the user agrees, and records every change in wfc-intake/. Use it before reading the files. For one script or notebook that is one step, use creating-a-wfc-method; for how wfc works, using-wfc. Not for Snakemake, Nextflow or other workflow tools.
---

# Converting an analysis to a wfc pipeline

The user has an analysis that works on their machine and wants it as a wfc pipeline without learning wfc first. You do the wfc work. The user makes the calls that are theirs: what the steps are, what a parameter should be, whether to register.

Converting code always changes it: hard-coded paths become inputs, constants become parameters, seeds get fixed, notebook cells get dropped or split, Windows or macOS becomes a Linux container. So the record is as much the product as the pipeline: the user, a reviewer or a later reader must be able to check that the pipeline still does what the original did. Write it at every stage (**The record**, below).

**Which skill.** The line is one step against several, not notebook against folder. One script or notebook that is a single step belongs to **creating-a-wfc-method**. Anything with several steps belongs here: a folder, a project, or one large notebook. When it's unclear, start here: if the inventory finds a single step, say so and carry on with **creating-a-wfc-method**'s steps. For how wfc works, see **using-wfc**, which also says which docs version to read.

Say what you are doing at each stage, and update the record at each stage. The user should never be surprised by something wfc recorded or something you changed.

## Before you start

1. **Version.** `wfc --version` must be `0.8.0b2` or later: drafting a whole pipeline before registering depends on it. If it's older, ask the user to upgrade wfc first.
2. **Tell the user how this will go**, in a few lines: you'll ask a few questions, propose a map of the pipeline for their approval before writing any method, draft and run everything before anything is registered, compare against their original results, and register only when they say yes. Tell them you will keep a record of every change in `wfc-intake/` and that it is theirs to commit with their code.
3. **The project.** It can be new or existing. A new one: `wfc init --dir <folder> --archive <path>`, asking the user where both go (**using-wfc**, "Setting up a project"), then `wfc doctor`. An existing one: `wfc list-methods` and `wfc list-envs`. A method already registered for this analysis goes on the map as "already in wfc" and is reused as it is; changing it goes through **creating-a-wfc-method**'s "Changing a registered method", not this skill.
4. **The project's location.** A project on a network or mapped drive runs steps against empty folders and still reports success. That includes a UNC path (`\\server\share\...`), a drive letter `net use` lists, or a mount that `df -T` or `mount` shows as nfs, cifs or smbfs. wfc doesn't check for this yet. If the project is on one, stop and say: "Keep the project on a local disk; the archive can be on the share." The user's original analysis can stay where it is.

## 1. Interview

Short, plain questions, one at a time, with a recommendation when you have one. Skip any question the files already answer, and on a small analysis ask only what you need; but always ask which results they actually use, since that decides what to port and what to compare.

- What is the analysis for, and which of its results do you actually use?
- Who runs it, and how often? On what data: one sample, many plates or batches, new data arriving?
- Which parts are settled, and which do you still change from day to day?
- Where are the existing results from a run of the original?
- Which environment (conda, pixi, uv, a container) do you run it in?
- Where should the wfc project live?

The answers decide what goes in. Settled steps that are rerun on new data are converted. Parts still changing daily stay notebooks for now: record them as "not yet, because …" and point to `wfc jupyter <env>`, which opens them in the env's container. Values someone changes become parameters; values that never change stay fixed in the code.

## 2. Inventory

Read every file and sort it into one kind:

- **step**: work that turns inputs into results;
- **human check**: someone looks at something and approves or picks;
- **orchestration**: choosing what to run, run tracking, bookkeeping (wfc replaces it);
- **exploration**: trying things out (often where a parameter was decided; read it for that);
- **unused**: backups, copies, saved outputs, old versions (`.bak`, `copy`, `old`, `out_`, sandbox files).

For a notebook, read its code cells in order (the `.ipynb` is JSON; read the `source` of each `code` cell) and note which cells belong to which step: one notebook can hold several steps, and two notebooks can be one step.

For each step, recover the real inputs and outputs, including data passed some way other than a file: a variable carried between cells, a pickle, a database, a tracking server, an environment variable. Note shared helper code, randomness, hard-coded paths (especially host paths such as `C:\...` or `/Users/...`), files fetched from the network at run time, a GPU, command-line tools, and data plus a config that only work together.

## 3. Run the original

Get reference outputs to compare against: either the user's existing results (stage 1), or a run of the original in the user's own environment. Run a copy of the folder, never the folder itself, so nothing of theirs is overwritten or added (running Python in place leaves a `__pycache__` folder beside their helpers). Ask before a run that takes long. If the original doesn't run as committed, say so and record what had to change for it to run; that is a finding for the user, not something to hide.

## 4. The map, for approval

Propose the pipeline as text plus a simple diagram (boxes and arrows in text are fine). The user approves the map before you write any method file. The map holds:

- **Steps**, each pointing back to the file and the cells or lines it came from.
- **Modules**: how the steps group (usually one module for the analysis).
- **Envs**: the user's env each step uses, registered as a copy (stage 5), and the expected build time.
- **Samples**: which data become samples. Data and the config it needs go in one directory sample; reference files (a template, a network download, a lookup table) are frozen once and registered as a sample or passed as an input, never fetched at run time.
- **Wiring**: each output to the input it feeds.
- **Parameters**: where each came from and its default.
- **Dropped**: what isn't converted and why (unused files, orchestration, exploration, parts still changing).
- **Human checks**: where they go (stage 6).
- **wfc limits hit** and the workaround (below).
- **Open questions** for the user.

**Where to split.** By what the steps do, not by file. Put the slow step alone, so it caches and changes downstream don't rerun it ("cosmetic changes to the figure should never re-run the statistics"). Split before a parameter the user will change late in the chain. Merge steps that read the same large input. Merge a step whose output nobody uses into its neighbour, once you've shown the merged result is identical. Never map one notebook to one method by default. Before restructuring, show the user the old layout next to the new one.

**wfc limits to plan around** (0.8.0; the **using-wfc** skill lists 0.8.0's known issues and their workarounds, planned to be fixed in 0.8.1):

- Notebooks become scripts. Method scripts are `.py`, `.R`/`.r` or `.sh`, and registration copies only scripts, `method.yaml` and declared `helpers:`. A config or data file beside a script must become an input or a sample.
- A file in the method folder that isn't a script (a reference table, a config) works in a draft, which copies the whole folder, but isn't in the registered version unless `helpers:` lists it. Make it an input or a sample instead, so the data stays visible and versioned as data.
- Many plates or batches: one sample each, on one input selector with `"fan_mode": "out"`. "Each plate, then combine across plates" in one pipeline is refused at load. While drafting, try the combining step with one `--parent-run-id` per plate on an input declared `multiple: true`. In the registered pipeline it becomes a second pipeline over the first one's runs (`run_reference` nodes, one per plate, into that input); as plates arrive, run the first pipeline for them, add a `run_reference` node for each new run and run the second again. One directory sample holding every plate is simpler, but each new plate then reruns every step for every plate, the slow one included; offer it only when the plates are analysed once, together, and say what it costs when new plates arrive over time.
- Two per-sample selectors into one step are refused. Use one directory sample holding both inputs.
- No pause or approval step: see human checks in stage 6.

Write the approved map into `conversion.md`. Later changes to it go there too, dated, with who decided.

## 5. Envs first

Envs take longest to build, so do them before the methods, one build at a time. A conda env from Windows or macOS takes a few minutes to register when it is small (about 5 minutes) and longer when it is large, most of it solving the env again for Linux and building its image (in 0.8.0b2 and earlier, 30 to 60 minutes); say so, and start it in the background while you write the method folders. Use the env the user already has: they use it for other things, so don't ask them to rebuild it. Only for a new env, recommend pixi (wfc reads `pixi.lock` directly and doesn't solve again when the lock includes `linux-64`), or for Python-only work a uv project (`wfc register-env <name> --backend uv --from <project>`).

While it builds, follow `wfc register-env`'s own output. When it prints phase lines, a heartbeat line means it is still working; don't read Docker's internals to find out. After an interrupted run or registration (a restart, Ctrl-C, a killed shell), run `wfc list-envs`; when its footer reports leftovers and `wfc prune-envs --help` works (it doesn't exist in 0.8.0b2), suggest `wfc prune-envs`, which lists what it would remove and asks first. Pass `--yes` only after the user says yes. If it says to quit and reopen Docker Desktop for builds it couldn't stop, pass that on. Never give the user raw Docker commands. To give another project the same env, register it there from this env's `packages.lock` (`wfc register-env <name> --backend conda --from <packages.lock>`), which skips the conda calls and the solve. In 0.8.0b2 that file fails inside the image build; offer it only when the installed wfc's changelog or `wfc register-env --help` says `--from` accepts an env's own `packages.lock`.

- **Never modify the user's env**: they use it for other things. Registering it (`wfc register-env <name> conda:<env>`) only reads it. A Python method written with `wfc_client` needs `wfc-client` in its env. When the user's env lacks it, recommend a copy of the env with `wfc-client` added, registered in its place: the scripts then use `wfc_client` as the docs' examples do, which is clearer for whoever reads them later. Only if the user would rather not make a copy, write the scripts to the plain contract instead (the docs page `reference/method-script-contract.html`: a few lines that read `WFC_INPUT_PATHS` and `WFC_PARAMS` and write into `WFC_RUN_DIR`) and register the user's env as it is. Either way, give the registered env a project-specific name (`<project>-<env>`), not the user's env name. For conda, build the copy from the packages the env asked for, pinned to the versions it has: `conda env export -n <env> --from-history` lists the requested packages, `conda list -n <env>` their installed versions; then `conda create -n <project>-<env> -c <its channels> <name>=<version> ...` and `conda run -n <project>-<env> pip install wfc-client`. Don't copy it with `conda create --clone`: a clone records every package as a platform-specific build, and on Windows or macOS `wfc register-env conda:<clone>` then fails while building the Linux image.
- Register: `wfc register-env <name> conda:<env>`, or the pixi, uv or byo form (**using-wfc**, "Setting up a project"). An R or bash env needs `--interpreter`.
- Tell the user what you created and changed on their machine (a cloned env, a new image), since it's theirs to keep or remove.
- Once each is built, write its env report, `wfc-intake/envs/<env>.md` (**The record**). For the package list, compare the host's list (`conda list --export`, `pip freeze`) against the captured one: `wfc show-env <name>` or the env's detail panel in the canvas.

## 6. Each method

Register the module first (`module.yaml` with a `description:` line, then `wfc register-module --name <module> --module-dir src/<module>`). Register the samples the map names (`wfc register-sample --name <name> --source <path>`). Then, for each step in pipeline order, follow **creating-a-wfc-method** steps 1 to 3: the method folder `src/<module>/<method>/`, its `method.yaml`, the script's edges and the rerun-identical fixes, then `wfc try`. Leave the user's original files where they are and unchanged.

- **Downstream drafts read upstream drafts.** The upstream step's try ends with its draft string. Pass it to the next try with `--parent-run-id <input>:<output>:draft:<module>.<method>/<node>@<sample>` (`wfc runs` lists draft strings too).
- **Shared code** (a helper several steps import): put a copy of it in each method folder that uses it, name the main script with `script: <method>.py` (a folder holding two scripts needs it), list the helper under `helpers:`, and import it as the original did (the script's folder is on the import path). In 0.8.0 a draft runs a copy of its own method folder only, so a helper kept outside it (`helpers: [../shared/x.py]`) works once registered but is missing when the draft runs. Unchanged copies of the user's helper keep the diff small; record in `conversion.md` that the copies must be edited together.
- **No wfc in the analysis code.** Only the edges change (inputs, parameters, outputs); keep wfc and container terms out of the user's analysis lines.
- **Figures the user expects to see** are outputs: a `figures` directory output when their number varies.
- **Human checks** become "look at the draft outputs" points while drafting: stop and show the user. In the registered pipeline a check is advice: record it in `conversion.md` and the method record as a review to do by hand, and let the pipeline run through. Split the pipeline at a check, the second pipeline running over the first one's runs, only when running past it would waste a lot (the slow step comes after it) or give wrong results.
- **Judgement parameters**: a value someone picked by eye, such as a threshold with a comment saying so or one tried in an exploration notebook. Even when the code gives one value, don't just carry it over: propose a few options from a stated rule, try each (`wfc try ... --params '{"<param>": <value>}'`), export them and show the results side by side, and let the user pick. Only the chosen value goes in the pipeline; the other options, what they showed and the reason for the choice go in the record. In a pipeline, `param_sets` runs named variants, and a hand-written variant replaces the node's `params`, so it repeats every required parameter.
- **A science problem in the original** (a filter that removes nothing, a fix commented out, a wrong unit): never fix it silently. Tell the user (and, when the user isn't the analysis's owner, ask them to check with the owner), keep the original's behaviour as the default, and expose the choice as a parameter when it is one.
- **The method record starts at the first try** (**The record**).

## 7. The whole pipeline, as drafts

Write the pipeline file, `pipelines/<name>.json` (the docs page `reference/pipeline-document.html` lists every key). Each method node runs its draft when pinned to it: `"version": "draft"` on the node. Leave out `"folder"`: the node then runs the method's current draft (in 0.8.0 a relative `folder` is refused). Every node downstream of a draft runs as a draft too. Name each link's slots with `sourceHandle` and `targetHandle` (the keys the canvas writes): in 0.8.0 `wfc check-pipeline` reads only those, and refuses a node with two inputs whose links use `target_slot`, although `wfc run-pipeline` accepts both.

```bash
wfc check-pipeline --pipeline pipelines/<name>.json
wfc run-pipeline --pipeline pipelines/<name>.json
```

`check-pipeline` needs no Docker. Every step of the map runs as a draft before you ask to register, including a step that will live in a second pipeline (try it with `--parent-run-id` entries from the first pipeline's draft run), so a wiring problem shows up while nothing is recorded. Warn the user: draft runs never use or fill the cache, so an end-to-end draft run reruns the slow steps every time. Iterate on single methods with `wfc try` and draft strings, and run end to end when a checkpoint is worth it. Each draft keeps only its latest run: running it in another pipeline replaces that run and clears the draft results downstream of it.

## 8. Compare against the original

Export each output (`wfc export --run <draft string or run id> --all --dest <dir outside the project>`) and compare it with the original's reference output. Before comparing, write down the tolerance for each output and why (for a step that was unseeded in the original, run the original twice and use the spread between its own runs). Give each output one verdict: **identical** (same hash), **within a stated tolerance** (say which, e.g. a relative difference below 1e-6, and why it's acceptable: a Linux/Windows numeric difference, a library version), or **different**, with the explanation or the open question. Compare tables by values, not only by bytes; compare figures by eye when their bytes can't match. Where a method claims reruns are identical, try it twice and compare the two exports' hashes.

Record the verdicts in each method record and a summary in `conversion.md`.

## 9. Review

Show the user every `method.yaml` and every script's diff against its original, with how each input, output and parameter maps to the original. List every change to what the code computes, reads or writes, however small. Offer two places to look: the canvas (Registry → **Methods** → **Drafts**, where each draft's panel lists its files, main script first; open the canvas only if the user agrees), or an editor (`code --diff <original> <converted>`). Then ask whether to register.

## 10. Register, when the user says yes

**Register only when the user says yes**: registering adds versions and git commits, and a registered version can't be deleted. Then:

1. `wfc register-method src/<module>/<method> --module <module> --yes`, for every method, upstream first. (`--yes` only after the user's yes.)
2. Remove the `"version": "draft"` pins from the pipeline file and check that none is left (wfc doesn't warn about a node still pinned to its draft), `wfc check-pipeline` it, and `wfc run-pipeline` it once: registration adopts no draft outputs, so this is the first recorded run. `wfc runs --pipeline <id>` should list every step with a version (`v1`), none as a draft. When you tell the user how long a run takes, give the first run's time and a cached rerun's separately.
3. Add each method's version and the run ids to the records (`wfc runs --pipeline <id>`).
4. The drafts stay until `wfc delete-draft <module>.<method>` removes them; say so, and remove them only if the user wants.

## The record

Always written; the user is told at the start (Before you start). wfc doesn't keep or read it: it is the user's, to commit with their method folders.

```
wfc-intake/
  conversion.md              the whole conversion
  envs/<env>.md              one per env
  <module>.<method>.md       one per method
```

**`conversion.md`**, kept current at every stage:

- **The interview**: the answers.
- **Inventory**: every file and its kind (step, human check, orchestration, exploration, unused).
- **The map**: as approved, and each later change, dated.
- **Calls**: each decision, who made it and why (a picked threshold with the options it beat, a dropped file, a split).
- **Status per step**: drafted, compared (verdict), blocked (reason and what was tried), registered (version).
- **Not converted**: what and why.
- **Flagged to the owner**: science issues found in the original.
- **Open questions.**
- **How to resume**: what to do next, so a later session can start from this file.

<!-- Shared with creating-a-wfc-method: keep this block identical in both skills. -->
**`envs/<env>.md`**, one per env, written once it's built: what differs between the user's machine and the Linux image, each item with a verdict (likely to matter, unlikely, or check this, and how):

- operating system and paths;
- language version;
- package versions: the host's list against the env's captured package list (`wfc show-env <env>`);
- tools on the host but not in the image;
- GPU, CPU threads, locale, line endings.

Method records link to it rather than repeating it.

**`<module>.<method>.md`**, one per method. It starts at the method's first `wfc try`, with a `## Draft — <date>` section on top:

- **Source**: the original file and the cells or lines it came from.
- **Changes**: every change to what the code computes, reads or writes, and why.
- **Mapping**: each input, output and parameter, and where it came from in the original.
- **Try results**: what ran, on which sample, and what came out.
- **Comparison with the original**: per output, identical, within a stated tolerance, or different (and why), when the original's outputs exist.
- **Env**: a link to `envs/<env>.md`.

On registering, the Draft section becomes `## v1 — <date>` (later versions add sections on top, newest first) and gains **How to run it**: the module, method, env and sample names and the commands.
<!-- End of the shared block. -->

## When a step fails mid-conversion

Mark the step blocked in `conversion.md`, with the reason and what was tried. Carry on with the steps that don't depend on it, and ask about anything that needs the user's decision rather than guessing. A later session starts by reading `wfc-intake/conversion.md` (ask where it is if it isn't at the project root) and resumes from its "How to resume" and the status per step; check the project's state against it (`wfc list-methods`, `wfc runs`) before going on.

## If you can't run commands

In a chat with no access to the user's computer, say so once, then still work through the stages from what the user pastes or uploads. Every round trip costs the user, so group your questions in one message rather than asking one at a time.

- **First reply:** the inventory and the map from what they pasted, the interview questions the files don't answer (grouped), and the commands that don't depend on the map: `wfc --version`, the env's package list (`conda env export` and `pip freeze`, or `pixi.lock`), and setting up the project and the env copy. Ask for anything else you need: a file they didn't paste, and the original's results (or a few of their numbers) for the comparison.
- **Once they approve the map**, write every file in full: each method folder (`method.yaml` and script), `module.yaml`, the pipeline file with draft pins, and the `wfc-intake/` record. If this chat can create files for download (you have a tool that writes files), offer them as one zip laid out as the project expects (`src/<module>/<method>/...`, `pipelines/`, `wfc-intake/`); otherwise give each file in its own block, headed with its path.
- **Give the rest of the commands in order:** module, samples, each `wfc try` (with the draft string the previous one printed), `wfc check-pipeline` and `wfc run-pipeline`, the exports for the comparison, and the register commands marked "only after you've checked the drafts". Ask the user to paste back each command's output, and adjust from it.
- You can't test anything. Never say a command ran or a result matched when you didn't see its output; in the record, mark each step "written, not yet tried" until the user's output shows otherwise.

## Talking to the user

One decision at a time, in plain words, with a recommendation. Show old against new before restructuring. Use plain wfc terms (method, env, sample, draft) and explain each the first time. Keep wfc and container terms out of the user's own scripts. Skip the ceremony on a small analysis.

## If wfc itself seems wrong

Follow "If wfc itself seems wrong" in **using-wfc**: reproduce it once, check the docs for the installed version, never work around a bug silently, and at the end add one short block, **I think I found a wfc bug:** `<command>` did `<what happened>`; I expected `<what the docs or --help say>`, with an offer to draft an issue. Mark the step blocked in `conversion.md` when the bug stops it.
