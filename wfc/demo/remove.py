"""Teardown logic for ``wfc demo --remove``.

Deletes ONLY by the ``__demo__`` tag / module cascade — never by matching
method names against whatever is present. Because the reserved-prefix guard
means no user-driven path can create a ``__demo__`` name, the tag is proof
of demo ownership. Deletion runs in dependency-safe order (SQLite FK
enforcement is OFF here — nothing at the storage layer catches mistakes):

    run children (RunInput / RunOutput / RunAnnotation)
    -> runs -> null surviving runs' FKs into the deleted set
    -> MethodContract / TrackedFunction (+ParamDef) / MethodVersion
    -> methods -> ModuleContract -> module -> samples -> env -> files.

The files are the demo module's whole snapshot directory under
``.wfc/registry/methods/`` (which holds each demo method's snapshot and
its version list, ``<method>.versions.json``), the demo's sample workspace directories, the
pipeline document, the demo schema stamp (``.wfc/demo.json``), and each
demo env's build and captures folders. The demo's envs are found through the
demo module, never by name: the demo env plus every env the demo module's
methods name (their rows and every entry of their version lists), read before
anything is deleted, so an older wfc's demo env goes too. The deletions and
the env manifest's change are then committed by pathspec, so the tree is
clean afterwards.

The DVC cache and the output archive are NEVER touched — cached bytes are
governed by ``wfc cache prune``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .. import layout
from .scaffold import (
    DEMO_IMAGE_TAG,
    DEMO_MODULE,
    DEMO_PIPELINE_FILENAME,
    DemoError,
    _project_env,
    demo_owned_envs,
)


def remove_demo(
    target_dir: str | Path | None = None,
    purge_image: bool = False,
    assume_yes: bool = False,
) -> int:
    """Remove every demo-owned entity, run, and file from the project.

    Idempotent and tolerant of partial scaffolds: whatever is present is
    removed, whatever is absent is skipped without error.

    Args:
        target_dir: Project directory (default: cwd).
        purge_image: Also delete the ``local/wfc-demo-env`` Docker image.
        assume_yes: Skip the confirmation prompt.

    Returns:
        0 on success or nothing-to-do; 1 when the user declines the
        confirmation.

    Raises:
        DemoError: If *target_dir* is not an initialised project.
    """
    from sqlmodel import col, select

    target = Path(target_dir or Path.cwd()).resolve()
    marker = layout.marker_path(target)
    db_path = layout.db_path(target)
    if not marker.exists() or not db_path.exists():
        raise DemoError(
            f"{target} is not a Workflow Canvas project — nothing to remove"
        )

    with _project_env(target):
        from ..environments import load_manifest_for_register
        from ..persistence import (
            Method,
            MethodContract,
            MethodVersion,
            Module,
            ModuleContract,
            ParamDef,
            Run,
            RunAnnotation,
            RunInput,
            RunOutput,
            Sample,
            TrackedFunction,
            get_session,
        )

        # ---- Compute the demo-owned set from the DB (tag / cascade only) ----
        with get_session() as session:
            module = session.exec(
                select(Module).where(Module.name == DEMO_MODULE)
            ).first()
            module_id = module.id if module is not None else None
            methods = (
                session.exec(
                    select(Method).where(Method.module_id == module_id)
                ).all()
                if module_id is not None
                else []
            )
            method_ids = [m.id for m in methods]
            method_names = [m.name for m in methods]
            runs = (
                session.exec(
                    select(Run).where(col(Run.method_id).in_(method_ids))
                ).all()
                if method_ids
                else []
            )
            run_ids = [r.id for r in runs]
            # autoescape: `_` is a single-char LIKE wildcard, so an
            # unescaped LIKE '__demo__%' would also select (and delete!)
            # user samples like 'mydemo__x'.
            samples = session.exec(
                select(Sample).where(
                    col(Sample.name).startswith(DEMO_MODULE, autoescape=True)
                )
            ).all()
            sample_names = [s.name for s in samples]

        try:
            # The register path's read (`wfc demo --force` re-registers the
            # demo env next, converting an older manifest), so a version-1
            # or version-2 file is opened rather than refused.
            manifest = load_manifest_for_register(target)
        except Exception as exc:
            print(
                f"WARNING: could not read the env manifest ({exc}) — env "
                f"teardown will be skipped.",
                file=sys.stderr,
            )
            manifest = {}
        # The demo's envs: DEMO_ENV and every env its methods name (an older
        # wfc's demo env included), read before anything is deleted.
        owned_envs = demo_owned_envs(target)
        present_envs = [e for e in owned_envs if e in manifest.get("envs", {})]
        env_present = bool(present_envs)

        # Files: the demo module's snapshot directory as a whole. Snapshots
        # are module-qualified, so it holds every demo method (and any
        # partial-scaffold residue) and nothing of another module's; no
        # method directory is chosen by name.
        snapshot_dir = layout.module_methods_dir(target, DEMO_MODULE)
        samples_root = layout.samples_dir(target)
        sample_dirs = sorted(
            p for p in samples_root.glob(f"{DEMO_MODULE}*")
            if p.is_dir()
        ) if samples_root.exists() else []
        pipeline_file = target / DEMO_PIPELINE_FILENAME
        stamp_file = layout.demo_stamp_path(target)

        file_targets: list[Path] = []
        if snapshot_dir.exists():
            file_targets.append(snapshot_dir)
        file_targets.extend(sample_dirs)
        for f in (pipeline_file, stamp_file):
            if f.exists():
                file_targets.append(f)
        # Each demo env's build folder and its captures folder, committed
        # by its register.
        captures_dirs: list[Path] = []
        for env in owned_envs:
            build_dir = layout.env_build_dir(target, env)
            if build_dir.exists():
                file_targets.append(build_dir)
            captures_dir = layout.env_captures_dir(target, env)
            if captures_dir.exists():
                file_targets.append(captures_dir)
                captures_dirs.append(captures_dir)

        nothing_in_db = module_id is None and not samples and not env_present
        if nothing_in_db and not file_targets:
            print("No demo state found in this project — nothing to remove.")
            return 0

        # ---- Print the removal list ----
        print("wfc demo --remove will delete:")
        if module_id is not None:
            print(f"  module {DEMO_MODULE} with {len(methods)} method(s): "
                  + ", ".join(method_names))
        print(f"  {len(run_ids)} demo run(s) and their input/output/annotation rows")
        for s in sample_names:
            print(f"  sample {s}")
        for env in present_envs:
            print(f"  env {env}")
        for f in file_targets:
            print(f"  {f}")
        if purge_image:
            print(f"  Docker image {DEMO_IMAGE_TAG}")

        # ---- Shared-env warning: non-demo methods on a demo env ----
        if env_present:
            from ..registration import methods_referencing_env

            for env in present_envs:
                refs = [
                    r for r in methods_referencing_env(env)
                    if not r.startswith(f"{DEMO_MODULE}/")
                ]
                if refs:
                    print(
                        f"WARNING: non-demo method(s) reference env {env} and "
                        f"will no longer resolve it: " + ", ".join(refs),
                        file=sys.stderr,
                    )

        if not assume_yes:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted — nothing removed.")
                return 1

        # ---- Delete DB rows in dependency-safe order ----
        with get_session() as session:
            if run_ids:
                for model in (RunInput, RunOutput):
                    for row in session.exec(
                        select(model).where(col(model.run_id).in_(run_ids))
                    ).all():
                        session.delete(row)
                for row in session.exec(
                    select(RunAnnotation).where(
                        col(RunAnnotation.run_id).in_(run_ids)
                    )
                ).all():
                    session.delete(row)
                for r in session.exec(
                    select(Run).where(col(Run.id).in_(run_ids))
                ).all():
                    session.delete(r)
                session.commit()

                # Null surviving runs' FKs into the deleted set. Cache keys
                # are method-scoped so this SHOULD be a closed set, but a
                # user who byte-copies a demo method can cache-hit a demo
                # run — nulling is cheap and makes teardown robust.
                for survivor in session.exec(
                    select(Run).where(
                        col(Run.cache_source_run_id).in_(run_ids)
                    )
                ).all():
                    survivor.cache_source_run_id = None
                    session.add(survivor)
                for survivor in session.exec(
                    select(Run).where(
                        col(Run.cancelled_due_to_run_id).in_(run_ids)
                    )
                ).all():
                    survivor.cancelled_due_to_run_id = None
                    session.add(survivor)
                # Same for surviving runs' input rows that recorded a demo
                # run as their source (e.g. a user pipeline that consumed a
                # demo method's output).
                for ri in session.exec(
                    select(RunInput).where(
                        col(RunInput.source_run_id).in_(run_ids)
                    )
                ).all():
                    ri.source_run_id = None
                    session.add(ri)
                session.commit()

            if method_ids:
                for method_model in (MethodContract, MethodVersion):
                    for row in session.exec(
                        select(method_model).where(
                            col(method_model.method_id).in_(method_ids)
                        )
                    ).all():
                        session.delete(row)
                tfs = session.exec(
                    select(TrackedFunction).where(
                        col(TrackedFunction.method_id).in_(method_ids)
                    )
                ).all()
                tf_ids = [tf.id for tf in tfs]
                if tf_ids:
                    for pd in session.exec(
                        select(ParamDef).where(
                            col(ParamDef.tracked_function_id).in_(tf_ids)
                        )
                    ).all():
                        session.delete(pd)
                for tf in tfs:
                    session.delete(tf)
                for m in session.exec(
                    select(Method).where(col(Method.id).in_(method_ids))
                ).all():
                    session.delete(m)
                session.commit()

            if module_id is not None:
                for mc in session.exec(
                    select(ModuleContract).where(
                        ModuleContract.module_id == module_id
                    )
                ).all():
                    session.delete(mc)
                mod = session.get(Module, module_id)
                if mod is not None:
                    session.delete(mod)
                session.commit()

            for s in session.exec(
                select(Sample).where(
                    col(Sample.name).startswith(DEMO_MODULE, autoescape=True)
                )
            ).all():
                session.delete(s)
            session.commit()

        # ---- Env ----
        if env_present:
            from ..environments import MANIFEST_SCHEMA_VERSION, save_manifest

            # Dropped from the manifest as read, not through `delete`, which
            # refuses an older (version 1 or 2) file; the file keeps its
            # version until a register converts it. A manifest left with no envs has nothing
            # to convert, so it is saved at the current version.
            current = load_manifest_for_register(target)
            popped = [current.get("envs", {}).pop(env, None) for env in present_envs]
            if any(p is not None for p in popped):
                if not current["envs"]:
                    current["schema_version"] = MANIFEST_SCHEMA_VERSION
                save_manifest(target, current)

        # ---- Files ----
        from ..storage import remove_restored_view

        sample_hashes = {s.name: s.content_hash for s in samples}
        removed_files: list[Path] = []
        for f in file_targets:
            if f in sample_dirs:
                # A restored sample's folder is read-only (its files are
                # views of cache objects): Storage removes the view, and a
                # removal that fails is named, never counted.
                try:
                    remove_restored_view(target, f.name, sample_hashes.get(f.name))
                except OSError as exc:
                    print(f"WARNING: could not remove the restored sample "
                          f"folder {f}: {exc}", file=sys.stderr)
                    continue
            elif f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
            removed_files.append(f)

        # ---- Commit the removal ----
        # The snapshot directory was committed at registration and the env
        # manifest at env registration; deleting either without a commit
        # leaves a dirty tree, which the next run's commit check refuses.
        # Each demo env's captures folder was committed with its record, and
        # the pipeline document and the stamp by `wfc demo`. Only these
        # paths are named: nothing else the user staged is swept in.
        from ..version import commit_paths

        committed = [snapshot_dir, pipeline_file, stamp_file, *captures_dirs]
        if env_present:
            committed.append(layout.env_manifest_path(target))
        sha = commit_paths(
            target, committed, f"Remove demo module {DEMO_MODULE}",
            require_repo=False,
        )
        if sha is not None:
            print(f"  git: committed the removal as {sha[:12]}")

        # ---- Optional image purge ----
        if purge_image:
            try:
                res = subprocess.run(
                    ["docker", "rmi", DEMO_IMAGE_TAG],
                    capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"WARNING: `docker rmi {DEMO_IMAGE_TAG}` did not finish "
                    f"within 120s — remove the image manually.",
                    file=sys.stderr,
                )
            else:
                if res.returncode != 0:
                    print(
                        f"WARNING: could not remove image {DEMO_IMAGE_TAG}: "
                        f"{res.stderr.strip()}",
                        file=sys.stderr,
                    )

    print(
        f"\nDemo removed: {len(run_ids)} run(s), "
        f"{len(method_names)} method(s), {len(sample_names)} sample(s), "
        f"{len(removed_files)} file path(s)."
        "\nCached output bytes remain in the DVC cache — reclaim them with "
        "`wfc cache prune`."
    )
    return 0
