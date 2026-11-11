<!-- generated from pm_mvp::docs/consumer/install @ f6325979e562; do not edit -->

# Install Workflow Canvas

## What You Need

Workflow Canvas (wfc) runs on Windows, macOS and Linux. It needs three things on your computer:

| What | Why |
|---|---|
| **Docker**, installed and running | runs each step of a pipeline |
| **git** | records which version of your code made each result |
| **Python 3.12** | runs wfc itself; no other version of Python will do, and both ways of installing wfc below bring it with them |

This page installs each of them, then wfc. If you work with an AI agent, it can take you through the same steps: add the plugin as [Work with an AI Agent](tutorials/work-with-an-ai-agent.md) shows, then ask it to set you up.

## Install Docker

Docker is the program that runs containers. wfc runs each step of a pipeline in one: a sealed copy of the software that step needs, which runs the same on any computer ([How wfc uses Docker](explanation/how-the-pieces-fit-together.md#docker-the-software-and-dependencies)).

- **Windows and macOS**: install Docker Desktop, following Docker's guide for [Windows](https://docs.docker.com/desktop/setup/install/windows-install/) or [Mac](https://docs.docker.com/desktop/setup/install/mac-install/), then start it. On Windows, its first start can ask for an administrator, to turn on WSL 2.
- **Linux**: install [Docker Engine](https://docs.docker.com/engine/install/), then follow Docker's [post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/) so that you can run `docker` without `sudo`.

Docker Desktop is free for personal use, education and small businesses; larger organisations need a paid subscription ([Docker's licence terms](https://docs.docker.com/subscription/desktop-license/)).

Docker has to be running whenever wfc runs a pipeline. Check it:

```bash
docker run --rm hello-world
```

It prints `Hello from Docker!` when Docker is ready.

## Install git

git keeps the history of your code, so wfc can record exactly which version of it produced each result. It all stays on your computer, with no account needed ([How wfc uses git](explanation/how-the-pieces-fit-together.md#git-the-code)).

Install git from [git-scm.com](https://git-scm.com/install/), keeping the installer's suggested settings. Check it:

```bash
git --version
```

## Install wfc

wfc is a Python program. It is installed into an environment: a folder that holds one version of Python and the packages installed for it, kept apart from the rest of your computer.

Make a folder for your Workflow Canvas work, somewhere you keep your own files on a local disk, and open a terminal in it. The projects you create later go inside this folder.

Then install wfc with one of two package managers for Python, uv or conda. Either one installs Python 3.12 and makes the environment. uv keeps wfc inside this folder. Use conda instead if it is what you already work with.

### With uv

[uv](https://docs.astral.sh/uv/getting-started/installation/) is a package manager for Python: it installs Python itself and Python packages. It is one small download, needs no administrator rights, and fetches Python 3.12 for you. Install it:

```{code-block} powershell
:caption: Windows (PowerShell)

powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```{code-block} bash
:caption: macOS and Linux

curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal in your folder, then:

```bash
uv venv --python 3.12 --seed
uv pip install workflow-canvas
```

This puts wfc in a `.venv` folder there, with its own `pip` for upgrading wfc later. Activate it before you use wfc, in this terminal and in every new one:

| Terminal | Command |
|---|---|
| Windows PowerShell | `.venv\Scripts\activate` |
| Windows Command Prompt | `.venv\Scripts\activate.bat` |
| macOS and Linux | `source .venv/bin/activate` |

PowerShell can refuse to activate:

```text
.venv\Scripts\activate : File ...\.venv\Scripts\activate.ps1 cannot be loaded because running
scripts is disabled on this system.
```

Use Command Prompt instead, or allow scripts for your own account, once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### With conda

conda is a package manager for Python and other scientific software; it comes with [Miniforge](https://conda-forge.org/download/), Miniconda or Anaconda. Make an environment for wfc with it:

```bash
conda create -n wfc python=3.12
conda activate wfc
pip install workflow-canvas
```

Run `conda activate wfc` in every new terminal before you use wfc.

### Check it

```bash
wfc --version
```

It prints the version you installed, such as `wfc 0.8.0`. Now create your first project: [Your First Pipeline](tutorials/getting-started.md). Its second step, `wfc doctor`, checks Docker and git again from inside the project.

## If the Install Fails

- **`ERROR: No matching distribution found for workflow-canvas`.** The Python you installed into is not 3.12. This is what `pip install workflow-canvas` says on the Python from python.org's download button, which is newer. Install wfc one of the two ways above.
- **pip installs `workflow-canvas-0.5.0`.** The Python is 3.11, where pip picks an old release of wfc without a warning. Install wfc one of the two ways above.
- **`wfc: command not found`, or `'wfc' is not recognized`.** The environment is not active in this terminal. Activate it as above.
