<!-- generated from pm_mvp::docs/consumer/reference/wf-canvas-toml @ f07212c9886a; do not edit -->

# wf-canvas.toml

## Reference: wf-canvas.toml

Every project keeps its configuration in `.wfc/wf-canvas.toml`, a TOML file. `wfc init` creates it, and it is committed to git with the rest of the project. The file also marks the project: wfc commands find the project by looking for it (see [Command Line](command-line.md)).

The file has these sections:

- `[dvc]` sets the archive where run outputs and samples are kept. `wfc init` writes it.
- `[integrity]` sets how wfc checks what a step reads against what it recorded. It is optional.
- `[pixi]` and `[conda]` tell `wfc register-env` where to find local environments to capture. Both are optional.
- `[registry]` sets the registry base that environment images are pushed to. `wfc push-env` writes it on its first run. It is optional.
- `[previews]` sets how much memory and disk the canvas may use for the previews it makes of run outputs. It is optional.

For where the file sits among the project's other folders, see [Project Anatomy](../explanation/project-anatomy.md).

## Full example

A config with every section filled in:

```toml
[dvc]
url = "C:/wfc-archives/myproject"   # Archive: a directory outside the project, or a remote such as s3://bucket/path
auto_init = true                    # Let `wfc init` set up DVC (default true)

[pixi]
root = ".pixi"                      # Where pixi environments live (default .pixi in the project)

[conda]
root = "/path/to/conda"             # Conda installation to capture envs from (default: detected)

[registry]
url = "ghcr.io/my-org/my-project"   # Where environment images are pushed: a registry host and path

[integrity]
inputs = "stat-then-hash"           # How a step's inputs are checked: "stat-then-hash" (default) or "hash"

[previews]
memory_mb = 1024                    # Most memory image previews use at once, in MB (default 1024)
cache_mb = 500                      # Most disk the saved previews take, in MB (default 500)
```

A new file from `wfc init` holds only the `[dvc]` section, with `url` set to the location you chose (default `~/.wfc/archives/<project>`). `wfc push-env` adds `[registry]` on its first run. Add `[pixi]`, `[conda]`, `[integrity]` or `[previews]` by hand when you need them.

## [dvc]

```toml
[dvc]
url = "C:/wfc-archives/myproject"
auto_init = true
```

The archive holds a copy of every archived run output and registered sample, so results can be restored or pulled on another machine. See [Storage & Provenance](../explanation/storage-and-provenance.md) for how the cache and the archive work together.

- **`url`**: the archive location. Either a directory path outside the project (wfc refuses a path inside it), or a DVC remote URL with one of these schemes: `s3://`, `gs://`, `azure://`, `ssh://`, `http://`, `https://`, `webdav://`, `webdavs://`, `gdrive://`, `oss://`, `hdfs://`. A remote scheme needs its DVC plugin installed, for example `pip install 'dvc[s3]'`. Write paths with forward slashes, also on Windows.
- **`auto_init`**: when `true` (the default), `wfc init` sets up DVC for the project and points it at `url`. Set it to `false` to set up DVC yourself.

Registering samples and archiving outputs need this section. `wfc init` writes it, and `wfc doctor` checks that the archive is reachable.

## [pixi]

```toml
[pixi]
root = ".pixi"
```

- **`root`**: the directory where pixi keeps its environments. `wfc register-env <name> pixi:<proj>:<env>` looks for `<root>/<proj>-*/envs/<env>`, and `pixi:<name>` looks for `<root>/<name>-*/envs/default`. If nothing matches there, it uses the project's own `.pixi/envs/<env>`. A relative path is relative to the project directory; an absolute path lets several projects share one environment store. Default: `.pixi`.

This setting is only used when capturing a pixi env into an image. Methods run in the registered container image, not in the local env.

## [conda]

```toml
[conda]
root = "/path/to/conda"
```

- **`root`**: the conda installation whose environments `wfc register-env <name> conda:<env>` captures. wfc looks for the env at `<root>/envs/<env>`. A relative path is relative to the project directory. Default: the installation reported by `conda info --base`.

Like `[pixi]`, this is only used when capturing an env into an image.

## [registry]

Where environment images are pushed to and pulled from.

```toml
[registry]
url = "ghcr.io/my-org/my-project"
```

`url` is the registry host and path that image names are built under. `wfc push-env` writes it on its first run: with a GitHub remote it proposes `ghcr.io/<owner>/<repo>`, and `--registry <url>` sets another. A later `wfc push-env --registry <url>` naming another registry changes it after you confirm. After that, `wfc register-env` pushes each image it builds to this registry. See [Share a Project](../how-to/sharing-a-project.md).


## [previews]

How much memory and disk the canvas may use for the previews it makes of a run's outputs.

```toml
[previews]
memory_mb = 1024
cache_mb = 500
```

- `memory_mb` (default 1024) is the most memory, in MB, that image previews use at once. The canvas makes at most two image previews at a time, and each may use up to half of `memory_mb`; the rest wait their turn while the canvas keeps answering. An image that would need more than half to read and shrink for the screen is never opened: the canvas shows "Too large to preview" with the image's size and the `memory_mb` that would let it show. To see it, set `memory_mb` to at least that value here, or download the file.
- `cache_mb` (default 500) is the most disk, in MB, that saved image previews take. A preview the canvas has made once is kept in `.wfc/cache/previews/` and shown again from there without being made again. When the folder reaches `cache_mb`, the previews used longest ago are removed first. `wfc cache prune` empties the folder; the canvas makes a preview again the next time you open it.

Each value is a positive whole number of MB, written without quotes or a decimal point. Any other value is refused with a message that names the setting and its default: fix the line, or delete it to use the default. A change applies without restarting the canvas.


## [integrity]

How wfc checks a file against what it recorded before a step reads it.

```toml
[integrity]
inputs = "stat-then-hash"
```

- `"stat-then-hash"` (the default): wfc compares a file's size and timestamps with what it recorded, and re-hashes the file only when they differ.
- `"hash"`: wfc re-hashes every file it has a recorded hash for on every read. Use it on a FAT32 or exFAT drive, which keeps no change time, or when you want no trust in timestamps at all.

Any other value is refused. The setting changes no cache key, so switching it reuses cached results as before. A file that is not what wfc recorded is repaired from the cache when it can be (a sample's copy, a pushed cache entry) and otherwise refused with the command that fixes it. `wfc verify` checks everything at once; see [Cache and Export Commands](cli-cache-export.md#wfc-verify).


## Next steps

- [Project Anatomy](../explanation/project-anatomy.md): where `wf-canvas.toml` sits beside the database, modules, methods and run directories.
- [Storage & Provenance](../explanation/storage-and-provenance.md): how outputs move between the local cache and the `[dvc]` archive.
- [Register an Environment](../tutorials/registering-an-environment.md): capturing a pixi or conda env with `wfc register-env`.
- [Your First Pipeline](../tutorials/getting-started.md): the first-project walkthrough that creates this file.
