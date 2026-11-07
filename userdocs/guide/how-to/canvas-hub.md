<!-- generated from pm_mvp::docs/consumer/how-to/canvas-hub @ df7c8766a4a3; do not edit -->

# Use the Canvas

## Overview

Canvas is the browser interface for building, running and inspecting pipelines. Start it from your project root:

```bash
wfc canvas
```

It serves at `http://127.0.0.1:8500`. Use `--port` to serve on another port. The top bar has three tabs:

- **Builder** -- wire methods into a pipeline, set their parameters, check what will run, and run it.
- **History** -- browse past runs, inspect their parameters, metrics, logs and output files, and bring a run back into the Builder. A pipeline that is still running can be watched in the Builder or stopped from here.
- **Registry** -- browse and register the project's modules, methods, environments and samples.

These pages cover each tab:

- [Check and Run a Pipeline](check-and-run.md): check what will run, run it, and follow it in the Builder.
- [Browse Past Runs in History](browse-history.md): find past runs and bring one back into the Builder.
- [Look at a Run's Results](run-details.md): a run's parameters, metrics, logs and output files.
- [Browse and Register in the Registry](browse-the-registry.md): the project's modules, methods, environments and samples.
- [Draft in the Canvas](draft-in-the-canvas.md): make, check and run a method's draft before you register it.

Building a pipeline in the Builder is covered step by step under [Wire a Pipeline](wire-a-pipeline.md). To run and export from a terminal instead, see [Use the Command Line](cli-hub.md).

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/how-to/check-and-run
/how-to/browse-history
/how-to/run-details
/how-to/browse-the-registry
```
