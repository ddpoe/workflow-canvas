<!-- generated from pm_mvp::docs.consumer.explanation.storage-and-provenance @ 038bb5c105dc; do not edit -->

# Storage & Provenance

## Storage & Provenance

A pipeline run produces two things: the **output files** your methods write, and the **record** of which method, parameters and inputs produced them. Workflow Canvas keeps them in different places:

- **Output files** go into a content-addressed cache: each file is stored under the hash of its contents, and copied to your archive (the DVC remote) for safekeeping.
- **The record** of every run lives in the project database, `.wfc/wfc.db`. It maps each run's outputs to the content hashes that hold their bytes.
- **Git** holds your method code. Every run records the commit it ran at.

For how to find and read a run's outputs, see the History tab in [Build, Run and Inspect in the Canvas](../how-to/canvas.md), or [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md) for `wfc export`. For when a step is reused instead of re-run, see [Caching & Reproducibility](caching-and-reproducibility.md).

## Where outputs live

While a step runs, its method writes outputs into that run's own folder under `.runs/`. When the pipeline finishes, wfc **archives** each output: it hashes the file and copies it into the project's DVC cache under `.dvc/cache/`, where the file's address is its content hash.

Addressing by content has two consequences:

- **Identical outputs are stored once.** Two runs that produce byte-identical files share one cache entry.
- **Archived outputs are read-only.** Changing a file in place would change every run that points at it, so the cache refuses writes.

You never need to browse these folders. `wfc export <run-id> <slot> <dest>` copies an output to a location you choose, and the copy is yours to edit. `wfc export <run-id> <slot> --path` prints the read-only cached file's path so you can open it without copying. If you write to that path, the write fails:

```
PermissionError: [Errno 13] Permission denied: '.dvc/cache/files/md5/1f/9c…'
```

The Canvas History tab reads outputs from the same cache. See [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md).

## Archiving, and what to back up

Archiving happens once, after the whole pipeline finishes, so hashing large files never slows the steps down. Between steps, a downstream step reads its upstream's output straight from the run folder. `wfc run-pipeline --no-archive` skips the pass; `wfc cache archive` archives any outputs still waiting, and is safe to re-run after an interruption because outputs already archived stay archived.

The cache holds bytes with no names. The database, `.wfc/wfc.db`, is what connects a run and its outputs to those bytes, and git does not track it. **Back up `.wfc/wfc.db`**: without it, the cached files can no longer be matched to the runs that produced them.

`wfc cache prune` reclaims disk space by removing run folders the database no longer needs; add `--include-local` to also remove cache entries no run refers to. While an archive is configured, prune keeps any entry that has not been copied to it yet. `--force` deletes those too, and their bytes are then gone for good.

## What git records

Git versions your method code. Registering a method copies its source into the project and commits that copy for you, committing only the paths registration wrote.

Running a pipeline makes no commits, but it does require a committed tree: a run starts only when tracked files have no uncommitted changes (untracked files are fine), and each run records the commit it ran at. If you have edits, commit or stash them first:

```
Working tree has uncommitted changes to tracked files.
Commit or stash your changes before running: `git commit -am ...`.
```

The commit is recorded for provenance only. Whether a step is reused from the cache depends on the method's code itself, so a commit that touches other files does not make it re-run (see [Caching & Reproducibility](caching-and-reproducibility.md)).

## The archive (DVC remote)

`wfc init` sets up an archive, a DVC remote that keeps a second copy of every cached file. By default it is a folder in your home directory, outside the project; pass `wfc init --archive PATH` to choose another folder, a network drive, or a remote URL (`s3://`, `ssh://`, `gs://`, `azure://`). The location is the `url` key of the `[dvc]` block in `.wfc/wf-canvas.toml`. A remote URL needs its DVC plugin, installed with the matching extra, for example `pip install 'workflow-canvas[s3]'` (or `[remotes]` for all four). wfc drives DVC for you; you do not run `dvc` yourself.

Copies to the archive happen in the background during a pipeline run, so steps never wait on uploads; a run's outputs are copied after they are archived at the end of the pipeline. Registering a sample copies it to the archive straight away. A failed upload is retried.

Reading works the other way round. When an output or sample is not in the local cache, wfc pulls it from the archive. This is how a pruned cache refills, and how another machine with the project and its database reads results it did not compute.

See [Project Anatomy](project-anatomy.md) for the `[dvc]` block.

## Where to go next

- [Build, Run and Inspect in the Canvas](../how-to/canvas.md): find a run in History, read its outputs, and trace its lineage.
- [Run and Inspect from the Command Line](../how-to/run-and-inspect-results.md): export a run's outputs with `wfc export`.
- [Caching & Reproducibility](caching-and-reproducibility.md): when a step is reused and how lineage is recorded.
- [Project Anatomy](project-anatomy.md): the project layout and `wf-canvas.toml`, including the `[dvc]` block.
- [Registering Modules, Methods, and Samples](../how-to/registration.md): how registering a method snapshots and commits its source.
