<!-- generated from pm_mvp::docs/consumer/how-to/register-samples @ b93daf3d5574; do not edit -->

# Register Samples

## Registering a Sample

A **sample** is a data file or a directory of files that a pipeline starts from.

### Command

```bash
wfc register-sample --name CFPAC_ERKi --source /data/raw/cfpac_erki.csv
```

`--source` can be a file or a directory. Sample names must be unique; registering a name that already exists is refused.

The project needs its archive set up, which `wfc init` does.

### What registration does

wfc copies the source into the project's archive. The original file stays where it is.

### Adding a description

Pass `--manifest <file>` to attach a description. The file is a small YAML file:

```yaml
description: CFPAC cells treated with ERK inhibitor, replicate 1
```

The description is shown when you pick samples in the canvas.

Pipelines fetch the samples they read when they run, from the archive when a sample is not on this machine, so you never copy data into the project yourself.
