<!-- generated from pm_mvp::docs/consumer/how-to/canvas-hub @ 5e6de85cb607; do not edit -->

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


## The version menu

The `v<version>` label beside the Workflow Canvas title names the wfc you have installed. It shows on every tab. Click it to open the version menu:

- **Update available**, when a newer wfc is out: the command that installs it, a link to update the wfc plugin too, and what the new version changes (its Upgrading notes and known issues). A dot on the label marks an update you have not looked at yet; it goes once you open the menu, and comes back for the next release.
- **What's new**: the notes of the version you have, Upgrading first and known issues last. After an upgrade it opens by itself once, listing every version since the one you last used in this browser.
- **Known issues**, with how many there are: the same notes, opened at the known issues of the version you have.
- **Report a bug**: opens a new GitHub issue in your browser with wfc's version, your operating system and your Python and Docker versions filled in. You write the rest; nothing is sent until you submit it on GitHub.
- Links to the guide, the [Known Issues](../reference/known-issues.md) page and the full changelog.

The canvas asks PyPI for a newer wfc at most once a day. Which releases it offers, and how to change that, is in [wf-canvas.toml](../reference/wf-canvas-toml.md#updates). The line at the bottom of the menu says which applies and what to set to change it. Offline, the check finds nothing and the canvas works as usual.

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/how-to/check-and-run
/how-to/browse-history
/how-to/run-details
/how-to/browse-the-registry
```
