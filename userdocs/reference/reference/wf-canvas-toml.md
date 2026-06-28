<!-- generated from pm_mvp::docs.consumer.reference.wf-canvas-toml @ dbb00421a92d; do not edit -->

# Reference: wf-canvas.toml

## Reference: wf-canvas.toml

Every project keeps its configuration in `.wfc/wf-canvas.toml`. `wfc init` generates this file, and `wfc` reads it with the standard-library `tomllib` parser (Python 3.11+), so it follows ordinary TOML syntax. The file is committed to git — it is project source, not state.

This page is the field reference for each section. For where the file sits in the wider project layout, see [[project-anatomy]].

## Full example

A complete config with every section filled in:

```toml
[database]
url = "sqlite:///.wfc/wfc.db"

[project]
name = "my-project"

[pixi]
root = ".pixi"   # Relative to project root; absolute paths also accepted

[conda]                          # Optional — named conda env resolution
root = "/path/to/conda/envs"

[dvc]                            # Optional — enables DVC provenance storage
url = "file:///path/to/storage"  # Any DVC-native scheme: file://, s3://, ssh://, gs://, azure://, …
auto_init = true                 # Auto-run `dvc init` if .dvc/ is missing (default true)

[registry]                       # Optional — where built container images are pushed
url = "ghcr.io/your-org"
```

A freshly generated file contains only `[database]`, `[project]`, and `[pixi]`. The `[conda]`, `[dvc]`, and `[registry]` sections are optional and absent until you add them — `wfc init` writes the `[dvc]` block as a commented-out template you uncomment to opt in.

## [database]

```toml
[database]
url = "sqlite:///.wfc/wfc.db"
```

- **`url`** — A SQLAlchemy connection string for the project database. The default is a local SQLite file at `.wfc/wfc.db`. Point `url` at a Postgres server (`postgresql://…`) instead when several machines share one project over a network drive. The database itself is the single source of truth for all pipeline state; see [[project-anatomy]] for what it holds.

## [project]

```toml
[project]
name = "my-project"
```

- **`name`** — A human-readable project name. `wfc init` defaults it to the project directory's name. It labels the project in reports and in the Canvas UI.

## [pixi]

```toml
[pixi]
root = ".pixi"
```

- **`root`** — The environment root directory. When a method declares a named environment (e.g. `env: image-io`), the interpreter is found by globbing `<pixi_root>/<env_name>-*/envs/default/`. A relative `root` is resolved against the project directory; an absolute path is taken as-is, which lets several projects share one environment store.

## [conda]

```toml
[conda]
root = "/path/to/conda/envs"
```

- **`root`** — Optional alternative to `[pixi]` for resolving named environments out of a conda env directory. Absent by default; when omitted, environment resolution uses only the pixi root.

## [dvc]

```toml
[dvc]
url = "file:///path/to/storage"
auto_init = true
```

The `[dvc]` section is optional and turns on DVC provenance storage so run outputs can be shared and pulled across machines. `wfc init` writes it as a commented-out template — uncomment it (or add the block by hand) to opt in.

- **`url`** — The remote location, given as any DVC-native URL: `file://` for a local or network path, plus `s3://`, `ssh://`, `gs://`, and `azure://` for cloud and remote backends. A single code path supports every backend because the remote push/pull uses DVC's own API. When the section is present, `wfc init` mirrors it to `.dvc/config` and DVC dispatches on the URL scheme.
- **`auto_init`** — When true (the default), `wfc init` runs `dvc init` automatically if a `.dvc/` directory does not yet exist.

For backwards compatibility the parser still accepts the legacy `remote_type` / `remote_path` pair (local-only) when `url` is absent, but new projects should use `url`. See [[storage-and-provenance]] for how the cache and remote actually work.

## [registry]

```toml
[registry]
url = "ghcr.io/your-org"
```

- **`url`** — Optional. Declares where built container images are pushed. Projects that never build their own container images do not need it. When you run `wfc init` inside a GitHub repository, it can pre-fill a `ghcr.io/<owner>` default derived from the git origin.

## How the config is read

Internally, `wfc` reads the file once into a settings dict. The reader returns `database_url`, `project_name`, `pixi_root` (always resolved to an absolute path), `conda_root` (an empty string when `[conda]` is absent), `dvc` (a dict with `url` and `auto_init`, or `None` when `[dvc]` is absent), and `registry` (a dict with `url`, or `None`). You never call this yourself — every `wfc` command loads the config for you — but knowing the resolved keys helps when you are debugging why an environment or remote was not found.

## Next steps

- Read [[project-anatomy]] to see where `wf-canvas.toml` lives alongside the database, modules, methods, and the run workspace.
- Read [[storage-and-provenance]] to understand what the `[dvc]` remote buys you and how outputs move between the cache and a remote.
- Read [[getting-started]] for the full first-project walkthrough that creates this file.
