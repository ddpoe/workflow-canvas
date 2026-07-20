# Changelog

Notable changes to Workflow Canvas. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
