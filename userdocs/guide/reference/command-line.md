<!-- generated from pm_mvp::docs/consumer/reference/command-line @ a957dcedc663; do not edit -->

# Command Line

## Overview

Every command is run as `wfc <command>` (or `python -m wfc <command>`). `wfc <command> --help` prints the same flags listed here.

`wfc --version` prints the installed version, as `wfc <version>`, and exits `0`.

Commands find the project by walking up from the current directory to the nearest folder that contains `.wfc/wf-canvas.toml`, so you can run them from any subdirectory of a project. Set `WFC_PROJECT_ROOT` to name the project explicitly (see [Environment Variables](#environment-variables)).



Exit codes: `0` means success, `1` means the command refused or failed and printed why, and `2` means the arguments were invalid.
The commands that list or read (`runs`, `list-methods`, `list-modules`, `list-samples`, `list-envs`, `show-run`, `check-pipeline`, `doctor`, `share` and `wfc export --path`) take `--json`, and so does the outcome of `wfc try`. With `--json` the command prints one JSON object on stdout, always with an `ok` field. Errors still go to stderr as an `ERROR:` line, and a failure prints `{"ok": false, "error": "..."}` as well. Commands that change the project take no `--json`.

A pipeline file can be anywhere on disk; `wfc run-pipeline` and `wfc check-pipeline` read it from the path you give and run or check it against the project you are in, or the one you name with `--project-root`. Outside a project, with no `--project-root`, they stop and tell you to run from the project's folder or pass `--project-root`.


## Environment Variables

None of these is required; each overrides a default.

| Variable | Effect |
|---|---|
| `WFC_PROJECT_ROOT` | The project directory to use instead of walking up from the current directory. It must contain `.wfc/wf-canvas.toml`. |
| `DATABASE_URL` | The database to read and write instead of the project's `.wfc/wfc.db`. |
| `WFC_GIT_COMMIT_TIMEOUT` | Seconds a `git commit` made by wfc may take before it is stopped (default `300`). Raise it if your git hooks are slow. Must be a positive number. |

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/reference/cli-project
/reference/cli-registration
/reference/cli-environments
/reference/cli-pipelines
/reference/cli-cache-export
```
