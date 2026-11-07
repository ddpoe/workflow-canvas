<!-- generated from pm_mvp::docs/consumer/reference/cli-cache-export @ a656f5007f95; do not edit -->

# Cache and Export Commands

## Cache and Export Commands

### wfc cache archive

Archive run outputs that are not archived yet. Use it after `wfc run-pipeline --no-archive`. It prints progress per file and saves each one as it finishes, so after an interruption, running it again archives only what is left.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache archive
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cache prune

Free disk space. By default, `wfc cache prune` removes run directories under `.wfc/runs/` that no recorded output needs. It lists what it will delete and asks before deleting; `--yes` answers yes. With no terminal (a script or CI job) and no `--yes`, it deletes nothing and tells you to pass `--yes`.

Before deleting anything it checks that the archive is reachable, and stops if it is not. Runs with outputs that are not archived yet are skipped with a warning; run `wfc cache archive` first. With `--include-local`, entries that have not been pushed to the archive are kept, whether or not an archive is configured and even with `--force`; prune counts them and names `wfc cache push`. Entries the archive already holds are removed, and are pulled back when something reads them.

The prune also empties the canvas's saved image previews (see `[previews]` in [wf-canvas.toml](wf-canvas-toml.md)); the canvas makes them again the next time you open them.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache prune
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc cache push

Push everything the archive does not hold yet (outputs, samples, environments and pipeline documents) now. Pushing otherwise happens only while a pipeline runs, so use this after registering a sample or an environment between runs, after an archive location is configured, or after a push failed. It needs a configured archive location and stops, changing nothing, when there is none. It states how many entries it will push, retries the entries whose earlier attempts ran out, lists every entry it could not push with the reason, and exits 1 if any failed. It asks no confirmation.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: cache push
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc verify

Check that what wfc holds is what it recorded. `wfc verify` checks every sample copy, cached file and output against what wfc recorded, and lists each one that changed, grouped by kind. It exits 1 when anything is wrong and 0 when everything matches, and `--json` prints the report as JSON for scripts.

`wfc verify --repair` puts back what wfc can restore exactly: a changed sample copy, a changed checkout, and a changed cache entry that was pushed (it is fetched again). It lists the rest with what to do: a changed output (run its step again), a changed cache entry that was never pushed, a lost cache entry, and a checkout it cannot rebuild. It still exits 1 while any of those remain.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: verify
   :nodescription:
   :noepilog:
   :nodefault:
```

Before a step reads a sample, an output or a cache entry, wfc already checks it against what it recorded and repairs or refuses a changed one; see the `[integrity]` setting in [wf-canvas.toml](wf-canvas-toml.md).

### wfc export

`wfc export --run <run-id> --slot <slot> --dest <dest>`, where `--run` is the run to export from, `--slot` is the output slot, and `--dest` is where to write the copy. For example, `wfc export --run 412 --slot masks --dest results/masks.png`. Leave out `--slot` to list the run's outputs. `--run` takes a run id or a draft string (`draft:<module>.<method>/<node>@<sample>`), the form `wfc try` prints.

Copy a run's output out of the cache into a file you own, or print where it is (for example, `p=$(wfc export --run 412 --slot masks --path)`). Cache files are read-only (see [Storage & Provenance](../explanation/storage-and-provenance.md)); the exported copy is writable. For a run that reused a cached result, the original run's output is exported.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: export
   :nodescription:
   :noepilog:
   :nodefault:
```

If any requested output cannot be exported, nothing is written and the command exits 1 with the reason.

### wfc runs

List every run in the project, one line each: run ID, pipeline, node, method, sample and status. The METHOD column shows the method and version the run ran, such as `__demo__plotting.__demo__plot v1`. Use it to find the run ID that `wfc export` and `wfc export-run` take. It also lists each draft step that still exists, with its draft string in the RUN column.

`--pipeline ID` lists only that pipeline's runs. `--method MODULE.METHOD` lists only the runs of that method, named with its module (`__demo__plotting.__demo__plot`). The two can be combined.

```bash
wfc runs --method __demo__plotting.__demo__plot
```

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: runs
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc show-run

`wfc show-run --run <run>` shows one run in full: its status, method, sample, pipeline and node, metrics, outputs (slot, state and path), error, the paths of its two logs and the last 40 lines of its stderr. `--run` takes a run id or a draft string (`draft:<module>.<method>/<node>@<sample>`); `wfc runs` lists both. Use it to read why a past run failed.

`--log stdout` or `--log stderr` prints that whole log instead. `--json` prints the detail as one object; it cannot be combined with `--log`. A run that does not exist, or a log that was never written, is an error.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: show-run
   :nodescription:
   :noepilog:
   :nodefault:
```

### wfc export-run

Write runs, every run above them and the samples they read as a zip that anyone with Python 3.11 and Docker can rerun with `python rerun.py`, without wfc. The command prints the plan first. If anything stops the bundle from being complete, it lists every reason and writes nothing. A step whose image was never pushed to a registry is asked about, unless `--with-image` is given; `--yes` answers yes. For the steps to follow and what `DIFFERENT` means, see [Share Runs as a Bundle Anyone Can Rerun](../how-to/share-bundle.md).

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: export-run
   :nodescription:
   :noepilog:
   :nodefault:
```
