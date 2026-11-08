<!-- generated from pm_mvp::docs/consumer/tutorials/drafting-a-method @ a5612ce18ecf; do not edit -->

# Draft a Method

## Overview

A *draft* is a method folder you run before you register it. It runs on your data, in the environment its `method.yaml` names, as a pipeline step would. Nothing it does is recorded: no run, no cache entry, no commit. When it does what you want, you register it once, and its runs are recorded from then on.

You can work with a draft from the command line or in the canvas; both use the same draft:

- [Draft from the Command Line](../how-to/draft-from-the-command-line.md): try a folder on a sample with `wfc try`, look at its outputs, register it.
- [Draft in the Canvas](../how-to/draft-in-the-canvas.md): make, check and run a draft in a pipeline, then register it.

An AI agent with the wfc plugin works the same way: it tries a draft on your data until it runs, and registers it only when you say yes ([Work with an AI Agent](work-with-an-ai-agent.md)).

## A New Method or the Next Version

The folder's name decides what the draft is. A folder named after a method the module already has becomes that method's **draft version**: registering it adds the next version, such as v2. A folder with a new name becomes a **draft method**: registering it adds the method as v1. Each method has one draft, from one folder.

## Edit the Folder, Run It Again

A draft runs its folder as it is now. Edit the script and run the draft again; nothing else is needed. A changed `method.yaml` is read again before the next run, which makes the same checks registering makes (see [When a Draft Cannot Run](#when-a-draft-cannot-run)).

## Where Draft Results Are

Only a draft's latest run is kept: the next run of the draft replaces it, from any pipeline. Draft steps are listed beside the recorded runs. On the command line, `wfc runs` lists each one with its draft string, such as `draft:my_analysis.filter_data/filter_data@scores`, which `wfc export --run` takes. In the canvas, History marks them **DRAFT**, and the draft's **Draft run** tab in the Registry shows its last run.

## Draft Mode in a Pipeline

A pipeline that holds a draft runs in Draft mode: the draft's step and every step after it run as drafts. They run every time, never read or fill the cache, and only their latest results are kept. Steps before the draft run as usual and can be reused from the cache.

## When a Draft Cannot Run

A draft can be stopped in two ways, and the message says which and why:

- **The folder fails the registration checks.** Before a draft runs, wfc checks its `method.yaml`, script and env as registering would, and refuses the run naming the check that failed. The rules are on [Write a Method](../how-to/write-a-method.md): the contract, registering it, and when it is checked. The canvas marks such a draft **⚠ invalid**.
- **A pipeline no longer fits the draft.** After a change to `method.yaml`, a pipeline that wires an input or sets a parameter the draft no longer declares is refused, naming the node and the field. The canvas marks the node **⚠ refused**.

Fix what the message names and run again.

## Register It

Registering copies the folder into the project, commits it to git and adds the version. From then on the method's runs are recorded and cached. Registering from the canvas also removes the draft; after `wfc register-method` the draft stays until you remove it with `wfc delete-draft`. The draft's results are not kept as runs: the first recorded run computes again.

To change the method later, edit the folder and draft it again: it becomes the draft of the next version.

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/how-to/draft-from-the-command-line
/how-to/draft-in-the-canvas
```
