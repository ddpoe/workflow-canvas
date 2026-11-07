<!-- generated from pm_mvp::docs/consumer/how-to/cli-export @ 216ea76565d8; do not edit -->

# Export a Run's Outputs

## Export a run's outputs

Outputs in the cache are read-only. `wfc export` copies them out as normal files you can edit, or prints where they are so you can read them in place. Run it as:

```bash
wfc export --run <run-id> --slot <slot> --dest <dest>
```

- `--run <run-id>`: the numeric ID of the run (`wfc runs` lists them).
- `--slot <slot>`: the name of the output slot to export (leave it out to list them).
- `--dest <dest>`: where to put the copy, a directory or a file path.

A cached run exports the outputs of the run it reused. See the [Cache and Export Commands](../reference/cli-cache-export.md#wfc-export) for every option.

### List a run's outputs

```bash
wfc export --run 412
```

With only a run ID, the command lists the run's output slots and the file each was saved as.

### Copy one output

```bash
wfc export --run 412 --slot counts --dest results/
```

This copies the `counts` output of run 412. If `results/` is an existing directory, the file lands inside it under its original name; otherwise the copy is written to that path. If a file is already there, add `--force` to overwrite it.

### Copy every output

```bash
wfc export --run 412 --all --dest results/
```

With `--all`, leave out `--slot` and give a directory as `--dest`. Each output goes into it under its file name, and a directory output under its own folder. If any target already exists, nothing is copied until you add `--force`.

### Read a large output in place

```bash
p=$(wfc export --run 412 --slot masks --path)
```

`--path` prints the output's local path instead of copying, so leave out `--dest`. Nothing else goes to stdout, so it works in scripts. The file is read-only: you can read it, but writing to it fails. With `--all`, `--path` prints one `name<TAB>path` line per output.

If a run's outputs haven't been archived yet (for example after `wfc run-pipeline --no-archive`), export tells you to run `wfc cache archive` first.
