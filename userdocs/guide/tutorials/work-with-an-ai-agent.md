<!-- generated from pm_mvp::docs/consumer/tutorials/work-with-an-ai-agent @ d6c2f495bff8; do not edit -->

# Work with an AI Agent

## Overview

The wfc plugin teaches an AI agent how Workflow Canvas works. With it, you can ask in plain words to set up a project, turn one of your scripts into a method, turn a whole analysis into a pipeline, run it, or explain why a run failed. Where the agent runs on your computer, as Claude Code and Codex do, it runs the same `wfc` commands you would and asks you before anything is registered. In a chat on claude.ai or ChatGPT, it answers your questions about wfc, writes method folders and pipeline files for you to download, and gives you the commands to run yourself.

What the agent builds is an ordinary wfc method: it appears in the canvas Registry with its code, runs in its own container, and every run is recorded in History, so you can see and check what it built.

This tutorial adds the plugin, then walks through a first session with an agent that runs on your computer. You need wfc installed and Docker running, as in [Your First Pipeline](getting-started.md).

## Add the Plugin

The plugin holds three skills, and the agent uses whichever fits what you ask:

- **using-wfc**: working with wfc: its model, its commands, setting up a project, running pipelines and reading their results, and working out why something failed.
- **creating-a-wfc-method**: bringing a script into wfc as a method, trying it as a draft before anything is registered.
- **converting-an-analysis-to-a-wfc-pipeline**: handing over a whole analysis, a folder of notebooks and scripts, and getting back a pipeline you approved step by step, with a record of every change ([Convert an Analysis into a Pipeline](convert-an-analysis.md)).

Where the agent runs on your computer, it runs `wfc` for you. A chat runs on the provider's servers: it cannot see your project or run `wfc`, but it answers questions about wfc and gives you the commands to run yourself. For a chat, download the plugin for this version of the docs and upload it as the tables below say:

[Download wfc-plugin.zip](../wfc-plugin.zip){.download-button}

### Claude

| Where | What it can do | How to add it |
|---|---|---|
| [Claude Code]{.product-name}, in a terminal or an editor | runs wfc for you | the two commands below; [Claude Code's plugin guide](https://code.claude.com/docs/en/plugins/install#add-a-marketplace) covers the rest |
| The [Claude desktop app]{.product-name}'s Code tab | runs wfc for you | the plugin browser, as [Anthropic's desktop app guide](https://code.claude.com/docs/en/desktop#install-plugins) shows |
| [claude.ai]{.product-name}, or a chat in the [Claude desktop app]{.product-name} | answers questions | upload wfc-plugin.zip, as [Anthropic's guide to plugins](https://claude.com/docs/plugins/overview#find-and-add-a-plugin) shows |

```{code-block} bash
:caption: Install the wfc plugin in Claude Code (run each line in a terminal)

claude plugin marketplace add ddpoe/workflow-canvas
claude plugin install wfc@workflow-canvas
```

Already in a Claude Code session? Type the same two commands there with `/plugin` in place of `claude plugin`.

### ChatGPT and Codex

| Where | What it can do | How to add it |
|---|---|---|
| [Codex]{.product-name} | runs wfc for you | the two commands below; [OpenAI's plugin guide](https://learn.chatgpt.com/docs/plugins) covers the rest |
| [ChatGPT]{.product-name} | answers questions | upload wfc-plugin.zip, as [OpenAI's guide to plugins in ChatGPT](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt#upload-or-update-a-plugin-zip-file) shows |

```{code-block} bash
:caption: Install the wfc plugin in Codex (run each line in a terminal)

codex plugin marketplace add ddpoe/workflow-canvas
codex plugin add wfc@workflow-canvas
```

### Other agents

The skills are plain folders, each with a `SKILL.md`. If your agent reads skills in that format, unzip wfc-plugin.zip and copy the folders under `skills/` into its skills folder; otherwise, give it the three `SKILL.md` files to read.

The skills read the documentation for the version of wfc you have installed.

## Set Up a Project

Start the agent in the folder for your project and ask:

```text
Set up a wfc project here.
```

The agent runs `wfc init` and `wfc doctor`, and fixes or explains each line `wfc doctor` does not report as OK. It asks you where to keep the archive, and which environment your methods run in, then registers that environment and a module.

Commands that change something, such as `wfc init` in an existing project or `wfc register-method`, ask before they act. The agent first runs them without `--yes` to show you what they will do, and passes `--yes` only after you agree.

## Bring a Script In as a Method

Name a script and the data it should run on:

```text
Turn scripts/filter.py into a wfc method that runs on my scores sample.
```

The agent:

1. reads the script for its inputs, outputs and parameters, and for anything that works only on your machine, such as a host path;
2. writes a method folder, by convention `src/<module>/<method>/`, with a `method.yaml` and a copy of the script changed only at its edges, and leaves your original script as it is;
3. fixes what would make the same run give different files, such as a random seed that is never set or a PDF that stores the time it was made, and tells you what it changed and why ([Write a Method in Python](../tutorials/authoring-a-method-script.md#writing-outputs-that-rerun-the-same) lists the usual causes);
4. shows you the `method.yaml` and the changes to the script, listing every change to what it computes, reads or writes;
5. tries the folder on your sample with `wfc try`, fixes it until it runs, and exports an output for you to check;
6. suggests keeping it as a draft while you check the outputs, including in the canvas, and registers it only when you say yes;
7. keeps a record from the first try, in `wfc-intake/<module>.<method>.md`: where the method came from, every change to your script and why, the try results, and how they compare with your script's own outputs when you have them. How your machine and the method's container differ goes in `wfc-intake/envs/<env>.md`, once per env. Each registered version gets its own dated section, newest first. wfc doesn't read these files; commit them with your method folder.

What a draft is, and how to try one yourself, is on [Draft a Method](../tutorials/drafting-a-method.md).

## Hand Over a Whole Analysis

Name the folder and the env you run it in:

```text
here is my analysis: ~/plate_assay. can you turn it into a wfc pipeline? I run it in my conda env plates-lab.
```

The agent works through it in stages and stops for you where the last column says so:

| Stage | What the agent does | What you do |
|---|---|---|
| Ask | Asks which results you use and which parts belong in wfc now | Answer |
| Run the original | Runs a copy of your analysis to keep its outputs for the comparison; your folder stays as it is | |
| Map | Proposes the steps, envs, samples and wiring | Approve or change the map |
| Envs | Registers the envs first, because building one is the slowest step | |
| Methods | Writes each method and tries it as a draft; where a step needs a judgement, such as a threshold picked by eye, shows the options side by side | Pick |
| Compare | Runs the whole pipeline as drafts and compares its results with your original's | Check the comparison |
| Register | Registers the methods and the pipeline | Say yes |

It keeps `wfc-intake/conversion.md` for the whole conversion beside the per-method and per-env records. [Convert an Analysis into a Pipeline](convert-an-analysis.md) walks through it on an example, by an agent and by hand.

## Run It and Read the Results

Once the method is registered, the agent runs it once through a pipeline, so the run is recorded, and reads the result with `wfc runs` and `wfc show-run`. You can open the same runs in the canvas's History; see [Run and Inspect Your Pipelines](../how-to/run-and-inspect.md).

## When Something Fails

Ask the agent why. It runs `wfc doctor` first, to rule out setup such as Docker not running, then reads the failed run's log with `wfc show-run --run <run id> --log stderr`. For a problem in the method, it edits the method folder, tries it again with `wfc try` and registers the fix as a new version, when you agree. To check how wfc behaves before it answers, it uses a throwaway project rather than yours.

If it thinks wfc itself is wrong, it says so at the end of the task, with what it expected, and offers to draft an issue on GitHub. It files the issue only when you agree.
