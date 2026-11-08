<!-- generated from pm_mvp::docs/consumer/reference/cli-project @ 2f47efa3d45e; do not edit -->

# Project Commands

## Project Commands

### wfc init

Make a directory into a wfc project, then run the other commands from inside it:

```bash
wfc init --dir wfc_project_root
cd wfc_project_root
```

`wfc init`:

1. Creates `.wfc/`, where wfc keeps everything it manages. Put your own code in `src/<module>/<method>/` (see [Project Anatomy](../explanation/project-anatomy.md)).
2. Writes `.wfc/wf-canvas.toml` with a `[dvc]` section naming the output archive (see [wf-canvas.toml](wf-canvas-toml.md)), and creates the project database `.wfc/wfc.db`.
3. Adds wfc's entries to `.gitignore` and sets up the archive.
4. Runs `git init` if the directory is not a repository yet, and commits the files it wrote. On Windows it also sets `core.longpaths` in the new repository, so git works in a deeply nested project folder. The commit is authored as you when git has your name and email, and as `wfc <wfc@wfc>` when it has neither; set yours with `git config user.name` and `git config user.email`. Every later commit wfc makes, when you register methods, modules and environments, follows the same rule. The repository is local only; wfc never pushes.
5. Prints a health table (the same checks as `wfc doctor`).

The archive location is where copies of your run outputs are kept. `wfc init` asks for it, with `~/.wfc/archives/<project>` as the default. Give a directory path, or a DVC remote URL such as `s3://bucket/path` (the matching DVC plugin must be installed, for example `pip install 'dvc[s3]'`).

In an existing project, `wfc init` lists what it would add and asks once before changing anything (in a script, pass `--yes`). When nothing is missing it says the project is already current and changes nothing. It only adds what is missing: a config key you already set is kept, and an `--archive` that differs from the one in the config is refused.

A missing git or Docker does not stop `wfc init`: the files are still written, and the health table shows what to install. Install it, re-run `wfc init` to fill in what is missing, then run `wfc doctor`.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: init
   :nodescription:
   :noepilog:
   :nodefault:
```

The project database `.wfc/wfc.db` is not tracked in git. Back up the `.wfc/` directory to keep your run records.

### wfc doctor

Check whether the project is ready to run. `wfc doctor` prints a health table with one row per check:

- **git**: git is installed, the project is a repository with a commit, and the working tree is clean.
- **dvc**: an archive is configured, set up in DVC, and reachable.
- **docker**: `docker` is installed and the daemon is running.
- **samples**: every registered sample's content is still in the local cache or the archive.
- **views**: wfc can link files to its cache instead of copying them. It warns when the filesystem forces copies, which take twice the disk space.

Each check reports `ok`, `warn` or `fail`, with a fix hint under any row that is not `ok`. `wfc doctor` exits 1 if any check fails (a `warn` does not), so it works as a pre-run check in scripts and CI. It takes no arguments.

A second table, **Env images**, follows the first. Its rows only warn, never fail:

- **registry**: with a registry set, the registry answers and every env image on this machine is pushed. It also warns about an image that `wfc push-env` cannot push because its env name is one no registry accepts.
- **versions**: every method version whose image is neither on this machine nor in the registry, with the commands that make it runnable again.
- **demo**: appears only for a demo made by an older wfc; run `wfc demo --force`.

### wfc demo

Add a complete, runnable demo pipeline to an initialised project and open it in the Canvas. `wfc demo` builds a small container environment, registers five methods in three modules and three samples, writes `demo-pipeline.json`, and opens the Canvas with that pipeline loaded. Press Run to execute it.

Run `wfc init` first; `wfc demo` also needs Docker running. If a check fails, it exits 1 and changes nothing. The demo's environment is `wfc-demo-env`. `wfc demo` never pushes its image, even when the project has a registry; the output names `wfc push-env wfc-demo-env`. If a demo is already present, pass `--force` to remove it, its runs included, and build it again. A demo made by an older wfc is refused until you run `wfc demo --force`, and `wfc doctor` warns about it.

`wfc demo --remove` deletes exactly what the demo added (its modules, methods, samples, environment, runs, files and pipeline) and leaves everything you registered yourself. It lists what it will remove and asks first unless you pass `--yes`. Demo entries use the `__demo__` name prefix, and the environment is `wfc-demo-env`; you cannot use either for your own names.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: demo
   :nodescription:
   :noepilog:
   :nodefault:
```

For a walkthrough see [A Tour of the Demo](../tutorials/wfc-demo.md).

### wfc canvas

Start the Canvas web UI for the current project and print its address.

```{eval-rst}
.. argparse::
   :module: wfc.cli
   :func: build_parser
   :prog: wfc
   :path: canvas
   :nodescription:
   :noepilog:
   :nodefault:
```

For a first-project walkthrough, see [Your First Pipeline](../tutorials/getting-started.md).

On Windows `wfc init` and `wfc doctor` also report where the project lives, in a `disk` row. A project on a network drive (a mapped drive or a `\\server\share` path) is a warning, not a failure: steps run in containers, and a container sees a project on a network drive as an empty folder. The fix hint says to move the project to a local disk and, to keep run outputs on the share, to make the share the archive with `wfc init --archive <share path>`. `wfc init` still creates the project and exits 0.

After the run-readiness table, `wfc doctor` prints an `Env images:` table. Its `binds` row starts one short container from an env's image and checks that the project and the cache show up inside it; an empty bind fails the row and `wfc doctor` exits 1, naming the bind. Its `leftovers` row warns when an earlier run or crash left containers or env builds running and names `wfc prune-envs`. `wfc init` adds `.wfc/logs/` to `.gitignore`, where `wfc register-env` keeps its logs.
