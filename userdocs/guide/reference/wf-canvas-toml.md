<!-- generated from pm_mvp::docs.consumer.reference.wf-canvas-toml @ b451fb3ebc1e; do not edit -->

# Reference: wf-canvas.toml

## Reference: wf-canvas.toml

Every project keeps its configuration in `.wfc/wf-canvas.toml`, a TOML file. `wfc init` creates it, and it is committed to git with the rest of the project. The file also marks the project: wfc commands find the project by looking for it (see [CLI Reference](cli-reference.md)).

The file has three sections:

- `[dvc]` sets the archive where run outputs and samples are kept. `wfc init` writes it.
- `[pixi]` and `[conda]` tell `wfc register-env` where to find local environments to capture. Both are optional.

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
```

A new file from `wfc init` holds only the `[dvc]` section, with `url` set to the location you chose (default `~/.wfc/archives/<project>`). Add `[pixi]` or `[conda]` by hand when you need them.

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

## Next steps

- [Project Anatomy](../explanation/project-anatomy.md): where `wf-canvas.toml` sits beside the database, modules, methods and run directories.
- [Storage & Provenance](../explanation/storage-and-provenance.md): how outputs move between the local cache and the `[dvc]` archive.
- [Registering an Environment](../tutorials/registering-an-environment.md): capturing a pixi or conda env with `wfc register-env`.
- [Getting Started](../tutorials/getting-started.md): the first-project walkthrough that creates this file.
