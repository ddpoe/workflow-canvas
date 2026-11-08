<!-- generated from pm_mvp::docs/consumer/tutorials/convert-an-analysis @ 048bd4ec8d32; do not edit -->

# Convert an Analysis into a Pipeline

## Overview

You have an analysis that works on your machine: a folder of notebooks and scripts. This page turns one such folder into a wfc pipeline twice, once by handing it to an AI agent and once by hand, and ends by comparing the two results, so you can see it is the same pipeline either way.

Both tracks end with the same thing: each step of the analysis is a registered method, the pipeline runs every plate in a container, its results match the notebooks', and a new plate reruns only its own steps. The agent track also leaves a written record of every change to your code.

## The Example

The example is a small single-cell drug-response analysis: each cell's fluorescence, measured from images of two plates, under four drug conditions, as fold change over DMSO with a bootstrap interval, and a comparison of the two plates. The folder is [`examples/single-cell-drug-response-analysis`](https://github.com/ddpoe/workflow-canvas/tree/main/examples/single-cell-drug-response-analysis) in the wfc repository (`git clone https://github.com/ddpoe/workflow-canvas`, then copy that folder out as `~/plate_assay`); it runs in under a minute in a conda env with numpy, pandas and matplotlib.

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

It has what a real analysis folder usually has: one notebook holding several steps, one of them slow; a check where someone looks at a histogram before going on; a threshold picked by eye; hard-coded `C:\Users\...` paths; a random step with no seed; a helper shared by two notebooks; old copies; and one bug, a filter that is computed and never applied.

## Track 1: By an Agent

Hand the folder to an agent that has the wfc plugin. [Work with an AI Agent](work-with-an-ai-agent.md) shows how to add it to Claude Code, Codex or a chat app. Then ask in your own words, naming the folder and the env you run it in:

```text
here is my analysis: ~/plate_assay (my plate assay notebooks). can you turn it into a
workflow canvas pipeline for me? I run it in my conda env plates-lab.
```

The plugin's `converting-an-analysis-to-a-wfc-pipeline` skill takes it from there. You make the calls that are yours; the agent does the wfc side. Here is what happens, with excerpts from a run on this example.

**It says how it will go.** A few questions, a map for you to approve before any code is written, everything built and run as drafts first, a comparison with your saved results, and nothing registered until you say yes. It also says it will keep a record of every change in `wfc-intake/`.

**It asks a few questions, one at a time.** Which results you actually use (here the two figures and `plate_comparison.csv`), how often new plates arrive, which parts are settled, where your saved results are, and where the project should live. If you name a network drive, it asks you to keep the project on a local disk; the archive can be on the share.

**It reads every file and proposes a map.** The map shows what you run today next to the pipeline it proposes, which notebook cells each step comes from, what becomes a setting you can change, and what is left out and why:

```text
TODAY (by hand)                               PIPELINE 1: process_plates (one branch per plate)

01_process_plates, PLATE="plate1", run all     sample plate1 ─┐   (data/plate1: cells.csv + plate.json)
01_process_plates, PLATE="plate2", run all     sample plate2 ─┤
02_compare_plates, run all                                    ▼
                                               1. normalize_plate   cells 3, 5, 6, 7
                                               2. bootstrap_ci (SLOW) cell 9
                                               3. plot_fold_change  cell 10

                                               PIPELINE 2: compare_plates (all plates so far)
                                               bootstrap of plate1 ─┐
                                               bootstrap of plate2 ─┴─▶ 4. compare_plates
```

It also flags what it found in your code, without changing what the analysis does:

> Cell 3 makes `combined_clean`, dropping conditions with fewer than 50 cells, but the next cell uses `combined`, so **the filter is never applied**. It matters for your data: plate2's drugB_1uM has only 29 cells and is in your current comparison. I won't change what the analysis does without your say: by default, `min_cells` = 0 keeps today's behaviour, and setting it to 50 applies the filter as the comment intended.

Nothing is written until you approve the map.

**It builds an env, then drafts every step.** It registers your env without changing it (a copy when it needs to add something), which takes a few minutes for a small conda env on Windows or macOS and longer for a large one. Then it writes each method, tries it as a draft, and runs the whole pipeline as drafts. For a value picked by eye, like the area threshold, it runs a few options and shows you the results before you choose.

**It compares the results with yours.** Each output gets a verdict, with the tolerance stated:

> normalized tables and the comparison table: identical numbers; `comparison.png`: identical bytes; bootstrap means and cell counts: identical, intervals within 6e-4 (your notebook's bootstrap was unseeded, so it differs from run to run by about that much); bar charts: the same by eye.

**It shows you the code and asks to register.** You see every `method.yaml` and a diff of each script against your notebook cells, in an editor or in the canvas (Registry → **Methods** → **Drafts**). It registers only when you say yes, then runs the pipeline once so the run is recorded.

**You keep the record.** `wfc-intake/` in the project holds:

- `conversion.md`: your answers, the map and every later change, the calls made and by whom, each step's status, what wasn't converted and why, and how to add the next plate or pick the work up in a later session;
- `envs/<env>.md`: what differs between your machine and the Linux container (Python and package versions, paths, line endings), each with whether it might matter;
- one file per method: where it came from, every change to the code and why, how its inputs and settings map to the notebook, and how its results compared with yours.

wfc doesn't keep or read these files: commit them with your code.

## Track 2: By Hand

Do the same conversion yourself, step by step. Each step names the page that explains it in full.

**1. Make the project and copy the env.** Create the project with its archive (the backup copy of your data and results). Keep the project on a local disk; the archive can be on a network share:

```bash
wfc init --dir plate-assay --archive ~/wfc-archives/plate-assay
cd plate-assay
wfc doctor
```

Every method runs in a Linux container built from an env. The analysis runs in the conda env `plates-lab`, which has no `wfc-client`, the small library a method script uses to read its inputs and save its outputs. Leave `plates-lab` as it is and make a copy with `wfc-client` added. Build the copy from the packages `plates-lab` asked for, at the versions it has (`conda env export -n plates-lab --from-history` lists them, `conda list -n plates-lab` gives the versions):

```bash
conda create -n plates-lab-wfc -c defaults python=3.12.16 numpy=2.5.4 pandas=3.1.0 matplotlib=3.11.1 nbconvert=7.17.2 ipykernel=7.3.0
conda run -n plates-lab-wfc pip install wfc-client
wfc register-env plates-lab-wfc conda:plates-lab-wfc
```

Don't copy it with `conda create --clone`: on Windows and macOS the clone's image doesn't build. Registering it took about 5 minutes here, while wfc worked out the Linux versions of its packages and built an image from them; [Register an Environment](registering-an-environment.md) explains what it records.

**2. Register the module and the plates.** A plate's `cells.csv` only means something with its `plate.json` (the well layout and the background), so each plate's folder becomes one sample:

Create `src/plate_assay/module.yaml` holding one line, `description: Plate assay`, then:

```bash
wfc register-module --name plate_assay --module-dir src/plate_assay
wfc register-sample --name plate1 --source ../plate_assay/data/plate1
wfc register-sample --name plate2 --source ../plate_assay/data/plate2
```

**3. Split the notebooks into methods.** `01_process_plates` holds three steps; `02_compare_plates` is a fourth. The bootstrap is slow, so it gets a method of its own: a change to the plot then never reruns it.

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

Three changes go beyond the edges, and each is one you decide:

- **The seed.** The notebook's bootstrap was unseeded, so every run gave slightly different intervals. The method takes a `seed` parameter.
- **The shared helper.** Three methods import `plate_utils.py`. Each method folder holds a copy of it, listed under `helpers:`, and `script:` names the main script.
- **The filter that does nothing.** `02_compare_plates` builds `combined_clean = combined[combined["n_cells"] >= 50]` and then uses `combined`, so the filter never applies. `compare_plates` keeps the notebook's result by default (`min_cells: 0`) and lets you switch it on (`min_cells: 50`). Changing it is the analysis owner's call.

The whole folder is in [`examples/single-cell-drug-response-pipeline`](https://github.com/ddpoe/workflow-canvas/tree/main/examples/single-cell-drug-response-pipeline). [Write a Method in Python](authoring-a-method-script.md) covers the script, and the [method.yaml Schema](../reference/method-yaml-schema.md) every key.

**4. Try each method as a draft, upstream first.** `wfc try` runs a folder on a sample and records nothing. Each try prints a draft string, the name of that draft's result (`draft:<module>.<method>/<node>@<sample>`); the next method reads its upstream's output by passing it to `--parent-run-id`:

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
{"id": "bootstrap", "module": "plate_assay", "method": "bootstrap_ci", "version": "draft",
 "params": {"n_boot": 200000, "seed": 0}}
```

```bash
wfc check-pipeline --pipeline pipelines/plates-draft.json
wfc run-pipeline --pipeline pipelines/plates-draft.json
```

Draft steps never use the cache, so this reruns the bootstrap each time. Then try `compare_plates` over both plates' bootstrap drafts, one `--parent-run-id` per plate on its `bootstraps` input:

```bash
wfc try src/plate_assay/compare_plates --module plate_assay --sample plate1 \
  --parent-run-id bootstraps:bootstrap:draft:plate_assay.bootstrap_ci/bootstrap@plate1 \
  --parent-run-id bootstraps:bootstrap:draft:plate_assay.bootstrap_ci/bootstrap@plate2
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
wfc run-pipeline --pipeline pipelines/plates.json --no-archive
```

**8. Compare the plates.** A pipeline can't run each plate and then combine across plates, so the comparison is a second pipeline over the first one's runs: `pipelines/compare.json` feeds the two `bootstrap_ci` runs into `compare_plates` with run-reference nodes. Its `run_id` values are the example's; put in your own from `wfc runs`. The comparison runs once per registered sample, each time over both tables, so expect two identical `compare_plates` runs. Run it, then archive the outputs:

```bash
wfc run-pipeline --pipeline pipelines/compare.json
wfc cache archive
```

A run reference reads its run's outputs only until they are archived, so run the first pipeline with `--no-archive` and archive after the comparison. To compare again later as new plates arrive, export each plate's `bootstrap.csv` from its run, register it as a sample (`wfc register-sample --name plate1_bootstrap --source <file>`), feed `compare_plates` from those samples, and note in your record which run each sample came from. The [Pipeline Document](../reference/pipeline-document.md) reference lists every key both files use.

## The Two Results Side by Side

Both tracks were run on this folder. The agent column is one evaluation run of the skill; the by-hand column is the steps above.

| | By an agent | By hand |
|---|---|---|
| Steps | `normalize_plate`, `bootstrap_ci` (alone, so it caches), `plot_fold_change`, `compare_plates` | the same four |
| Settings | `min_area` 35, `n_boot` 200000, `seed` 0 (new), `min_cells` 0 | the same |
| The plate's name for the chart title | a `plate` column added to the bootstrap table | the plate's folder as a second input of the chart |
| Shared helper `plate_utils.py` | copied, unchanged | copied, unchanged, into each method folder |
| Env | a copy of `plates-lab` with `wfc-client` added; `plates-lab` unchanged | the same |
| Combining the plates | a second pipeline over the first one's results | a second pipeline over the first one's runs |
| The unused filter | flagged; `min_cells` 0 keeps the notebook's result | the same |
| Results against `results/` | tables equal, `comparison.png` identical, intervals within 6e-4 | the same |
| Record of the changes | `wfc-intake/`: the conversion, the env, each method, and a diff per script | what you write yourself |

The two pipelines differ only in naming and in where the plate's name travels; the methods, settings and results are the same.

## Next Steps

- [Your First Pipeline](getting-started.md) builds a pipeline from scratch, and [Build Your Own Pipeline](../how-to/build-your-own-pipeline.md) lists the steps for your own analysis.
- [Draft a Method](drafting-a-method.md) explains drafts and `wfc try`.
- [Pipeline Document](../reference/pipeline-document.md) lists every key of the pipeline files.
