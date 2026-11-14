<!-- generated from pm_mvp::docs/consumer/explanation/storage-and-provenance @ 99118b5e5690; do not edit -->

# Storage & Provenance

## Storage & Provenance

A pipeline run produces two things: the **output files** your methods write, and the **record** of which method, parameters and inputs produced them. Workflow Canvas keeps them in different places:

- **Output files** go into the project's cache, which stores each distinct file once. wfc also copies them to your archive, where it keeps a backup copy of your data and results.
- **The record** of every run lives in the project database, `.wfc/wfc.db`. It records which stored file is each run's output.
- **Git** holds your method code. wfc commits it once, when you register the method. A run makes no commit: it records the commit the project was at.

For how to find and read a run's outputs, see the History tab in [Browse Past Runs in History](../how-to/browse-history.md), or [Use the Command Line](../how-to/cli-hub.md) for `wfc export`. For when a step is reused instead of re-run, see [Caching & Reproducibility](caching-and-reproducibility.md).


## Where outputs live

While a step runs, its method writes outputs into that run's own folder under `.wfc/runs/`. When the pipeline finishes, wfc **archives** each output: it moves the file into the project's cache, filed by its content, and the run folder keeps only the run's logs.

Addressing by content has two consequences:

- **Identical outputs are stored once.** Two runs that produce byte-identical files share one cache entry.
- **Archived outputs are read-only.** Changing a file in place would change every run that points at it, so the cache refuses writes.

You never need to browse these folders. `wfc export --run <run-id> --slot <slot> --dest <dest>` copies an output to a location you choose, and the copy is yours to edit. `wfc export --run <run-id> --slot <slot> --path` prints the read-only cached file's path so you can open it without copying. If you write to that path, the write fails:

```
PermissionError: [Errno 13] Permission denied: '.dvc/cache/files/md5/1f/9c…'
```


## Archiving, and what to back up

Archiving happens once, after the whole pipeline finishes, so it never slows the steps down. `wfc run-pipeline --no-archive` skips it; `wfc cache archive` archives any outputs still waiting.

The cache holds bytes with no names. The database, `.wfc/wfc.db`, is what connects a run and its outputs to those bytes, and git does not track it. **Back up `.wfc/wfc.db`**: without it, the cached files can no longer be matched to the runs that produced them.

`wfc cache prune` frees disk space by removing run folders wfc no longer needs; add `--include-local` to also remove cached files the archive already holds. It never removes a file that has not been backed up to the archive. `wfc cache push` copies any waiting files to the archive now.


## When something changed after wfc recorded it

Before a step reads a file, wfc checks that it has not changed since wfc stored it. A changed file is put back from the archive. If there is no backup copy, the step stops and names the file; run the step that made it again.

`wfc verify` runs the check over everything at once and `wfc verify --repair` puts back what it can; see the [Cache and Export Commands](../reference/cli-cache-export.md#wfc-verify).


## What git records

Git versions your method code. Registering a method saves a copy of its source in the project and commits that copy for you. Registering an environment commits its record and capture folder the same way. Your own source directory is not committed by wfc, and wfc never pushes to a git remote: its commits stay on your machine until you run `git push`.

Running a pipeline makes no commits, but it does require a committed tree: a run starts only when tracked files have no uncommitted changes (untracked files are fine), and each run records the commit it ran at. If you have edits, commit or stash them first:

```
Working tree has uncommitted changes to tracked files.
Commit or stash your changes before running: `git commit -am ...`.
```

The commit is recorded for provenance only. Whether a step is reused from the cache depends on the method's code itself, so a commit that touches other files does not make it re-run (see [Caching & Reproducibility](caching-and-reproducibility.md)).


## The archive (DVC remote)

`wfc init` sets up an archive: a folder where wfc keeps a backup copy of your data and results. By default it is a folder in your home directory, outside the project; pass `wfc init --archive PATH` to choose another folder, a network drive, or a remote URL (`s3://`, `ssh://`, `gs://`, `azure://`).

wfc copies files to the archive in the background, so steps never wait on uploads.

Reading works the other way round. When an output or sample is not in the local cache, wfc pulls it from the archive. This is how a pruned cache refills, and how another machine with the project and its database reads results it did not compute.

Environment images travel separately, through an image registry, a server that stores container images: `wfc push-env` uploads them there, and a collaborator's wfc downloads them from it. [Share a Project](../how-to/sharing-a-project.md) covers the registry, the git repository and the archive together.


## Where to go next

- [Browse Past Runs in History](../how-to/browse-history.md): find a run in History, read its outputs, and trace its lineage.
- [Use the Command Line](../how-to/cli-hub.md): export a run's outputs with `wfc export`.
- [Caching & Reproducibility](caching-and-reproducibility.md): when a step is reused and how lineage is recorded.
- [Project Anatomy](project-anatomy.md): the project layout and `wf-canvas.toml`, including the `[dvc]` block.
- [Write a Method](../how-to/write-a-method.md): how registering a method snapshots and commits its source.
