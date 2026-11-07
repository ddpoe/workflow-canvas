<!-- generated from pm_mvp::docs/consumer/how-to/cli-hub @ 19c47b025071; do not edit -->

# Use the Command Line

## Run and inspect from the command line

Run every command from your project root (for example `wfc_project_root/`).

- [Run a Pipeline](cli-run.md): `wfc run-pipeline` for a pipeline file, and `wfc run-method` for one method on one sample.
- [See What Ran](cli-runs.md): `wfc runs` and `wfc show-run`.
- [Export a Run's Outputs](cli-export.md): copy a run's output files out with `wfc export`.
- [Share Runs as a Bundle Anyone Can Rerun](share-bundle.md): package runs, with the samples they read, as a zip with `wfc export-run`.


## Next steps

- [Use the Canvas](canvas-hub.md): build pipelines, run them over many samples and parameter values, and browse runs in the History tab.
- [Caching & Reproducibility](../explanation/caching-and-reproducibility.md): when a step is reused and how a run traces back to its samples.
- [Storage & Provenance](../explanation/storage-and-provenance.md): where outputs are stored and how to keep them recoverable.
- [Command Line](../reference/command-line.md): every `wfc` command and flag.

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/how-to/cli-run
/how-to/cli-runs
/how-to/cli-export
```
