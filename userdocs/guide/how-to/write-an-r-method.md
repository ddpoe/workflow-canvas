<!-- generated from pm_mvp::docs/consumer/how-to/write-an-r-method @ a544701b5fa8; do not edit -->

# How-to: Write an R Method

## Write an R Method

An R method is an R script and a `method.yaml` in a method folder. wfc runs the script with `Rscript` inside the method's environment. The script reads its input paths and parameters from environment variables and writes its outputs into the run directory.

This page writes the `filter_data` method from [Writing a Method in Python](../tutorials/authoring-a-method-script.md) in R: it keeps the rows of a CSV whose `score` is above a threshold, and also writes one CSV per value of its `group` column. It assumes a project with the module `my_analysis` registered; [Your First Pipeline](../tutorials/getting-started.md) sets one up.


## Build an R environment

The script reads JSON with the `jsonlite` package, so the environment needs R, `jsonlite` and the packages your analysis uses. Register it with `--interpreter` pointing at `Rscript`; the default interpreter is Python.

### From a rocker image

The [rocker](https://rocker-project.org/) `r-ver` images hold R and its base packages. Use a versioned tag, and add your packages in a Dockerfile, for example `envs/r-analysis/Dockerfile`:

```dockerfile
FROM rocker/r-ver:4.3.1
RUN install2.r --error jsonlite
```

Build it under a `local/` name and register it as a `byo` env:

```bash
docker build -t local/r-analysis:1 envs/r-analysis
wfc register-env r-analysis --backend byo \
  --image docker://local/r-analysis:1 --interpreter Rscript
```

Give each rebuild a new tag (`:2`, `:3`). Each method version runs the image it was registered with, and a reused tag replaces that image.

### From a pixi project

pixi installs packages from conda-forge, which carries CRAN packages under an `r-` prefix (`dplyr` is `r-dplyr`) and Bioconductor packages as `bioconductor-*`. Add `python` and `pip` next to R, because the image build installs the project's pip packages with them:

```bash
pixi init r-env --platform linux-64 --platform win-64
pixi add --manifest-path r-env/pixi.toml r-base r-jsonlite python pip
wfc register-env default --backend pixi --from r-env/pixi.lock \
  --interpreter /opt/.pixi/envs/default/bin/Rscript
```

The env's name is the pixi environment in the lock, `default` for a new project, so the method's `method.yaml` says `env: default`. [Registering an Environment](../tutorials/registering-an-environment.md) covers every backend.


## Declare the slots

Create `src/my_analysis/filter_data/method.yaml`:

```yaml
inputs:
  data:
    type: csv
    description: Scored rows to filter.

outputs:
  filtered:
    type: csv
    description: Rows whose score exceeds the threshold.
  by_group:
    type: dir
    description: One CSV per group.

params:
  threshold:
    type: float
    default: 0.5

env: r-analysis
```

Each output is a file named after its slot and type: `filtered` is `filtered.csv`. A `dir` output is a folder named after the slot, here `by_group`. Every key is listed in the [method.yaml Schema](../reference/method-yaml-schema.md).


## Write the script

Create `src/my_analysis/filter_data/filter_data.R`:

```r
inputs  <- jsonlite::fromJSON(Sys.getenv("WFC_INPUT_PATHS"))
params  <- jsonlite::fromJSON(Sys.getenv("WFC_PARAMS"))
run_dir <- Sys.getenv("WFC_RUN_DIR")

data <- read.csv(inputs$data[1])
kept <- data[data$score > params$threshold, ]

write.csv(kept, file.path(run_dir, "filtered.csv"), row.names = FALSE)

out_dir <- file.path(run_dir, "by_group")
dir.create(out_dir)
for (g in unique(kept$group)) {
  write.csv(kept[kept$group == g, ], file.path(out_dir, paste0(g, ".csv")),
            row.names = FALSE)
}

results <- list(
  outputs = list(filtered = "filtered.csv", by_group = "by_group"),
  metrics = list(kept_rows = nrow(kept))
)
jsonlite::write_json(results, file.path(run_dir, "_wfc_results.json"),
                     auto_unbox = TRUE)
```

| Value | Contents |
|---|---|
| `inputs$data` | The paths wired into the `data` slot, as a character vector. A slot that collects several files has one path per file. |
| `params$threshold` | The node's value for `threshold`. JSON numbers arrive as numeric and `true`/`false` as logical. A parameter left unset is there at its `default`. |
| `run_dir` | The run directory. Write every output inside it. |

The [Method Script Contract](../reference/method-script-contract.md) lists every variable wfc sets.

If the script stops with an error, `Rscript` exits non-zero and the step fails; the R error is in the run's log. A declared output that is missing when the script exits also fails the step.


## Record metrics

The last lines of the script write `_wfc_results.json` into the run directory, which records metrics with the run:

```r
results <- list(
  outputs = list(filtered = "filtered.csv", by_group = "by_group"),
  metrics = list(kept_rows = nrow(kept))
)
jsonlite::write_json(results, file.path(run_dir, "_wfc_results.json"),
                     auto_unbox = TRUE)
```

`outputs` maps each slot to its path inside the run directory, and `metrics` maps each name to a number or short string. Pass `auto_unbox = TRUE`: without it `jsonlite` writes every value as a one-element array (`"filtered": ["filtered.csv"]`) and the step fails. A script that records no metrics can leave the file out.


## Try the script before registering

`wfc exec` runs a command in the env's image, with the project folder as the working directory. Save a small R file in the project root, outside the method folder, that sets the variables wfc would set and runs the script. `try_filter_data.R`:

```r
Sys.setenv(
  WFC_RUN_DIR     = "try-run",
  WFC_INPUT_PATHS = '{"data": ["raw/scores.csv"]}',
  WFC_PARAMS      = '{"threshold": 0.5}'
)
dir.create("try-run", showWarnings = FALSE)
source("src/my_analysis/filter_data/filter_data.R")
```

Paths are relative to the project folder. Run it:

```bash
wfc exec r-analysis Rscript try_filter_data.R
```

The outputs and `_wfc_results.json` appear in `try-run/`.


## Register and run

```bash
wfc register-method src/my_analysis/filter_data --module my_analysis
```

wfc finds `filter_data.R` because it is named after the folder; for another name, set `script:` in `method.yaml`. Other `.R` files in the folder register as part of the method (see [script and helpers](../reference/method-yaml-schema.md#script-and-helpers)).

Add the method to a pipeline in the canvas, or in a `pipeline.json` as in [Your First Pipeline](../tutorials/getting-started.md), and run it like any other method. A pipeline can connect Python and R methods: each step runs in its own method's environment, and steps pass files, so connect them through formats both languages read, such as CSV.


## Next steps

- Every variable wfc sets and the results file: [Method Script Contract](../reference/method-script-contract.md).
- Every `method.yaml` key: [method.yaml Schema](../reference/method-yaml-schema.md).
- Column declarations and column pickers: [Writing Contracts](../tutorials/writing-contracts.md).

