<!-- generated from pm_mvp::docs/consumer/reference/method-script-contract @ 57a6dd49f8f9; do not edit -->

# Reference: Method Script Contract

## Overview

A method is a process that reads a few environment variables and writes files. wfc sets the variables when it starts the step, and collects the files when the script exits. A method can be written this way in any language, with no wfc package installed.

The Python package `wfc-client` ([Python API](wfc-client-api.md)) is a layer over this contract. [Write an R Method](../how-to/write-an-r-method.md) uses it from R.


## What wfc gives the method

| Variable | Contents |
|---|---|
| `WFC_RUN_DIR` | The run directory. Write your outputs here. |
| `WFC_INPUT_PATHS` | JSON `{slot: [paths]}`: the files (or directories) wired into each input slot. |
| `WFC_PARAMS` | JSON `{name: value}`: the node's parameter values. A parameter left unset is there at its declared default. |
| `WFC_RUN_ID` | The run's id. |
| `WFC_SAMPLE` | The sample this run is for (`__all__` for a step that bundles several samples). |
| `WFC_NODE_ID` | The node's id in the pipeline. |
| `WFC_PIPELINE_ID` | The pipeline run's id. |
| `WFC_VARIANT` | The parameter variant's name. |


## What the method gives back

- Write each output into `WFC_RUN_DIR`, named after its slot plus the slot's `type`: the `filtered` output with `type: csv` is `filtered.csv`. A `dir` output is a directory named after the slot.
- Exit 0 on success and non-zero on failure.
- Anything printed to stdout or stderr is kept in the run's log.

Every declared output must be there when the method exits, or the step fails. Other files in the run directory are ignored.


## The results file

To record metrics, write `_wfc_results.json` into `WFC_RUN_DIR`:

```json
{"outputs": {"filtered": "filtered.csv"}, "metrics": {"kept_rows": 42}}
```

`outputs` maps each slot to its path, relative to the run directory, so a file listed here can have any name inside it. Each value is a single string, and each metric a number or short string. Without this file, wfc looks for each output under its slot name and extension.


## Examples

Each example keeps the rows of the `data` input whose `score` is above the `threshold` parameter, and writes them to the `filtered` output.

### Python with the standard library

```python
import csv
import json
import os
from pathlib import Path

run_dir = Path(os.environ["WFC_RUN_DIR"])
inputs = json.loads(os.environ["WFC_INPUT_PATHS"])
params = json.loads(os.environ.get("WFC_PARAMS", "{}"))

threshold = float(params["threshold"])

with open(inputs["data"][0], newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = [r for r in reader if float(r["score"]) > threshold]

with open(run_dir / "filtered.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
```

### R

```r
inputs <- jsonlite::fromJSON(Sys.getenv("WFC_INPUT_PATHS"))
params <- jsonlite::fromJSON(Sys.getenv("WFC_PARAMS"))

rows <- read.csv(inputs$data[1])
write.csv(rows[rows$score > params$threshold, ],
          file.path(Sys.getenv("WFC_RUN_DIR"), "filtered.csv"), row.names = FALSE)
```

The environment needs `jsonlite`; [Write an R Method](../how-to/write-an-r-method.md) builds one.

### bash

This example uses `jq`, so the environment needs it installed:

```bash
input=$(jq -r '.data[0]' <<< "$WFC_INPUT_PATHS")
threshold=$(jq -r '.threshold' <<< "$WFC_PARAMS")
awk -F, -v t="$threshold" 'NR == 1 || $2 > t' "$input" > "$WFC_RUN_DIR/filtered.csv"
```

The bash example assumes `score` is the second column.

On Windows, save `.sh` scripts with LF line endings (for example with `git config core.autocrlf input`). A bash script with CRLF line endings fails in the container, and the step reports that it did not produce its declared slot.


## Which interpreter runs the script

A method script runs under the interpreter its environment was registered with: the environment's Python by default, or the program named with `wfc register-env --interpreter`, such as `Rscript` or `/bin/bash`. See [Registering an Environment](../tutorials/registering-an-environment.md).

