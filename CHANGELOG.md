# Changelog

Notable changes to Workflow Canvas. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.6.0] — 2026-09-29

### Upgrading
- Every existing cache entry misses once and recomputes: cache keys now include which input slot each upstream output or sample feeds, the module-qualified method, and the method's declared contract. There is no conversion.
- Runs recorded before this release have no output slot records and are treated as malformed: they are never reused from the cache, and reading their outputs fails naming the run to re-run. Re-run those steps and re-select the new run in any Run Reference node.
- Samples registered without a content hash, and directory samples registered before this release, are refused at pipeline start and on restore. Re-register them; runs over a directory sample recompute once.
- `--parent-run-id` entries must name the input slot (`input:output:run` or `input:run`); the bare run-id form is refused. `wfc check_cache` now requires `--module`.
- Runs recorded before this release carry no canvas node id and no sample-read records: the canvas shows no node state for them, History shows no "reads" lines, and opening their lineage uses the old wiring until they are re-run. The new database columns are added automatically on startup.
- The demo's methods are now named `__demo__preprocess`, `__demo__filter_cells`, and so on.
- HTTP API clients: see Removed for the retired routes and the dropped `steps` map.

### Added
- Methods can now be written in R (`.R`/`.r`) or bash (`.sh`) as well as Python: registration discovers the script by extension, snapshots it, and the code fingerprint covers it, so editing an R or bash script correctly re-runs the step instead of serving a stale cached result. Existing Python methods are unaffected — identical fingerprints, no cache invalidation.
- Optional `script:` key in `method.yaml` names the method's script explicitly (useful when the file isn't `<method>.<ext>`, or when a directory holds more than one candidate — ambiguous directories now fail registration with the candidate list instead of guessing). The `--script` flag on `wfc register-method` takes precedence over the key.
- Optional `helpers:` list in `method.yaml` declares the method's exact code set — the main script plus listed helper files, which may live elsewhere in the project (`../shared/utils.R`). Declared helpers are snapshotted and fingerprinted with the method, so editing one re-runs the step; with `helpers:` present, an undeclared script in the method directory fails registration loudly rather than silently escaping the cache key.
- `wfc register-env --interpreter` as the documented name for the interpreter-path flag (`--python` remains accepted; passing both is an error). R environments built through the conda/pixi backends need it, e.g. `--interpreter /opt/conda/bin/Rscript`.
- `wfc register-env` checks a pixi lock before building: the env name must match an environment defined in the lock (the error lists the environments the lock actually has — most pixi projects define only `default`), and a lock written by a newer pixi than the build tool bundled with this release is rejected with both versions named. Previously either mistake surfaced as a cryptic pixi error partway through `docker build`.
- `wfc register-sample --manifest sample.yaml` attaches a description to a sample (the only allowed key is `description`); the Canvas register form accepts one too. The sample detail shows the description and, for a directory sample, its file count. A description never affects a sample's identity or any cache key.
- Optional install extras for DVC remote plugins: `workflow-canvas[s3]`, `[ssh]`, `[gcs]`, `[azure]`, and `[remotes]` for all four. A local remote needs none of them.
- Runs Preview cache status: after Lock All, each row says whether it will be reused from the local cache, reused from the remote, re-run because the step changed, re-run because an upstream re-runs, or blocked (with the reason); a cached row links to its source run. Backed by `POST /api/wfc/cache-status`.
- Lock summary: Lock All and Run open a summary listing every unlocked row and every parameter that will run with its default, and ask you to confirm. A row that fails to commit is named instead of leaving Run hanging.
- `wfc init --yes`. In an existing project, `wfc init` lists what it would change and asks before applying anything; with no way to confirm, it exits without changing anything.
- `WFC_GIT_COMMIT_TIMEOUT` sets how many seconds wfc's `git commit` may run (default 300); other git calls are limited to 60 seconds. A hung git call (slow hook, credential prompt) is stopped and reported, and nothing is left staged.
- Every run records the samples it read, with slot, sample name and content hash. The History run detail panel lists them under the parent chips, one "reads *sample* → *slot*" line each.
- `--parent-run-id input:output:run` names which output of a parent feeds which input; repeat the flag once per entry.
- `wfc run-pipeline` checks at start that every sample's content is in the local cache or has been pushed, and refuses before launch otherwise. `wfc doctor` and `wfc init`'s health table gain a samples probe.

### Changed
- The leading dot on a slot `type` in `method.yaml` is now optional: `type: csv` and `type: .csv` are both accepted and normalise to the same contract. The Canvas shows slot types without the dot (`csv`, `tar.gz`) in node chips and the inspector's inputs/outputs lists.
- Resolving a multi-output parent without slot information (a manual `wfc run-step --parent-run-id` invocation, or a legacy pipeline link with no `source_slot`) now fails with an error naming the available outputs, instead of silently picking the first one. Single-output parents resolve exactly as before.
- Wiring two different outputs of the same node into one input slot is now rejected before the run starts, with an error naming both competing source slots — previously it silently delivered the first output twice.
- Outputs are found by slot, not file name: a method can save a declared output under any file name and a downstream step wired to that slot still receives it. `wfc export <run> <slot>` takes a slot name, and when two outputs share a file name `wfc export --all` and the Files list put each under its slot's folder. A run that saves two outputs to the same file fails naming both.
- Run Reference nodes name a run, and each link names the output (`source_slot`, optional when the run has one output). `output_slot`/`output_path` on the node are ignored and file-path references are no longer supported. An invalid reference fails the pipeline load naming the node.
- Directory samples and directory step outputs are stored in DVC's own directory format, so they push, pull and restore identically on another machine, and a file shared by two directories is stored once. A directory counts as local only when all its files are in the cache. A directory containing a symlink, two names differing only by case, or no files is refused, naming the path.
- `wfc register-sample --source` caches the content and leaves the source where it is. `wfc export --path` prints a directory output's read-only local copy inside the project.
- Each queued upload to the remote is pushed and recorded on its own, so one failed push no longer blocks the rest.
- `wfc init` sets up DVC before its first commit, commits everything it creates, and ignores `.snakemake/`, `.dvc/cache/` and `.wfc/build/`. The generated `wf-canvas.toml` holds only `[dvc]`, and an explicit `--archive` is no longer lost.
- `register-method`, `register-module` and `register-env` commit exactly the files they wrote and nothing else you had staged; a refused registration leaves nothing behind.
- Every command finds the project root from any subdirectory.
- The Canvas is installed by default: `fastapi`, `uvicorn` and `psutil` moved from the optional `api` group to the main dependencies.
- `wfc run-pipeline` prepares pipelines the same way the Canvas does, and ends a failed pipeline with one `ERROR:` line and exit code 1 instead of a traceback.
- If the database can't be reached, a pipeline launch stops before anything starts, with one message naming the database (password masked).
- Registration refuses slot names containing `:`, `=` or whitespace, and a method name another module already holds (previously it silently overwrote that module's scripts).
- The claim refuses, before any run row is written, an input that resolves to no registered sample or run, a required input slot nothing feeds, an unnamed link to an upstream with several outputs, a missing or empty sample directory, and a referenced run whose output cannot be found. Refusals distinguish an unwired slot ("nothing feeds") from a wired slot wfc failed to deliver (reported as a wfc defect). A fan-in bundle beside a run reference is refused at pipeline load.
- The Descendants tree draws a run with several parents under each of them, with an "N parents" marker; selecting one copy highlights all of them.
- In History, "→ Descendants" opens the Descendants view scoped to that run instead of a separate panel.
- The Inspector shows ages the same way History does ("3h ago", or a short date after a week).
- A run whose environment was deleted after its method was registered fails naming the unregistered environment and pointing at `wfc register-env`, instead of "Unknown env backend".
- Error messages, CLI help, the default config's comments and the Canvas confirm dialog no longer mention internal development cycles or planned versions.

### Removed
- The optional `[registry]` block in `.wfc/wf-canvas.toml`. `wfc` never used its value (registry push is deferred), and a `[registry]` block left in an existing config is now ignored.
- The `register-env` legacy input mode (`--backend pixi|conda` alone, reading source files from the project root). Use `--from <lock> --backend pixi|conda` or a live-env spec (`pixi:<proj>:<env>`, `conda:<env>`); the bare invocation now errors with that guidance. The deprecated `--pixi-env`/`--conda-env` flags are gone, and `--dry-run` now pairs with `--from` instead of the legacy mode.
- HTTP routes `GET /api/wfc/lineage/{run_id}` and `GET /api/wfc/tree/{run_id}` (the runs payload carries this information), and six unused routes: `POST /api/workflow/save`, `GET /api/wfc/experiments`, `POST /api/wfc/export-csvs`, `POST /api/wfc/preview-csvs`, `GET /api/project/status`, `POST /api/envs`.
- The unused per-method `steps` map in the pipeline status response; per-node state stays under the node-state map.
- The `[database] url` key in `wf-canvas.toml`, which was never read.
- The `wfc.snakemake_gen` module: import `generate_snakefile` from `wfc.orchestration` and `run_pipeline`/`load_pipeline_from_path` from `wfc.execution`.

### Fixed
- Pixi-backend `register-env` builds failed before the first layer ran: the pinned base images pointed at digests that exist in no registry, the generated Dockerfile ran `pixi install` from a directory without the copied `pixi.toml`, and the recorded interpreter path named a directory pixi never creates. Builds now use verified base digests (pixi 0.66.0), install from `/opt`, and record the real interpreter path (`/opt/.pixi/envs/<name>/bin/python`). The conda backend shared the nonexistent base digest and is fixed by the same repin; an integration test now verifies both pinned digests resolve and that a generated pixi image builds and runs end-to-end.
- Conda-backend environments without pip (e.g. R-only conda-forge envs) failed at image build: the generated Dockerfile now skips the pip layers when there is nothing to freeze, and live capture (`wfc register-env conda:<env>`) no longer requires a python binary in the environment.
- Conda-backend `register-env` builds failed at the final permissions step: the generated Dockerfile ran `chmod` on `/opt/conda` as the base image's non-root user, which does not own that directory (`chmod: changing permissions of '/opt/conda': Operation not permitted`). The permissions step now runs as root and then hands the build back to the base's default user, leaving the image's runtime user unchanged.
- `wfc register-method` reports script-discovery problems (missing or misnamed script files) as clean error messages instead of tracebacks.
- Environments built through the pixi/conda backends recorded a container reference (`<name>@sha256:<hex>`) that `wfc register-method` rejects, so a freshly built env could not be attached to any method. Builds now tag the local image under the `local/` namespace and record the accepted digest-pinned form `docker://local/<name>@sha256:<hex>`; `docker run` still resolves it against the locally built image. Envs registered before this fix need re-registering (`register-env --force`) to be usable with `register-method`.
- Any pipeline starting with an input-selector node — the standard shape for CLI and Canvas submissions — crashed `wfc run-step` with `KeyError: 'method'`, because the by-method-name node lookup assumed every node carries a `method` key. Non-method nodes (input selectors, run references) are now skipped in that lookup.
- Multi-output pipeline nodes: a consumer wired to a specific output slot now receives that output. `run_step` previously routed every consumer to the parent's first-recorded output regardless of which output slot the link named, silently delivering wrong data (e.g. a directory where a config file was expected) and making distinct-outputs-to-distinct-consumers pipelines impossible to express. The fix applies to live runs and to cache-hit re-runs that resolve through audit rows.
- The Canvas Artifacts tab was still empty for cache-hit runs: the export command followed the run's cache source, but the Canvas history provider never did — a cached run's record owns no outputs of its own, so the Artifacts tab, inline image preview, and Canvas export bundle all came back empty unless you hunted down the original run. The provider now follows the cache source the same way `wfc export` does, so cached runs list, preview, and export their artifacts by their own id.
- `wfc run-pipeline` never recorded cancelled runs. The pipeline-end walk that writes a `cancelled` Run row for each step skipped downstream of a failure reads the frozen pipeline document under `.runs/pipelines/<id>/`, and only Canvas submissions created that file — so after a CLI `--keep-going` run with a failed step, the skipped downstream steps left no trace in history. CLI runs now freeze the executed pipeline document the same way Canvas submissions do, and skipped steps get `cancelled` rows pointing at the failed upstream run.
- Two Canvas nodes running the same method now each show their own status, run ids, outputs, log, error and cache source. Previously both showed the pair's combined runs, and a failure beside a success read `mixed` on both. Cancellation after a failure follows the node each run belongs to, and renaming a run in History no longer changes what a later pipeline cancels.
- A step refused before it ran was invisible: the Canvas stayed "running", History counted no failure and downstream steps were silently skipped. The refused node now shows as failed with its message, and its descendants show as cancelled, with the Inspector banner naming the failed node instead of `?`.
- A method node wired to both a per-sample input selector and an upstream output or run reference (e.g. a raw image plus a mask) ran its upstream and then failed. The sample now reaches the method, is restored before the step runs, and is part of its cache key.
- Opening a run's lineage in the Canvas wired every edge to the upstream's first output and sample edges to the default `data` slot, so Lock All refused steps until edges were redrawn by hand. Edges now connect to the output and input slot the run actually used, root steps reading the same sample share one input selector, and the opened pipeline runs as-is and reuses its results. Opened lineages are laid out left to right by depth instead of at random overlapping positions.
- A node whose run was a cache hit now shows a `cached` badge instead of looking like one that ran.
- Lock All and Run no longer refuse because of parameter rows from a node you already moved away from in the Inspector.
- A method dropped from the sidebar now behaves like a loaded one: blank required parameters are caught before submit, the version chip survives save and reload, and system nodes keep their names across reloads. Deleting a node by any route clears the selection, and one undo restores it.
- The Lineages view shows a row for every re-run, including one where every step was a cache hit. The pipeline summary counts a cache-hit re-run as cached.
- Run Reference nodes showed every output as `csv`; they now show slot names with their real types.
- A step whose result was only on the remote was recomputed; it is now reused and pulled.
- Re-registering a method, module or environment left uncommitted changes that blocked the next run ("dirty repository").
- Runs launched from an unactivated environment (`wfc` not on PATH) were recorded as failed even when every job succeeded.
- For S3, SSH, GCS and Azure remotes, reachability is now actually checked through DVC, and a failure names the remote; a fresh S3 prefix with nothing pushed yet counts as reachable. After `dvc remote modify` or `dvc remote default`, wfc no longer reports "no remotes configured", and re-running `wfc init` no longer writes a duplicate remote section.
- A project moved to a new folder restores samples into its new location. A duplicate sample name is refused before anything is written to the cache. When a sample folder held more than one entry, a step could receive the wrong one.
- A step that failed before it started (e.g. its input could not be set up) showed "orphaned run"; it now shows the real error.
- `wfc cache prune --all --include-local --force` kept every unpushed entry while reporting "Pruned N item(s)"; it now prunes them.
- In sweep and override variants, a blank optional parameter is left out instead of being sent as `None`.
- The Canvas artifact-member route refuses paths that point outside the output (`..`, absolute or drive paths, backslashes, encoded traversal).
- A failed background run no longer raises an error in the server thread when its job entry disappears as the run ends.

## [0.5.0] — 2026-07-19

### Added
- `wfc demo`: one command populates an initialised project with a complete, runnable demo — a five-method pipeline (`preprocess → filter_cells → label`, branching into `summarize` and a per-sample `plot` figure) over three bundled samples, registered through the genuine registration path with its own container env — then opens the Canvas with the pipeline pre-wired. Requires `wfc init` first. `wfc demo --remove` tears down exactly what the demo added (entities, runs, files) and nothing else; cached output bytes remain until `wfc cache prune`.
- Inline image preview in the run detail Artifacts tab: browser-renderable image artifacts (png, jpg, jpeg, gif, webp, svg) show a thumbnail that opens a full-size lightbox on click. Other artifact types are unchanged.
- Descendants tab in the Canvas History view, now the default: per-sample trees of executed runs nested by lineage. Cache hits and filter-hidden runs are omitted from the tree, with their children promoted to the nearest visible ancestor; the filter bar gains collapse-all / expand-all controls. The view switcher is reordered Descendants | Lineages | Pipelines.
- Cached-run marking in the Lineages view: run cards that were served from cache get an amber border and a CACHED pill, and the status summary gains a cached count. Child run rows in the Pipelines view get the same pill.
- Archive tracking in the Canvas toolbar: a badge shows how many completed runs still have unarchived cached outputs (e.g. after an interrupted run), turns into a live progress indicator with a per-run popover while archiving, and offers an "Archive now" action. Backed by new server endpoints (`GET /api/wfc/archive-status`, `POST /api/wfc/cache/archive`); archiving now commits each run's outputs as they land instead of in one batch at the end.

### Changed
- Registered env images no longer need workflow-canvas installed. `wfc run-step` now executes the method script directly under the env's interpreter instead of dispatching through `python -m wfc exec-method` — a plain analysis env (e.g., a cellpose conda env) registered with `wfc register-env` now runs pipeline steps as documented.
- `register-env` records each env's Python interpreter path in `.wfc/envs.json` (computed for pixi/conda backends; BYO defaults to `python` with a registration-time `--python` override). Envs registered before this change keep working via per-backend defaults — no re-registration needed.
- The `wfc demo` image no longer installs workflow-canvas — it now contains only what the demo methods use (wfc-client and matplotlib), making the first demo build smaller and faster.
- The `__demo__` name prefix is now reserved: `wfc register-module`, `register-method`, `register-sample`, `register-env`, and the Canvas Registry tab refuse names beginning with `__demo__` so that `wfc demo --remove` can safely identify demo-owned entries.
- `wfc seed` no longer inserts demo rows; it exits with a pointer to `wfc demo`. The old command produced a project that could not run (its rows bypassed env registration and contracts).
- History filter dropdowns now cascade: selecting a module narrows the Methods dropdown to that module's methods, and the Samples dropdown narrows to samples with runs matching the module/method filters. Selections that fall outside the narrowed lists are cleared automatically, so a filter can never stay active invisibly.
- Archive locations are now plain directory paths (`~/.wfc/archives/<project>`, `/data/wfc-archive`) or DVC remote URLs (`s3://...`); `wfc init` refuses `file://` values, unrecognized schemes, and remote schemes whose DVC plugin is not installed, each with an actionable message — interactively it re-prompts instead of failing. `wfc doctor` now validates the archive configuration through DVC itself, so a location DVC would reject at first push is caught at doctor time.
- Environment image builds show live progress on a TTY — a single status line with the current build step and elapsed time — instead of blocking silently until done. A failed build reports the last 40 lines of build output.
- The Lineages view hides fully-cached paths by default — a path whose every node was a cache hit duplicates the executed path it was cloned from. A count line shows how many were hidden and toggles them back; partially cached paths remain visible with the CACHED marking.
- Nested tables in the Canvas Registry tab use fixed shared column widths.

### Removed
- The internal `exec-method` CLI verb. A missing method script now fails on the host with a clear error before any container starts.

### Fixed
- The conda image recipe's permissions step targeted `/opt/conda/envs/<name>`, a directory that does not exist in the built image (micromamba installs into the `base` env at `/opt/conda`), failing every conda-backend `register-env` build. The chmod now targets the real env tree.
- Running with parallel jobs on Windows could crash the pipeline when two steps produced the same cached file at once: the losing writer's rename onto the winner's read-only file raised a permission error. Concurrent writers now deduplicate — same content hash means same bytes, and the first writer wins.
- Run records for registered container environments stored a hash of the environment fingerprint instead of the fingerprint itself, so the recorded value didn't match the manifest in `.wfc/envs.json`. Runs now record the manifest fingerprint directly.
- On Windows, `wfc init` wrote the default archive as a `file://C:/...` URL — a form DVC's config schema rejects — so every push from a fresh project failed even though init reported success.
- History-tab requests could intermittently return empty lists — most visibly a "No methods loaded" Methods dropdown despite registered methods — because the Canvas server cleared its in-memory run/module/method registries in place at the start of every reload while parallel requests were still reading them. A reload now builds the new registries aside and swaps them in atomically, so concurrent reads always see a complete snapshot.
- In a mixed run where upstream steps were cache hits but a downstream step re-executed (e.g. after a parameter change), the downstream step received an empty input list and crashed: cache-hit records own no output rows, so input resolution silently dropped the slot. Cached inputs now resolve through the original run's outputs, and a step whose parent input fails to resolve errors loudly instead of proceeding with nothing.
- `wfc export` and the Canvas Artifacts tab failed when pointed at a cache-hit run for the same reason — the run record owns no outputs of its own. Both now follow the run's cache source, so cached runs export and preview by their own id.
- Pipeline cards in the Pipelines view were titled with the first child run's method prefix, so two different submissions of same-shaped pipelines showed identical labels (two "preprocess" cards for two demo runs). Cards now show the name the pipeline was submitted with, falling back to the short pipeline id.

## [0.4.0] — 2026-07-17

### Added

- `wfc export` command for copying run outputs out of the results cache to a destination directory, with a matching artifact-export surface in Canvas.
- `wfc init` setup wizard for creating a new project and `wfc doctor` for diagnosing a broken setup (Docker, environments, project layout).
- Run-readiness gate in Canvas: the Run button checks the project can actually execute before dispatching, instead of failing mid-run.
- Packages panel in the Canvas environment registry showing the installed package contents of each environment (replaces the snapshot/diff view).
- `workflow-canvas` as the primary CLI command; `wfc` remains as a short alias.

### Changed

- Execution is container-only: every step runs in a Docker container declared via `env: container:<name>`. Host execution is no longer supported.
- Task-side helpers used inside containers (`load_input`, `save_artifact`, …) moved to the separate `wfc-client` package, published independently on PyPI.
- An output slot's type is now used directly as its file extension; the internal type-to-extension mapping table is gone, so any extension works in `method.yaml` without registration.
- Workflow authoring markers now come from `axiom-annotations` (`from axiom_annotations import workflow, task, Step, AutoStep`); the bundled `dflow` module is removed.
- The results cache is marked read-only after a step completes, so task code or manual edits can't silently corrupt provenance; use `wfc export` to take copies out.
- User documentation restructured into guide and reference tracks and published on Read the Docs.

### Fixed

- Running a pipeline from Canvas failed to resolve container environments because the `container:` prefix wasn't stripped before the manifest lookup.
- Canvas now shows downstream nodes as cancelled when an upstream step fails, instead of leaving them pending.
- The Output tab settles correctly for fast-completing runs that produce no console output.
- Numeric parameter types are handled correctly in the variables panel and value lists.
- Windows robustness: case-insensitive path comparisons and UTF-8 decoding of subprocess output.

### Removed

- Host (non-container) execution, including the `inherit` Docker backend.
- The deprecated generated `wfc/agent_docs` tree.

### Internal

- Provenance correctness test suite: cache-key sensitivity matrix, cache-integrity invariants, and tightened end-to-end tests.
- Canvas run-history reads converted to the SQLModel ORM with schema backfill for older project databases.

## [0.3.0] — 2026-06-23

First release under the Workflow Canvas name: `workflow-canvas` on PyPI with the `wfc` CLI, BSD-3-Clause licensed. Earlier `pm` releases (0.1.x–0.2.x) predate this changelog.
