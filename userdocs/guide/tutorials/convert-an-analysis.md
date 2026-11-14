<!-- generated from pm_mvp::docs/consumer/tutorials/convert-an-analysis @ 5f01e9efffdf; do not edit -->

# Convert an Analysis into a Pipeline

## Overview

You have an analysis that works on your machine: a folder of notebooks and scripts. This page takes one such folder, names the things in it that make its results hard to reproduce, and turns it into a Workflow Canvas (wfc) pipeline that deals with each of them.

At the end each step of the analysis is a registered method, the pipeline runs every plate in a container, its results match the notebooks', and a new plate reruns only its own steps. The conversion is done by hand here, so you can see every step; an agent with the wfc plugin can do the same one with you, and the page ends by comparing the two.

## The Example

The example is a small single-cell drug-response analysis: each cell's fluorescence, measured from images of two plates, under four drug conditions, as fold change over DMSO with a bootstrap interval, and a comparison of the two plates. The folder is [`examples/single-cell-drug-response-analysis`](https://github.com/ddpoe/workflow-canvas/tree/main/examples/single-cell-drug-response-analysis) in the wfc repository (`git clone https://github.com/ddpoe/workflow-canvas`, then copy that folder out as `~/plate_assay`); it runs in under a minute, about 15 seconds of it per plate in the bootstrap, in the conda env `plates-lab`. To follow along, create that env first:

```bash
conda create -n plates-lab -c defaults python=3.12 numpy pandas matplotlib nbconvert ipykernel
```

```text
plate_assay/
  01_process_plates.ipynb          load, QC histogram, area filter, normalize, bootstrap (slow), bar chart; run once per plate
  01_process_plates - Copy.ipynb   an old copy
  02_compare_plates.ipynb          compares the plates
  explore_thresholds.ipynb         where the area threshold 35 was picked
  plate_utils.py                   helpers both notebooks import
  plate_utils.py.bak               an old copy
  notes.txt                        how to run it
  data/plate1/, data/plate2/       cells.csv and plate.json (the well layout and background), which belong together
  results/                         the notebooks' outputs from the last run
```

It has what a real analysis folder usually has: one notebook holding several steps, one of them slow; a check where someone looks at a histogram before going on; a threshold picked by eye; hard-coded `C:\Users\...` paths; a random step with no seed; a helper shared by two notebooks; old copies; and one bug. The next section goes through them.

## What Makes a Result Hard to Reproduce

The example runs, and gives the right figures, on the computer it was written on. Eight things in it would stop someone else, or you a year from now, getting the same result. Each is common in analysis folders, and the conversion below deals with each at the step named here.

| What gets in the way | In the example | In the pipeline | Step |
|---|---|---|---|
| Software only one computer has | the conda env `plates-lab` | a container image, pinned | 1 |
| A path only one computer has | `C:\Users\sam\Desktop\...` | each plate is a registered sample | 2 |
| A setting edited by hand between runs | `PLATE = "plate1"`, then `"plate2"` | the pipeline runs every plate | 2 |
| Steps run by hand, in order | `notes.txt` | wired methods, each cached | 3 |
| A setting buried in the code | `MIN_AREA = 35` | a parameter, recorded with each run | 3 |
| Random numbers that differ on every run | `np.random.choice(...)` | a `seed` parameter | 3 |
| Shared code and old copies | `plate_utils.py`, `.bak`, `- Copy` | each method keeps the helper it ran with | 3 |
| A result that uses the wrong table | a filter that does nothing | found while splitting the steps; a parameter | 3 |

:::{admonition} An AI agent can do this with you
:class: agent-tip

An agent with the wfc plugin is instructed to look for these as it reads your files, and to ask before it changes what the analysis does: see [Have an Agent Do It](#have-an-agent-do-it).
:::


## Convert It by Hand

The conversion, step by step. Each step names the page that explains it in full.

**1. Make the project and copy the env.** Create the project with its archive (the backup copy of your data and results). Keep the project on a local disk; the archive can be on a network share:

```bash
wfc init --dir plate-assay --archive ~/wfc-archives/plate-assay
cd plate-assay
wfc doctor
```

**In the way: software only one computer has.** The analysis runs in the conda env `plates-lab` on Sam's Windows computer. To run it on another computer you would rebuild that env by hand and hope the versions match. In wfc you register the env once: wfc builds a Linux container image from it and pins that exact build, so every step runs with the same software on any computer with Docker ([Share a Project](../how-to/sharing-a-project.md) covers handing it over). `plates-lab` has no `wfc-client`, the small, optional helper library a method script can use to read its inputs and save its outputs. Leave `plates-lab` as it is and make a copy with `wfc-client` added. Build the copy from the packages `plates-lab` asked for, at the versions it has (`conda env export -n plates-lab --from-history` lists them, `conda list -n plates-lab` gives the versions). The versions below are those of the env this page was run with; use yours:

```bash
conda create -n plates-lab-wfc -c defaults python=3.12.16 numpy=2.5.4 pandas=3.1.0 matplotlib=3.11.1 nbconvert=7.17.2 ipykernel=7.3.0
conda run -n plates-lab-wfc pip install wfc-client
wfc register-env plates-lab-wfc conda:plates-lab-wfc
```

Don't copy it with `conda create --clone`: on Windows and macOS the clone's image doesn't build. Registering it took about 5 minutes here, while wfc worked out the Linux versions of its packages and built an image from them; [Register an Environment](registering-an-environment.md) explains what it records.

**2. Register the module and the plates.** 

**In the way: a path only one computer has.** Both notebooks start by naming folders on Sam's desktop:

```python
DATA_DIR = r"C:\Users\sam\Desktop\plate_assay\data"
OUT_DIR = r"C:\Users\sam\Desktop\plate_assay\results"
```

Nobody else has those folders. A registered sample replaces them: wfc hands the method the data's location, and outputs are saved by name.

**In the way: a setting edited by hand between runs.** The same cell holds the plate to process:

```python
PLATE = "plate1"   # change to plate2 and run again
```

Which plate the saved results came from depends on what was typed last. With both plates registered as samples, one run of the pipeline processes each of them.

A plate's `cells.csv` only means something with its `plate.json` (the well layout and the background), so each plate's folder becomes one sample:

Create `src/plate_assay/module.yaml` holding one line, `description: Plate assay`, then:

```bash
wfc register-module --name plate_assay --module-dir src/plate_assay
wfc register-sample --name plate1 --source ../plate_assay/data/plate1
wfc register-sample --name plate2 --source ../plate_assay/data/plate2
```

**3. Split the notebooks into methods.** 

**In the way: steps run by hand, in order.** `notes.txt` is the only record of how to run the analysis: open this notebook, run all, change the plate, run all again, then open the other one. As wired methods, the order is part of the pipeline.

`01_process_plates` holds three steps; `02_compare_plates` is a fourth. The bootstrap is slow, so it gets a method of its own: a change to the plot then never reruns it.

| Method | From | Inputs | Outputs | Parameters |
|---|---|---|---|---|
| `normalize_plate` | 01, cells 3-7 | `plate` (the plate's folder) | `normalized`, `qc_histogram` | `min_area` (35) |
| `bootstrap_ci` | 01, cell 9 | `normalized` | `bootstrap` | `n_boot` (200000), `seed` (0) |
| `plot_fold_change` | 01, cell 10 | `bootstrap`, `plate` | `figure` | |
| `compare_plates` | 02 | `bootstraps` (several) | `comparison`, `figure` | `plate_names`, `min_cells` (0) |

`explore_thresholds` was where 35 was chosen, so it stays out; `01_process_plates - Copy` and `plate_utils.py.bak` are old copies. The QC histogram the notebook showed inline becomes the `qc_histogram` output, to look at for each new plate.

Each method is a folder under `src/plate_assay/`, holding the script and its `method.yaml`. The analysis lines stay as they were; only the edges change: inputs come from `ctx.input`, parameters from `ctx.params`, and outputs are saved by name. Here is `bootstrap_ci`:

```python
import numpy as np
import pandas as pd
import wfc_client as wfc

from plate_utils import CONDITION_ORDER


@wfc.method
def bootstrap_ci(ctx):
    norm = pd.read_csv(ctx.input("normalized"))
    n_boot = int(ctx.params["n_boot"])
    np.random.seed(int(ctx.params["seed"]))

    rows = []
    for cond in CONDITION_ORDER:
        ...  # the notebook's loop, unchanged
    boot = pd.DataFrame(rows)
    out_path = ctx.workdir / "bootstrap.csv"
    boot.to_csv(out_path, index=False)
    ctx.save_artifact("bootstrap", out_path)


if __name__ == "__main__":
    wfc.run()
```

```yaml
script: bootstrap_ci.py
inputs:
  normalized:
    type: csv
    description: One plate's normalized cells (normalize_plate's output).
outputs:
  bootstrap:
    type: csv
    description: Per condition, the mean fold change, its 95% interval and the number of cells.
params:
  n_boot:
    type: int
    default: 200000
  seed:
    type: int
    default: 0
    description: Random seed, so a rerun gives the same intervals. The notebook had none.
helpers:
  - plate_utils.py
env: plates-lab-wfc
```

Four changes go beyond the edges. Each deals with something that was in the way of reproducing the result:

- **A setting buried in the code.** The notebook drops cells smaller than a threshold, set by one line in one cell of a long notebook: `MIN_AREA = 35`. Nothing records which value made the saved results; the old copy of the notebook says `30`. As the parameter `min_area`, you see it on the step, and every run records the value it used.
- **Random numbers that differ on every run.** `bootstrap_ci` estimates its error bars by drawing cells at random, many times over (a bootstrap), with `np.random.choice`. The notebook never gives the random number generator a seed, the starting number that makes it repeat the same draws, so every run gave slightly different error bars and no rerun could match the saved ones. The method takes a `seed` parameter, recorded with each run, so a rerun draws the same cells.
- **Shared code and old copies.** Both notebooks import `plate_utils.py`, and the folder also holds `plate_utils.py.bak` and `01_process_plates - Copy.ipynb`; nothing records which version made the saved results. Each method folder holds a copy of the helper, listed under `helpers:`, and registering the method saves that copy with it. `script:` names the main script.
- **A result that uses the wrong table.** `02_compare_plates` builds a filtered table, `combined_clean = combined[combined["n_cells"] >= 50]`, and then carries on with the unfiltered `combined`. Conditions with fewer than 50 cells stay in the comparison (plate2's drugB_1uM has 29), nothing fails, and the figure looks fine. wfc does not find a bug like this for you: it shows up here, when you split the notebook and have to say what each step takes in and gives out. `compare_plates` keeps the notebook's result by default (`min_cells: 0`) and lets you switch the filter on (`min_cells: 50`). Changing it is the analysis owner's call.

The whole folder is in [`examples/single-cell-drug-response-pipeline`](https://github.com/ddpoe/workflow-canvas/tree/main/examples/single-cell-drug-response-pipeline). [Write a Method in Python](authoring-a-method-script.md) covers the script, and the [method.yaml Schema](../reference/method-yaml-schema.md) every key.

**4. Try each method as a draft, upstream first.** `wfc try` runs a folder on a sample and records nothing. Each try prints a draft string, the name of that draft's result (`draft:<module>.<method>/<node>@<sample>`); the next method reads its upstream's output by passing it to `--parent-run-id` as `<input>:<output>:<draft string>`, naming this method's input and the upstream output it reads:

```bash
wfc try src/plate_assay/normalize_plate --module plate_assay --sample plate1
wfc try src/plate_assay/bootstrap_ci --module plate_assay --sample plate1 \
  --parent-run-id normalized:normalized:draft:plate_assay.normalize_plate/normalize_plate@plate1
wfc try src/plate_assay/plot_fold_change --module plate_assay --sample plate1 \
  --parent-run-id bootstrap:bootstrap:draft:plate_assay.bootstrap_ci/bootstrap_ci@plate1
```

[Draft from the Command Line](../how-to/draft-from-the-command-line.md) walks through `wfc try`.

**5. Run the whole pipeline as drafts.** In `pipelines/plates-draft.json`, an input selector (the node that picks which samples go in) feeds both plates to the three methods, each pinned to its draft with `"version": "draft"`:

```json
{"id": "bootstrap_ci", "module": "plate_assay", "method": "bootstrap_ci", "version": "draft",
 "params": {"n_boot": 200000, "seed": 0}}
```

```bash
wfc check-pipeline --pipeline pipelines/plates-draft.json
wfc run-pipeline --pipeline pipelines/plates-draft.json
```

The whole file is [`pipelines/plates-draft.json`](https://github.com/ddpoe/workflow-canvas/blob/main/examples/single-cell-drug-response-pipeline/pipelines/plates-draft.json) in the example. Draft steps never use the cache, so this reruns the bootstrap each time. Then try `compare_plates` over both plates' bootstrap drafts, one `--parent-run-id` per plate on its `bootstraps` input. `wfc try` always takes a registered sample; `compare_plates` reads none, so here `--sample plate1` only names the draft:

```bash
wfc try src/plate_assay/compare_plates --module plate_assay --sample plate1 \
  --parent-run-id bootstraps:bootstrap:draft:plate_assay.bootstrap_ci/bootstrap_ci@plate1 \
  --parent-run-id bootstraps:bootstrap:draft:plate_assay.bootstrap_ci/bootstrap_ci@plate2
```

**6. Compare with the notebooks' results.** Export each draft's outputs (`wfc export --run <draft string> --all --dest <folder>`) and compare them with `results/`:

| Output | Against the notebook's |
|---|---|
| `normalized_plate1.csv`, `normalized_plate2.csv` | equal values |
| `bootstrap_plate1.csv`, `bootstrap_plate2.csv` | means and cell counts equal; interval bounds within a relative 6e-4 (the notebook's bootstrap was unseeded, and its own reruns differ by about as much) |
| `fold_change_plate1.png`, `fold_change_plate2.png` | the same figures, error bars within that 6e-4 |
| `plate_comparison.csv` | equal values |
| `comparison.png` | identical file |

**7. Register and run it once.** Registering adds each method as v1 and commits it to the project's git history:

```bash
wfc register-method src/plate_assay/normalize_plate --module plate_assay
wfc register-method src/plate_assay/bootstrap_ci --module plate_assay
wfc register-method src/plate_assay/plot_fold_change --module plate_assay
wfc register-method src/plate_assay/compare_plates --module plate_assay
```

Copy `plates-draft.json` to `pipelines/plates.json` without the `"version": "draft"` pins and run it. That is the first recorded run: wfc caches each step, so the next plate reruns only what changed.

```bash
wfc run-pipeline --pipeline pipelines/plates.json
```

**8. Compare the plates.** A pipeline can't run each plate and then combine across plates, so the comparison is a second pipeline over the first one's runs: `pipelines/compare.json` feeds the two `bootstrap_ci` runs into `compare_plates` with run-reference nodes. Its `run_id` values are the example's; put in your own from `wfc runs`. Run it:

```bash
wfc run-pipeline --pipeline pipelines/compare.json
```

To compare again as new plates arrive, run `plates.json` for them, add a run-reference node for each new `bootstrap_ci` run to `compare.json`, and run it again. The [Pipeline Document](../reference/pipeline-document.md) reference lists every key both files use.

## Have an Agent Do It

An agent that has the wfc plugin can do the same conversion with you. [Work with an AI Agent](work-with-an-ai-agent.md) shows how to add the plugin to Claude Code, Codex or a chat app. Then ask in your own words, naming the folder and the env you run it in:

```text
here is my analysis: ~/plate_assay (my plate assay notebooks). can you turn it into a
workflow canvas pipeline for me? I run it in my conda env plates-lab.
```

The plugin's `converting-an-analysis-to-a-wfc-pipeline` skill takes it from there. You make the calls that are yours; the agent does the wfc side:

- **It asks a few questions, then proposes a map** of your notebooks next to the pipeline it would build: which cells each step comes from, what becomes a setting, and what is left out and why. Nothing is written until you approve it.
- **It flags what it finds without changing what the analysis does.** On this example:

  > Cell 3 makes `combined_clean`, dropping conditions with fewer than 50 cells, but the next cell uses `combined`, so **the filter is never applied**. It matters for your data: plate2's drugB_1uM has only 29 cells and is in your current comparison. I won't change what the analysis does without your say: by default, `min_cells` = 0 keeps today's behaviour, and setting it to 50 applies the filter as the comment intended.

- **It builds the env and drafts every step**, then compares each output with your saved results and states the tolerance. For a value picked by eye, like the area threshold, it runs a few options and shows you the results before you choose.
- **It registers only when you say yes**, after showing you every `method.yaml` and a diff of each script against your notebook cells.
- **It leaves a record** in `wfc-intake/` in the project: your answers and the map, every change to the code and why, what differs between your machine and the Linux container, and how each result compared with yours. wfc doesn't keep or read these files: commit them with your code.


## The Two Results Side by Side

Both conversions were run on this folder. The agent column is one evaluation run of the skill; the by-hand column is the steps above.

| | By an agent | By hand |
|---|---|---|
| Steps | `normalize_plate`, `bootstrap_ci` (alone, so it caches), `plot_fold_change`, `compare_plates` | the same four |
| Settings | `min_area` 35, `n_boot` 200000, `seed` 0 (new), `min_cells` 0 | the same |
| The plate's name for the chart title | a `plate` column added to the bootstrap table | the plate's folder as a second input of the chart |
| Shared helper `plate_utils.py` | copied, unchanged | copied, unchanged, into each method folder |
| Env | a copy of `plates-lab` with `wfc-client` added; `plates-lab` unchanged | the same |
| Combining the plates | a second pipeline over the first one's results | a second pipeline over the first one's runs |
| The unused filter | flagged; `min_cells` 0 keeps the notebook's result | the same |
| Results against `results/` | tables equal, `comparison.png` identical, intervals within a relative 6e-4 | the same |
| Record of the changes | `wfc-intake/`: the conversion, the env, each method, and a diff per script | what you write yourself |

The two pipelines differ only in naming and in where the plate's name travels; the methods, settings and results are the same.

## Next Steps

- [Your First Pipeline](getting-started.md) builds a pipeline from scratch, and [Build Your Own Pipeline](../how-to/build-your-own-pipeline.md) lists the steps for your own analysis.
- [Draft a Method](drafting-a-method.md) explains drafts and `wfc try`.
- [Pipeline Document](../reference/pipeline-document.md) lists every key of the pipeline files.
