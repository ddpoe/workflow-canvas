"""
wfc CLI — the bridge between Snakemake rules and the database.

Commands (called by each Snakemake rule):
  register_run       — insert a runs row (status='running'), print run ID
  complete_run       — mark completed, write RunOutput rows (cache is authoritative)
  check_cache        — check if identical run exists (method+sample+params+parent), print run ID or NONE
  finalize_pipeline  — log successful pipeline completion
  fail_pipeline      — mark in-flight runs as failed, keep workspace for debugging
  resolve_input      — given a run ID, print the cache path for its output
  lookup_run         — (legacy) find most-recent completed run for method+sample
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import select

from dflow.core.decorators import workflow, task, Step, AutoStep

from .database import get_session, runs_dir, project_root as get_project_root
from .models import Method, MethodVersion, Module, Run, RunInput, RunOutput, Sample


# =============================================================================
# Helpers
# =============================================================================

def _run_archive_dir(run_id: int) -> Path:
    """Return the archive directory for a run: .runs/{id:08d}/"""
    return runs_dir() / f"{run_id:08d}"


def _ensure_wfc_shim() -> Path:
    """Build (idempotently) a directory that puts *only* ``wfc`` on sys.path.

    Returns the shim root — a directory whose sole entry is a single-file
    ``wfc/__init__.py`` stub containing one line:

        __path__ = [r"<absolute path to the real wfc package dir>"]

    When this stub is loaded as a Python package, Python rewrites
    ``wfc.__path__`` to the real location.  Subsequent submodule lookups
    (``wfc.method``, ``wfc.wfc_context``, …) are resolved against the real
    package via the namespace-package machinery.  Top-level imports of
    *siblings* of wfc (``pandas``, ``numpy``, ``sqlmodel``, …) are **not**
    satisfied by the shim dir — they fall through to the running
    interpreter's own ``site-packages``.

    Why this exists
    ---------------
    The naïve fix is ``wfc_env["PYTHONPATH"] = parent_of_wfc_init``.  When
    ``workflow-canvas`` is pip-installed, ``parent_of_wfc_init`` is the
    *host venv's* ``site-packages`` directory.  Propagating that into a
    pixi-env subprocess (which runs a *different* CPython with a
    different numpy/pandas ABI) causes `ImportError: numpy … compiled
    module file is _multiarray_umath.cp312-win_amd64.pyd` — the host
    venv's ABI-tagged extensions win over the pixi env's own copies.

    ADR-008 planned to eliminate this PYTHONPATH hack entirely
    (see ``docs/adrs/008-run-step-execution-layer.json``, "Zero-dependency
    RunContext").  That plan regressed when the ``@wfc_method`` decorator
    was introduced: method scripts now legitimately need ``from wfc.method
    import wfc_method`` to resolve in the pixi env.  The shim is the
    smallest possible resurrection of the PYTHONPATH hack — it exposes
    ``wfc`` and **nothing else** — so the pixi env's own numpy/pandas win.

    Implementation notes
    --------------------
    * Cache location lives under ``<project_root>/.wfc/cache/wfc-shim/``.
      Earlier versions used ``platformdirs.user_cache_dir`` (i.e.
      ``%LOCALAPPDATA%\\workflow-canvas\\Cache``) but Microsoft Store
      Python runs inside a UWP sandbox that silently redirects writes to
      ``%LOCALAPPDATA%`` into a per-package ``LocalCache`` directory.
      The host venv's ``Path.exists()`` is redirected too, so the shim
      appears to exist from inside MS Store Python — but a non-Store
      child process (e.g. the pixi env's Python) sees only the literal
      non-virtualized path, which is empty, and raises
      ``ModuleNotFoundError: No module named 'wfc'``.  Writing under the
      project root sidesteps every UWP filter driver and makes the path
      ground truth for every process, MS Store or not.
    * ``WFC_SHIM_CACHE_DIR`` (env var) overrides the default location.
      Intended as an escape hatch for CI with a read-only project
      checkout — point it at a writable tmp path.
    * Cache key is ``<version>-<md5(real_wfc_path)>`` so two venvs of the
      same workflow-canvas version (e.g. sibling projects) don't share a
      stale shim pointing at the wrong package directory.
    * First-run atomicity: write ``__init__.py.tmp`` then ``os.replace``.
      ``os.replace`` is atomic on Windows since Python 3.3, so racing
      Snakemake workers can't observe a half-written stub.
    * Called lazily from ``run_step`` (not at module-import time) so
      canvas startup stays cheap in the happy path.
    """
    import hashlib
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    import wfc as _wfc_pkg
    real_wfc_dir = Path(_wfc_pkg.__file__).parent.resolve()

    try:
        version = _pkg_version("workflow-canvas")
    except PackageNotFoundError:
        version = "unknown"
    path_key = hashlib.md5(str(real_wfc_dir).encode("utf-8")).hexdigest()[:12]

    override = os.environ.get("WFC_SHIM_CACHE_DIR")
    if override:
        cache_root = Path(override)
    else:
        cache_root = get_project_root() / ".wfc" / "cache" / "wfc-shim"
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        raise RuntimeError(
            f"Cannot create wfc-shim cache at {cache_root}: {exc}. "
            f"workflow-canvas needs a writable .wfc/cache/ under the project "
            f"root. If you're in CI with a read-only checkout, set "
            f"WFC_SHIM_CACHE_DIR to a writable location."
        ) from exc
    shim_root = cache_root / f"wfc-shim-{version}-{path_key}"
    shim_pkg = shim_root / "wfc"
    init_py = shim_pkg / "__init__.py"

    if init_py.exists():
        return shim_root

    shim_pkg.mkdir(parents=True, exist_ok=True)
    # Single-line stub: rewrite __path__ to the real wfc package dir.
    # See _ensure_wfc_shim() docstring above for the full rationale.
    content = (
        "# Auto-generated by wfc._ensure_wfc_shim — do not edit.\n"
        "# See wfc/cli.py::_ensure_wfc_shim for why this file exists.\n"
        f"__path__ = [r{str(real_wfc_dir)!r}]\n"
    )
    # Per-writer tmp name avoids two parallel Snakemake workers colliding on
    # the same "__init__.py.tmp" file (corrupt reads, or write-after-rename).
    # Content is fully determined by (version, path_key) — both encoded in
    # the dest path — so any successful replace, whoever wins, produces the
    # exact bytes every worker intended to write.
    import uuid as _uuid
    tmp = shim_pkg / f"__init__.py.{os.getpid()}.{_uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp, init_py)
    except (PermissionError, OSError):
        # Sibling writer already laid down the file — on SMB/UNC shares
        # Windows can refuse the rename if the dest handle is still open
        # elsewhere. Our work is done either way: the content is
        # deterministic per (version, real_wfc_dir), so whichever writer
        # won produced bytes identical to what we would have. Just clean
        # up our tmp and return. Re-raise only if the dest truly didn't
        # get written by anyone.
        if not init_py.exists():
            raise
        try:
            tmp.unlink()
        except OSError:
            pass
    return shim_root


# =============================================================================
# register_run
# =============================================================================

@task(purpose="Register a new run in the database with status='running'")
def register_run(
    method_name: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_id: int | None = None,
    parent_run_ids: list[int] | None = None,
    nf_process_name: str | None = None,
    pipeline_id: str | None = None,
) -> int:
    """Insert a new run (status='running') and return the run ID.

    ``parent_run_ids`` is a list of strings in the format ``"slot:id"`` for
    fan-in (e.g. ``["sources:5", "sources:8"]``) or plain ``"id"`` for
    single-input nodes.  The slot name is stored in ``RunInput.input_name``.
    """
    # Normalize to list of (slot, pid) tuples
    parent_entries: list[tuple[str, int]] = []
    if parent_run_ids:
        for entry in parent_run_ids:
            s = str(entry)
            if ":" in s:
                slot, pid_str = s.split(":", 1)
                parent_entries.append((slot, int(pid_str)))
            else:
                parent_entries.append(("upstream", int(s)))
    elif parent_run_id is not None:
        parent_entries.append(("upstream", int(parent_run_id)))

    with get_session() as session:
        from .models import Module
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            print(f"ERROR: module '{module_name}' not found in DB", file=sys.stderr)
            sys.exit(1)
        stmt = select(Method).where(
            Method.name == method_name,
            Method.module_id == mod.id,
        )
        method = session.exec(stmt).first()
        if method is None:
            print(f"ERROR: method '{method_name}' not found in module '{module_name}'", file=sys.stderr)
            sys.exit(1)

        run = Run(
            method_id=method.id,
            params=params,
            sample=sample,
            status="running",
            pipeline_id=pipeline_id,
            nf_process_name=nf_process_name,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id_val: int = run.id  # type: ignore[assignment]

        for slot, pid in parent_entries:
            run_input = RunInput(
                run_id=run_id_val,
                source_run_id=pid,
                input_name=slot,
            )
            session.add(run_input)
        if parent_entries:
            session.commit()

        # Create the archive directory so the method can write to it
        _run_archive_dir(run_id_val).mkdir(parents=True, exist_ok=True)

    return run_id_val


# =============================================================================
# complete_run  (hardlink archive → workspace, write sidecar)
# =============================================================================

@task(purpose="Mark a run as finished, write RunOutput rows (cache is authoritative storage)")
def complete_run(
    run_id: int,
    status: str = "completed",
    output_files: list[str] | None = None,
    metrics: dict | None = None,
    error_message: str | None = None,
    error_traceback: str | None = None,
) -> None:
    """Mark a run as finished and record output rows.

    ADR-018: The DVC cache is the authoritative storage for outputs;
    nothing is published to ``.runs/workspace/``.  Snakemake-visible
    completion is signaled via a zero-byte sentinel touched by
    ``run_step`` (not this function).  Content hashing and caching
    happen in the post-pipeline archive pass (or ``wfc cache archive``);
    RunOutput rows here have ``content_hash=NULL`` and ``artifact_path``
    pointing at the run-archive staging entry.

    Args:
        run_id: The run to complete.
        status: Final status (usually 'completed').
        output_files: Paths to output files (in run-archive staging area).
        metrics: Optional dict of metrics to store.
        error_message: Error message for failed runs (ADR 004).
        error_traceback: Error traceback for failed runs (ADR 004).
    """
    with get_session() as session:
        run = session.get(Run, run_id)
        if run is None:
            print(f"ERROR: run {run_id} not found", file=sys.stderr)
            sys.exit(1)

        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        if metrics:
            run.metrics = metrics
        # ADR 004: persist error information on failed runs
        if error_message is not None:
            run.error_message = error_message
        if error_traceback is not None:
            run.error_traceback = error_traceback

        # Record outputs (no content hashing; deferred archive pass handles that)
        for fpath in output_files or []:
            src = Path(fpath)
            if not src.exists():
                print(f"ERROR: output file not found: {fpath}", file=sys.stderr)
                sys.exit(1)

            # Upsert RunOutput: wfc_context._record_output() may have already written
            # this row with a proper artifact_type. If so, patch it rather than
            # creating a duplicate.  If not, create with "method_file" as the default.
            # content_hash is left NULL (deferred archiving).
            existing_ro = session.exec(
                select(RunOutput).where(
                    RunOutput.run_id == run_id,
                    RunOutput.output_name == src.name,
                )
            ).first()
            if existing_ro is not None:
                existing_ro.artifact_path = str(src)
            else:
                ro = RunOutput(
                    run_id=run_id,
                    output_name=src.name,
                    artifact_path=str(src),
                    artifact_type="method_file",  # default; wfc_context sets the precise type
                )
                session.add(ro)

        session.commit()


# =============================================================================
# check_cache
# =============================================================================

def check_cache(
    method_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_id: int | None = None,
    parent_run_ids: list | None = None,
) -> int | None:
    """Check if an identical completed run exists.

    Matches on: method + sample + params (JSON-equal) + the exact set of
    ``(input_name, source_run_id)`` pairs in ``RunInput``.

    ``parent_run_ids`` entries may be ``"slot:id"`` strings or plain ints.

    Returns the run ID if found, None otherwise.
    """
    # Normalize to set of expected (slot, pid) tuples
    expected_inputs: set[tuple[str, int]] = set()
    if parent_run_ids:
        for entry in parent_run_ids:
            s = str(entry)
            if ":" in s:
                slot, pid_str = s.split(":", 1)
                expected_inputs.add((slot, int(pid_str)))
            else:
                expected_inputs.add(("upstream", int(s)))
    elif parent_run_id is not None:
        expected_inputs.add(("upstream", int(parent_run_id)))

    # Normalize params for comparison
    params_json = json.dumps(params, sort_keys=True) if params else None

    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == method_name)
            .where(Run.sample == sample)
            .where(Run.status == "completed")
        )
        stmt = stmt.order_by(Run.finished_at.desc())  # type: ignore[union-attr]
        candidates = session.exec(stmt).all()

        for run in candidates:
            # Check params match (JSON-normalized comparison)
            run_params_json = json.dumps(run.params, sort_keys=True) if run.params else None
            if run_params_json != params_json:
                continue

            # Check (slot, parent_id) pairs match exactly
            input_stmt = select(RunInput).where(RunInput.run_id == run.id)
            run_inputs = session.exec(input_stmt).all()
            actual_inputs = {
                (ri.input_name or "upstream", ri.source_run_id)
                for ri in run_inputs
            }

            if actual_inputs != expected_inputs:
                continue

            # Verify archived output still exists
            run_dir = _run_archive_dir(run.id)
            if not run_dir.exists():
                continue

            return run.id

    return None


# =============================================================================
# pre_run  (Gap 15: versioning + cache check + run registration in one step)
# =============================================================================

@workflow(purpose="Version-aware pre-run hook: git commit check, cache lookup, run registration")
def pre_run(
    method_name: str,
    module_name: str,
    sample: str,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    pipeline_id: str | None = None,
    nf_process_name: str | None = None,
    repo_path: str | None = None,
    git_commit: str | None = None,
    nid: str | None = None,
) -> tuple[str, int]:
    """Git-commit check, input fingerprinting, cache lookup, and run registration.

    Replaces the separate ``check_cache`` → ``register_run`` sequence in
    Snakemake rules with a single call that also enforces version discipline.

    Returns:
        ``("CACHED", source_run_id)`` — cache hit; source_run_id is the
        original completed run whose archive should be reused.  A new Run row
        with ``cache_source_run_id`` set is inserted for lineage.

        ``("NEW", new_run_id)`` — cache miss; a fresh Run row with
        ``version_id`` and ``cache_key`` is inserted with status='running'.

    Raises:
        DirtyRepositoryError: If the working tree has uncommitted changes and
            ``git_commit`` was not pre-supplied.
        ValueError: If the method or module is not found in the DB.
    """
    from .version import (
        DirtyRepositoryError,
        build_cache_key,
        build_code_fingerprint,
        build_input_fingerprint,
        capture_env_content,
        get_git_commit,
        get_or_create_version,
        store_env_content,
    )

    params = params or {}

    口 = Step(step_num=1, name="Resolve git commit",
             purpose="Check working tree is clean; raises DirtyRepositoryError if dirty")
    # git_commit is kept as audit metadata only — not part of cache key.
    if git_commit is None:
        git_commit = get_git_commit(repo_path)

    口 = Step(step_num=2, name="Look up method",
             purpose="Query database for module and method rows")
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        if mod is None:
            raise ValueError(f"Module '{module_name}' not found in DB")

        method = session.exec(
            select(Method).where(
                Method.name == method_name,
                Method.module_id == mod.id,
            )
        ).first()
        if method is None:
            raise ValueError(
                f"Method '{method_name}' not found in module '{module_name}'"
            )
        method_id: int = method.id  # type: ignore[assignment]
        method_env: str = method.env or "inherit"

    口 = Step(step_num=3, name="Build code fingerprint and version",
             purpose="Compute code fingerprint from registered source copy, get or create MethodVersion")
    method_source_dir = get_project_root() / "methods" / method_name
    code_fingerprint = build_code_fingerprint(method_source_dir)

    version_id = get_or_create_version(method_id, code_fingerprint, git_commit=git_commit)

    口 = Step(step_num=4, name="Normalize parent entries",
             purpose="Parse slot:id pairs and collect upstream run IDs")
    parent_entries: list[tuple[str, int]] = []
    upstream_run_ids: list[int] = []
    if parent_run_ids:
        for entry in parent_run_ids:
            s = str(entry)
            if ":" in s:
                slot, pid_str = s.split(":", 1)
                pid = int(pid_str)
            else:
                slot, pid = "upstream", int(s)
            parent_entries.append((slot, pid))
            upstream_run_ids.append(pid)

    口 = Step(step_num=5, name="Build input fingerprint",
             purpose="Cache key chaining via upstream Run.cache_key or sample hash")
    sample_ids: list[int] = []
    if not upstream_run_ids:
        # Root node — fingerprint the registered sample file
        with get_session() as session:
            samp = session.exec(
                select(Sample).where(Sample.name == sample)
            ).first()
            if samp is not None:
                sample_ids = [samp.id]  # type: ignore[list-item]

    input_fingerprint = build_input_fingerprint(upstream_run_ids, sample_ids)

    口 = Step(step_num=6, name="Capture env content",
             purpose="Resolve the method's env backend and capture a deterministic "
                     "content blob (lock + pip freeze, or interpreter identity + freeze)")
    project_dir_for_env = get_project_root()
    # ADR-019 Cycle D: ``container:<bare-name>`` (e.g. ``container:smoke-env``)
    # is the prefix-tagged form ``_resolve_env`` accepts at registration and
    # the form persisted on ``Method.env``. ``capture_env_content``'s manifest
    # short-circuit fires for bare names only, so strip the prefix here when
    # the payload is a bare manifest name (no ``@sha256:``). The direct ref
    # form ``container:<image>@sha256:<hex>`` falls through unchanged because
    # ``capture_env_content`` handles that branch itself.
    _env_for_capture = method_env
    if isinstance(method_env, str) and method_env.startswith("container:"):
        _payload = method_env[len("container:"):]
        if "@sha256:" not in _payload and not _payload.startswith("docker://"):
            _env_for_capture = _payload
    env_content = capture_env_content(_env_for_capture, project_dir_for_env)

    口 = Step(step_num=7, name="Store env content in DVC cache",
             purpose="Hash the env blob and store it under .dvc/cache/files/md5/; "
                     "the returned md5 is the env_fingerprint persisted on the Run row")
    env_fingerprint = store_env_content(env_content, project_dir_for_env)

    口 = Step(step_num=8, name="Build cache key",
             purpose="Combine code_fingerprint + params + input_fingerprint + env_fingerprint into cache key")
    cache_key = build_cache_key(
        code_fingerprint, params, input_fingerprint, env_fingerprint
    )

    口 = Step(step_num=9, name="Cache lookup and registration",
             purpose="Query by cache_key; return CACHED:{id} on hit or register new run on miss")
    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == method_name)
            .where(Run.sample == sample)
            .where(Run.status == "completed")
            .where(Run.cache_key == cache_key)
            .where(Run.cache_source_run_id == None)  # noqa: E711  exclude audit rows
        )
        stmt = stmt.order_by(Run.finished_at.desc())  # type: ignore[union-attr]
        hit = session.exec(stmt).first()

        if hit is not None:
            # Verify archive still exists (deleted archive = miss)
            if not _run_archive_dir(hit.id).exists():
                hit = None

    if hit is not None:
        # -- Cache HIT: insert an audit Run row, return the AUDIT row's ID --
        # The return value is the newly-inserted audit row, not the cached
        # source. Callers (run_step, sidecar writer) then record lineage and
        # write workspace sidecars with the audit ID, so downstream nodes
        # wire their ``parent_run_ids`` to this pipeline's sibling audits
        # instead of the old pipeline's source runs. The cached source is
        # still reachable via ``Run.cache_source_run_id`` when a caller
        # actually needs it (e.g. to find the RunOutput rows for restore).
        source_run_id: int = hit.id  # type: ignore[assignment]
        with get_session() as session:
            audit_run = Run(
                method_id=method_id,
                params=params,
                sample=sample,
                status="completed",
                pipeline_id=pipeline_id,
                nf_process_name=nf_process_name,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                version_id=version_id,
                cache_key=cache_key,
                cache_source_run_id=source_run_id,
                env_fingerprint=env_fingerprint,
                nid=nid,
            )
            session.add(audit_run)
            session.commit()
            session.refresh(audit_run)
            audit_run_id: int = audit_run.id  # type: ignore[assignment]

            # Record lineage for the audit row. Without these rows, PathsView
            # and the DescendantTree treat the audit run as disconnected —
            # every cache-hit branch of a fan-out pipeline shows up as an
            # orphan instead of as a terminal in its sample's sub-DAG.
            for slot, pid in parent_entries:
                session.add(RunInput(
                    run_id=audit_run_id,
                    source_run_id=pid,
                    input_name=slot,
                ))
            if parent_entries:
                session.commit()
        return ("CACHED", audit_run_id)

    # -- Cache MISS: register fresh run --
    with get_session() as session:
        mod = session.exec(
            select(Module).where(Module.name == module_name)
        ).first()
        run = Run(
            method_id=method_id,
            params=params,
            sample=sample,
            status="running",
            pipeline_id=pipeline_id,
            nf_process_name=nf_process_name,
            started_at=datetime.now(timezone.utc),
            version_id=version_id,
            cache_key=cache_key,
            env_fingerprint=env_fingerprint,
            nid=nid,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        new_run_id: int = run.id  # type: ignore[assignment]

        for slot, pid in parent_entries:
            session.add(RunInput(
                run_id=new_run_id,
                source_run_id=pid,
                input_name=slot,
            ))
        if parent_entries:
            session.commit()

        _run_archive_dir(new_run_id).mkdir(parents=True, exist_ok=True)

    return ("NEW", new_run_id)


# =============================================================================
# restore_sample (ADR-009)
# =============================================================================

@task(purpose="Restore a registered sample from DVC cache to data/samples/")
def restore_sample(
    name: str,
    content_hash: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Restore a sample file from the DVC cache into ``data/samples/{name}/``.

    Called by Snakemake ``restore_sample`` rules to lazily materialize
    sample files before root pipeline steps execute.

    If the sample's ``content_hash`` is NULL (legacy sample registered
    before DVC integration), exits with an error directing the user to
    re-register the sample.

    Idempotent: ``restore_from_cache`` handles integrity verification
    internally -- if the file already exists with matching content hash,
    the restore is skipped; if the hash mismatches, the file is replaced.

    Args:
        name: Sample identifier.
        content_hash: Expected content hash (passed from Snakemake rule).
            If not provided, looked up from the database.
        project_root: Project root directory (defaults to cwd).

    Raises:
        SystemExit: If the sample is not found, has no content_hash, or
            the cache entry is missing.
    """
    if project_root is None:
        project_root = get_project_root()

    with get_session() as session:
        sample = session.exec(
            select(Sample).where(Sample.name == name)
        ).first()
        if sample is None:
            print(f"ERROR: sample '{name}' not found in the database.", file=sys.stderr)
            sys.exit(1)

        # Use DB content_hash if not passed explicitly
        hash_val = content_hash or sample.content_hash
        if not hash_val:
            print(
                f"ERROR: Sample '{name}' has no content_hash — it was registered "
                f"before DVC integration. Re-register it with: "
                f"wfc register-sample --name {name} --source <path>",
                file=sys.stderr,
            )
            sys.exit(1)

        dest = Path(sample.registered_path)

        # Restore from DVC cache (handles idempotency: skips if dest
        # already exists with matching hash, replaces if mismatched)
        from .provenance import restore_from_cache, pull_cache
        restored = restore_from_cache(hash_val, dest, project_root)
        if not restored:
            # Try pulling from remote first, then restore again
            pull_cache([hash_val], project_root)
            restored = restore_from_cache(hash_val, dest, project_root)

        if not restored:
            print(
                f"ERROR: cannot restore sample '{name}' "
                f"(content_hash={hash_val} not found in DVC cache). "
                f"Try running: wfc pull",
                file=sys.stderr,
            )
            sys.exit(1)

        # Touch the Snakemake sentinel marker. ADR-009 keeps sample files in
        # the DVC cache rather than under data/samples/<name>/, so Snakemake
        # can't use the sample file itself as the rule output. We use a
        # sentinel at <project_root>/data/samples/<name>/.sample_ready and
        # create the parent directory if missing — the path is absolute via
        # get_project_root() so it is cwd-independent (critical for Windows
        # UNC shell rules where cmd.exe rewrites cwd to C:\Windows).
        sentinel = project_root / "data" / "samples" / name / ".sample_ready"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()


# =============================================================================
# cleanup_workspace
# =============================================================================

def cleanup_workspace() -> None:
    """Delete .runs/workspace/ directory.

    Called before generating a new pipeline to ensure a clean slate.
    Safe because workspace only contains hardlinks -- real data lives
    in .runs/{id}/ archive directories.
    """
    workspace = runs_dir() / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
        print("Cleaned up workspace")
    else:
        print("No workspace to clean up")


# =============================================================================
# finalize_pipeline
# =============================================================================

def finalize_pipeline(pipeline_id: str) -> None:
    """Log successful pipeline completion.

    Workspace is NOT deleted here -- it stays so re-runs of the same
    pipeline are instant ('Nothing to be done'). Cleanup happens at
    generation time via cleanup_workspace.
    """
    print(f"Pipeline {pipeline_id} completed successfully")


# =============================================================================
# fail_pipeline
# =============================================================================

def cancel_pipeline(pipeline_id: str, project_root: str | None = None) -> int:
    """Mark any in-flight runs for this pipeline as 'cancelled'.

    Sibling of :func:`fail_pipeline`.  User-initiated cancel: distinct from
    upstream-failure cancel (which writes :func:`_write_cancelled_rows`
    rows tagged with ``upstream_node_id``).  Idempotent — repeated calls
    on a pipeline whose runs are already terminal are a no-op.

    Args:
        pipeline_id: Pipeline whose in-flight rows should be flipped.
        project_root: Reserved (matches ``fail_pipeline`` style); not used
            today because we only touch the runs table.

    Returns:
        Number of rows flipped.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")
        )
        in_flight = session.exec(stmt).all()
        for run in in_flight:
            run.status = "cancelled"
            run.finished_at = datetime.now(timezone.utc)
            if run.error_message is None:
                run.error_message = "Cancelled by user"
        session.commit()
        n = len(in_flight)
        if n > 0:
            print(f"Marked {n} in-flight run(s) as cancelled for pipeline {pipeline_id}")
        return n


def fail_pipeline(pipeline_id: str) -> None:
    """Mark any in-flight runs for this pipeline as 'failed'.

    Already-completed runs are left untouched (their data is safe in .runs/{id}/).
    Workspace is preserved for debugging.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .where(Run.pipeline_id == pipeline_id)
            .where(Run.status == "running")
        )
        in_flight = session.exec(stmt).all()
        for run in in_flight:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            # ADR 004: only set error fields if not already populated
            # (the try/except in the generated rule may have already called
            # complete_run with error details before fail_pipeline runs)
            if run.error_message is None:
                run.error_message = f"Pipeline {pipeline_id} failed (orphaned run)"
            if run.error_traceback is None:
                run.error_traceback = "No traceback available (run was still in-flight when pipeline failed)"
        session.commit()

        n = len(in_flight)
        if n > 0:
            print(f"Marked {n} in-flight run(s) as failed for pipeline {pipeline_id}")
        else:
            print(f"No in-flight runs to mark for pipeline {pipeline_id}")
        print("Workspace preserved for debugging")


# =============================================================================
# _write_cancelled_rows  (pipeline-end diff-and-walk)
# =============================================================================


def _write_cancelled_rows(pipeline_id: str, project_root: str) -> int:
    """Persist first-class 'cancelled' Run rows for targets that did not run.

    Called at pipeline-end (both success and failure paths) from
    ``run_pipeline``. For every expected ``(node_id, sample, variant)``
    triple produced by ``expand_variant_combos``, if the DB does not
    already contain a matching Run row, walk upstream through the frozen
    DAG (``StepDef.depends_on``) until we hit a run with
    ``status='failed'``. Write a Run row with ``status='cancelled'`` and
    ``cancelled_due_to_run_id`` pointing at that failed ancestor.

    Idempotent: triples that already have any Run row (any status) are
    skipped. Re-invocation writes no duplicates.

    Always-on (D-4): on a fully-successful pipeline this finds zero
    missing triples and writes zero rows -- the cost of the walk is
    O(steps * samples * variants) DB reads.

    Tiebreak when multiple failed ancestors are equidistant (D-3):
    lowest ``run.id``.

    Args:
        pipeline_id: The pipeline execution ID to reconcile.
        project_root: Absolute path to the wfc project root (used to
            locate ``.runs/pipelines/<pid>/pipeline.json``).

    Returns:
        Number of cancelled rows written (useful for tests / logging).
    """
    from .snakemake_gen import load_pipeline, expand_variant_combos

    pipeline_json = (
        Path(project_root) / ".runs" / "pipelines" / pipeline_id / "pipeline.json"
    )
    if not pipeline_json.exists():
        # Nothing to reconcile -- no frozen pipeline doc means the run
        # never reached the snake-gen stage (or was cleaned up).
        return 0

    try:
        pipeline = load_pipeline(pipeline_json)
    except Exception as exc:
        print(
            f"[cancelled-walk] Failed to load pipeline.json for {pipeline_id}: {exc}",
            file=sys.stderr,
        )
        return 0

    steps = pipeline.steps
    if not steps:
        return 0

    # Build a map from StepDef.node_id → canvas label (stored as Run.nid
    # when present).  When a user labels a canvas node, that label is
    # persisted both into pipeline.json nodes (``label`` attribute) and
    # onto the Run row (``Run.nid``).  Using (nid, sample) as the triple
    # key avoids collisions when two canvas nodes share the same
    # method + sample + params (impossible in a normal pipeline, but
    # possible when the same method is parameter-swept in different
    # branches that happen to collapse identically).  Fall back to
    # (method_name, sample, params_fp) for steps without a label.
    node_id_to_nid: dict[str, str] = {}
    try:
        raw_pipeline_doc = json.loads(pipeline_json.read_text())
        for raw_node in raw_pipeline_doc.get("nodes", []):
            raw_id = str(raw_node.get("id", ""))
            label = raw_node.get("label") or None
            if raw_id and label:
                node_id_to_nid[raw_id] = str(label)
                # Legacy node_id == method_name fallback (see load_pipeline
                # line ~315): when pipeline uses plain int IDs and method
                # names are unique, StepDef.node_id collapses to method.
                method_name = raw_node.get("method")
                if method_name and raw_id.isdigit():
                    node_id_to_nid.setdefault(method_name, str(label))
    except (OSError, json.JSONDecodeError):
        # Malformed pipeline.json -- silently fall back to method-keyed
        # matching so the rest of the walk still functions.
        node_id_to_nid = {}

    # Build per-node variant→params map identical to generate_snakefile's
    # resolved_params (keyed by node_id; fall back to method_name; default
    # variant "default" when no param_sets entry exists).
    resolved_params: dict[str, dict[str, dict]] = {}
    for step in steps:
        resolved_params[step.node_id] = pipeline.param_sets.get(
            step.node_id,
            pipeline.param_sets.get(
                step.method_name, {"default": step.params}
            ),
        )

    # Pad: every node carries every variant name (matching snake-gen's
    # behaviour so expand_variant_combos enumerates exhaustively).
    all_variant_names: set[str] = set()
    for variants in resolved_params.values():
        all_variant_names.update(variants.keys())
    variant_names = sorted(all_variant_names) if all_variant_names else ["default"]
    for step in steps:
        for vname in variant_names:
            if vname not in resolved_params[step.node_id]:
                resolved_params[step.node_id][vname] = step.params

    combos = expand_variant_combos(
        steps=steps,
        samples=pipeline.samples,
        resolved_params=resolved_params,
        explicit_combos=pipeline.explicit_combos,
    )

    # DAG adjacency keyed by node_id (upstream parents for BFS).
    step_by_nid: dict[str, Any] = {s.node_id: s for s in steps}

    with get_session() as session:
        # Pre-load all Run rows for this pipeline_id plus the Method and
        # Module names we need for triple-matching and for writing new
        # cancelled rows.
        existing_runs = session.exec(
            select(Run).where(Run.pipeline_id == pipeline_id)
        ).all()

        # Build set of actual triples seen in the DB.
        # triple = (node_id, sample, params_fingerprint)
        method_by_id: dict[int, Method] = {}
        module_name_by_method_id: dict[int, str] = {}

        # Batch-load methods to resolve method_id -> (name, module_id)
        method_ids = {r.method_id for r in existing_runs if r.method_id is not None}
        if method_ids:
            for m in session.exec(select(Method).where(Method.id.in_(method_ids))).all():
                method_by_id[m.id] = m  # type: ignore[index]
            mod_ids = {m.module_id for m in method_by_id.values()}
            if mod_ids:
                mods = session.exec(select(Module).where(Module.id.in_(mod_ids))).all()
                mod_name_by_id = {m.id: m.name for m in mods}
                for mid, m in method_by_id.items():
                    module_name_by_method_id[mid] = mod_name_by_id.get(m.module_id, "")

        # Helper: normalised params fingerprint for equality matching.
        def _fp(params: dict | None) -> str:
            return json.dumps(params or {}, sort_keys=True, default=str)

        # Triples that already have a Run row (any status). The walk
        # uses this set for its presence check (idempotency + cache-hit
        # safety), so we include cancelled rows too.
        #
        # Preferred keying is (nid, sample, "") when the Run has a
        # non-empty ``nid`` and the pipeline carries a matching label --
        # this disambiguates two canvas nodes that share method+sample+
        # params but occupy distinct graph positions.  Falls back to the
        # original (method_name, sample, params_fingerprint) when no nid
        # is present (pre-existing pipelines without labels still work).
        actual_triples: set[tuple[str, str, str]] = set()
        for r in existing_runs:
            m = method_by_id.get(r.method_id)
            if m is None:
                continue
            if r.nid and r.nid in node_id_to_nid.values():
                # Nid-based keying: ("__nid__", nid, sample)
                actual_triples.add(("__nid__", r.nid, r.sample or ""))
            actual_triples.add((m.name, r.sample or "", _fp(r.params)))

        # Failed-run lookup keyed by triple -> lowest run.id (D-3 tiebreak).
        # Both keying styles are populated so the BFS walk can look up via
        # whichever discriminator is available on the ancestor step.
        failed_by_triple: dict[tuple[str, str, str], int] = {}
        for r in existing_runs:
            if r.status != "failed":
                continue
            m = method_by_id.get(r.method_id)
            if m is None:
                continue
            keys: list[tuple[str, str, str]] = [
                (m.name, r.sample or "", _fp(r.params))
            ]
            if r.nid and r.nid in node_id_to_nid.values():
                keys.append(("__nid__", r.nid, r.sample or ""))
            for key in keys:
                prior = failed_by_triple.get(key)
                if prior is None or (r.id is not None and r.id < prior):
                    failed_by_triple[key] = r.id  # type: ignore[assignment]

        # BFS upstream in the DAG from a given node to find the nearest
        # failed ancestor for a (sample, variant) combo. Same-depth hits
        # are collected together and tie-broken by lowest run id.
        def _find_failed_ancestor(
            start_nid: str, sample: str, variant: str
        ) -> int | None:
            from collections import deque

            seen: set[str] = set()
            # Frontier carries a list of node ids at the current depth;
            # BFS proceeds level-by-level so all equidistant hits land
            # in the same sweep.
            frontier: list[str] = list(step_by_nid.get(start_nid).depends_on) \
                if start_nid in step_by_nid else []
            while frontier:
                level_hits: list[int] = []
                next_frontier: list[str] = []
                for nid in frontier:
                    if nid in seen:
                        continue
                    seen.add(nid)
                    anc_step = step_by_nid.get(nid)
                    if anc_step is None:
                        continue
                    # Compute the sample this ancestor would have run on:
                    # collapsed ancestors always run as "__all__".
                    anc_sample = "__all__" if anc_step.sample_collapsed else sample
                    variants_for_anc = resolved_params.get(anc_step.node_id, {})
                    anc_params = variants_for_anc.get(variant, anc_step.params)
                    # Prefer nid-based lookup when the ancestor step has a
                    # canvas label -- disambiguates same-method collisions.
                    rid: int | None = None
                    anc_nid = node_id_to_nid.get(anc_step.node_id)
                    if anc_nid is not None:
                        rid = failed_by_triple.get(
                            ("__nid__", anc_nid, anc_sample)
                        )
                    if rid is None:
                        rid = failed_by_triple.get(
                            (anc_step.method_name, anc_sample, _fp(anc_params))
                        )
                    if rid is not None:
                        level_hits.append(rid)
                    # Always traverse through to upstream parents (a
                    # successful intermediate doesn't block finding the
                    # originating failure further upstream).
                    next_frontier.extend(anc_step.depends_on)
                if level_hits:
                    return min(level_hits)
                frontier = next_frontier
            return None

        # Modules/methods we need to look up for new rows. Group by
        # (module, method) so we only hit the DB once per unique pair.
        new_rows: list[Run] = []
        warned_missing_methods: set[tuple[str, str]] = set()

        for step in steps:
            for combo in combos:
                sample = combo["sample"]
                variant = combo["variant"]
                # Collapsed steps bake the sample axis to "__all__"; the
                # rest run once per sample.
                effective_sample = "__all__" if step.sample_collapsed else sample
                variants_for_step = resolved_params.get(step.node_id, {})
                params = variants_for_step.get(variant, step.params)
                # Presence check: prefer nid keying when the step has a
                # canvas label; fall back to method+sample+params_fp only
                # for steps without a label.  Falling back for labelled
                # steps would let a sibling branch's run mask a missing
                # same-method+params row here (the whole point of
                # nid-keying is to disambiguate that case).
                step_nid = node_id_to_nid.get(step.node_id)
                triple = (step.method_name, effective_sample, _fp(params))
                nid_triple: tuple[str, str, str] | None = (
                    ("__nid__", step_nid, effective_sample)
                    if step_nid is not None else None
                )
                if nid_triple is not None:
                    if nid_triple in actual_triples:
                        continue
                else:
                    if triple in actual_triples:
                        continue

                # Missing triple -- walk upstream to find the cause.
                cause_run_id = _find_failed_ancestor(
                    step.node_id, effective_sample, variant
                )
                if cause_run_id is None:
                    # No failed ancestor -- this can happen on a
                    # legitimately-skipped branch (shouldn't in practice).
                    # Architect guidance: write the row with NULL cause,
                    # log a warning. Choose to skip here to avoid
                    # polluting history with rows the user can't act on
                    # from the banner. Log for debugging.
                    print(
                        f"[cancelled-walk] no failed ancestor for "
                        f"({step.node_id}, {effective_sample}, {variant}) "
                        f"in pipeline {pipeline_id}; skipping",
                        file=sys.stderr,
                    )
                    continue

                # Look up method_id -- skip with a warning if the method
                # has been unregistered since the run was scheduled (D-1).
                method_key = (step.method_name, step.module_name)
                mod = session.exec(
                    select(Module).where(Module.name == step.module_name)
                ).first()
                if mod is None:
                    if method_key not in warned_missing_methods:
                        warned_missing_methods.add(method_key)
                        print(
                            f"[cancelled-walk] module '{step.module_name}' "
                            f"not registered; skipping cancelled rows for "
                            f"method '{step.method_name}'",
                            file=sys.stderr,
                        )
                    continue
                method_row = session.exec(
                    select(Method).where(
                        Method.name == step.method_name,
                        Method.module_id == mod.id,
                    )
                ).first()
                if method_row is None:
                    if method_key not in warned_missing_methods:
                        warned_missing_methods.add(method_key)
                        print(
                            f"[cancelled-walk] method "
                            f"'{step.module_name}.{step.method_name}' not "
                            f"registered; skipping cancelled row",
                            file=sys.stderr,
                        )
                    continue

                new_row = Run(
                    method_id=method_row.id,  # type: ignore[arg-type]
                    params=params,
                    sample=effective_sample,
                    status="cancelled",
                    pipeline_id=pipeline_id,
                    started_at=None,
                    finished_at=None,
                    cancelled_due_to_run_id=cause_run_id,
                    nid=step_nid,
                )
                new_rows.append(new_row)
                # Mark the triple as now present so repeated steps don't
                # double-write (paranoia against combo duplication).
                actual_triples.add(triple)
                if nid_triple is not None:
                    actual_triples.add(nid_triple)

        if new_rows:
            for r in new_rows:
                session.add(r)
            session.commit()
            print(
                f"[cancelled-walk] wrote {len(new_rows)} cancelled row(s) "
                f"for pipeline {pipeline_id}"
            )

    return len(new_rows)


# =============================================================================
# resolve_input
# =============================================================================

def resolve_input(run_id: int) -> str | None:
    """Return the cache path for a given run ID's output (ADR-018).

    Two-tier resolution:
      CACHE       -- content_hash in local DVC cache; return cache path.
      REMOTE-PULL -- not in local cache; pull from configured DVC remote,
                     then return cache path.
      FAIL        -- both attempts fail; return None.

    The cache is the authoritative store (ADR-018). The returned path is
    the actual cache location, not a workspace copy. Downstream consumers
    read directly from the cache.

    For runs predating content-hash integration (content_hash is None),
    the artifact_path is returned as-is (no cache to consult).

    Args:
        run_id: The run whose output to resolve.

    Returns:
        Cache path string if the output is available locally or pullable;
        None if the run/output is not found or resolution failed.
    """
    project_dir = get_project_root()

    with get_session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return None

        output_stmt = select(RunOutput).where(RunOutput.run_id == run_id)
        ro = session.exec(output_stmt).first()
        if ro is None:
            return None

        # No content_hash -- backward compat: return original artifact_path.
        if not ro.content_hash:
            return ro.artifact_path

        from .provenance import _cache_path, pull_cache

        cache_path = _cache_path(project_dir, ro.content_hash)

        if cache_path.exists():
            print(f"resolve_input: CACHE (run {run_id})", file=sys.stderr)
            return str(cache_path)

        # REMOTE-PULL: ask the configured remote to populate local cache.
        try:
            pull_cache([ro.content_hash], project_dir)
        except Exception as exc:
            print(
                f"resolve_input: pull_cache raised for run {run_id}: {exc}",
                file=sys.stderr,
            )

        if cache_path.exists():
            print(f"resolve_input: REMOTE-PULL (run {run_id})", file=sys.stderr)
            return str(cache_path)

        # FAIL: not in local cache and remote pull didn't materialize it.
        print(
            f"resolve_input: FAIL (run {run_id}, "
            f"content_hash={ro.content_hash} not in local or remote cache)",
            file=sys.stderr,
        )
        return None


def resolve_sample(name: str) -> str | None:
    """Return the cache path for a registered sample (ADR-018).

    Two-tier resolution mirroring ``resolve_input``:
      CACHE       -- sample content_hash in local DVC cache; return path.
      REMOTE-PULL -- not local; pull from configured remote, then return.
      FAIL        -- both attempts fail; return None.

    Args:
        name: Sample identifier (``Sample.name``).

    Returns:
        Cache path string if the sample bytes are available locally or
        pullable; None if the sample is missing, has no content_hash, or
        resolution failed.
    """
    project_dir = get_project_root()

    with get_session() as session:
        sample = session.exec(
            select(Sample).where(Sample.name == name)
        ).first()
        if sample is None:
            return None
        if not sample.content_hash:
            return None

        from .provenance import _cache_path, pull_cache

        cache_path = _cache_path(project_dir, sample.content_hash)

        if cache_path.exists():
            print(f"resolve_sample: CACHE ({name})", file=sys.stderr)
            return str(cache_path)

        try:
            pull_cache([sample.content_hash], project_dir)
        except Exception as exc:
            print(
                f"resolve_sample: pull_cache raised for sample {name}: {exc}",
                file=sys.stderr,
            )

        if cache_path.exists():
            print(f"resolve_sample: REMOTE-PULL ({name})", file=sys.stderr)
            return str(cache_path)

        print(
            f"resolve_sample: FAIL (sample={name}, "
            f"content_hash={sample.content_hash} not in local or remote cache)",
            file=sys.stderr,
        )
        return None


# =============================================================================
# register_sample
# =============================================================================

@task(purpose="Register a data sample by copying it into the managed data directory")
def register_sample(
    name: str,
    source_path: Path,
    project_root: Path | None = None,
    registration_mode: str = "copy",
) -> Path:
    """Copy a data file into ``data/samples/{name}/``, content-hash it via
    DVC, store it in the DVC cache, and record it in the DB.

    DVC must be configured (``[dvc]`` section in ``wf-canvas.toml``).
    If DVC is not configured, raises ``DvcNotConfiguredError`` before any
    file copy or DB write occurs.

    If a sample with the same name already exists, raises an error.

    Args:
        name: Sample identifier (e.g. 'CFPAC_ERKi').
        source_path: Path to the source data file.
        project_root: Project root directory (defaults to cwd).
        registration_mode: Only ``"copy"`` is implemented. ``"link"`` is
            reserved for a future path-only registration mode.

    Returns:
        The path to the registered copy of the file.

    Raises:
        FileNotFoundError: If the source file does not exist.
        NotImplementedError: If ``registration_mode != "copy"``.
        ValueError: If a sample with this name is already registered.
        DvcNotConfiguredError: If DVC is not configured.
    """
    if registration_mode != "copy":
        raise NotImplementedError(
            f"registration_mode={registration_mode!r} is not implemented. "
            "Only 'copy' is supported."
        )
    source_path = Path(source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    if project_root is None:
        project_root = get_project_root()

    # DVC gate: require [dvc] config before any file operations (ADR-009/018)
    from .provenance import ensure_dvc_ready, hash_path, cache_file
    from .models import PushStatus
    ensure_dvc_ready(project_root)

    # Per ADR-009: do NOT copy into data/samples/. The DVC cache is the sole
    # store; data/samples/ is ephemeral workspace, populated lazily by the
    # Snakemake restore_sample rule. registered_path is a contract (where the
    # file WILL be restored) not a claim that it exists there now.
    dest = project_root / "data" / "samples" / name / source_path.name

    # Capture size and mtime FROM THE SOURCE FILE.
    src_stat = source_path.stat()
    src_file_size = src_stat.st_size
    src_file_mtime = src_stat.st_mtime

    # Content-hash the source and store in DVC cache (ADR-009)
    content_hash = hash_path(source_path)
    # register_sample owns its source — user-provided file should not be moved.
    cache_file(source_path, content_hash, project_root, move=False)

    # ADR-018: in-pipeline registrations enqueue onto the push worker
    # (WFC_PIPELINE_ID env set by run_pipeline).  Standalone CLI mode does a
    # one-shot synchronous push so the user has immediate feedback.
    in_pipeline = bool(os.environ.get("WFC_PIPELINE_ID"))
    push_status = PushStatus.deferred
    pushed_at = None
    push_error = None

    from .remote import has_remote_configured
    remote_configured = has_remote_configured(project_root)

    if remote_configured:
        if in_pipeline:
            push_status = PushStatus.pending  # worker will pick it up
        else:
            # Standalone: synchronous push, mark terminal state.
            from .remote import push as remote_push
            try:
                remote_push([content_hash], project_root)
                push_status = PushStatus.pushed
                pushed_at = datetime.now(timezone.utc)
            except Exception as exc:
                push_status = PushStatus.failed
                push_error = str(exc)
                print(
                    f"WARNING: DVC push failed ({exc}); sample is in local cache only.",
                    file=sys.stderr,
                )

    # Record in DB
    with get_session() as session:
        existing = session.exec(
            select(Sample).where(Sample.name == name)
        ).first()
        if existing is not None:
            raise ValueError(f"Sample '{name}' is already registered (id={existing.id})")

        sample = Sample(
            name=name,
            source_path=str(source_path),
            registered_path=str(dest),
            file_type=source_path.suffix.lstrip("."),
            file_size=src_file_size,
            file_mtime=src_file_mtime,
            registration_mode="copy",
            content_hash=content_hash,
            push_status=push_status,
            pushed_at=pushed_at,
            push_error=push_error,
        )
        session.add(sample)
        session.commit()

    return dest


# =============================================================================
# Push worker (ADR-018 Task 7)
# =============================================================================

# Exponential backoff (seconds) per attempt index.  Index 0 = first retry.
PUSH_BACKOFFS = (1, 4, 16, 60)
PUSH_MAX_ATTEMPTS = 5
PUSH_POLL_INTERVAL = 2.0  # seconds between worker ticks


def _reset_orphan_pushes(project_root: Path, *, age_days: int = 7) -> int:
    """Reset push_attempts on stale pending/in_flight/failed rows.

    Called on ``run_pipeline`` startup before spawning the worker.  Re-enters
    rows that finished in the last ``age_days`` into the retry budget.

    Args:
        project_root: wfc project root (for logging only).
        age_days: How recent ``finished_at`` must be to qualify.

    Returns:
        Number of rows reset.
    """
    from .models import PushStatus, RunOutput, Sample, Run

    cutoff = datetime.now(timezone.utc).timestamp() - age_days * 86400
    n = 0
    with get_session() as session:
        # RunOutput: join Run to get finished_at.
        ro_rows = session.exec(
            select(RunOutput)
            .join(Run, RunOutput.run_id == Run.id)  # type: ignore[arg-type]
            .where(RunOutput.push_status.in_([  # type: ignore[union-attr]
                PushStatus.pending.value,
                PushStatus.in_flight.value,
                PushStatus.failed.value,
            ]))
            .where(Run.finished_at.isnot(None))  # type: ignore[union-attr]
        ).all()
        for r in ro_rows:
            run = session.get(Run, r.run_id)
            if run and run.finished_at and run.finished_at.timestamp() > cutoff:
                r.push_attempts = 0
                r.push_status = PushStatus.pending.value
                session.add(r)
                n += 1
        # Sample: gate on registered_at (the Sample analog of Run.finished_at)
        # so the worker only re-enters samples registered within the recovery
        # window. Prevents thundering-herd recovery of years-old samples on
        # every pipeline startup.
        sample_rows = session.exec(
            select(Sample)
            .where(Sample.push_status.in_([  # type: ignore[union-attr]
                PushStatus.pending.value,
                PushStatus.in_flight.value,
                PushStatus.failed.value,
            ]))
            .where(Sample.registered_at.isnot(None))  # type: ignore[union-attr]
        ).all()
        for s in sample_rows:
            if s.registered_at and s.registered_at.timestamp() > cutoff:
                s.push_attempts = 0
                s.push_status = PushStatus.pending.value
                session.add(s)
                n += 1
        session.commit()
    if n:
        print(f"[push_worker] reset {n} orphan push row(s) for retry")
    return n


def _push_worker_tick(project_root: Path) -> tuple[int, int]:
    """Single push-worker tick: scan rows, push in batch, update DB.

    Returns:
        Tuple of (pushed_count, remaining_count).  ``remaining_count`` is
        the rows still in pending/failed state after this tick.
    """
    from .models import PushStatus, RunOutput, Sample
    from .remote import push as remote_push

    # 1. Snapshot rows needing push.
    with get_session() as session:
        ro_rows = list(session.exec(
            select(RunOutput)
            .where(RunOutput.push_status.in_([  # type: ignore[union-attr]
                PushStatus.pending.value, PushStatus.failed.value
            ]))
            .where(RunOutput.push_attempts < PUSH_MAX_ATTEMPTS)
            .where(RunOutput.content_hash.isnot(None))  # type: ignore[union-attr]
        ).all())
        sample_rows = list(session.exec(
            select(Sample)
            .where(Sample.push_status.in_([  # type: ignore[union-attr]
                PushStatus.pending.value, PushStatus.failed.value
            ]))
            .where(Sample.push_attempts < PUSH_MAX_ATTEMPTS)
            .where(Sample.content_hash.isnot(None))  # type: ignore[union-attr]
        ).all())

        # Detach IDs and hashes for the actual push (avoid holding the
        # session open across a network call).
        ro_ids_by_hash: dict[str, list[int]] = {}
        for r in ro_rows:
            ro_ids_by_hash.setdefault(r.content_hash, []).append(r.id)  # type: ignore[arg-type]
        sample_ids_by_hash: dict[str, list[int]] = {}
        for s in sample_rows:
            sample_ids_by_hash.setdefault(s.content_hash, []).append(s.id)  # type: ignore[arg-type]

    all_hashes = set(ro_ids_by_hash) | set(sample_ids_by_hash)
    if not all_hashes:
        return 0, 0

    # 2. Mark rows in_flight (best-effort, separate session).
    with get_session() as session:
        for r in session.exec(
            select(RunOutput).where(RunOutput.id.in_([  # type: ignore[union-attr]
                rid for ids in ro_ids_by_hash.values() for rid in ids
            ]))
        ).all():
            r.push_status = PushStatus.in_flight.value
            session.add(r)
        for s in session.exec(
            select(Sample).where(Sample.id.in_([  # type: ignore[union-attr]
                sid for ids in sample_ids_by_hash.values() for sid in ids
            ]))
        ).all():
            s.push_status = PushStatus.in_flight.value
            session.add(s)
        session.commit()

    # 3. Push.
    push_error: str | None = None
    succeeded: set[str] = set()
    try:
        result = remote_push(list(all_hashes), project_root)
        failed_objs = getattr(result, "failed", None) or []
        failed_hashes = {
            getattr(o, "value", None) or getattr(getattr(o, "hash_info", None), "value", None)
            for o in failed_objs
        }
        succeeded = all_hashes - {h for h in failed_hashes if h}
    except Exception as exc:
        push_error = str(exc)
        succeeded = set()  # treat entire batch as failed

    # 4. Update DB.
    now = datetime.now(timezone.utc)
    pushed_count = 0
    with get_session() as session:
        for h, ids in ro_ids_by_hash.items():
            for rid in ids:
                row = session.get(RunOutput, rid)
                if row is None:
                    continue
                if h in succeeded:
                    row.push_status = PushStatus.pushed.value
                    row.pushed_at = now
                    row.push_error = None
                    pushed_count += 1
                else:
                    row.push_attempts = (row.push_attempts or 0) + 1
                    row.push_error = push_error or "push failed"
                    row.push_status = PushStatus.failed.value
                session.add(row)
        for h, ids in sample_ids_by_hash.items():
            for sid in ids:
                row = session.get(Sample, sid)
                if row is None:
                    continue
                if h in succeeded:
                    row.push_status = PushStatus.pushed.value
                    row.pushed_at = now
                    row.push_error = None
                    pushed_count += 1
                else:
                    row.push_attempts = (row.push_attempts or 0) + 1
                    row.push_error = push_error or "push failed"
                    row.push_status = PushStatus.failed.value
                session.add(row)
        session.commit()

    remaining = len(all_hashes) - len(succeeded)
    return pushed_count, remaining


def _push_worker_loop(project_root: Path, stop_event) -> None:
    """Background loop: tick every ``PUSH_POLL_INTERVAL`` until stop_event set.

    Exponential backoff is applied when a tick yields zero successful
    pushes — the next tick waits longer.

    Args:
        project_root: wfc project root.
        stop_event: threading.Event; loop exits when set.
    """
    import time
    backoff_idx = 0
    while not stop_event.is_set():
        try:
            pushed, remaining = _push_worker_tick(project_root)
        except Exception as exc:
            print(f"[push_worker] tick raised: {exc}", file=sys.stderr)
            pushed, remaining = 0, 0
        if pushed:
            print(f"[push_worker] pushed {pushed} hashes ({remaining} remaining)")
            backoff_idx = 0  # reset on progress
        # No work and no failures -> short sleep; on repeated empty ticks
        # we don't expand backoff (it's a poll loop, not a retry loop).
        wait = PUSH_POLL_INTERVAL
        if remaining and not pushed:
            # All failures: back off.
            wait = PUSH_BACKOFFS[min(backoff_idx, len(PUSH_BACKOFFS) - 1)]
            backoff_idx += 1
        if stop_event.wait(wait):
            break
    # Final drain pass on clean shutdown.
    try:
        _push_worker_tick(project_root)
    except Exception:
        pass


# =============================================================================
# run-pipeline  — generate Snakefile + invoke snakemake
# =============================================================================

@workflow(
    purpose="Generate a Snakefile from a pipeline JSON and execute it via Snakemake",
    inputs="Pipeline JSON path, project root, cores",
    outputs="Completed pipeline; all run rows written to DB",
)
def run_pipeline(
    pipeline_path: str,
    project_root: str | None = None,
    wfc_root: str | None = None,
    cores: int = 4,
    snakefile_path: str | None = None,
    pipeline_id: str | None = None,
    capture_output: bool = False,
    archive: bool = True,
    keep_going: bool = False,
    process_registry=None,
    is_cancelled=None,
) -> int:
    """Generate a Snakefile from a pipeline JSON and run it with Snakemake.

    This is the single entry-point the GUI (or any caller) uses to execute a
    pipeline.  It handles Snakefile generation, git-commit resolution, and
    Snakemake invocation internally -- callers never need to touch snakemake
    directly.

    After successful pipeline completion, an archive pass hashes and caches
    all un-archived outputs (deferred archiving).  Controlled by the
    ``archive`` parameter (default: True).

    Args:
        pipeline_path: Path to the pipeline JSON file.
        project_root: Root of the wfc project (git repo with method commits).
            Defaults to the current working directory.
        wfc_root: Path added to PYTHONPATH in worker processes so ``import wfc``
            works.  Defaults to the directory containing the ``wfc`` package
            (i.e. the location of this file's parent).
        cores: Number of Snakemake cores (default: 4).
        snakefile_path: Where to write the generated Snakefile.  Defaults to
            ``<project_root>/Snakefile``.
        pipeline_id: Optional caller-provided pipeline ID.  When provided,
            this ID is used instead of generating a new UUID.  This ensures
            the canvas server and run_pipeline share the same ID for status
            tracking.  When omitted (CLI usage), a new UUID is generated.
        capture_output: When True, redirect stdout/stderr to log files in the
            pipeline log directory (used by the canvas server for log capture).
            When False (default, CLI usage), leave stdout/stderr connected to
            the terminal.
        archive: When True (default), run the deferred archive pass after
            successful pipeline completion.  When False, leave outputs
            un-archived (content_hash=NULL).
        keep_going: When True, pass ``--keep-going`` to Snakemake so a
            failure in one job doesn't cancel jobs that have no dependency
            on the failed one.  Useful for fan-out pipelines where each
            sample is independent: one bad sample still lets the others
            complete.  Default False (Snakemake's default fail-fast).

    Returns:
        Snakemake exit code (0 = success).
    """
    import subprocess as _sp
    from pathlib import Path as _P

    from .snakemake_gen import load_pipeline, generate_snakefile

    _project_root = str(_P(project_root).resolve() if project_root else _P.cwd())

    # Resolve wfc_root: caller can supply explicitly (test always should); in
    # production we discover it from wfc's own __file__ (installed package).
    if wfc_root is None:
        import wfc as _wfc_pkg
        wfc_root = str(_P(_wfc_pkg.__file__).parent.parent)

    import uuid as _uuid

    口 = AutoStep(step_num=1, name="Load pipeline")
    pipeline = load_pipeline(_P(pipeline_path))

    # ADR-018: cache is authoritative — workspace is obsolete.  Sweep the
    # legacy ``.runs/workspace/`` tree once at pipeline start (idempotent;
    # log one line whether it existed or not).
    _legacy_workspace = _P(_project_root) / ".runs" / "workspace"
    if _legacy_workspace.exists():
        import logging as _logging
        _ws_logger = _logging.getLogger("wfc.cli")
        try:
            shutil.rmtree(_legacy_workspace)
            _ws_logger.info("removed legacy workspace at %s", _legacy_workspace)
        except Exception as _ws_exc:
            _ws_logger.warning(
                "could not remove legacy workspace %s: %s",
                _legacy_workspace, _ws_exc,
            )

    # ADR 004: Generate pipeline ID and create log directories before Snakemake
    _pipeline_id = pipeline_id if pipeline_id is not None else str(_uuid.uuid4())
    _pipeline_log_dir = _P(_project_root) / ".runs" / "pipelines" / _pipeline_id
    _pipeline_log_dir.mkdir(parents=True, exist_ok=True)
    (_pipeline_log_dir / "runs").mkdir(exist_ok=True)

    # ADR-018 Task 7: orphan recovery + push worker.  Only spin up when a
    # remote is configured -- otherwise rows stay in `deferred` and the
    # worker has nothing to do.
    import threading as _threading
    _push_stop = _threading.Event()
    _push_thread = None
    try:
        from .remote import has_remote_configured as _has_remote
        _push_enabled = _has_remote(_P(_project_root))
    except Exception:
        _push_enabled = False
    if _push_enabled:
        try:
            _reset_orphan_pushes(_P(_project_root))
        except Exception as _orph_exc:
            print(f"[run_pipeline] orphan recovery skipped: {_orph_exc}", file=sys.stderr)
        _push_thread = _threading.Thread(
            target=_push_worker_loop,
            args=(_P(_project_root), _push_stop),
            daemon=True,
            name="wfc-push-worker",
        )
        _push_thread.start()

    def _drain_push_worker(*, cancel: bool = False) -> None:
        """Signal worker stop; wait for it unless cancelling."""
        if _push_thread is None:
            return
        _push_stop.set()
        if not cancel:
            # NOTE: 600s join timeout bounds finalize-drain latency. A
            # 50GB push tail at ~100MB/s takes ~8 minutes, which exceeds
            # the old 120s and would have left rows in `in_flight` when
            # the pipeline returned. 600s covers realistic dataset sizes
            # at typical bandwidth; longer tails are abandoned and re-
            # entered by _reset_orphan_pushes on the next pipeline run.
            _push_thread.join(timeout=600)

    口 = AutoStep(step_num=2, name="Generate Snakefile")
    content = generate_snakefile(
        pipeline, wfc_root, project_root=_project_root, pipeline_id=_pipeline_id,
        pipeline_json_path=str(_P(pipeline_path).resolve()),
    )

    口 = Step(
        step_num=3,
        name="Write Snakefile to disk",
        purpose="Serialize the generated Snakefile content to <project_root>/Snakefile",
        inputs="Generated Snakefile content string",
        outputs="Snakefile written to sf_path",
    )
    sf_path = _P(snakefile_path) if snakefile_path else _pipeline_log_dir / "Snakefile"
    sf_path.write_text(content, encoding="utf-8")
    print(f"Snakefile written to {sf_path}")

    口 = Step(
        step_num=4,
        name="Invoke Snakemake",
        purpose="Execute the generated Snakefile via snakemake subprocess to run all pipeline rule instances",
        inputs="Snakefile at sf_path, wfc project cwd, cores count",
        outputs="All pipeline runs completed; Run rows written to DB",
        critical="Executes all pipeline rules; raises RuntimeError on non-zero exit",
    )
    import os as _os
    _snake_env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    # ADR 004: pass pipeline log dir so generated Snakefile can find it
    _snake_env["WFC_PIPELINE_ID"] = _pipeline_id
    _snake_env["WFC_PIPELINE_LOG_DIR"] = str(_pipeline_log_dir)
    # Explicitly forward DATABASE_URL so isolated test databases (set via
    # monkeypatch.setenv) are always seen by Snakemake worker processes even
    # when the env-var inheritance chain is broken (e.g. CI, --forked workers).
    _db_url = _os.environ.get("DATABASE_URL")
    if _db_url:
        _snake_env["DATABASE_URL"] = _db_url
    # Capture stdout/stderr to log files only when requested (canvas server).
    # CLI users get normal terminal output (capture_output=False by default).
    _snake_cmd = ["snakemake", "--cores", str(cores), "--snakefile", str(sf_path)]
    if keep_going:
        _snake_cmd.append("--keep-going")
    if capture_output:
        _stdout_log = _pipeline_log_dir / "stdout.log"
        _stderr_log = _pipeline_log_dir / "stderr.log"
        with open(_stdout_log, "w", encoding="utf-8") as _out_f, \
             open(_stderr_log, "w", encoding="utf-8") as _err_f:
            proc = _sp.Popen(
                _snake_cmd,
                cwd=_project_root,
                env=_snake_env,
                stdout=_out_f,
                stderr=_err_f,
            )
            if process_registry is not None:
                try:
                    process_registry(proc)
                except Exception as _reg_exc:
                    print(
                        f"[run_pipeline] process_registry raised: {_reg_exc}",
                        file=sys.stderr,
                    )
            proc.wait()
            result_returncode = proc.returncode
        if result_returncode != 0:
            # If the caller signalled cancellation (e.g. canvas cancel
            # endpoint), the cancel handler owns row state -- skip
            # fail_pipeline so it doesn't overwrite ``cancelled`` rows.
            _was_cancelled = bool(is_cancelled and is_cancelled())
            # Flip in-flight rows to 'failed' BEFORE the walk so BFS can
            # find them as failed ancestors. Best-effort -- the walk is
            # still useful even if fail_pipeline hit an issue.
            if not _was_cancelled:
                try:
                    fail_pipeline(_pipeline_id)
                except Exception as _fp_exc:
                    print(
                        f"[run_pipeline] fail_pipeline raised: {_fp_exc}",
                        file=sys.stderr,
                    )
            try:
                _write_cancelled_rows(_pipeline_id, _project_root)
            except Exception as _walk_exc:
                print(
                    f"[run_pipeline] _write_cancelled_rows raised: {_walk_exc}",
                    file=sys.stderr,
                )
            _err_content = _stderr_log.read_text(encoding="utf-8", errors="replace")
            _cancelled = bool(is_cancelled and is_cancelled())
            _drain_push_worker(cancel=_cancelled)
            raise RuntimeError(
                f"Snakemake pipeline failed (exit {result_returncode}). "
                f"See logs in {_pipeline_log_dir}.\n{_err_content}"
            )
    else:
        proc = _sp.Popen(
            _snake_cmd,
            cwd=_project_root,
            env=_snake_env,
        )
        if process_registry is not None:
            try:
                process_registry(proc)
            except Exception as _reg_exc:
                print(
                    f"[run_pipeline] process_registry raised: {_reg_exc}",
                    file=sys.stderr,
                )
        proc.wait()
        result_returncode = proc.returncode
        if result_returncode != 0:
            _was_cancelled = bool(is_cancelled and is_cancelled())
            if not _was_cancelled:
                try:
                    fail_pipeline(_pipeline_id)
                except Exception as _fp_exc:
                    print(
                        f"[run_pipeline] fail_pipeline raised: {_fp_exc}",
                        file=sys.stderr,
                    )
            try:
                _write_cancelled_rows(_pipeline_id, _project_root)
            except Exception as _walk_exc:
                print(
                    f"[run_pipeline] _write_cancelled_rows raised: {_walk_exc}",
                    file=sys.stderr,
                )
            _cancelled = bool(is_cancelled and is_cancelled())
            _drain_push_worker(cancel=_cancelled)
            raise RuntimeError(
                f"Snakemake pipeline failed (exit {result_returncode}). "
                f"See logs in {_pipeline_log_dir}."
            )

    # -- Pipeline-end cancelled-row walk (success path) --
    # Always-on (D-4): on a fully-successful pipeline this is an O(nodes)
    # no-op. When Snakemake's --keep-going skipped some targets, it fills
    # in cancelled rows for the un-run triples before the archive pass.
    try:
        _write_cancelled_rows(_pipeline_id, _project_root)
    except Exception as _walk_exc:
        print(
            f"[run_pipeline] _write_cancelled_rows raised on success path: "
            f"{_walk_exc}",
            file=sys.stderr,
        )

    # -- Deferred archive pass --
    if archive:
        from .provenance import archive_outputs as _archive_outputs

        def _progress(name: str, status: str) -> None:
            print(f"  {name}: {status}")

        print("Archiving pipeline outputs...")
        _results = _archive_outputs(_project_root, progress_fn=_progress)
        archived = sum(1 for r in _results if r["status"] == "archived")
        if archived:
            print(f"Archived {archived} output(s).")
        else:
            print("No outputs to archive.")

    # ADR-018: drain push worker before returning so the caller sees a
    # quiescent state (all rows reached pushed/failed terminals).
    _drain_push_worker(cancel=bool(is_cancelled and is_cancelled()))


# =============================================================================
# lookup_run (legacy — kept for backward compatibility)
# =============================================================================

def lookup_run(method_name: str, sample: str, nf_process_name: str | None = None) -> int | None:
    """Find the most-recent completed run for a method + sample.

    Legacy command — new pipelines use sidecar run_id.txt and check_cache instead.
    """
    with get_session() as session:
        stmt = (
            select(Run)
            .join(Method, Run.method_id == Method.id)
            .where(Method.name == method_name)
            .where(Run.sample == sample)
            .where(Run.status == "completed")
        )
        if nf_process_name is not None:
            stmt = stmt.where(Run.nf_process_name == nf_process_name)
        stmt = stmt.order_by(Run.finished_at.desc())  # type: ignore[union-attr]
        run = session.exec(stmt).first()
        return run.id if run else None


# =============================================================================
# run_step (ADR 008 — single-command step execution)
# =============================================================================

def _run_method_subprocess(
    cmd: list,
    *,
    cwd: str,
    env: dict,
    stdout_log: Path,
    stderr_log: Path,
):
    """Run the method subprocess, tee'ing stdout/stderr to per-run log files
    AND the parent process's std streams.

    Per-run logs land at ``stdout_log`` / ``stderr_log`` (typically inside
    ``.runs/<run_id>/``).  Output is also forwarded to the parent's
    stdout/stderr so Snakemake's pipeline-level capture and live CLI
    users still see output in real time.

    Returns a CompletedProcess-like object so callers keep using
    ``.returncode`` unchanged.  ``stdout``/``stderr`` on the result are
    always ``None`` — read the per-run log files instead.
    """
    import subprocess as _sp
    import threading

    stdout_log.parent.mkdir(parents=True, exist_ok=True)

    proc = _sp.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
        text=True,
        bufsize=1,
    )

    def _pump(src, log_file, parent_stream):
        try:
            for line in iter(src.readline, ""):
                log_file.write(line)
                log_file.flush()
                try:
                    parent_stream.write(line)
                    parent_stream.flush()
                except Exception:
                    pass  # parent stream may be closed/redirected; logs still captured
        finally:
            try:
                src.close()
            except Exception:
                pass

    with open(stdout_log, "w", encoding="utf-8") as out_f, \
         open(stderr_log, "w", encoding="utf-8") as err_f:
        t_out = threading.Thread(
            target=_pump, args=(proc.stdout, out_f, sys.stdout), daemon=True
        )
        t_err = threading.Thread(
            target=_pump, args=(proc.stderr, err_f, sys.stderr), daemon=True
        )
        t_out.start()
        t_err.start()
        proc.wait()
        t_out.join()
        t_err.join()

    return _sp.CompletedProcess(
        args=cmd, returncode=proc.returncode, stdout=None, stderr=None
    )


@workflow(purpose="Execute a single pipeline step: pre_run, method dispatch, complete_run")
def run_step(
    node_id: str,
    sample: str,
    variant: str = "default",
    method_name: str | None = None,
    module_name: str | None = None,
    script_path: str | None = None,
    params: dict | None = None,
    parent_run_ids: list | None = None,
    pipeline_id: str | None = None,
    pipeline_json: str | None = None,
    git_commit: str | None = None,
    ref_inputs: list[str] | None = None,
    collapsed_samples: list[str] | None = None,
) -> int:
    """Execute a single pipeline step end-to-end.

    Implements the 7-step execution protocol from ADR 008 + ADR 007:
    1. Load step config (from pipeline JSON or inline args)
    2. Call pre_run() for cache check + run registration
    3. On CACHED: restore_output() and write outcome sidecar
    4. On NEW: set PM_* env vars, run method subprocess
    5. Read metrics.json, scan outputs, create RunOutput rows
    6. Call complete_run(), write outcome sidecar
    7. Push artifacts to DVC remote (non-fatal)

    Args:
        node_id: Unique node identity within the pipeline.
        sample: Sample identifier.
        variant: Parameter variant name (default: "default").
        method_name: Method name (inline fallback).
        module_name: Module name (inline fallback).
        script_path: Path to method script (inline fallback).
        params: Parameter dict (inline fallback).
        parent_run_ids: Parent run IDs as slot:id or plain id strings.
        pipeline_id: Pipeline execution ID.
        pipeline_json: Path to pipeline JSON file.
        git_commit: Pre-computed git commit SHA.
        ref_inputs: Static ``label=path`` ref-input flags from
            ``run_reference`` nodes. Pre-resolved by the orchestrator.
        collapsed_samples: For collapsed-fan-in roots (``sample="__all__"``),
            the bundled sample identities. The runtime resolver walks
            ``data/samples/<s>/`` per name and accumulates the per-sample
            data files into the fan-in slot. Order is preserved.

    Returns:
        0 on success, 1 on failure.
    """
    import subprocess as _sp
    import traceback as _tb

    口 = Step(step_num=1, name="Resolve step config",
             purpose="Load node config from pipeline JSON or inline args")
    if pipeline_json is None:
        pipeline_json = os.environ.get("WFC_PIPELINE_JSON")

    if pipeline_id is None:
        pipeline_id = os.environ.get("WFC_PIPELINE_ID", "standalone")

    if pipeline_json and Path(pipeline_json).exists():
        # Load from pipeline JSON
        raw = json.loads(Path(pipeline_json).read_text())
        node_map = {str(n["id"]): n for n in raw["nodes"]}
        # Also try matching by method name for legacy pipelines
        method_map = {n["method"]: n for n in raw["nodes"]}
        node = node_map.get(node_id) or method_map.get(node_id)
        if node is None:
            print(f"ERROR: node '{node_id}' not found in pipeline JSON", file=sys.stderr)
            return 1
        method_name = method_name or node["method"]
        module_name = module_name or node["module"]
        script_path = script_path or node.get("script", f"methods/{method_name}/{method_name}.py")
        if params is None:
            # Look up variant params from param_sets or node params
            ps = raw.get("param_sets", {})
            node_ps = ps.get(node_id, ps.get(method_name, {}))
            params = node_ps.get(variant, node.get("params", {}))

    # Validate required inline args
    if not method_name or not module_name or not script_path:
        print("ERROR: --method, --module, and --script are required "
              "when --pipeline-json is not provided", file=sys.stderr)
        return 1
    params = params or {}

    # Extract custom NID label from pipeline JSON node (if present).
    # Parse pipeline JSON once and reuse for both NID extraction and
    # parent run resolution below.
    nid_label: str | None = None
    pipeline_data: dict | None = None
    if pipeline_json and Path(pipeline_json).exists():
        pipeline_data = json.loads(Path(pipeline_json).read_text())
        nodes_by_id = {str(n["id"]): n for n in pipeline_data["nodes"]}
        nodes_by_method = {n["method"]: n for n in pipeline_data["nodes"]}
        current_node = nodes_by_id.get(node_id) or nodes_by_method.get(node_id) or {}
        nid_label = current_node.get("label") or None

    # Resolve parent run IDs from sentinel sidecars if not provided
    # ADR-018: workspace is gone; sidecars live next to the sentinel files
    # at .runs/sentinels/{pipeline_id}/{node_id}/{sample}/{variant}/run_id.txt.
    if parent_run_ids is None:
        parent_run_ids = []
        ws_dir = runs_dir() / "sentinels"
        if pipeline_data is not None:
            links = pipeline_data.get("links", [])
            node_map_raw = {str(n["id"]): n for n in pipeline_data["nodes"]}
            for link in links:
                tgt = str(link["target"])
                src = str(link["source"])
                # Match target to our node_id
                tgt_node = node_map_raw.get(tgt)
                src_node = node_map_raw.get(src)
                if tgt_node and src_node:
                    tgt_nid = tgt if not tgt.isdigit() else tgt_node["method"]
                    src_nid = src if not src.isdigit() else src_node["method"]
                    if tgt_nid == node_id:
                        slot = link.get("target_slot", "data")
                        # Workspace outputs are scoped by pipeline_id (see
                        # ws_base below), so parent sidecar lookups must
                        # include pipeline_id too. Without this, the lookup
                        # reads from the legacy un-scoped path and either
                        # finds nothing or finds a stale sidecar from a
                        # prior session, leaving WFC_INPUT_PATHS empty.
                        sidecar = (
                            ws_dir / pipeline_id / src_nid / sample / variant
                            / "run_id.txt"
                        )
                        if sidecar.exists():
                            pid = sidecar.read_text().strip()
                            parent_run_ids.append(f"{slot}:{pid}")

    口 = AutoStep(step_num=2, name="Pre-run")
    try:
        flag, run_id = pre_run(
            method_name=method_name,
            module_name=module_name,
            sample=sample,
            params=params,
            parent_run_ids=parent_run_ids if parent_run_ids else None,
            pipeline_id=pipeline_id,
            git_commit=git_commit,
            nid=nid_label,
        )
    except Exception as exc:
        print(f"ERROR: pre_run failed: {exc}", file=sys.stderr)
        return 1

    run_dir = _run_archive_dir(run_id)
    ws_base = runs_dir() / "workspace" / pipeline_id / node_id / sample / variant

    # ADR-010: Resolve workspace output paths via the single shared helper.
    # slot_outputs (filename per slot) and slot_types (type per slot) come
    # from the pipeline JSON emitted by _enrich_pipeline.  Empty slot_outputs
    # triggers the legacy single-output fallback inside resolve_node_outputs.
    slot_outputs: dict[str, str] = {}
    slot_types: dict[str, str] = {}
    node_cfg: dict = {}
    if pipeline_json and Path(pipeline_json).exists():
        raw = json.loads(Path(pipeline_json).read_text())
        node_map_raw = {str(n["id"]): n for n in raw["nodes"]}
        method_map_raw = {n["method"]: n for n in raw["nodes"]}
        node_cfg = node_map_raw.get(node_id) or method_map_raw.get(node_id) or {}
        slot_outputs = node_cfg.get("slot_outputs", {}) or {}
        slot_types = node_cfg.get("slot_types", {}) or {}

    from .node_outputs import resolve_node_outputs
    ws_outputs = resolve_node_outputs(node_cfg, ws_base)  # {slot: Path}

    # Outcome sidecar setup
    outcomes_dir = runs_dir() / "pipelines" / pipeline_id / "outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    outcome = {
        "node_id": node_id, "sample": sample, "variant": variant,
        "run_id": run_id, "status": "unknown", "error": None,
    }

    口 = Step(step_num=3, name="Handle cache hit",
             purpose="Touch sentinel and write outcome sidecar (cache is authoritative — no workspace publish)")
    if flag == "CACHED":
        # ADR-018: outputs already live in the DVC cache (the authoritative
        # store). Downstream nodes use ``resolve_input`` to read directly
        # from the cache; we only need to signal Snakemake via the sentinel
        # and write the audit-row sidecar for lineage.
        try:
            outcome["status"] = "cached"
        except Exception as exc:
            outcome["status"] = "failed"
            outcome["error"] = str(exc)
            _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
            print(f"ERROR: cache-hit handling failed: {exc}", file=sys.stderr)
            return 1
        # ADR-018: touch sentinel so Snakemake sees the cache-hit rule succeed.
        sentinel_sample = "__all__" if sample == "__all__" else sample
        sentinel_path = (
            runs_dir() / "sentinels" / pipeline_id / node_id /
            sentinel_sample / variant / ".complete"
        )
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel_path.touch()
        # Write audit-row sidecar at the canonical sentinel-adjacent location
        # so downstream nodes can resolve lineage to this pipeline's audit row
        # (not the old pipeline's source run).
        sidecar = sentinel_path.parent / "run_id.txt"
        sidecar.write_text(str(run_id))
        _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
        return 0

    口 = Step(step_num=4, name="Execute method subprocess",
             purpose="Set PM_* env vars and run method script in isolated subprocess")
    # Resolve input path(s).  For fan-in nodes with multiple parents we
    # build a slot→paths dict so method.py can dispatch via WFC_INPUT_PATHS.
    slot_paths: dict[str, list[str]] = {}  # slot → [resolved_path, ...]
    if parent_run_ids:
        for pid_entry in parent_run_ids:
            s = str(pid_entry)
            if ":" in s:
                slot, pid_str = s.split(":", 1)
            else:
                slot = "data"
                pid_str = s
            resolved = resolve_input(run_id=int(pid_str))
            if resolved:
                slot_paths.setdefault(slot, []).append(str(resolved))
    else:
        # Root node (e.g. downstream of input_selector): resolve sample
        # data from data/samples/{sample}/ when available.
        #
        # Only fire when the method has at least one incoming link from an
        # ``input_selector`` node. Methods rooted solely at ``run_reference``
        # nodes receive their inputs via ``--ref-input`` (handled below) and
        # the sample YAML has no role — firing the fallback here would
        # double-populate the slot and clobber the ref artifact.
        has_input_selector_upstream = False
        selector_target_slot: str | None = None
        if pipeline_data is not None:
            raw_id = str(current_node.get("id", node_id))
            nodes_by_id = {str(n["id"]): n for n in pipeline_data.get("nodes", [])}
            for link in pipeline_data.get("links", []):
                tgt = str(link.get("target", ""))
                if tgt not in (node_id, raw_id):
                    continue
                src = str(link.get("source", ""))
                src_node = nodes_by_id.get(src, {})
                if src_node.get("type") == "input_selector":
                    has_input_selector_upstream = True
                    ts = link.get("target_slot")
                    if ts and selector_target_slot is None:
                        selector_target_slot = ts
                    break
        # Legacy pipelines without type discriminators treat missing
        # pipeline_data the same as "root sample node" for backcompat.
        if pipeline_data is None:
            has_input_selector_upstream = True

        if has_input_selector_upstream:
            slot = selector_target_slot or "data"
            project_root_for_samples = get_project_root()
            # Collapsed-fan-in root branch: sample is the literal "__all__"
            # and one --collapsed-sample flag was emitted per bundled sample
            # by _generate_rule. Walk each sample's data dir at execution
            # time -- restore_sample has already populated them by now
            # (the .sample_ready sentinels in the rule's input: block
            # gate the rule's execution). The generator no longer
            # inspects the filesystem at Snakefile-generation time.
            # (PEV cycle 2026-05-02-snakemake-gen-collapsed-fanin-fix; D-4.)
            if sample == "__all__" and collapsed_samples:
                missing: list[str] = []
                for s in collapsed_samples:
                    sample_dir = project_root_for_samples / "data" / "samples" / s
                    if not sample_dir.is_dir():
                        missing.append(s)
                        continue
                    sample_files = sorted(
                        f for f in os.listdir(sample_dir) if not f.startswith(".")
                    )
                    if not sample_files:
                        missing.append(s)
                        continue
                    resolved_path = str((sample_dir / sample_files[0]).resolve())
                    slot_paths.setdefault(slot, []).append(resolved_path)
                if missing:
                    print(
                        f"ERROR: collapsed-fan-in root '{node_id}' could not "
                        f"resolve data files for sample(s) {missing}. "
                        f"Each sample's directory under data/samples/ must "
                        f"contain at least one non-dotfile after restore_sample "
                        f"runs. Check the upstream restore_sample rule output.",
                        file=sys.stderr,
                    )
                    return 1
            else:
                sample_dir = project_root_for_samples / "data" / "samples" / sample
                if sample_dir.exists():
                    sample_files = [f for f in os.listdir(sample_dir) if not f.startswith(".")]
                    if sample_files:
                        resolved_path = str((sample_dir / sample_files[0]).resolve())
                        slot_paths.setdefault(slot, []).append(resolved_path)

    # ADR-008 boundary rule: run_reference paths are resolved by the
    # orchestrator and passed via --ref-input.  Merge them into
    # slot_paths so the method receives them as normal
    # inputs without run_step needing to understand system node types.
    if ref_inputs:
        for entry in ref_inputs:
            if "=" not in entry:
                continue
            label, ref_path = entry.split("=", 1)
            slot_paths.setdefault(label, []).append(ref_path)

    # D-2: Enforce --ref-input for root nodes.  If we reach this point with
    # no parent_run_ids AND no ref_inputs resolved, the node is a root node
    # invoked without the required --ref-input flag.
    if not parent_run_ids and not slot_paths:
        print(
            f"ERROR: root node '{node_id}' has no input data.  "
            f"Provide --ref-input <slot>=<path> to supply input for root nodes.",
            file=sys.stderr,
        )
        return 1

    # Write _run_context.json
    context = {
        "run_id": int(run_id), "run_dir": str(run_dir), "sample": sample,
        "slot_paths": slot_paths, "params": params,
        "method_name": method_name, "module_name": module_name,
        "slot_outputs": slot_outputs,
        "slot_types": slot_types,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_run_context.json").write_text(json.dumps(context, indent=2, default=str))

    # Set PM_* env vars
    wfc_env = os.environ.copy()
    wfc_env.update({
        "WFC_RUN_ID": str(run_id),
        "WFC_RUN_DIR": str(run_dir.resolve()),
        "WFC_SAMPLE": sample,
        "WFC_PARAMS": json.dumps(params),
        "WFC_NODE_ID": node_id,
        "WFC_PIPELINE_ID": pipeline_id,
        "WFC_VARIANT": variant,
    })
    # Fan-in: expose all parent outputs grouped by slot so method.py can
    # load multi-input dicts via WFC_INPUT_PATHS.
    if slot_paths:
        wfc_env["WFC_INPUT_PATHS"] = json.dumps(slot_paths)

    # Expose *only* the wfc package to the method subprocess via a narrow
    # shim (see _ensure_wfc_shim() for the rationale).  Overwrite — do NOT
    # append — any inherited PYTHONPATH: the Snakemake parent process or
    # the user's shell may have put the host venv's site-packages there,
    # which is exactly the ABI-mismatched numpy/pandas we are trying to
    # keep out of the pixi env.
    wfc_env["PYTHONPATH"] = str(_ensure_wfc_shim())
    wfc_env["PYTHONUNBUFFERED"] = "1"

    # Resolve python executable (env dispatch)
    python_exe = sys.executable
    project_root = get_project_root()
    env_name = "inherit"  # hoisted: also read by Cycle D container branch below
    config_path = project_root / ".wfc" / "wf-canvas.toml"
    if config_path.exists():
        try:
            from .init import read_config
            cfg = read_config(project_root)
            pixi_root = cfg.get("pixi_root", "")
            conda_root = cfg.get("conda_root", "")
            # Look up env for this node from pipeline JSON
            if pipeline_json and Path(pipeline_json).exists():
                raw_pj = json.loads(Path(pipeline_json).read_text())
                nm = {str(n["id"]): n for n in raw_pj["nodes"]}
                mm = {n["method"]: n for n in raw_pj["nodes"]}
                nc = nm.get(node_id) or mm.get(node_id) or {}
                env_name = nc.get("env", nc.get("env_strategy", "inherit"))
            if env_name != "inherit":
                from .register import resolve_python_for_env
                try:
                    python_exe = str(resolve_python_for_env(
                        env_name, pixi_root=pixi_root, conda_root=conda_root,
                    ))
                except ValueError:
                    pass  # Fall back to sys.executable
        except Exception:
            pass  # Fall back to sys.executable

    # Prepend pixi env directories to PATH so Windows DLL loader finds the
    # correct native libs (e.g. libopenblas, jaxlib).  PYTHONPATH (handled
    # above via _ensure_wfc_shim) controls Python imports; PATH controls
    # OS-level DLL search order.  Without this, the parent env's DLLs leak
    # into the subprocess and cause ABI-mismatch segfaults.  (See ADR-008.)
    if python_exe != sys.executable:
        env_root = Path(python_exe).resolve().parent
        if env_root.name.lower() == "scripts":
            env_root = env_root.parent
        path_dirs = [
            str(env_root),                # python.exe, python3XX.dll
            str(env_root / "Library" / "bin"),  # native lib DLLs
            str(env_root / "Scripts"),     # pip-installed entry points
            str(env_root / "DLLs"),        # Python extension modules
        ]
        wfc_env["PATH"] = os.pathsep.join(path_dirs) + os.pathsep + wfc_env.get("PATH", "")

    # ----- ADR-019 Cycle D: container dispatch branch -----
    # If the resolved env is registered in .wfc/envs.json with a non-empty
    # ``container`` field, OR the method's own env field is the per-method
    # escape hatch (``container:docker://...@sha256:...``), dispatch through
    # an ephemeral local Docker container instead of host-Python.
    #
    # Engine selection: read ``executor`` from the parsed method.yaml
    # contract. ``local`` -> build_docker_command; ``slurm`` -> reject with
    # "out of scope for v1" (ADR-019 amendment 2026-05-17 carved cluster
    # Apptainer out of v1).
    container_image_ref: str | None = None
    # 1) Manifest lookup by env name. No recursive-dispatch guard needed:
    # the in-container entrypoint is ``wfc exec-method`` (not ``wfc run-step``),
    # and ``exec-method`` has no container-dispatch branch — so recursion is
    # impossible by construction.
    try:
        from .envs import get as _envs_get
        record = _envs_get(env_name, project_root)
        if record is not None and getattr(record, "container", ""):
            # Strip docker:// prefix; the docker CLI accepts the bare
            # registry/repo@digest form. (Apptainer wants docker://;
            # build_apptainer_command re-prefixes.)
            ref = record.container
            container_image_ref = ref[len("docker://"):] if ref.startswith("docker://") else ref
    except Exception:
        container_image_ref = None

    # 2) Per-method escape hatch: ``env: container:docker://...@sha256:...``.
    # We re-parse method.yaml here (cheap) to pick up the ``env`` and
    # ``executor`` and ``gpus`` keys without threading the contract dict
    # all the way from step-1.
    method_executor = "local"
    method_gpus = False
    try:
        from .contracts import parse_method_yaml
        method_dir = Path(script_path).resolve().parent
        contract = parse_method_yaml(method_dir)
        if contract is not None:
            method_executor = contract.get("executor") or "local"
            method_gpus = bool(contract.get("gpus", False))
            method_env_field = contract.get("env") or ""
            if (
                container_image_ref is None
                and method_env_field.startswith("container:")
            ):
                escape_payload = method_env_field[len("container:"):]
                # Only treat as direct-ref escape hatch if it includes the
                # digest marker; bare ``container:<name>`` is a manifest ref
                # that the manifest-lookup branch above already handled.
                if "@sha256:" in escape_payload:
                    container_image_ref = (
                        escape_payload[len("docker://"):]
                        if escape_payload.startswith("docker://")
                        else escape_payload
                    )
    except Exception:
        pass

    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"

    if container_image_ref is not None:
        # SLURM carve-out: v1 ships local-only. cluster Apptainer dispatch
        # is the v1.x cycle (lands alongside registry push, which they
        # unlock together).
        if method_executor == "slurm":
            print(
                "ERROR: cluster Apptainer dispatch is out of scope for v1 "
                "(lands in v1.x alongside registry push)",
                file=sys.stderr,
            )
            return 1

        from .container_runner import build_docker_command

        # UID/GID: ``os.getuid()`` doesn't exist on Windows. Docker Desktop
        # on Windows/macOS ignores ``--user`` anyway; on Linux stock Docker
        # this is what keeps bind-mount writes from landing as root:root.
        _uid = getattr(os, "getuid", lambda: 0)()
        _gid = getattr(os, "getgid", lambda: 0)()

        # DVC cache dir: default to <project_root>/.dvc/cache. The helper
        # mounts it at /dvc-cache inside the container so container-side
        # wfc.provenance resolves to the same content-addressed store the
        # host sees.
        dvc_cache_dir = project_root / ".dvc" / "cache"

        # Inner command: ``wfc exec-method`` runs the method script using the
        # run-state already established by THIS outer ``wfc run-step``. The
        # in-container wfc does NOT re-enter the run_step workflow — that
        # would regenerate run_id and write outputs to the wrong run dir,
        # because the outer host run-state is the source of truth (DB rows,
        # cache keys, output collection all happen out here in the host wfc).
        #
        # Script-path translation: host ``<project_root>/methods/<m>/<m>.py``
        # becomes ``/work/methods/<m>/<m>.py`` because ``/work`` is the
        # bind-mount of ``project_root``. If the script lives outside
        # project_root (unusual; an inline-fallback path), pass it through
        # as-is and rely on the caller having bind-mounted it.
        try:
            rel_script = Path(script_path).resolve().relative_to(project_root)
            script_in_container = "/work/" + rel_script.as_posix()
        except ValueError:
            script_in_container = str(script_path)
        inner_argv = [
            "python", "-m", "wfc", "exec-method",
            "--run-id", str(run_id),
            "--node-id", node_id,
            "--script", script_in_container,
        ]

        cmd = build_docker_command(
            image_ref=container_image_ref,
            project_root=project_root,
            dvc_cache_dir=dvc_cache_dir,
            run_step_argv=inner_argv,
            uid=_uid,
            gid=_gid,
            gpus=method_gpus,
        )

        # Forward PM_* env vars via -e flags. Do NOT forward PYTHONPATH
        # into the container (Cycle D constraint: host venv leakage is
        # the entire reason for the container barrier).
        #
        # Path translation: WFC_RUN_DIR is a host absolute path, and
        # WFC_INPUT_PATHS is a JSON dict/list of host absolute paths. The
        # in-container ``wfc`` reads these env vars; the project tree is
        # bind-mounted at ``/work`` and the DVC cache at ``/dvc-cache``,
        # so host paths under those roots must be rewritten before
        # forwarding (otherwise the container hits nonexistent paths).
        proj_posix = Path(project_root).resolve().as_posix()
        dvc_posix = Path(dvc_cache_dir).resolve().as_posix()

        def _translate_host_path(host_path: str) -> str:
            """Rewrite a host absolute path to its in-container equivalent.

            Paths under ``project_root`` map to ``/work/<rel>``; paths
            under ``dvc_cache_dir`` map to ``/dvc-cache/<rel>``; anything
            else is returned unchanged. Output is always POSIX
            (forward-slash) so the in-container Python sees Linux-style
            paths even when the host is Windows.
            """
            try:
                p = Path(host_path).resolve().as_posix()
            except (OSError, ValueError):
                return host_path
            if p == proj_posix:
                return "/work"
            if p.startswith(proj_posix + "/"):
                return "/work/" + p[len(proj_posix) + 1:]
            if p == dvc_posix:
                return "/dvc-cache"
            if p.startswith(dvc_posix + "/"):
                return "/dvc-cache/" + p[len(dvc_posix) + 1:]
            return host_path

        wfc_keys = (
            "WFC_RUN_ID", "WFC_RUN_DIR", "WFC_SAMPLE", "WFC_PARAMS",
            "WFC_NODE_ID", "WFC_PIPELINE_ID", "WFC_VARIANT", "WFC_INPUT_PATHS",
        )
        env_flags: list[str] = []
        for k in wfc_keys:
            if k not in wfc_env:
                continue
            value = wfc_env[k]
            if k == "WFC_RUN_DIR":
                value = _translate_host_path(value)
            elif k == "WFC_INPUT_PATHS":
                # JSON dict/list of paths -- decode, translate each leaf, re-encode.
                try:
                    decoded = json.loads(value)
                except (TypeError, ValueError):
                    decoded = None
                if isinstance(decoded, dict):
                    translated: dict = {}
                    for slot, entry in decoded.items():
                        if isinstance(entry, list):
                            translated[slot] = [
                                _translate_host_path(p) if isinstance(p, str) else p
                                for p in entry
                            ]
                        elif isinstance(entry, str):
                            translated[slot] = _translate_host_path(entry)
                        else:
                            translated[slot] = entry
                    value = json.dumps(translated)
                elif isinstance(decoded, list):
                    value = json.dumps([
                        _translate_host_path(p) if isinstance(p, str) else p
                        for p in decoded
                    ])
                # else: leave value as-is (unparseable -- preserve original).
            env_flags.extend(["-e", f"{k}={value}"])
        # Splice -e flags right after "docker run --rm" for readability.
        # Find the position after the last "-v ... -w /work -v ..." bind
        # block but before --gpus / image. Simplest: insert after "--rm".
        rm_idx = cmd.index("--rm") + 1
        cmd = cmd[:rm_idx] + env_flags + cmd[rm_idx:]
    else:
        # Execute the method script.  stdout/stderr are tee'd to per-run log
        # files (.runs/<run_id>/{stdout,stderr}.log) while also being forwarded
        # to the parent's std streams so Snakemake's pipeline-level capture
        # still sees method output.
        cmd = [python_exe, str(script_path)]
    try:
        result = _run_method_subprocess(
            cmd,
            cwd=str(project_root),
            env=wfc_env,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
    except Exception as exc:
        # Subprocess launch failure
        error_msg = str(exc)
        error_tb = _tb.format_exc()
        try:
            complete_run(run_id=run_id, status="failed",
                         error_message=error_msg, error_traceback=error_tb)
        except Exception:
            pass
        outcome["status"] = "failed"
        outcome["error"] = error_msg
        _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
        print(f"ERROR: method execution failed: {error_msg}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        # ADR-015 Phase A.1: lift the real error from stderr.log so the
        # structured error_message / error_traceback fields carry the actual
        # ValueError (or whatever) instead of "Method exited with code N".
        # The latter is uninformative on compact summary surfaces (per-node
        # error badge in /api/.../status, ADR-016 nodeRunActor.failed context).
        error_msg = f"Method exited with code {result.returncode}"
        error_tb = ""
        if stderr_log.exists():
            try:
                stderr_text = stderr_log.read_text(encoding="utf-8", errors="replace")
                error_tb = "".join(stderr_text.splitlines(keepends=True)[-100:])
                last_line = next(
                    (ln.strip() for ln in reversed(error_tb.splitlines()) if ln.strip()),
                    "",
                )
                # Python tracebacks end with "<ExceptionClass>: <message>".
                head, sep, _ = last_line.partition(": ")
                if sep and head and " " not in head:
                    error_msg = last_line
            except OSError:
                pass
        try:
            complete_run(run_id=run_id, status="failed",
                         error_message=error_msg, error_traceback=error_tb)
        except Exception:
            pass
        outcome["status"] = "failed"
        outcome["error"] = error_msg
        _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
        print(f"ERROR: {error_msg}", file=sys.stderr)
        return 1

    口 = Step(step_num=5, name="Collect outputs",
             purpose="Read metrics.json, scan declared output slots, create RunOutput rows")
    metrics_path = run_dir / "metrics.json"
    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # Default to empty metrics

    # ADR-010: Scan outputs slot-first.  For each declared slot in the
    # pipeline JSON, locate the file/dir under run_dir, verify it exists,
    # record a RunOutput row (content_hash=NULL -- deferred archiving),
    # and collect it for complete_run.  A missing declared slot fails the
    # run with a clear error naming the method and slot.
    # Deferred archiving: hash_path and cache_file are NOT called here.
    # Content hashing happens in the post-pipeline archive pass.

    output_files_in_archive: list[str] = []
    workspace_outputs: list[str] = []
    for slot_name, ws_path in ws_outputs.items():
        filename = slot_outputs.get(slot_name, ws_path.name)
        archive_entry = run_dir / filename
        if not archive_entry.exists():
            # Method honored pre_run but failed to produce a declared slot.
            error_msg = (
                f"Method '{method_name}' did not produce declared slot "
                f"'{slot_name}' (expected at {archive_entry})"
            )
            try:
                complete_run(run_id=run_id, status="failed",
                             error_message=error_msg, error_traceback=error_msg)
            except Exception:
                pass
            outcome["status"] = "failed"
            outcome["error"] = error_msg
            _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
            print(f"ERROR: {error_msg}", file=sys.stderr)
            return 1

        output_files_in_archive.append(str(archive_entry))
        workspace_outputs.append(str(ws_path))

        # Determine artifact_type: slot-declared files are module outputs.
        artifact_type = "module_file" if slot_outputs else "method_file"

        # Create RunOutput row keyed by the slot filename (content_hash=NULL)
        try:
            with get_session() as session:
                existing = session.exec(
                    select(RunOutput).where(
                        RunOutput.run_id == run_id,
                        RunOutput.output_name == archive_entry.name,
                    )
                ).first()
                if archive_entry.is_file():
                    stat = archive_entry.stat()
                    file_size = stat.st_size
                    file_mtime = stat.st_mtime
                else:
                    file_size = None
                    file_mtime = None
                if existing:
                    existing.artifact_path = str(archive_entry)
                    existing.artifact_type = artifact_type
                    existing.file_size = file_size
                    existing.file_mtime = file_mtime
                else:
                    session.add(RunOutput(
                        run_id=run_id, output_name=archive_entry.name,
                        artifact_path=str(archive_entry), artifact_type=artifact_type,
                        file_size=file_size, file_mtime=file_mtime,
                    ))
                session.commit()
        except Exception:
            pass  # Best-effort DB write

    口 = AutoStep(step_num=6, name="Complete run")
    complete_run(
        run_id=run_id,
        status="completed",
        output_files=output_files_in_archive,
        metrics=metrics,
    )

    # ADR-018: Touch the Snakemake-visible sentinel.  The Snakefile declares
    # one zero-byte sentinel per (pipeline, node, sample, variant); creating
    # it here signals to Snakemake that the rule succeeded.  Real outputs
    # remain in the run-staging dir / DVC cache.
    sentinel_sample = "__all__" if sample == "__all__" else sample
    sentinel_path = (
        runs_dir() / "sentinels" / pipeline_id / node_id /
        sentinel_sample / variant / ".complete"
    )
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel_path.touch()
    # Write run_id.txt sidecar next to the sentinel so downstream nodes
    # can resolve parent run IDs by path (mirrors the lookup at run_step
    # step 1 that reads sentinels/{pipeline}/{node}/{sample}/{variant}/run_id.txt).
    (sentinel_path.parent / "run_id.txt").write_text(str(run_id))

    口 = Step(step_num=7, name="Enqueue outputs for async DVC push",
             purpose="Mark RunOutput rows as pending so the push worker picks them up (ADR-018)")
    # ADR-018: outputs live in the run-staging dir (and the deferred archive
    # pass will move them into the DVC cache).  Mark rows for the push
    # worker; if no remote is configured, leave them as deferred.
    try:
        from .models import PushStatus
        # Task 6 introduces wfc.remote.has_remote_configured.  Until that lands,
        # fall back to the existing _remote_path probe (returns None when
        # no [dvc] block is present in wf-canvas.toml).
        remote_configured = False
        try:
            from .remote import has_remote_configured
            remote_configured = has_remote_configured(Path(project_root))
        except ImportError:
            try:
                from .provenance import _remote_path
                remote_configured = _remote_path(Path(project_root)) is not None
            except Exception:
                remote_configured = False
        target_state = PushStatus.pending if remote_configured else PushStatus.deferred
        with get_session() as session:
            for archive_entry_str in output_files_in_archive:
                name = Path(archive_entry_str).name
                row = session.exec(
                    select(RunOutput).where(
                        RunOutput.run_id == run_id,
                        RunOutput.output_name == name,
                    )
                ).first()
                if row is not None:
                    row.push_status = target_state
            session.commit()
    except Exception as exc:
        print(f"WARNING: push enqueue failed: {exc}", file=sys.stderr)

    outcome["status"] = "completed"
    _write_outcome(outcomes_dir, node_id, sample, variant, outcome)
    return 0


def exec_method(run_id: int, node_id: str, script_path: str) -> int:
    """Execute one method script in the env established by the outer wfc run-step.

    In-container entrypoint of the ADR-019 container dispatch path. The outer
    host ``wfc run-step`` owns run-state (DB rows, cache keys, output
    collection); this subcommand just executes the method script with the PM_*
    env vars already set in ``os.environ`` by ``docker run -e ...``.

    Critically, ``exec-method`` does NOT call ``register_run``, ``pre_run``,
    ``post_run``, ``complete_run``, hit the DB, compute cache keys, collect
    outputs, or enter any container-dispatch branch. Those are all owned by
    the outer host ``wfc run-step`` invocation. ``exec-method`` is the leanest
    possible "exec my method in this env" verb — and because it has no
    container-dispatch branch, recursion is impossible by construction (no
    ``WFC_IN_CONTAINER`` guard needed).

    Args:
        run_id: Run ID from the outer ``wfc run-step``. Not used internally;
            captured for error-message clarity and to leave a record of which
            outer run dispatched into this container.
        node_id: Pipeline node ID. Captured for error-message clarity.
        script_path: Absolute path inside the container to the method script
            (typically ``/work/methods/<m>/<m>.py``).

    Returns:
        Exit code from the method-script subprocess. ``0`` on success;
        non-zero on validation failure or method-script failure.
    """
    import subprocess as _sp

    # Validate required env: WFC_RUN_DIR must be set and point at an existing
    # directory inside the container (typically /work/.runs/<run_id>/...).
    wfc_run_dir = os.environ.get("WFC_RUN_DIR")
    if not wfc_run_dir:
        print(
            "ERROR: wfc exec-method requires WFC_RUN_DIR in environment "
            "(set by the outer dispatch via `docker run -e WFC_RUN_DIR=...`)",
            file=sys.stderr,
        )
        return 1
    run_dir = Path(wfc_run_dir)
    if not run_dir.is_dir():
        print(
            f"ERROR: WFC_RUN_DIR={wfc_run_dir} does not exist or is not a directory",
            file=sys.stderr,
        )
        return 1

    script = Path(script_path)
    if not script.is_file():
        print(
            f"ERROR: method script not found: {script_path}",
            file=sys.stderr,
        )
        return 1

    # Exec the script; inherit env (PM_* already set by outer dispatch via
    # -e flags). Pipe stdout/stderr through directly so the user sees method
    # output in the container log stream.
    result = _sp.run(
        [sys.executable, str(script)],
        env=os.environ.copy(),
    )
    return result.returncode


def _write_outcome(outcomes_dir: Path, node_id: str, sample: str, variant: str, outcome: dict) -> None:
    """Write a JSON outcome sidecar file."""
    filename = f"{node_id}__{sample}__{variant}.json"
    (outcomes_dir / filename).write_text(json.dumps(outcome, indent=2, default=str))


# =============================================================================
# pipeline_summary (ADR 008 — outcome aggregation)
# =============================================================================

def pipeline_summary(pipeline_id: str) -> int:
    """Aggregate outcome sidecar JSONs into a pipeline summary.

    Reads all JSON files from ``.runs/pipelines/{pipeline_id}/outcomes/``
    and prints a summary table.

    Args:
        pipeline_id: Pipeline execution ID.

    Returns:
        0 on success.
    """
    outcomes_dir = runs_dir() / "pipelines" / pipeline_id / "outcomes"
    outcomes = []
    if outcomes_dir.exists():
        for f in sorted(outcomes_dir.glob("*.json")):
            try:
                outcomes.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                pass

    completed = [o for o in outcomes if o.get("status") == "completed"]
    cached = [o for o in outcomes if o.get("status") == "cached"]
    failed = [o for o in outcomes if o.get("status") == "failed"]
    total = len(outcomes)

    print("=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"Pipeline ID: {pipeline_id}")
    print(f"Total runs: {total}  |  Passed: {len(completed)}  |  Failed: {len(failed)}  |  Cached: {len(cached)}")
    print()

    if failed:
        print("FAILED RUNS:")
        for o in failed:
            print(f"  - {o.get('node_id')} sample={o.get('sample')} variant={o.get('variant')}")
            if o.get("error"):
                print(f"    Error: {o['error']}")
        print()

    print("=" * 60)
    return 0


# =============================================================================
# cache management (ADR-011)
# =============================================================================

@task(purpose="Hash and cache un-archived outputs from completed runs")
def cache_archive(*, run_id: int | None = None) -> int:
    """Hash and cache un-archived outputs from completed runs.

    Queries RunOutput rows with NULL content_hash, hashes each file,
    copies into DVC cache, and updates the DB.  Prints per-file progress.

    Args:
        run_id: Optional filter to archive only outputs from a specific run.

    Returns:
        0 on success.
    """
    from .provenance import archive_outputs

    project_dir = get_project_root()

    def _progress(name: str, status: str) -> None:
        print(f"  {name}: {status}")

    print("Archiving un-archived outputs...")
    results = archive_outputs(project_dir, run_id=run_id, progress_fn=_progress)

    if not results:
        print("Nothing to archive.")
    else:
        archived = sum(1 for r in results if r["status"] == "archived")
        missing = sum(1 for r in results if r["status"] == "missing")
        errors = sum(1 for r in results if r["status"].startswith("error:"))
        print(f"Done: {archived} archived, {missing} missing, {errors} errors.")

    return 0


@task(purpose="Remove old run archives and optionally prune DVC local cache entries")
def cache_prune(
    *,
    prune_all: bool = False,
    include_local: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Remove old run archives and optionally DVC local cache entries.

    Args:
        prune_all: Remove all archives regardless of reference status.
        include_local: Also prune .dvc/cache/ entries for unreferenced hashes.
        dry_run: Print what would be deleted without deleting.
        force: Skip confirmation prompt.

    Returns:
        0 on success, 1 on user abort.
    """
    from .provenance import prune_run_archives, prune_dvc_cache, check_remote_reachable

    project_dir = get_project_root()

    # ADR-011 safety check: verify DVC remote is reachable before pruning
    if not dry_run:
        reachable, reason = check_remote_reachable(project_dir)
        if not reachable:
            if include_local and not force:
                print(
                    f"ERROR: DVC remote is unreachable ({reason}). "
                    f"With --include-local, pruning will make outputs unrecoverable. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
                return 1
            elif not force:
                print(
                    f"WARNING: DVC remote is unreachable ({reason}). "
                    f"Pruned archives may not be recoverable from remote. "
                    f"Use --force to override.",
                    file=sys.stderr,
                )
                return 1

    # Prune guard: refuse to prune runs with un-archived outputs
    from .models import RunOutput as _RunOutput
    _exclude_run_ids: set[int] = set()
    with get_session() as _guard_session:
        unarchived = _guard_session.exec(
            select(_RunOutput).where(_RunOutput.content_hash.is_(None))  # type: ignore[union-attr]
        ).all()
        if unarchived:
            _exclude_run_ids = {ro.run_id for ro in unarchived}
            print(
                f"WARNING: {len(unarchived)} output(s) from run(s) "
                f"{sorted(_exclude_run_ids)} have not been archived "
                f"(content_hash is NULL). Skipping those runs to prevent "
                f"data loss. Run 'wfc cache archive' first.",
                file=sys.stderr,
            )

    # Compute what would be pruned (always dry_run first for summary)
    archive_paths = prune_run_archives(
        project_dir, all_archives=prune_all, dry_run=True,
        exclude_run_ids=_exclude_run_ids or None,
    )
    cache_paths = (
        prune_dvc_cache(project_dir, all_entries=prune_all, dry_run=True) if include_local else []
    )

    if not archive_paths and not cache_paths:
        print("Nothing to prune.")
        return 0

    # Summary
    print(f"Run archives to remove: {len(archive_paths)}")
    for p in archive_paths:
        print(f"  {p}")
    if include_local:
        print(f"DVC cache entries to remove: {len(cache_paths)}")
        for p in cache_paths:
            print(f"  {p}")

    if dry_run:
        print("(dry run -- no files deleted)")
        return 0

    # Confirmation prompt
    if not force:
        try:
            answer = input("Proceed? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    # Actually prune
    prune_run_archives(
        project_dir, all_archives=prune_all, dry_run=False,
        exclude_run_ids=_exclude_run_ids or None,
    )
    if include_local:
        prune_dvc_cache(project_dir, all_entries=prune_all, dry_run=False)

    total = len(archive_paths) + len(cache_paths)
    print(f"Pruned {total} item(s).")
    return 0


# =============================================================================
# Env-manifest CLI helpers (ADR-019)
# =============================================================================

def _resolve_project_dir_for_envs() -> Path | None:
    """Locate the project root for env-manifest CLI commands.

    Walks up from the cwd looking for a ``.wfc/`` directory. Returns
    ``None`` if none is found — callers print an error and exit non-zero.
    """
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".wfc").is_dir():
            return candidate
    return None


def _cli_list_envs() -> int:
    """``wfc list-envs`` — print every env in ``.wfc/envs.json``."""
    from . import envs as envs_mod

    project_dir = _resolve_project_dir_for_envs()
    if project_dir is None:
        print(
            "ERROR: No wfc project found (no .wfc/ directory). "
            "Run `wfc init` first.",
            file=sys.stderr,
        )
        return 1

    try:
        records = envs_mod.list_envs(project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("No container envs registered. "
              "Use `wfc register-env` to add one (cycle B).")
        return 0

    # records is list[tuple[str, EnvRecord]] (name is the manifest dict KEY,
    # not a record field — ADR-019 §registration-model-and-manifest).
    # Fixed-width table: name | backend | container | built_at
    headers = ("NAME", "BACKEND", "CONTAINER", "BUILT AT")
    rows = [
        (name, r.backend, r.container, r.built_at or "")
        for name, r in records
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))
    return 0


def _cli_show_env(name: str) -> int:
    """``wfc show-env <name>`` — print the full record as key/value lines."""
    from . import envs as envs_mod

    project_dir = _resolve_project_dir_for_envs()
    if project_dir is None:
        print(
            "ERROR: No wfc project found (no .wfc/ directory). "
            "Run `wfc init` first.",
            file=sys.stderr,
        )
        return 1

    try:
        record = envs_mod.get(name, project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if record is None:
        print(f"ERROR: env {name!r} not found in .wfc/envs.json", file=sys.stderr)
        return 1

    # The name is the manifest dict KEY, not a record field; print it
    # separately so users still see it on `wfc show-env <name>`.
    data = record.to_dict()
    keys = (
        "name", "backend", "source", "container",
        "env_fingerprint", "source_fingerprint",
        "built_from_lock", "built_at",
    )
    width = max(len(k) for k in keys)
    for key in keys:
        if key == "name":
            value = name
        else:
            value = data.get(key)
        print(f"{key:<{width}} : {value if value is not None else ''}")
    return 0


def _methods_referencing_env(env_name: str) -> list[str]:
    """Return ``module.name/method.name`` strings for every Method.env == container:<env_name>."""
    from .models import Method, Module

    references: list[str] = []
    target = f"container:{env_name}"
    with get_session() as session:
        rows = session.exec(
            select(Method, Module).join(Module, Method.module_id == Module.id)
            .where(Method.env == target)
        ).all()
        for method, module in rows:
            references.append(f"{module.name}/{method.name}")
    return references


def _typed_spec_backend(spec: str) -> Optional[str]:
    """Infer backend from a typed env spec, or ``None`` if not typed.

    Recognized prefixes:

    * ``conda:<env>``     -> ``"conda"``
    * ``pixi:<name>``     -> ``"pixi"``
    * ``pixi:<proj>:<env>`` -> ``"pixi"``

    Bare names (``"image-io"``, ``"my_env"``, ``"conda"``, ``"pixi"``,
    ``"byo"``, ``"inherit"``) all have NO colon and return ``None`` —
    those are positional env-name slots or backend names, not typed
    specs.
    """
    if ":" not in spec:
        return None
    head = spec.split(":", 1)[0]
    if head == "conda":
        return "conda"
    if head == "pixi":
        return "pixi"
    return None


def _cli_register_env(args) -> int:
    """``wfc register-env <name> [<spec>] [--backend X] [--from PATH]`` —
    build a container image for an env and register it in ``.wfc/envs.json``.

    Three input modes are accepted:

    1. **Positional typed-spec** (``wfc register-env my conda:cell_pose`` /
       ``wfc register-env my pixi:wcia:hello``). The spec re-uses the
       vocabulary already parsed by ``method.yaml``'s ``env:`` field.
       The CLI resolves the live env via :func:`resolve_python_for_env`,
       captures the package list (conda explicit-list / pixi.lock semantic
       slice + pip freeze), stages it into the build context, and stores
       a content-addressed md5 of the captured blob as
       :attr:`EnvRecord.source_fingerprint` so a reviewer can inspect
       exactly what went into the image.

    2. **File mode** (``--from <path> --backend X``). Copies the file
       into the build context under the generator's expected filename
       (``explicit-list.txt`` for conda, ``pixi.lock`` for pixi). For
       pixi, an adjacent ``pixi.toml`` next to the lock file is also
       copied when present. ``--backend`` is required in this mode (no
       extension sniffing — keeps the contract simple).
       :attr:`EnvRecord.source_fingerprint` stays ``None`` in file mode
       because there is no live env to introspect.

    3. **Legacy mode** (``--backend X`` alone). Uses the pre-Cycle E
       behavior of expecting source files at the project root
       (``<project>/pixi.lock``, ``<project>/explicit-list.txt``). Kept
       for backward compatibility with existing scripts.

    Capturing from a live env records the env's current state, including
    any ad-hoc ``pip install`` mutations on top of the conda/pixi env.
    Inspect the captured package list (md5 = ``source_fingerprint``) via
    ``GET /api/registry/envs/blob/<md5>`` before relying on the image
    for downstream runs.
    """
    project_dir = _resolve_project_dir_for_envs()
    if project_dir is None:
        print(
            "ERROR: No wfc project found (no .wfc/ directory). "
            "Run `wfc init` first.",
            file=sys.stderr,
        )
        return 1

    # ---- Mutex enforcement: typed-spec ⨯ --backend ⨯ --from ----
    positional_spec: Optional[str] = getattr(args, "spec", None)
    from_path: Optional[str] = getattr(args, "from_path", None)
    inferred_backend: Optional[str] = (
        _typed_spec_backend(positional_spec) if positional_spec else None
    )

    if inferred_backend is not None and args.backend is not None:
        print(
            f"ERROR: positional typed-spec '{positional_spec}' implies "
            f"backend '{inferred_backend}'; do not also pass --backend.",
            file=sys.stderr,
        )
        return 1
    if inferred_backend is not None and from_path is not None:
        print(
            f"ERROR: positional typed-spec '{positional_spec}' captures "
            f"from a live env; --from is for file-mode only.",
            file=sys.stderr,
        )
        return 1
    if from_path is not None and args.backend is None:
        print(
            "ERROR: --from <path> requires --backend pixi|conda.",
            file=sys.stderr,
        )
        return 1
    if (
        positional_spec is not None
        and inferred_backend is None
        and args.backend is None
    ):
        print(
            f"ERROR: positional '{positional_spec}' is not a typed env "
            f"spec (use conda:<env>, pixi:<proj>:<env>, or pixi:<name>) "
            f"and no --backend was provided.",
            file=sys.stderr,
        )
        return 1
    if (
        positional_spec is None
        and from_path is None
        and args.backend is None
    ):
        print(
            "ERROR: wfc register-env requires either a positional typed "
            "spec (conda:<env> / pixi:<...>), --from <path> --backend, "
            "or --backend alone.",
            file=sys.stderr,
        )
        return 1

    backend = inferred_backend or args.backend

    # ---- --dry-run early-exit (preserves Cycle B behavior) ----
    # Dry-run keeps legacy semantics: render the Dockerfile only. Live-spec
    # and --from staging is implemented in the full-build path.
    if args.dry_run:
        if inferred_backend is not None or from_path is not None:
            print(
                "ERROR: --dry-run does not yet support positional typed "
                "specs or --from <path>. Re-run without --dry-run.",
                file=sys.stderr,
            )
            return 1
        return _cli_register_env_dry_run(args, project_dir)

    # ---- Full build path ----
    from . import envs as envs_mod

    source: dict = {}
    live_spec: Optional[str] = None

    if inferred_backend is not None:
        # Mode 1: positional typed-spec → live-env capture.
        try:
            live_spec = positional_spec
            source = _stage_live_env_source(
                live_spec=positional_spec,
                project_dir=project_dir,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    elif from_path is not None:
        # Mode 2: --from <path>.
        try:
            source = _stage_from_path(
                backend=backend,
                from_path=Path(from_path).resolve(),
            )
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        # Mode 3: legacy. Preserve the pre-existing behavior of expecting
        # files at the project root. The dead --pixi-env / --conda-env
        # flags are intentionally NOT propagated to source: they were
        # never read by wfc.envs.register and the live-spec path
        # supersedes them.
        if backend == "byo":
            if not args.image:
                print(
                    "ERROR: --backend byo requires --image docker://...",
                    file=sys.stderr,
                )
                return 1
            source["image"] = args.image
        # For pixi/conda/inherit, source stays empty in legacy mode.
        # wfc.envs.register will write the Dockerfile but not stage any
        # source-content files; the docker build will fail if the
        # project-root files referenced by the Dockerfile are missing.

    try:
        record = envs_mod.register(
            name=args.name,
            backend=backend,
            source=source,
            base_image=args.base_image,
            force=args.force,
            project_dir=project_dir,
            live_spec=live_spec,
        )
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(record.container)
    return 0


def _stage_live_env_source(live_spec: str, project_dir: Path) -> dict:
    """Capture introspection blobs from the live env named by *live_spec*.

    Returns a *source* payload dict shaped for :func:`wfc.envs.register`
    so the resolved env's pixi.lock / pixi.toml / conda explicit-list /
    pip-freeze land in the build context.

    Raises:
        ValueError: If the env spec is unknown, the env cannot be
            resolved, or the captured-content read fails.
        FileNotFoundError: If a required source file (pixi.lock,
            pixi.toml) is missing from the resolved env's project dir.
    """
    from .env_introspect import (
        conda_list_explicit,
        pip_freeze_best_effort,
    )
    from .init import read_config
    from .register import resolve_python_for_env

    config = read_config(project_dir)
    pixi_root = config.get("pixi_root") or None
    conda_root = config.get("conda_root") or None

    env_python = resolve_python_for_env(
        live_spec,
        pixi_root=pixi_root,
        conda_root=conda_root,
        project_dir=project_dir,
    )

    source: dict = {}
    head = live_spec.split(":", 1)[0]

    if head == "conda":
        # conda prefix = parent of bin/python (posix) / parent of python.exe.
        env_prefix = (
            env_python.parent.parent
            if env_python.name == "python"
            else env_python.parent
        )
        source["explicit_list_content"] = conda_list_explicit(env_prefix)
    elif head == "pixi":
        # env_python is at <pixi-project>/envs/<env>/bin/python (posix)
        # or <pixi-project>/envs/<env>/python.exe (windows). Walk up to
        # the pixi-project directory and read pixi.lock + pixi.toml.
        env_dir = (
            env_python.parent.parent
            if env_python.name == "python"
            else env_python.parent
        )
        pixi_project_dir = env_dir.parent.parent
        lock = pixi_project_dir / "pixi.lock"
        toml = pixi_project_dir / "pixi.toml"
        if not lock.exists():
            raise FileNotFoundError(
                f"pixi.lock not found next to env at {pixi_project_dir} — "
                f"run `pixi install` to regenerate it."
            )
        source["pixi_lock_content"] = lock.read_text(encoding="utf-8")
        if toml.exists():
            source["pixi_toml_content"] = toml.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"Live-env capture not supported for spec {live_spec!r}."
        )

    source["pip_freeze_content"] = pip_freeze_best_effort(env_python)
    return source


def _stage_from_path(backend: str, from_path: Path) -> dict:
    """Read a user-supplied source file and shape it for ``wfc.envs.register``.

    For pixi, an adjacent ``pixi.toml`` next to the lock is also copied
    when present.

    Raises:
        FileNotFoundError: If *from_path* does not exist.
        ValueError: If *backend* is not pixi or conda.
    """
    if not from_path.exists():
        raise FileNotFoundError(f"--from path does not exist: {from_path}")

    source: dict = {}
    if backend == "conda":
        source["explicit_list_content"] = from_path.read_text(encoding="utf-8")
    elif backend == "pixi":
        source["pixi_lock_content"] = from_path.read_text(encoding="utf-8")
        sibling_toml = from_path.parent / "pixi.toml"
        if sibling_toml.exists():
            source["pixi_toml_content"] = sibling_toml.read_text(encoding="utf-8")
    else:
        raise ValueError(
            f"--from is only valid for --backend pixi or conda, not {backend!r}."
        )
    # pip freeze is empty in file mode — no live env to introspect.
    source["pip_freeze_content"] = ""
    return source


def _cli_register_env_dry_run(args, project_dir: Path) -> int:
    """``wfc register-env <name> --backend X --dry-run`` — Cycle B path.

    Render the Dockerfile and exit before any docker subprocess fires.
    Manifest is NOT mutated.
    """
    from . import dockerfiles as df_pkg

    backend = args.backend
    gen_kwargs: dict = {"env_name": args.name}
    if args.base_image is not None:
        gen_kwargs["base_image"] = args.base_image

    if backend == "pixi":
        gen_kwargs["pixi_lock_path"] = project_dir / "pixi.lock"
        gen_kwargs["pip_freeze_content"] = ""
    elif backend == "conda":
        gen_kwargs["explicit_list_path"] = project_dir / "explicit-list.txt"
        gen_kwargs["pip_freeze_content"] = ""
    elif backend == "inherit":
        gen_kwargs["pip_freeze_content"] = ""
    elif backend == "byo":
        gen_kwargs["image"] = args.image

    try:
        dockerfile = df_pkg.generate_for_backend(backend, **gen_kwargs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if dockerfile is None:
        # BYO: no Dockerfile to write. Notice + exit 0 under --dry-run.
        print(
            f"No Dockerfile for BYO env {args.name!r} — the upstream "
            f"image is used as-is; digest resolution runs without "
            f"--dry-run."
        )
        return 0

    build_dir = project_dir / ".wfc" / "build" / args.name
    build_dir.mkdir(parents=True, exist_ok=True)
    dockerfile_path = build_dir / "Dockerfile"
    dockerfile_path.write_text(dockerfile, encoding="utf-8")
    print(str(dockerfile_path.resolve()))
    return 0


def _cli_delete_env(name: str, force: bool = False) -> int:
    """``wfc delete-env <name>`` — remove an env, warn if methods reference it."""
    from . import envs as envs_mod

    project_dir = _resolve_project_dir_for_envs()
    if project_dir is None:
        print(
            "ERROR: No wfc project found (no .wfc/ directory). "
            "Run `wfc init` first.",
            file=sys.stderr,
        )
        return 1

    try:
        record = envs_mod.get(name, project_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if record is None:
        print(f"ERROR: env {name!r} not found in .wfc/envs.json", file=sys.stderr)
        return 1

    # Warn-on-reference: query Method.env rows. Methods are NOT auto-deleted
    # (ADR-019 #7) — the user is responsible for retargeting them.
    try:
        references = _methods_referencing_env(name)
    except Exception:
        # Tolerate a missing/uninitialized DB: deletion still works on the
        # manifest, the warning is best-effort.
        references = []

    if references:
        print(f"WARNING: {len(references)} method(s) reference env {name!r}:")
        for ref in references:
            print(f"  - {ref}")
        print("  Method rows will NOT be auto-deleted. Re-register them "
              "with a different env or they will fail at run time.")

    if not force:
        try:
            answer = input(f"Delete env {name!r}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    envs_mod.delete(name, project_dir)
    print(f"Deleted env {name!r} from .wfc/envs.json. "
          f"Registry tag (if any) was NOT removed.")
    return 0


# =============================================================================
# argparse entry-point
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow-canvas", description="Workflow Canvas CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- register_run --
    reg = sub.add_parser("register_run", help="Register a new run (status=running)")
    reg.add_argument("--method", required=True)
    reg.add_argument("--module", required=True, help="Module that owns this method")
    reg.add_argument("--sample", required=True)
    reg.add_argument("--params", default=None, help="JSON string of parameters")
    reg.add_argument("--parent-run-id", nargs="*", default=None,
                       help="Parent run ID(s) as 'slot:id' or plain 'id'. Repeat for fan-in.")
    reg.add_argument("--nf-process-name", default=None)
    reg.add_argument("--pipeline-id", default=None, help="Pipeline execution ID (UUID)")

    # -- pre_run --
    pr = sub.add_parser("pre_run",
                         help="Versioned pre-run hook: git check + cache lookup + run registration")
    pr.add_argument("--method", required=True)
    pr.add_argument("--module", required=True)
    pr.add_argument("--sample", required=True)
    pr.add_argument("--params", default=None, help="JSON string of parameters")
    pr.add_argument("--parent-run-id", nargs="*", default=None,
                    help="Parent run ID(s) as 'slot:id' or plain 'id'.")
    pr.add_argument("--pipeline-id", default=None)
    pr.add_argument("--nf-process-name", default=None)
    pr.add_argument("--git-commit", default=None,
                    help="Pre-computed commit SHA (skips git check; for testing).")

    # -- complete_run --
    comp = sub.add_parser("complete_run", help="Mark a run as completed")
    comp.add_argument("--run-id", type=int, required=True)
    comp.add_argument("--status", default="completed")
    comp.add_argument("--output", nargs="*", default=[], help="Output file paths (in archive)")
    comp.add_argument("--metrics", default=None, help="JSON string of metrics")
    comp.add_argument("--error", default=None, help="Error message for failed runs (ADR 004)")
    comp.add_argument("--traceback", default=None, dest="traceback_str",
                       help="Error traceback for failed runs (ADR 004)")

    # -- check_cache --
    cache = sub.add_parser("check_cache", help="Check for cached identical run")
    cache.add_argument("--method", required=True)
    cache.add_argument("--sample", required=True)
    cache.add_argument("--params", default=None, help="JSON string of parameters")
    cache.add_argument("--parent-run-id", nargs="*", default=None,
                        help="Parent run ID(s) as 'slot:id' or plain 'id'. Repeat for fan-in.")


    # -- restore-sample (ADR-009) --
    rss = sub.add_parser("restore-sample", help="Restore a sample from DVC cache to data/samples/")
    rss.add_argument("--name", required=True, help="Sample identifier")
    rss.add_argument("--hash", default=None, dest="content_hash",
                     help="Expected content hash (optional; looked up from DB if omitted)")

    # ADR-018: cleanup_workspace CLI subcommand removed. The workspace is
    # gone (sentinels-only) so the dedicated CLI entry point has no
    # purpose. The cleanup_workspace() function is retained for any
    # remaining importers.

    # -- finalize_pipeline --
    finalize = sub.add_parser("finalize_pipeline", help="Log successful pipeline completion")
    finalize.add_argument("--pipeline-id", required=True)

    # -- fail_pipeline --
    fail = sub.add_parser("fail_pipeline", help="Mark in-flight runs as failed")
    fail.add_argument("--pipeline-id", required=True)

    # -- resolve_input --
    resolve = sub.add_parser("resolve_input", help="Get archived output path for a run")
    resolve.add_argument("--run-id", type=int, required=True)

    # -- lookup_run (legacy) --
    look = sub.add_parser("lookup_run", help="(Legacy) Find most-recent completed run")
    look.add_argument("--method", required=True)
    look.add_argument("--sample", required=True)
    look.add_argument("--nf-process-name", default=None)

    # -- init --
    init_p = sub.add_parser("init", help="Scaffold a new wfc project directory")
    init_p.add_argument("--dir", default=".", help="Target directory (default: current dir)")
    init_p.add_argument("--git", action="store_true",
                        help="Run `git init` in the project directory if no repo is found")

    # -- seed --
    sub.add_parser("seed", help="Seed the database with demo methods")

    # -- register-sample --
    rs = sub.add_parser("register-sample", help="Register a data sample (copy into data/samples/)")
    rs.add_argument("--name", required=True, help="Sample identifier (e.g. CFPAC_ERKi)")
    rs.add_argument("--source", required=True, help="Path to the source data file")

    # -- register-module --
    rm = sub.add_parser("register-module", help="Create or update a module in the database")
    rm.add_argument("--name", required=True, help="Module name (e.g. data_preprocessing)")
    rm.add_argument("--description", default=None, help="Human-readable description")
    rm.add_argument("--contracts", default=None,
                    help='Contracts: path to a JSON file, or inline JSON string. '
                         'Optional if --module-dir has module.yaml. '
                         'Example: [{"type":"output","name":"x","value_type":".parquet"}]')
    rm.add_argument("--module-dir", default=None,
                    help="Path to module directory containing module.yaml (contracts loaded from file)")

    # -- register-method --
    rme = sub.add_parser("register-method", help="AST-scan method script and register method + params")
    rme.add_argument("method_dir", help="Path to the method directory containing the method script")
    rme.add_argument("--module", required=True, help="Module name this method belongs to")
    rme.add_argument("--name", default=None, help="Method name (defaults to directory name)")
    rme.add_argument("--script", default=None,
                     help="Script filename to scan (default: {method_name}.py)")

    # -- run-pipeline --
    rp = sub.add_parser("run-pipeline", help="Generate Snakefile and run the pipeline")
    rp.add_argument("--pipeline", required=True, help="Path to the pipeline JSON file")
    rp.add_argument("--project-root", default=None,
                    help="wfc project directory (git repo with method commits). Defaults to cwd.")
    rp.add_argument("--wfc-root", default=None,
                    help="Directory added to PYTHONPATH in workers for `import wfc`. "
                         "Defaults to wfc's installed location.")
    rp.add_argument("--cores", type=int, default=4, help="Snakemake cores (default: 4)")
    rp.add_argument("--snakefile", default=None, help="Where to write the Snakefile (default: <project-root>/Snakefile)")
    rp.add_argument("--archive", action="store_true", default=True, dest="archive",
                    help="Archive outputs after pipeline completion (default: on)")
    rp.add_argument("--no-archive", action="store_false", dest="archive",
                    help="Skip output archiving after pipeline completion")
    rp.add_argument("--keep-going", action="store_true", default=False, dest="keep_going",
                    help="Pass --keep-going to Snakemake: a failed job doesn't "
                         "cancel independent jobs (useful for fan-out pipelines)")

    # -- canvas --
    cv = sub.add_parser("canvas", help="Launch the workflow canvas web UI")
    cv.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    cv.add_argument("--port", type=int, default=8500, help="Bind port (default: 8500)")
    cv.add_argument("--reload", action="store_true", help="Enable auto-reload (development mode)")
    cv.add_argument("--project-root", default=None,
                    help="Path to wfc project directory (default: cwd). "
                         "The directory must contain .wfc/wfc.db.")

    # -- run-step (ADR 008) --
    rs_cmd = sub.add_parser("run-step", help="Execute a single pipeline step end-to-end")
    rs_cmd.add_argument("--node-id", required=True, help="Unique node identity in the pipeline")
    rs_cmd.add_argument("--sample", required=True, help="Sample identifier")
    rs_cmd.add_argument("--variant", default="default", help="Parameter variant name")
    rs_cmd.add_argument("--method", default=None, help="Method name (inline fallback)")
    rs_cmd.add_argument("--module", default=None, help="Module name (inline fallback)")
    rs_cmd.add_argument("--script", default=None, help="Path to method script (inline fallback)")
    rs_cmd.add_argument("--params", default=None, help="JSON string of parameters")
    rs_cmd.add_argument("--parent-run-id", nargs="*", default=None,
                        help="Parent run ID(s) as 'slot:id' or plain 'id'.")
    rs_cmd.add_argument("--pipeline-id", default=None, help="Pipeline execution ID")
    rs_cmd.add_argument("--pipeline-json", default=None, help="Path to pipeline JSON file")
    rs_cmd.add_argument("--git-commit", default=None, help="Pre-computed git commit SHA")
    rs_cmd.add_argument("--ref-input", action="append", default=None,
                        help="Static ref input as 'label=path' from run_reference nodes. "
                             "Repeatable. Orchestrator resolves these; run-step stays "
                             "topology-agnostic (ADR-008 boundary rule).")
    rs_cmd.add_argument("--collapsed-sample", action="append", default=None,
                        help="For collapsed-fan-in roots invoked with --sample __all__, "
                             "the bundled sample identities (one flag per sample). "
                             "Repeatable. The runtime walks data/samples/<s>/ per name "
                             "and accumulates the per-sample data files into the fan-in "
                             "slot. Order is preserved.")

    # -- exec-method (ADR-019 Cycle D, fix-pass 5) --
    em_cmd = sub.add_parser(
        "exec-method",
        help="In-container entrypoint: execute a method script in the env "
             "established by the outer `wfc run-step` (no run-state ownership)",
    )
    em_cmd.add_argument("--run-id", type=int, required=True,
                        help="Outer run_id (for error-message clarity; "
                             "cross-referenced against WFC_RUN_DIR)")
    em_cmd.add_argument("--node-id", required=True,
                        help="Pipeline node ID (for error-message clarity)")
    em_cmd.add_argument("--script", required=True,
                        help="Absolute path to the method script inside the "
                             "container (e.g. /work/methods/<m>/<m>.py)")

    # -- pipeline-summary (ADR 008) --
    ps_cmd = sub.add_parser("pipeline-summary", help="Aggregate outcome sidecars into summary")
    ps_cmd.add_argument("--pipeline-id", required=True, help="Pipeline execution ID")

    # -- list-envs (ADR-019) --
    le_cmd = sub.add_parser(
        "list-envs",
        help="List container envs in .wfc/envs.json",
    )

    # -- show-env (ADR-019) --
    se_cmd = sub.add_parser(
        "show-env",
        help="Print full record for a container env",
    )
    se_cmd.add_argument("name", help="Env name (key in .wfc/envs.json)")

    # -- delete-env (ADR-019) --
    de_cmd = sub.add_parser(
        "delete-env",
        help="Remove a container env from .wfc/envs.json (registry tag untouched)",
    )
    de_cmd.add_argument("name", help="Env name to delete")
    de_cmd.add_argument(
        "--force", action="store_true",
        help=(
            "Skip the confirmation prompt "
            "(the warn-on-reference listing still prints)."
        ),
    )

    # -- register-env --
    re_cmd = sub.add_parser(
        "register-env",
        help="Build a container image for an env and register it in "
             ".wfc/envs.json. Accepts a positional typed-spec "
             "(conda:<env> / pixi:<proj>:<env>) to capture from a live "
             "env, --from <path> to copy a source file, or --backend "
             "alone for legacy mode.",
        epilog=(
            "Capturing from a live env (conda:<env>, pixi:<proj>:<env>) "
            "records the env's current state — including any ad-hoc "
            "`pip install` mutations on top of the conda/pixi env. "
            "Inspect the captured package list in the canvas env-detail "
            "panel (or via GET /api/registry/envs/blob/<md5>) before "
            "relying on the image for downstream runs."
        ),
    )
    re_cmd.add_argument("name", help="Env name (key in .wfc/envs.json)")
    re_cmd.add_argument(
        "spec", nargs="?", default=None,
        help="Optional typed env spec to capture from a live env "
             "(conda:<env>, pixi:<name>, pixi:<proj>:<env>). Mutually "
             "exclusive with --backend and --from.",
    )
    re_cmd.add_argument(
        "--backend", default=None,
        choices=["pixi", "conda", "inherit", "byo"],
        help="Build backend. Inferred from positional typed-spec when "
             "present; required for --from and legacy modes.",
    )
    re_cmd.add_argument(
        "--from", dest="from_path", default=None, metavar="PATH",
        help="File-mode: copy this file into the build context under "
             "the generator's expected filename (explicit-list.txt for "
             "conda, pixi.lock for pixi). Requires explicit --backend.",
    )
    re_cmd.add_argument("--pixi-env", default=None,
                        help="(deprecated) Pixi env name. Use positional "
                             "'pixi:<name>' or 'pixi:<proj>:<env>' instead.")
    re_cmd.add_argument("--conda-env", default=None,
                        help="(deprecated) Conda env name. Use positional "
                             "'conda:<env>' instead.")
    re_cmd.add_argument("--image", default=None,
                        help="docker:// reference for --backend byo")
    re_cmd.add_argument("--base-image", default=None,
                        help="Override the default base image for this env")
    re_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Write the Dockerfile to .wfc/build/<name>/Dockerfile and "
             "exit; do NOT invoke docker. Legacy mode only — positional "
             "typed specs and --from are not yet supported under "
             "--dry-run.",
    )
    re_cmd.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing manifest entry for <name>. "
             "Default behavior is to error if the env is already "
             "registered.",
    )

    # -- dev-loop commands (ADR-019 Cycle E) --
    # Three convenience verbs that launch an ephemeral container of the
    # env's image with the same bind-mount layout wfc run-step uses. The
    # ephemeral-container reminder is in each subparser's epilog so it
    # surfaces in --help output (ADR-019 §dev-loop-commands).
    _EPHEMERAL_REMINDER = (
        "Note: the container is spawned fresh per invocation; changes "
        "made during the session, including packages installed via pip, "
        "do not persist into pipeline runs."
    )

    # -- jupyter --
    jup_cmd = sub.add_parser(
        "jupyter",
        help="Launch Jupyter Lab in an ephemeral container of the env's image",
        epilog=_EPHEMERAL_REMINDER,
    )
    jup_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")
    jup_cmd.add_argument(
        "--port", type=int, default=None,
        help="Host port to forward to the container's 8888. "
             "Default: autopick the first free port in 8888-8999 "
             "(port 8000 is always skipped due to local conflicts).",
    )

    # -- shell --
    sh_cmd = sub.add_parser(
        "shell",
        help="Drop into an interactive shell in an ephemeral container",
        epilog=_EPHEMERAL_REMINDER,
    )
    sh_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")

    # -- exec --
    ex_cmd = sub.add_parser(
        "exec",
        help="Run a command in an ephemeral container of the env's image",
        epilog=_EPHEMERAL_REMINDER,
    )
    ex_cmd.add_argument("env", help="Env name (key in .wfc/envs.json)")
    ex_cmd.add_argument(
        "cmd", nargs=argparse.REMAINDER,
        help="Command to run inside the container (everything after <env>)",
    )

    # -- cache (ADR-011) --
    cache_cmd = sub.add_parser("cache", help="Cache management commands")
    cache_sub = cache_cmd.add_subparsers(dest="cache_command", required=True)

    prune_cmd = cache_sub.add_parser(
        "prune",
        help="Remove old run archives and optionally DVC local cache entries",
    )
    prune_cmd.add_argument(
        "--all", action="store_true", dest="prune_all",
        help="Remove all run archives regardless of reference status",
    )
    prune_cmd.add_argument(
        "--include-local", action="store_true",
        help="Also prune the DVC local cache (.dvc/cache/) for unreferenced hashes",
    )
    prune_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting",
    )
    prune_cmd.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt",
    )

    # -- cache archive (deferred output archiving) --
    archive_cmd = cache_sub.add_parser(
        "archive",
        help="Hash and cache un-archived outputs from completed runs",
    )
    archive_cmd.add_argument(
        "--run-id", type=int, default=None,
        help="Archive only outputs from a specific run ID",
    )

    return parser


def cli_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "register_run":
        params = json.loads(args.params) if args.params else None
        run_id = register_run(
            method_name=args.method,
            module_name=args.module,
            sample=args.sample,
            params=params,
            parent_run_ids=args.parent_run_id,
            nf_process_name=args.nf_process_name,
            pipeline_id=args.pipeline_id,
        )
        print(run_id)
        return 0

    elif args.command == "pre_run":
        params = json.loads(args.params) if args.params else None
        try:
            flag, run_id = pre_run(
                method_name=args.method,
                module_name=args.module,
                sample=args.sample,
                params=params,
                parent_run_ids=args.parent_run_id,
                pipeline_id=args.pipeline_id,
                nf_process_name=args.nf_process_name,
                git_commit=args.git_commit,
            )
        except Exception as exc:  # DirtyRepositoryError, ValueError, etc.
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"{flag}:{run_id}")
        return 0

    elif args.command == "complete_run":
        metrics = json.loads(args.metrics) if args.metrics else None
        complete_run(
            run_id=args.run_id,
            status=args.status,
            output_files=args.output,
            metrics=metrics,
            error_message=args.error,
            error_traceback=args.traceback_str,
        )
        return 0

    elif args.command == "check_cache":
        params = json.loads(args.params) if args.params else None
        run_id = check_cache(
            method_name=args.method,
            sample=args.sample,
            params=params,
            parent_run_ids=args.parent_run_id,
        )
        print(run_id if run_id is not None else "NONE")
        return 0

    elif args.command == "finalize_pipeline":
        finalize_pipeline(pipeline_id=args.pipeline_id)
        return 0

    elif args.command == "fail_pipeline":
        fail_pipeline(pipeline_id=args.pipeline_id)
        return 0

    elif args.command == "resolve_input":
        path = resolve_input(run_id=args.run_id)
        if path is None:
            print("ERROR: no output found for run", file=sys.stderr)
            return 1
        print(path)
        return 0

    elif args.command == "lookup_run":
        run_id = lookup_run(args.method, args.sample, nf_process_name=args.nf_process_name)
        if run_id is None:
            print("ERROR: no completed run found", file=sys.stderr)
            return 1
        print(run_id)
        return 0

    elif args.command == "init":
        from .init import init_project
        target = Path(args.dir).resolve()
        created = init_project(target, init_git=args.git)
        print(f"Initialized wfc project at {target}")
        for name, was_created in created.items():
            icon = "+" if was_created else "."
            print(f"  {icon} {name}")
        return 0

    elif args.command == "seed":
        from .seed import seed
        seed()
        return 0

    elif args.command == "register-sample":
        source = Path(args.source).resolve()
        try:
            registered = register_sample(
                name=args.name,
                source_path=source,
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Registered sample '{args.name}' -> {registered}")
        return 0

    elif args.command == "restore-sample":
        restore_sample(
            name=args.name,
            content_hash=args.content_hash,
        )
        return 0

    elif args.command == "register-module":
        from .register import register_module
        contracts = None
        if args.contracts is not None:
            # Accept a file path or inline JSON string
            contracts_path = Path(args.contracts)
            if contracts_path.exists():
                contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
            else:
                contracts = json.loads(args.contracts)
        module_dir = Path(args.module_dir) if args.module_dir else None
        # Auto-discover module_dir from modules/{name} if not provided
        if module_dir is None:
            candidate = Path("modules") / args.name
            if candidate.is_dir():
                module_dir = candidate
        register_module(
            name=args.name,
            contracts=contracts,
            description=args.description,
            module_dir=module_dir,
        )
        return 0

    elif args.command == "register-method":
        from .register import register_method
        register_method(
            method_dir=Path(args.method_dir),
            module_name=args.module,
            method_name=args.name,
            script_name=args.script,
        )
        return 0

    elif args.command == "run-pipeline":
        rc = run_pipeline(
            pipeline_path=args.pipeline,
            project_root=args.project_root,
            wfc_root=args.wfc_root,
            cores=args.cores,
            snakefile_path=args.snakefile,
            archive=args.archive,
            keep_going=args.keep_going,
        )
        return rc

    elif args.command == "run-step":
        params = json.loads(args.params) if args.params else None
        rc = run_step(
            node_id=args.node_id,
            sample=args.sample,
            variant=args.variant,
            method_name=args.method,
            module_name=args.module,
            script_path=args.script,
            params=params,
            parent_run_ids=args.parent_run_id,
            pipeline_id=args.pipeline_id,
            pipeline_json=args.pipeline_json,
            git_commit=args.git_commit,
            ref_inputs=args.ref_input,
            collapsed_samples=args.collapsed_sample,
        )
        return rc

    elif args.command == "exec-method":
        return exec_method(
            run_id=args.run_id,
            node_id=args.node_id,
            script_path=args.script,
        )

    elif args.command == "pipeline-summary":
        return pipeline_summary(pipeline_id=args.pipeline_id)

    elif args.command == "list-envs":
        return _cli_list_envs()

    elif args.command == "show-env":
        return _cli_show_env(args.name)

    elif args.command == "delete-env":
        return _cli_delete_env(args.name, force=args.force)

    elif args.command == "register-env":
        return _cli_register_env(args)

    elif args.command == "jupyter":
        from . import dev_loop
        return dev_loop.jupyter(args.env, port=args.port)

    elif args.command == "shell":
        from . import dev_loop
        return dev_loop.shell(args.env)

    elif args.command == "exec":
        from . import dev_loop
        return dev_loop.exec_(args.env, args.cmd or [])

    elif args.command == "cache":
        if args.cache_command == "prune":
            return cache_prune(
                prune_all=args.prune_all,
                include_local=args.include_local,
                dry_run=args.dry_run,
                force=args.force,
            )
        elif args.cache_command == "archive":
            return cache_archive(run_id=args.run_id)
        return 1

    elif args.command == "canvas":
        try:
            import uvicorn
        except ImportError:
            print(
                "ERROR: uvicorn is required to run the canvas server.\n"
                "Install it with: pip install 'uvicorn[standard]'",
                file=sys.stderr,
            )
            return 1
        # Point DATABASE_URL at the chosen project before uvicorn starts
        if args.project_root:
            proj = Path(args.project_root).resolve()
            db = proj / ".wfc" / "wfc.db"
            if not db.exists():
                print(f"ERROR: No .wfc/wfc.db found in {proj}", file=sys.stderr)
                return 1
            os.environ["WFC_CANVAS_PROJECT_ROOT"] = str(proj)
        print(f"Starting Workflow Canvas at http://{args.host}:{args.port}")
        uvicorn.run(
            "wfc.canvas.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    return 1
