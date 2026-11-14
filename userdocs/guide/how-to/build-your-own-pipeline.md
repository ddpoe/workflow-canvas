<!-- generated from pm_mvp::docs/consumer/how-to/build-your-own-pipeline @ d3f299cef500; do not edit -->

# Build Your Own Pipeline

## Build your own pipeline

You have run [Your First Pipeline](../tutorials/getting-started.md). These are the same steps for your own analysis, in order, with the page that covers each one.

| Step | What you do | Command | Page |
|---|---|---|---|
| 1 | Package the software your methods run with | `wfc register-env` | [Register an Environment](../tutorials/registering-an-environment.md) |
| 2 | Group your methods, and optionally require outputs from all of them | `wfc register-module` | [Register a Module](register-a-module.md) |
| 3 | Write each step: its folder, `method.yaml` and script; try it as a draft, then register it | `wfc try`, `wfc register-method` | [Write a Method](write-a-method.md) |
| 4 | Add your data | `wfc register-sample` | [Register Samples](register-samples.md) |
| 5 | Wire the steps into a pipeline | the canvas Builder, or a pipeline file | [Wire a Pipeline](wire-a-pipeline.md) |
| 6 | Run it and look at the results | **Run** in the canvas, or `wfc run-pipeline` | [Run and Inspect Your Pipelines](run-and-inspect.md) |

:::{admonition} An AI agent can do this with you
:class: agent-tip

An agent with the wfc plugin can run these steps for you, from one script or a whole folder of notebooks, and asks before it registers anything. It keeps a written record of every change to your code; see [Work with an AI Agent](../tutorials/work-with-an-ai-agent.md).
:::

 To see the steps done on a whole example analysis, by an agent and by hand, see [Convert an Analysis into a Pipeline](../tutorials/convert-an-analysis.md).

## Going further

- [Let Users Pick Columns in the Canvas](pick-columns-in-the-canvas.md): declare the names an output offers (column names, gene names, sample labels), so a downstream parameter can offer them as a list to pick from.
- [Share a Project](sharing-a-project.md): let someone else open your project and rerun what you ran.

<!-- group_index: nested pages -->
```{toctree}
:hidden:
:numbered: 1

/tutorials/registering-an-environment
/how-to/register-a-module
/how-to/write-a-method
/how-to/register-samples
/how-to/wire-a-pipeline
```

```{toctree}
:hidden:

/how-to/pick-columns-in-the-canvas
```
