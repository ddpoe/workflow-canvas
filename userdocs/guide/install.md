<!-- generated from pm_mvp::docs/consumer/install @ c367028584b7; do not edit -->

# Install Workflow Canvas

## What You Need

Workflow Canvas is written `wfc` for short, and `wfc` is also the command you type to use it. It runs on Windows, macOS and Linux, and needs three things on your computer:

| What | Why |
|---|---|
| **Docker**, installed and running | runs each step of a pipeline |
| **git** | records which version of your code made each result |
| **Python 3.12** | runs wfc itself; no other version of Python will do, and both ways of installing wfc below bring it with them |

This page installs each of them, then wfc.

:::{admonition} An AI agent can do this with you
:class: agent-tip

If you use Claude Code, Codex or a chat assistant, add the wfc plugin as [Work with an AI Agent](tutorials/work-with-an-ai-agent.md) shows, then ask it to set you up. It takes you through the same steps and checks each one.
:::

## How to Run the Commands

Each grey box on this page holds one or more commands. You run a command by typing it into a terminal, a window that takes typed commands, and pressing Enter.

Some steps differ between Windows, macOS and Linux. Pick your system's tab below; every other set of tabs on this page follows it.

Open a terminal:

::::{tab-set}
:sync-group: os

:::{tab-item} Windows
:sync: windows

Open the Start menu, type `PowerShell`, and open **Windows PowerShell**.

- The Windows 11 app named **Terminal** opens PowerShell too, so it works as well.
- Command Prompt does not: this page's commands are for PowerShell.
:::

:::{tab-item} macOS
:sync: macos

Open the **Terminal** app: press Command+Space, type `Terminal`, and press Return.
:::

:::{tab-item} Linux
:sync: linux

Open your desktop's terminal app.
:::
::::

Copy each command, paste it into the terminal, and press Enter. Where a box holds several lines, run them one at a time from the top, and wait for each to finish before you run the next.


## Install Docker

Docker is the program that runs containers. wfc runs each step of a pipeline in one: a sealed copy of the software that step needs, which runs the same on any computer ([How wfc uses Docker](explanation/how-the-pieces-fit-together.md#docker-the-software-and-dependencies)).

Docker Desktop is free for personal use, education and small businesses; larger organisations need a paid subscription ([Docker's licence terms](https://docs.docker.com/subscription/desktop-license/)).

::::{tab-set}
:sync-group: os

:::{tab-item} Windows
:sync: windows

Install Docker Desktop, following [Docker's guide for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Then open Docker Desktop from the Start menu and wait until it shows that the engine is running. Its first start can ask for an administrator, to turn on WSL 2.

Open a new terminal and check it:

```powershell
docker run --rm hello-world
```

It prints `Hello from Docker!` when Docker is ready. If it prints an error instead:

- **`failed to connect to the docker API ... The system cannot find the file specified.`** Docker is installed but not running. Open Docker Desktop, wait until it shows that the engine is running, and run the command again.
- **`docker : The term 'docker' is not recognized ...`** The terminal was opened before Docker was installed. Close it, open a new one, and run the command again.
:::

:::{tab-item} macOS
:sync: macos

Install Docker Desktop, following [Docker's guide for Mac](https://docs.docker.com/desktop/setup/install/mac-install/). Then open Docker Desktop from your Applications folder and wait until it shows that the engine is running.

Open a new terminal and check it:

```bash
docker run --rm hello-world
```

It prints `Hello from Docker!` when Docker is ready. If it prints an error instead:

- **An error that asks whether the Docker daemon is running.** Docker is installed but not running. Open Docker Desktop, wait until it shows that the engine is running, and run the command again.
- **`command not found: docker`.** The terminal was opened before Docker was installed. Close it, open a new one, and run the command again.
:::

:::{tab-item} Linux
:sync: linux

Install [Docker Engine](https://docs.docker.com/engine/install/), then follow Docker's [post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/) so that you can run `docker` without `sudo`.

Check it:

```bash
docker run --rm hello-world
```

It prints `Hello from Docker!` when Docker is ready. If it prints an error instead:

- **An error that says `permission denied`.** The post-installation steps are not done, or you have not logged out and in again since you did them.
- **An error that asks whether the Docker daemon is running.** Docker is installed but not running. Start it with `sudo systemctl start docker` and run the command again.
:::
::::

Docker has to be running whenever wfc runs a pipeline.


## Install git

git keeps the history of your code, so wfc can record exactly which version of it produced each result. It all stays on your computer, with no account needed ([How wfc uses git](explanation/how-the-pieces-fit-together.md#git-the-code)).

Install git from [git-scm.com](https://git-scm.com/install/), keeping the installer's suggested settings. Then open a new terminal and check it:

```bash
git --version
```

It prints a line that starts with `git version`.


## Install wfc

wfc is a Python program. It is installed into an environment: a folder that holds one version of Python and the packages installed for it, kept apart from the rest of your computer.

Install wfc with one of two package managers for Python, uv or conda. Either one installs Python 3.12 and makes the environment. Use conda if it is what you already work with; otherwise use uv.

### With uv

[uv](https://docs.astral.sh/uv/getting-started/installation/) is a package manager for Python: it installs Python itself and Python packages. It is one small download, needs no administrator rights, and fetches Python 3.12 for you.

**1. Install uv.**

::::{tab-set}
:sync-group: os

:::{tab-item} Windows
:sync: windows

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
:::

:::{tab-item} macOS
:sync: macos

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
:::

:::{tab-item} Linux
:sync: linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
:::
::::

**2. Close the terminal and open a new one.** A terminal finds uv only when it was opened after uv was installed.

**3. Make a folder for your Workflow Canvas work and go into it.**

```bash
cd ~
mkdir workflow-canvas
cd workflow-canvas
```

This makes a folder named `workflow-canvas` in your home folder. uv installs wfc inside the folder the terminal is in, so wfc lives in this folder, and the projects you create later go in it too. Another name or place works as well, as long as it is on your computer's own disk and not on a network drive.

**4. Install wfc.**

```bash
uv venv --python 3.12 --seed
uv pip install workflow-canvas
```

This puts wfc in a `.venv` folder inside `workflow-canvas`, with its own `pip` for upgrading wfc later.

**5. Activate wfc.** Activating tells the terminal where wfc is.

::::{tab-set}
:sync-group: os

:::{tab-item} Windows
:sync: windows

Windows blocks PowerShell scripts until you allow them, and activating runs one. Allow them for your own account. You do this once, and it needs no administrator:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If it asks you to confirm, type `Y` and press Enter. Then activate:

```powershell
.venv\Scripts\activate
```

If activating prints the error below, the first command has not been run. Run it, then activate again.

```text
.venv\Scripts\activate : File ...\.venv\Scripts\activate.ps1 cannot be loaded because running
scripts is disabled on this system.
```
:::

:::{tab-item} macOS
:sync: macos

```bash
source .venv/bin/activate
```
:::

:::{tab-item} Linux
:sync: linux

```bash
source .venv/bin/activate
```
:::
::::

The line you type on now starts with `(workflow-canvas)`: wfc is active in this terminal.

:::::{tip}
**Each time you open a new terminal**, go to the folder and activate again before you use wfc:

::::{tab-set}
:sync-group: os

:::{tab-item} Windows
:sync: windows

```powershell
cd ~\workflow-canvas
.venv\Scripts\activate
```
:::

:::{tab-item} macOS
:sync: macos

```bash
cd ~/workflow-canvas
source .venv/bin/activate
```
:::

:::{tab-item} Linux
:sync: linux

```bash
cd ~/workflow-canvas
source .venv/bin/activate
```
:::
::::
:::::

### With conda

conda is a package manager for Python and other scientific software; it comes with [Miniforge](https://conda-forge.org/download/), Miniconda or Anaconda. Make an environment for wfc with it:

```bash
conda create -n wfc python=3.12
conda activate wfc
pip install workflow-canvas
```

Run `conda activate wfc` in every new terminal before you use wfc.

Then make a folder to keep your projects in, on your computer's own disk and not on a network drive, and go into it:

```bash
cd ~
mkdir workflow-canvas
cd workflow-canvas
```




## Confirm the Workflow Canvas Installation

```bash
wfc --version
```

It prints the version you installed, such as `wfc 0.8.0`. That shows wfc is installed. To check that Docker and git work with it, create a project and run `wfc doctor` inside it:

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
wfc doctor
```

`wfc init` makes the `wfc_project_root` folder for you, inside the folder the terminal is in. It asks where to keep the archive: press Enter to accept the suggestion. `wfc doctor` prints one line for each check, and every line should say `OK`. If one doesn't, the line under it says how to fix it.

After an install with uv, your folders look like this:

```text
workflow-canvas/         the folder you made
├── .venv/               made by uv: wfc is installed here
└── wfc_project_root/    made by wfc init: your first project
```

This is the project [Your First Pipeline](tutorials/getting-started.md) starts with, so carry on there from its step 3.


### If the Install Fails

The errors from the Docker check and from activating wfc are listed with those steps above. Others:

- **`ERROR: No matching distribution found for workflow-canvas`.** The Python you installed into is not 3.12. This is what `pip install workflow-canvas` says on the Python from python.org's download button, which is newer. Install wfc one of the two ways above.
- **pip installs `workflow-canvas-0.5.0`.** The Python is 3.11, where pip picks an old release of wfc without a warning. Install wfc one of the two ways above.
- **`wfc: command not found`, or `The term 'wfc' is not recognized`.** wfc is not active in this terminal. Activate it as above: with uv, go to your folder first.
